# walkthrough/01-windows-bootstrap.ps1
#
# Single-shot prep for a Windows host that will serve as a wlb target
# in the real-Windows walkthrough. Bundles OpenSSH Server setup +
# wlb-agent install + Python deps + (optional) pywinpty for HTTP PTY.
#
# Idempotent: every step probes before changing state. Re-run after
# fixing one piece without redoing the others.
#
# Usage (admin PowerShell, on the Windows host):
#
#     # Copy this directory to the Windows side first — Samba share,
#     # USB stick, scp from a controller — anywhere the script + its
#     # siblings (../scripts/windows-setup/, ../scripts/windows-agent/)
#     # can be reached. Then:
#     Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#     .\01-windows-bootstrap.ps1
#
# Optional switches:
#   -SkipPyWinPty     Don't install pywinpty (HTTP PTY won't work).
#   -SkipAgent        Skip wlb-agent install (SSH-only setup).
#   -AgentPort 8443   Override agent port (also opens the firewall).
#
# What it does NOT do:
#   - Install Python (you must have Python 3.11+ on PATH). Use
#       winget install Python.Python.3.12
#     before running this, if needed.
#   - Set up your SSH pubkey on the host — that's 02-linux-pair.sh.
#   - Generate TLS certs — see scripts/windows-agent/README.md step 4.

#Requires -RunAsAdministrator

[CmdletBinding()]
param(
    [switch]$SkipPyWinPty,
    [switch]$SkipAgent,
    [int]$AgentPort = 8443
)

$ErrorActionPreference = "Stop"

function Write-Step($m)  { Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan }
function Write-OK($m)    { Write-Host "[ OK ] $m" -ForegroundColor Green }
function Write-Warn2($m) { Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Write-Bad($m)   { Write-Host "[FAIL] $m" -ForegroundColor Red }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir
$SetupDir  = Join-Path $RepoRoot 'scripts\windows-setup'
$AgentDir  = Join-Path $RepoRoot 'scripts\windows-agent'

# ─── 0. Sanity ──────────────────────────────────────────────────────
Write-Step "Sanity-checking the layout"
foreach ($req in @($SetupDir, $AgentDir)) {
    if (-not (Test-Path $req)) {
        Write-Bad "Expected sibling directory not found: $req"
        Write-Bad "Copy the WHOLE repo (or at least scripts/ + walkthrough/) to Windows, not just this one file."
        exit 1
    }
}
Write-OK "Repo layout looks complete"

# ─── 1. OpenSSH Server ──────────────────────────────────────────────
Write-Step "Phase 1 — OpenSSH Server (via scripts/windows-setup/enable-openssh.ps1)"
$opensshScript = Join-Path $SetupDir 'enable-openssh.ps1'
if (-not (Test-Path $opensshScript)) {
    Write-Bad "enable-openssh.ps1 missing — repo copy incomplete."
    exit 1
}
& $opensshScript
Write-OK "OpenSSH Server phase complete"

# ─── 2. Python presence + version ───────────────────────────────────
Write-Step "Phase 2 — Python 3.11+ check"
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Bad "python.exe not on PATH. Install Python 3.11+ first:"
    Write-Host "    winget install Python.Python.3.12"
    Write-Host "  Then re-run this script."
    exit 1
}
$pyVer = & python -c "import sys; print('.'.join(str(p) for p in sys.version_info[:3]))"
$verParts = $pyVer.Split('.')
if (([int]$verParts[0] -lt 3) -or ([int]$verParts[0] -eq 3 -and [int]$verParts[1] -lt 11)) {
    Write-Bad "Python $pyVer is too old (need 3.11+)."
    exit 1
}
Write-OK "Python $pyVer at $($py.Source)"

# ─── 3. wlb-agent deps ──────────────────────────────────────────────
if (-not $SkipAgent) {
    Write-Step "Phase 3 — wlb-agent Python deps (fastapi + uvicorn[standard])"
    & python -m pip install --upgrade pip *> $null
    & python -m pip install --quiet "fastapi>=0.110" "uvicorn[standard]>=0.27"
    if ($LASTEXITCODE -ne 0) {
        Write-Bad "pip install failed. Check network / proxy settings."
        exit 1
    }
    Write-OK "fastapi + uvicorn[standard] installed"
} else {
    Write-Warn2 "Phase 3 skipped (-SkipAgent)"
}

