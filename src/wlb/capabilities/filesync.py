"""filesync capability — push / pull files between controller and Windows host.

Sits on top of :meth:`Transport.push` / :meth:`Transport.pull` and wraps the
transport-level ShellResult into a domain-level ``Result[FileSyncOutput]``
that carries direction-aware metadata (local / remote / bytes / duration).

For SSH the underlying mechanism is asyncssh's SFTP client; for Local it's
``shutil``. The capability is transport-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from wlb.infra.result import Result, fail, ok
from wlb.transport.base import Transport

Direction = Literal["push", "pull"]


@dataclass(frozen=True)
class FileSyncOutput:
    local: str
    remote: str
    direction: Direction
    bytes_transferred: int
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "local": self.local,
            "remote": self.remote,
            "direction": self.direction,
            "bytes_transferred": self.bytes_transferred,
            "duration_ms": self.duration_ms,
        }


def _parse_bytes(stdout: str) -> int:
    """Best-effort: parse 'transferred N bytes (push)' from transport stdout."""
    if not stdout:
        return 0
    # Format defined in SshTransport._sftp_transfer / LocalTransport.push|pull.
    try:
        # "transferred 12345 bytes (push)"
        return int(stdout.split()[1])
    except (IndexError, ValueError):
        return 0


async def push(
    transport: Transport,
    local: Path | str,
    remote: str,
) -> Result[FileSyncOutput]:
    """Push ``local`` to ``remote`` on the Windows host.

    ``local`` may be a file or directory. ``remote`` is a Windows-side path
    (``C:\\stage\\fw.bin`` style).
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
    if not remote or not remote.strip():
        return fail(
            code="REMOTE_PATH_INVALID",
            message="remote path is empty",
            suggestion="Provide a non-empty Windows-side path (e.g. C:\\\\stage\\\\file.bin)",
            category="input",
        )

    if direction == "push":
        r = await transport.push(local, remote)
    else:
        r = await transport.pull(remote, local)

    if not r.ok:
        return fail(
            code=r.error_code or "SFTP_ERROR",
            message=(r.stderr or "transfer failed").strip(),
            suggestion=_suggest_for(r.error_code),
            category="io",
            details={
                "local": str(local),
                "remote": remote,
                "direction": direction,
                "stderr": r.stderr,
            },
            timing_ms=r.duration_ms,
        )

    return ok(
        data=FileSyncOutput(
            local=str(local),
            remote=remote,
            direction=direction,
            bytes_transferred=_parse_bytes(r.stdout),
            duration_ms=r.duration_ms,
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
