# Architecture

> Read [`REQUIREMENTS.md`](../REQUIREMENTS.md) and [`PLAN.md`](../PLAN.md) first.
> This document explains *how* the code is organized; those documents
> explain *what* it does and *when*.

---

## Layer diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                          LLM Agent                              │
│  (Claude Code, Cursor, Codex, custom — uses MCP / HTTP / CLI)   │
└──────────────┬───────────────┬───────────────┬──────────────────┘
               │ stdio MCP     │ HTTP          │ argv
               ▼               ▼               ▼
┌──────────────────┐ ┌─────────────────┐ ┌──────────────────┐
│ wlb.mcp.server   │ │ wlb.api.server  │ │ wlb.cli.main     │
│  FastMCP @tool   │ │ FastAPI routes  │ │ typer commands   │
│  (M1)            │ │ (M2)            │ │ (M1)             │
└────────┬─────────┘ └────────┬────────┘ └────────┬─────────┘
         │                    │                   │
         └────────────┬───────┴───────────────────┘
                      ▼
        ┌──────────────────────────────┐
        │     wlb.capabilities.*       │   ← domain logic
        │  cmd / powershell / status   │      transport-agnostic
        │  filesync / tool   (M2)      │      return Result[T]
        └─────────────┬────────────────┘
                      ▼
        ┌──────────────────────────────┐
        │      wlb.transport.*         │   ← Transport ABC
        │  ssh / local / http / hybrid │      async, structured returns,
        │                              │      no exceptions to caller
        └─────────────┬────────────────┘
                      ▼
        ┌──────────────────────────────┐
        │      Windows host            │   ← OpenSSH Server (M1) or
        │                              │      wlb-agent over HTTP (M2)
        └──────────────────────────────┘
```

Three interface layers feed one capability layer feed one transport layer.
The Windows host is whatever the configured transport reaches.

---

## Layer rules

### Transport layer (`wlb.transport`)

- One class per file, implementing `Transport` (ABC in `base.py`).
- Async-only. No public method raises — every error path returns a
  structured `ShellResult` with `ok=False` and an `error_code`.
- Holds connection state (SSH session, HTTP client, subprocess) but no
  business logic.
- Implements `check_permissions()` (default delegates to
  `wlb.infra.permissions.default_check`; transports can layer additional
  rules on top).

The SSH transport delegates connection lifetime to
[`wlb.transport.ssh_pool`](../src/wlb/transport/ssh_pool.py): connections
are keyed by `(host, port, user, key, known_hosts, timeout)`, dial once,
and stay alive for the lifetime of the process (MCP server) or single
invocation (CLI). `ConnectionLost` during `run()` marks the entry dead so
the next `shell()` redials.

### Capability layer (`wlb.capabilities`)

- One file per capability area. Each exposes one or two async functions.
- Capability functions are transport-agnostic — they accept a `Transport`
  and never import a specific transport implementation.
- Always wrap calls in `wlb.infra.result.Result`. Errors translated from
  `ShellResult.error_code` into structured `Result.error`.
- Enforce permissions **before** the transport call. The transport's
  default permission hook is a backstop, not the only line of defense.

### Interface layers

- **MCP** (`wlb.mcp`): each tool is a thin wrapper that builds a transport
  via `wlb.mcp.transport_factory.build_transport()`, calls a capability,
  returns `result.to_dict()`. No business logic.
- **CLI** (`wlb.cli`): typer subcommand calls a capability, prints via
  `print_result()` which honors `--json`. No business logic.
- **Web API** (`wlb.api`, M2): FastAPI route maps 1:1 to a capability.

### Infrastructure (`wlb.infra`)

Pure utilities:

| File             | What it provides                                              |
|------------------|---------------------------------------------------------------|
| `result.py`      | `Result[T]`, `ok()`, `fail()`, `ErrorInfo`                    |
| `errors.py`      | `ERROR_CODES` catalog with default messages / suggestions     |
| `permissions.py` | `DANGEROUS_PATTERNS`, `default_check()`                       |
| `registry.py`    | `TRANSPORTS` / `CAPABILITIES` lists for `wlb describe`        |
| `workspace.py`   | Canonical paths under `workspace/hosts/<host>/<category>/`    |
| `config.py`      | `load_active()` — pulls env (M0) and TOML profile (M1)        |
| `env_loader.py`  | `.env` / `.env.local` loader (no python-dotenv dependency)    |
| `safe_path.py`   | Windows path validation helpers                               |

---

## The Result type

Every public function returns a `Result[T]`:

```python
{
    "ok": bool,
    "data": T | None,            # populated on success
    "error": ErrorInfo | None,   # populated on failure
    "artifacts": list[str],      # files written under workspace/
    "timing_ms": int,
}
```

`ErrorInfo` always has:

```python
{
    "code": str,           # e.g. "PERMISSION_DENIED" — stable identifier
    "message": str,        # human-readable
    "suggestion": str,     # actionable next step for the caller
    "category": str,       # transport / host / permission / timeout / io / input / system / capability
    "details": dict,       # context-specific
}
```

Why this shape:

- LLMs consume structured JSON better than free-form text.
- `code` is stable and enumerable (see `wlb.infra.errors.ERROR_CODES`).
- `suggestion` short-circuits "the agent doesn't know what to do next."
- `artifacts` tells the agent where the on-disk evidence is, so it can
  request a follow-up read.

---

## Permission flow

```
capability.execute(transport, cmd, ...)
       │
       ▼
