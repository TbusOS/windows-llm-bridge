"""End-to-end WS contract tests for ``wlb-agent`` ``/v1/pty`` (M3.6).

Spins up the agent's FastAPI app under uvicorn on a free localhost port,
then drives :class:`HttpPtySession` against it. This is the closest thing
to a full integration test we can run on a Linux box — the agent's PTY
backend on non-Windows uses ``pty.openpty()`` + ``/bin/sh -i`` so we can
verify the full WebSocket round-trip (handshake, bytes, resize, exit)
without a Windows machine.
"""

from __future__ import annotations

import asyncio
import importlib.util
import socket
import sys
from pathlib import Path
from typing import Any

import pytest
import uvicorn
import websockets

from wlb.transport.http import HttpTransport


pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="agent PTY backend on Windows needs pywinpty — covered by walkthrough.",
)

_AGENT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts" / "windows-agent" / "wlb_agent.py"
)
_TOKEN = "test-token-do-not-leak"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def agent_module() -> Any:
    if not _AGENT_PATH.exists():
        pytest.skip(f"wlb_agent.py not present at {_AGENT_PATH}")
    spec = importlib.util.spec_from_file_location("wlb_agent_for_pty", _AGENT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)            # type: ignore[union-attr]
    except SystemExit as e:
        pytest.skip(f"wlb_agent imports failed: {e}")
    return module


class _LiveAgent:
    """uvicorn running the agent app on a free localhost port."""

    def __init__(self, app: Any, port: int) -> None:
        self.app = app
        self.port = port
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        config = uvicorn.Config(
            self.app, host="127.0.0.1", port=self.port,
            log_level="error", lifespan="off",
        )
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())
        # Wait until the server is actually accepting connections.
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                    return
            except OSError:
                await asyncio.sleep(0.05)
        raise RuntimeError(f"uvicorn never came up on port {self.port}")

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=3)
            except asyncio.TimeoutError:
                self._task.cancel()


@pytest.fixture
async def live_agent(agent_module: Any):
    app = agent_module.build_app(_TOKEN)
    port = _free_port()
    live = _LiveAgent(app, port)
    await live.start()
    try:
        yield live
    finally:
        await live.stop()


def _http_transport(port: int, *, token: str = _TOKEN) -> HttpTransport:
    return HttpTransport(
        base_url=f"http://127.0.0.1:{port}",
        token=token,
        connect_timeout=3,
        verify_tls=False,
    )


async def _drain_until(session: Any, needle: bytes, *, timeout: float = 3.0) -> bytes:
    """Read from the session until ``needle`` shows up or ``timeout`` elapses."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    buf = bytearray()
    while loop.time() < deadline:
        try:
            chunk = await asyncio.wait_for(session.read(4096), timeout=0.4)
        except asyncio.TimeoutError:
            continue
        if not chunk:
            break
        buf.extend(chunk)
        if needle in buf:
            break
    return bytes(buf)


# ─── contract: handshake + round-trip ────────────────────────────


async def test_open_pty_handshake_to_real_agent(live_agent: _LiveAgent) -> None:
    t = _http_transport(live_agent.port)
    session = await t.open_pty(interpreter="raw", cols=80, rows=24)
    try:
        # /bin/sh prints something quickly even without a write — but just in
        # case it's quiet, send an echo so we can observe the round-trip.
        await session.write(b"echo wlb-http-pty-roundtrip\n")
        out = await _drain_until(session, b"wlb-http-pty-roundtrip")
        assert b"wlb-http-pty-roundtrip" in out
    finally:
        await session.close()


async def test_resize_succeeds_against_real_agent(live_agent: _LiveAgent) -> None:
    t = _http_transport(live_agent.port)
    session = await t.open_pty()
    try:
        # Resize before any reads — agent should accept the control frame
        # without spawning errors. If it crashes, the next read will hit EOF
        # immediately, which the assertion catches.
        await session.resize(132, 50)
        await session.write(b"echo resized\n")
        out = await _drain_until(session, b"resized", timeout=2)
        assert b"resized" in out
    finally:
        await session.close()


async def test_exit_message_propagates_exit_code(live_agent: _LiveAgent) -> None:
    t = _http_transport(live_agent.port)
    session = await t.open_pty()
    try:
        # Make sh exit with a known code; drain everything until exit message
        # closes the read side.
        await session.write(b"exit 7\n")
        # Read until EOF
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 3
        while loop.time() < deadline:
            chunk = await asyncio.wait_for(session.read(4096), timeout=0.4)
            if not chunk:
                break
        code = await asyncio.wait_for(session.wait(), timeout=2)
        # Unix sh exit status 7
        assert code == 7
    finally:
        await session.close()


# ─── contract: auth + protocol violations ─────────────────────────


async def test_handshake_rejected_with_bad_token(live_agent: _LiveAgent) -> None:
    t = _http_transport(live_agent.port, token="not-the-right-token")
    with pytest.raises(ConnectionError):
        await t.open_pty()


async def test_handshake_rejected_without_auth_header(live_agent: _LiveAgent) -> None:
    # Raw websockets client without our Bearer header so we exercise the
    # agent's pre-accept rejection path directly.
    url = f"ws://127.0.0.1:{live_agent.port}/v1/pty"
    with pytest.raises((websockets.InvalidStatus, websockets.ConnectionClosed)):
        async with websockets.connect(url, open_timeout=2):
            pass


async def test_first_frame_not_start_returns_error(live_agent: _LiveAgent) -> None:
    """Sending text that isn't a start message gets an error JSON back."""
    url = f"ws://127.0.0.1:{live_agent.port}/v1/pty"
    headers = [("Authorization", f"Bearer {_TOKEN}")]
    async with websockets.connect(url, additional_headers=headers, open_timeout=2) as ws:
        await ws.send('{"type":"hello"}')
        first = await asyncio.wait_for(ws.recv(), timeout=2)
        import json
        payload = json.loads(first)
        assert payload["type"] == "error"
        assert payload["code"] == "BAD_FIRST_FRAME"


async def test_bad_interpreter_returns_error(live_agent: _LiveAgent) -> None:
    url = f"ws://127.0.0.1:{live_agent.port}/v1/pty"
    headers = [("Authorization", f"Bearer {_TOKEN}")]
    async with websockets.connect(url, additional_headers=headers, open_timeout=2) as ws:
        import json
        await ws.send(json.dumps({"type": "start", "interpreter": "bogus"}))
        first = await asyncio.wait_for(ws.recv(), timeout=2)
        payload = json.loads(first)
        assert payload["type"] == "error"
        assert payload["code"] == "BAD_INTERPRETER"
