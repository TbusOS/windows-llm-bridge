"""Verify the MCP layer registers the expected tools."""

from __future__ import annotations


def test_register_all_attaches_expected_tools() -> None:
    """Mock FastMCP and confirm register_all() attaches every wlb tool.

    We don't import the real ``mcp`` package here — we want this test to
    pass even on systems where the FastMCP server can't bind a stdio
    handle. The wlb.mcp.tools.register_all() function only needs a
    duck-typed object with a ``tool()`` decorator.

    Acts as a regression guard: if a new capability ships without an MCP
    tool wired up, this test catches the omission.
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

    expected = {
        "wlb_status", "wlb_describe",                              # status
        "wlb_cmd",                                                  # cmd
        "wlb_powershell",                                           # powershell
        "wlb_push", "wlb_pull",                                     # filesync (M2.1)
        "wlb_tool_list", "wlb_tool_show", "wlb_tool_run",           # tool (M2.3)
    }
    missing = expected - set(registered)
    assert not missing, f"missing MCP tools: {missing}"
    # Fail loudly on accidental duplicates.
    assert len(registered) == len(set(registered)), f"duplicate registration: {registered}"
