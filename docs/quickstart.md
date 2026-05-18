# Quickstart

Get wlb talking to a Windows host in under 10 minutes.

> **Prerequisites**
> - Linux or macOS controller host with `bash`, `curl` (or `wget`), and `git`.
> - A Windows 10 1809+ / Windows 11 / Windows Server 2019+ target host
>   you can reach over TCP 22.
> - Administrator access on the Windows host (one-time, to enable OpenSSH).

---

## 1. Install on the controller

```bash
git clone https://github.com/TbusOS/windows-llm-bridge.git
cd windows-llm-bridge
./scripts/install.sh
```

What the installer does (and what it doesn't):

- ✅ Installs `uv` into `~/.local/bin` (or reuses an existing one).
- ✅ Asks uv to fetch Python 3.11 into `~/.local/share/uv/python/`.
- ✅ Creates `.venv/` in the repo, syncs deps, runs smoke tests.
- ❌ Never invokes `sudo`.
- ❌ Never modifies `/usr/bin/python3` or `/etc/`.
- ❌ Never edits other users' files.

---

## 2. Enable OpenSSH Server on the Windows host

Copy `scripts/windows-setup/enable-openssh.ps1` to the Windows machine
(any way — Samba share, USB stick, paste it). On the Windows side, in an
elevated PowerShell:

```powershell
.\enable-openssh.ps1
```

The script:

- Installs the OpenSSH Server optional feature.
- Starts the `sshd` service and sets auto-start.
- Opens TCP 22 in the firewall.
- Disables password auth (key-only).
- Prints where to put your public key.

Detailed walkthrough in [`windows-side-setup.md`](windows-side-setup.md).

---

## 3. Generate a key and install it

On the controller host:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/wlb_ed25519 -C 'wlb'
```

Copy `~/.ssh/wlb_ed25519.pub` to **one** of these on the Windows side:

- `C:\Users\<your-user>\.ssh\authorized_keys` — recommended, scoped to one user.
- `C:\ProgramData\ssh\administrators_authorized_keys` — needed if the SSH
  user is in the local Administrators group AND you want elevated SSH
  sessions.

Verify from the controller:

```bash
ssh -i ~/.ssh/wlb_ed25519 <your-user>@<win-host> 'ver'
```

You should see something like `Microsoft Windows [Version 10.0.19045.xxxx]`.

---

## 4. Configure wlb

```bash
cp .env.example .env
$EDITOR .env
```

Fill in:

```bash
WLB_SSH_HOST=<win-host>          # hostname or IP
WLB_SSH_PORT=22
WLB_SSH_USER=<your-windows-user>
WLB_SSH_KEY=~/.ssh/wlb_ed25519
```

---

## 5. Self-check

```bash
uv run wlb describe   # list all transports + capabilities
uv run wlb status     # transport health
uv run wlb doctor     # everything-at-once probe
```

`status` should report `ok: true` once the M1 SSH transport is implemented.
On the M0 bootstrap commit, status returns the placeholder shape so you
can confirm the rest of the wiring is correct.

---

## 6. Run a command

```bash
uv run wlb cmd "ver"
uv run wlb cmd "ipconfig /all"
uv run wlb powershell "Get-ComputerInfo | Select-Object OsName, OsVersion | ConvertTo-Json"
```

`--json` returns the full Result envelope so you can pipe to `jq`:

```bash
uv run wlb --json cmd "ver" | jq '.data.stdout'
```

---

## 7. Hook into Claude Code (or Cursor / Codex)

Add wlb as an MCP server. For Claude Code, edit `~/.claude/mcp-settings.json`:

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

Restart Claude Code. Ask the agent something like:

> What version of Windows is the bridge connected to? Use `wlb_status`
> first and then `wlb_cmd "ver"`.

Full MCP integration details (Cursor / Codex / custom clients) are in
[`mcp-integration.md`](mcp-integration.md).

---

## 8. Try something that should be denied

```bash
uv run wlb cmd "format c:"
```

You'll get:

```
✗ PERMISSION_DENIED — Matches dangerous pattern: format a drive
suggestion: Scope the command to a specific path, or run it manually after confirming the intent.
```

The permission engine is between you and a really bad day. Add patterns
in `src/wlb/infra/permissions.py` if you find a category that should be
denied by default.

---

## Where to go next

- [`architecture.md`](architecture.md) — how the code is organized.
- [`mcp-integration.md`](mcp-integration.md) — wire wlb to your LLM client.
- [`windows-side-setup.md`](windows-side-setup.md) — hardening tips.
- [`../PLAN.md`](../PLAN.md) — what's coming in M1 / M2 / M3.
- [`../CLAUDE.md`](../CLAUDE.md) — rules if you're contributing.
