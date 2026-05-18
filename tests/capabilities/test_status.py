"""status capability tests."""

from __future__ import annotations

from wlb.capabilities.status import describe, status
from wlb.transport.local import LocalTransport


async def test_describe_lists_transports_and_capabilities() -> None:
    r = await describe()
    assert r.ok
    data = r.data
    assert data is not None
    assert "transports" in data
    assert "capabilities" in data
    assert len(data["transports"]) > 0
    assert len(data["capabilities"]) > 0


async def test_status_returns_transport_health() -> None:
    transport = LocalTransport()
    r = await status(transport)
    assert r.ok
    assert r.data is not None
    assert r.data.transport == "local"
    assert r.data.health.get("ok") is True
