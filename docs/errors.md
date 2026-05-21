# Error catalog

Every error code wlb can emit is registered in
[`src/wlb/infra/errors.py`](../src/wlb/infra/errors.py). This page is a
human-readable summary; the file is authoritative.

The codes below are stable identifiers. Once shipped they will not be
renamed. New codes may be added; old codes may be marked deprecated in
the docstring (but kept for backward compat).

| Code                          | Category   | When                                                       |
|-------------------------------|------------|------------------------------------------------------------|
| `TRANSPORT_NOT_CONFIGURED`    | transport  | No transport host configured (e.g. WLB_SSH_HOST unset).    |
| `TRANSPORT_NOT_SUPPORTED`     | transport  | The active transport doesn't support this op (yet).        |
| `SSH_AUTH_FAILED`             | transport  | SSH key rejected / wrong user / permissions on key file.   |
| `SSH_HOST_UNREACHABLE`        | transport  | TCP connection to the SSH port failed or timed out.        |
| `SSH_HOSTKEY_REJECTED`        | transport  | The Windows host's key did not match `known_hosts`.        |
| `SSH_KEY_NOT_FOUND`           | transport  | Configured SSH private key file does not exist on disk.    |
| `SSH_CONNECTION_LOST`         | transport  | SSH connection dropped mid-operation.                      |
| `SHELL_NONZERO_EXIT`          | transport  | The command ran but exited non-zero.                       |
| `POWERSHELL_NOT_AVAILABLE`    | transport  | Neither `pwsh.exe` nor `powershell.exe` is on PATH.        |
| `HOST_NOT_FOUND`              | host       | Configured host not reachable at all.                      |
| `HOST_REBOOTING`              | host       | Host is mid-reboot; commands not yet runnable.             |
| `PERMISSION_DENIED`           | permission | Command matched the dangerous-pattern deny-list.           |
| `TIMEOUT_SHELL`               | timeout    | Command exceeded the `--timeout` value.                    |
| `TIMEOUT_CONNECT`             | timeout    | SSH / HTTP connect exceeded its connect-timeout.           |
| `FILE_NOT_FOUND`              | io         | File not found — pull source missing, or generic not-found. |
| `LOCAL_PATH_NOT_FOUND`        | io         | Local push source (or pull destination parent) does not exist. |
| `REMOTE_PATH_INVALID`         | io         | Remote path malformed / not writable / parent missing.     |
| `SFTP_ERROR`                  | io         | SFTP server returned an error (generic, see details.stderr). |
| `SFTP_NOT_AVAILABLE`          | io         | SFTP subsystem disabled on the remote sshd.                |
| `TOOL_NOT_FOUND`              | tool       | Named tool isn't declared in `wlb-tools.toml`.             |
| `TOOLS_CONFIG_ERROR`          | tool       | `wlb-tools.toml` is missing or malformed.                  |
| `TOOL_ARG_MISSING`            | tool       | A required tool arg wasn't provided.                       |
| `TOOL_ARG_INVALID`            | tool       | Tool arg contains a forbidden character.                   |
| `TOOL_FAILED`                 | tool       | Tool ran but didn't succeed (failure_re / exit / etc).     |
| `WORKSPACE_FULL`              | io         | wlb's workspace directory is out of space.                 |
| `INVALID_HOST`                | input      | Configured host string is malformed.                       |
| `INVALID_TIMEOUT`             | input      | Timeout value is out of the allowed range.                 |
| `SYSTEM_DEPENDENCY_MISSING`   | system     | Missing system dependency (e.g. ssh binary on controller). |

## How to read errors

Every `ErrorInfo` has the same five fields:

- `code`: pick one from the table above.
- `message`: human-friendly summary.
- `suggestion`: what to try next. **Read this** — it's actionable.
- `category`: one of `transport / host / permission / timeout / io / input / system / capability`.
- `details`: context (matched rule, attempted command, stderr, exit code, ...).

If you're an LLM agent: **branch on `category` first** to decide whether
to retry (`timeout`, transient `transport`), reformulate (`permission`),
or escalate to a human (`system`, persistent `transport`).
