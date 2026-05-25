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
    resources: list[str] = []

    class _MockMcp:
        def tool(self):  # noqa: ANN202
            def deco(fn):  # noqa: ANN202
                registered.append(fn.__name__)
                return fn

            return deco

        def resource(self, uri, **kw):  # noqa: ANN001, ANN202
            def deco(fn):  # noqa: ANN202
                resources.append(uri)
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
        "wlb_skill_list", "wlb_skill_get",                          # skill (M3.11)
    }
    missing = expected - set(registered)
    assert not missing, f"missing MCP tools: {missing}"
    # Fail loudly on accidental duplicates.
    assert len(registered) == len(set(registered)), f"duplicate registration: {registered}"

    # Resources registered (M3.11): templated skill-pack URI.
    assert "wlb-skill://{name}" in resources, f"missing MCP resource: {resources}"
