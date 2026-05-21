"""Connection pool for :class:`wlb.transport.ssh.SshTransport`.

Why pool at all
---------------
Every ``SshTransport.shell()`` call previously dialed a fresh SSH connection
and closed it after the run — one TCP + SSH handshake (~100-500 ms) per
command. For a long-lived MCP server doing 10+ tool calls in a row, that
adds up. The pool amortizes the handshake: one open connection per
``(host, port, user, key_path, known_hosts, connect_timeout)`` tuple is
reused across calls, until either the remote end closes it or the process
ends.

Design notes
------------
- Keyed by a tuple of every parameter that affects connection identity.
  Two transports with identical params share one connection; one with a
  different user gets a separate connection.
- One :class:`asyncio.Lock` per key, lazily created. Concurrent acquires
  for the same key serialize through the lock so only one dials. Different
  keys can dial in parallel.
- asyncssh's :class:`SSHClientConnection.run` opens a fresh SSH channel per
  call, so one pooled connection safely services many concurrent runs.
- No background idle reaper. The trade-off: idle connections may sit
  consuming a socket until the remote side reaps them. Callers that see
  ``asyncssh.ConnectionLost`` mid-operation call :func:`mark_dead`, which
  evicts the entry on the next acquire.
- :func:`clear` is sync and forgets all entries without closing them —
  used by tests to reset between cases without needing an event loop.
- :func:`close_all` is async, closes every entry gracefully — used by the
  CLI on shutdown.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Hashable

PoolKey = tuple[Hashable, ...]
DialFn = Callable[[], Awaitable[Any]]


@dataclass
class _PooledEntry:
    conn: Any
    last_used: float
    dead: bool = False


_pool: dict[PoolKey, _PooledEntry] = {}
_locks: dict[PoolKey, asyncio.Lock] = {}


def _get_lock(key: PoolKey) -> asyncio.Lock:
    """Lazily create a per-key lock.

    Plain dict access is fine here: the only race is two coroutines both
    constructing a Lock at the same key; one wins and writes to the dict,
    the loser's Lock is GCed. Both end up using the winning Lock through
    subsequent ``_locks[key]`` reads. Worst case: one extra Lock allocation
    once per key.
    """
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


async def acquire(key: PoolKey, dial_fn: DialFn) -> Any:
    """Return a healthy pooled connection for ``key``; dial via ``dial_fn`` otherwise.

    ``dial_fn`` is an ``async`` callable returning a freshly-opened
    connection. It runs while the per-key lock is held, so two concurrent
    acquires of the same key won't both dial — the second waits on the
    lock and finds the first acquire's connection in the dict.

    Any exception raised by ``dial_fn`` propagates to the caller; nothing
    is added to the pool when a dial fails.
    """
    lock = _get_lock(key)
    async with lock:
        entry = _pool.get(key)
        if entry is not None and not entry.dead:
            entry.last_used = time.monotonic()
            return entry.conn
        if entry is not None:
            # Dead entry — drop it. Caller already saw the disconnect; we
            # don't try to wait_closed() here because the conn is already
            # gone or wedged.
            _pool.pop(key, None)
        conn = await dial_fn()
        _pool[key] = _PooledEntry(conn=conn, last_used=time.monotonic())
        return conn


def mark_dead(key: PoolKey) -> None:
    """Mark the pooled entry for ``key`` as dead.

    Call this when a ``run()`` on the connection raised something like
    :class:`asyncssh.ConnectionLost`. The entry stays in the dict (so the
    error path is sync), but the next :func:`acquire` will evict and
    redial.
    """
    entry = _pool.get(key)
    if entry is not None:
        entry.dead = True


def pool_size() -> int:
    """Number of entries currently cached (incl. dead). For diagnostics / tests."""
    return len(_pool)


def keys() -> list[PoolKey]:
    """Snapshot of pool keys. For tests."""
    return list(_pool.keys())


def clear() -> None:
    """Forget every entry WITHOUT closing the underlying connections.

    Sync; safe to call from anywhere (no event loop required). Tests use
    this to reset between cases. Production code should prefer
    :func:`close_all`, which closes connections gracefully.
    """
    _pool.clear()
    _locks.clear()


async def close_all() -> None:
    """Close every pooled connection and clear the pool.

    Used by the CLI's :func:`wlb.cli.common.run_async` wrapper so each
    ``uv run wlb …`` invocation exits with a clean pool. The MCP server
    does not call this — its pool lives for the lifetime of the server
    process.
    """
    entries = list(_pool.values())
    _pool.clear()
    _locks.clear()
    for e in entries:
        try:
            e.conn.close()
            wc = getattr(e.conn, "wait_closed", None)
            if wc is not None:
                await wc()
        except Exception:  # noqa: BLE001 — shutdown must not raise
            pass
