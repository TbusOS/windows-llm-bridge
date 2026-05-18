"""Permission engine — default deny-list for dangerous Windows commands.

Design (mirrors alb's permissions.py):

- ``DANGEROUS_PATTERNS`` is a list of (regex, reason) tuples.
- The default ``check_permissions`` in each Transport calls ``default_check``
  which runs the command string against the patterns.
- A match returns ``PermissionResult(behavior="deny", ...)`` with a stable
  ``matched_rule`` so the caller can produce a helpful error.

Behaviors:
    "allow" — proceed
    "ask"   — refuse unless the caller passes ``allow_dangerous=True``
    "deny"  — refuse unconditionally (cannot be bypassed by ``allow_dangerous``)

The patterns target both ``cmd.exe`` syntax (``format c:``, ``del /q /s C:\\*``,
``rmdir /s /q C:\\``) and ``powershell`` syntax (``Format-Volume``,
``Remove-Item -Recurse -Force C:\\``, ``Stop-Computer``, ``bcdedit``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Behavior = Literal["allow", "ask", "deny"]


@dataclass(frozen=True)
class PermissionResult:
    behavior: Behavior
    reason: str | None = None
    matched_rule: str | None = None
    suggestion: str | None = None


# ─── Default deny-list ──────────────────────────────────────────
#
# All regexes are matched case-insensitively (re.IGNORECASE).
# Add new patterns above the catch-alls so they take precedence.
DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    # ── cmd.exe destructive ─────────────────────────────────────
    (r"^\s*format\s+[a-z]:", "format a drive"),
    (r"^\s*del\s+/[a-z\s]*[qsf][a-z\s]*\s+[a-z]:\\?\*?", "del /q /s on a drive root"),
    (r"^\s*erase\s+/[a-z\s]*[qsf][a-z\s]*\s+[a-z]:\\?\*?", "erase /q /s on a drive root"),
    (r"^\s*rmdir\s+/s\s+/q\s+[a-z]:\\?", "rmdir /s /q on a drive root"),
    (r"^\s*rd\s+/s\s+/q\s+[a-z]:\\?", "rd /s /q on a drive root"),

    # ── cmd.exe boot / shutdown ─────────────────────────────────
    (r"^\s*shutdown\s+/[a-z]*[sr][a-z]*\b", "shutdown / restart"),
    (r"^\s*bcdedit\s+/(delete|export|import|set)\b", "bcdedit modification"),
    (r"^\s*diskpart\b", "interactive diskpart session"),

    # ── cmd.exe disk-image ──────────────────────────────────────
    (r"\\\\\\.\\PhysicalDrive\d+", "raw physical-drive access"),

    # ── PowerShell destructive ──────────────────────────────────
    (r"\bFormat-Volume\b", "Format-Volume"),
    (r"\bClear-Disk\b", "Clear-Disk"),
    (r"\bInitialize-Disk\b", "Initialize-Disk"),
    (
        r"\bRemove-Item\b[^|;]*-Recurse[^|;]*-Force[^|;]*\s+[a-z]:\\?\s*['\"]?$",
        "Remove-Item -Recurse -Force on a drive root",
    ),
    (
        r"\bRemove-Item\b[^|;]*-Recurse[^|;]*-Force[^|;]*\s+[a-z]:\\?\\?\s*\*",
        "Remove-Item -Recurse -Force C:\\*",
    ),

    # ── PowerShell shutdown / boot ─────────────────────────────
    (r"\bStop-Computer\b", "Stop-Computer"),
    (r"\bRestart-Computer\b", "Restart-Computer"),
    (r"\bSet-ExecutionPolicy\s+(Unrestricted|Bypass)\b", "loosen ExecutionPolicy"),

    # ── Registry mass-mutation ─────────────────────────────────
    (r"\bReg\s+delete\s+HKLM\b", "reg delete HKLM"),
    (r"\bRemove-Item\b[^|;]*HKLM:", "Remove-Item HKLM"),

    # ── Users / sec ────────────────────────────────────────────
    (r"\bnet\s+user\s+\S+\s+/delete\b", "net user delete"),
    (r"\bRemove-LocalUser\b", "Remove-LocalUser"),

    # ── Services & firewall mass-mutation ──────────────────────
    (r"\bnetsh\s+advfirewall\s+set\s+allprofiles\s+state\s+off\b", "disable Windows Firewall"),
    (r"\bSet-MpPreference\b[^|;]*-DisableRealtimeMonitoring\s+\$?true", "disable Defender"),
]

_COMPILED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pat, re.IGNORECASE), reason) for pat, reason in DANGEROUS_PATTERNS
]


async def default_check(
    transport_name: str,
    action: str,
    input_data: dict,
) -> PermissionResult:
    """Default permission check: deny dangerous patterns, allow everything else.

    Each Transport may override ``check_permissions()`` to layer
    transport-specific rules on top (e.g. the HTTP transport may reject
    actions that would never come from a trusted controller).
    """
    cmd = input_data.get("cmd", "") or ""
    if not cmd:
        return PermissionResult(behavior="allow")

    for pattern, reason in _COMPILED:
        if pattern.search(cmd):
            return PermissionResult(
                behavior="deny",
                reason=f"Matches dangerous pattern: {reason}",
                matched_rule=pattern.pattern,
                suggestion=(
                    "Scope the command to a specific path, or run it manually "
                    "after confirming the intent."
                ),
            )

    return PermissionResult(behavior="allow")
