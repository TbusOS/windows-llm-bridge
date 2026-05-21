"""filesync SMB shortcut + path-translation integration tests.

Uses an SshTransport built around a mocked asyncssh connection so we can
detect whether SFTP fired (= shortcut missed) or didn't (= shortcut hit).
``tmp_path`` plays the role of both the Linux mount and the source/dest
files; ``C:\\share`` is the fake Windows path used in env config.

The ``_isolated_env`` autouse fixture redirects WLB_WORKSPACE / WLB_SMB_MAPS
into tmp_path so tests don't pollute each other.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from wlb.capabilities.filesync import pull as cap_pull
from wlb.capabilities.filesync import push as cap_push
from wlb.transport.ssh import SshTransport


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WLB_WORKSPACE", str(tmp_path))
    for k in (
        "WLB_PROFILE", "WLB_TRANSPORT", "WLB_SMB_MAPS",
        "WLB_SSH_HOST", "WLB_SSH_PORT", "WLB_SSH_USER",
        "WLB_SSH_KEY", "WLB_SSH_KNOWN_HOSTS", "WLB_SSH_TIMEOUT",
    ):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def _ssh_transport_with_blocking_sftp(monkeypatch: pytest.MonkeyPatch) -> tuple[SshTransport, MagicMock]:
    """Build an SshTransport whose SFTP would raise if accidentally invoked.

    Lets tests assert "the SMB shortcut handled this, SFTP wasn't touched."
    """
    sftp = MagicMock()
    sftp.put = AsyncMock(side_effect=AssertionError("SFTP should not have been called"))
    sftp.get = AsyncMock(side_effect=AssertionError("SFTP should not have been called"))
    sftp.stat = AsyncMock(side_effect=AssertionError("SFTP should not have been called"))
    sftp.__aenter__ = AsyncMock(return_value=sftp)
    sftp.__aexit__ = AsyncMock(return_value=None)

    conn = MagicMock()
    conn.start_sftp_client = AsyncMock(return_value=sftp)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()

    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))
    return SshTransport(host="win-host", user="admin"), sftp


# ─── push via SMB shortcut ───────────────────────────────────────


async def test_push_uses_smb_shortcut_when_mount_reachable(
    _isolated_env: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User gives a Windows-form path that maps to a reachable Linux mount → shortcut."""
    mount = _isolated_env / "win-share"
    mount.mkdir()
    monkeypatch.setenv("WLB_SMB_MAPS", f"{mount}=C:\\share")

    src = _isolated_env / "src" / "fw.bin"
    src.parent.mkdir()
    src.write_bytes(b"firmware-bytes")

    transport, sftp = _ssh_transport_with_blocking_sftp(monkeypatch)

    r = await cap_push(transport, src, "C:\\share\\out\\fw.bin")
    assert r.ok, r
    assert r.data is not None
    assert r.data.via == "smb"
    assert r.data.remote == "C:\\share\\out\\fw.bin"
    assert r.data.bytes_transferred == len(b"firmware-bytes")
    # SFTP must not have been touched.
    sftp.put.assert_not_called()
    # The actual file landed on the Linux mount.
    assert (mount / "out" / "fw.bin").read_bytes() == b"firmware-bytes"


async def test_push_accepts_linux_form_path_with_mapping(
    _isolated_env: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User writes /mnt/win-share/... — wlb translates to Windows form, uses shortcut."""
    mount = _isolated_env / "win-share"
    mount.mkdir()
    monkeypatch.setenv("WLB_SMB_MAPS", f"{mount}=C:\\share")

    src = _isolated_env / "x.bin"
    src.write_bytes(b"x")

    transport, _ = _ssh_transport_with_blocking_sftp(monkeypatch)

    r = await cap_push(transport, src, f"{mount}/out.bin")
    assert r.ok, r
    assert r.data is not None
    assert r.data.via == "smb"
    # Result reports Windows-form path, even though input was Linux-form.
    assert r.data.remote == "C:\\share\\out.bin"
    assert (mount / "out.bin").read_bytes() == b"x"


async def test_push_falls_back_to_sftp_when_mount_unreachable(
    _isolated_env: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SMB map points to a directory that doesn't exist → silent SFTP fallback."""
    # Configure a mount that is NOT actually present on this host.
    monkeypatch.setenv("WLB_SMB_MAPS", "/nonexistent/mount=C:\\share")

    src = _isolated_env / "x.bin"
    src.write_bytes(b"x")

    # SFTP must succeed for fallback to be observable.
    sftp_put_calls: list[tuple[str, str]] = []

    async def fake_put(local_str: str, remote: str, **kw: Any) -> None:
        sftp_put_calls.append((local_str, remote))

    sftp = MagicMock()
    sftp.put = AsyncMock(side_effect=fake_put)
    attrs = MagicMock(); attrs.size = 1; attrs.type = 1
    sftp.stat = AsyncMock(return_value=attrs)
    sftp.__aenter__ = AsyncMock(return_value=sftp)
    sftp.__aexit__ = AsyncMock(return_value=None)

    conn = MagicMock()
    conn.start_sftp_client = AsyncMock(return_value=sftp)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))

    transport = SshTransport(host="win-host", user="admin")
    r = await cap_push(transport, src, "C:\\share\\out.bin")
    assert r.ok, r
    assert r.data is not None
    assert r.data.via == "sftp"
    assert len(sftp_put_calls) == 1
    assert sftp_put_calls[0][1] == "C:\\share\\out.bin"


