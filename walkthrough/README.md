# Real-Windows walkthrough

End-to-end smoke test of every wlb shipping feature against a real
Windows machine. Validates the SSH transport, the HTTP transport,
all three PTY backends, the recording layer, and the replay UI in
a single flow.

This directory is the substrate. The scripts are designed to be
copy-pasted onto fresh hosts — they hold no internal IPs, no
hostnames, no credentials.

---

## What's in here

| File                                  | Side    | What it does                                            |
|---------------------------------------|---------|---------------------------------------------------------|
| `local-notes.env.example`             | both    | Template for your local target details (IP, user, paths).|
| `01-windows-bootstrap.ps1`            | Windows | Install OpenSSH Server + Python + wlb-agent deps + ACLs. |
| `02-linux-pair.sh`                    | Linux   | Generate SSH keypair, write wlb profile, smoke `wlb status`. |
| `03-smoke-tests.sh`                   | Linux   | Scripted SSH smoke: status / cmd / powershell / fs / tool. |
| `04-smoke-checklist.md`               | manual  | Browser PTY + HTTP transport + recording + replay UI.    |

`local-notes.env` (no `.example`) is **gitignored** — fill it in once,
keep it on your local disk, never commit.

---

## Why this exists

Every M3.x feature has unit + contract tests (315+ tests as of M3.7).
But:

- The agent's Windows ConPTY backend (`pywinpty`) only runs on Win32.
- The SSH PTY path needs a real Windows OpenSSH Server.
- The replay UI is a browser thing — automated tests cover the
  endpoints, not the rendered player.
- A real flashing-tool workflow can only be eyeballed end-to-end.

This walkthrough closes those gaps. Pass it once per Windows version
you support (Win 10 / Win 11 / Win Server 2019+).

---

## Workflow (first run)

```bash
# 1. Prep local config (Linux side)
cp walkthrough/local-notes.env.example walkthrough/local-notes.env
chmod 600 walkthrough/local-notes.env
$EDITOR walkthrough/local-notes.env       # fill in WIN_HOST, WIN_USER, etc.

# 2. Bootstrap the Windows host (Windows admin PowerShell)
#    Copy walkthrough/01-windows-bootstrap.ps1 to the Windows side
#    (via the Samba share is fine), then in an admin PowerShell:
#       cd C:\path\to\01-windows-bootstrap.ps1's_dir
#       Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#       .\01-windows-bootstrap.ps1
#    Re-run is safe (idempotent).

# 3. Pair the Linux controller (Linux side)
./walkthrough/02-linux-pair.sh
#    Prints a pubkey for you to install on the Windows side, then
#    waits for you to confirm before running `wlb status`.

# 4. Scripted SSH smoke (Linux side)
./walkthrough/03-smoke-tests.sh
#    Pass/fail count at the end. Output also saved to
#    walkthrough/local-smoke-<timestamp>.log (gitignored).

# 5. Manual browser smoke
#    Follow walkthrough/04-smoke-checklist.md.
```

Subsequent runs only need step 4 + step 5 if the Windows side hasn't
changed.

---

## Security

- **Never commit `local-notes.env`** or any `local-*` file. The
  `.gitignore` rule `walkthrough/local-*` covers it; verify with
  `git status` before any commit.
- **Token transfer**: the agent writes its token to a mode-locked file
  on Windows. Move that file to the Linux side via the Samba share or
  scp — do NOT paste the token contents into chat, shell history, or
  CLI arguments. See `scripts/windows-agent/README.md` "Step 3".
- **TLS**: lab runs over plain HTTP are OK. For anything beyond a
  closed network, generate a self-signed cert on the Windows side and
  pin it via `WLB_HTTP_CA_BUNDLE`.

---

## Pass criteria

The walkthrough passes when every box in `04-smoke-checklist.md` is
checked AND `03-smoke-tests.sh` exits 0. Record the resulting summary
in a local note (e.g., `walkthrough/local-results-2026-MM-DD.md`) for
your own audit trail; do not commit it.
