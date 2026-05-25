# MCP integration

Hook wlb into an LLM client (Claude Code, Cursor, Codex, or any MCP host).

---

## What is MCP

[Model Context Protocol](https://modelcontextprotocol.io) is a standard
that lets an LLM client (the host process running the model) call tools
exposed by independent server processes. wlb ships a FastMCP server
(`wlb-mcp`) that runs over stdio and exposes every capability as a tool.

Every tool returns the same shape:

```json
{
  "ok": true,
  "data": { ... },
  "error": null,
  "artifacts": [],
  "timing_ms": 142
}
```

On failure:

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "PERMISSION_DENIED",
    "message": "Matches dangerous pattern: format a drive",
    "suggestion": "Scope the command to a specific path, or run it manually after confirming the intent.",
    "category": "permission",
    "details": { "matched_rule": "...", "attempted_command": "format c:" }
  },
  "artifacts": [],
  "timing_ms": 0
}
```

This is what your LLM client sees verbatim. Train the agent to read
`error.suggestion` — it's there for it.

---

## Tools registered (M0 bootstrap)

| Tool             | Purpose                                                          |
|------------------|------------------------------------------------------------------|
| `wlb_status`     | Active transport health snapshot.                                |
| `wlb_describe`   | Full transport + capability matrix (metadata, no transport call).|
| `wlb_cmd`        | Execute a command via cmd.exe /c.                                |
| `wlb_powershell` | Execute a PowerShell script (pwsh.exe preferred).                |

More land in M1 / M2 (see [`PLAN.md`](../PLAN.md)).

---

## Claude Code

Edit `~/.claude/mcp-settings.json` (create it if missing):

```json
{
  "mcpServers": {
    "wlb": {
      "command": "uv",
      "args": ["run", "--project", "/abs/path/to/windows-llm-bridge", "wlb-mcp"]
    }
  }
}
```

Replace `/abs/path/to/windows-llm-bridge` with the actual repo path
(e.g. `~/windows-llm-bridge` resolved to its absolute form).

Restart Claude Code. Verify with `/mcp` — you should see `wlb` listed
with the 4 tools.

If `uv` is not on Claude Code's PATH, give it the absolute path:

```json
{
  "mcpServers": {
    "wlb": {
      "command": "/home/<you>/.local/bin/uv",
      "args": ["run", "--project", "/abs/path/to/windows-llm-bridge", "wlb-mcp"]
    }
  }
}
```

---

## Cursor

Cursor reads `~/.cursor/mcp.json` (and `.cursor/mcp.json` per-project).
Use the same shape:

```json
{
  "mcpServers": {
    "wlb": {
      "command": "uv",
      "args": ["run", "--project", "/abs/path/to/windows-llm-bridge", "wlb-mcp"]
    }
  }
}
```

Restart Cursor (or hit "Refresh" in the MCP settings pane).

---

## Codex / other MCP hosts

Any host that follows the MCP spec for stdio servers works. The command
line that starts the server is just:

```bash
uv run --project /abs/path/to/windows-llm-bridge wlb-mcp
```

You can confirm it's a valid MCP server by running it interactively —
it'll wait for JSON-RPC on stdin. Use the inspector tools from your host
of choice for testing.

---

## Calling wlb tools from an agent

A typical interaction:

```
User: "Compile main.c with the toolchain on the Windows box,
       then read back the warnings."

Agent: I'll first check what's reachable.
       → wlb_status
       ← { ok: true, data: { transport: "ssh", health: { ... } } }

Agent: I'll compile.
       → wlb_cmd("cl.exe /W4 C:\\src\\main.c /Fo C:\\build\\main.obj")
       ← { ok: false, error: { code: "SHELL_NONZERO_EXIT",
                              details: { stdout: "warning C4xxx: ..." } } }

