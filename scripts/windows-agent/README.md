# wlb-agent — Windows-side micro-service

A single-file Python service that lets `wlb`'s HTTP transport drive a
Windows host. Use this only when SSH is unavailable (corporate proxy
blocks TCP 22, locked-down regulated environment, etc.). SSH remains
the recommended primary path; this exists as a fallback.

## What you'll need

- Windows 10 1809+ / Windows 11 / Windows Server 2019+ (any host where
  the controller-side `wlb` will run commands)
- Python 3.11+ (install via [python.org](https://www.python.org/downloads/)
  or `winget install Python.Python.3.12`)
- Admin rights for the one-time install

---

## Step-by-step

### 1. Install Python deps

In an elevated PowerShell on the Windows host:

```powershell
python -m pip install --upgrade pip
python -m pip install "fastapi>=0.110" "uvicorn[standard]>=0.27"
```

### 2. Lay out the agent directory

```powershell
New-Item -ItemType Directory -Force -Path 'C:\ProgramData\wlb-agent' | Out-Null
Copy-Item wlb_agent.py             'C:\ProgramData\wlb-agent\'
Copy-Item wlb-agent.example.toml   'C:\ProgramData\wlb-agent\wlb-agent.toml'
```

Tighten the directory ACL (Administrators + SYSTEM only):

```powershell
icacls 'C:\ProgramData\wlb-agent' /inheritance:r `
  /grant 'Administrators:(OI)(CI)F' 'SYSTEM:(OI)(CI)F'
```

### 3. Generate a bearer token

Generate a random token **on the Windows side** and write it to the
token file the agent will read. Never paste this token into a chat or
into any other process. The intended workflow is "file in, file out":

```powershell
# Generate a 32-byte URL-safe token (Python comes with us already).
$token = python -c "import secrets; print(secrets.token_urlsafe(32))"
$tokenPath = 'C:\ProgramData\wlb-agent\token'
$token | Out-File -Encoding ascii -NoNewline $tokenPath
icacls $tokenPath /inheritance:r `
  /grant 'Administrators:F' 'SYSTEM:F'
```

> **Why "file in, file out"?** Bearer tokens are credentials. If they
> appear in chat transcripts, shell history, or environment dumps,
> they're effectively leaked. The agent reads its copy from a
> mode-locked file; the wlb controller reads its copy from another
> mode-600 file. The token never travels by argv or stdin.

### 4. (Recommended) Generate a TLS keypair

For production, run the agent over HTTPS. A self-signed cert is fine
when the controller side pins it via `WLB_HTTP_CA_BUNDLE`:

```powershell
# Self-signed cert valid 5 years. The CN matches the controller's URL.
$cert = New-SelfSignedCertificate `
    -DnsName "win-host.local" `
    -CertStoreLocation "Cert:\LocalMachine\My" `
    -NotAfter (Get-Date).AddYears(5) `
    -KeyExportPolicy Exportable

# Export to PEM
$pwd = ConvertTo-SecureString -String (python -c "import secrets; print(secrets.token_urlsafe(24))") -Force -AsPlainText
Export-PfxCertificate -Cert $cert -FilePath 'C:\ProgramData\wlb-agent\agent.pfx' -Password $pwd | Out-Null
openssl pkcs12 -in 'C:\ProgramData\wlb-agent\agent.pfx' -out 'C:\ProgramData\wlb-agent\agent.crt' -nokeys -password "pass:$($pwd | ConvertFrom-SecureString -AsPlainText)"
openssl pkcs12 -in 'C:\ProgramData\wlb-agent\agent.pfx' -out 'C:\ProgramData\wlb-agent\agent.key' -nocerts -nodes -password "pass:$($pwd | ConvertFrom-SecureString -AsPlainText)"
Remove-Item 'C:\ProgramData\wlb-agent\agent.pfx'
icacls 'C:\ProgramData\wlb-agent\agent.key' /inheritance:r /grant 'Administrators:F' 'SYSTEM:F'
```

Then edit `wlb-agent.toml` so `tls_cert` and `tls_key` point at the PEM files.

If you're on a closed lab network and want plain HTTP, comment both
`tls_cert` and `tls_key` lines out — the agent will start an HTTP server.

### 5. Open the firewall

```powershell
New-NetFirewallRule `
    -Name "wlb-agent" `
    -DisplayName "wlb-agent (HTTPS)" `
    -Enabled True `
    -Direction Inbound `
    -Protocol TCP `
    -LocalPort 8443 `
    -Action Allow
```

Adjust the port to match `port =` in `wlb-agent.toml`.

### 6. Run the agent

For a one-off test:

```powershell
cd 'C:\ProgramData\wlb-agent'
python .\wlb_agent.py --config .\wlb-agent.toml
```

For production, register a service so it auto-starts. The simplest cross-
Windows-version recipe uses NSSM ([nssm.cc](https://nssm.cc)):

```powershell
nssm install wlb-agent `
    "$((Get-Command python).Source)" `
    "C:\ProgramData\wlb-agent\wlb_agent.py --config C:\ProgramData\wlb-agent\wlb-agent.toml"
nssm set wlb-agent Start SERVICE_AUTO_START
nssm set wlb-agent AppDirectory "C:\ProgramData\wlb-agent"
Start-Service wlb-agent
```

---

## Step-by-step (controller side)

### 1. Copy the token file from the Windows side

Use whatever channel matches your environment (Samba share, USB stick,
ssh+scp from a third host). On the controller:

```bash
mkdir -m 700 -p ~/.config/wlb
chmod 600 ~/.config/wlb/http-token
$EDITOR ~/.config/wlb/http-token   # paste the token; SAVE; close
chmod 600 ~/.config/wlb/http-token
```

### 2. Copy the CA cert (if you used TLS)

```bash
cp /mnt/win-share/agent.crt ~/.config/wlb/agent-ca.crt
chmod 600 ~/.config/wlb/agent-ca.crt
```

### 3. Configure wlb

Edit your profile (`workspace/profiles/default.toml` or
`workspace/profiles/<name>.toml`):

```toml
[host]
transport = "http"

[http]
url             = "https://win-host.local:8443"
token_file      = "~/.config/wlb/http-token"
ca_bundle       = "~/.config/wlb/agent-ca.crt"   # omit when using public CA
connect_timeout = 10
verify_tls      = true                            # set to false only for lab
```

Or use env vars:

```bash
export WLB_TRANSPORT=http
export WLB_HTTP_URL=https://win-host.local:8443
export WLB_HTTP_TOKEN_FILE=~/.config/wlb/http-token
export WLB_HTTP_CA_BUNDLE=~/.config/wlb/agent-ca.crt
```

### 4. Verify

```bash
uv run wlb status
uv run wlb cmd "ver"
uv run wlb fs push hello.txt 'C:\stage\hello.txt'
```

---

## Wire protocol

| Method | Endpoint                  | Body                                      | Response                            |
|--------|---------------------------|-------------------------------------------|-------------------------------------|
| GET    | `/v1/health`              | —                                         | `{ok, agent_version, platform, windows_version, powershell}` |
| POST   | `/v1/shell`               | `{cmd, interpreter, timeout}`             | `{ok, exit_code, stdout, stderr, duration_ms, error_code?}`  |
| POST   | `/v1/file/push?path=...`  | raw bytes (`application/octet-stream`)    | `{ok, bytes, path}`                 |
| GET    | `/v1/file/pull?path=...`  | —                                         | bytes (`application/octet-stream`)  |

All requests must carry `Authorization: Bearer <token>`. The agent
verifies with constant-time comparison.

## Security model

- **Network**: TLS recommended. If you must run HTTP, restrict the
  agent's `bind` to a private interface and isolate the segment.
- **Token**: random 32-byte token, file-based on both sides, mode 600.
  Never via argv / env / stdin.
- **Deny-list**: the agent re-runs the wlb dangerous-pattern check
  (format, Format-Volume, bcdedit, etc.) — if a controller misbehaves,
  the agent still refuses to wipe a drive. The controller's deny-list
  is the first guard; the agent's is defense in depth.
- **Privileges**: run the service under a dedicated low-privilege user
  when possible. Tools that need elevation should call themselves with
  `Start-Process -Verb RunAs` rather than running the agent as
  Administrator.

## Limits and known gaps

- Single-file push only in M2.4 (recursive directory push lands in M2.4.1).
- No live stdout streaming yet — the agent captures full output before
  responding. M3 will add chunked streaming for long-running tools.
- The agent reads its config once at startup. Changing the token file
  requires a service restart.
