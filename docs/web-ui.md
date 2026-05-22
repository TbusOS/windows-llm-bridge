# Web UI (M3.3)

A small local dashboard for wlb: shows transport health + the registered
tool list, lets you run any declared tool with a form, streams the output
live in the browser. Built on FastAPI + a single-page vanilla-JS UI (no
build step, no framework dependencies).

It's the same surface MCP / CLI use under the hood — the same
capabilities, the same Result shape, the same permission deny-list. The
Web UI just gives humans a click-through view.

---

## Starting it

```bash
uv run wlb web                    # localhost:8765 (default)
uv run wlb web --port 9000        # custom port
uv run wlb-api --host 0.0.0.0     # bind to all (DANGER — see below)
```

Equivalent invocations:
- `wlb web` — typer subcommand, same flags as `wlb-api`.
- `wlb-api` — the `[project.scripts]` entry point.

The CLI prints the URL it's bound to. The dashboard is at `/`.

---

## Security model (read this before exposing)

**M3.3 has no authentication.** Anyone who can reach the bind address can:

- read the active profile / SSH host / token-file path (not the token
  itself, but the path)
- inspect any declared tool spec
- **run any declared tool with any args**

Default bind is `127.0.0.1:8765` — localhost only. Don't expose past
localhost without an authenticated reverse proxy in front. If you bind
to anything else, the CLI prints a yellow warning to stderr.

To safely expose:
- Stand up a reverse proxy (Caddy, nginx, Traefik) that handles
  authentication, then point it at the wlb-api on localhost.
- Or use SSH local-forward: `ssh -L 8765:127.0.0.1:8765 controller-host`
  and access from your laptop's localhost.

A future M3.3.1 may add a built-in shared-token check, but it's out of
scope for the M3.3 milestone.

---

## Routes

### Static

| Path        | Notes                                          |
|-------------|------------------------------------------------|
| `GET /`     | Bundled `index.html` — the single-page UI.    |
| `GET /static/*` | UI assets (`style.css`, `app.js`).         |
| `GET /api/docs` | FastAPI's auto-generated Swagger UI.       |
| `GET /api/openapi.json` | OpenAPI schema for the JSON endpoints. |

### JSON

| Method | Path                  | Returns                                            |
|--------|-----------------------|----------------------------------------------------|
| GET    | `/api/version`        | `{wlb}`                                            |
| GET    | `/api/describe`       | Registry — transports + capabilities (Result).     |
| GET    | `/api/status`         | Active transport health (Result).                  |
| GET    | `/api/profile`        | Merged active settings (env > profile > defaults). |
| GET    | `/api/maps`           | Configured SMB / Samba mappings.                   |
| GET    | `/api/tools`          | Tool list (Result).                                |
| GET    | `/api/tools/{name}`   | Single tool spec; `404` if unknown.                |

### WebSocket

| Path                | Protocol                                                                |
|---------------------|-------------------------------------------------------------------------|
| `WS /ws/tool/{name}` | First client → server frame is JSON `{"args": {...}}`; server then streams `ToolStreamEvent` JSON one frame per event, closing after `kind=done`. |

The `ToolStreamEvent` shape matches `wlb.capabilities.tool.ToolStreamEvent`
(see [`docs/streaming.md`](streaming.md)):

```json
{"kind":"line",  "line":"50%",      "stream":"stdout"}
{"kind":"progress", "percent":50}
{"kind":"match",    "pattern_label":"success", "match":"OK"}
{"kind":"done",     "ok":true, "output":{"tool":"...","exit_code":0,"...":"..."}}
```

---

## UI walkthrough

The page is sectioned:

1. **Header** — wlb version + active profile name.
2. **Status** — current transport + health snapshot. Yellow tag if
   `ok: false` (e.g. SSH host unset).
3. **Active profile** — file path, ssh / http fields, warnings.
4. **Registry** — table of transports + table of capabilities. Pulled
   from `/api/describe`.
5. **Declared tools** — table of every `[tool.<name>]` from
   `wlb-tools.toml`. The **Run** button opens a modal:
   - Form input per declared arg.
   - **Run** opens `WS /ws/tool/{name}`, sends `{args}`, streams.
   - Output panel renders each line (stderr in warning color),
     a live progress bar from `progress` events, color-coded
     `success` / `failure` match lines, and a final summary block
     with the same fields `wlb tool run` reports.

UI is dark-themed by default, no theme toggle in M3.3.

---

## Programmatic use

The JSON endpoints are usable from anything that speaks HTTP:

```bash
# Health
curl -s localhost:8765/api/status | jq

# Tool list
curl -s localhost:8765/api/tools | jq '.data.tools[].name'
```

WebSocket from Python:

```python
import asyncio, json
import websockets

async def run():
    async with websockets.connect("ws://localhost:8765/ws/tool/echo") as ws:
        await ws.send(json.dumps({"args": {"msg": "hi"}}))
        async for frame in ws:
            ev = json.loads(frame)
            print(ev["kind"], ev.get("line") or ev.get("error_code") or "")
            if ev["kind"] == "done":
                break

asyncio.run(run())
```

---

## Limits and next steps

- **No auth.** See the security section above. Use a reverse proxy or
  SSH tunnel if you need to expose past localhost.
- **No ad-hoc REPL.** The UI lists declared tools only — you cannot
  type a raw `cmd.exe` line from the browser. Use `wlb cmd` from the
  terminal for that. (Why: the WS pipe to `wlb_cmd` would bypass the
  declared-tool surface the security model assumes; left for M3.4.)
- **No history of past runs.** Logs land on disk under
  `workspace/hosts/<host>/tools/<name>/<ts>.log` — the UI links to the
  current run's log but doesn't list past ones.
- **No interactive PTY.** Streaming output is line-based;
  interactive prompts (`y/N?`) won't render. M3.4 will add ConPTY +
  xterm.js for full PTY sessions.
- **No multi-user sessions.** Two browsers connected at the same time
  see independent views; there's no shared state.
