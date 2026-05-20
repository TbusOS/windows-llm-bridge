"""SSH transport — drives a Windows OpenSSH Server via asyncssh.

Connection model (M1):
    Open a fresh asyncssh connection per ``shell()`` / ``health()`` call,
    close it after. This pays one SSH handshake per command — acceptable
    for the first release. M2 may add a per-host idle-pool to amortize.

Interpreter dispatch:
    The Windows OpenSSH Server default shell is ``cmd.exe``, so any
    command we send is interpreted by cmd.exe unless we explicitly
    invoke another interpreter inside the command string.

    - ``interpreter="cmd"`` / ``"raw"``:
        Send the command verbatim. cmd.exe runs it.
    - ``interpreter="powershell"``:
        Wrap as
        ``<binary> -NoProfile -NonInteractive -EncodedCommand <base64-utf16le>``
        Encoding the script with ``-EncodedCommand`` avoids all quote /
        escape gymnastics around the outer cmd.exe layer. We try
        ``pwsh.exe`` (PowerShell 7+) first and fall back to
        ``powershell.exe`` (Windows PowerShell 5.x) if pwsh isn't on PATH.

Error mapping:
    asyncssh / OS exceptions are caught at the public surface and
    translated to ``ShellResult.error_code`` strings registered in
    :mod:`wlb.infra.errors`. Public methods never raise.
"""

from __future__ import annotations

import asyncio
import base64
import os
import socket
import time
from pathlib import Path
from typing import Any

import asyncssh

from wlb.transport.base import Interpreter, ShellResult, Transport

_PWSH_PRIMARY = "pwsh.exe"
_PWSH_FALLBACK = "powershell.exe"


