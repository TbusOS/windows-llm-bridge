# enable-openssh.ps1
#
# Enable and harden Windows OpenSSH Server for use as a wlb transport target.
# Run on the Windows host as an Administrator (right-click PowerShell → Run as administrator).
#
# What it does:
#   1. Installs the Windows OpenSSH Server optional feature (if not present).
#   2. Starts the sshd service and sets it to auto-start.
#   3. Opens the firewall rule for inbound TCP 22.
#   4. (Default) Sets PubkeyAuthentication=yes, PasswordAuthentication=no, leaves PermitRootLogin alone.
#   5. Prints the next steps for getting your wlb controller's pubkey installed.
#
# What it does NOT do:
#   - Set up the actual keypair. You generate that on the controller side (ssh-keygen).
#   - Modify sshd_config beyond auth method toggles.
#   - Open any port other than 22.
#
# Re-run is safe — it's idempotent.

#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

function Write-Step($msg) {
    Write-Host "==> $msg" -ForegroundColor Cyan
}
function Write-OK($msg) {
    Write-Host "[ OK ] $msg" -ForegroundColor Green
}
function Write-Warn2($msg) {
    Write-Host "[WARN] $msg" -ForegroundColor Yellow
}

# ─── 1. Install OpenSSH Server feature ─────────────────────────────
Write-Step "Checking OpenSSH Server feature"
$cap = Get-WindowsCapability -Online | Where-Object Name -Like 'OpenSSH.Server*'
if (-not $cap) {
    throw "OpenSSH Server capability not found. Is this Windows 10 1809+ / Windows 11 / Server 2019+?"
}
if ($cap.State -ne 'Installed') {
    Write-Step "Installing OpenSSH.Server..."
    Add-WindowsCapability -Online -Name $cap.Name | Out-Null
    Write-OK "OpenSSH.Server installed"
} else {
    Write-OK "OpenSSH.Server already installed"
}

# ─── 2. Start the service ──────────────────────────────────────────
Write-Step "Starting sshd service"
Set-Service -Name sshd -StartupType Automatic
Start-Service -Name sshd
Write-OK "sshd is running (auto-start enabled)"

# ─── 3. Firewall ───────────────────────────────────────────────────
Write-Step "Ensuring inbound firewall rule for TCP 22"
$rule = Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue
if (-not $rule) {
    New-NetFirewallRule `
        -Name "OpenSSH-Server-In-TCP" `
        -DisplayName "OpenSSH Server (sshd)" `
        -Enabled True `
        -Direction Inbound `
        -Protocol TCP `
        -LocalPort 22 `
        -Action Allow | Out-Null
    Write-OK "Firewall rule created for TCP 22"
} else {
    if (-not $rule.Enabled) {
        Set-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -Enabled True
        Write-OK "Firewall rule re-enabled"
    } else {
        Write-OK "Firewall rule already present and enabled"
    }
}

# ─── 4. Auth method preferences ────────────────────────────────────
$cfg = "$env:ProgramData\ssh\sshd_config"
if (Test-Path $cfg) {
    Write-Step "Tightening $cfg (PubkeyAuthentication=yes, PasswordAuthentication=no)"
    $content = Get-Content $cfg -Raw

    function Set-OrAppend([ref]$text, $key, $value) {
        if ($text.Value -match "(?m)^[#\s]*$key\s+\S+") {
            $text.Value = $text.Value -replace "(?m)^[#\s]*$key\s+\S+", "$key $value"
        } else {
            $text.Value += "`n$key $value`n"
        }
    }
    Set-OrAppend ([ref]$content) "PubkeyAuthentication" "yes"
    Set-OrAppend ([ref]$content) "PasswordAuthentication" "no"
    Set-Content -Path $cfg -Value $content -Encoding ASCII
    Restart-Service sshd
    Write-OK "Auth methods updated; sshd restarted"
} else {
    Write-Warn2 "sshd_config not found at $cfg — skipping auth tightening"
}

# ─── 5. Next steps ─────────────────────────────────────────────────
$user = $env:USERNAME
$adminGroupKeys = "$env:ProgramData\ssh\administrators_authorized_keys"
$userKeys = "$env:USERPROFILE\.ssh\authorized_keys"

Write-Host ""
Write-Step "Next steps"
Write-Host ""
Write-Host "  On your wlb controller host (Linux / macOS) generate a key if you don't have one:"
Write-Host "    ssh-keygen -t ed25519 -f ~/.ssh/wlb_ed25519 -C 'wlb'"
Write-Host ""
Write-Host "  Copy the resulting ~/.ssh/wlb_ed25519.pub to ONE of these locations on this Windows host:"
Write-Host "    • $userKeys                     (for the '$user' user only — recommended)"
Write-Host "    • $adminGroupKeys  (for ANY Administrator — needed if '$user' is in the Administrators group AND you want elevated SSH)"
Write-Host ""
Write-Host "  Verify from the controller:"
Write-Host "    ssh -i ~/.ssh/wlb_ed25519 $user@$env:COMPUTERNAME 'ver'"
Write-Host ""
Write-OK "OpenSSH Server is ready. See docs/windows-side-setup.md for hardening tips."
