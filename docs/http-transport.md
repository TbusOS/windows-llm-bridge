# HTTP transport

The HTTP transport exists for environments where SSH is blocked but
HTTPS to the Windows host is allowed. The Windows side runs a small
single-file Python service (`scripts/windows-agent/wlb_agent.py`); the
controller side talks to it through `wlb.transport.http.HttpTransport`.

If SSH works for you, keep using it — pooled SSH is faster, more
audited, and exposes SFTP natively. This page exists for the cases
where SSH doesn't.

---

## Architecture

```
Controller (Linux/macOS)                  Windows host
─────────────────────                     ─────────────
wlb CLI / MCP                             wlb-agent (FastAPI)
    │                                      │
    ▼                                      ▼
wlb.transport.http                         /v1/shell   ──▶ cmd.exe / pwsh
   HttpTransport                           /v1/file/push
       │ httpx                             /v1/file/pull
       │ Bearer <token>                    /v1/health
       │ optional TLS                      │
       ▼                                   ▼
HTTPS (default 8443) ─────────────────────▶ uvicorn
```

- Token-based auth (`Authorization: Bearer <token>`), constant-time
  comparison on the server.
- TLS recommended; plain HTTP allowed for lab use.
- The agent re-runs the wlb deny-list locally as defense-in-depth — even
  if a controller misbehaves, the agent refuses to format a drive.

---

## Wire protocol

| Method | Endpoint                  | Body                                       | Response                                  |
|--------|---------------------------|--------------------------------------------|-------------------------------------------|
| GET    | `/v1/health`              | —                                          | `{ok, agent_version, platform, windows_version, powershell}` |
| POST   | `/v1/shell`               | `{cmd, interpreter, timeout}`              | `{ok, exit_code, stdout, stderr, duration_ms, error_code?}`  |
| POST   | `/v1/shell/stream` (M3.2) | `{cmd, interpreter, timeout}`              | NDJSON stream (`application/x-ndjson`) — see below |
| POST   | `/v1/file/push?path=...`  | raw bytes (`application/octet-stream`)     | `{ok, bytes, path}`                       |
| GET    | `/v1/file/pull?path=...`  | —                                          | bytes (`application/octet-stream`)        |

All requests require `Authorization: Bearer <token>`.

### `/v1/shell/stream` (M3.2)

Streaming variant of `/v1/shell`. The response body is
`application/x-ndjson` — one JSON object per line, terminated by `\n`.
Each line matches the wlb `StreamEvent` schema:

```
{"kind":"line","line":"step 1/3 ...","stream":"stdout"}
{"kind":"line","line":"step 2/3 ...","stream":"stdout"}
{"kind":"line","line":"warn: foo","stream":"stderr"}
{"kind":"done","exit_code":0,"duration_ms":1842}
```

`kind` values:

- `"line"` — one line of output (without trailing newline). `stream` is
  `"stdout"` or `"stderr"`.
- `"done"` — terminal event. `exit_code` + optional `error_code` +
  `duration_ms`. Always the last NDJSON line.

The client (`HttpTransport.run_streaming`) reads with
`httpx.AsyncClient.stream(...)` + `aiter_lines()`. If the stream closes
without a `done` event (network drop, agent crash mid-run), the client
synthesizes a `done` with `error_code=HTTP_AGENT_ERROR`.

Pre-stream HTTP status maps the same way as `/v1/shell`:
401 → `HTTP_AUTH_FAILED`, 403 → `PERMISSION_DENIED`, 5xx → `HTTP_AGENT_ERROR`.

The agent's deny-list still runs server-side as defense-in-depth.
`format c:` produces a 200 response whose only NDJSON line is
`{"kind":"done","error_code":"PERMISSION_DENIED"}` — the stream stays
well-formed, just gets a single terminal event.

Status codes used by `HttpTransport`:

| HTTP status | wlb `error_code`        | Note                                        |
|-------------|-------------------------|---------------------------------------------|
| 200         | (none — success)        | Parse body                                  |
| 401         | `HTTP_AUTH_FAILED`      | Token mismatch                              |
| 403         | `PERMISSION_DENIED`     | Agent deny-list rejected the command        |
| 404         | `FILE_NOT_FOUND`        | Pull source missing                         |
| 400 / 4xx   | `HTTP_AGENT_ERROR`      | Generic client-side request error           |
| 5xx         | `HTTP_AGENT_ERROR`      | Server-side bug — read the agent log        |
| non-JSON    | `HTTP_BAD_RESPONSE`     | Version drift between controller and agent  |

Connection-level failures map as follows:

| httpx exception     | wlb `error_code`         |
|---------------------|--------------------------|
| `ConnectError`      | `HTTP_HOST_UNREACHABLE`  |
| `ConnectTimeout`    | `TIMEOUT_CONNECT`        |
| `ReadTimeout`       | `TIMEOUT_SHELL`          |
| other `HTTPError`   | `HTTP_AGENT_ERROR`       |

---

## Token security

Bearer tokens are credentials. Never paste them into chat windows, shell
history, or environment dumps. wlb follows the **save-to-file + shred**
pattern:

- The agent reads its copy from a mode-locked file
  (`C:\ProgramData\wlb-agent\token`, ACL: Administrators + SYSTEM only).
- The controller reads its copy from another mode-600 file
  (`~/.config/wlb/http-token` by default).
- The token never travels via argv, stdin, or env (the env var
  `WLB_HTTP_TOKEN_FILE` points at the *file*, not the token itself).

[`scripts/windows-agent/README.md`](../scripts/windows-agent/README.md)
walks through token generation on the Windows side and the controller-
side file copy.

---

## Configuration

Two ways to set the HTTP transport (env wins, as always):

### Env

```bash
export WLB_TRANSPORT=http
export WLB_HTTP_URL=https://win-host.local:8443
export WLB_HTTP_TOKEN_FILE=~/.config/wlb/http-token
export WLB_HTTP_CA_BUNDLE=~/.config/wlb/agent-ca.crt    # optional
export WLB_HTTP_TIMEOUT=10                              # optional, default 10
export WLB_HTTP_VERIFY_TLS=1                            # set 0 only for lab
```

### Profile TOML

```toml
[host]
transport = "http"

[http]
url             = "https://win-host.local:8443"
token_file      = "~/.config/wlb/http-token"
ca_bundle       = "~/.config/wlb/agent-ca.crt"
connect_timeout = 10
verify_tls      = true
```

---

## Where things land

- The HttpTransport implementation lives in `src/wlb/transport/http.py`.
- The Windows-side service is `scripts/windows-agent/wlb_agent.py`.
- Setup walkthrough: `scripts/windows-agent/README.md`.
- Bootstrap helper: `scripts/windows-agent/install-agent.ps1` (admin
  shell on the Windows side).

---

## Limits and what's next

- **Single-file push only** in M2.4. Recursive directory push is M2.4.1.
- **No live streaming** — the agent captures full output before responding.
  Long-running tools won't surface progress mid-flight. M3 will add a
  chunked-streaming endpoint.
- **No HTTP connection pool yet**. Each transport call opens an httpx
  client. The MCP server pays a small per-call cost. Pooling here will
  mirror the SSH pool design (`src/wlb/transport/ssh_pool.py`); deferred
  until benchmarks say it matters.
- **The agent is single-instance**. No cluster / load balancer guidance
  yet — wlb assumes one agent per Windows host.
