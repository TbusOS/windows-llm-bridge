"""cmd capability — execute a command via cmd.exe /c.

The capability layer is thin: it enforces permissions, calls the
transport, and wraps the result in our standard ``Result[T]`` shape.
Transport-specific concerns (SSH connection management, HTTP retry,
local subprocess spawning) live in the transport, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wlb.infra.result import Result, fail, ok
from wlb.transport.base import Transport


@dataclass(frozen=True)
class CmdOutput:
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
        }


async def execute(
    transport: Transport,
    cmd: str,
    *,
    timeout: int = 30,
    allow_dangerous: bool = False,
) -> Result[CmdOutput]:
    """Run ``cmd`` through ``cmd.exe /c <cmd>``.

    Args:
        transport: an active Transport (ssh / local / http / hybrid).
        cmd: the command line to run on the Windows host.
        timeout: seconds before the command is killed.
        allow_dangerous: bypass ASK-level permission checks. DENY is never bypassed.

    Returns:
        Result[CmdOutput] — ``ok=True`` on exit code 0, ``ok=False`` otherwise.
        Permission denials return ``error.code = PERMISSION_DENIED``.
        Non-zero exit returns ``error.code = SHELL_NONZERO_EXIT`` with
        stdout/stderr/exit_code on ``error.details``.
    """
    perm = await transport.check_permissions(
        "cmd.execute",
        {"cmd": cmd, "allow_dangerous": allow_dangerous},
    )
    if perm.behavior == "deny":
        return fail(
            code="PERMISSION_DENIED",
            message=perm.reason or "Command blocked by permission policy",
            suggestion=perm.suggestion or "",
            category="permission",
            details={
                "matched_rule": perm.matched_rule,
                "attempted_command": cmd,
            },
        )
    if perm.behavior == "ask" and not allow_dangerous:
        return fail(
            code="PERMISSION_DENIED",
            message=perm.reason or "Command needs explicit confirmation",
            suggestion=perm.suggestion or "Re-run with --allow-dangerous after confirming",
            category="permission",
            details={
                "behavior": "ask",
                "matched_rule": perm.matched_rule,
                "attempted_command": cmd,
            },
        )

    r = await transport.shell(cmd, interpreter="cmd", timeout=timeout)

    if not r.ok:
        return fail(
            code=r.error_code or "SHELL_NONZERO_EXIT",
            message=(r.stderr or "command failed").strip(),
            suggestion=_suggest_for(r.error_code),
            category="transport",
            details={
                "stdout": r.stdout,
                "stderr": r.stderr,
                "exit_code": r.exit_code,
            },
            timing_ms=r.duration_ms,
        )

    return ok(
        data=CmdOutput(
            stdout=r.stdout,
            stderr=r.stderr,
            exit_code=r.exit_code,
            duration_ms=r.duration_ms,
        ),
        timing_ms=r.duration_ms,
    )


def _suggest_for(code: str | None) -> str:
    mapping = {
        "TIMEOUT_SHELL": "Increase --timeout, or use the streaming variant (M2)",
        "TRANSPORT_NOT_CONFIGURED": "Run: wlb setup ssh (or set WLB_SSH_HOST in .env)",
        "TRANSPORT_NOT_SUPPORTED": "This transport is not yet implemented — see PLAN.md",
        "SSH_AUTH_FAILED": "Check key permissions and authorized_keys on the Windows side",
        "SSH_HOST_UNREACHABLE": "Confirm sshd is running on the Windows host: Get-Service sshd",
    }
    return mapping.get(code or "", "See docs/errors.md for details")
