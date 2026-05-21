"""filesync capability tests, parameterized over LocalTransport.

LocalTransport's push/pull are shutil-based, so these tests exercise the
full capability flow without touching asyncssh. SFTP-specific code paths
are covered by tests/transport/test_ssh_filesync.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wlb.capabilities.filesync import pull as cap_pull
from wlb.capabilities.filesync import push as cap_push
from wlb.transport.local import LocalTransport


async def test_push_file_happy_path(tmp_path: Path) -> None:
    src = tmp_path / "src.bin"
    src.write_bytes(b"hello-bytes")
    dst = tmp_path / "dst.bin"

    r = await cap_push(LocalTransport(), src, str(dst))

    assert r.ok, r
    assert r.data is not None
    assert r.data.direction == "push"
    assert r.data.bytes_transferred == len(b"hello-bytes")
    assert r.data.local == str(src)
    assert r.data.remote == str(dst)
    assert dst.read_bytes() == b"hello-bytes"


async def test_pull_file_happy_path(tmp_path: Path) -> None:
    src = tmp_path / "remote.txt"
    src.write_text("pulled\n", encoding="utf-8")
    dst = tmp_path / "captured.txt"

    r = await cap_pull(LocalTransport(), str(src), dst)

    assert r.ok, r
    assert r.data is not None
    assert r.data.direction == "pull"
    assert r.data.bytes_transferred == len("pulled\n")
    assert dst.read_text(encoding="utf-8") == "pulled\n"


async def test_push_directory_recurses(tmp_path: Path) -> None:
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("A")
    (src / "sub" / "b.txt").write_text("BB")

    dst = tmp_path / "copy"
    r = await cap_push(LocalTransport(), src, str(dst))

    assert r.ok, r
    assert (dst / "a.txt").read_text() == "A"
    assert (dst / "sub" / "b.txt").read_text() == "BB"
    assert r.data is not None
    assert r.data.bytes_transferred == 3  # "A" + "BB"


async def test_push_missing_local_maps_to_local_path_not_found(tmp_path: Path) -> None:
    r = await cap_push(LocalTransport(), tmp_path / "missing.bin", str(tmp_path / "out.bin"))
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "LOCAL_PATH_NOT_FOUND"


async def test_pull_missing_remote_maps_to_file_not_found(tmp_path: Path) -> None:
    r = await cap_pull(LocalTransport(), str(tmp_path / "no-such.txt"), tmp_path / "x.txt")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "FILE_NOT_FOUND"


async def test_push_empty_remote_rejected(tmp_path: Path) -> None:
    src = tmp_path / "f.bin"
    src.write_bytes(b"x")
    r = await cap_push(LocalTransport(), src, "")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "REMOTE_PATH_INVALID"


async def test_push_to_disallowed_dir_returns_structured(tmp_path: Path) -> None:
    """A push that fails on copy (e.g. permission, unwritable parent) is structured.

    We make the parent directory non-writable on Linux to provoke a copy error.
    """
    import os
    if os.name == "nt":
        pytest.skip("permission semantics differ on Windows; covered by integration test")

    src = tmp_path / "src.bin"
    src.write_bytes(b"x")
    forbidden_parent = tmp_path / "ro"
    forbidden_parent.mkdir()
    os.chmod(forbidden_parent, 0o500)   # read+exec only
    try:
        r = await cap_push(LocalTransport(), src, str(forbidden_parent / "out.bin"))
        assert not r.ok
        assert r.error is not None
        assert r.error.code == "REMOTE_PATH_INVALID"
    finally:
        os.chmod(forbidden_parent, 0o700)  # so pytest can clean up
