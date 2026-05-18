"""powershell capability — execute a command via pwsh.exe / powershell.exe.

The transport decides which interpreter binary to actually invoke (PS 7+
``pwsh.exe`` is preferred when present). This capability provides the
structured Result wrapping and permission check.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wlb.infra.result import Result, fail, ok
from wlb.transport.base import Transport


@dataclass(frozen=True)
class PowerShellOutput:
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
    script: str,
    *,
    timeout: int = 60,
    allow_dangerous: bool = False,
) -> Result[PowerShellOutput]:
    """Run ``script`` through ``pwsh -Command`` (or ``powershell -Command``).

    The transport prefers ``pwsh.exe`` (PS 7+) but falls back to
    ``powershell.exe`` (Windows PS 5) when ``pwsh`` is not on PATH.

    Args:
        transport: an active Transport.
        script: PowerShell source to execute. Use ``ConvertTo-Json`` for
                structured output you intend to parse on the controller side.
        timeout: seconds. Defaults higher than ``cmd`` because PowerShell
                 startup is heavier.
        allow_dangerous: bypass ASK-level permission checks. DENY is never bypassed.
    """
    perm = await transport.check_permissions(
        "powershell.execute",
        {"cmd": script, "allow_dangerous": allow_dangerous},
    )
    if perm.behavior == "deny":
        return fail(
            code="PERMISSION_DENIED",
            message=perm.reason or "Script blocked by permission policy",
            suggestion=perm.suggestion or "",
            category="permission",
            details={
                "matched_rule": perm.matched_rule,
                "attempted_script": script,
            },
        )
    if perm.behavior == "ask" and not allow_dangerous:
        return fail(
            code="PERMISSION_DENIED",
            message=perm.reason or "Script needs explicit confirmation",
            suggestion=perm.suggestion or "Re-run with --allow-dangerous after confirming",
            category="permission",
            details={
                "behavior": "ask",
                "matched_rule": perm.matched_rule,
                "attempted_script": script,
            },
        )

    r = await transport.shell(script, interpreter="powershell", timeout=timeout)

    if not r.ok:
        return fail(
            code=r.error_code or "SHELL_NONZERO_EXIT",
            message=(r.stderr or "script failed").strip(),
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
        data=PowerShellOutput(
            stdout=r.stdout,
            stderr=r.stderr,
            exit_code=r.exit_code,
            duration_ms=r.duration_ms,
        ),
        timing_ms=r.duration_ms,
    )


def _suggest_for(code: str | None) -> str:
    mapping = {
        "TIMEOUT_SHELL": "Increase --timeout; PowerShell startup is heavier than cmd",
        "TRANSPORT_NOT_CONFIGURED": "Run: wlb setup ssh (or set WLB_SSH_HOST in .env)",
        "TRANSPORT_NOT_SUPPORTED": "This transport is not yet implemented — see PLAN.md",
        "POWERSHELL_NOT_AVAILABLE": (
            "Install PowerShell 7+ (winget install --id Microsoft.Powershell) "
            "or ensure Windows PowerShell 5 is on PATH"
        ),
    }
    return mapping.get(code or "", "See docs/errors.md for details")
