"""MCP tool: wlb_powershell."""

from __future__ import annotations

from typing import Any

from wlb.capabilities.powershell import execute as ps_execute
from wlb.mcp.transport_factory import build_transport


def register(mcp) -> None:  # noqa: ANN001
    @mcp.tool()
    async def wlb_powershell(
        script: str,
        timeout: int = 60,
        allow_dangerous: bool = False,
    ) -> dict[str, Any]:
        """Execute a PowerShell script on the Windows host.

        The transport picks pwsh.exe (PS 7+) if available, otherwise falls
        back to powershell.exe (Windows PS 5). The script runs with
        ``-NoProfile -NonInteractive`` defaults.

        When to use:
            - Reading WMI / CIM data (``Get-ComputerInfo``, ``Get-CimInstance``).
            - Anything where structured output helps: pipe through
              ``ConvertTo-Json`` to get parsable text back.
            - Tasks that depend on PowerShell modules (``Microsoft.PowerShell.*``).

        When NOT to use:
            - Plain command-line tools — use ``wlb_cmd``, it's lighter.
            - Long-running interactive sessions — these need PTY (M3).

        Safety:
            - Same permission deny-list as ``wlb_cmd`` plus PowerShell-specific
              entries (``Format-Volume``, ``Stop-Computer``, ``Set-ExecutionPolicy``).

        Args:
            script: PowerShell source to execute.
            timeout: seconds, default 60 (PowerShell startup is heavier than cmd).
            allow_dangerous: bypass ASK-level permission checks. DENY unaffected.

        Returns:
            Standard Result {ok, data: {stdout, stderr, exit_code, duration_ms},
            error, artifacts, timing_ms}.
        """
        transport = build_transport()
        r = await ps_execute(
            transport, script, timeout=timeout, allow_dangerous=allow_dangerous
        )
        return r.to_dict()
