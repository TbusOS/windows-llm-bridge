# Real-Windows walkthrough

The unit + contract test suite covers every shipping feature
algorithmically (315+ tests as of M3.7). What it doesn't cover:

- Windows ConPTY behavior under pywinpty (only runs on Win32).
- Whether the Windows OpenSSH Server is actually reachable from a
  Linux controller in your environment.
- The browser-rendered asciinema-player against a real `.cast` file.
- Long-tail behavior of a real flashing tool driven through `wlb tool`.

The walkthrough closes those gaps. It's a one-time per Windows
version exercise — run it on a fresh Win 10 / Win 11 / Win Server
2019+ box, record the result, ship.

---

## Where it lives

`walkthrough/` at the repo root:

| File                                  | What it does                                                |
|---------------------------------------|-------------------------------------------------------------|
| `walkthrough/README.md`               | Top-level overview and the security/sanitization rules.     |
| `walkthrough/local-notes.env.example` | Template for YOUR local target IP / user / paths.           |
| `walkthrough/01-windows-bootstrap.ps1`| Windows admin PowerShell — full prep in one shot.           |
| `walkthrough/02-linux-pair.sh`        | Linux side — keypair, profile, `wlb status` confirm.        |
| `walkthrough/03-smoke-tests.sh`       | Scripted SSH smoke (5 cases, pass/fail summary).            |
| `walkthrough/04-smoke-checklist.md`   | Manual browser / recording / replay checklist.              |

Anything matching `walkthrough/local-*` is `.gitignore`d — real IPs,
tokens, run logs, and result notes never enter the repo.

---

## Five phases

1. **Windows bootstrap** (`01-windows-bootstrap.ps1`) — installs
   OpenSSH Server, opens the firewall, installs Python deps
   (fastapi / uvicorn / pywinpty), stages `C:\ProgramData\wlb-agent\`,
   generates a token, opens the agent port. Idempotent.
2. **Linux pair** (`02-linux-pair.sh`) — generates an SSH keypair,
   prints the pubkey + install instructions, waits for confirmation,
   writes `workspace/profiles/<WLB_PROFILE>.toml`, runs `wlb status`
   over SSH.
3. **Scripted smoke** (`03-smoke-tests.sh`) — covers `wlb status`,
   `wlb cmd`, `wlb powershell`, `wlb fs push/pull` (with SHA256
   round-trip check), and `wlb tool run` against an ephemeral
   `wlb-tools.toml`. Pass/fail summary; full log to
   `walkthrough/local-smoke-<ts>.log`.
4. **Browser + HTTP smoke** (`04-smoke-checklist.md` sections A-C) —
   open the dashboard, run a tool from the modal, open `/pty.html`
   over SSH then over HTTP (after switching transports), confirm
   bidirectional PTY behavior.
5. **Recording + replay** (`04-smoke-checklist.md` sections D-E) —
   turn on `WLB_PTY_RECORD=1`, run a few sessions, confirm `.cast`
   files appear in `workspace/hosts/<host>/pty/`, open `/casts.html`
   and play one back.

Phase 6 (optional, `04-smoke-checklist.md` section F) — install wlb
on the Windows host itself and exercise the local ConPTY PTY backend.
Most users don't need this.

---

## Security

The walkthrough touches credentials (an SSH key, an agent bearer
token, a Windows IP). The substrate keeps all of it off the public
repo:

- `walkthrough/local-notes.env` (gitignored) holds the real `WIN_HOST`,
  `WIN_USER`, key paths, token paths.
- `walkthrough/local-smoke-*.log` (gitignored) holds the raw smoke
  output.
- `walkthrough/local-results-*.md` (gitignored) is where you record
  the manual checklist result.
- The committed scripts only ever reference `$WIN_HOST`, `$WIN_USER`
  etc. via env — no string ever ends up baked into a tracked file.

For the **bearer token** specifically, follow the save-to-file
pattern (`scripts/windows-agent/README.md` Step 3): the token lives
in a mode-locked file on both sides, never travels via argv, env
value, stdin, or chat.

---

## Pass criteria

The walkthrough passes when:

1. `03-smoke-tests.sh` exits 0 (`5/5 passed`).
2. Every checkbox in `04-smoke-checklist.md` sections A-E is ticked.
3. Section F (local ConPTY) is ticked OR explicitly marked
   `not applicable — controller is Linux`.

Drop the result into a `walkthrough/local-results-YYYY-MM-DD.md`
note for your own audit trail.

---

## Where this fits in the milestone plan

Real-Windows walkthrough is the closeout of the M3 PTY arc:

- **M3.4** introduced the PTY ABC + LocalTransport (Unix) + SshTransport.
- **M3.5** added Windows-local ConPTY via pywinpty (gated behind the
  optional `windows-local-pty` extra).
- **M3.6** added HTTP PTY via `WS /v1/pty` on the agent.
- **M3.7** added asciinema recording at the PtySession boundary.
- **M3.8** added the browser replay UI.
- **Walkthrough** confirms every one of those works on a real Windows
  host before marking the M3 milestone "stable".
