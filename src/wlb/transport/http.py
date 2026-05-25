"""HTTP transport — talks to a wlb-agent micro-service on the Windows host.

Use case: SSH is blocked by network policy, but HTTPS to the Windows host
is allowed (corporate proxy, locked-down regulated environment). The
Windows side runs ``scripts/windows-agent/wlb-agent.py``; this transport
is the controller-side client.

Wire protocol (M2.4):

    POST /v1/shell        {cmd, interpreter, timeout}      → ShellResult JSON
    GET  /v1/health                                         → health JSON
    POST /v1/file/push    multipart: path, file            → {ok, bytes, ...}
    GET  /v1/file/pull?path=...                            → bytes

All requests carry ``Authorization: Bearer <token>``. Token is loaded
from a file (mode 600 expected) referenced by ``WLB_HTTP_TOKEN_FILE`` or
the profile's ``[http].token_file``. The token NEVER appears on the CLI
or in Claude conversations — see ``scripts/windows-agent/README.md`` for
the save-to-file workflow.

TLS:
    - Production: ``https://...``. The agent serves a TLS cert, the client
      validates against the system trust store or a custom CA bundle
      (``WLB_HTTP_CA_BUNDLE`` env / ``[http].ca_bundle``).
    - Lab only: ``http://...``. Verify_tls also has an explicit knob
      (``WLB_HTTP_VERIFY_TLS=0``) for self-signed corner cases; the agent
      README documents the trade-off.
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import websockets

from wlb.transport.base import Interpreter, PtySession, ShellResult, StreamEvent, Transport


def _read_token(token_file: str | None) -> str | None:
    """Read the bearer token from ``token_file`` (expanding ``~``).

    Returns None if the file is missing or unreadable — caller should
    surface this as ``TRANSPORT_NOT_CONFIGURED``. Warn (via the ``stderr``
    of returned errors) when the mode is not 0o600.
    """
    if not token_file:
        return None
    path = Path(os.path.expanduser(token_file))
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


class HttpPtySession(PtySession):
    """PTY backed by a WebSocket to wlb-agent's ``/v1/pty`` endpoint.

    Wire protocol (matches the agent side):

        client → text JSON, first message:
            {"type":"start","interpreter":...,"cols":...,"rows":...,"term_type":...}
        server → text JSON:
            {"type":"started","pid":N}                 (success, sent once)
            {"type":"error","code":...,"message":...}  (failure, then closes)
        client → binary frame                          → stdin bytes
        client → text JSON:
            {"type":"resize","cols":N,"rows":N}
            {"type":"close"}                            (graceful client-side end)
        server → binary frame                          → stdout bytes
        server → text JSON:
            {"type":"exit","exit_code":N}              (terminal — server closes after)

    Concurrency: :mod:`websockets` documents that one task may call ``send``
    while another calls ``recv``; we rely on that — :meth:`read` runs in the
    consumer's loop, :meth:`write` / :meth:`resize` run on user keystroke
    events. An internal asyncio.Lock guards :meth:`read` so multiple readers
    drain the same byte stream cleanly (the WS / API server pattern).
    """

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._closed = False
        self._exit_code: int | None = None
        self._read_buffer = bytearray()
        self._read_lock = asyncio.Lock()
        self._exit_event = asyncio.Event()

    async def read(self, n: int = 4096) -> bytes:
        if self._closed and not self._read_buffer:
            return b""
        async with self._read_lock:
            if self._read_buffer:
                chunk = bytes(self._read_buffer[:n])
                del self._read_buffer[:n]
                return chunk
            while True:
                try:
                    msg = await self._ws.recv()
                except websockets.ConnectionClosed:
                    self._closed = True
                    self._exit_event.set()
                    return b""
                if isinstance(msg, (bytes, bytearray, memoryview)):
                    data = bytes(msg)
                    if not data:
                        continue
                    if len(data) <= n:
                        return data
                    self._read_buffer.extend(data[n:])
                    return data[:n]
                # text frame = control
                try:
                    payload = json.loads(msg)
                except (ValueError, TypeError):
                    continue
                if payload.get("type") == "exit":
                    self._exit_code = int(payload.get("exit_code", -1) or 0)
                    self._exit_event.set()
                    self._closed = True
                    return b""
                # Unknown / other text frames — skip and keep reading.
                continue

    async def write(self, data: bytes) -> None:
        if self._closed or not data:
            return
        try:
            await self._ws.send(bytes(data))
        except websockets.ConnectionClosed:
            self._closed = True
            self._exit_event.set()

    async def resize(self, cols: int, rows: int) -> None:
        if self._closed:
            return
        try:
            await self._ws.send(json.dumps(
                {"type": "resize", "cols": int(cols), "rows": int(rows)}
            ))
        except websockets.ConnectionClosed:
            self._closed = True
            self._exit_event.set()

    async def wait(self) -> int:
        await self._exit_event.wait()
        return self._exit_code if self._exit_code is not None else -1

    async def close(self) -> None:
        if self._closed:
            self._exit_event.set()
            try:
                await self._ws.close()
            except Exception:                       # noqa: BLE001 — idempotent
                pass
            return
        self._closed = True
        # Hint the agent we're going away so it can flush an exit message.
        try:
            await self._ws.send(json.dumps({"type": "close"}))
        except Exception:                           # noqa: BLE001
            pass
        try:
            await self._ws.close()
        except Exception:                           # noqa: BLE001
            pass
        self._exit_event.set()


class HttpTransport(Transport):
    name = "http"
    supports_files = True
    supports_streaming = True    # real NDJSON streaming via /v1/shell/stream (M3.2)
    supports_pty = True          # WebSocket /v1/pty (M3.6)

    def __init__(
        self,
        *,
        base_url: str | None,
        token_file: str | None = None,
        token: str | None = None,    # accepted for tests that bypass file loading
        ca_bundle: str | None = None,
        connect_timeout: int = 10,
        verify_tls: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token_file = token_file
        self._token_override = token   # tests / wlb-agent-dev-mode only
        self.ca_bundle = ca_bundle
        self.connect_timeout = connect_timeout
        self.verify_tls = verify_tls

    # ── public surface ─────────────────────────────────────────────
    async def shell(
        self,
        cmd: str,
        *,
        interpreter: Interpreter = "cmd",
        timeout: int = 30,
    ) -> ShellResult:
        cfg_err = self._validate_config()
        if cfg_err is not None:
            return cfg_err

        started = time.monotonic()
        try:
            async with self._client() as client:
                resp = await client.post(
                    "/v1/shell",
                    json={"cmd": cmd, "interpreter": interpreter, "timeout": timeout},
                    timeout=timeout + 5,
                )
        except httpx.ConnectError as e:
            return self._fail("HTTP_HOST_UNREACHABLE", str(e), started)
        except httpx.ConnectTimeout as e:
            return self._fail("TIMEOUT_CONNECT", str(e), started)
        except httpx.ReadTimeout as e:
            return self._fail("TIMEOUT_SHELL", str(e), started)
        except httpx.HTTPError as e:
            return self._fail("HTTP_AGENT_ERROR", str(e), started)

        return self._parse_shell_response(resp, started)

    async def run_streaming(
        self,
        cmd: str,
        *,
        interpreter: Interpreter = "cmd",
        timeout: int = 30,
    ) -> AsyncIterator[StreamEvent]:
        """Stream NDJSON-encoded StreamEvent objects from the agent.

        Calls ``POST /v1/shell/stream`` on the wlb-agent (M3.2 endpoint),
        which returns ``application/x-ndjson`` — one JSON object per line,
        each matching the wlb StreamEvent shape. The terminal ``done`` is
        synthesized client-side if the agent closes the stream early
        (network drop, agent crash).
        """
        cfg_err = self._validate_config()
        if cfg_err is not None:
            yield StreamEvent(
                kind="done", exit_code=-1,
                error_code=cfg_err.error_code,
                duration_ms=0,
            )
            return

        started = time.monotonic()
        saw_done = False
        try:
            async with self._client() as client:
                async with client.stream(
                    "POST",
                    "/v1/shell/stream",
                    json={"cmd": cmd, "interpreter": interpreter, "timeout": timeout},
                    timeout=httpx.Timeout(self.connect_timeout, read=timeout + 30),
                ) as response:
                    if response.status_code == 401:
                        yield StreamEvent(
                            kind="done", exit_code=-1,
                            error_code="HTTP_AUTH_FAILED",
                            duration_ms=int((time.monotonic() - started) * 1000),
                        )
                        return
                    if response.status_code == 403:
                        yield StreamEvent(
                            kind="done", exit_code=-1,
                            error_code="PERMISSION_DENIED",
                            duration_ms=int((time.monotonic() - started) * 1000),
                        )
                        return
                    if response.status_code >= 500:
                        yield StreamEvent(
                            kind="done", exit_code=-1,
                            error_code="HTTP_AGENT_ERROR",
                            duration_ms=int((time.monotonic() - started) * 1000),
                        )
                        return
                    if response.status_code >= 400:
                        yield StreamEvent(
                            kind="done", exit_code=-1,
                            error_code="HTTP_AGENT_ERROR",
                            duration_ms=int((time.monotonic() - started) * 1000),
                        )
                        return

                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except (ValueError, TypeError):
                            # Garbled NDJSON — emit BAD_RESPONSE and stop reading.
                            yield StreamEvent(
                                kind="done", exit_code=-1,
                                error_code="HTTP_BAD_RESPONSE",
                                duration_ms=int((time.monotonic() - started) * 1000),
                            )
                            saw_done = True
                            return
                        if not isinstance(data, dict):
                            continue
                        kind = data.get("kind", "line")
                        ev = StreamEvent(
                            kind=kind,
                            line=data.get("line"),
                            stream=data.get("stream"),
                            percent=data.get("percent"),
                            pattern_label=data.get("pattern_label"),
                            match=data.get("match"),
                            exit_code=int(data.get("exit_code", 0) or 0),
                            error_code=data.get("error_code"),
                            duration_ms=int(
                                data.get(
                                    "duration_ms",
                                    int((time.monotonic() - started) * 1000),
                                )
                                or 0
                            ),
                        )
                        yield ev
                        if kind == "done":
                            saw_done = True
                            return
        except httpx.ConnectError as e:
            yield StreamEvent(
                kind="done", exit_code=-1,
                error_code="HTTP_HOST_UNREACHABLE",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return
        except httpx.ConnectTimeout as e:
            yield StreamEvent(
                kind="done", exit_code=-1,
                error_code="TIMEOUT_CONNECT",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return
        except httpx.ReadTimeout as e:
            yield StreamEvent(
                kind="done", exit_code=-1,
                error_code="TIMEOUT_SHELL",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return
        except httpx.HTTPError as e:
            yield StreamEvent(
                kind="done", exit_code=-1,
                error_code="HTTP_AGENT_ERROR",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return

        # Stream ended without a terminal done — synthesize one so callers
        # always see a final event.
        if not saw_done:
            yield StreamEvent(
                kind="done", exit_code=-1,
                error_code="HTTP_AGENT_ERROR",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

    async def push(self, local: Path, remote: str) -> ShellResult:
        cfg_err = self._validate_config()
        if cfg_err is not None:
            return cfg_err
        if not local.exists():
            return ShellResult(
                ok=False, stderr=f"local path not found: {local}",
                error_code="LOCAL_PATH_NOT_FOUND",
            )
        if local.is_dir():
            # M2.4 single-file only; recursive push deferred to M2.4.1
            return ShellResult(
                ok=False,
                stderr="HttpTransport.push currently supports single files only "
                       "(directory push lands in M2.4.1).",
                error_code="TRANSPORT_NOT_SUPPORTED",
            )

        started = time.monotonic()
        try:
            data = local.read_bytes()
            async with self._client() as client:
                resp = await client.post(
                    "/v1/file/push",
                    params={"path": remote},
                    content=data,
                    headers={"Content-Type": "application/octet-stream"},
                    timeout=60,
                )
        except httpx.ConnectError as e:
            return self._fail("HTTP_HOST_UNREACHABLE", str(e), started)
        except (httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            return self._fail("TIMEOUT_SHELL", str(e), started)
        except httpx.HTTPError as e:
            return self._fail("HTTP_AGENT_ERROR", str(e), started)

        return self._parse_file_response(resp, started, local=local, direction="push")

    async def pull(self, remote: str, local: Path) -> ShellResult:
        cfg_err = self._validate_config()
        if cfg_err is not None:
            return cfg_err

        started = time.monotonic()
        try:
            async with self._client() as client:
                resp = await client.get(
                    "/v1/file/pull",
                    params={"path": remote},
                    timeout=60,
                )
        except httpx.ConnectError as e:
            return self._fail("HTTP_HOST_UNREACHABLE", str(e), started)
        except (httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            return self._fail("TIMEOUT_SHELL", str(e), started)
        except httpx.HTTPError as e:
            return self._fail("HTTP_AGENT_ERROR", str(e), started)

        if resp.status_code == 401:
            return self._fail("HTTP_AUTH_FAILED", "401 from agent", started)
        if resp.status_code == 404:
            return self._fail("FILE_NOT_FOUND", f"remote {remote} not found", started)
        if resp.status_code >= 500:
            return self._fail("HTTP_AGENT_ERROR", f"{resp.status_code}: {resp.text[:200]}", started)
        if resp.status_code != 200:
            return self._fail("HTTP_BAD_RESPONSE", f"unexpected status {resp.status_code}", started)

        try:
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(resp.content)
        except OSError as e:
            return ShellResult(
                ok=False, stderr=str(e),
                duration_ms=int((time.monotonic() - started) * 1000),
                error_code="LOCAL_PATH_NOT_FOUND",
            )
        return ShellResult(
            ok=True,
            stdout=f"transferred {len(resp.content)} bytes (pull)",
            duration_ms=int((time.monotonic() - started) * 1000),
            artifacts=[local],
        )

    async def open_pty(
        self,
        *,
        interpreter: Interpreter = "cmd",
        cols: int = 80,
        rows: int = 24,
        term_type: str = "xterm-256color",
    ) -> PtySession:
        """Open a PTY-backed shell over WebSocket to wlb-agent ``/v1/pty`` (M3.6).

        - Translates ``http://``/``https://`` base_url to ``ws://``/``wss://``.
        - Authenticates with the same Bearer token as the REST endpoints.
        - TLS verify / CA bundle honor the same knobs as :meth:`shell`.
        - Sends the ``start`` first frame and waits (up to ``connect_timeout``)
          for ``started`` before returning. Any other first response (error
          message, binary, garbled JSON) raises :class:`ConnectionError`.

        Returns an :class:`HttpPtySession`. Caller owns ``close()`` (the WS
        connection stays open for the life of the session).
        """
        cfg_err = self._validate_config()
        if cfg_err is not None:
            raise ConnectionError(cfg_err.stderr or "http transport not configured")

        ws_url = self._ws_url("/v1/pty")
        if ws_url is None:
            raise ConnectionError(
                f"unrecognized URL scheme in base_url={self.base_url!r} — "
                "expected http:// or https://"
            )

        headers = [("Authorization", f"Bearer {self._token()}")]
        ssl_ctx = self._ws_ssl_context(ws_url)

        try:
            ws = await websockets.connect(
                ws_url,
                additional_headers=headers,
                ssl=ssl_ctx,
                open_timeout=self.connect_timeout,
                max_size=None,            # PTY chunks can be sizeable; no cap.
                ping_interval=20,
                ping_timeout=20,
            )
        except websockets.InvalidStatus as e:
            status = getattr(e.response, "status_code", "?")
            code = "HTTP_AUTH_FAILED" if status in (401, 403) else "HTTP_AGENT_ERROR"
            raise ConnectionError(f"agent rejected ws handshake: HTTP {status} ({code})") from None
        except (OSError, asyncio.TimeoutError) as e:
            raise ConnectionError(f"ws connect failed: {e}") from None
        except websockets.WebSocketException as e:
            raise ConnectionError(f"ws connect failed: {e}") from None

        # Send the start frame.
        try:
            await ws.send(json.dumps({
                "type": "start",
                "interpreter": interpreter,
                "cols": int(cols),
                "rows": int(rows),
                "term_type": term_type,
            }))
        except websockets.ConnectionClosed as e:
            raise ConnectionError(f"agent closed before start handshake: {e}") from None

        # Wait for the agent's first response (started / error).
        try:
            first = await asyncio.wait_for(ws.recv(), timeout=self.connect_timeout)
        except asyncio.TimeoutError as e:
            await ws.close()
            raise ConnectionError(
                f"no response from agent within {self.connect_timeout}s"
            ) from None
        except websockets.ConnectionClosed as e:
            raise ConnectionError(f"agent closed before sending started: {e}") from None

        if isinstance(first, (bytes, bytearray, memoryview)):
            await ws.close()
            raise ConnectionError("agent sent binary before started — protocol mismatch")
        try:
            payload = json.loads(first)
        except (ValueError, TypeError) as e:
            await ws.close()
            raise ConnectionError(f"agent sent non-JSON start response: {e}") from None

        kind = payload.get("type")
        if kind == "error":
            await ws.close()
            raise ConnectionError(
                f"agent error: {payload.get('code', '?')} — {payload.get('message', '')}"
            )
        if kind != "started":
            await ws.close()
            raise ConnectionError(f"unexpected first message from agent: {payload}")

        return HttpPtySession(ws=ws)

    async def health(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": False,
            "transport": self.name,
            "base_url": self.base_url or "<unset>",
            "configured": bool(self.base_url and self._token()),
        }
        if not out["configured"]:
            out["stage"] = "not configured — set WLB_HTTP_URL and WLB_HTTP_TOKEN_FILE"
            return out

        started = time.monotonic()
        try:
            async with self._client() as client:
                resp = await client.get("/v1/health", timeout=self.connect_timeout)
        except httpx.HTTPError as e:
            out["stage"] = f"unreachable: {type(e).__name__}: {e}"
            out["connect_ms"] = int((time.monotonic() - started) * 1000)
            return out

        out["connect_ms"] = int((time.monotonic() - started) * 1000)
        if resp.status_code == 401:
            out["stage"] = "agent rejected token (401)"
            out["error_code"] = "HTTP_AUTH_FAILED"
            return out
        if resp.status_code != 200:
            out["stage"] = f"agent returned status {resp.status_code}"
            out["error_code"] = "HTTP_AGENT_ERROR"
            return out
        try:
            body = resp.json()
        except ValueError:
            out["stage"] = "agent returned non-JSON to /v1/health"
            out["error_code"] = "HTTP_BAD_RESPONSE"
            return out
        out.update(body or {})
        out["ok"] = bool(body.get("ok"))
        return out

    # ── internals ──────────────────────────────────────────────────
    def _token(self) -> str | None:
        if self._token_override is not None:
            return self._token_override
        return _read_token(self.token_file)

    def _validate_config(self) -> ShellResult | None:
        if not self.base_url:
            return ShellResult(
                ok=False,
                stderr="WLB_HTTP_URL is not set",
                error_code="TRANSPORT_NOT_CONFIGURED",
            )
        if not self._token():
            return ShellResult(
                ok=False,
                stderr=(
                    "wlb-agent bearer token unavailable — set WLB_HTTP_TOKEN_FILE "
                    "to a file (mode 600) containing the token"
                ),
                error_code="TRANSPORT_NOT_CONFIGURED",
            )
        return None

    def _client(self) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {self._token()}"}
        verify: bool | str = self.verify_tls
        if self.verify_tls and self.ca_bundle:
            verify = os.path.expanduser(self.ca_bundle)
        return httpx.AsyncClient(
            base_url=self.base_url or "",
            headers=headers,
            timeout=httpx.Timeout(self.connect_timeout, connect=self.connect_timeout),
            verify=verify,
        )

    def _ws_url(self, path: str) -> str | None:
        """Translate ``base_url`` to a ``ws://`` / ``wss://`` URL with ``path`` appended.

        Returns ``None`` if base_url isn't http(s) — caller raises a
        configuration error so the user sees a clear message rather than
        a downstream connect failure.
        """
        if not self.base_url:
            return None
        if self.base_url.startswith("https://"):
            base = "wss://" + self.base_url[len("https://"):]
        elif self.base_url.startswith("http://"):
            base = "ws://" + self.base_url[len("http://"):]
        else:
            return None
        return base.rstrip("/") + path

    def _ws_ssl_context(self, ws_url: str) -> ssl.SSLContext | None:
        """Build an SSL context for ``wss://`` URLs honoring verify_tls / ca_bundle.

        Returns ``None`` for ``ws://`` so :func:`websockets.connect` doesn't
        try to TLS-wrap a plain socket.
        """
        if not ws_url.startswith("wss://"):
            return None
        if not self.verify_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        if self.ca_bundle:
            return ssl.create_default_context(cafile=os.path.expanduser(self.ca_bundle))
        return ssl.create_default_context()

    def _fail(self, code: str, msg: str, started: float) -> ShellResult:
        return ShellResult(
            ok=False, stderr=msg,
            duration_ms=int((time.monotonic() - started) * 1000),
            error_code=code,
        )

    def _parse_shell_response(self, resp: httpx.Response, started: float) -> ShellResult:
        if resp.status_code == 401:
            return self._fail("HTTP_AUTH_FAILED", "agent rejected token", started)
        if resp.status_code >= 500:
            return self._fail("HTTP_AGENT_ERROR", f"{resp.status_code}: {resp.text[:200]}", started)
        if resp.status_code == 403:
            return self._fail("PERMISSION_DENIED", resp.text[:200], started)
        if resp.status_code >= 400:
            return self._fail("HTTP_AGENT_ERROR", f"{resp.status_code}: {resp.text[:200]}", started)
        try:
            body = resp.json()
        except ValueError:
            return self._fail("HTTP_BAD_RESPONSE", "non-JSON response", started)
        if not isinstance(body, dict):
            return self._fail("HTTP_BAD_RESPONSE", "JSON body is not an object", started)

        exit_code = int(body.get("exit_code", 0) or 0)
        stdout = body.get("stdout", "") or ""
        stderr = body.get("stderr", "") or ""
        duration_ms = int(body.get("duration_ms", (time.monotonic() - started) * 1000))
        # Agent may report its own error_code (e.g. POWERSHELL_NOT_AVAILABLE).
        error_code = body.get("error_code")
        ok_flag = bool(body.get("ok", exit_code == 0))
        return ShellResult(
            ok=ok_flag,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            error_code=error_code if not ok_flag else None,
        )

    def _parse_file_response(
        self, resp: httpx.Response, started: float, *, local: Path, direction: str,
    ) -> ShellResult:
        if resp.status_code == 401:
            return self._fail("HTTP_AUTH_FAILED", "agent rejected token", started)
        if resp.status_code == 404:
            return self._fail("REMOTE_PATH_INVALID", "agent returned 404 for path", started)
        if resp.status_code == 403:
            return self._fail("PERMISSION_DENIED", resp.text[:200], started)
        if resp.status_code >= 500:
            return self._fail("HTTP_AGENT_ERROR", f"{resp.status_code}: {resp.text[:200]}", started)
        if resp.status_code >= 400:
            return self._fail("HTTP_AGENT_ERROR", f"{resp.status_code}: {resp.text[:200]}", started)
        try:
            body = resp.json()
        except ValueError:
            return self._fail("HTTP_BAD_RESPONSE", "non-JSON response", started)
        bytes_n = int(body.get("bytes", 0))
        return ShellResult(
            ok=True,
            stdout=f"transferred {bytes_n} bytes ({direction})",
            duration_ms=int((time.monotonic() - started) * 1000),
            artifacts=[local],
        )
