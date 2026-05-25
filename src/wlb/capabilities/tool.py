"""tool capability — invoke pre-declared Windows tools by name.

Reads ``workspace/wlb-tools.toml`` for tool specs (see
:mod:`wlb.infra.tools_config`). For each call:

1. Validate user-supplied args (declared names present, no shell-meta chars).
2. Format the command via ``str.format_map(args)``.
3. Wrap in a workdir-changing prefix if the spec sets one.
4. Run via the active transport, capturing full stdout + stderr.
5. Save the captured output under
   ``workspace/hosts/<host>/tools/<name>/<ts>.log``.
6. Scan for ``progress_re`` / ``success_re`` / ``failure_re`` matches.
7. Return a domain-level ``Result[ToolRunOutput]``.

The capability does NOT bypass the permission engine — the underlying
transport's ``check_permissions`` still runs against the formatted command.
A tool spec may set ``allow_dangerous = true`` to bypass ASK-level rules
(but not DENY-level rules) when the legitimate command would otherwise
trip the deny-list.
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wlb.infra.config import load_active
from wlb.infra.result import Result, fail, ok
from wlb.infra.tools_config import ToolSpec, find_tool, load_tools
from wlb.infra.workspace import is_safe_host, iso_timestamp, workspace_path
from wlb.transport.base import StreamEvent, Transport

# Reject anything that opens up command injection through arg substitution.
# The tool's command_template author trusts the static template, not the
# dynamic values — we keep that trust intact by refusing values that could
# turn a single tool call into a multi-statement shell sequence.
_UNSAFE_ARG_CHARS = re.compile(r"[\n\r\x00;&|<>`$]")

_LOG_TAIL_LINES = 50


@dataclass(frozen=True)
class ToolRunOutput:
    tool: str
    command_invoked: str
    exit_code: int
    duration_ms: int
    stdout_tail: str
    progress_percent: int | None
    success: bool
    success_match: str | None
    failure_match: str | None
    log_path: str
    interpreter: str
    via_transport: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "command_invoked": self.command_invoked,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "stdout_tail": self.stdout_tail,
            "progress_percent": self.progress_percent,
            "success": self.success,
            "success_match": self.success_match,
            "failure_match": self.failure_match,
            "log_path": self.log_path,
            "interpreter": self.interpreter,
            "via_transport": self.via_transport,
        }


@dataclass(frozen=True)
class ToolStreamEvent:
    """One event emitted by :func:`run_tool_stream`.

    Wraps :class:`wlb.transport.base.StreamEvent` with tool-level enrichments:

    - ``kind="line"`` — raw line from stdout / stderr (re-yielded as-is).
    - ``kind="progress"`` — ``progress_re`` matched; ``percent`` is set.
    - ``kind="match"`` — ``success_re`` or ``failure_re`` matched;
      ``pattern_label`` identifies which.
    - ``kind="done"`` — terminal event. ``output`` carries the same
      :class:`ToolRunOutput` shape :func:`run_tool` would return; ``ok``
      is the verdict (False on TOOL_FAILED-equivalent paths). On
      transport-level failure (timeout / connection lost), ``error_code``
      preserves the original transport code.
    """

    kind: str                         # "line" | "progress" | "match" | "done"
    line: str | None = None
    stream: str | None = None         # "stdout" / "stderr" for line kind
    percent: int | None = None        # for progress kind
    pattern_label: str | None = None  # "success" / "failure" for match kind
    match: str | None = None          # the matched substring
    ok: bool = True                   # for done kind
    output: ToolRunOutput | None = None  # for done kind
    error_code: str | None = None     # for done kind (transport-level errors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "line": self.line,
            "stream": self.stream,
            "percent": self.percent,
            "pattern_label": self.pattern_label,
            "match": self.match,
            "ok": self.ok,
            "output": self.output.to_dict() if self.output else None,
            "error_code": self.error_code,
        }


# ─── list / show ─────────────────────────────────────────────────


async def list_tools() -> Result[dict[str, Any]]:
    """Return every declared tool, plus any load-time warnings.

    Doesn't run anything — safe to call before any transport is configured.
    """
    specs, warnings, path = load_tools()
    return ok(
        data={
            "tools_file": str(path),
            "tools": [
                {
                    "name": s.name,
                    "description": s.description,
                    "interpreter": s.interpreter,
                    "args": list(s.args),
                    "timeout": s.timeout,
                }
                for s in specs
            ],
            "warnings": warnings,
        }
    )


async def show_tool(name: str) -> Result[dict[str, Any]]:
    """Return the full spec for one tool. Useful for the LLM to inspect templates."""
    spec, warnings, path = find_tool(name)
    if spec is None:
        return fail(
            code="TOOL_NOT_FOUND",
            message=f"no tool named {name!r} in {path}",
            suggestion=(
                "Run `wlb tool list` to see what's declared, or add a "
                "[tool.<name>] section to your wlb-tools.toml."
            ),
            category="tool",
            details={"tool": name, "tools_file": str(path), "warnings": warnings},
        )
    return ok(data={"tools_file": str(path), "spec": spec.to_dict(), "warnings": warnings})


# ─── run_tool ────────────────────────────────────────────────────


async def run_tool(
    transport: Transport,
    name: str,
    args: dict[str, str] | None = None,
) -> Result[ToolRunOutput]:
    """Run a declared tool over the active transport.

    Args:
        transport: an active Transport.
        name: tool name as declared in ``wlb-tools.toml``.
        args: dict of placeholder values for the command template.
    """
    args = args or {}
    spec, warnings, path = find_tool(name)
    if spec is None:
        return fail(
            code="TOOL_NOT_FOUND",
            message=f"no tool named {name!r} in {path}",
            suggestion=(
                "Run `wlb tool list` to see what's declared, or add a "
                "[tool.<name>] section to your wlb-tools.toml."
            ),
            category="tool",
            details={"tool": name, "tools_file": str(path), "warnings": warnings},
        )

    # ── validate args ────────────────────────────────────────────
    for k, v in args.items():
        if not isinstance(v, str):
            return fail(
                code="TOOL_ARG_INVALID",
                message=f"arg {k!r}: value must be a string",
                suggestion="Convert values to strings before passing.",
                category="tool",
                details={"tool": name, "arg": k, "value_type": type(v).__name__},
            )
        if _UNSAFE_ARG_CHARS.search(v):
            return fail(
                code="TOOL_ARG_INVALID",
                message=f"arg {k!r}: value contains a forbidden character",
                suggestion=(
                    "Arguments cannot contain newlines, NULs, or shell metacharacters "
                    "(`;`, `&`, `|`, `<`, `>`, backtick, `$`)."
                ),
                category="tool",
                details={"tool": name, "arg": k, "value": v},
            )

    missing = [a for a in spec.args if a not in args]
    if missing:
        return fail(
            code="TOOL_ARG_MISSING",
            message=f"required args missing: {missing}",
            suggestion=(
                f"`wlb tool show {name}` lists the declared args; pass them "
                "as `--arg key=value` (CLI) or via the `args` dict (MCP)."
            ),
            category="tool",
            details={"tool": name, "missing": missing, "declared": spec.args},
        )

    # ── format command ───────────────────────────────────────────
    try:
        formatted = spec.command_template.format_map(args)
    except KeyError as e:
        # A placeholder in the template wasn't satisfied by args.
        return fail(
            code="TOOL_ARG_MISSING",
            message=f"command_template needs arg {e.args[0]!r}",
            suggestion=(
                f"Add `--arg {e.args[0]}=<value>` (CLI) or include it in the "
                "args dict (MCP)."
            ),
            category="tool",
            details={
                "tool": name,
                "missing": [e.args[0]],
                "command_template": spec.command_template,
            },
        )

    invoke = _wrap_workdir(formatted, spec)

    # ── run ──────────────────────────────────────────────────────
    started = time.monotonic()
    r = await transport.shell(
        invoke,
        interpreter=spec.interpreter,
        timeout=spec.timeout,
    )
    duration_ms = int((time.monotonic() - started) * 1000)

    # ── persist log ──────────────────────────────────────────────
    host_id = _host_id(transport)
    log_path = workspace_path(
        f"tools/{name}",
        f"{iso_timestamp()}.log",
        host=host_id,
    )
    _write_log(log_path, name, invoke, spec, r)

    # ── parse regexes ────────────────────────────────────────────
    combined = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
    progress = _last_progress_percent(combined, spec.progress_re)
    success_match = _first_match(combined, spec.success_re)
    failure_match = _first_match(combined, spec.failure_re)

    # ── transport-level error (timeout / connection / permission) ────
    if r.error_code in (
        "TIMEOUT_SHELL",
        "TIMEOUT_CONNECT",
        "SSH_CONNECTION_LOST",
        "SSH_AUTH_FAILED",
        "SSH_HOST_UNREACHABLE",
        "SSH_KEY_NOT_FOUND",
        "SSH_HOSTKEY_REJECTED",
        "TRANSPORT_NOT_CONFIGURED",
        "TRANSPORT_NOT_SUPPORTED",
        "PERMISSION_DENIED",
    ):
        return fail(
            code=r.error_code,
            message=(r.stderr or f"{name}: transport-level error").strip(),
            suggestion=_suggest_for(r.error_code),
            category="transport" if r.error_code != "PERMISSION_DENIED" else "permission",
            details={
                "tool": name,
                "command_invoked": invoke,
                "exit_code": r.exit_code,
                "stdout_tail": _tail(r.stdout, _LOG_TAIL_LINES),
                "stderr_tail": _tail(r.stderr, _LOG_TAIL_LINES),
                "log_path": str(log_path),
            },
            timing_ms=duration_ms,
        )

    output = ToolRunOutput(
        tool=name,
        command_invoked=invoke,
        exit_code=r.exit_code,
        duration_ms=duration_ms,
        stdout_tail=_tail(r.stdout, _LOG_TAIL_LINES),
        progress_percent=progress,
        success=False,
        success_match=success_match,
        failure_match=failure_match,
        log_path=str(log_path),
        interpreter=spec.interpreter,
        via_transport=transport.name,
    )

    # ── verdict: did the tool succeed? ───────────────────────────
    if failure_match is not None:
        return fail(
            code="TOOL_FAILED",
            message=f"{name}: failure pattern matched ({failure_match!r})",
            suggestion=(
                "Inspect the log at error.details.log_path for the full output."
            ),
            category="tool",
            details=output.to_dict(),
            timing_ms=duration_ms,
        )

    if spec.success_re and success_match is None:
        return fail(
            code="TOOL_FAILED",
            message=f"{name}: success pattern {spec.success_re!r} did not match",
            suggestion=(
                "The tool ran but the expected success marker was absent — "
                "check the log under error.details.log_path."
            ),
            category="tool",
            details=output.to_dict(),
            timing_ms=duration_ms,
        )

    if not r.ok:
        return fail(
            code="TOOL_FAILED",
            message=f"{name}: exited with status {r.exit_code}",
            suggestion="Read error.details.stdout_tail / log_path for the failure context.",
            category="tool",
            details=output.to_dict(),
            timing_ms=duration_ms,
        )

    return ok(
        data=ToolRunOutput(**{**output.to_dict(), "success": True}),
        artifacts=[log_path],
        timing_ms=duration_ms,
    )


# ─── run_tool_stream (M3.1) ──────────────────────────────────────


async def run_tool_stream(
    transport: Transport,
    name: str,
    args: dict[str, str] | None = None,
) -> AsyncIterator[ToolStreamEvent]:
    """Stream a tool's output line-by-line; emit progress / match events live.

    Same validation + workdir wrapping + log writing as :func:`run_tool`,
    but events flow to the consumer as soon as each remote line arrives.

    Workflow:
        1. Look up the spec; validate args (same rules as run_tool).
        2. Open the log file in append mode and stream each line into it.
        3. For each ``line`` event from the transport, re-yield it AND run
           the spec's progress / success / failure regexes against that line.
           Regex hits become their own ``ToolStreamEvent("progress" | "match")``.
        4. The terminal ``done`` event carries the final verdict + the
           same ToolRunOutput payload :func:`run_tool` would produce.

    Streaming behavior depends on the transport: LocalTransport and
    SshTransport emit lines as they arrive; HttpTransport falls back to
    capture-then-replay (until M3.2 adds a streaming agent endpoint).
    """
    args = args or {}
    spec, warnings, _ = find_tool(name)
    if spec is None:
        yield ToolStreamEvent(
            kind="done", ok=False, error_code="TOOL_NOT_FOUND",
            line=f"tool {name!r} not declared in wlb-tools.toml",
        )
        return

    # ── validate args (mirrors run_tool exactly) ─────────────────
    for k, v in args.items():
        if not isinstance(v, str):
            yield ToolStreamEvent(
                kind="done", ok=False, error_code="TOOL_ARG_INVALID",
                line=f"arg {k!r}: value must be a string",
            )
            return
        if _UNSAFE_ARG_CHARS.search(v):
            yield ToolStreamEvent(
                kind="done", ok=False, error_code="TOOL_ARG_INVALID",
                line=f"arg {k!r}: value contains a forbidden character",
            )
            return

    missing = [a for a in spec.args if a not in args]
    if missing:
        yield ToolStreamEvent(
            kind="done", ok=False, error_code="TOOL_ARG_MISSING",
            line=f"required args missing: {missing}",
        )
        return

    try:
        formatted = spec.command_template.format_map(args)
    except KeyError as e:
        yield ToolStreamEvent(
            kind="done", ok=False, error_code="TOOL_ARG_MISSING",
            line=f"command_template needs arg {e.args[0]!r}",
        )
        return

    invoke = _wrap_workdir(formatted, spec)
    host_id = _host_id(transport)
    log_path = workspace_path(
        f"tools/{name}", f"{iso_timestamp()}.log", host=host_id,
    )

    # ── compile regex once each ──────────────────────────────────
    progress_rx = _safe_compile(spec.progress_re)
    success_rx = _safe_compile(spec.success_re)
    failure_rx = _safe_compile(spec.failure_re)

    last_progress: int | None = None
    success_match: str | None = None
    failure_match: str | None = None
    stdout_buf: list[str] = []
    stderr_buf: list[str] = []
    started = time.monotonic()

    # ── open log file in append mode (header first) ──────────────
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fp = log_path.open("a", encoding="utf-8", errors="replace")
    try:
        log_fp.write(
            f"# wlb tool log (stream)\n"
            f"# tool: {name}\n"
            f"# interpreter: {spec.interpreter}\n"
            f"# invoked: {invoke}\n"
            f"# ---- stream ----\n"
        )
        log_fp.flush()

        terminal_error_code: str | None = None
        exit_code: int = 0

        # ── consume transport stream ─────────────────────────────
        async for ev in transport.run_streaming(
            invoke, interpreter=spec.interpreter, timeout=spec.timeout,
        ):
            if ev.kind == "line":
                line = ev.line or ""
                # Mirror to log + buffer for stdout_tail.
                log_fp.write(f"[{ev.stream}] {line}\n")
                log_fp.flush()
                if ev.stream == "stderr":
                    stderr_buf.append(line)
                else:
                    stdout_buf.append(line)

                yield ToolStreamEvent(
                    kind="line", line=line, stream=ev.stream,
                )

                if progress_rx is not None:
                    m = progress_rx.search(line)
                    if m is not None:
                        try:
                            n = int(m.group(1))
                            if 0 <= n <= 100:
                                last_progress = n
                                yield ToolStreamEvent(kind="progress", percent=n)
                        except (IndexError, ValueError):
                            pass

                if success_match is None and success_rx is not None:
                    m = success_rx.search(line)
                    if m is not None:
                        success_match = m.group(0)
                        yield ToolStreamEvent(
                            kind="match", pattern_label="success", match=success_match,
                        )

                if failure_match is None and failure_rx is not None:
                    m = failure_rx.search(line)
                    if m is not None:
                        failure_match = m.group(0)
                        yield ToolStreamEvent(
                            kind="match", pattern_label="failure", match=failure_match,
                        )

            elif ev.kind == "done":
                exit_code = ev.exit_code
                terminal_error_code = ev.error_code
                break
            # Other event kinds from the transport are ignored — capabilities
            # define their own taxonomy.
    finally:
        log_fp.close()

    duration_ms = int((time.monotonic() - started) * 1000)
    stdout_text = "\n".join(stdout_buf)

    # ── transport-level error → preserve original code ───────────
    if terminal_error_code in _TRANSPORT_ERROR_CODES:
        yield ToolStreamEvent(
            kind="done", ok=False, error_code=terminal_error_code,
            line=f"{name}: transport-level error",
        )
        return

    output = ToolRunOutput(
        tool=name,
        command_invoked=invoke,
        exit_code=exit_code,
        duration_ms=duration_ms,
        stdout_tail=_tail(stdout_text, _LOG_TAIL_LINES),
        progress_percent=last_progress,
        success=False,
        success_match=success_match,
        failure_match=failure_match,
        log_path=str(log_path),
        interpreter=spec.interpreter,
        via_transport=transport.name,
    )

    # ── verdict (same logic as run_tool) ─────────────────────────
    if failure_match is not None:
        yield ToolStreamEvent(kind="done", ok=False, output=output)
        return
    if spec.success_re and success_match is None:
        yield ToolStreamEvent(kind="done", ok=False, output=output)
        return
    if exit_code != 0:
        yield ToolStreamEvent(kind="done", ok=False, output=output)
        return

    yield ToolStreamEvent(
        kind="done",
        ok=True,
        output=ToolRunOutput(**{**output.to_dict(), "success": True}),
    )


# ─── run_tool_with_progress (M3.10) ──────────────────────────────


async def run_tool_with_progress(
    transport: Transport,
    name: str,
    args: dict[str, str] | None = None,
    *,
    on_event: Callable[[ToolStreamEvent], Awaitable[None]] | None = None,
) -> Result[ToolRunOutput]:
    """Run a tool with mid-flight events, returning a single aggregated Result.

    Bridges the streaming + single-result patterns:

    - :func:`run_tool_stream` is for callers that consume events live (the
      Web UI WebSocket, the ``--stream`` CLI flag).
    - :func:`run_tool` is for callers that just want one structured result
      at the end.
    - :func:`run_tool_with_progress` is for callers that want BOTH —
      mid-flight notifications AND a single final :class:`Result`. The
      primary user is the MCP tool wrapper, which emits
      ``notifications/progress`` via ``ctx.report_progress`` as
      ``progress`` events arrive and returns one structured result when
      the run finishes.

    ``on_event`` is invoked for every :class:`ToolStreamEvent` from the
    underlying :func:`run_tool_stream`. It MUST NOT raise — any exception
    is swallowed so a misbehaving callback can't kill the run. The
    callback is awaited synchronously between events; keep it fast.

    The returned :class:`Result` shape matches :func:`run_tool` exactly,
    so callers can render it identically.
    """
    last_event: ToolStreamEvent | None = None

    async for ev in run_tool_stream(transport, name, args):
        if on_event is not None:
            try:
                await on_event(ev)
            except Exception:                  # noqa: BLE001 — best-effort
                pass
        if ev.kind == "done":
            last_event = ev
            break

    return _stream_done_to_result(name, last_event)


def _stream_done_to_result(
    name: str,
    done: ToolStreamEvent | None,
) -> Result[ToolRunOutput]:
    """Convert the terminal ``done`` event into the same Result shape run_tool returns.

    Lossy by construction — the streaming path emits less verbose error
    messages than :func:`run_tool` does. We restore the suggestion + tool
    name + log path where possible so MCP clients still see useful
    diagnostics.
    """
    if done is None:
        return fail(
            code="TOOL_STREAM_INCOMPLETE",
            message=f"{name}: stream ended without a terminal done event",
            suggestion=(
                "Likely a transport bug — file an issue at "
                "https://github.com/TbusOS/windows-llm-bridge/issues."
            ),
            category="tool",
            details={"tool": name},
        )

    output = done.output

    # Setup-stage failures: no output, plain error_code (TOOL_NOT_FOUND, etc.).
    if output is None:
        code = done.error_code or "TOOL_FAILED"
        return fail(
            code=code,
            message=done.line or f"{name}: {code}",
            suggestion=_suggest_for(code),
            category="transport" if code in _TRANSPORT_ERROR_CODES else "tool",
            details={"tool": name, "stream_done": done.to_dict()},
        )

    if done.ok:
        return ok(
            data=output,
            artifacts=[Path(output.log_path)] if output.log_path else [],
            timing_ms=output.duration_ms,
        )

    # ok=False with an output → TOOL_FAILED equivalent (failure regex hit,
    # expected success regex missed, or non-zero exit). Map to the same
    # error code run_tool would emit so MCP clients see consistent codes.
    return fail(
        code="TOOL_FAILED",
        message=f"{name}: exit_code={output.exit_code}, see log",
        suggestion="Inspect details.log_path for the full output.",
        category="tool",
        details=output.to_dict(),
        timing_ms=output.duration_ms,
    )


_TRANSPORT_ERROR_CODES = {
    "TIMEOUT_SHELL", "TIMEOUT_CONNECT",
    "SSH_CONNECTION_LOST", "SSH_AUTH_FAILED", "SSH_HOST_UNREACHABLE",
    "SSH_KEY_NOT_FOUND", "SSH_HOSTKEY_REJECTED",
    "TRANSPORT_NOT_CONFIGURED", "TRANSPORT_NOT_SUPPORTED",
    "PERMISSION_DENIED",
    "HTTP_AUTH_FAILED", "HTTP_HOST_UNREACHABLE",
    "HTTP_AGENT_ERROR", "HTTP_BAD_RESPONSE",
    "SYSTEM_DEPENDENCY_MISSING",
}


def _safe_compile(pattern: str | None) -> re.Pattern[str] | None:
    if not pattern:
        return None
    try:
        return re.compile(pattern, re.MULTILINE)
    except re.error:
        return None


# ─── helpers ─────────────────────────────────────────────────────


def _wrap_workdir(cmd: str, spec: ToolSpec) -> str:
    """Prepend a workdir-changing prefix if the spec requests one."""
    if not spec.workdir:
        return cmd
    if spec.interpreter == "cmd":
        # pushd / popd on cmd.exe handle drive switches; the command is grouped
        # with ``&&`` so a failed pushd doesn't try to run the command anyway.
        return f'pushd "{spec.workdir}" && {cmd} & popd'
    if spec.interpreter == "powershell":
        return (
            f'Push-Location "{spec.workdir}"; '
            f"try {{ {cmd} }} finally {{ Pop-Location }}"
        )
    # raw: user owns cwd semantics; do not wrap.
    return cmd


def _last_progress_percent(text: str, pattern: str | None) -> int | None:
    if not pattern or not text:
        return None
    try:
        rx = re.compile(pattern, re.MULTILINE)
    except re.error:
        return None
    last: int | None = None
    for m in rx.finditer(text):
        try:
            n = int(m.group(1))
        except (IndexError, ValueError):
            continue
        if 0 <= n <= 100:
            last = n
    return last


def _first_match(text: str, pattern: str | None) -> str | None:
    if not pattern or not text:
        return None
    try:
        rx = re.compile(pattern, re.MULTILINE)
    except re.error:
        return None
    m = rx.search(text)
    if m is None:
        return None
    return m.group(0)


def _tail(text: str | None, n_lines: int) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-n_lines:])


def _host_id(transport: Transport) -> str:
    """Stable identifier for the workspace path. Falls back to transport name."""
    host = getattr(transport, "host", None)
    if isinstance(host, str) and host and is_safe_host(host):
        return host
    return transport.name


def _write_log(log_path: Any, name: str, invoke: str, spec: ToolSpec, r: Any) -> None:
    header = (
        f"# wlb tool log\n"
        f"# tool: {name}\n"
        f"# interpreter: {spec.interpreter}\n"
        f"# invoked: {invoke}\n"
        f"# exit_code: {r.exit_code}\n"
        f"# duration_ms: {r.duration_ms}\n"
        f"# ---- stdout ----\n"
    )
    body_out = r.stdout or ""
    body_err = r.stderr or ""
    log_path.write_text(
        header + body_out + "\n# ---- stderr ----\n" + body_err,
        encoding="utf-8",
        errors="replace",
    )


def _suggest_for(code: str | None) -> str:
    mapping = {
        "TIMEOUT_SHELL": "Raise the spec's timeout, or split the operation into smaller steps.",
        "TIMEOUT_CONNECT": "Network reachable? Raise WLB_SSH_TIMEOUT.",
        "SSH_CONNECTION_LOST": "Retry; check Get-WinEvent -LogName OpenSSH/Operational on Windows.",
        "SSH_AUTH_FAILED": "Check key permissions and authorized_keys on the Windows side.",
        "SSH_HOST_UNREACHABLE": "Confirm sshd is running: Get-Service sshd.",
        "SSH_KEY_NOT_FOUND": "Check WLB_SSH_KEY path.",
        "TRANSPORT_NOT_CONFIGURED": "Run: wlb setup ssh.",
        "TRANSPORT_NOT_SUPPORTED": "Switch transport, or finish the implementation — see PLAN.md.",
        "PERMISSION_DENIED": (
            "The tool's formatted command matched the dangerous-pattern deny-list. "
            "Either narrow the command, or set allow_dangerous=true on the tool spec."
        ),
    }
    return mapping.get(code or "", "See docs/errors.md for details")


# Unused import guard — load_active is referenced indirectly by callers
# that want to display the active host; keep the symbol available.
_ = load_active
