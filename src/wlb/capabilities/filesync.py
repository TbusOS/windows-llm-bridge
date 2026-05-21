"""filesync capability — push / pull files between controller and Windows host.

Sits on top of :meth:`Transport.push` / :meth:`Transport.pull` and wraps the
transport-level ShellResult into a domain-level ``Result[FileSyncOutput]``
that carries direction-aware metadata (local / remote / bytes / duration).

For SSH the underlying mechanism is asyncssh's SFTP client; for Local it's
``shutil``. The capability is transport-agnostic.

SMB shortcut (M2.2)
-------------------
When a Samba/SMB share is mounted on the Linux side and points at a
directory the Windows side sees as a drive path, wlb skips SFTP and just
copies locally on the Linux side. The Windows side sees the result
through the share. Configuration is via ``WLB_SMB_MAPS`` env or the
profile's ``[[smb_maps]]`` array; see :mod:`wlb.infra.smb_maps`.

The capability also accepts a Linux-form ``remote`` (e.g.
``/mnt/win-share/x.bin``) when it falls under a configured SMB mount —
it translates to the Windows form for the result payload so the LLM
gets back a path that makes sense on the Windows side.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from wlb.infra.config import load_active
from wlb.infra.result import Result, fail, ok
from wlb.infra.smb_maps import (
    SmbMap,
    looks_like_linux_path,
    translate_linux_to_windows,
    translate_windows_to_linux,
)
from wlb.transport.base import Transport

Direction = Literal["push", "pull"]
Via = Literal["sftp", "smb", "local"]


@dataclass(frozen=True)
class FileSyncOutput:
    local: str
    remote: str                # always reported in Windows form when an SMB map matches
    direction: Direction
    bytes_transferred: int
    duration_ms: int
    via: Via = "sftp"          # "smb" when the SMB shortcut fired; "local" for LocalTransport

    def to_dict(self) -> dict[str, Any]:
        return {
            "local": self.local,
            "remote": self.remote,
            "direction": self.direction,
            "bytes_transferred": self.bytes_transferred,
            "duration_ms": self.duration_ms,
            "via": self.via,
        }


def _parse_bytes(stdout: str) -> int:
    """Best-effort: parse 'transferred N bytes (push)' from transport stdout."""
    if not stdout:
        return 0
    try:
        return int(stdout.split()[1])
    except (IndexError, ValueError):
        return 0


def _path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _resolve_remote(
    remote: str, maps: list[SmbMap]
) -> tuple[str | None, str | None, SmbMap | None, str | None]:
    """Resolve ``remote`` to (windows_form, linux_mount_path, matched_map, err).

    Behavior:
    - Linux-form input (``/...``) that falls under a configured SMB mount →
      ``(windows_form, original_linux_path, matched_map, None)``.
    - Linux-form input that does NOT fall under any SMB mount → error.
    - Windows-form input under an SMB-mapped path →
      ``(windows_form, linux_mount_path, matched_map, None)``.
    - Windows-form input with no map → ``(windows_form, None, None, None)``.

    ``matched_map`` lets the caller check whether the mount root itself
    is present (the destination subdir doesn't have to exist yet).
    """
    if not remote or not remote.strip():
        return None, None, None, "remote path is empty"

    remote = remote.strip()

    if looks_like_linux_path(remote):
        for m in maps:
            if remote.rstrip("/") == m.linux_mount or remote.startswith(m.linux_mount + "/"):
                windows = translate_linux_to_windows(remote, [m])
                return windows, remote.rstrip("/"), m, None
        return (
            None,
            None,
            None,
            f"remote path {remote!r} is Linux-form but no SMB map covers it. "
            f"Configure WLB_SMB_MAPS or use a Windows-form path (C:\\...).",
        )

    # Windows-form path. May or may not have an SMB mapping.
    for m in maps:
        linux = translate_windows_to_linux(remote, [m])
        if linux is not None:
            return remote, linux, m, None
    return remote, None, None, None


# ─── SMB shortcut implementations ────────────────────────────────


def _smb_push(local: Path, linux_mount_path: str, mount_root: str) -> tuple[bool, int, str]:
    """shutil-copy ``local`` into the SMB-mounted ``linux_mount_path``.

    ``mount_root`` is the configured Linux mount point itself; we use it to
    decide whether the mount is actually present (any subdirectories under
    it will be created by ``mkdir(parents=True)``).

    Returns ``(ok, bytes_transferred, message)``. ``ok=False`` means the
    shortcut wasn't usable and the caller should fall back to SFTP.
    """
    if not Path(mount_root).exists():
        return False, 0, f"mount {mount_root} not present on this host — falling back"
    dst = Path(linux_mount_path)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if local.is_dir():
            shutil.copytree(local, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(local, dst)
    except OSError as e:
        return False, 0, f"SMB copy failed: {e}"
    return True, _path_size(dst), "ok"


def _smb_pull(linux_mount_path: str, local: Path, mount_root: str) -> tuple[bool, int, str]:
    """shutil-copy from the SMB mount to ``local``.

    Returns ``(ok, bytes_transferred, message)``. ``ok=False`` when either
    the mount isn't present or the specific source file isn't visible
    (caller falls back to SFTP).
    """
    if not Path(mount_root).exists():
        return False, 0, f"mount {mount_root} not present on this host — falling back"
    src = Path(linux_mount_path)
    if not src.exists():
        return False, 0, f"source {src} not visible on local mount — falling back"
    try:
        local.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, local, dirs_exist_ok=True)
        else:
            shutil.copy2(src, local)
    except OSError as e:
        return False, 0, f"SMB copy failed: {e}"
    return True, _path_size(local), "ok"


# ─── Public API ──────────────────────────────────────────────────


async def push(
    transport: Transport,
    local: Path | str,
    remote: str,
) -> Result[FileSyncOutput]:
    """Push ``local`` to ``remote`` on the Windows host.

    ``local`` may be a file or directory. ``remote`` may be a Windows-form
    path (``C:\\stage\\fw.bin``) or a Linux-form path under a configured
    SMB mount (``/mnt/win-share/fw.bin``). When the destination is covered
    by an SMB map and the Linux mount is reachable, wlb skips SFTP and
    copies via the share.
    """
    local_path = Path(local).expanduser()
    return await _run(transport, local_path, remote, direction="push")


async def pull(
    transport: Transport,
    remote: str,
    local: Path | str,
) -> Result[FileSyncOutput]:
    """Pull ``remote`` from the Windows host to ``local`` on the controller."""
    local_path = Path(local).expanduser()
    return await _run(transport, local_path, remote, direction="pull")


async def _run(
    transport: Transport,
    local: Path,
    remote: str,
    *,
    direction: Direction,
) -> Result[FileSyncOutput]:
    # LocalTransport is loopback — paths are host-native, no SMB games apply.
    # Skip SMB resolution entirely; just require a non-empty remote.
    if transport.name == "local":
        if not remote or not remote.strip():
            return fail(
                code="REMOTE_PATH_INVALID",
                message="remote path is empty",
                suggestion="Provide a non-empty path",
                category="input",
                details={"remote": remote, "direction": direction},
            )
        windows_form: str | None = remote
        linux_mount_path: str | None = None
        matched_map: SmbMap | None = None
    else:
        settings = load_active()
        windows_form, linux_mount_path, matched_map, err = _resolve_remote(
            remote, settings.smb_maps
        )
        if windows_form is None:
            return fail(
                code="REMOTE_PATH_INVALID",
                message=err or "remote path is invalid",
                suggestion=(
                    "Use a Windows-form path (C:\\\\...) or configure WLB_SMB_MAPS / "
                    "the profile's [[smb_maps]] section so a Linux mount path is recognized."
                ),
                category="input",
                details={"remote": remote, "direction": direction},
            )

    # ── SMB shortcut path ───────────────────────────────────────
    if linux_mount_path is not None and matched_map is not None:
        started = time.monotonic()
        if direction == "push":
            if not local.exists():
                return fail(
                    code="LOCAL_PATH_NOT_FOUND",
                    message=f"local path not found: {local}",
                    suggestion="Check the path; create the parent directory if needed",
                    category="io",
                    details={"local": str(local), "remote": windows_form},
                )
            ok_short, n_bytes, _msg = _smb_push(local, linux_mount_path, matched_map.linux_mount)
        else:
            ok_short, n_bytes, _msg = _smb_pull(linux_mount_path, local, matched_map.linux_mount)
        duration_ms = int((time.monotonic() - started) * 1000)
        if ok_short:
            return ok(
                data=FileSyncOutput(
                    local=str(local),
                    remote=windows_form,
                    direction=direction,
                    bytes_transferred=n_bytes,
                    duration_ms=duration_ms,
                    via="smb",
                ),
                artifacts=[local],
                timing_ms=duration_ms,
            )
        # SMB shortcut not viable — silently fall back to the transport.

    # ── Transport path (SFTP for ssh, shutil for local) ─────────
    if direction == "push":
        r = await transport.push(local, windows_form)
    else:
        r = await transport.pull(windows_form, local)

    if not r.ok:
        return fail(
            code=r.error_code or "SFTP_ERROR",
            message=(r.stderr or "transfer failed").strip(),
            suggestion=_suggest_for(r.error_code),
            category="io",
            details={
                "local": str(local),
                "remote": windows_form,
                "direction": direction,
                "stderr": r.stderr,
            },
            timing_ms=r.duration_ms,
        )

    via: Via = "local" if transport.name == "local" else "sftp"
    return ok(
        data=FileSyncOutput(
            local=str(local),
            remote=windows_form,
            direction=direction,
            bytes_transferred=_parse_bytes(r.stdout),
            duration_ms=r.duration_ms,
            via=via,
        ),
        artifacts=list(r.artifacts),
        timing_ms=r.duration_ms,
    )


def _suggest_for(code: str | None) -> str:
    mapping = {
        "LOCAL_PATH_NOT_FOUND": "Check the local path; create the parent directory if needed",
        "FILE_NOT_FOUND": "Check the remote path on the Windows side; it must exist for pull",
        "REMOTE_PATH_INVALID": "Inspect error.details.stderr; check the SSH user can write there",
        "SFTP_ERROR": "Inspect error.details.stderr for the remote-side message",
        "SFTP_NOT_AVAILABLE": (
            "On the Windows side, ensure the sftp subsystem is enabled in "
            "C:\\\\ProgramData\\\\ssh\\\\sshd_config (default for Windows OpenSSH)"
        ),
        "TRANSPORT_NOT_CONFIGURED": "Run: wlb setup ssh (or set WLB_SSH_HOST in .env)",
        "SSH_AUTH_FAILED": "Check key permissions and authorized_keys on the Windows side",
        "SSH_HOST_UNREACHABLE": "Confirm sshd is running on the Windows host: Get-Service sshd",
        "SSH_CONNECTION_LOST": "Retry; if persistent, check Get-WinEvent -LogName OpenSSH/Operational",
    }
    return mapping.get(code or "", "See docs/errors.md for details")
