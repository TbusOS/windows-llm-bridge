"""asciinema .cast recording — unit + LocalPtySession integration (M3.7)."""

from __future__ import annotations

import asyncio
import itertools
import json
import sys
from pathlib import Path

import pytest

from wlb.capabilities.pty_recorder import (
    CastRecorder,
    RecordingPtySession,
    cast_path_for,
    maybe_wrap,
)
from wlb.infra.config import PtyRecordSettings
from wlb.transport.base import PtySession


# ─── CastRecorder: header + event encoding ───────────────────────


def _read_cast(path: Path) -> tuple[dict, list[list]]:
    """Split a cast file into (header, [event, event, ...])."""
    lines = path.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    events = [json.loads(line) for line in lines[1:] if line]
    return header, events


def _fake_clock() -> object:
    """Monotonic clock that advances 0.5s each call. Deterministic for tests."""
    counter = itertools.count()
    return lambda: next(counter) * 0.5


async def test_recorder_writes_v2_header(tmp_path: Path) -> None:
    path = tmp_path / "session.cast"
    rec = CastRecorder(path, cols=100, rows=30, title="probe", env={"TERM": "xterm"})
    await rec.close()
    header, events = _read_cast(path)
    assert header["version"] == 2
    assert header["width"] == 100
    assert header["height"] == 30
    assert header["title"] == "probe"
    assert header["env"] == {"TERM": "xterm"}
    assert "timestamp" in header
    assert events == []


async def test_recorder_writes_output_events_with_relative_timestamps(tmp_path: Path) -> None:
    path = tmp_path / "out.cast"
    rec = CastRecorder(path, cols=80, rows=24, clock=_fake_clock())
    await rec.write_output(b"hello\n")
    await rec.write_output(b"world\n")
    await rec.close()
    header, events = _read_cast(path)
    assert header["version"] == 2
    assert events == [[0.0, "o", "hello\n"], [0.5, "o", "world\n"]]


async def test_recorder_input_events_recorded_only_when_requested(tmp_path: Path) -> None:
    path = tmp_path / "in.cast"
    rec = CastRecorder(path, cols=80, rows=24, clock=_fake_clock())
    await rec.write_input(b"echo hi\n")
    await rec.write_output(b"hi\n")
    await rec.close()
    _, events = _read_cast(path)
    assert events == [[0.0, "i", "echo hi\n"], [0.5, "o", "hi\n"]]


async def test_recorder_decodes_utf8_with_replace(tmp_path: Path) -> None:
    path = tmp_path / "utf8.cast"
    rec = CastRecorder(path, cols=80, rows=24, clock=_fake_clock())
    # 0xff is invalid as a leading UTF-8 byte → replacement char.
    await rec.write_output(b"\xff-bad")
    await rec.close()
    _, events = _read_cast(path)
    assert events[0][2] == "�-bad"


async def test_recorder_close_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "close.cast"
    rec = CastRecorder(path, cols=80, rows=24)
    await rec.close()
    await rec.close()                          # must not raise
    # Header line still readable.
    header, _ = _read_cast(path)
    assert header["version"] == 2


async def test_recorder_drops_empty_writes(tmp_path: Path) -> None:
    path = tmp_path / "empty.cast"
    rec = CastRecorder(path, cols=80, rows=24, clock=_fake_clock())
    await rec.write_output(b"")
    await rec.write_input(b"")
    await rec.write_output(b"actual")
    await rec.close()
    _, events = _read_cast(path)
    assert len(events) == 1
    assert events[0][1] == "o"
    assert events[0][2] == "actual"


# ─── RecordingPtySession: transparent passthrough + mirroring ───


class _FakeInner(PtySession):
    """Scriptable inner PtySession for wrapper tests."""

    def __init__(self, *, reads: list[bytes], exit_code: int = 0) -> None:
        self.reads = list(reads)
        self.writes: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []
        self.closed = False
        self.exit_code = exit_code

    async def read(self, n: int = 4096) -> bytes:
        if not self.reads:
            return b""
        return self.reads.pop(0)

    async def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))

    async def resize(self, cols: int, rows: int) -> None:
        self.resizes.append((cols, rows))

    async def wait(self) -> int:
        return self.exit_code

    async def close(self) -> None:
        self.closed = True


