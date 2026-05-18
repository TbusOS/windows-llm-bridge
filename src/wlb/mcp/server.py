"""MCP server entry point (``wlb-mcp`` command).

Starts a FastMCP server over stdio that exposes every wlb capability as a
tool. The ``mcp`` package is imported lazily so the CLI / tests / install
don't pay the import cost if the server never runs.
"""

from __future__ import annotations

import sys


def create_server():  # type: ignore[no-untyped-def]
    """Create and return a configured FastMCP instance.

    Kept as a free function so tests can introspect the registered tool
    list without going through stdio.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:
        raise SystemExit(
            f"[wlb-mcp] The `mcp` package is not installed: {e}\n"
            "Run: uv sync   (or: pip install mcp)"
        ) from None

    mcp = FastMCP(
        "wlb",
        instructions=(
            "windows-llm-bridge — Windows shell / tool bridge for LLM agents.\n"
            "Call wlb_status / wlb_describe first to learn the environment.\n"
            "All tools return structured { ok, data, error, artifacts } results."
        ),
    )
    from wlb.mcp.tools import register_all

    register_all(mcp)
    return mcp


def main() -> None:
    """Entry point referenced by pyproject.toml ``[project.scripts]``."""
    try:
        mcp = create_server()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — top-level guard
        print(f"[wlb-mcp] Failed to start: {e}", file=sys.stderr)
        sys.exit(1)

    # FastMCP.run() defaults to stdio transport — correct for MCP clients
    # launching us as a subprocess (Claude Code, Cursor, Codex).
    mcp.run()


if __name__ == "__main__":
    main()