# ─── 4. pywinpty (optional, for HTTP PTY) ───────────────────────────
if (-not $SkipPyWinPty) {
    Write-Step "Phase 4 — pywinpty (enables WS /v1/pty on the agent)"
    & python -m pip install --quiet "pywinpty>=2.0"
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 "pywinpty install failed. HTTP PTY won't work, but other paths still will."
    } else {
        Write-OK "pywinpty installed"
    }
} else {
    Write-Warn2 "Phase 4 skipped (-SkipPyWinPty); HTTP PTY will be unavailable"
}

# ─── 5. wlb-agent install ───────────────────────────────────────────
if (-not $SkipAgent) {
    Write-Step "Phase 5 — wlb-agent install (via scripts/windows-agent/install-agent.ps1)"
    $installAgent = Join-Path $AgentDir 'install-agent.ps1'
    if (-not (Test-Path $installAgent)) {
        Write-Bad "install-agent.ps1 missing — repo copy incomplete."
        exit 1
    }
    & $installAgent

    # The installer hardcodes 8443 in its firewall rule. If the user
    # picked a different port, swap the rule.
    if ($AgentPort -ne 8443) {
        Write-Step "Adjusting firewall rule to port $AgentPort"
        Remove-NetFirewallRule -Name 'wlb-agent' -ErrorAction SilentlyContinue
        New-NetFirewallRule `
            -Name 'wlb-agent' `
            -DisplayName "wlb-agent (port $AgentPort)" `
            -Enabled True `
            -Direction Inbound `
            -Protocol TCP `
            -LocalPort $AgentPort `
            -Action Allow | Out-Null
        Write-OK "Firewall rule re-pointed at TCP $AgentPort"
        Write-Warn2 "Also edit C:\ProgramData\wlb-agent\wlb-agent.toml so port = $AgentPort"
    }
    Write-OK "wlb-agent phase complete"
} else {
    Write-Warn2 "Phase 5 skipped (-SkipAgent)"
}

# ─── 6. Summary ─────────────────────────────────────────────────────
Write-Step "Bootstrap summary"

# Find the host's primary IPv4 so the operator can read it off.
$ip = (Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.IPAddress -notmatch '^169\.254\.|^127\.' -and $_.InterfaceAlias -notmatch '^(Loopback|vEthernet)' } |
       Select-Object -First 1).IPAddress

Write-Host ""
Write-Host "  Windows hostname : $env:COMPUTERNAME"
Write-Host "  Primary IPv4     : $ip"
Write-Host "  SSH user         : $env:USERNAME"
Write-Host "  SSH port         : 22  (firewall opened, sshd running)"
if (-not $SkipAgent) {
    Write-Host "  Agent port       : $AgentPort"
    Write-Host "  Agent dir        : C:\ProgramData\wlb-agent"
    Write-Host "  Agent token      : C:\ProgramData\wlb-agent\token  (mode-locked)"
}
Write-Host ""
Write-Host "  Next:"
Write-Host "    1. On the controller (Linux/macOS): cp walkthrough/local-notes.env.example"
Write-Host "       walkthrough/local-notes.env, fill in WIN_HOST=$ip etc."
Write-Host "    2. Run walkthrough/02-linux-pair.sh — it generates an SSH key and"
Write-Host "       prints the pubkey for you to paste into:"
Write-Host "         C:\Users\$env:USERNAME\.ssh\authorized_keys"
Write-Host "    3. Run walkthrough/03-smoke-tests.sh — scripted SSH smoke."
Write-Host "    4. For HTTP transport, copy C:\ProgramData\wlb-agent\token to the"
Write-Host "       controller at the path WLB_HTTP_TOKEN_FILE points to (chmod 600)."
Write-Host "       Then start the agent in a foreground window:"
Write-Host "         cd C:\ProgramData\wlb-agent"
Write-Host "         python .\wlb_agent.py --config .\wlb-agent.toml"
Write-Host ""
Write-OK "Bootstrap complete"