async def test_wrapper_records_reads_and_passes_them_through(tmp_path: Path) -> None:
    path = tmp_path / "wrap.cast"
    inner = _FakeInner(reads=[b"alpha", b"beta", b""])
    rec = CastRecorder(path, cols=80, rows=24, clock=_fake_clock())
    sess = RecordingPtySession(inner, rec)
    assert await sess.read() == b"alpha"
    assert await sess.read() == b"beta"
    assert await sess.read() == b""
    await sess.close()
    _, events = _read_cast(path)
    payloads = [e[2] for e in events if e[1] == "o"]
    assert payloads == ["alpha", "beta"]


async def test_wrapper_passes_writes_through_and_records_only_when_enabled(
    tmp_path: Path,
) -> None:
    # record_input=False: writes flow through but cast contains no "i".
    path = tmp_path / "no-input.cast"
    inner = _FakeInner(reads=[])
    rec = CastRecorder(path, cols=80, rows=24, clock=_fake_clock())
    sess = RecordingPtySession(inner, rec, record_input=False)
    await sess.write(b"ls\n")
    await sess.close()
    assert inner.writes == [b"ls\n"]
    _, events = _read_cast(path)
    assert events == []

    # record_input=True: writes appear as "i" events.
    path2 = tmp_path / "with-input.cast"
    inner2 = _FakeInner(reads=[])
    rec2 = CastRecorder(path2, cols=80, rows=24, clock=_fake_clock())
    sess2 = RecordingPtySession(inner2, rec2, record_input=True)
    await sess2.write(b"ls\n")
    await sess2.close()
    assert inner2.writes == [b"ls\n"]
    _, events = _read_cast(path2)
    assert [e[1:] for e in events] == [["i", "ls\n"]]


async def test_wrapper_close_closes_inner_and_recorder(tmp_path: Path) -> None:
    path = tmp_path / "close-both.cast"
    inner = _FakeInner(reads=[])
    rec = CastRecorder(path, cols=80, rows=24)
    sess = RecordingPtySession(inner, rec)
    await sess.close()
    await sess.close()                         # idempotent
    assert inner.closed is True


async def test_wrapper_resize_delegates_to_inner(tmp_path: Path) -> None:
    path = tmp_path / "resize.cast"
    inner = _FakeInner(reads=[])
    rec = CastRecorder(path, cols=80, rows=24)
    sess = RecordingPtySession(inner, rec)
    await sess.resize(132, 50)
    await sess.close()
    assert inner.resizes == [(132, 50)]


async def test_wrapper_wait_returns_inner_exit_code(tmp_path: Path) -> None:
    path = tmp_path / "wait.cast"
    inner = _FakeInner(reads=[], exit_code=42)
    rec = CastRecorder(path, cols=80, rows=24)
    sess = RecordingPtySession(inner, rec)
    code = await sess.wait()
    await sess.close()
    assert code == 42


# ─── cast_path_for: workspace convention + override ──────────────


def test_cast_path_default_uses_workspace_hosts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WLB_WORKSPACE", str(tmp_path))
    p = cast_path_for(host="win-host.example", interpreter="cmd")
    assert tmp_path / "hosts" / "win-host.example" / "pty" in p.parents
    assert p.name.endswith("-cmd.cast")


def test_cast_path_override_dir_skips_workspace(tmp_path: Path) -> None:
    target = tmp_path / "casts-here"
    p = cast_path_for(host="anything", interpreter="powershell",
                      override_dir=str(target))
    assert p.parent == target
    assert p.name.endswith("-powershell.cast")


