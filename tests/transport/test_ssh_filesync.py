"""SshTransport push / pull unit tests — asyncssh SFTP is mocked.

The pool is cleared between tests by the autouse fixture in conftest.
Real-host SFTP round-trips live in test_ssh_integration.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncssh
import pytest

from wlb.transport.ssh import SshTransport


def _fake_attrs(*, size: int = 0, is_dir: bool = False) -> Any:
    a = MagicMock()
    a.size = size
    a.type = 2 if is_dir else 1   # 2 = SFTP_TYPE_DIR, 1 = SFTP_TYPE_FILE (see ssh.py)
    return a


def _fake_sftp(*, put_side_effect: Any = None, get_side_effect: Any = None,
               stat_attrs: Any = None) -> Any:
    """Mock an SFTPClient that's also an async context manager."""
    sftp = MagicMock()
    sftp.put = AsyncMock(side_effect=put_side_effect)
    sftp.get = AsyncMock(side_effect=get_side_effect)
    sftp.stat = AsyncMock(return_value=stat_attrs or _fake_attrs(size=0))
    sftp.__aenter__ = AsyncMock(return_value=sftp)
    sftp.__aexit__ = AsyncMock(return_value=None)
    return sftp


def _fake_conn_with_sftp(sftp: Any) -> Any:
    c = MagicMock()
    # start_sftp_client is async and returns the SFTPClient (which is itself the CM).
    c.start_sftp_client = AsyncMock(return_value=sftp)
    c.close = MagicMock()
    c.wait_closed = AsyncMock()
    return c


# ─── push ─────────────────────────────────────────────────────────


async def test_ssh_push_file_calls_sftp_put(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "fw.bin"
    src.write_bytes(b"firmware-payload")

    sftp = _fake_sftp(stat_attrs=_fake_attrs(size=len(b"firmware-payload")))
    conn = _fake_conn_with_sftp(sftp)
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))

    t = SshTransport(host="win-host", user="admin")
    r = await t.push(src, "C:\\stage\\fw.bin")

    assert r.ok, r
    sftp.put.assert_awaited_once()
    args, kwargs = sftp.put.await_args
    assert args[0] == str(src)
    assert args[1] == "C:\\stage\\fw.bin"
    assert kwargs.get("recurse") is False   # file, not dir
    assert "transferred" in r.stdout
    assert r.artifacts == [src]


async def test_ssh_push_directory_sets_recurse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tree"
    src.mkdir()
    (src / "f.txt").write_text("hi")

    sftp = _fake_sftp(stat_attrs=_fake_attrs(size=0, is_dir=True))
    conn = _fake_conn_with_sftp(sftp)
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))

    t = SshTransport(host="win-host", user="admin")
    r = await t.push(src, "C:\\stage\\tree")

    assert r.ok, r
    _, kwargs = sftp.put.await_args
    assert kwargs.get("recurse") is True


async def test_ssh_push_missing_local_fast_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # asyncssh.connect must not even be called.
    connect = AsyncMock()
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", connect)

    t = SshTransport(host="win-host", user="admin")
    r = await t.push(tmp_path / "nope.bin", "C:\\stage\\nope.bin")

    assert not r.ok
    assert r.error_code == "LOCAL_PATH_NOT_FOUND"
    connect.assert_not_called()


async def test_ssh_push_sftp_no_such_path_maps_to_remote_path_invalid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    src = tmp_path / "x.bin"
    src.write_bytes(b"x")

    async def boom(*a: Any, **kw: Any) -> None:
        raise asyncssh.SFTPNoSuchFile("parent dir does not exist")

    sftp = _fake_sftp(put_side_effect=boom)
    conn = _fake_conn_with_sftp(sftp)
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))

    t = SshTransport(host="win-host", user="admin")
    r = await t.push(src, "C:\\nope\\x.bin")
    assert not r.ok
    assert r.error_code == "REMOTE_PATH_INVALID"


