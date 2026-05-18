"""Verify the MCP layer registers the expected tools."""

from __future__ import annotations


def test_register_all_attaches_three_tools() -> None:
    """Mock FastMCP and confirm register_all() attaches the M0 tools.

    We don't import the real ``mcp`` package here — we want this test to
    pass even on systems where the FastMCP server can't bind a stdio
    handle. The wlb.mcp.tools.register_all() function only needs a
    duck-typed object with a ``tool()`` decorator.
    """
    registered: list[str] = []

    class _MockMcp:
        def tool(self):  # noqa: ANN202
            def deco(fn):  # noqa: ANN202
                registered.append(fn.__name__)
                return fn

            return deco

    from wlb.mcp.tools import register_all

    register_all(_MockMcp())

    assert "wlb_status" in registered
    assert "wlb_describe" in registered
    assert "wlb_cmd" in registered
    assert "wlb_powershell" in registered
    # Exactly 4 tools in M0 — fail loudly if we accidentally regress to fewer.
    assert len(registered) == 4
