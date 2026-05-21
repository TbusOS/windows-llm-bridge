"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from wlb.transport import ssh_pool
from wlb.transport.local import LocalTransport


@pytest.fixture(autouse=True)
def _reset_ssh_pool() -> object:
    """Clear the SSH connection pool before AND after every test.

    The pool is module-level state; without this fixture, a connection that
    one test mocked into the pool would survive into later tests and either
    leak or contaminate assertions. ``clear()`` is sync and doesn't try to
    ``wait_closed()`` mock connections.
    """
    ssh_pool.clear()
    yield
    ssh_pool.clear()


@pytest.fixture
def local_transport() -> LocalTransport:
    """A LocalTransport instance for hermetic capability tests."""
    return LocalTransport()
