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
| `SHELL_NONZERO_EXIT`          | transport  | The command ran but exited non-zero.                       |
| `POWERSHELL_NOT_AVAILABLE`    | transport  | Neither `pwsh.exe` nor `powershell.exe` is on PATH.        |
| `HOST_NOT_FOUND`              | host       | Configured host not reachable at all.                      |
| `HOST_REBOOTING`              | host       | Host is mid-reboot; commands not yet runnable.             |
| `PERMISSION_DENIED`           | permission | Command matched the dangerous-pattern deny-list.           |
| `TIMEOUT_SHELL`               | timeout    | Command exceeded the `--timeout` value.                    |
| `TIMEOUT_CONNECT`             | timeout    | SSH / HTTP connect exceeded its connect-timeout.           |
| `FILE_NOT_FOUND`              | io         | Local file not found (push side).                          |
| `REMOTE_PATH_INVALID`         | io         | Remote path failed validation (UNC / traversal / outside allow-list). |
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
