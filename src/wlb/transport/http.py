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

import os
import time
from pathlib import Path
from typing import Any

import httpx

from wlb.transport.base import Interpreter, ShellResult, Transport


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


class HttpTransport(Transport):
    name = "http"
    supports_files = True
    supports_streaming = False   # progress streaming → M3

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
