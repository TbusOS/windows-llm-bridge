# Windows-side setup

Everything that needs to happen on the Windows machine for wlb to talk to it.

> The `scripts/windows-setup/enable-openssh.ps1` script automates the
> common case. This document explains what it does and how to do it
> by hand if you need more control.

---

## Requirements

- Windows 10 version 1809 or later, Windows 11, or Windows Server 2019+.
  These ship the OpenSSH Server as an optional feature.
- Administrator rights for the one-time install.
- Network reachability from the controller host to the Windows host on
  TCP 22 (or another port if you change `Port` in `sshd_config`).

---

## Option A: run the helper script

From an elevated PowerShell on the Windows host:

```powershell
.\enable-openssh.ps1
```

This handles steps 1–4 below. Re-running is safe (idempotent).

---

## Option B: do it by hand

### 1. Install the OpenSSH Server feature

```powershell
Get-WindowsCapability -Online | Where-Object Name -Like 'OpenSSH.Server*'
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
```

If the second line says "already installed", you're fine.

### 2. Start and enable the sshd service

```powershell
Set-Service -Name sshd -StartupType Automatic
Start-Service -Name sshd
Get-Service sshd     # should report "Running"
```

### 3. Open the firewall

```powershell
New-NetFirewallRule `
    -Name "OpenSSH-Server-In-TCP" `
    -DisplayName "OpenSSH Server (sshd)" `
    -Enabled True `
    -Direction Inbound `
    -Protocol TCP `
    -LocalPort 22 `
    -Action Allow
```

### 4. Harden `sshd_config`

Edit `C:\ProgramData\ssh\sshd_config`. Recommended minimum:

```
PubkeyAuthentication yes
PasswordAuthentication no
PermitEmptyPasswords no
PermitRootLogin no
StrictModes yes
```

Restart the service after editing:

```powershell
Restart-Service sshd
```

---

## 5. Install your public key

On the **controller** host:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/wlb_ed25519 -C 'wlb'
cat ~/.ssh/wlb_ed25519.pub
```

Copy the printed line to **one** of these on the Windows side:

### For a regular user

`C:\Users\<your-user>\.ssh\authorized_keys`

Make sure the file has the right ACLs (the OpenSSH config defaults reject
keys in world-readable files). The simplest fix:

```powershell
$file = "$env:USERPROFILE\.ssh\authorized_keys"
icacls $file /inheritance:r
icacls $file /grant:r "${env:USERNAME}:F"
icacls $file /grant:r "SYSTEM:F"
```

### For Administrators

If the user is a member of the local **Administrators** group, the
authorized_keys file is **special**: it lives at
`C:\ProgramData\ssh\administrators_authorized_keys` instead. The
per-user file is ignored for admin accounts. Verify:

```powershell
$file = "$env:ProgramData\ssh\administrators_authorized_keys"
Add-Content $file (Get-Content path\to\wlb_ed25519.pub)
icacls $file /inheritance:r /grant:r "Administrators:F" "SYSTEM:F"
```

---

## 6. Verify from the controller

```bash
ssh -i ~/.ssh/wlb_ed25519 <your-user>@<win-host> 'ver'
```

You should get something like `Microsoft Windows [Version 10.0.19045.xxxx]`.

If you get `Permission denied (publickey)`:

- Check `Get-Service sshd` is `Running` on the Windows side.
- Check the ACL on the `authorized_keys` file (see above).
- Run `ssh -vvv` on the controller side for verbose debug.
- On the Windows side, look at the sshd event log:
  `Get-WinEvent -LogName 'OpenSSH/Operational' -MaxEvents 50`.

---

## Hardening checklist

For production / shared-network use, also do:

- [ ] Disable `PasswordAuthentication` (the script does this for you).
- [ ] Change the SSH port from 22 to something non-default. Edit
      `Port 22` in `sshd_config`, then update the firewall rule and
      `WLB_SSH_PORT` on the controller.
- [ ] Set `LoginGraceTime 30`, `MaxAuthTries 3`, `ClientAliveInterval 60`.
- [ ] Add an `AllowUsers <your-user>` line so only the wlb account can
      log in via SSH.
- [ ] Rotate the keypair quarterly. wlb writes `.env` not `~/.ssh/config`,
      so rotation is a single file edit.
- [ ] Audit `Get-WinEvent -LogName 'OpenSSH/Operational'` periodically.

---

## Default shell

By default, Windows OpenSSH starts `cmd.exe` as the login shell. wlb's
`cmd` capability assumes this and prefixes `cmd /c` itself, so changing
the default shell is not required.

If you want PowerShell as the default shell anyway (so that a plain
`ssh user@host` lands in PowerShell), set the registry value:

```powershell
New-ItemProperty `
    -Path "HKLM:\SOFTWARE\OpenSSH" `
    -Name DefaultShell `
    -Value (Get-Command pwsh.exe).Source `
    -PropertyType String -Force
```

wlb's `powershell` capability invokes the interpreter explicitly so this
setting doesn't affect it either way.

---

## Multiple targets

You can register multiple Windows hosts by switching `WLB_SSH_HOST` per
invocation, or — once M1 lands — via `wlb setup ssh --profile <name>`
which writes per-host TOML profiles under `workspace/profiles/`.

---

## What about WinRM, WSMan, RDP?

Not used. wlb is OpenSSH-first because:

- OpenSSH is built into modern Windows (no extra install on the controller side).
- Key auth is the standard; secrets stay on the controller machine.
- The same transport object can do shell **and** SFTP, which matters for M2.

WinRM and RDP are explicitly out of scope (see REQUIREMENTS §5).
