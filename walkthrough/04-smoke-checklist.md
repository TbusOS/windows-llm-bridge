# Manual smoke checklist

The scripted parts of the walkthrough (`02-linux-pair.sh`,
`03-smoke-tests.sh`) cover SSH transport + filesync + tool runner.
Everything below needs human eyes (browser rendering, ConPTY behavior,
recording playback). Tick boxes as you go.

Run these only after `03-smoke-tests.sh` exits 0.

---

## A. Dashboard (M3.3)

```bash
cd <repo>
uv run wlb-api --profile "$WLB_PROFILE"      # listens on 127.0.0.1:8765 by default
```

Open `http://127.0.0.1:8765/` in your browser.

- [ ] **A1**: dashboard loads; header shows version + profile name.
- [ ] **A2**: Status card shows `ok: true`, agent (if started) info.
- [ ] **A3**: Active profile card shows `transport=ssh`, your `WIN_HOST`.
- [ ] **A4**: Registry tables list `ssh` + `local` + `http` transports
        and at least the `status / cmd / powershell / filesync / tool /
        web / pty` capabilities.
- [ ] **A5**: Click `Run` on `walkthrough_echo` (after exporting
        `WLB_TOOLS_FILE=walkthrough/local-smoke-tools.toml` and
        re-starting `wlb-api`). Modal opens; arg input prompts for
        `tag`. Run completes; output streams live; ends with a
        `done` line and exit 0.

---

## B. SSH PTY (M3.4)

In the dashboard header click `open PTY →`.

- [ ] **B1**: interpreter dropdown defaults to `cmd`; Connect button is active.
- [ ] **B2**: pick `cmd`, click Connect — within 2s xterm.js shows the
        Windows command prompt (`C:\Users\<user>>`).
- [ ] **B3**: type `ver` + Enter — Windows version line appears,
        prompt returns.
- [ ] **B4**: resize the browser window — status line updates `cols × rows`.
- [ ] **B5**: pick `powershell` (after Disconnect), Connect again — a
        proper PowerShell prompt (`PS C:\…>`) shows up.
- [ ] **B6**: type `Get-Process pwsh | Select Name,Id` — output renders
        with the PS table layout.
- [ ] **B7**: close the browser tab — `wlb-api` log shows the PTY
        session cleanly closed (no traceback).

---

## C. HTTP transport + HTTP PTY (M2.4 + M3.6)

Start the agent on the Windows host (one-off, foreground):

```powershell
cd C:\ProgramData\wlb-agent
python .\wlb_agent.py --config .\wlb-agent.toml
```

Copy the token from `C:\ProgramData\wlb-agent\token` to the Linux
side at `$WLB_HTTP_TOKEN_FILE` (Samba / scp), `chmod 600`. Then on
Linux:

```bash
# Switch the walkthrough profile to http (or use env override):
WLB_TRANSPORT=http \
WLB_HTTP_URL="http://$WIN_HOST:$WIN_AGENT_PORT" \
WLB_HTTP_TOKEN_FILE="$WLB_HTTP_TOKEN_FILE" \
WLB_HTTP_VERIFY_TLS=0 \
  uv run wlb --profile "$WLB_PROFILE" status
```

- [ ] **C1**: `wlb status` over HTTP reports `ok=true`, agent_version.
- [ ] **C2**: `wlb cmd "echo http-cmd-$RANDOM"` round-trips.
- [ ] **C3**: `wlb fs push` then `wlb fs pull` round-trip a file with
        matching sha256 (same flow as case 4 in `03-smoke-tests.sh`,
        but now over HTTP).
- [ ] **C4**: Restart `wlb-api` with the env above set, open
        `/pty.html`, connect with `cmd` — the PTY opens via WS
        `/v1/pty` against the agent. Same B-section checks apply.
- [ ] **C5**: Agent log shows `WS /v1/pty` connect + clean close on
        browser-tab close.

If pywinpty wasn't installed on the Windows side, C4 will surface a
`PTY_NOT_AVAILABLE` error toast — install pywinpty there, restart the
agent, retry.

---

## D. PTY recording (M3.7)

Turn recording on for a session:

```bash
WLB_PTY_RECORD=1 uv run wlb-api --profile "$WLB_PROFILE"
```

Open `/pty.html`, run a quick session in each transport (SSH then
HTTP), close the tab to flush.

- [ ] **D1**: a fresh `.cast` file appears under
        `workspace/hosts/<WIN_HOST>/pty/<ts>-cmd.cast` (or
        `workspace/hosts/local/pty/...` if you also recorded a local
        session).
- [ ] **D2**: `head -1 workspace/hosts/.../pty/*.cast` is a valid
        asciinema v2 header (`{"version":2,...}`).
- [ ] **D3**: `asciinema play workspace/hosts/.../pty/<ts>-cmd.cast`
        replays the session you just ran (or use
        `agg <cast> /tmp/wlb-replay.gif` if `asciinema` isn't
        installed locally).

---

## E. Replay UI (M3.8)

With `wlb-api` still running, open `http://127.0.0.1:8765/casts.html`.

- [ ] **E1**: sidebar lists every `.cast` file from section D.
- [ ] **E2**: click a row — asciinema-player loads inside the right
        pane, the file plays back at real-time speed.
- [ ] **E3**: scrub the player timeline back and forth — terminal
        repaints correctly (control sequences replay cleanly).
- [ ] **E4**: click another row — previous player disposes; new one
        loads without leaking event listeners (check browser console
        for warnings).
- [ ] **E5**: try `GET /api/casts/<host>/../../etc/passwd` from a
        browser tab — should return 400 or 404, never 200 with file
        contents.

---

## F. Local PTY (LocalTransport, M3.5 — Windows-side run only)

This phase only applies if you also installed wlb ON the Windows
host (rare for end users; primarily for wlb developers). Skip
otherwise.

```powershell
# In a fresh admin PowerShell on the Windows host:
cd C:\path\to\windows-llm-bridge
uv sync --extra windows-local-pty
$env:WLB_TRANSPORT="local"
uv run wlb-api
```

- [ ] **F1**: dashboard `Active profile` shows `transport=local`.
- [ ] **F2**: `/pty.html` connect with `cmd` — ConPTY spawns
        `cmd.exe`; prompt renders.
- [ ] **F3**: `/pty.html` connect with `powershell` — pywinpty picks
        `pwsh.exe` (or falls back to `powershell.exe`).
- [ ] **F4**: ConPTY recording (`WLB_PTY_RECORD=1`) produces a
        `.cast` file under `workspace/hosts/local/pty/`.

---

## Recording the results

After you finish, drop a local note next to the scripts:

```bash
$EDITOR walkthrough/local-results-$(date -u +%Y-%m-%d).md
```

Suggested template:

```md
# wlb walkthrough — 2026-MM-DD

Tester: <name>
Windows version: <Win10 22H2 / Win11 24H2 / ...>

| Phase | Pass | Notes                          |
|-------|------|--------------------------------|
| A 1-5 | x/5  | …                              |
| B 1-7 | x/7  | …                              |
| C 1-5 | x/5  | …                              |
| D 1-3 | x/3  | …                              |
| E 1-5 | x/5  | …                              |
| F 1-4 | x/4  | (skipped — not running on Win) |
```

`walkthrough/local-*` is gitignored — keep your real findings
private. Open a GitHub issue (sanitized) if any phase fails on a
clean install.
