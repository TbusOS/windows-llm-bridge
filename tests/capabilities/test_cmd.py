"""cmd capability tests, parameterized over LocalTransport."""

from __future__ import annotations

from wlb.capabilities.cmd import execute as cmd_execute
from wlb.transport.local import LocalTransport


async def test_cmd_happy_path() -> None:
    transport = LocalTransport()
    r = await cmd_execute(transport, "echo wlb", timeout=10)
    assert r.ok, r
    assert r.data is not None
    assert "wlb" in r.data.stdout.lower()


async def test_cmd_permission_denied() -> None:
    transport = LocalTransport()
    r = await cmd_execute(transport, "format c:", timeout=10)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "PERMISSION_DENIED"
    assert r.error.category == "permission"


async def test_cmd_dict_serialization() -> None:
    transport = LocalTransport()
    r = await cmd_execute(transport, "echo serial", timeout=10)
    out = r.to_dict()
    # Standard Result shape
    assert set(out.keys()) == {"ok", "data", "error", "artifacts", "timing_ms"}
    assert out["ok"] is True
    assert out["error"] is None
