"""HttpTransport.open_pty + HttpPtySession — WebSocket client coverage (M3.6).

These tests spin up a tiny ``websockets.serve()`` mock that speaks the same
wire protocol as ``wlb-agent``'s ``/v1/pty``. Keeps focus on the controller
side: handshake, protocol violations, bidirectional pumping, resize, exit,
auth handling. End-to-end against the real agent process is covered by the
contract test (``test_wlb_agent_pty_contract``).
"""

from __future__ import annotations

import asyncio
import json
import socket
from typing import Any, Awaitable, Callable

import pytest
import websockets

from wlb.transport.base import PtySession
from wlb.transport.http import HttpPtySession, HttpTransport


# ─── server fixture helpers ──────────────────────────────────────


def _free_port() -> int:
    """Pick an unused localhost port; small race window is fine for tests."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _serve(
    handler: Callable[[Any], Awaitable[None]],
    *,
    require_auth: bool = True,
    token: str = "test-token",
) -> tuple[Any, int]:
    """Start a ``websockets.serve()`` on a free port; return (server, port).

    ``handler(ws)`` runs per connection. If ``require_auth`` is True the
    server rejects handshakes without ``Authorization: Bearer <token>``.
    """
    port = _free_port()

    async def _entry(ws: Any) -> None:
        if require_auth:
            # websockets exposes request headers on ws.request.headers (server side).
            req_headers = getattr(ws.request, "headers", {})
            auth = req_headers.get("Authorization", "") if hasattr(req_headers, "get") else ""
            if not auth.lower().startswith("bearer ") or auth[len("Bearer "):].strip() != token:
                await ws.close(code=1008, reason="auth")
                return
        await handler(ws)

    # websockets.serve accepts process_request to deny handshake earlier, but
    # the auth check inside the handler is simpler and matches our scenarios.
    server = await websockets.serve(_entry, "127.0.0.1", port)
    return server, port


def _transport(port: int, *, token: str = "test-token") -> HttpTransport:
    return HttpTransport(
        base_url=f"http://127.0.0.1:{port}",
        token=token,
        connect_timeout=2,
        verify_tls=False,
    )


# ─── happy path: handshake → bytes → exit ────────────────────────


async def test_open_pty_returns_session_after_started_handshake() -> None:
    seen: dict[str, Any] = {}

    async def handler(ws: Any) -> None:
        first = await ws.recv()
        seen["first"] = json.loads(first)
        await ws.send(json.dumps({"type": "started", "pid": 12345}))
        # Hold the connection so close() runs cleanly.
        try:
            await ws.recv()
        except websockets.ConnectionClosed:
            return

    server, port = await _serve(handler)
    try:
        t = _transport(port)
        session = await t.open_pty(interpreter="cmd", cols=120, rows=40)
        try:
            assert isinstance(session, PtySession)
            assert isinstance(session, HttpPtySession)
            assert seen["first"] == {
                "type": "start", "interpreter": "cmd",
                "cols": 120, "rows": 40, "term_type": "xterm-256color",
            }
        finally:
            await session.close()
    finally:
        server.close()
        await server.wait_closed()


async def test_supports_pty_flag_is_true() -> None:
    t = _transport(0)
    assert t.supports_pty is True


async def test_read_returns_binary_chunks_from_server() -> None:
    async def handler(ws: Any) -> None:
        await ws.recv()
        await ws.send(json.dumps({"type": "started", "pid": 1}))
        await ws.send(b"hello-from-agent")
        await ws.send(b"-more-bytes")
        # Close cleanly so the read loop sees EOF.
        await ws.send(json.dumps({"type": "exit", "exit_code": 0}))

    server, port = await _serve(handler)
    try:
        t = _transport(port)
        session = await t.open_pty(cols=80, rows=24)
        try:
            chunk1 = await asyncio.wait_for(session.read(64), timeout=2)
            chunk2 = await asyncio.wait_for(session.read(64), timeout=2)
            eof = await asyncio.wait_for(session.read(64), timeout=2)
            assert chunk1 == b"hello-from-agent"
            assert chunk2 == b"-more-bytes"
            assert eof == b""
        finally:
            await session.close()
    finally:
        server.close()
        await server.wait_closed()


async def test_read_splits_large_chunk_across_calls() -> None:
    async def handler(ws: Any) -> None:
        await ws.recv()
        await ws.send(json.dumps({"type": "started", "pid": 1}))
        await ws.send(b"X" * 100)
        await ws.send(json.dumps({"type": "exit", "exit_code": 0}))

    server, port = await _serve(handler)
    try:
        t = _transport(port)
        session = await t.open_pty()
        try:
            part1 = await asyncio.wait_for(session.read(40), timeout=2)
            part2 = await asyncio.wait_for(session.read(40), timeout=2)
            part3 = await asyncio.wait_for(session.read(40), timeout=2)
            assert part1 == b"X" * 40
            assert part2 == b"X" * 40
            assert part3 == b"X" * 20
        finally:
            await session.close()
    finally:
        server.close()
        await server.wait_closed()


async def test_write_sends_binary_frames_to_agent() -> None:
    received: list[bytes] = []
    started_event = asyncio.Event()

    async def handler(ws: Any) -> None:
        await ws.recv()
        await ws.send(json.dumps({"type": "started", "pid": 1}))
        started_event.set()
        try:
            while True:
                msg = await ws.recv()
                if isinstance(msg, (bytes, bytearray, memoryview)):
                    received.append(bytes(msg))
        except websockets.ConnectionClosed:
            return

    server, port = await _serve(handler)
    try:
        t = _transport(port)
        session = await t.open_pty()
        try:
            await started_event.wait()
            await session.write(b"echo abc\n")
            await session.write(b"exit\n")
            # Give the server task a moment to receive.
            await asyncio.sleep(0.05)
        finally:
            await session.close()
        assert b"echo abc\n" in received
        assert b"exit\n" in received
    finally:
        server.close()
        await server.wait_closed()


async def test_resize_sends_control_json_to_agent() -> None:
    received_text: list[str] = []
    started_event = asyncio.Event()

    async def handler(ws: Any) -> None:
        await ws.recv()
        await ws.send(json.dumps({"type": "started", "pid": 1}))
        started_event.set()
        try:
            while True:
                msg = await ws.recv()
                if isinstance(msg, str):
                    received_text.append(msg)
        except websockets.ConnectionClosed:
            return

    server, port = await _serve(handler)
    try:
        t = _transport(port)
        session = await t.open_pty()
        try:
            await started_event.wait()
            await session.resize(200, 50)
            await asyncio.sleep(0.05)
        finally:
            await session.close()
        resize_msgs = [json.loads(m) for m in received_text if "resize" in m]
        assert {"type": "resize", "cols": 200, "rows": 50} in resize_msgs
    finally:
        server.close()
        await server.wait_closed()


async def test_wait_returns_exit_code_from_agent_message() -> None:
    async def handler(ws: Any) -> None:
        await ws.recv()
        await ws.send(json.dumps({"type": "started", "pid": 1}))
        await asyncio.sleep(0.05)
        await ws.send(json.dumps({"type": "exit", "exit_code": 42}))

    server, port = await _serve(handler)
    try:
        t = _transport(port)
        session = await t.open_pty()
        try:
            # Drain the exit message so the session sees it.
            await asyncio.wait_for(session.read(1024), timeout=2)
            code = await asyncio.wait_for(session.wait(), timeout=2)
            assert code == 42
        finally:
            await session.close()
    finally:
        server.close()
        await server.wait_closed()


async def test_close_is_idempotent() -> None:
    async def handler(ws: Any) -> None:
        await ws.recv()
        await ws.send(json.dumps({"type": "started", "pid": 1}))
        try:
            await ws.recv()
        except websockets.ConnectionClosed:
            return

    server, port = await _serve(handler)
    try:
        t = _transport(port)
        session = await t.open_pty()
        await session.close()
        await session.close()                       # second call must not raise
        # wait() must return even after close (event is set in close).
        code = await asyncio.wait_for(session.wait(), timeout=2)
        assert code == -1
    finally:
        server.close()
        await server.wait_closed()


# ─── error paths ─────────────────────────────────────────────────


async def test_open_pty_raises_on_agent_error_message() -> None:
    async def handler(ws: Any) -> None:
        await ws.recv()
        await ws.send(json.dumps(
            {"type": "error", "code": "PTY_NOT_AVAILABLE", "message": "no pywinpty"}
        ))
        await ws.close()

    server, port = await _serve(handler)
    try:
        t = _transport(port)
        with pytest.raises(ConnectionError, match="PTY_NOT_AVAILABLE"):
            await t.open_pty()
    finally:
        server.close()
        await server.wait_closed()


async def test_open_pty_raises_on_unexpected_first_kind() -> None:
    async def handler(ws: Any) -> None:
        await ws.recv()
        await ws.send(json.dumps({"type": "what-now", "data": 1}))
        await ws.close()

    server, port = await _serve(handler)
    try:
        t = _transport(port)
        with pytest.raises(ConnectionError, match="unexpected first message"):
            await t.open_pty()
    finally:
        server.close()
        await server.wait_closed()


async def test_open_pty_raises_on_binary_before_started() -> None:
    async def handler(ws: Any) -> None:
        await ws.recv()
        await ws.send(b"\x00\x01rude")
        await ws.close()

    server, port = await _serve(handler)
    try:
        t = _transport(port)
        with pytest.raises(ConnectionError, match="binary before started"):
            await t.open_pty()
    finally:
        server.close()
        await server.wait_closed()


async def test_open_pty_raises_on_garbled_json() -> None:
    async def handler(ws: Any) -> None:
        await ws.recv()
        await ws.send("not-json{")
        await ws.close()

    server, port = await _serve(handler)
    try:
        t = _transport(port)
        with pytest.raises(ConnectionError, match="non-JSON"):
            await t.open_pty()
    finally:
        server.close()
        await server.wait_closed()


async def test_open_pty_raises_on_auth_failure() -> None:
    async def handler(ws: Any) -> None:
        # Never reached — handshake denied by _serve's auth check.
        await ws.recv()

    server, port = await _serve(handler, token="server-token")
    try:
        t = _transport(port, token="wrong-token")
        with pytest.raises(ConnectionError):
            await t.open_pty()
    finally:
        server.close()
        await server.wait_closed()


async def test_open_pty_raises_when_transport_not_configured() -> None:
    t = HttpTransport(base_url=None, token=None)
    with pytest.raises(ConnectionError, match="WLB_HTTP_URL"):
        await t.open_pty()


async def test_open_pty_raises_on_unsupported_url_scheme() -> None:
    t = HttpTransport(base_url="ftp://nowhere/", token="abc")
    with pytest.raises(ConnectionError, match="expected http"):
        await t.open_pty()


# ─── ws/wss url translation + ssl helpers ────────────────────────


def test_ws_url_translates_http_to_ws() -> None:
    t = HttpTransport(base_url="http://agent.example:8443/", token="x")
    assert t._ws_url("/v1/pty") == "ws://agent.example:8443/v1/pty"


def test_ws_url_translates_https_to_wss() -> None:
    t = HttpTransport(base_url="https://agent.example/", token="x")
    assert t._ws_url("/v1/pty") == "wss://agent.example/v1/pty"


def test_ws_url_none_when_base_url_unset() -> None:
    t = HttpTransport(base_url=None, token="x")
    assert t._ws_url("/v1/pty") is None


def test_ws_ssl_context_none_for_ws_scheme() -> None:
    t = HttpTransport(base_url="http://agent.example/", token="x")
    assert t._ws_ssl_context("ws://agent.example/v1/pty") is None


def test_ws_ssl_context_disabled_when_verify_tls_false() -> None:
    import ssl

    t = HttpTransport(base_url="https://agent.example/", token="x", verify_tls=False)
    ctx = t._ws_ssl_context("wss://agent.example/v1/pty")
    assert ctx is not None
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_NONE
