"""FastAPI app + entry point for ``wlb-api`` / ``wlb web``.

Routes:

    GET  /                       index.html (the bundled SPA)
    GET  /static/*               assets next to index.html
    GET  /api/version            wlb + agent version banner
    GET  /api/describe           registry: transports + capabilities
    GET  /api/status             active transport health snapshot
    GET  /api/profile            merged active settings (env > profile > defaults)
    GET  /api/tools              list of declared tools + load-time warnings
    GET  /api/tools/{name}       full spec for one tool
    GET  /api/maps               configured SMB / Samba mappings
    WS   /ws/tool/{name}         stream a tool run as JSON ToolStreamEvents

Security model:

- Default bind ``127.0.0.1:8765``. Anyone who can reach the port has the
  same powers as the user that started the server (including running any
  declared tool). Do NOT expose past localhost without adding auth —
  there is no token check in M3.3.
- The bundled UI is a vanilla-JS SPA with no build step; assets live
  next to ``server.py`` under ``static/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from wlb import __version__
from wlb.capabilities.filesync import push as cap_push  # noqa: F401 — reserved for M3.4
from wlb.capabilities.status import describe as cap_describe
from wlb.capabilities.status import status as cap_status
from wlb.capabilities.tool import list_tools as cap_list_tools
from wlb.capabilities.tool import run_tool_stream as cap_run_tool_stream
from wlb.capabilities.tool import show_tool as cap_show_tool
from wlb.infra.config import load_active
from wlb.mcp.transport_factory import build_transport


_STATIC_DIR = Path(__file__).parent / "static"
_INDEX_HTML = _STATIC_DIR / "index.html"

DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8765


def create_app(profile_name: str | None = None) -> FastAPI:
    """Build the FastAPI app. Profile name pins which wlb profile this server uses."""
    app = FastAPI(
        title="wlb-api",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # ── static assets ────────────────────────────────────────────
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def root() -> Any:
        if _INDEX_HTML.exists():
            return FileResponse(str(_INDEX_HTML), media_type="text/html")
        return JSONResponse(
            {
                "ok": True,
                "wlb": __version__,
                "note": "Static UI not bundled. Use /api/* endpoints directly.",
            }
        )

    @app.get("/pty.html", include_in_schema=False)
    async def pty_page() -> Any:
        pty_path = _STATIC_DIR / "pty.html"
        if pty_path.exists():
            return FileResponse(str(pty_path), media_type="text/html")
        raise HTTPException(status_code=404, detail="pty.html not bundled")

    # ── meta ─────────────────────────────────────────────────────
    @app.get("/api/version")
    async def api_version() -> dict[str, Any]:
        return {"wlb": __version__}

    @app.get("/api/describe")
    async def api_describe() -> dict[str, Any]:
        r = await cap_describe()
        return r.to_dict()

    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        transport = build_transport(profile_name=profile_name)
        r = await cap_status(transport)
        return r.to_dict()

    @app.get("/api/profile")
    async def api_profile() -> dict[str, Any]:
        s = load_active(profile_name)
        return {
            "profile_name": s.profile_name,
            "profile_path": str(s.profile_path) if s.profile_path else None,
            "profile_loaded": s.profile_loaded,
            "warnings": list(s.profile_warnings),
            "primary_transport": s.primary_transport,
            "ssh": {
                "host": s.ssh.host,
                "port": s.ssh.port,
                "user": s.ssh.user,
                "key_path": s.ssh.key_path,
                "connect_timeout": s.ssh.connect_timeout,
            },
            "http": {
                "url": s.http.url,
                "token_file": s.http.token_file,
                "ca_bundle": s.http.ca_bundle,
                "verify_tls": s.http.verify_tls,
                "connect_timeout": s.http.connect_timeout,
            },
        }

    @app.get("/api/maps")
    async def api_maps() -> dict[str, Any]:
        s = load_active(profile_name)
        return {
            "profile_name": s.profile_name,
            "maps": [
                {
                    "linux_mount": m.linux_mount,
                    "windows_path": m.windows_path,
                    "linux_reachable": Path(m.linux_mount).exists(),
                }
                for m in s.smb_maps
            ],
        }

    @app.get("/api/tools")
    async def api_tools() -> dict[str, Any]:
        r = await cap_list_tools()
        return r.to_dict()

    @app.get("/api/tools/{name}")
    async def api_tool_show(name: str) -> dict[str, Any]:
        r = await cap_show_tool(name)
        if not r.ok:
            raise HTTPException(status_code=404, detail=r.to_dict())
        return r.to_dict()

    # ── streaming: run a tool ───────────────────────────────────
    @app.websocket("/ws/tool/{name}")
    async def ws_tool(websocket: WebSocket, name: str) -> None:
        """Stream a tool run as JSON ToolStreamEvents.

        Wire protocol:
            client → server (first frame, JSON text): ``{"args": {"key": "value", ...}}``
            server → client (each frame, JSON text): one ToolStreamEvent dict
            terminal frame: ``kind == "done"``; server then closes the socket.

        Server-side errors (bad first frame, capability raise) close with
        a synthetic done event carrying ``error_code``.
        """
        await websocket.accept()
        try:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                return
            try:
                payload = json.loads(raw)
                args = payload.get("args") or {}
                if not isinstance(args, dict):
                    raise ValueError("args must be an object")
            except (ValueError, TypeError) as e:
                await websocket.send_text(json.dumps({
                    "kind": "done",
                    "ok": False,
                    "error_code": "INVALID_HOST",   # closest existing input-shaped code
                    "line": f"bad first frame: {e}",
                }))
                await websocket.close()
                return

            transport = build_transport(profile_name=profile_name)
            try:
                async for ev in cap_run_tool_stream(transport, name, args):
                    await websocket.send_text(json.dumps(ev.to_dict(), default=str))
            except WebSocketDisconnect:
                return
        finally:
            try:
                await websocket.close()
            except Exception:                # noqa: BLE001 — close on already-closed is fine
                pass

    # ── interactive PTY (M3.4) ──────────────────────────────────
    @app.websocket("/ws/pty")
    async def ws_pty(websocket: WebSocket) -> None:
        """Bidirectional PTY pump.

        Wire protocol:
            First client→server frame (TEXT, JSON):
                {"interpreter": "cmd"|"powershell"|"raw",
                 "cols": 80, "rows": 24}

            After that:
                client→server BINARY  = keystrokes / paste bytes
                client→server TEXT    = control JSON:
                    {"kind":"resize","cols":N,"rows":N}
                    {"kind":"close"}
                server→client BINARY  = raw PTY bytes (xterm escapes etc.)
                server→client TEXT    = control JSON:
                    {"kind":"exit","exit_code":N}
                    {"kind":"error","error":"..."}

            Either side may close the socket at any time; server cleans
            up the PTY in a ``finally`` block.
        """
        import asyncio
        await websocket.accept()

        session = None
        pump_to_ws_task = None
        try:
            # ── first frame: settings ──────────────────────────
            try:
                raw = await websocket.receive_text()
                opts = json.loads(raw) or {}
                interpreter = opts.get("interpreter", "cmd")
                cols = int(opts.get("cols", 80))
                rows = int(opts.get("rows", 24))
            except (WebSocketDisconnect, ValueError, TypeError) as e:
                await websocket.send_text(json.dumps({"kind": "error", "error": f"bad first frame: {e}"}))
                return

            if interpreter not in ("cmd", "powershell", "raw"):
                await websocket.send_text(json.dumps(
                    {"kind": "error", "error": f"bad interpreter: {interpreter}"}
                ))
                return

            transport = build_transport(profile_name=profile_name)
            if not getattr(transport, "supports_pty", False):
                await websocket.send_text(json.dumps(
                    {"kind": "error", "error": f"transport {transport.name!r} has no PTY support"}
                ))
                return

            try:
                session = await transport.open_pty(
                    interpreter=interpreter, cols=cols, rows=rows,
                )
            except NotImplementedError as e:
                await websocket.send_text(json.dumps({"kind": "error", "error": str(e)}))
                return
            except ConnectionError as e:
                await websocket.send_text(json.dumps({"kind": "error", "error": str(e)}))
                return

            # ── pump bytes from PTY out to the WS ──────────────
            async def pump_to_ws() -> None:
                assert session is not None
                while True:
                    chunk = await session.read(4096)
                    if not chunk:
                        break
                    try:
                        await websocket.send_bytes(chunk)
                    except (WebSocketDisconnect, RuntimeError):
                        return
                exit_code = await session.wait()
                try:
                    await websocket.send_text(json.dumps(
                        {"kind": "exit", "exit_code": exit_code}
                    ))
                except Exception:                # noqa: BLE001
                    pass

            pump_to_ws_task = asyncio.create_task(pump_to_ws())

            # ── pump WS frames into the PTY ────────────────────
            while True:
                try:
                    message = await websocket.receive()
                except WebSocketDisconnect:
                    break
                mtype = message.get("type")
                if mtype == "websocket.disconnect":
                    break
                if "bytes" in message and message["bytes"] is not None:
                    await session.write(message["bytes"])
                elif "text" in message and message["text"] is not None:
                    try:
                        ctrl = json.loads(message["text"])
                    except (ValueError, TypeError):
                        continue
                    kind = ctrl.get("kind")
                    if kind == "resize":
                        try:
                            await session.resize(int(ctrl["cols"]), int(ctrl["rows"]))
                        except (KeyError, ValueError, TypeError):
                            pass
                    elif kind == "close":
                        break
        finally:
            if session is not None:
                try:
                    await session.close()
                except Exception:                # noqa: BLE001
                    pass
            if pump_to_ws_task is not None and not pump_to_ws_task.done():
                pump_to_ws_task.cancel()
            try:
                await websocket.close()
            except Exception:                    # noqa: BLE001
                pass

    return app


def main() -> None:
    """Entry point for ``wlb-api`` — starts uvicorn on localhost by default."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="wlb-api", description="wlb HTTP API + Web UI.")
    parser.add_argument("--host", default=DEFAULT_BIND, help=f"Bind address (default {DEFAULT_BIND}).")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (default {DEFAULT_PORT}).")
    parser.add_argument("--profile", default=None, help="wlb profile name (env WLB_PROFILE wins if both set).")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code change (dev only).")
    args = parser.parse_args()

    try:
        import uvicorn       # type: ignore[import-not-found]
    except ModuleNotFoundError:                  # pragma: no cover — install hint
        raise SystemExit("wlb-api needs uvicorn: uv sync") from None

    app = create_app(profile_name=args.profile)
    bind_label = f"http://{args.host}:{args.port}"
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"⚠  wlb-api is binding to {args.host}. There is NO authentication "
            f"in M3.3 — anyone who reaches {bind_label} can run every declared "
            f"tool. Restrict the network or add a reverse proxy with auth.",
            file=sys.stderr,
        )
    else:
        print(f"wlb-api listening on {bind_label} (localhost only)", file=sys.stderr)

    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload, log_level="info")


if __name__ == "__main__":                       # pragma: no cover
    main()
