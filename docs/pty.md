# Interactive PTY (M3.4)

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

- Page: `/pty.html` (linked from the dashboard header as `open PTY →`).
- WebSocket: `/ws/pty`.
- Transports: `LocalTransport` (Unix only — uses `pty.openpty`) and
  `SshTransport` (asyncssh `create_process` with a PTY channel on the
  pooled connection).

`HttpTransport` does NOT implement PTY in M3.4 — the wlb-agent needs its
own WS endpoint first. See "What's next" below.

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

## What's next

- **Windows-local ConPTY** (M3.4.1): swap `pty.openpty` for the
  Windows ConPTY API when `sys.platform == "win32"`. Lets a contributor
  test the UI on a Windows laptop without an SSH target.
- **HTTP transport PTY** (M3.5): add a `WS /v1/pty` to the wlb-agent
  and an `HttpPtySession` on the controller. NDJSON won't fit (bidirectional
  binary), so this is a WebSocket-only contract.
- **Recording / replay** (M3.6): asciinema-style cast files saved under
  `workspace/hosts/<host>/pty/<ts>.cast`.