async def test_push_linux_form_without_map_rejected(
    _isolated_env: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Linux-form remote with no SMB map at all is a clear input error."""
    src = _isolated_env / "x.bin"
    src.write_bytes(b"x")
    transport, _ = _ssh_transport_with_blocking_sftp(monkeypatch)

    r = await cap_push(transport, src, "/var/log/elsewhere.bin")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "REMOTE_PATH_INVALID"


# ─── pull via SMB shortcut ───────────────────────────────────────


async def test_pull_uses_smb_shortcut_when_source_visible(
    _isolated_env: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    mount = _isolated_env / "win-share"
    mount.mkdir()
    (mount / "log.txt").write_text("captured by Windows\n", encoding="utf-8")
    monkeypatch.setenv("WLB_SMB_MAPS", f"{mount}=C:\\share")

    dst = _isolated_env / "pulled.txt"
    transport, sftp = _ssh_transport_with_blocking_sftp(monkeypatch)

    r = await cap_pull(transport, "C:\\share\\log.txt", dst)
    assert r.ok, r
    assert r.data is not None
    assert r.data.via == "smb"
    assert dst.read_text(encoding="utf-8") == "captured by Windows\n"
    sftp.get.assert_not_called()


async def test_pull_falls_back_to_sftp_when_source_missing_on_mount(
    _isolated_env: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mount is reachable but the specific file isn't visible on Linux side → SFTP."""
    mount = _isolated_env / "win-share"
    mount.mkdir()
    # Note: we do NOT create the file on the mount. Windows wrote it, but
    # for whatever reason Linux hasn't refreshed the share view.
    monkeypatch.setenv("WLB_SMB_MAPS", f"{mount}=C:\\share")

    dst = _isolated_env / "pulled.txt"

    sftp_get_calls: list[tuple[str, str]] = []

    async def fake_get(remote: str, local_str: str, **kw: Any) -> None:
        Path(local_str).write_text("via SFTP\n", encoding="utf-8")
        sftp_get_calls.append((remote, local_str))

    sftp = MagicMock()
    sftp.get = AsyncMock(side_effect=fake_get)
    attrs = MagicMock(); attrs.size = 1; attrs.type = 1
    sftp.stat = AsyncMock(return_value=attrs)
    sftp.__aenter__ = AsyncMock(return_value=sftp)
    sftp.__aexit__ = AsyncMock(return_value=None)

    conn = MagicMock()
    conn.start_sftp_client = AsyncMock(return_value=sftp)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))

    transport = SshTransport(host="win-host", user="admin")
    r = await cap_pull(transport, "C:\\share\\never-mind.txt", dst)
    assert r.ok, r
    assert r.data is not None
    assert r.data.via == "sftp"
    assert dst.read_text(encoding="utf-8") == "via SFTP\n"
    assert len(sftp_get_calls) == 1


# ─── output via field ────────────────────────────────────────────


async def test_local_transport_marks_via_local(_isolated_env: Path) -> None:
    """LocalTransport bypasses SMB logic entirely; via reports 'local'."""
    from wlb.transport.local import LocalTransport
    src = _isolated_env / "src.txt"
    src.write_text("hi")
    dst = _isolated_env / "dst.txt"

    r = await cap_push(LocalTransport(), src, str(dst))
    assert r.ok, r
    assert r.data is not None
    assert r.data.via == "local"


async def test_via_field_round_trips_to_dict() -> None:
    """to_dict() includes the via field — important for MCP clients."""
    from wlb.capabilities.filesync import FileSyncOutput
    out = FileSyncOutput(
        local="/a", remote="C:\\b", direction="push",
        bytes_transferred=123, duration_ms=4, via="smb",
    )
    assert out.to_dict()["via"] == "smb"
