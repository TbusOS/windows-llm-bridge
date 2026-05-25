# windows-llm-bridge — Plan

> Pair with [`REQUIREMENTS.md`](REQUIREMENTS.md). This file is the
> implementation roadmap. When you change scope, also touch REQUIREMENTS
> so the two stay aligned.

---

## Milestones at a glance

| Milestone | Goal                                             | Status      |
|-----------|--------------------------------------------------|-------------|
| **M0**    | Repo bootstrap                                   | shipped     |
| **M1.1**  | SSH transport real (asyncssh, cmd/powershell)    | shipped     |
| **M1.2**  | TOML profiles + `wlb setup ssh` interactive + `--profile` flag | shipped |
| **M1.3**  | SSH connection pool (per-host, lazy redial on ConnectionLost) | shipped |
| **M1**    | First usable release — SSH transport + cmd/powershell + status/describe + MCP + CLI | in progress |
| **M2.1**  | filesync — SFTP push/pull + LocalTransport copy           | shipped     |
| **M2.2**  | SMB / Samba path translation + local-copy shortcut        | shipped     |
| **M2.3**  | Tool runner — wlb-tools.toml + regex parsing + log capture | shipped    |
| **M2.4**  | HTTP transport + Windows-side wlb-agent micro-service     | shipped    |
| **M2**    | File transfer + named-tool runner + streaming output + HTTP transport | shipped |
| **M3.1**  | Streaming — StreamEvent + run_streaming (local + ssh) + tool stream CLI | shipped |
| **M3.2**  | HTTP transport streaming — agent NDJSON endpoint + httpx aiter_lines    | shipped |
| **M3.3**  | Web UI — FastAPI dashboard + WebSocket tool-run streaming               | shipped |
| **M3.4**  | Interactive PTY — base ABC + Local (Unix) + SSH + xterm.js page         | shipped |
| **M3.5**  | Windows-local PTY (ConPTY) — pywinpty dispatch + dispatch tests         | shipped |
| **M3.6**  | HTTP PTY — wlb-agent `WS /v1/pty` + `HttpPtySession`                    | shipped |
| **M3.7**  | PTY recording — asciinema v2 `.cast` writer at PtySession boundary      | shipped |
| **M3.8**  | Replay UI — `/casts.html` + asciinema-player v3 + list/serve endpoints  | shipped |
| **M3.9**  | Real-Windows walkthrough — scripts + checklist shipped (machine run TBD)| substrate shipped |
| **M3**    | Skill packs + MCP progress notifications                                | in progress |

---

## M0 — Repo bootstrap

This is the commit you're reading.

- [x] Directory tree
- [x] `pyproject.toml`, `.python-version`, `.gitignore`, `LICENSE`
- [x] `CLAUDE.md` (AI-agent rules)
- [x] `REQUIREMENTS.md`, `PLAN.md`, `README.md`
- [x] `wlb.infra` skeleton: `result.py`, `errors.py`, `permissions.py`,
      `registry.py`, `workspace.py`, `safe_path.py`
- [x] `wlb.transport.base` ABC
- [x] `wlb.transport.local` (loopback, used for tests)
- [x] `wlb.transport.ssh` stub (real impl in M1)
- [x] `wlb.capabilities.{cmd, powershell, status}` skeletons
- [x] `wlb.mcp.server` entry + `wlb.mcp.tools.{status, cmd, powershell}`
- [x] `wlb.cli.main` entry + 5 subcommands
- [x] `scripts/install.sh`, `uninstall.sh`, `check_sensitive_words.sh`
- [x] `scripts/windows-setup/enable-openssh.ps1`
- [x] `tests/test_smoke.py` green on a fresh clone
- [x] `docs/architecture.md`, `quickstart.md`, `windows-side-setup.md`,
      `mcp-integration.md`

**Done when:** `uv sync && uv run pytest -q` is green, `uv run wlb describe`
prints the planned tool matrix, and `uv run wlb-mcp` starts without errors.

---

## M1 — First usable release