def _encode_powershell(script: str) -> str:
    """Base64-encode a script as UTF-16LE for PowerShell ``-EncodedCommand``."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _looks_like_missing_binary(proc: asyncssh.SSHCompletedProcess, _binary: str) -> bool:
    """True if ``proc`` looks like cmd.exe couldn't find the binary it tried to launch.

    cmd.exe says ``'pwsh.exe' is not recognized as an internal or external command...``
    when the binary isn't on PATH. Exit status is 1 in that case. We check the stderr
    substring to avoid mis-classifying real PowerShell errors as "not installed".
    """
    if proc.exit_status == 0:
        return False
    stderr = proc.stderr or ""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", "replace")
    return "is not recognized" in stderr.lower()


def _proc_to_result(proc: asyncssh.SSHCompletedProcess, started_at: float) -> ShellResult:
    """Convert an asyncssh SSHCompletedProcess to our ShellResult."""
    exit_status = proc.exit_status if proc.exit_status is not None else 0
    stdout = proc.stdout if isinstance(proc.stdout, str) else (proc.stdout or b"").decode("utf-8", "replace")
    stderr = proc.stderr if isinstance(proc.stderr, str) else (proc.stderr or b"").decode("utf-8", "replace")
    return ShellResult(
        ok=(exit_status == 0),
        exit_code=exit_status,
        stdout=stdout,
        stderr=stderr,
        duration_ms=int((time.monotonic() - started_at) * 1000),
        error_code=None if exit_status == 0 else "SHELL_NONZERO_EXIT",
    )


class SshTransport(Transport):
    name = "ssh"
    supports_files = True       # SFTP capability planned for M2
    supports_streaming = True   # streaming planned for M2

    def __init__(
        self,
        *,
        host: str | None,
        port: int = 22,
        user: str | None = None,
        key_path: str | None = None,
        known_hosts: str | None = None,
        connect_timeout: int = 10,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.key_path = key_path
        self.known_hosts = known_hosts
        self.connect_timeout = connect_timeout

    # ── Public surface ─────────────────────────────────────────────
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
        conn, open_err = await self._open(started)
        if conn is None:
            return open_err  # type: ignore[return-value]

        try:
            if interpreter == "powershell":
                return await self._run_powershell(conn, cmd, timeout=timeout, started_at=started)
            return await self._run_cmd(conn, cmd, timeout=timeout, started_at=started)
        finally:
            await _safe_close(conn)

    async def health(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": False,
            "transport": self.name,
            "host": self.host or "<unset>",
            "port": self.port,
            "user": self.user or "<unset>",
            "configured": bool(self.host and self.user),
        }
        if not out["configured"]:
            out["stage"] = "not configured — set WLB_SSH_HOST and WLB_SSH_USER"
            return out

        cfg_err = self._validate_config()
        if cfg_err is not None:
            out["stage"] = cfg_err.stderr
            out["error_code"] = cfg_err.error_code
            return out

        started = time.monotonic()
        conn, open_err = await self._open(started)
        out["connect_ms"] = int((time.monotonic() - started) * 1000)
        if conn is None:
            out["stage"] = (open_err.stderr if open_err else "open failed")
            out["error_code"] = open_err.error_code if open_err else None
            return out

        try:
            try:
                ver = await conn.run("ver", timeout=10)
                if ver.exit_status == 0 and ver.stdout:
                    lines = [ln.strip() for ln in ver.stdout.splitlines() if ln.strip()]
                    out["windows_version"] = lines[-1] if lines else ""
                else:
                    out["windows_version"] = "<probe failed>"
            except Exception as e:  # noqa: BLE001
                out["windows_version"] = f"<probe error: {type(e).__name__}>"

            out["powershell"] = "<not available>"
            for binary in (_PWSH_PRIMARY, _PWSH_FALLBACK):
                try:
                    probe = await conn.run(
                        f'{binary} -NoProfile -Command "$PSVersionTable.PSVersion.ToString()"',
                        timeout=10,
                    )
                except Exception:  # noqa: BLE001
                    continue
                if probe.exit_status == 0:
                    out["powershell"] = f"{binary} {(probe.stdout or '').strip()}"
                    break

            out["ok"] = True
        finally:
            await _safe_close(conn)
        return out

    # ── Internals ──────────────────────────────────────────────────
    def _validate_config(self) -> ShellResult | None:
        if not self.host:
            return ShellResult(
                ok=False,
                stderr="WLB_SSH_HOST is not set",
                error_code="TRANSPORT_NOT_CONFIGURED",
            )
        if self.key_path:
            resolved = Path(os.path.expanduser(self.key_path))
            if not resolved.exists():
                return ShellResult(
                    ok=False,
                    stderr=f"SSH key not found at {self.key_path}",
                    error_code="SSH_KEY_NOT_FOUND",
                )
        return None

    def _connect_kwargs(self) -> dict[str, Any]:
        kw: dict[str, Any] = {"host": self.host, "port": self.port}
        if self.user:
            kw["username"] = self.user
        if self.key_path:
            kw["client_keys"] = [os.path.expanduser(self.key_path)]
        # known_hosts handling:
        #   None / "" → asyncssh default (use ~/.ssh/known_hosts).
        #   "none" / "off" / "skip" → disable host-key check (testing only).
        #   any other string → treat as a path.
        if self.known_hosts:
            if self.known_hosts.lower() in ("none", "off", "skip"):
                kw["known_hosts"] = None
            else:
                kw["known_hosts"] = os.path.expanduser(self.known_hosts)
        return kw

    async def _open(self, started_at: float) -> tuple[asyncssh.SSHClientConnection | None, ShellResult | None]:
        """Open an SSH connection or return a structured ShellResult on failure."""
        try:
            conn = await asyncio.wait_for(
                asyncssh.connect(**self._connect_kwargs()),
                timeout=self.connect_timeout,
            )
            return conn, None
        except asyncio.TimeoutError:
            return None, ShellResult(
                ok=False,
                stderr=f"connect to {self.host}:{self.port} timed out after {self.connect_timeout}s",
                duration_ms=int((time.monotonic() - started_at) * 1000),
                error_code="TIMEOUT_CONNECT",
            )
        except asyncssh.HostKeyNotVerifiable as e:
            return None, ShellResult(
                ok=False, stderr=str(e),
                duration_ms=int((time.monotonic() - started_at) * 1000),
                error_code="SSH_HOSTKEY_REJECTED",
            )
        except asyncssh.PermissionDenied as e:
            return None, ShellResult(
                ok=False, stderr=str(e),
                duration_ms=int((time.monotonic() - started_at) * 1000),
                error_code="SSH_AUTH_FAILED",
            )
        except asyncssh.ConnectionLost as e:
            return None, ShellResult(
                ok=False, stderr=str(e),
                duration_ms=int((time.monotonic() - started_at) * 1000),
                error_code="SSH_CONNECTION_LOST",
            )
        except FileNotFoundError as e:
            # asyncssh raises this when client_keys=[path] doesn't exist
            # — _validate_config catches the common case, but guard anyway.
            return None, ShellResult(
                ok=False, stderr=str(e),
                duration_ms=int((time.monotonic() - started_at) * 1000),
                error_code="SSH_KEY_NOT_FOUND",
            )
        except (OSError, socket.gaierror) as e:
            return None, ShellResult(
                ok=False, stderr=str(e),
                duration_ms=int((time.monotonic() - started_at) * 1000),
                error_code="SSH_HOST_UNREACHABLE",
            )
        except asyncssh.Error as e:
            return None, ShellResult(
                ok=False, stderr=str(e),
                duration_ms=int((time.monotonic() - started_at) * 1000),
                error_code="SSH_HOST_UNREACHABLE",
            )

    async def _run_cmd(
        self,
        conn: asyncssh.SSHClientConnection,
        cmd: str,
        *,
        timeout: int,
        started_at: float,
    ) -> ShellResult:
        try:
            proc = await conn.run(cmd, timeout=timeout)
        except asyncssh.TimeoutError:
            return ShellResult(
                ok=False,
                stderr=f"command exceeded {timeout}s timeout",
                duration_ms=int((time.monotonic() - started_at) * 1000),
                error_code="TIMEOUT_SHELL",
            )
        except asyncssh.ConnectionLost as e:
            return ShellResult(
                ok=False, stderr=str(e),
                duration_ms=int((time.monotonic() - started_at) * 1000),
                error_code="SSH_CONNECTION_LOST",
            )
        return _proc_to_result(proc, started_at)

    async def _run_powershell(
        self,
        conn: asyncssh.SSHClientConnection,
        script: str,
        *,
        timeout: int,
        started_at: float,
    ) -> ShellResult:
        encoded = _encode_powershell(script)
        for binary in (_PWSH_PRIMARY, _PWSH_FALLBACK):
            remote = f"{binary} -NoProfile -NonInteractive -EncodedCommand {encoded}"
            try:
                proc = await conn.run(remote, timeout=timeout)
            except asyncssh.TimeoutError:
                return ShellResult(
                    ok=False,
                    stderr=f"powershell script exceeded {timeout}s timeout",
                    duration_ms=int((time.monotonic() - started_at) * 1000),
                    error_code="TIMEOUT_SHELL",
                )
            except asyncssh.ConnectionLost as e:
                return ShellResult(
                    ok=False, stderr=str(e),
                    duration_ms=int((time.monotonic() - started_at) * 1000),
                    error_code="SSH_CONNECTION_LOST",
                )
            if not _looks_like_missing_binary(proc, binary):
                return _proc_to_result(proc, started_at)
        # Both pwsh.exe and powershell.exe are missing on the remote PATH.
        return ShellResult(
            ok=False,
            stderr="neither pwsh.exe nor powershell.exe is available on the remote PATH",
            duration_ms=int((time.monotonic() - started_at) * 1000),
            error_code="POWERSHELL_NOT_AVAILABLE",
        )


async def _safe_close(conn: asyncssh.SSHClientConnection) -> None:
    """Close ``conn`` swallowing any tear-down race that asyncssh raises."""
    try:
        conn.close()
        await conn.wait_closed()
    except Exception:  # noqa: BLE001 — tear-down should never bubble
        pass
