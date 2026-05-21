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
_TOOL_CATEGORY = "tool"

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
    "HTTP_AUTH_FAILED": ErrorSpec(
        "HTTP_AUTH_FAILED",
        "transport",
        "wlb-agent rejected the bearer token (HTTP 401)",
        "Regenerate the token on the Windows side, copy the token file to "
        "the controller (mode 600), and point WLB_HTTP_TOKEN_FILE at it. "
        "See scripts/windows-agent/README.md.",
    ),
    "HTTP_HOST_UNREACHABLE": ErrorSpec(
        "HTTP_HOST_UNREACHABLE",
        "transport",
        "Cannot reach wlb-agent at the configured URL",
        "Confirm the agent is running on the Windows host and that TCP "
        "is open in the firewall.",
    ),
    "HTTP_AGENT_ERROR": ErrorSpec(
        "HTTP_AGENT_ERROR",
        "transport",
        "wlb-agent returned a 5xx error",
        "Check the agent log on the Windows side; this is usually a "
        "server-side bug or a misconfigured agent, not a wlb client issue.",
    ),
    "HTTP_BAD_RESPONSE": ErrorSpec(
        "HTTP_BAD_RESPONSE",
        "transport",
        "wlb-agent returned a response wlb couldn't parse",
        "Likely a version mismatch between the wlb client and the agent. "
        "Update both sides.",
    ),
    "SSH_KEY_NOT_FOUND": ErrorSpec(
        "SSH_KEY_NOT_FOUND",
        "transport",
        "Configured SSH private key file does not exist",
        "Check WLB_SSH_KEY points to a real file; generate one with: "
        "ssh-keygen -t ed25519 -f ~/.ssh/wlb_ed25519",
    ),
    "SSH_CONNECTION_LOST": ErrorSpec(
        "SSH_CONNECTION_LOST",
        "transport",
        "SSH connection dropped mid-operation",
        "Check the Windows host is still reachable; check Get-WinEvent "
        "-LogName 'OpenSSH/Operational' on the Windows side for clues",
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
        "File not found (remote pull source, or generic not-found)",
        "Check the path",
    ),
    "LOCAL_PATH_NOT_FOUND": ErrorSpec(
        "LOCAL_PATH_NOT_FOUND",
        "io",
        "Local path (push source / pull destination parent) does not exist",
        "Check the path; create the parent directory if needed",
    ),
    "REMOTE_PATH_INVALID": ErrorSpec(
        "REMOTE_PATH_INVALID",
        "io",
        "Remote path is malformed or not writable",
        "Inspect error.details.stderr; ensure the parent directory exists "
        "and the SSH user has write permission",
    ),
    "SFTP_ERROR": ErrorSpec(
        "SFTP_ERROR",
        "io",
        "SFTP server returned an error",
        "Inspect error.details.stderr for the remote-side message",
    ),
    "SFTP_NOT_AVAILABLE": ErrorSpec(
        "SFTP_NOT_AVAILABLE",
        "io",
        "SFTP subsystem is not enabled on the remote SSH server",
        "On the Windows side: Get-Service sshd; ensure the sftp subsystem "
        "is enabled in sshd_config (default for Windows OpenSSH)",
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

    # ── tool runner (M2.3) ───────────────────────────────────────
    "TOOL_NOT_FOUND": ErrorSpec(
        "TOOL_NOT_FOUND",
        _TOOL_CATEGORY,
        "Named tool is not declared in wlb-tools.toml",
        "Run `wlb tool list` to see what's defined; check the spelling, "
        "or add a [tool.<name>] section in workspace/wlb-tools.toml",
    ),
    "TOOLS_CONFIG_ERROR": ErrorSpec(
        "TOOLS_CONFIG_ERROR",
        _TOOL_CATEGORY,
        "wlb-tools.toml is missing or malformed",
        "Check the file at error.details.path; copy wlb-tools.example.toml "
        "as a starting point and adjust",
    ),
    "TOOL_ARG_MISSING": ErrorSpec(
        "TOOL_ARG_MISSING",
        _TOOL_CATEGORY,
        "Required tool argument was not provided",
        "Run `wlb tool show <name>` to see required args; pass them as "
        "`--arg key=value` (CLI) or `args` dict (MCP)",
    ),
    "TOOL_ARG_INVALID": ErrorSpec(
        "TOOL_ARG_INVALID",
        _TOOL_CATEGORY,
        "Tool argument contains a forbidden character",
        "Arguments cannot contain newlines, NULs, or shell metacharacters "
        "(`;`, `&`, `|`, `<`, `>`, backtick, `$`). If you need those, "
        "embed them inside the command_template, not in the value.",
    ),
    "TOOL_FAILED": ErrorSpec(
        "TOOL_FAILED",
        _TOOL_CATEGORY,
        "Tool ran to completion but did not succeed",
        "Inspect error.details: exit_code, failure_match, log_path. The "
        "full output is in the saved log under workspace/hosts/.../tools/",
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
