"""Hybrid transport — picks the best concrete transport per operation.

Planned for M2. For ``shell`` it prefers SSH; for ``push/pull`` it prefers
SFTP if available and falls back to HTTP multipart through the wlb-agent.
"""

from __future__ import annotations

from typing import Any

from wlb.transport.base import Interpreter, ShellResult, Transport


class HybridTransport(Transport):
    name = "hybrid"
    supports_files = True
    supports_streaming = True

    def __init__(self, *, sub_transports: list[Transport] | None = None) -> None:
        self.sub_transports = sub_transports or []

    async def shell(
        self,
        cmd: str,
        *,
        interpreter: Interpreter = "cmd",
        timeout: int = 30,
    ) -> ShellResult:
        return ShellResult(
            ok=False,
            stderr="HybridTransport is planned for M2 — see PLAN.md.",
            error_code="TRANSPORT_NOT_SUPPORTED",
        )

    async def health(self) -> dict[str, Any]:
        return {
            "ok": False,
            "transport": self.name,
            "sub_count": len(self.sub_transports),
            "stage": "M2 planned",
        }
