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
from dataclasses import dataclass
from typing import Any

from wlb.infra.config import load_active
from wlb.infra.result import Result, fail, ok
from wlb.infra.tools_config import ToolSpec, find_tool, load_tools
from wlb.infra.workspace import is_safe_host, iso_timestamp, workspace_path
from wlb.transport.base import Transport

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
