# windows-llm-bridge — Requirements

> Sister project of [`android-llm-bridge`](https://github.com/TbusOS/android-llm-bridge).
> Same philosophy, different OS surface.

This document defines **what** wlb must do, **for whom**, and **what it
deliberately does not do**. Implementation choices are in
[`PLAN.md`](PLAN.md); architecture is in [`docs/architecture.md`](docs/architecture.md).

---

## 1. The problem

A growing share of embedded / firmware / device-driver work happens with
this split:

- Heavy lifting (cross-compile, codegen, test farm, CI) lives on **Linux**
  build hosts where an LLM coding agent is convenient.
- A handful of critical tools — vendor flashers, JTAG GUIs, factory-test
  jigs, signed-image packagers, USB device manipulators — **only ship as
  Windows binaries**, and the engineer has to switch desks (or windows)
  to run them.

Three pain points come from this split:

1. **Context switching cost.** Every flash cycle pulls the engineer out
   of the Linux shell, into a Windows GUI, then back.
2. **The LLM agent loses the loop.** Once the flasher lives on a different
   machine, the agent can't read its output, can't react to failure modes,
   can't drive iteration.
3. **Repeatable workflows turn into tribal knowledge.** "Click here, wait
   for the LED, then read the COM port..." lives in a wiki nobody updates.

wlb fixes the second point — exposes the Windows machine as a structured
tool surface the LLM agent can call directly — which incidentally fixes
the first and third.

---

## 2. Who is this for

| Audience              | Primary use case                                                     |
|-----------------------|----------------------------------------------------------------------|
| Embedded firmware dev | "Compile on Linux, flash on Windows" loop, driven by the agent.      |
| Driver / kernel dev   | Boot Windows test VM, run a driver test binary, collect dmp.         |
| Build / release eng   | Sign / package artifacts with a Windows-only signing tool.           |
| QA automation         | Drive a Windows test harness with structured input/output.           |
| Hobbyist / homelab    | Single-shot Windows commands from a Linux session without RDP.       |

Anti-audience: people who need a full Windows IT-admin framework
(Sysinternals / Group Policy). wlb is not that and won't grow into that.

---

## 3. Functional requirements

### 3.1 Must (M1 — first usable release)

| Req     | Capability                                                            |
|---------|-----------------------------------------------------------------------|
| F-M1-01 | Execute a `cmd.exe` command line on the Windows host. Return stdout / stderr / exit code, structured. |
| F-M1-02 | Execute a PowerShell command (Windows PowerShell 5 or PowerShell 7+). |
| F-M1-03 | Report environment: OS version, PowerShell version, transport status, configured paths. |
| F-M1-04 | Self-describe: list every transport, capability, and MCP tool the build supports (`wlb describe`). |
| F-M1-05 | Health check (`wlb status`): can we reach the Windows host? Is the SSH key working? |
| F-M1-06 | Default permission blocklist: refuse destructive commands (`format`, `del /q /s C:\`, `Format-Volume`, `bcdedit /delete`, etc.) unless explicitly allowed. |
| F-M1-07 | Run as an MCP server (`wlb-mcp` over stdio) and surface every CLI capability as an MCP tool. |
| F-M1-08 | Run as a CLI (`wlb <subcommand>`) with `--json` for structured output. |
| F-M1-09 | Transport: SSH to Windows OpenSSH Server, key-auth. Configurable host / port / user / key path. |
| F-M1-10 | All artifacts (captured stdout, file pulls, tool logs) land under `workspace/hosts/<host>/<category>/` so the agent can reason about them. |

### 3.2 Should (M2 — first complete release)

| Req     | Capability                                                            |
|---------|-----------------------------------------------------------------------|
| F-M2-01 | File push / pull via SFTP (over the same SSH transport).              |
| F-M2-02 | Samba / SMB-aware path translation: if Linux sees `/mnt/win-share/build.bin` and Windows sees `C:\share\build.bin`, accept either form. |
| F-M2-03 | Run a configured named tool with structured arguments (`wlb tool run <name> --arg key=value`). Tools are declared in a TOML config — no hard-coded vendor names. |
| F-M2-04 | Stream long-running output (line-by-line) so the agent can react to mid-flash messages. |
| F-M2-05 | Per-tool timeout, progress regex, success/failure regex (declarative). |
| F-M2-06 | A second transport: HTTP agent that runs on Windows, for environments where SSH is blocked. |
| F-M2-07 | A simple Web API (FastAPI) mirroring the CLI for non-MCP clients.     |

### 3.3 Could (M3 — nice-to-have)

| Req     | Capability                                                            |
|---------|-----------------------------------------------------------------------|
| F-M3-01 | A small Web UI for ad-hoc command execution + tool runs.              |
| F-M3-02 | Interactive PTY shell (xterm.js front-end → ConPTY back-end via SSH). |
| F-M3-03 | Recording / replay of a session (audit trail).                        |
| F-M3-04 | Pluggable backends: WinRM, Windows Admin Center HTTP, custom.         |
| F-M3-05 | Skill packs: per-tool SKILL.md files an LLM client can preload.       |

---

## 4. Non-functional requirements

| NF      | Requirement                                                            |
|---------|------------------------------------------------------------------------|
| NF-01   | **Zero-root install** on the Linux/macOS host. `install.sh` never invokes `sudo`. |
| NF-02   | **Zero-system-Python** install. uv-managed Python 3.11+ in `.venv/`.  |
| NF-03   | **One-line MCP register**: a single JSON snippet drops wlb into Claude Code / Cursor / Codex. |
| NF-04   | **Structured output everywhere.** No tool returns unstructured prose. |
| NF-05   | **Actionable errors.** Every failure includes a `code`, a `message`, and a `suggestion`. |
| NF-06   | **Async-first.** All transports and capabilities are asyncio coroutines. |
| NF-07   | **No state between calls.** Each MCP / CLI call is self-contained; SSH connection pooling is internal and invisible. |
| NF-08   | **Permission blocklist denies by default.** `format c:`, `Format-Volume`, `bcdedit /delete`, `Remove-Item -Recurse -Force C:\`, `shutdown /s /t 0`, etc. are refused without explicit `--allow-dangerous`. |
| NF-09   | **Open-source, brand-neutral.** No mention of any specific vendor's tool. Real-world examples in docs use the generic "your vendor's Windows flashing tool" framing. |
| NF-10   | **Reproducible installs.** `uv.lock` is committed. Smoke tests must pass on a clean clone. |
| NF-11   | **Cross-host portability.** Same Linux binary works against any Windows host that has OpenSSH Server enabled. |

---

## 5. Out of scope (anti-requirements)

These are intentionally not wlb's job. PRs that add these will be closed
with a pointer to a better-suited project.

- **Android debugging.** Use [alb](https://github.com/TbusOS/android-llm-bridge).
- **General Windows administration** (AD, GPO, registry editing as a
  first-class capability). wlb may expose `reg query` through `wlb cmd`,
  but a dedicated `wlb reg` subcommand is out of scope.
- **Remote desktop / GUI scraping.** No screenshot capture, no UI
  automation, no WinAppDriver. wlb is a CLI bridge.
- **Anti-virus evasion / stealth.** wlb runs over standard OpenSSH with
  key auth; if your AV blocks it, fix the AV exclusion, don't ask wlb
  to hide.
- **A general "run anything anywhere" platform.** wlb is specifically
  Linux/macOS → Windows. The reverse (Windows → Linux) is what plain SSH
  already does well.

---

## 6. Constraints

- **License:** MIT.
- **Python:** 3.11+ on the controller side. Windows side just needs
  OpenSSH Server (Windows 10 1809+ / Windows 11 / Windows Server 2019+,
  built-in optional feature).
- **No vendor lock-in.** Tools are declared in user config; wlb ships
  with no vendor tool names hard-coded.
- **Pre-commit hook enforces banned-words list** — see `CLAUDE.md`.

---

## 7. Success criteria

We will consider M1 a success when a fresh contributor can:

1. `git clone https://github.com/TbusOS/windows-llm-bridge && cd windows-llm-bridge`
2. `./scripts/install.sh`
3. Enable OpenSSH Server on a Windows machine
   (`./scripts/windows-setup/enable-openssh.ps1`, run on Windows).
4. `cp .env.example .env` and fill in `WLB_SSH_HOST`, `WLB_SSH_USER`,
   `WLB_SSH_KEY`.
5. `uv run wlb status` returns OK.
6. `uv run wlb cmd "ver"` returns the Windows version string.
7. `uv run wlb powershell "Get-ComputerInfo | Select-Object OsName, OsVersion"`
   returns structured output.
8. Add wlb as an MCP server to Claude Code, ask the agent
   "what version of Windows is the bridge connected to?", and get a
   correct answer — without the agent ever leaving the chat.

We will consider M2 a success when the same contributor can:

1. Drop a vendor's Windows flashing CLI on the Windows machine.
2. Declare it in `wlb-tools.toml`.
3. Push a firmware binary from Linux: `uv run wlb fs push build/fw.bin C:\stage\fw.bin`.
4. Run the tool: `uv run wlb tool run flasher --image C:\stage\fw.bin`.
5. Get structured progress + final status in the agent's chat — including
   an `artifacts` list with the captured log.