Agent: I see two warnings. Want me to address them?
```

The agent reads `error.code` (`SHELL_NONZERO_EXIT`), reads
`error.details.stdout` for the actual compiler output, and decides how
to proceed. wlb gives the agent **all** the data it needs in one
structured return — no follow-up `read this file please` round trips.

---

## Permissions while using MCP

The deny-list applies just as much to MCP calls as to CLI calls. An LLM
agent that tries to be "helpful" by deleting a build directory with
`Remove-Item -Recurse -Force C:\` gets:

```json
{
  "ok": false,
  "error": {
    "code": "PERMISSION_DENIED",
    "message": "Matches dangerous pattern: Remove-Item -Recurse -Force on a drive root",
    "suggestion": "Scope the command to a specific path, or run it manually after confirming the intent."
  }
}
```

You can pass `allow_dangerous=True` from the agent side to bypass
*ASK*-level patterns, but *DENY*-level patterns (the ones that match the
real drive-wipe / shutdown / bcdedit forms) cannot be bypassed by an
argument. This is a deliberate design choice — the LLM should never be
able to argue itself into a `format c:`.

If you need a wider allow surface for a specific workflow, add it to
your local `wlb.infra.permissions` fork and rebuild. We accept PRs that
*narrow* the deny-list (false positives), but not ones that widen it
without a strong case.

---

## Progress notifications (M3.10)

Long-running tools surface mid-flight progress via the standard MCP
[`notifications/progress`](https://modelcontextprotocol.io/specification/server/utilities/progress)
channel. Wire it up once and any compliant client picks it up.

### How a client opts in

Clients that want progress send a request `_meta.progressToken` (the
MCP SDK does this transparently when you supply one). FastMCP exposes
the token through a `Context` parameter; wlb uses it to call
`ctx.report_progress(progress, total, message)`.

If a client doesn't send a `progressToken`, `wlb_tool_run` falls back
to the original one-shot path — no progress noise, same final Result.
This is fully backwards compatible.

### What wlb_tool_run emits

For each invocation, the wrapper translates `ToolStreamEvent`s coming
out of `run_tool_stream` into MCP notifications:

| Stream event                                  | MCP notification                                  |
|-----------------------------------------------|---------------------------------------------------|
| `kind=progress, percent=N`                    | `notifications/progress` `progress=N total=100`   |
| `kind=match, pattern_label=success`           | `notifications/message` level `info`              |
| `kind=match, pattern_label=failure`           | `notifications/message` level `warning`           |
| `kind=line` (every 50th)                      | `notifications/message` level `info`              |
| `kind=done`                                   | `notifications/progress` `progress=100 total=100` (caps the bar even when no `progress_re` ever hit) |

The final `tools/call` response is the same structured Result as before
(`{ok, data, error, artifacts, timing_ms}`). Progress notifications are
strictly additive — clients that ignore them still get the right answer
at the end.

### Example: declaring a tool with a progress regex

```toml
# wlb-tools.toml
[tools.vendor_flash]
interpreter = "cmd"
description = "Flash firmware via vendor tool"
command_template = '"C:\Tools\vendor_flash.exe" --image "{image}" --port {port}'
args         = ["image", "port"]
timeout      = 600

[tools.vendor_flash.regex]
# Vendor tool prints "Progress: 42%" mid-flight; capture group 1 is the %.
progress = '^Progress:\s+(\d{1,3})%'
success  = '^Flash complete'
failure  = '^(ERROR|Failed):'
```

When an LLM agent calls `wlb_tool_run("vendor_flash", {"image": ..., "port": ...})`
with a `progressToken` in the request, the client sees a live progress
indicator climb 0→100% as the flasher emits its "Progress: N%" lines,
plus an info ping every 50 stdout lines and a warning if the
`failure` regex hits.

### Other tools

- `wlb_status`, `wlb_describe`, `wlb_cmd`, `wlb_powershell`,
  `wlb_push`, `wlb_pull`, `wlb_tool_list`, `wlb_tool_show` — fast or
  fixed-duration; no progress notifications emitted.
- Only `wlb_tool_run` benefits from the streaming + regex parsing
  needed to drive a progress bar honestly.

---

## Verifying without an LLM client

```bash
# Start the server in the foreground; ^C to quit.
uv run wlb-mcp
```

The server waits for JSON-RPC on stdin. Use the MCP inspector
(`npx @modelcontextprotocol/inspector uv run --project ... wlb-mcp`) for
ad-hoc tool calls without an LLM client.
