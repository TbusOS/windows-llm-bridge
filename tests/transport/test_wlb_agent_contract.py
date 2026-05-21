"""Contract tests — Linux-side HttpTransport against an in-process wlb_agent.

The agent script (``scripts/windows-agent/wlb_agent.py``) is a single file,
so we import it directly here and instantiate its FastAPI app for testing.
On non-Windows the agent's shell endpoint falls back to ``/bin/sh -c`` for
the ``cmd`` / ``raw`` interpreters, so we can still verify the round-trip
end-to-end on the CI Linux host.

This is the closest thing to an integration test we can run without a real
Windows machine — and it catches version drift between the controller and
the agent.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from wlb.transport.http import HttpTransport

_AGENT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts" / "windows-agent" / "wlb_agent.py"
)


@pytest.fixture(scope="module")
def agent_module() -> Any:
    """Import wlb_agent.py as a module so we can build its FastAPI app."""
    if not _AGENT_PATH.exists():
        pytest.skip(f"wlb_agent.py not present at {_AGENT_PATH}")
    spec = importlib.util.spec_from_file_location("wlb_agent_under_test", _AGENT_PATH)
    assert spec and spec.loader, "could not load wlb_agent.py"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)        # type: ignore[union-attr]
    except SystemExit as e:                    # missing fastapi/uvicorn
        pytest.skip(f"wlb_agent imports failed: {e}")
    return module


@pytest.fixture
def agent_app(agent_module: Any) -> Any:
    return agent_module.build_app("test-token-do-not-leak")


@pytest.fixture
def transport(agent_app: Any) -> HttpTransport:
    """HttpTransport plumbed straight into the agent's FastAPI app."""
    t = HttpTransport(
        base_url="http://agent.test",
        token="test-token-do-not-leak",
        connect_timeout=5,
    )

    def _patched() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=t.base_url or "",
            headers={"Authorization": f"Bearer {t._token() or ''}"},
            transport=httpx.ASGITransport(app=agent_app),
        )

    t._client = _patched   # type: ignore[method-assign]
    return t


# ─── contract: health ─────────────────────────────────────────────


async def test_health_round_trip(transport: HttpTransport) -> None:
    h = await transport.health()
    assert h["ok"] is True
    assert h["agent_version"]
    assert "platform" in h


async def test_health_rejects_bad_token(agent_app: Any) -> None:
    t = HttpTransport(base_url="http://agent.test", token="WRONG", connect_timeout=5)

    def _patched() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=t.base_url or "",
            headers={"Authorization": f"Bearer {t._token()}"},
            transport=httpx.ASGITransport(app=agent_app),
        )

    t._client = _patched   # type: ignore[method-assign]

    h = await t.health()
    assert h["ok"] is False
    assert h["error_code"] == "HTTP_AUTH_FAILED"


# ─── contract: shell ──────────────────────────────────────────────


async def test_shell_round_trip_echo(transport: HttpTransport) -> None:
    """On Linux the agent's cmd interpreter shells out to /bin/sh — fine for echo."""
    r = await transport.shell("echo wlb-http-roundtrip", interpreter="cmd")
    assert r.ok, r
    assert "wlb-http-roundtrip" in r.stdout


async def test_shell_nonzero_exit(transport: HttpTransport) -> None:
    r = await transport.shell("false", interpreter="cmd")
    assert not r.ok
    assert r.exit_code != 0
    assert r.error_code == "SHELL_NONZERO_EXIT"


async def test_shell_agent_deny_list_blocks_format(transport: HttpTransport) -> None:
    """Agent's own deny-list returns 403 even if the controller missed it."""
    r = await transport.shell("format c:", interpreter="cmd")
    assert not r.ok
    assert r.error_code == "PERMISSION_DENIED"


# ─── contract: file push / pull ──────────────────────────────────


async def test_push_then_pull_round_trip(transport: HttpTransport, tmp_path: Path) -> None:
    payload = b"contract-test-payload-" + b"x" * 100
    src = tmp_path / "src.bin"
    src.write_bytes(payload)

    remote = str(tmp_path / "remote_target.bin")
    push_r = await transport.push(src, remote)
    assert push_r.ok, push_r

    dst = tmp_path / "pulled.bin"
    pull_r = await transport.pull(remote, dst)
    assert pull_r.ok, pull_r
    assert dst.read_bytes() == payload


async def test_pull_missing_file_404(transport: HttpTransport, tmp_path: Path) -> None:
    r = await transport.pull("/no/such/file", tmp_path / "out.bin")
    assert not r.ok
    assert r.error_code == "FILE_NOT_FOUND"
