# windows-llm-bridge

> Let an LLM agent drive a Windows host like a function call: run `cmd.exe`
> / PowerShell, push and pull files, invoke vendor tooling that only ships
> as Windows binaries — all returning `{ok, data, error, artifacts}`.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Status: M0 bootstrap](https://img.shields.io/badge/status-M0%20bootstrap-orange.svg)](PLAN.md)

English · [中文](README.md)

---

## What is this

**windows-llm-bridge** (**wlb**) is the sister project of
[`android-llm-bridge`](https://github.com/TbusOS/android-llm-bridge).

alb turns "debugging a real Android device" into a tool set that LLM agents
can call directly. **wlb does the same for "run commands / push files /
drive vendor tools on a Windows host".**

The workflow wlb is built for:

1. Cross-compile firmware on Linux. Output lands on a Samba/SMB share that
   Windows sees.
2. The LLM agent invokes the Windows-side vendor flasher through wlb.
3. The agent reads structured progress + final status, decides whether to
   retry, change parameters, or report the failure to a human.

What used to be a human switching windows is now the agent doing the loop.

---

## Why it exists

Many embedded / driver / firmware projects look like this today:

- **Heavy lifting on Linux**: cross-compile, CI, test farm. LLM agents
  feel right at home here.
- **A handful of critical tools on Windows**: vendor flashers, JTAG GUIs,
  signing utilities, factory-test jigs. **Windows-only binaries.**

The painful part is not the human context switch. The painful part is
**the agent goes blind** once a tool runs on a different machine. wlb
gives the agent eyes and hands on the Windows side.

A side-by-side:

| Aspect              | Raw SSH / RDP                                          | wlb                                                                          |
|---------------------|--------------------------------------------------------|------------------------------------------------------------------------------|
| Output format       | Free-form text                                         | Structured `{ok, data, error, artifacts, timing_ms}`                         |
| Error signal        | Read exit code, guess the rest                         | `error.code` + `error.suggestion`, agent-consumable                          |
| Dangerous actions   | Run at your own risk (`format c:` flies through)       | Default deny-list (`format`, `Format-Volume`, `bcdedit /delete`, `Remove-Item -Recurse -Force C:\`) |
| MCP integration     | Write your own glue                                    | One-line JSON registration with Claude Code / Cursor / Codex                 |
| Tool invocation     | String concatenation                                   | Declarative TOML config: progress regex, success/failure regex (M2)          |
| File transfer       | `scp` or a shared folder                               | SFTP or SMB path translation (M2)                                            |

---

## Current capability matrix

> This repo is currently at **M1**: the SSH primary path is live (asyncssh,
> cmd + powershell both work, PowerShell uses `-EncodedCommand` to avoid
> quoting pitfalls). M2 will add filesync, named-tool runner, and an
> HTTP fallback. See [`PLAN.md`](PLAN.md).

### Transports

| Name   | Path                       | Status   | Purpose                                                |
|--------|----------------------------|----------|--------------------------------------------------------|
| ssh    | `wlb.transport.ssh`        | beta     | Primary: Windows OpenSSH Server, asyncssh, key auth    |
| local  | `wlb.transport.local`      | beta     | Loopback for unit tests                                |
| http   | `wlb.transport.http`       | beta     | Fallback: Windows-side wlb-agent (FastAPI) + httpx client with bearer token (save-to-file) and optional TLS |
| hybrid | `wlb.transport.hybrid`     | planned  | M2 smart router: file → SFTP, cmd → SSH, offline → HTTP |

### Capabilities

| Name       | CLI                       | MCP tool                       | Status   | Notes                                            |
|------------|---------------------------|--------------------------------|----------|--------------------------------------------------|
| status     | `wlb status` / `describe` | `wlb_status` / `wlb_describe`  | beta     | Host info, transport health                       |
| cmd        | `wlb cmd <args>`          | `wlb_cmd`                      | beta     | Run via `cmd.exe /c`                              |
| powershell | `wlb powershell <args>`   | `wlb_powershell`               | beta     | Auto-detect PS 5 vs 7+, structured output         |
| filesync   | `wlb fs push|pull` / `maps` | `wlb_push` / `wlb_pull`      | beta     | SFTP push/pull + SMB path translation + local-copy shortcut (skips SFTP when the mount is reachable) |
| tool       | `wlb tool list / show / run [--stream]` | `wlb_tool_list` / `wlb_tool_show` / `wlb_tool_run` | beta     | User-declared tools in TOML (command_template + progress/success/failure regex + workdir); `--stream` for live line/progress/match events (M3.1); args reject shell metachars; full output captured to workspace/hosts/.../tools/.../<ts>.log |
| web        | `wlb web` / `wlb-api`     | —                              | beta     | Local dashboard (FastAPI + WebSocket) — status / registry / tool runner with live streaming. Localhost-only default; **no auth in M3.3** — front with an authenticated reverse proxy if exposed |
| pty        | (browser) /pty.html       | —                              | beta     | Interactive PTY terminal (xterm.js + WebSocket). ssh = asyncssh PTY channel; local = Unix pty.openpty() or Windows ConPTY via pywinpty (`uv sync --extra windows-local-pty`); http = wlb-agent `WS /v1/pty` (M3.6). Optional asciinema `.cast` recording: `WLB_PTY_RECORD=1` or `[pty] record=true` (M3.7) |

---

## Quickstart

```bash
# 1. Install (user-local, no sudo, no system Python touched)
git clone https://github.com/TbusOS/windows-llm-bridge.git
cd windows-llm-bridge
./scripts/install.sh

# 2. Enable OpenSSH Server on the Windows side
#    (copy scripts/windows-setup/enable-openssh.ps1 over and run as admin)
#    See docs/windows-side-setup.md

# 3. Configure the SSH target (interactive — writes workspace/profiles/default.toml)
uv run wlb setup ssh
#    Multi-host:    uv run wlb setup ssh --profile homelab
#    Scripted/CI:   uv run wlb setup ssh --non-interactive --host ... --user ... --yes

# 4. Self-check
uv run wlb describe
uv run wlb status
uv run wlb setup show           # see merged env > profile > defaults

# 5. Run commands
uv run wlb cmd "ver"
uv run wlb powershell "Get-ComputerInfo | Select-Object OsName, OsVersion"
#    Switch profile: uv run wlb --profile homelab cmd "ver"
```

Register wlb with Claude Code (or Cursor / Codex) as an MCP server:

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

Full walkthrough in [`docs/quickstart.md`](docs/quickstart.md) and
[`docs/mcp-integration.md`](docs/mcp-integration.md).

---

## Project layout

```
windows-llm-bridge/
├── CLAUDE.md                  # AI agent rules (banned words, style, flow)
├── REQUIREMENTS.md            # What we build, for whom, anti-requirements
├── PLAN.md                    # Roadmap (M0/M1/M2/M3) down to file level
├── README.md / README.en.md   # Intro (this file)
├── pyproject.toml             # PEP 621 manifest (hatchling + uv)
├── src/wlb/
│   ├── infra/                 # Result / Errors / Permissions / Registry / Workspace
│   ├── transport/             # base ABC + ssh / local / http / hybrid
│   ├── capabilities/          # cmd / powershell / status / filesync / tool
│   ├── mcp/                   # FastMCP server + per-capability tool registration
│   └── cli/                   # typer entry + 5 subcommands
├── scripts/
│   ├── install.sh / uninstall.sh
│   ├── check_sensitive_words.sh
│   └── windows-setup/enable-openssh.ps1
├── tests/                     # pytest, asyncio_mode=auto
├── docs/                      # architecture / quickstart / setup / mcp
└── workspace/                 # runtime artifacts (gitignored)
```

---

## Design philosophy

- **Structure first.** All capabilities return
  `{ok, data, error, artifacts, timing_ms}`. Errors always carry a `code`
  and a `suggestion`.
- **Deny by default.** Dangerous command patterns (`format`,
  `Format-Volume`, `bcdedit /delete`, `Remove-Item -Recurse -Force C:\`)
  are refused unless explicitly allowed.
- **Zero system footprint.** `install.sh` never calls sudo, never modifies
  the system Python, never writes to `/etc`.
- **MCP is a first-class citizen.** Every capability has both a CLI
  subcommand and an MCP tool. The behavior is identical across both.
- **Brand-neutral.** No vendor names, SoC model numbers, or internal
  hostnames appear in this repo.

---

## Docs

| File                                | Topic                                     |
|-------------------------------------|-------------------------------------------|
| [REQUIREMENTS.md](REQUIREMENTS.md)  | Requirements / anti-requirements / success criteria |
| [PLAN.md](PLAN.md)                  | Milestone breakdown (M0/M1/M2/M3)         |
| [docs/architecture.md](docs/architecture.md) | Layered architecture + Result flow + permissions |
| [docs/quickstart.md](docs/quickstart.md)     | 8-step getting started             |
| [docs/windows-side-setup.md](docs/windows-side-setup.md) | OpenSSH Server on Windows |
| [docs/mcp-integration.md](docs/mcp-integration.md)       | MCP registration for Claude Code / Cursor |
| [CLAUDE.md](CLAUDE.md)              | Rules for AI agents working on this repo  |

---

## Contributing

Read [`CLAUDE.md`](CLAUDE.md) and [`PLAN.md`](PLAN.md) before opening a PR.
Highlights:

- `./scripts/check_sensitive_words.sh` must report 0 hits before any commit.
- A new capability needs all of: capability module + MCP tool + CLI
  subcommand + tests + registry entry + README matrix line.
- We do not accept `Co-Authored-By: Claude ...` style AI co-author lines.

---

## License

MIT — see [`LICENSE`](LICENSE).
