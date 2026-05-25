# Interactive PTY (M3.4 → M3.6)

A browser-based interactive terminal for wlb. Opens an xterm.js terminal,
pipes keystrokes over a WebSocket to the active transport's PTY, streams
shell output back as raw bytes. Useful for:

- Long-running interactive sessions (`docker exec` on Windows, debugging
  prompts, ncurses tools) that don't fit the request/response model of
  `wlb cmd`.
- Running a quick `pwsh` REPL without leaving the browser.
- Anything where a real terminal — line editing, ANSI colors, signals
  — matters.

It's the same surface MCP / CLI use under the hood (the active wlb
transport), just with a different I/O loop.

---

## Where it lives

- Dashboard page: `/pty.html` (linked from the dashboard header as `open PTY →`).
- Dashboard WebSocket: `/ws/pty` (wlb-api → active transport).
- Transports:
  - `LocalTransport` — Unix `pty.openpty` (M3.4) + Windows ConPTY via
    `pywinpty` (M3.5, optional `windows-local-pty` extra).
  - `SshTransport` — asyncssh `create_process` with a PTY channel on the
    pooled connection.
  - `HttpTransport` — `WebSocket /v1/pty` on the wlb-agent (M3.6, see
    [HTTP PTY](#http-pty-m36) below). The dashboard wraps this transparently;
    `HttpPtySession` is also callable directly from controller code.

---

## Wire protocol

The first text frame from the client tells the server which interpreter
to run and the initial terminal size:

```json
{"interpreter": "cmd", "cols": 80, "rows": 24}
```

`interpreter` is one of `cmd` / `powershell` / `raw` (same meaning as
`wlb cmd` / `wlb powershell`). `cols` + `rows` are passed straight to
`pty.openpty` / asyncssh `term_size=(cols, rows)`.

After that, the protocol is byte-for-byte:

| Direction       | Frame type | Payload                                            |
|-----------------|-----------|----------------------------------------------------|
| client → server | binary    | keystrokes / pasted bytes — written straight to PTY |
| client → server | text JSON | `{"kind":"resize","cols":N,"rows":N}`              |
| client → server | text JSON | `{"kind":"close"}` — explicit teardown             |
| server → client | binary    | raw PTY output bytes (xterm escapes, ANSI colors)  |
| server → client | text JSON | `{"kind":"exit","exit_code":N}` — terminal event   |
| server → client | text JSON | `{"kind":"error","error":"..."}` — pre-PTY failure |

Either side may close the socket at any time; the server cleans up the
PTY in a `finally` block (terminate + close fd / close channel).

---

## Browser UI

The bundled page is intentionally minimal:

- Interpreter dropdown (`cmd` / `powershell` / `raw`).
- Connect / Disconnect buttons.
- Status line showing live state + current `cols × rows`.
- An xterm.js terminal sized to the window minus chrome; the `FitAddon`
  re-fits + sends a `resize` control message on window resize.

xterm.js is loaded from jsDelivr by default. For an air-gapped install,
vendor the assets locally:

```bash
# Pick a known-good version
VER=5.5.0
FIT=0.10.0
mkdir -p src/wlb/api/static/vendor
curl -L "https://cdn.jsdelivr.net/npm/@xterm/xterm@${VER}/lib/xterm.min.js" \
     -o src/wlb/api/static/vendor/xterm.min.js
curl -L "https://cdn.jsdelivr.net/npm/@xterm/xterm@${VER}/css/xterm.min.css" \
     -o src/wlb/api/static/vendor/xterm.min.css
curl -L "https://cdn.jsdelivr.net/npm/@xterm/addon-fit@${FIT}/lib/addon-fit.min.js" \
     -o src/wlb/api/static/vendor/addon-fit.min.js
```

Then edit `src/wlb/api/static/pty.html` to replace the CDN URLs with
`/static/vendor/...`.

---

## Server-side details

### `wlb.transport.base.PtySession`

ABC with `read`/`write`/`resize`/`wait`/`close`. The WebSocket pump in
`wlb.api.server` only depends on this interface — transport authors
implement it (`LocalPtySession`, `SshPtySession`).

### `LocalTransport.open_pty`

Forks a `/bin/sh -i` (or PowerShell on a Windows-native LocalTransport
once ConPTY ships — M3.4.1). `os.read` / `os.write` on the master fd
are wrapped in `asyncio.to_thread` so they don't block the event loop.
Closing the master fd from the main task causes any in-flight `os.read`
in its worker thread to fail with `OSError`, which we swallow.

### `SshTransport.open_pty`

Calls `conn.create_process(command, term_type=..., term_size=(cols,
rows), encoding=None)` on the pooled SSH connection. The PTY is one
extra channel; other shell / SFTP channels on the same connection keep
working. On `asyncssh.ConnectionLost` we `ssh_pool.mark_dead(key)` so
the next acquire redials.

### WebSocket pump

`/ws/pty` runs two coroutines in parallel under one task group:

- `pump_to_ws` — `await session.read()` in a loop, send each chunk as a
  binary frame. On EOF (`b""`), `await session.wait()` and send the
  terminal `{"kind":"exit","exit_code":N}` text frame.
- main loop — `await websocket.receive()` and dispatch by frame type:
  binary → `session.write(bytes)`; text JSON → handle `resize` / `close`.

Either side disconnecting drops us into `finally` which terminates the
PTY and closes the socket.

---

## Security

PTY inherits the dashboard's M3.3 security caveats:

- Default bind 127.0.0.1; no auth.
- A PTY is strictly more dangerous than `wlb_cmd` — the user has a full
  interactive shell, not a single-shot command. The deny-list does NOT
  inspect interactive sessions (it can't reliably parse a stream of
  keystrokes mid-typing).
- Don't expose the wlb-api past localhost without an authenticated
  reverse proxy in front. See `docs/web-ui.md`.

---

## Windows-local PTY (M3.5)

If you run `wlb` *on* Windows (not as a Linux/macOS controller talking
to a remote Windows host), `LocalTransport.open_pty` can spawn a local
ConPTY-backed `cmd.exe` or `pwsh.exe` for the dashboard's `/pty.html`
page.

Implementation: pywinpty — same library the jupyter ecosystem uses.
pywinpty picks ConPTY automatically on Windows 10 1809+ and falls back
to the bundled winpty shim on older systems.

### Install on a Windows controller

```powershell
# From the wlb repo root in an admin PowerShell:
uv sync --extra windows-local-pty
```

That pulls in `pywinpty>=2.0`. Without the extra, `LocalTransport.open_pty`
raises `NotImplementedError` with a hint pointing at this section.

### When you DON'T need this

Most users run `wlb` from a Linux/macOS controller, configure
`WLB_SSH_HOST` to point at a Windows OpenSSH Server, and use the
**SSH PTY** (`SshTransport.open_pty`) which is part of the core deps.
ConPTY only matters when you want a `LocalTransport` PTY — i.e. when
the wlb process *is* the Windows host.

### Backend layout

```
wlb.transport.local.LocalTransport.open_pty
  └─ sys.platform == "win32" ?
      ├─ yes →  wlb.transport._windows_pty.open_windows_pty
      │           ├─ pywinpty.PtyProcess.spawn(argv, dimensions=(rows,cols))
      │           └─ WindowsPtySession wraps the proc
      └─ no  →  pty.openpty() + asyncio.create_subprocess_exec
                LocalPtySession wraps the master fd
```

The two paths produce different concrete `PtySession` subclasses with
the same async surface (`read` / `write` / `resize` / `wait` / `close`),
so callers (`/ws/pty` pump, tests) don't care which fired.

### Testing on Linux CI

`tests/transport/test_windows_pty_dispatch.py` monkeypatches
`sys.platform` to `"win32"` and injects a synthetic `winpty` module to
exercise the Windows branch end-to-end on Linux:

- `argv` selection per interpreter (cmd / raw → `cmd.exe`; powershell
  → `pwsh.exe` if on PATH else `powershell.exe`).
- `PtyProcess.spawn` invoked with `dimensions=(rows, cols)` — Windows
  order, not the Unix `(cols, rows)`.
- `WindowsPtySession`'s `read` normalizes pywinpty's mixed str/bytes
  return; `write` passes bytes through; `resize` calls `setwinsize(rows, cols)`.

Real ConPTY end-to-end runs only on Windows — documented as part of the
Windows walkthrough rather than a CI assertion.

---

## HTTP PTY (M3.6)

When SSH is blocked by network policy, the HTTP transport still gets a
real interactive PTY by talking to the wlb-agent's `WS /v1/pty` endpoint.
The on-the-wire contract is intentionally tighter than the dashboard's
internal `/ws/pty` so it can be re-implemented in other languages.

### Wire protocol

Auth: `Authorization: Bearer <token>` on the handshake — same token as
the REST endpoints. Bad / missing auth → handshake rejected with 1008
(websockets clients see `InvalidStatus` / `ConnectionClosed`).

| Direction       | Frame type | Payload                                                                              |
|-----------------|-----------|--------------------------------------------------------------------------------------|
| client → server | text JSON (1st) | `{"type":"start","interpreter":"cmd"\|"powershell"\|"raw","cols":N,"rows":N,"term_type":"xterm-256color"}` |
| server → client | text JSON | `{"type":"started","pid":N}` (success, sent once)                                    |
| server → client | text JSON | `{"type":"error","code":"BAD_FIRST_FRAME"\|"BAD_INTERPRETER"\|"PTY_NOT_AVAILABLE"\|"PTY_SPAWN_FAILED","message":"..."}` |
| client → server | binary    | keystrokes / paste — written to PTY stdin                                            |
| client → server | text JSON | `{"type":"resize","cols":N,"rows":N}` or `{"type":"close"}`                          |
| server → client | binary    | raw PTY output bytes                                                                 |
| server → client | text JSON | `{"type":"exit","exit_code":N}` (terminal — server closes after)                     |

Either side may close at any time; the agent kills the PTY in its
`finally` block.

### Agent-side PTY backend

The single-file agent ships a tiny `_PtyAdapter` with two backends:

- **Windows (production)** — `pywinpty.PtyProcess.spawn` (ConPTY on
  Windows 10 1809+, winpty shim on older). Requires `pip install pywinpty`
  on the Windows host where the agent runs.
- **Unix (dev / test)** — `pty.openpty()` + `/bin/sh -i`. Lets the
  agent serve a working PTY on Linux so contract tests run on CI without
  a Windows machine.

### Controller-side `HttpPtySession`

`wlb.transport.http.HttpPtySession` is a `PtySession` subclass over a
single WebSocket. Internal buffer chops oversized frames across multiple
`read(n)` calls. Concurrent `read` / `write` is safe — websockets
permits one task per direction; an internal `asyncio.Lock` serializes
multiple `read()` callers.

### Tests

- `tests/transport/test_http_pty_client.py` — `HttpPtySession` against
  a `websockets.serve()` mock that exercises handshake, bytes, resize,
  exit, and 7 error paths (auth fail, bad first kind, binary before
  started, garbled JSON, etc.).
- `tests/transport/test_wlb_agent_pty.py` — end-to-end against the
  real agent under uvicorn on a free port; verifies the
  controller ↔ agent contract on a Linux box (using the Unix PTY
  backend; Windows ConPTY validates via the walkthrough).

---

## What's next

- **Recording / replay** (M3.7): asciinema-style cast files saved under
  `workspace/hosts/<host>/pty/<ts>.cast`. Tap into the existing
  `PtySession.read` / `write` boundary so all three transports record
  uniformly.
- **Real Windows walkthrough**: spin up Windows + OpenSSH + wlb-agent
  with `pywinpty`, verify the ConPTY paths (local + agent) for real.