Goal: an end-to-end loop with one transport (SSH) and three capabilities
(cmd / powershell / status). The "Success criteria" in REQUIREMENTS §7
becomes runnable.

### Work breakdown (file level)

#### Transport layer
- [x] `src/wlb/transport/ssh.py` — real `SshTransport` (M1.1, 2026-05-20):
  - [x] asyncssh client; connect + key auth (password auth not wired —
        default deny; can revisit behind a flag if anyone asks).
  - [x] `shell(cmd, timeout)` runs through Windows OpenSSH default shell
        (cmd.exe). `interpreter` flag dispatches to `pwsh.exe` /
        `powershell.exe` with `-NoProfile -NonInteractive -EncodedCommand`
        (base64 UTF-16LE) so quoting never escapes the outer cmd.exe layer.
  - [x] Falls back from pwsh.exe to powershell.exe automatically when
        the primary binary isn't on PATH.
  - [x] `health()` returns reachable / Windows version / pwsh version /
        connect duration.
  - [x] Structured error mapping: TRANSPORT_NOT_CONFIGURED / SSH_AUTH_FAILED
        / SSH_HOSTKEY_REJECTED / SSH_KEY_NOT_FOUND / SSH_HOST_UNREACHABLE /
        SSH_CONNECTION_LOST / TIMEOUT_CONNECT / TIMEOUT_SHELL /
        SHELL_NONZERO_EXIT / POWERSHELL_NOT_AVAILABLE.
  - [x] Connection pool (M1.3, 2026-05-21): `wlb.transport.ssh_pool`.
        Pool keyed by `(host, port, user, key, known_hosts, timeout)`.
        Per-key `asyncio.Lock` serializes dials; same-key concurrent
        acquires share one connection. asyncssh opens a fresh channel per
        `run()`, so the shared conn handles concurrent runs safely.
        `mark_dead(key)` on `ConnectionLost` evicts and redials on the
        next acquire. CLI's `run_async` calls `close_all()` so per-process
        invocations exit with an empty pool; the MCP server keeps the
        pool for its lifetime.
  - [ ] `check_permissions()` transport overlay for SSH-specific rules.
- [x] `src/wlb/transport/local.py` — covered by tests/transport/test_local.py.

#### Capability layer
- `src/wlb/capabilities/cmd.py` — already in M0; flesh out:
  - line-ending normalization (CRLF → LF for log storage),
  - codepage detection (chcp 65001 prefix when env says UTF-8),
  - error mapping (exit_code != 0 → `SHELL_NONZERO_EXIT`).
- `src/wlb/capabilities/powershell.py` — already in M0; flesh out:
  - choose between `powershell.exe` (Win PS 5) and `pwsh.exe` (PS 7+)
    based on availability,
  - `-NoProfile -NonInteractive -ExecutionPolicy Bypass` defaults,
  - optional JSON output mode (`ConvertTo-Json -Depth N` wrapper).
- `src/wlb/capabilities/status.py` — already in M0; flesh out:
  - probes: reachable, ssh-version, pwsh-version, os-version,
    free-disk on system drive.

#### MCP layer
- `src/wlb/mcp/server.py` — wired to register status / cmd / powershell.
- `src/wlb/mcp/tools/{status,cmd,powershell}.py` — already in M0;
  flesh out docstrings to match the alb tone.

#### CLI layer
- [x] `wlb setup ssh` (M1.2, 2026-05-21) — interactive prompt for host /
      port / user / key / known_hosts / timeout (defaults from existing
      profile if any). Atomic write to `workspace/profiles/<name>.toml`
      with mode 600. `--non-interactive` for scripted use. Re-runnable.
- [x] `wlb setup local` — writes a local-loopback profile.
- [x] `wlb setup show` — prints merged active settings + source per key.
- [x] `wlb setup list` — lists every profile on disk.
- [x] `wlb setup path` — prints absolute path of a profile file.
- [x] `wlb doctor` extended with a "profile" probe (active name + loaded?
      + file path).

