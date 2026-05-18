"""HTTP transport — talks to a wlb-agent micro-service on the Windows side.

Planned for M2. Used when OpenSSH is blocked by policy or the Windows host
is unreachable over SSH but reachable over HTTPS (e.g. through a corporate
proxy or reverse tunnel).
"""

from __future__ import annotations

from typing import Any

from wlb.transport.base import Interpreter, ShellResult, Transport


class HttpTransport(Transport):
    name = "http"
    supports_files = True
    supports_streaming = True

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        connect_timeout: int = 10,
    ) -> None:
        self.base_url = base_url
        self.token = token
        self.connect_timeout = connect_timeout

    async def shell(
        self,
        cmd: str,
        *,
        interpreter: Interpreter = "cmd",
        timeout: int = 30,
    ) -> ShellResult:
        return ShellResult(
            ok=False,
            stderr="HttpTransport is planned for M2 — see PLAN.md.",
            error_code="TRANSPORT_NOT_SUPPORTED",
        )

    async def health(self) -> dict[str, Any]:
        return {
            "ok": False,
            "transport": self.name,
            "base_url": self.base_url or "<unset>",
            "stage": "M2 planned",
        }