async def test_ssh_push_permission_denied(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "x.bin"
    src.write_bytes(b"x")

    async def boom(*a: Any, **kw: Any) -> None:
        raise asyncssh.SFTPPermissionDenied("write denied")

    sftp = _fake_sftp(put_side_effect=boom)
    conn = _fake_conn_with_sftp(sftp)
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))

    t = SshTransport(host="win-host", user="admin")
    r = await t.push(src, "C:\\Windows\\x.bin")
    assert not r.ok
    assert r.error_code == "REMOTE_PATH_INVALID"


async def test_ssh_push_sftp_generic_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "x.bin"
    src.write_bytes(b"x")

    async def boom(*a: Any, **kw: Any) -> None:
        raise asyncssh.SFTPError(4, "Failure", "en")

    sftp = _fake_sftp(put_side_effect=boom)
    conn = _fake_conn_with_sftp(sftp)
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))

    t = SshTransport(host="win-host", user="admin")
    r = await t.push(src, "C:\\stage\\x.bin")
    assert not r.ok
    assert r.error_code == "SFTP_ERROR"


# ─── pull ─────────────────────────────────────────────────────────


async def test_ssh_pull_file_calls_sftp_get(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dst = tmp_path / "captured.log"

    # When get() runs we want to also create the destination on the local
    # filesystem so the post-transfer size check returns a sensible number.
    async def fake_get(remote: str, local: str, **kw: Any) -> None:
        Path(local).write_bytes(b"log-contents")

    sftp = _fake_sftp(get_side_effect=fake_get, stat_attrs=_fake_attrs(size=12, is_dir=False))
    conn = _fake_conn_with_sftp(sftp)
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))

    t = SshTransport(host="win-host", user="admin")
    r = await t.pull("C:\\logs\\flash.log", dst)

    assert r.ok, r
    sftp.get.assert_awaited_once()
    args, kwargs = sftp.get.await_args
    assert args[0] == "C:\\logs\\flash.log"
    assert args[1] == str(dst)
    assert kwargs.get("recurse") is False
    assert dst.read_bytes() == b"log-contents"


async def test_ssh_pull_directory_recurses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dst = tmp_path / "pulled"

    async def fake_get(remote: str, local: str, **kw: Any) -> None:
        Path(local).mkdir(parents=True, exist_ok=True)
        (Path(local) / "a.txt").write_text("A")

    sftp = _fake_sftp(get_side_effect=fake_get, stat_attrs=_fake_attrs(is_dir=True))
    conn = _fake_conn_with_sftp(sftp)
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))

    t = SshTransport(host="win-host", user="admin")
    r = await t.pull("C:\\stage\\tree", dst)

    assert r.ok, r
    _, kwargs = sftp.get.await_args
    assert kwargs.get("recurse") is True
    assert (dst / "a.txt").read_text() == "A"


async def test_ssh_pull_remote_missing_maps_to_file_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    async def boom(*a: Any, **kw: Any) -> Any:
        raise asyncssh.SFTPNoSuchFile("no such file")

    sftp = MagicMock()
    sftp.stat = AsyncMock(side_effect=boom)
    sftp.get = AsyncMock()
    sftp.__aenter__ = AsyncMock(return_value=sftp)
    sftp.__aexit__ = AsyncMock(return_value=None)
    conn = _fake_conn_with_sftp(sftp)
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))

    t = SshTransport(host="win-host", user="admin")
    r = await t.pull("C:\\missing.txt", tmp_path / "out.txt")
    assert not r.ok
    assert r.error_code == "FILE_NOT_FOUND"


async def test_ssh_sftp_subsystem_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ChannelOpenError from start_sftp_client → SFTP_NOT_AVAILABLE."""
    src = tmp_path / "x.bin"
    src.write_bytes(b"x")

    async def boom(*a: Any, **kw: Any) -> Any:
        raise asyncssh.ChannelOpenError(7, "subsystem request failed")

    conn = MagicMock()
    conn.start_sftp_client = AsyncMock(side_effect=boom)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))

    t = SshTransport(host="win-host", user="admin")
    r = await t.push(src, "C:\\stage\\x.bin")
    assert not r.ok
    assert r.error_code == "SFTP_NOT_AVAILABLE"