#### Infrastructure
- [x] `src/wlb/infra/config.py` — `load_active(profile_name=None)` layers
      env > profile > built-in defaults. Profile resolution: arg →
      `WLB_PROFILE` env → `"default"`. Corrupt files surface as a
      `profile_warnings` entry, not a hard error.
- Profile TOML schema (as actually written by `wlb setup ssh`):
  ```toml
  [host]
  transport = "ssh"

  [ssh]
  host          = "<win-host>"
  port          = 22
  user          = "<user>"
  key           = "~/.ssh/wlb_ed25519"
  known_hosts   = ""          # optional; "none" disables host-key check (tests only)
  connect_timeout = 10        # optional; default 10
  ```
- [x] `src/wlb/infra/env_loader.py` — loads `.env` / `.env.local`.

#### Tests
- `tests/transport/test_local.py` — local transport returns structured.
- `tests/transport/test_ssh.py` (marked `integration`) — needs a real
  Windows host; skipped in CI unless `WLB_TEST_SSH_HOST` is set.
- `tests/capabilities/test_cmd.py`, `test_powershell.py` —
  parameterized over the local transport so they're hermetic.
- `tests/mcp/test_tools.py` — register all, assert 5 tools present.
- `tests/cli/test_smoke.py` — `wlb describe` returns JSON; `wlb status`
  prints something.

#### Docs
- `docs/architecture.md` — layer model (transport / capability / MCP / CLI),
  Result type, permission flow.
- `docs/quickstart.md` — the success-criteria walkthrough.
- `docs/windows-side-setup.md` — enable OpenSSH Server, harden it
  (disable password auth, key only), test from Linux side.
- `docs/mcp-integration.md` — JSON snippet for Claude Code / Cursor /
  Codex, with explanations.
- `docs/errors.md` — full error-code catalog (auto-generated from
  `wlb.infra.errors.ERROR_CODES`).

**Done when:**
- `pytest -q` green on a clean clone (without integration tests).
- The 8-step REQUIREMENTS §7 M1 walkthrough completes against a real
  Windows host on the maintainer's network.
- `wlb describe` lists 1 transport (`ssh`, status `beta`), 1 transport
  (`local`, status `beta`), 3 capabilities (status `beta`).

---

## M2 — File transfer + named-tool runner + streaming + HTTP

Adds the "actually drive vendor tools" capability that motivates the
project.

### Work breakdown (file level)

#### Capabilities
- [x] `src/wlb/capabilities/filesync.py` (M2.1, 2026-05-21):
  - [x] `push(transport, local, remote)` / `pull(transport, remote, local)` →
        `Result[FileSyncOutput]` (local / remote / direction / bytes / duration).
  - [x] SshTransport: `conn.start_sftp_client()` SFTP put/get with
        `recurse=local.is_dir()` for push, `recurse=stat.type==DIR` for pull.
  - [x] LocalTransport: `shutil.copy2` / `copytree` so capability tests are
        hermetic and Windows-self-use works.
  - [x] SFTP exception mapping: `SFTPNoSuchFile` / `SFTPPermissionDenied` →
        `REMOTE_PATH_INVALID` (push) or `FILE_NOT_FOUND` (pull);
        `SFTPError` → `SFTP_ERROR`; `ChannelOpenError` → `SFTP_NOT_AVAILABLE`;
        `ConnectionLost` triggers `ssh_pool.mark_dead`.
  - [x] SMB/Samba shortcut (M2.2, 2026-05-21): `wlb.infra.smb_maps`
        — translate `/mnt/win-share/...` ↔ `C:\share\...` (Linux case-
        sensitive, Windows case-insensitive). Configured via
        `WLB_SMB_MAPS` env (`linux=windows;linux=windows`) or profile
        `[[smb_maps]]` array. `wlb fs push|pull` accepts either form
        and skips SFTP when the mount root is reachable. Silent fall-
        back to SFTP when the mount isn't mounted. `FileSyncOutput.via`
        reports `smb` / `sftp` / `local`. `wlb fs maps` inspects what's
        loaded + which mounts are reachable.
  - [ ] Progress callback (M2.3 with named-tool runner streaming).
