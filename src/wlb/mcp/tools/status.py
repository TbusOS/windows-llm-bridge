"""MCP tools: wlb_status, wlb_describe."""

from __future__ import annotations

from typing import Any

from wlb.capabilities.status import describe as cap_describe
from wlb.capabilities.status import status as cap_status
from wlb.mcp.transport_factory import build_transport


def register(mcp) -> None:  # noqa: ANN001
    @mcp.tool()
    async def wlb_status() -> dict[str, Any]:
        """Return the active transport's health snapshot.

        When to use:
            - At the start of a session to confirm the bridge is reachable.
            - When a subsequent command fails with a transport error.

        Returns:
            Standard Result {ok, data: {version, transport, health},
            error, artifacts, timing_ms}.
        """
        transport = build_transport()
        r = await cap_status(transport)
        return r.to_dict()

    @mcp.tool()
    async def wlb_describe() -> dict[str, Any]:
        """Enumerate every transport and capability this build supports.

        Output is pure metadata — no transport call is made. Safe to call
        before any setup. Use this to discover the tool surface before
        deciding what to invoke.
        """
        r = await cap_describe()
        return r.to_dict()
