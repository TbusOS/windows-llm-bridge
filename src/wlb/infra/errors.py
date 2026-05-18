"""Error code catalog.

Every Result.error.code that wlb returns is registered here so:

- Tools generating docs can enumerate them.
- LLM clients can lookup a stable code → suggestion mapping.
- Regression: if a capability returns a code not in the catalog, tests fail.

Codes are stable identifiers — once shipped, do not rename. Add new codes,
mark old ones deprecated in the docstring.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    category: str
    default_message: str
    default_suggestion: str


# Minimum catalog for the M0 skeleton; expand as capabilities are added in M1+.
ERROR_CODES: dict[str, ErrorSpec] = {
    # ── transport ────────────────────────────────────────────────
    "TRANSPORT_NOT_CONFIGURED": ErrorSpec(
        "TRANSPORT_NOT_CONFIGURED",
        "transport",
        "No active transport configured",
        "Run: wlb setup ssh  (or set WLB_SSH_HOST in .env)",
    ),
    "TRANSPORT_NOT_SUPPORTED": ErrorSpec(
        "TRANSPORT_NOT_SUPPORTED",
        "transport",
        "Operation not supported by this transport",
        "Switch transport: --transport ssh  (or set WLB_TRANSPORT=ssh)",
    ),
    "SSH_AUTH_FAILED": ErrorSpec(
        "SSH_AUTH_FAILED",
        "transport",
        "SSH authentication failed",
        "Check key permissions (chmod 600), authorized_keys on the Windows side, "
        "and that the user actually owns the key. Run: wlb doctor",
    ),
    "SSH_HOST_UNREACHABLE": ErrorSpec(
        "SSH_HOST_UNREACHABLE",
        "transport",
        "Cannot reach SSH host",
        "Confirm the Windows host is up and OpenSSH Server is running. "
        "From PowerShell on the Windows side: Get-Service sshd",
    ),
    "SSH_HOSTKEY_REJECTED": ErrorSpec(
        "SSH_HOSTKEY_REJECTED",
        "transport",
        "Host key did not match known_hosts",
        "If the host key legitimately changed, update known_hosts. Otherwise "
        "this may be a MITM — do not bypass.",
    ),
    "SHELL_NONZERO_EXIT": ErrorSpec(
        "SHELL_NONZERO_EXIT",
        "transport",
        "Shell command exited with a non-zero status",
        "Inspect data.exit_code + data.stderr; fix the command or handle the failure",
    ),
    "POWERSHELL_NOT_AVAILABLE": ErrorSpec(
        "POWERSHELL_NOT_AVAILABLE",
        "transport",
        "Neither pwsh.exe nor powershell.exe is on PATH on the target",
        "Install PowerShell 7+ or ensure Windows PowerShell 5 is enabled",
    ),

    # ── host (Windows-side state) ────────────────────────────────
    "HOST_NOT_FOUND": ErrorSpec(
        "HOST_NOT_FOUND",
        "host",
        "Configured Windows host not reachable",
        "Run: wlb status",
    ),
    "HOST_REBOOTING": ErrorSpec(
        "HOST_REBOOTING",
        "host",
        "Host is rebooting / not yet ready",
        "Wait and retry, or run: wlb status",
    ),

    # ── permission ───────────────────────────────────────────────
    "PERMISSION_DENIED": ErrorSpec(
        "PERMISSION_DENIED",
        "permission",
        "Command blocked by permission policy",
        "Read error.details.matched_rule; scope the command, or pass "
        "--allow-dangerous after confirming the intent",
    ),

    # ── timeout ──────────────────────────────────────────────────
    "TIMEOUT_SHELL": ErrorSpec(
        "TIMEOUT_SHELL",
        "timeout",
        "Shell command timed out",
        "Increase the --timeout flag, or stream long output with the streaming variant (M2)",
    ),
    "TIMEOUT_CONNECT": ErrorSpec(
        "TIMEOUT_CONNECT",
        "timeout",
        "Connection to the Windows host timed out",
        "Check network reachability and firewall rules; try a higher WLB_SSH_TIMEOUT",
    ),

    # ── io ───────────────────────────────────────────────────────
    "FILE_NOT_FOUND": ErrorSpec(
        "FILE_NOT_FOUND",
        "io",
        "Local file not found",
        "Check the path",
    ),
    "REMOTE_PATH_INVALID": ErrorSpec(
        "REMOTE_PATH_INVALID",
        "io",
        "Remote path is not allowed",
        "Use a path inside an allow-listed area; avoid traversal",
    ),
    "WORKSPACE_FULL": ErrorSpec(
        "WORKSPACE_FULL",
        "io",
        "Workspace disk is full",
        "Free space or rotate workspace/ contents",
    ),

    # ── input ────────────────────────────────────────────────────
    "INVALID_HOST": ErrorSpec(
        "INVALID_HOST",
        "input",
        "Configured host string is malformed",
        "Use a hostname or IPv4/IPv6 address; no scheme prefix",
    ),
    "INVALID_TIMEOUT": ErrorSpec(
        "INVALID_TIMEOUT",
        "input",
        "Timeout value out of range",
        "Use an integer between 1 and 3600 seconds",
    ),

    # ── system ───────────────────────────────────────────────────
    "SYSTEM_DEPENDENCY_MISSING": ErrorSpec(
        "SYSTEM_DEPENDENCY_MISSING",
        "system",
        "Missing system dependency",
        "Install the missing tool per error.details",
    ),
}


def lookup(code: str) -> ErrorSpec | None:
    """Return the ErrorSpec for ``code``, or None if it's not registered."""
    return ERROR_CODES.get(code)