transport.check_permissions("cmd.execute", {"cmd": cmd, ...})
       │
       ▼
    default_check
       │  matches each pattern in DANGEROUS_PATTERNS
       │  case-insensitive
       ▼
PermissionResult(behavior=allow | ask | deny, matched_rule, suggestion)
       │
       ├── deny  → capability returns fail(code=PERMISSION_DENIED, ...)
       │           Cannot be bypassed.
       │
       ├── ask   → if allow_dangerous=True, proceed.
       │           Else capability returns fail(code=PERMISSION_DENIED, behavior=ask).
       │
       └── allow → transport.shell(cmd) ...
```

The default deny-list lives in `wlb.infra.permissions.DANGEROUS_PATTERNS`.
Add Windows-specific entries there. Transports that face the public
network (e.g. HTTP transport, M2) can override `check_permissions()` to
layer additional rules on top.

---

## Workspace layout

All runtime artifacts land under `workspace/`:

```
workspace/
├── hosts/
│   └── <host>/
│       ├── logs/          # captured stdout/stderr of arbitrary commands
│       ├── tools/         # per-named-tool run logs (M2)
│       ├── pulls/         # files pulled from the Windows side (M2)
│       └── screenshots/   # M3
└── profiles/              # M1 — per-target TOML profiles
```

`<host>` is the resolved SSH target name, validated via
`wlb.infra.workspace.is_safe_host()` to prevent traversal. Multiple
controller hosts can share the same Windows target; they'll all write
into the same `workspace/hosts/<host>/` subtree.

`workspace/` is gitignored (see `.gitignore`) — only the empty `.gitkeep`
is tracked.

---

## Async model

- All transport methods and capability functions are `async def`.
- The CLI bridges async ↔ sync via `wlb.cli.common.run_async()` which
  calls `asyncio.run()`.
- The MCP server (FastMCP) is async-native — tools are registered as
  `async def` and FastMCP awaits them.
- Long-running operations (M2 file transfer, tool streaming) yield events
  through an `AsyncIterator[TransferEvent]` so callers can react to
  progress without buffering the full output.

---

## Things this architecture deliberately doesn't do

- **No middleware stack.** Capabilities call transports directly. No
  decorator chain, no DI container.
- **No singletons.** A new `Transport` is built per call (the factory
  caches settings; the transport itself is lightweight).
- **No global state in business logic.** State lives in:
  - the transport instance (connection pool),
  - environment variables / TOML profile (read once at startup),
  - the workspace on disk.
- **No "framework".** It's just async functions returning `Result[T]`.
