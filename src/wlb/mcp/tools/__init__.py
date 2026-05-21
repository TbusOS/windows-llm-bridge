"""MCP tool registration package.

Each submodule exposes a ``register(mcp)`` function which attaches one
capability area's tools to the FastMCP instance. The server module calls
``register_all()`` for the full set.
"""

from wlb.mcp.tools import cmd as cmd_tools
from wlb.mcp.tools import filesync as filesync_tools
from wlb.mcp.tools import powershell as powershell_tools
from wlb.mcp.tools import status as status_tools


def register_all(mcp) -> None:  # noqa: ANN001 — FastMCP import lazy in server
    status_tools.register(mcp)
    cmd_tools.register(mcp)
    powershell_tools.register(mcp)
    filesync_tools.register(mcp)
