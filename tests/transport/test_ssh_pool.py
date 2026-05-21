"""SSH connection-pool tests — exercise the pool module and pool-aware SshTransport.

All asyncssh I/O is mocked; no real network. The autouse ``_reset_ssh_pool``
fixture in ``tests/conftest.py`` clears the pool around every test, so the
fact that ``SshTransport`` stashes connections doesn't leak across cases.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncssh
import pytest

from wlb.transport import ssh_pool
from wlb.transport.ssh import SshTransport


def _fake_proc(*, stdout: str = "", stderr: str = "", exit_status: int = 0) -> Any:
    p = MagicMock()
    p.stdout = stdout
    p.stderr = stderr
    p.exit_status = exit_status
    p.returncode = exit_status
    return p


def _fake_conn(stdout: str = "ok") -> Any:
    c = MagicMock()
    c.run = AsyncMock(return_value=_fake_proc(stdout=stdout, exit_status=0))
    c.close = MagicMock()
    c.wait_closed = AsyncMock()
    return c


# ─── module API ──────────────────────────────────────────────────


async def test_acquire_dials_once_for_same_key() -> None:
    """Two acquires of the same key share one dial."""
    dialed = []

    async def dial() -> Any:
        dialed.append(object())
        return _fake_conn()

    key = ("h", 22, "u", None, None, 10)
    c1 = await ssh_pool.acquire(key, dial)
    c2 = await ssh_pool.acquire(key, dial)
    assert c1 is c2
    assert len(dialed) == 1
    assert ssh_pool.pool_size() == 1


async def test_acquire_dials_separately_for_distinct_keys() -> None:
    """Different keys → independent connections."""
    dial_counter = {"n": 0}

    async def dial() -> Any:
        dial_counter["n"] += 1
        return _fake_conn()

    k1 = ("host-a", 22, "u1", None, None, 10)
    k2 = ("host-b", 22, "u1", None, None, 10)
    c_a = await ssh_pool.acquire(k1, dial)
    c_b = await ssh_pool.acquire(k2, dial)
    assert c_a is not c_b
    assert dial_counter["n"] == 2
    assert ssh_pool.pool_size() == 2


async def test_mark_dead_triggers_redial() -> None:
    dialed = []

    async def dial() -> Any:
        c = _fake_conn(stdout=f"dial#{len(dialed) + 1}")
        dialed.append(c)
        return c

    key = ("h", 22, "u", None, None, 10)
    first = await ssh_pool.acquire(key, dial)
    ssh_pool.mark_dead(key)
    second = await ssh_pool.acquire(key, dial)
    assert first is not second
    assert len(dialed) == 2


async def test_mark_dead_on_unknown_key_is_noop() -> None:
    ssh_pool.mark_dead(("never-seen", 22, None, None, None, 10))   # no raise


async def test_close_all_closes_pooled_conns_and_clears() -> None:
    async def dial() -> Any:
        return _fake_conn()

    await ssh_pool.acquire(("a", 22, "u", None, None, 10), dial)
    await ssh_pool.acquire(("b", 22, "u", None, None, 10), dial)
    assert ssh_pool.pool_size() == 2
    # Snapshot the conns before close_all empties the dict.
    keys_before = ssh_pool.keys()
    assert len(keys_before) == 2

    await ssh_pool.close_all()

    assert ssh_pool.pool_size() == 0


async def test_acquire_does_not_pool_failed_dials() -> None:
    """An exception in dial_fn must not leave a phantom entry."""
    async def boom() -> Any:
        raise asyncssh.PermissionDenied("nope", "en")

    key = ("h", 22, "u", None, None, 10)
    with pytest.raises(asyncssh.PermissionDenied):
        await ssh_pool.acquire(key, boom)
    assert ssh_pool.pool_size() == 0


# ─── pool integration with SshTransport ──────────────────────────


async def test_transport_reuses_conn_across_shell_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two ``shell()`` calls on identical transports dial only once."""
    dialed = []

    async def fake_connect(*a: Any, **kw: Any) -> Any:
        dialed.append(object())
        return _fake_conn(stdout="hello")

    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", fake_connect)

    t1 = SshTransport(host="win-host", user="admin")
    r1 = await t1.shell("ver", interpreter="cmd")
    assert r1.ok, r1

    t2 = SshTransport(host="win-host", user="admin")  # identical params
    r2 = await t2.shell("hostname", interpreter="cmd")
    assert r2.ok, r2

    # Only one dial despite two SshTransport instances + two shell calls.
    assert len(dialed) == 1, f"expected 1 dial, got {len(dialed)}"
    assert ssh_pool.pool_size() == 1


async def test_transport_different_user_separate_conn(monkeypatch: pytest.MonkeyPatch) -> None:
    dialed = []

    async def fake_connect(*a: Any, **kw: Any) -> Any:
        dialed.append(kw.get("username"))
        return _fake_conn(stdout="ok")

    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", fake_connect)

    await SshTransport(host="win-host", user="alice").shell("ver", interpreter="cmd")
    await SshTransport(host="win-host", user="bob").shell("ver", interpreter="cmd")

    assert dialed == ["alice", "bob"]
    assert ssh_pool.pool_size() == 2


async def test_transport_connection_lost_marks_pool_dead_and_redials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First call dies mid-run → next call redials."""
    dialed = []

    def make_conn(stdout: str, run_side_effect: Any = None) -> Any:
        c = MagicMock()
        if run_side_effect is not None:
            c.run = AsyncMock(side_effect=run_side_effect)
        else:
            c.run = AsyncMock(return_value=_fake_proc(stdout=stdout, exit_status=0))
        c.close = MagicMock()
        c.wait_closed = AsyncMock()
        return c

    async def lost(*a: Any, **kw: Any) -> Any:
        raise asyncssh.ConnectionLost("connection lost")

    conns = [make_conn("never-reached", run_side_effect=lost), make_conn("fresh-ok")]

    async def fake_connect(*a: Any, **kw: Any) -> Any:
        dialed.append(object())
        return conns.pop(0)

    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", fake_connect)

    t = SshTransport(host="win-host", user="admin")
    r1 = await t.shell("ver", interpreter="cmd")
    assert not r1.ok
    assert r1.error_code == "SSH_CONNECTION_LOST"

    # Pool still has the dead entry, but next acquire must redial.
    r2 = await t.shell("hostname", interpreter="cmd")
    assert r2.ok, r2
    assert "fresh-ok" in r2.stdout
    assert len(dialed) == 2
