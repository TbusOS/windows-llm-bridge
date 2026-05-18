"""MCP tool: wlb_cmd."""

from __future__ import annotations

from typing import Any

from wlb.capabilities.cmd import execute as cmd_execute
from wlb.mcp.transport_factory import build_transport


def register(mcp) -> None:  # noqa: ANN001
    @mcp.tool()
    async def wlb_cmd(
        cmd: str,
        timeout: int = 30,
        allow_dangerous: bool = False,
    ) -> dict[str, Any]:
        """Execute a command on the Windows host via cmd.exe /c.

        When to use:
            - Quick queries: ``ver``, ``ipconfig``, ``dir C:\\``.
            - Invoking command-line tools that ship as ``.exe`` on Windows.
            - One-off Windows-only commands.

        When NOT to use:
            - PowerShell-flavoured one-liners — use ``wlb_powershell``.
            - Long output (> ~5 KB) — prefer a streaming variant once M2 ships.
            - Destructive operations: the permission system denies by default.
              You can pass ``allow_dangerous=True`` for ASK-level commands,
              but DENY is never bypassable.

        Args:
            cmd: command line to run, just like you would type after ``cmd /c``.
            timeout: seconds, default 30.
            allow_dangerous: bypass ASK-level permission checks. DENY unaffected.

        Returns:
            Standard Result {ok, data: {stdout, stderr, exit_code, duration_ms},
            error, artifacts, timing_ms}.
        """
        transport = build_transport()
        r = await cmd_execute(
            transport, cmd, timeout=timeout, allow_dangerous=allow_dangerous
        )
        return r.to_dict()