- [x] `src/wlb/capabilities/tool.py` (M2.3, 2026-05-21):
  - [x] `wlb.infra.tools_config`: TOML loader for `workspace/wlb-tools.toml`
        (override via `WLB_TOOLS_FILE`). Lenient: malformed tools become
        warnings, not hard errors. Validates interpreter / command_template /
        timeout / args list / regex sub-table.
  - [x] `list_tools()` / `show_tool(name)` / `run_tool(transport, name, args)`.
  - [x] `str.format_map(args)` for template substitution. Required args
        validated up-front. Values reject newlines / NULs / shell metachars
        (`;` `&` `|` `<` `>` backtick `$`) so a single tool call can't
        spawn a multi-statement shell sequence.
  - [x] `workdir` wrapped per interpreter (`pushd ... && cmd & popd` for
        cmd; `Push-Location ... try {{}} finally Pop-Location` for
        powershell; raw passes through unchanged).
  - [x] Full stdout+stderr saved to `workspace/hosts/<host>/tools/<name>/<ts>.log`
        with a header (tool / interpreter / invoked / exit_code).
  - [x] `progress_re` group 1 → last-seen percent; `success_re` /
        `failure_re` parsed against combined output. Verdict:
        `failure_match` → fail; `success_re` declared but no match →
        fail; non-zero exit → fail.
  - [x] Transport-level errors (timeout / auth / connection / permission)
        preserved with their original `error_code` so the agent sees
        the real cause.
  - [x] 5 new error codes: `TOOL_NOT_FOUND` / `TOOLS_CONFIG_ERROR` /
        `TOOL_ARG_MISSING` / `TOOL_ARG_INVALID` / `TOOL_FAILED`.
  - [x] MCP: `wlb_tool_list` / `wlb_tool_show` / `wlb_tool_run`.
  - [x] CLI: `wlb tool list / show / run --arg key=value`.
  - [x] `wlb-tools.example.toml` template in the repo root.
  - [ ] Live progress streaming (M3+): wraps stdout/stderr lines into
        an AsyncIterator[StreamEvent]. M2.3 captures full output and
        surfaces progress post-completion.
- `src/wlb/capabilities/stream.py`:
  - Generic line-streaming helper shared by `tool.py` and a future
    `cmd --stream` mode.

#### Transports
- `src/wlb/transport/http.py`:
  - Counterpart `wlb-agent` micro-service runs on Windows (FastAPI), wlb
    talks to it over HTTPS. Used when SSH is blocked.
  - Auth: shared secret (HMAC) — same save-to-file + shred pattern wlb's
    CLAUDE.md describes for the maintainer's own workflow.
- `src/wlb/transport/hybrid.py`:
  - Pick best transport per operation. E.g. file push prefers SFTP if SSH
    is available; otherwise falls back to HTTP multipart.

#### CLI / MCP additions
- `wlb fs push|pull` + `wlb_push` / `wlb_pull` MCP tools.
- `wlb tool list|run` + `wlb_tool_list` / `wlb_tool_run` MCP tools.

#### Web API
- `src/wlb/api/server.py` — FastAPI mirror of the CLI (skeleton in M1,
  fleshed out here).

#### Windows-side agent (separate optional component)
- `scripts/windows-agent/` — minimal FastAPI script + `install-agent.ps1`.

**Done when:**
- Full M2 success criteria from REQUIREMENTS §7 work end-to-end.
- A user can declare a tool in TOML, the agent can call it through MCP,
  and structured progress + final status come back.

---

## M3.6 — HTTP PTY (shipped)

Closes the last gap in the PTY coverage: HTTP transport now serves
interactive PTY through a WebSocket on the wlb-agent.

