"""MCP tools: wlb_tool_list, wlb_tool_show, wlb_tool_run."""

from __future__ import annotations

from typing import Any

from wlb.capabilities.tool import list_tools as cap_list_tools
from wlb.capabilities.tool import run_tool as cap_run_tool
from wlb.capabilities.tool import show_tool as cap_show_tool
from wlb.mcp.transport_factory import build_transport


def register(mcp) -> None:  # noqa: ANN001
    @mcp.tool()
    async def wlb_tool_list() -> dict[str, Any]:
        """List every tool declared in ``workspace/wlb-tools.toml``.

        Each tool entry includes name, description, interpreter, declared
        args, and timeout. To see the full spec (including the command
        template and regex patterns), call ``wlb_tool_show`` for a
        specific name.

        Returns:
            Standard Result {ok, data: {tools_file, tools, warnings},
            error, artifacts, timing_ms}.
        """
        r = await cap_list_tools()
        return r.to_dict()

    @mcp.tool()
    async def wlb_tool_show(name: str) -> dict[str, Any]:
        """Return the full declared spec for one tool.

        Use this before invoking ``wlb_tool_run`` to learn the required
        args and command template. The spec is read-only — wlb won't
        modify ``wlb-tools.toml`` from here.

        Args:
            name: tool name as declared in ``[tool.<name>]``.

        Returns:
            Standard Result {ok, data: {tools_file, spec, warnings},
            error, artifacts, timing_ms}.
        """
        r = await cap_show_tool(name)
        return r.to_dict()

    @mcp.tool()
    async def wlb_tool_run(name: str, args: dict[str, str] | None = None) -> dict[str, Any]:
        """Run a declared tool on the Windows host.

        The tool's command template is formatted with ``args``, prefixed
        with a workdir-changing wrapper if the spec sets one, then run
        through the configured interpreter (cmd / powershell / raw). The
        full output is saved under
        ``workspace/hosts/<host>/tools/<name>/<ts>.log``.

        When to use:
            - Anything declared in ``wlb-tools.toml``: vendor flashers,
              signers, packagers — the wlb operator vetted these.

        When NOT to use:
            - Ad-hoc commands. Use ``wlb_cmd`` / ``wlb_powershell``.
            - Operations that need progress streaming during the run
              (M2.3 captures full output and surfaces progress
              post-completion; live streaming is M3+).

        Args:
            name: tool name as declared in the config.
            args: dict of placeholder values for the command template
                (e.g. ``{"image": "C:\\stage\\fw.bin", "port": "COM3"}``).
                String keys, string values. Forbidden characters in
                values: newlines, NULs, and shell metacharacters
                (``;`` ``&`` ``|`` ``<`` ``>`` backtick ``$``).

        Returns:
            Standard Result {ok, data: {tool, command_invoked, exit_code,
            duration_ms, stdout_tail, progress_percent, success,
            success_match, failure_match, log_path, interpreter,
            via_transport}, error, artifacts, timing_ms}.

            On failure, ``error.code`` is one of: ``TOOL_NOT_FOUND``,
            ``TOOL_ARG_MISSING``, ``TOOL_ARG_INVALID``, ``TOOL_FAILED``,
            or any transport-level code (timeout / connection / auth).
        """
        transport = build_transport()
        r = await cap_run_tool(transport, name, args or {})
        return r.to_dict()
