"""wlb-agent — minimal Windows-side micro-service for the HTTP transport.

Run this on the Windows host when SSH is unavailable. The Linux-side
:class:`wlb.transport.http.HttpTransport` talks to it.

Usage on Windows (PowerShell admin shell):

    python -m pip install fastapi uvicorn[standard]
    python C:\\path\\to\\wlb_agent.py --config C:\\ProgramData\\wlb-agent\\wlb-agent.toml

The config file (mode-protected) supplies:

    bind = "0.0.0.0"
    port = 8443
    token_file = 'C:\\ProgramData\\wlb-agent\\token'    # mode-protected, single line
    tls_cert = 'C:\\ProgramData\\wlb-agent\\agent.crt'  # optional; HTTPS when both set
    tls_key  = 'C:\\ProgramData\\wlb-agent\\agent.key'

The agent re-implements the deny-list locally as defense in depth — even
if a controller misbehaves, the agent will refuse to format the C: drive.

Wire protocol (matches :mod:`wlb.transport.http`):

    POST /v1/shell        {cmd, interpreter, timeout}      → ShellResult JSON
    GET  /v1/health                                         → health JSON
    POST /v1/file/push    raw bytes, ?path=                → {ok, bytes}
    GET  /v1/file/pull    ?path=                           → bytes (octet-stream)

This file is intentionally self-contained — no wlb package import — so an
operator can copy it to a Windows host and run it standalone.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

try:
    import tomllib                          # Python 3.11+
except ModuleNotFoundError:                 # pragma: no cover — Win Py 3.10 fallback
    import tomli as tomllib                  # type: ignore[import-not-found,no-redef]

try:
    from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
    from fastapi.responses import JSONResponse, StreamingResponse
except ModuleNotFoundError as e:             # pragma: no cover
    raise SystemExit(
        "wlb-agent needs fastapi + uvicorn: pip install fastapi 'uvicorn[standard]'"
    ) from e


__version__ = "0.0.1"


# ─── Deny-list (mirrors wlb.infra.permissions; kept inline so the agent
#     is single-file deployable) ────────────────────────────────────────
_DANGEROUS = [
    (re.compile(p, re.IGNORECASE), reason)
    for p, reason in [
        (r"^\s*format\s+[a-z]:", "format a drive"),
        (r"^\s*del\s+/[a-z\s]*[qsf][a-z\s]*\s+[a-z]:\\?\*?", "del /q /s on a drive root"),
        (r"^\s*erase\s+/[a-z\s]*[qsf][a-z\s]*\s+[a-z]:\\?\*?", "erase /q /s on a drive root"),
        (r"^\s*rmdir\s+/s\s+/q\s+[a-z]:\\?", "rmdir /s /q on a drive root"),
        (r"^\s*rd\s+/s\s+/q\s+[a-z]:\\?", "rd /s /q on a drive root"),
        (r"^\s*shutdown\s+/[a-z]*[sr][a-z]*\b", "shutdown / restart"),
        (r"^\s*bcdedit\s+/(delete|export|import|set)\b", "bcdedit modification"),
        (r"^\s*diskpart\b", "interactive diskpart session"),
        (r"\\\\\\.\\PhysicalDrive\d+", "raw physical-drive access"),
        (r"\bFormat-Volume\b", "Format-Volume"),
        (r"\bClear-Disk\b", "Clear-Disk"),
        (r"\bInitialize-Disk\b", "Initialize-Disk"),
        (
            r"\bRemove-Item\b[^|;]*-Recurse[^|;]*-Force[^|;]*\s+[a-z]:\\?\s*['\"]?$",
            "Remove-Item -Recurse -Force on a drive root",
        ),
        (
            r"\bRemove-Item\b[^|;]*-Recurse[^|;]*-Force[^|;]*\s+[a-z]:\\?\\?\s*\*",
            "Remove-Item -Recurse -Force C:\\*",
        ),
        (r"\bStop-Computer\b", "Stop-Computer"),
        (r"\bRestart-Computer\b", "Restart-Computer"),
        (r"\bSet-ExecutionPolicy\s+(Unrestricted|Bypass)\b", "loosen ExecutionPolicy"),
        (r"\bReg\s+delete\s+HKLM\b", "reg delete HKLM"),
        (r"\bRemove-Item\b[^|;]*HKLM:", "Remove-Item HKLM"),
        (r"\bnet\s+user\s+\S+\s+/delete\b", "net user delete"),
        (r"\bRemove-LocalUser\b", "Remove-LocalUser"),
        (r"\bnetsh\s+advfirewall\s+set\s+allprofiles\s+state\s+off\b", "disable Windows Firewall"),
        (r"\bSet-MpPreference\b[^|;]*-DisableRealtimeMonitoring\s+\$?true", "disable Defender"),
    ]
]


def check_dangerous(cmd: str) -> tuple[bool, str | None]:
    """Return (is_dangerous, matched_rule)."""
    for rx, reason in _DANGEROUS:
        if rx.search(cmd):
            return True, reason
    return False, None


# ─── Config ──────────────────────────────────────────────────────


class AgentConfig:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.bind: str = str(raw.get("bind", "0.0.0.0"))
        self.port: int = int(raw.get("port", 8443))
        self.token_file: str | None = raw.get("token_file") or None
        self.token_inline: str | None = raw.get("token") or None
        self.tls_cert: str | None = raw.get("tls_cert") or None
        self.tls_key: str | None = raw.get("tls_key") or None

    @property
    def use_tls(self) -> bool:
        return bool(self.tls_cert and self.tls_key)

    def load_token(self) -> str:
        if self.token_inline:
            return self.token_inline.strip()
        if self.token_file:
            return Path(self.token_file).read_text(encoding="utf-8").strip()
        raise SystemExit("wlb-agent config: set either `token` or `token_file`.")


def load_config(path: Path) -> AgentConfig:
    if not path.exists():
        raise SystemExit(f"wlb-agent config not found: {path}")
    with path.open("rb") as fp:
        data = tomllib.load(fp)
    return AgentConfig(data)


# ─── PowerShell helper ──────────────────────────────────────────


def _encode_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _pwsh_binary() -> str | None:
    for bin_ in ("pwsh.exe", "powershell.exe"):
        if shutil.which(bin_):
            return bin_
    return None


# ─── Command runner ──────────────────────────────────────────────


def _build_argv(cmd: str, interpreter: str) -> list[str]:
    if interpreter == "powershell":
        pwsh = _pwsh_binary()
        if pwsh is None:
            raise FileNotFoundError("POWERSHELL_NOT_AVAILABLE")
        return [pwsh, "-NoProfile", "-NonInteractive", "-EncodedCommand", _encode_powershell(cmd)]
    if interpreter in ("cmd", "raw"):
        if sys.platform == "win32":
            return ["cmd.exe", "/c", cmd]
        return ["/bin/sh", "-c", cmd]
    raise ValueError(f"unknown interpreter: {interpreter}")


def run_command(cmd: str, interpreter: str, timeout: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        argv = _build_argv(cmd, interpreter)
    except FileNotFoundError:
        return {
            "ok": False, "exit_code": -1, "stdout": "", "stderr": "neither pwsh.exe nor powershell.exe found",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "error_code": "POWERSHELL_NOT_AVAILABLE",
        }
    try:
        proc = subprocess.run(
            argv, capture_output=True, timeout=max(1, int(timeout)), text=False, check=False
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "exit_code": -1, "stdout": "", "stderr": f"timed out after {timeout}s",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "error_code": "TIMEOUT_SHELL",
        }
    stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
    exit_code = int(proc.returncode)
    return {
        "ok": exit_code == 0,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "error_code": None if exit_code == 0 else "SHELL_NONZERO_EXIT",
    }


# ─── FastAPI app ─────────────────────────────────────────────────


def build_app(token: str) -> FastAPI:
    app = FastAPI(title="wlb-agent", version=__version__)

    def _auth(request: Request) -> None:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        provided = header[len("Bearer ") :].strip()
        # Constant-time comparison so timing doesn't leak the right answer.
        if not secrets.compare_digest(provided, token):
            raise HTTPException(status_code=401, detail="invalid token")

    @app.get("/v1/health")
    async def health(request: Request) -> JSONResponse:
        _auth(request)
        # Quick probe — Windows version + PowerShell presence.
        win_ver = "<unknown>"
        if sys.platform == "win32":
            try:
                out = subprocess.run(
                    ["cmd.exe", "/c", "ver"], capture_output=True, text=True, timeout=5
                )
                if out.returncode == 0:
                    lines = [ln.strip() for ln in (out.stdout or "").splitlines() if ln.strip()]
                    if lines:
                        win_ver = lines[-1]
            except (subprocess.SubprocessError, OSError):
                pass
        pwsh = _pwsh_binary()
        return JSONResponse(
            {
                "ok": True,
                "agent_version": __version__,
                "platform": sys.platform,
                "windows_version": win_ver,
                "powershell": pwsh or "<not available>",
            }
        )

    @app.post("/v1/shell")
    async def shell(request: Request, payload: dict[str, Any] = Body(...)) -> JSONResponse:
        _auth(request)
        cmd = payload.get("cmd")
        interp = payload.get("interpreter", "cmd")
        timeout = int(payload.get("timeout", 30))
        if not isinstance(cmd, str) or not cmd.strip():
            raise HTTPException(status_code=400, detail="missing cmd")
        if interp not in ("cmd", "powershell", "raw"):
            raise HTTPException(status_code=400, detail=f"bad interpreter: {interp}")

        # Defense in depth: agent runs the deny-list too.
        bad, reason = check_dangerous(cmd)
        if bad:
            return JSONResponse(
                status_code=403,
                content={
                    "ok": False, "exit_code": -1, "stdout": "", "stderr": f"deny-list: {reason}",
                    "error_code": "PERMISSION_DENIED",
                    "duration_ms": 0,
                },
            )

        result = await asyncio.to_thread(run_command, cmd, interp, timeout)
        return JSONResponse(content=result)

    @app.post("/v1/file/push")
    async def file_push(request: Request, path: str = Query(...)) -> JSONResponse:
        _auth(request)
        if not path or any(ch in path for ch in ("\n", "\r", "\x00")):
            raise HTTPException(status_code=400, detail="bad path")
        data = await request.body()
        dst = Path(path)
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            # Write to a sibling temp file, then rename — atomic on Windows.
            fd, tmp_str = tempfile.mkstemp(prefix=dst.name + ".", dir=str(dst.parent))
            with os.fdopen(fd, "wb") as fp:
                fp.write(data)
            os.replace(tmp_str, dst)
        except OSError as e:
            raise HTTPException(status_code=400, detail=f"write failed: {e}") from None
        return JSONResponse({"ok": True, "bytes": len(data), "path": str(dst)})

    @app.get("/v1/file/pull")
    async def file_pull(request: Request, path: str = Query(...)) -> Response:
        _auth(request)
        src = Path(path)
        if not src.exists():
            raise HTTPException(status_code=404, detail="not found")
        if not src.is_file():
            raise HTTPException(status_code=400, detail="path is not a regular file")
        return StreamingResponse(_stream_file(src), media_type="application/octet-stream")

    return app


async def _stream_file(path: Path, chunk: int = 64 * 1024):
    """Yield ``chunk``-sized byte slices off disk; lets large files flow without slurping."""
    with path.open("rb") as fp:
        while True:
            data = fp.read(chunk)
            if not data:
                break
            yield data


# ─── Entrypoint ──────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="wlb-agent", description=__doc__.splitlines()[0])
    p.add_argument(
        "--config",
        default=os.environ.get("WLB_AGENT_CONFIG", "wlb-agent.toml"),
        help="Path to wlb-agent.toml (default: ./wlb-agent.toml).",
    )
    p.add_argument("--bind", default=None, help="Override bind address.")
    p.add_argument("--port", type=int, default=None, help="Override listen port.")
    args = p.parse_args(argv)

    cfg = load_config(Path(args.config))
    if args.bind:
        cfg.bind = args.bind
    if args.port:
        cfg.port = args.port

    token = cfg.load_token()
    if not token or len(token) < 16:
        raise SystemExit("wlb-agent: token must be >= 16 chars (use `wlb-agent --gen-token` to make one)")

    app = build_app(token)

    try:
        import uvicorn       # type: ignore[import-not-found]
    except ModuleNotFoundError:                 # pragma: no cover
        raise SystemExit("wlb-agent needs uvicorn: pip install 'uvicorn[standard]'") from None

    print(
        f"wlb-agent v{__version__} listening on "
        f"{'https' if cfg.use_tls else 'http'}://{cfg.bind}:{cfg.port}",
        file=sys.stderr,
    )
    uvicorn.run(
        app,
        host=cfg.bind,
        port=cfg.port,
        ssl_certfile=cfg.tls_cert if cfg.use_tls else None,
        ssl_keyfile=cfg.tls_key if cfg.use_tls else None,
        log_level="info",
    )


if __name__ == "__main__":                       # pragma: no cover — invocation entrypoint
    main()