def test_cast_path_invalid_host_falls_back_to_unknown(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WLB_WORKSPACE", str(tmp_path))
    # ".." would normally be rejected by is_safe_host → fall back to "unknown".
    p = cast_path_for(host="../escape", interpreter="cmd")
    assert tmp_path / "hosts" / "unknown" / "pty" in p.parents


def test_cast_path_unknown_interpreter_normalized(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WLB_WORKSPACE", str(tmp_path))
    p = cast_path_for(host="x", interpreter="bogus")
    assert p.name.endswith("-raw.cast")


# ─── maybe_wrap: gating + transparent passthrough ────────────────


async def test_maybe_wrap_returns_input_when_disabled(tmp_path: Path) -> None:
    inner = _FakeInner(reads=[])
    same = maybe_wrap(
        inner, PtyRecordSettings(enabled=False),
        host="x", cols=80, rows=24, interpreter="cmd", term_type="xterm",
    )
    assert same is inner

    same2 = maybe_wrap(
        inner, None,
        host="x", cols=80, rows=24, interpreter="cmd", term_type="xterm",
    )
    assert same2 is inner


async def test_maybe_wrap_wraps_when_enabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WLB_WORKSPACE", str(tmp_path))
    inner = _FakeInner(reads=[b"banner"])
    wrapped = maybe_wrap(
        inner,
        PtyRecordSettings(enabled=True, record_input=True),
        host="probe", cols=80, rows=24,
        interpreter="raw", term_type="xterm-256color",
    )
    assert wrapped is not inner
    assert isinstance(wrapped, RecordingPtySession)
    await wrapped.read()                        # drives one output event
    await wrapped.write(b"input!")
    await wrapped.close()
    cast = wrapped.cast_path
    assert cast.exists()
    header, events = _read_cast(cast)
    assert header["version"] == 2
    assert {e[1] for e in events} == {"o", "i"}


async def test_maybe_wrap_honors_override_dir(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "custom-casts"
    inner = _FakeInner(reads=[])
    wrapped = maybe_wrap(
        inner,
        PtyRecordSettings(enabled=True, dir=str(target)),
        host="probe", cols=80, rows=24,
        interpreter="cmd", term_type="xterm",
    )
    assert isinstance(wrapped, RecordingPtySession)
    await wrapped.close()
    assert wrapped.cast_path.parent == target


# ─── End-to-end: record a real LocalPtySession (Unix only) ──────


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="real LocalPtySession round-trip needs Unix pty.openpty",
)
async def test_end_to_end_records_real_local_pty(tmp_path: Path, monkeypatch) -> None:
    from wlb.transport.local import LocalTransport

    monkeypatch.setenv("WLB_WORKSPACE", str(tmp_path))
    t = LocalTransport()
    inner = await t.open_pty(interpreter="raw", cols=80, rows=24)
    sess = maybe_wrap(
        inner,
        PtyRecordSettings(enabled=True),
        host=t.host_label,
        cols=80, rows=24, interpreter="raw", term_type="xterm-256color",
    )
    try:
        # Drain initial banner / prompt (sh -i may take a moment under load).
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 1.5
        while loop.time() < deadline:
            try:
                chunk = await asyncio.wait_for(sess.read(4096), timeout=0.3)
                if not chunk:
                    break
            except asyncio.TimeoutError:
                break
        await sess.write(b"echo hello-cast-roundtrip\n")
        # Read until needle appears — generous deadline so test doesn't flake
        # when CI is under load.
        deadline = loop.time() + 5.0
        buf = bytearray()
        while loop.time() < deadline:
            try:
                chunk = await asyncio.wait_for(sess.read(4096), timeout=0.4)
            except asyncio.TimeoutError:
                continue
            if not chunk:
                break
            buf.extend(chunk)
            if b"hello-cast-roundtrip" in buf:
                break
        assert b"hello-cast-roundtrip" in buf, (
            "shell never echoed needle within 5s — buf was: "
            + buf.decode("utf-8", errors="replace")[-200:]
        )
    finally:
        await sess.close()
        await sess.wait()

    assert isinstance(sess, RecordingPtySession)
    cast = sess.cast_path
    assert cast.exists()
    assert tmp_path / "hosts" / "local" / "pty" in cast.parents
    header, events = _read_cast(cast)
    assert header["version"] == 2
    assert header["width"] == 80
    assert header["height"] == 24
    # We drove one round-trip; expect at least one "o" event containing the echo.
    text = "".join(e[2] for e in events if e[1] == "o")
    assert "hello-cast-roundtrip" in text
