"""SSH transport — drives a Windows OpenSSH Server via asyncssh.

Connection model (M1.3):
    Connections are pooled by
    ``(host, port, user, key_path, known_hosts, connect_timeout)``.
    The first ``shell()`` / ``health()`` call dials and stores the
    connection in :mod:`wlb.transport.ssh_pool`. Subsequent calls with
    the same parameters reuse it — one SSH handshake per host instead of
    one per command. When ``run()`` raises :class:`asyncssh.ConnectionLost`,
    the entry is marked dead and the next acquire redials.

    The CLI's ``run_async`` wrapper flushes the pool on each invocation;
    the MCP server keeps it for the lifetime of the process (which is
    where pooling actually pays off).

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

from wlb.transport import ssh_pool
from wlb.transport.base import Interpreter, ShellResult, Transport

_PWSH_PRIMARY = "pwsh.exe"
_PWSH_FALLBACK = "powershell.exe"

# SFTP file-type constant (from POSIX, used by asyncssh.SFTPAttrs.type).
_SFTP_TYPE_DIR = 2


def _attrs_is_dir(attrs: Any) -> bool:
    """True if an SFTPAttrs describes a directory."""
    return getattr(attrs, "type", None) == _SFTP_TYPE_DIR


async def _remote_size(sftp: Any, remote: str) -> int:
    """Best-effort size lookup for the post-push artifact on the remote side.

    For directories, recursive walking via SFTP is expensive — return 0 and
    let the caller decide whether to inspect further. For files, returns
    the file size or 0 if the stat fails.
    """
    try:
        attrs = await sftp.stat(remote)
    except Exception:  # noqa: BLE001 — best-effort
        return 0
    if _attrs_is_dir(attrs):
        return 0
    return int(getattr(attrs, "size", 0) or 0)


def _local_size(path: Path) -> int:
    """Local-side size: file → bytes; dir → sum of file sizes (one os.walk)."""
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


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
        conn, open_err = await self._acquire(started)
        if conn is None:
            return open_err  # type: ignore[return-value]

        # Connection is owned by the pool — do NOT close here. ConnectionLost
        # during run() triggers ssh_pool.mark_dead(key) so the next acquire
        # redials cleanly.
        if interpreter == "powershell":
            return await self._run_powershell(conn, cmd, timeout=timeout, started_at=started)
        return await self._run_cmd(conn, cmd, timeout=timeout, started_at=started)

    async def push(self, local: Path, remote: str) -> ShellResult:
        return await self._sftp_transfer(local, remote, direction="push")

    async def pull(self, remote: str, local: Path) -> ShellResult:
        return await self._sftp_transfer(local, remote, direction="pull")

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
        conn, open_err = await self._acquire(started)
        out["connect_ms"] = int((time.monotonic() - started) * 1000)
        if conn is None:
            out["stage"] = (open_err.stderr if open_err else "open failed")
            out["error_code"] = open_err.error_code if open_err else None
            return out

        # Connection is pool-owned; we don't close it here either.
        key = self._pool_key()
        try:
            ver = await conn.run("ver", timeout=10)
            if ver.exit_status == 0 and ver.stdout:
                lines = [ln.strip() for ln in ver.stdout.splitlines() if ln.strip()]
                out["windows_version"] = lines[-1] if lines else ""
            else:
                out["windows_version"] = "<probe failed>"
        except asyncssh.ConnectionLost as e:  # pool entry just died
            ssh_pool.mark_dead(key)
            out["windows_version"] = f"<probe error: ConnectionLost: {e}>"
        except Exception as e:  # noqa: BLE001 — best-effort probe
            out["windows_version"] = f"<probe error: {type(e).__name__}>"

        out["powershell"] = "<not available>"
        for binary in (_PWSH_PRIMARY, _PWSH_FALLBACK):
            try:
                probe = await conn.run(
                    f'{binary} -NoProfile -Command "$PSVersionTable.PSVersion.ToString()"',
                    timeout=10,
                )
            except asyncssh.ConnectionLost:
                ssh_pool.mark_dead(key)
                break
            except Exception:  # noqa: BLE001 — best-effort probe
                continue
            if probe.exit_status == 0:
                out["powershell"] = f"{binary} {(probe.stdout or '').strip()}"
                break

        out["ok"] = True
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

    def _pool_key(self) -> tuple:
        """Identity tuple used to key the connection pool.

        Every constructor field that affects the dial parameters is included
        so two transports with different params get different connections.
        """
        return (
            self.host,
            self.port,
            self.user,
            self.key_path,
            self.known_hosts,
            self.connect_timeout,
        )

    async def _acquire(
        self, started_at: float
    ) -> tuple[asyncssh.SSHClientConnection | None, ShellResult | None]:
        """Return a pooled connection or a structured ShellResult on dial failure."""
        key = self._pool_key()

        async def _dial() -> asyncssh.SSHClientConnection:
            return await asyncio.wait_for(
                asyncssh.connect(**self._connect_kwargs()),
                timeout=self.connect_timeout,
            )

        try:
            conn = await ssh_pool.acquire(key, _dial)
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
            # Connection died mid-run; evict so the next acquire redials.
            ssh_pool.mark_dead(self._pool_key())
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
                ssh_pool.mark_dead(self._pool_key())
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

    async def _sftp_transfer(
        self,
        local: Path,
        remote: str,
        *,
        direction: str,
    ) -> ShellResult:
        """Common SFTP path for push (direction='push') and pull ('pull').

        Push: ``local`` exists on the controller, ``remote`` is created on the host.
        Pull: ``remote`` exists on the host, ``local`` is created on the controller.

        Recursion is auto-detected by checking the source side (``local`` for push,
        ``sftp.stat(remote).type`` for pull).
        """
        cfg_err = self._validate_config()
        if cfg_err is not None:
            return cfg_err

        # Source-side existence check for push — fast-fail before opening SFTP.
        if direction == "push" and not local.exists():
            return ShellResult(
                ok=False,
                stderr=f"local path not found: {local}",
                error_code="LOCAL_PATH_NOT_FOUND",
            )

        started = time.monotonic()
        conn, open_err = await self._acquire(started)
        if conn is None:
            return open_err  # type: ignore[return-value]

        key = self._pool_key()
        try:
            async with await conn.start_sftp_client() as sftp:
                if direction == "push":
                    recurse = local.is_dir()
                    await sftp.put(str(local), remote, recurse=recurse, preserve=False)
                    bytes_transferred = await _remote_size(sftp, remote)
                else:  # pull
                    remote_attrs = await sftp.stat(remote)
                    recurse = _attrs_is_dir(remote_attrs)
                    local.parent.mkdir(parents=True, exist_ok=True)
                    await sftp.get(remote, str(local), recurse=recurse, preserve=False)
                    bytes_transferred = _local_size(local)
        except asyncssh.SFTPNoSuchFile as e:
            code = "FILE_NOT_FOUND" if direction == "pull" else "REMOTE_PATH_INVALID"
            return ShellResult(
                ok=False, stderr=str(e),
                duration_ms=int((time.monotonic() - started) * 1000),
                error_code=code,
            )
        except asyncssh.SFTPPermissionDenied as e:
            return ShellResult(
                ok=False, stderr=str(e),
                duration_ms=int((time.monotonic() - started) * 1000),
                error_code="REMOTE_PATH_INVALID",
            )
        except asyncssh.SFTPError as e:
            return ShellResult(
                ok=False, stderr=str(e),
                duration_ms=int((time.monotonic() - started) * 1000),
                error_code="SFTP_ERROR",
            )
        except asyncssh.ChannelOpenError as e:
            # Most likely SFTP subsystem not enabled on the remote sshd.
            return ShellResult(
                ok=False, stderr=str(e),
                duration_ms=int((time.monotonic() - started) * 1000),
                error_code="SFTP_NOT_AVAILABLE",
            )
        except asyncssh.ConnectionLost as e:
            ssh_pool.mark_dead(key)
            return ShellResult(
                ok=False, stderr=str(e),
                duration_ms=int((time.monotonic() - started) * 1000),
                error_code="SSH_CONNECTION_LOST",
            )
        except OSError as e:
            # Local-side filesystem error during pull / read during push.
            return ShellResult(
                ok=False, stderr=str(e),
                duration_ms=int((time.monotonic() - started) * 1000),
                error_code="LOCAL_PATH_NOT_FOUND",
            )

        return ShellResult(
            ok=True,
            stdout=f"transferred {bytes_transferred} bytes ({direction})",
            duration_ms=int((time.monotonic() - started) * 1000),
            artifacts=[local],
        )
