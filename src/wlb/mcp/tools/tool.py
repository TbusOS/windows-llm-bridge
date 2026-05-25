"""MCP tools: wlb_tool_list, wlb_tool_show, wlb_tool_run."""

from __future__ import annotations

from typing import Any

from wlb.capabilities.tool import ToolStreamEvent
from wlb.capabilities.tool import list_tools as cap_list_tools
from wlb.capabilities.tool import run_tool as cap_run_tool
from wlb.capabilities.tool import run_tool_with_progress as cap_run_tool_with_progress
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
    async def wlb_tool_run(
        name: str,
        args: dict[str, str] | None = None,
        ctx=None,                           # noqa: ANN001 — fastmcp Context auto-injected
    ) -> dict[str, Any]:
        """Run a declared tool on the Windows host.

        The tool's command template is formatted with ``args``, prefixed
        with a workdir-changing wrapper if the spec sets one, then run
        through the configured interpreter (cmd / powershell / raw). The
        full output is saved under
        ``workspace/hosts/<host>/tools/<name>/<ts>.log``.

        Progress notifications (M3.10):
            When the calling MCP client supplied a ``progressToken``
            (FastMCP exposes it via ``ctx.report_progress``), wlb_tool_run
            emits standard ``notifications/progress`` messages whenever
            the tool's ``progress_re`` matches a fresh percentage in the
            live output. Failure-pattern hits surface as ``ctx.warning``;
            a "still running, N lines so far" tick every 50 lines
            surfaces as ``ctx.info``. Final structured result is the same
            whether or not progress was requested.

        When to use:
            - Anything declared in ``wlb-tools.toml``: vendor flashers,
              signers, packagers — the wlb operator vetted these.

        When NOT to use:
            - Ad-hoc commands. Use ``wlb_cmd`` / ``wlb_powershell``.

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
        if ctx is None:
            # No FastMCP context (direct call from a test, or older client
            # that didn't expose Context) — short-circuit to the simpler
            # one-shot path.
            r = await cap_run_tool(transport, name, args or {})
            return r.to_dict()

        line_count = 0

        async def _on_event(ev: ToolStreamEvent) -> None:
            nonlocal line_count
            if ev.kind == "progress" and ev.percent is not None:
                await ctx.report_progress(
                    progress=float(ev.percent),
                    total=100.0,
                    message=f"{name}: {ev.percent}%",
                )
            elif ev.kind == "match" and ev.pattern_label == "failure":
                await ctx.warning(
                    f"{name}: failure pattern matched: {ev.match}"
                )
            elif ev.kind == "match" and ev.pattern_label == "success":
                await ctx.info(f"{name}: success pattern matched")
            elif ev.kind == "line":
                line_count += 1
                if line_count % 50 == 0:
                    await ctx.info(f"{name}: {line_count} lines streamed")
            elif ev.kind == "done":
                # Always cap progress at 100 on completion so the client's
                # progress bar settles, even if progress_re never hit 100%.
                await ctx.report_progress(
                    progress=100.0,
                    total=100.0,
                    message=f"{name}: done (ok={ev.ok})",
                )

        r = await cap_run_tool_with_progress(
            transport, name, args or {}, on_event=_on_event,
        )
        return r.to_dict()