- [x] `src/wlb/transport/http.py`:
  - [x] `HttpPtySession` (PtySession subclass, WS-backed) — bidirectional
        binary + text-control JSON; internal buffer chops oversized frames;
        `asyncio.Lock` serializes concurrent `read()` callers.
  - [x] `HttpTransport.open_pty` — translates `http(s)://` to `ws(s)://`,
        sends start frame, waits for `started` or `error`.
  - [x] `_ws_url` / `_ws_ssl_context` helpers honor `verify_tls` + `ca_bundle`.
  - [x] `supports_pty = True`.
- [x] `scripts/windows-agent/wlb_agent.py`:
  - [x] `_PtyAdapter` cross-platform: pywinpty on Win32, `pty.openpty` on
        Unix (for dev / tests, agent on Linux still serves `/v1/pty`).
  - [x] `WS /v1/pty` endpoint: Bearer auth on handshake (close 1008 on
        rejection), start-frame validation, pump-to-WS + WS-to-PTY in a
        single task group, control JSON dispatch (`resize` / `close`).
- [x] `pyproject.toml` — `websockets>=12.0` as direct dep (was transitive
      via uvicorn[standard]; making it explicit so HttpPtySession's import
      isn't accidentally orphaned).
- [x] `wlb.infra.registry` — pty capability `supported_transports` adds
      `"http"`; HttpTransport spec gains `/v1/pty` reference.
- [x] Tests:
  - [x] `tests/transport/test_http_pty_client.py` (20 tests) —
        HttpPtySession against a `websockets.serve()` mock; covers
        handshake, bytes, resize, exit, 8 error paths, `_ws_url` /
        `_ws_ssl_context` helpers.
  - [x] `tests/transport/test_wlb_agent_pty.py` (7 tests) — end-to-end
        contract against the real agent under uvicorn on a free port;
        verifies handshake, echo round-trip, resize, exit_code propagation,
        auth + protocol violations.
- [x] Docs: `docs/pty.md` "HTTP PTY (M3.6)" section + protocol table;
      `docs/http-transport.md` adds `WS /v1/pty` row + bumps "What's next".

**Done when:** `pytest -q` 283 passing (256 baseline + 27 new). The
controller calls `HttpTransport.open_pty()` and gets a working
PtySession over WS; the agent on Linux serves it with `pty.openpty`,
on Windows with ConPTY.

---

## M3.7 — PTY recording (shipped)

Transparent asciinema v2 cast recording. Wraps any `PtySession` so the
recorder works identically across local / SSH / HTTP transports.

- [x] `src/wlb/capabilities/pty_recorder.py`:
  - [x] `CastRecorder` — v2 header + NDJSON event writer, `asyncio.Lock`-
        guarded, line-buffered file, UTF-8 `errors="replace"`.
  - [x] `RecordingPtySession(PtySession)` — transparent decorator;
        `read` mirrors to `"o"` events, `write` optionally to `"i"`,
        `close` closes inner then recorder.
  - [x] `cast_path_for(host, interpreter, override_dir=None)` — workspace
        path or external dir; traversal-safe.
  - [x] `maybe_wrap(session, settings, ...)` — gate by settings; returns
        unchanged session when disabled (zero overhead).
- [x] `src/wlb/infra/config.py`:
  - [x] `PtyRecordSettings(enabled, record_input, dir)` dataclass.
  - [x] `_layer_bool` helper for env > profile > default with
        `1/0/true/false/yes/no/on/off` normalization.
  - [x] `[pty]` section + `WLB_PTY_RECORD` / `WLB_PTY_RECORD_INPUT` /
        `WLB_PTY_RECORD_DIR` env support.
- [x] `src/wlb/transport/base.py` + concrete classes:
  - [x] `host_label` property — sanitized identifier for
        `workspace/hosts/<label>/...`. local → `"local"`, ssh →
        configured host (validated) else `"ssh"`, http → URL hostname
        (validated) else `"http"`.
- [x] `src/wlb/api/server.py`:
  - [x] `ws_pty` wraps the opened PTY in `maybe_record_pty` with the
        active profile's `pty_record` settings. No-op when disabled.
  - [x] `/api/profile` surfaces the `pty_record` block so a dashboard
        can show recording state.
- [x] `src/wlb/infra/workspace.py` — docstring lists `pty` category.
- [x] `src/wlb/infra/registry.py` — pty capability description mentions
      recording + activation env.
- [x] Tests (32 new, 315 total):
  - [x] `tests/capabilities/test_pty_recorder.py` (19) — `.cast` header
        + event encoding, UTF-8 replace, idempotent close, empty-write
        drop, decorator passthrough, input gating, `cast_path_for`
        defaults + override + traversal fallback, `maybe_wrap` gating,
        end-to-end recording of a real `LocalPtySession`.
  - [x] `tests/infra/test_profile.py` (5 new) — `[pty]` section + env
        override + boolean normalization.
  - [x] `tests/transport/test_host_label.py` (8) — every transport's
        `host_label` resolves safely and refuses traversal.
- [x] Docs: `docs/pty.md` "PTY recording (M3.7)" section + format
      reference + activation matrix + replay commands.

**Done when:** Setting `WLB_PTY_RECORD=1` and opening `/pty.html`
produces a valid `.cast` file under
`workspace/hosts/<host>/pty/<ts>-<interp>.cast` that
`asciinema play` can replay verbatim.

---

## M3.8 — Replay UI (shipped)

Browser-based viewer for the `.cast` files produced by M3.7. Adds a
`/casts.html` dashboard page powered by asciinema-player v3 and two
sandboxed API endpoints to list + serve recordings.

- [x] `src/wlb/api/server.py`:
  - [x] `GET /api/casts` — list `.cast` files under
        `workspace/hosts/<host>/pty/` newest-first, with metadata
        (host / filename / path / size / mtime). Skips unsafe host
        directory names and missing `pty/` subdirs.
  - [x] `GET /api/casts/{host}/{filename}` — serve one cast as
        `application/x-asciicast`. Validates host via `is_safe_host`,
        rejects non-`.cast` extensions / traversal / dot-prefixed
        filenames, resolves the path through `Path.resolve()` to
        refuse symlinks escaping `<workspace>/hosts`.
  - [x] `GET /casts.html` — explicit route (same pattern as `/pty.html`).
- [x] `src/wlb/api/static/casts.html` (new) — vanilla JS page with a
      sidebar list + player pane. Loads asciinema-player v3 from
      jsDelivr (vendoring instructions in `docs/pty.md`).
- [x] `src/wlb/api/static/casts.js` (new) — fetches `/api/casts`,
      renders sidebar buttons, on click disposes any previous player
      and creates a fresh `AsciinemaPlayer.create(...)` instance.
- [x] `src/wlb/api/static/style.css` — `.casts-main` / `.casts-sidebar`
      / `.cast-item` / `.casts-player` rules in the existing dark
      theme.
- [x] `src/wlb/api/static/index.html` — dashboard header gains a
      `casts →` link next to `open PTY →`.
- [x] Tests: `tests/api/test_casts_endpoints.py` (13).
- [x] Docs: `docs/pty.md` "Replay UI (M3.8)" section with the endpoint
      matrix, security notes, and air-gapped vendoring instructions.

**Done when:** With `WLB_PTY_RECORD=1`, opening `/pty.html` and running
a session produces a `.cast` file that shows up in `/casts.html` and
plays back in the browser.

---

## M3.9 — Real-Windows walkthrough (substrate shipped)

Adds the scripts, checklists, and docs needed to validate every M3.x
shipping feature against a real Windows host. The actual machine run
is a one-off per Windows version and lives in the operator's local
notes — not the repo.

- [x] `walkthrough/` directory added:
  - [x] `README.md` — top-level overview, security/sanitization rules.
  - [x] `local-notes.env.example` — template for the operator's real
        target details (`WIN_HOST`, `WIN_USER`, `WLB_KEY`,
        `WLB_PROFILE`, `WIN_AGENT_PORT`, `WLB_HTTP_TOKEN_FILE`,
        `WLB_HTTP_CA_BUNDLE`, `WIN_STAGE_DIR`).
  - [x] `01-windows-bootstrap.ps1` — single PowerShell admin script:
        Phase 1 OpenSSH (delegates to existing `enable-openssh.ps1`),
        Phase 2 Python 3.11+ probe, Phase 3 fastapi+uvicorn install,
        Phase 4 pywinpty (optional), Phase 5 wlb-agent stage
        (delegates to existing `install-agent.ps1`), Phase 6 summary
        with detected IPv4 + next-steps printout. Idempotent.
  - [x] `02-linux-pair.sh` — source local-notes.env, ed25519 keygen,
        print pubkey + Windows install one-liner, wait for `y`, write
        `workspace/profiles/<WLB_PROFILE>.toml` atomically (mode 600),
        smoke `wlb status`.
  - [x] `03-smoke-tests.sh` — 5 scripted SSH cases: status, cmd echo,
        powershell echo, fs push/pull with sha256 round-trip,
        ephemeral wlb-tools.toml + tool run round-trip. Tees output
        to `walkthrough/local-smoke-<ts>.log` (gitignored). Exit
        reflects pass/fail count.
  - [x] `04-smoke-checklist.md` — manual checklist for the things not
        scriptable from CLI: dashboard A1-A5, SSH PTY B1-B7,
        HTTP transport + HTTP PTY C1-C5, recording D1-D3, replay UI
        E1-E5, optional local ConPTY F1-F4. Result-recording template.
- [x] `.gitignore` — `walkthrough/local-*` (with `!walkthrough/local-*.example`
      so templates stay tracked).
- [x] `docs/walkthrough.md` — public-facing doc: why it exists, five
      phases, security/sanitization rules, pass criteria, milestone
      relationship.

**Done when:** Operators can run the three scripts on a fresh
Windows host and reach `5/5 passed` on `03-smoke-tests.sh`. Actual
execution and result recording are local to each operator — the
substrate proves we've shipped enough to make that one-off cheap.

---

## M3 — Web UI + PTY + skill packs

The "production-grade" milestone. Out of scope for the initial author's
near-term commitment, but designed for so PRs can land cleanly.

- Web UI (React + Vite) — small dashboard showing transport health, tool
  list, recent runs, ad-hoc CLI runner.
- Interactive PTY: ConPTY on Windows side, xterm.js on browser side,
  WebSocket in between.
- Session recording / replay.
- Skill packs: per-tool `SKILL.md` an LLM client can preload as system
  prompt context.

---

## Risks and open questions

| Risk                                                | Mitigation                                                            |
|-----------------------------------------------------|-----------------------------------------------------------------------|
| Windows OpenSSH `cmd.exe` PTY behavior is quirky    | Use non-PTY exec for M1; revisit for PTY in M3.                       |
| Codepage / encoding chaos on legacy Windows         | Default to UTF-8 (chcp 65001) on the wrapped session; document it.    |
| Long-running tools that ignore Ctrl-C               | Document the timeout-then-kill behavior; expose `force_kill=True`.    |
| AV / EDR flagging the Windows agent (M2)            | Ship as plain-source PowerShell + py3 script; document the AV exclusion needed. |
| Secret handling for HTTP transport (M2)             | Mirror alb's save-to-file + shred pattern; never accept secret on CLI args. |

---

## Coding conventions (echoes alb, restated here for in-repo discoverability)

- **One thing per module.** A transport module exposes one Transport
  subclass. A capability module exposes one or two related functions.
- **No global state.** Settings come through `ActiveSettings`. Transports
  are constructed per call (the transport_factory caches under the hood).
- **No `print()` in library code.** Only CLI / scripts may print. Library
  code returns Result objects.
- **No backwards-compat shims** until M1 ships. Until then, just rename.

---

## How to track progress

Each milestone has a GitHub milestone with the same name. Issues are
labeled `M1` / `M2` / `M3`. A PR closing an issue must:

1. Move the relevant check-box in this file.
2. Update `wlb.infra.registry` so `wlb describe` reflects the new status.
3. Add / update tests.
4. Update `docs/` if behavior changed.
5. Update `README.md`'s capability matrix on milestone flips.
