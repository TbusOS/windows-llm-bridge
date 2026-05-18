"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from wlb.transport.local import LocalTransport


@pytest.fixture
def local_transport() -> LocalTransport:
    """A LocalTransport instance for hermetic capability tests."""
    return LocalTransport()
