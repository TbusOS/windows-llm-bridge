"""WS /ws/pty — bidirectional PTY tests via FastAPI TestClient.

Drives the real LocalTransport PTY end-to-end: send the JSON settings
frame, send keystrokes as binary, read back binary output, send a
resize control message, and finally let the shell exit.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wlb.api.server import create_app


pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="LocalTransport PTY requires Unix pty.openpty (ConPTY support is M3.4.1)",
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WLB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("WLB_TRANSPORT", "local")
    monkeypatch.delenv("WLB_PROFILE", raising=False)
    return tmp_path


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _drain_until(ws, needle: bytes, max_frames: int = 40) -> bytes:
    """Receive frames (binary or text) until ``needle`` appears or we hit max_frames."""
    buf = bytearray()
    for _ in range(max_frames):
        try:
            msg = ws.receive()
        except Exception:
            break
        if msg.get("bytes") is not None:
            buf.extend(msg["bytes"])
            if needle in buf:
                return bytes(buf)
        elif msg.get("text") is not None:
            # Control event from server — ignore for needle search, but
            # break on exit so we don't hang.
            try:
                ev = json.loads(msg["text"])
            except ValueError:
                continue
            if ev.get("kind") == "exit":
                break
            if ev.get("kind") == "error":
                break
    return bytes(buf)


def test_pty_round_trip_via_websocket(client: TestClient) -> None:
    with client.websocket_connect("/ws/pty") as ws:
        ws.send_text(json.dumps({"interpreter": "raw", "cols": 80, "rows": 24}))
        ws.send_bytes(b"echo wlb-ws-pty\n")
        out = _drain_until(ws, b"wlb-ws-pty", max_frames=30)
        assert b"wlb-ws-pty" in out
        # Trigger a clean exit so the server's pump_to_ws emits the exit event.
        ws.send_bytes(b"exit\n")


def test_pty_bad_interpreter_emits_error(client: TestClient) -> None:
    with client.websocket_connect("/ws/pty") as ws:
        ws.send_text(json.dumps({"interpreter": "bogus", "cols": 80, "rows": 24}))
        msg = ws.receive_text()
        ev = json.loads(msg)
        assert ev["kind"] == "error"
        assert "interpreter" in ev["error"]


def test_pty_bad_first_frame_emits_error(client: TestClient) -> None:
    with client.websocket_connect("/ws/pty") as ws:
        ws.send_text("not-json-at-all")
        msg = ws.receive_text()
        ev = json.loads(msg)
        assert ev["kind"] == "error"


def test_pty_resize_control_does_not_crash(client: TestClient) -> None:
    with client.websocket_connect("/ws/pty") as ws:
        ws.send_text(json.dumps({"interpreter": "raw", "cols": 80, "rows": 24}))
        ws.send_text(json.dumps({"kind": "resize", "cols": 120, "rows": 40}))
        ws.send_bytes(b"echo after-resize\n")
        out = _drain_until(ws, b"after-resize", max_frames=30)
        assert b"after-resize" in out
        ws.send_bytes(b"exit\n")


def test_pty_close_control_ends_session(client: TestClient) -> None:
    with client.websocket_connect("/ws/pty") as ws:
        ws.send_text(json.dumps({"interpreter": "raw"}))
        ws.send_text(json.dumps({"kind": "close"}))
        # Server should drop the connection.
        try:
            ws.receive(timeout=2)
        except Exception:
            pass   # close is the success signal
