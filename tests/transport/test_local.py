"""LocalTransport sanity checks (cross-platform)."""

from __future__ import annotations

from wlb.transport.local import LocalTransport


async def test_local_echo_runs(local_transport: LocalTransport) -> None:
    r = await local_transport.shell("echo hello", interpreter="raw")
    assert r.ok, r
    assert "hello" in r.stdout.lower()
    assert r.exit_code == 0


async def test_local_nonzero_exit_propagates(local_transport: LocalTransport) -> None:
    # ``false`` exists on POSIX; on Windows we'd need a different test.
    # The LocalTransport's "non-windows" fallback uses /bin/sh, so this
    # works on Linux/macOS CI hosts.
    r = await local_transport.shell("false", interpreter="raw")
    assert not r.ok
    assert r.exit_code != 0
    assert r.error_code == "SHELL_NONZERO_EXIT"


async def test_local_health(local_transport: LocalTransport) -> None:
    h = await local_transport.health()
    assert h["transport"] == "local"
    assert h["ok"] is True
