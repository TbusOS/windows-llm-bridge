"""LocalTransport.open_pty — real PTY round-trip tests (Unix only)."""

from __future__ import annotations

import asyncio
import sys

import pytest

from wlb.transport.local import LocalTransport


pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="LocalTransport PTY requires Unix pty.openpty (ConPTY support is M3.4.1)",
)


async def _drain(session, total_timeout: float = 1.0, chunk_timeout: float = 0.3) -> bytes:
    """Collect bytes until no new ones for chunk_timeout seconds or total_timeout overall."""
    loop = asyncio.get_event_loop()
    started = loop.time()
    buf = bytearray()
    while loop.time() - started < total_timeout:
        try:
            chunk = await asyncio.wait_for(session.read(4096), timeout=chunk_timeout)
        except asyncio.TimeoutError:
            break
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


def test_supports_pty_is_true_on_unix() -> None:
    t = LocalTransport()
    assert t.supports_pty is True


async def test_pty_round_trip_echo() -> None:
    t = LocalTransport()
    session = await t.open_pty(interpreter="raw", cols=80, rows=24)
    try:
        # Drain the initial banner / prompt
        await _drain(session, total_timeout=0.4)
        await session.write(b"echo hello-pty-roundtrip\n")
        out = await _drain(session, total_timeout=1.0)
        assert b"hello-pty-roundtrip" in out
    finally:
        await session.close()
        await session.wait()


async def test_pty_resize_no_crash() -> None:
    t = LocalTransport()
    session = await t.open_pty(interpreter="raw", cols=80, rows=24)
    try:
        await _drain(session, total_timeout=0.3)
        await session.resize(120, 40)
        # Resize must not crash; the shell sees SIGWINCH but doesn't reply.
        await session.write(b"echo after-resize\n")
        out = await _drain(session, total_timeout=0.8)
        assert b"after-resize" in out
    finally:
        await session.close()
        await session.wait()


async def test_pty_close_terminates_shell() -> None:
    t = LocalTransport()
    session = await t.open_pty(interpreter="raw")
    try:
        await _drain(session, total_timeout=0.3)
    finally:
        await session.close()
    exit_code = await session.wait()
    # Either signal-terminated (negative) or 0 (clean exit), but not None.
    assert exit_code is not None


async def test_pty_read_after_close_returns_empty() -> None:
    t = LocalTransport()
    session = await t.open_pty(interpreter="raw")
    await _drain(session, total_timeout=0.3)
    await session.close()
    chunk = await session.read(4096)
    assert chunk == b""


async def test_pty_write_after_close_no_raise() -> None:
    t = LocalTransport()
    session = await t.open_pty(interpreter="raw")
    await session.close()
    # Should swallow OSError silently.
    await session.write(b"this should be a no-op\n")
