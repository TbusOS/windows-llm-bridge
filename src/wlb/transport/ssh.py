"""SSH transport — talks to Windows OpenSSH Server.

M0 ships a thin placeholder so the registry / describe / smoke tests pass.
M1 will replace ``shell()`` and ``health()`` with real asyncssh-backed
implementations and add a connection pool.

The placeholder returns a structured "not yet implemented" ShellResult so
contributors see the intended return shape immediately, and any premature
caller gets a clear error code instead of an opaque AttributeError.
"""

from __future__ import annotations

from typing import Any

from wlb.transport.base import Interpreter, ShellResult, Transport


class SshTransport(Transport):
    name = "ssh"
    supports_files = True       # M2 — SFTP
    supports_streaming = True   # M2

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

    async def shell(
        self,
        cmd: str,
        *,
        interpreter: Interpreter = "cmd",
        timeout: int = 30,
    ) -> ShellResult:
        if not self.host:
            return ShellResult(
                ok=False,
                stderr="WLB_SSH_HOST is not set",
                error_code="TRANSPORT_NOT_CONFIGURED",
            )
        # M1: open asyncssh connection, run cmd via interpreter, collect output.
        # For M0 we return a clear "not yet" so smoke tests pass and any
        # eager caller sees the intended Result shape.
        return ShellResult(
            ok=False,
            stderr=(
                "SshTransport.shell is a placeholder in M0. "
                "Implementation lands in M1 — see PLAN.md."
            ),
            error_code="TRANSPORT_NOT_SUPPORTED",
        )

    async def health(self) -> dict[str, Any]:
        return {
            "ok": False,
            "transport": self.name,
            "host": self.host or "<unset>",
            "port": self.port,
            "user": self.user or "<unset>",
            "configured": bool(self.host and self.user),
            "stage": "M0 placeholder — real connect lands in M1",
        }
