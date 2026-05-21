# install-agent.ps1 — bootstrap the wlb-agent on a Windows host.
#
# What this does (idempotent — safe to re-run):
#   1. Ensures C:\ProgramData\wlb-agent\ exists with a tight ACL.
#   2. Copies wlb_agent.py + wlb-agent.toml into it.
#   3. Generates a random token and writes it to <dir>\token (mode-locked).
#   4. Opens TCP 8443 in the firewall for the agent.
#   5. Prints the next-steps for installing as a service (NSSM-based).
#
# What this DOES NOT do:
#   - Install Python or the fastapi/uvicorn packages. Run those once on
#     your own (see README.md step 1).
#   - Generate TLS keys. See README.md step 4 for the openssl flow.
#   - Install a Windows service. We don't pick a service manager for you —
#     NSSM is the simplest, but your environment may prefer Task Scheduler.

#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

function Write-Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Write-OK($m)   { Write-Host "[ OK ] $m" -ForegroundColor Green }
function Write-Warn2($m){ Write-Host "[WARN] $m" -ForegroundColor Yellow }

$AgentDir = 'C:\ProgramData\wlb-agent'

# ─── 1. Directory + ACL ────────────────────────────────────────────
Write-Step "Preparing $AgentDir"
if (-not (Test-Path $AgentDir)) {
    New-Item -ItemType Directory -Path $AgentDir | Out-Null
}
icacls $AgentDir /inheritance:r `
    /grant "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F" | Out-Null
Write-OK "ACL locked down to Administrators + SYSTEM"

# ─── 2. Copy agent files ───────────────────────────────────────────
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Copy-Item -Force "$here\wlb_agent.py" "$AgentDir\wlb_agent.py"
if (-not (Test-Path "$AgentDir\wlb-agent.toml")) {
    Copy-Item -Force "$here\wlb-agent.example.toml" "$AgentDir\wlb-agent.toml"
    Write-OK "Wrote initial wlb-agent.toml (review and edit before starting!)"
} else {
    Write-Warn2 "wlb-agent.toml already exists — left as-is"
}

# ─── 3. Generate token (if absent) ─────────────────────────────────
$tokenPath = "$AgentDir\token"
if (-not (Test-Path $tokenPath)) {
    Write-Step "Generating bearer token"
    $token = python -c "import secrets; print(secrets.token_urlsafe(32))"
    if (-not $token) { throw "python -c failed; is python.exe on PATH?" }
    Set-Content -Path $tokenPath -Value $token -NoNewline -Encoding ASCII
    icacls $tokenPath /inheritance:r /grant "Administrators:F" "SYSTEM:F" | Out-Null
    Write-OK "Token written to $tokenPath (mode-locked)"
} else {
    Write-Warn2 "Token file already exists at $tokenPath — left as-is"
}

# ─── 4. Firewall ───────────────────────────────────────────────────
Write-Step "Ensuring inbound firewall rule for TCP 8443"
$rule = Get-NetFirewallRule -Name "wlb-agent" -ErrorAction SilentlyContinue
if (-not $rule) {
    New-NetFirewallRule `
        -Name "wlb-agent" `
        -DisplayName "wlb-agent (HTTPS)" `
        -Enabled True `
        -Direction Inbound `
        -Protocol TCP `
        -LocalPort 8443 `
        -Action Allow | Out-Null
    Write-OK "Firewall rule created for TCP 8443"
} else {
    Write-OK "Firewall rule already present"
}

# ─── 5. Next steps ─────────────────────────────────────────────────
Write-Host ""
Write-Step "Next"
Write-Host ""
Write-Host "  1. Review $AgentDir\wlb-agent.toml and adjust bind / port / TLS paths."
Write-Host "  2. (Optional) Generate TLS keys per README.md step 4."
Write-Host "  3. Smoke test in the foreground:"
Write-Host "       cd $AgentDir"
Write-Host "       python .\wlb_agent.py --config .\wlb-agent.toml"
Write-Host ""
Write-Host "  4. Install as a service (NSSM example):"
Write-Host "       nssm install wlb-agent (Get-Command python).Source ``"
Write-Host "         '$AgentDir\wlb_agent.py --config $AgentDir\wlb-agent.toml'"
Write-Host "       nssm set wlb-agent Start SERVICE_AUTO_START"
Write-Host "       Start-Service wlb-agent"
Write-Host ""
Write-Host "  5. Copy the token to your controller:"
Write-Host "       $tokenPath"
Write-Host "     -> on Linux:  chmod 600 ~/.config/wlb/http-token"
Write-Host ""
Write-OK "wlb-agent files staged."
