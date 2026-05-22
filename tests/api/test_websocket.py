"""WebSocket tool-run tests using fastapi.TestClient.

The /ws/tool/{name} endpoint streams ToolStreamEvent JSON over a
WebSocket. We drive it with a real LocalTransport so the full pipeline
runs (TOML load → arg validation → subprocess streaming → regex parsing
→ event emission).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wlb.api.server import create_app


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WLB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("WLB_TRANSPORT", "local")
    monkeypatch.delenv("WLB_PROFILE", raising=False)
    monkeypatch.delenv("WLB_TOOLS_FILE", raising=False)
    return tmp_path


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _write_tools(tmp_path: Path, body: str) -> None:
    (tmp_path / "wlb-tools.toml").write_text(body, encoding="utf-8")


def _drain(ws) -> list[dict]:
    """Pull JSON frames off the WS until a done event arrives."""
    events: list[dict] = []
    while True:
        try:
            text = ws.receive_text()
        except Exception:
            break
        try:
            ev = json.loads(text)
        except ValueError:
            continue
        events.append(ev)
        if ev.get("kind") == "done":
            break
    return events


def test_ws_streams_line_then_done(client: TestClient, _isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.echo]
interpreter = "raw"
command_template = "echo wlb-ws-test"

[tool.echo.regex]
success = "wlb-ws-test"
""")
    with client.websocket_connect("/ws/tool/echo") as ws:
        ws.send_text(json.dumps({"args": {}}))
        events = _drain(ws)

    lines = [e for e in events if e["kind"] == "line"]
    assert any("wlb-ws-test" in (e.get("line") or "") for e in lines)
    done = events[-1]
    assert done["kind"] == "done"
    assert done["ok"] is True
    assert done["output"]["success_match"] == "wlb-ws-test"


def test_ws_unknown_tool_yields_done_with_error_code(client: TestClient, _isolated: Path) -> None:
    _write_tools(_isolated, '[tool.real]\ninterpreter = "raw"\ncommand_template = "echo x"\n')
    with client.websocket_connect("/ws/tool/never-declared") as ws:
        ws.send_text(json.dumps({"args": {}}))
        events = _drain(ws)
    assert len(events) == 1
    assert events[0]["kind"] == "done"
    assert events[0]["ok"] is False
    assert events[0]["error_code"] == "TOOL_NOT_FOUND"


def test_ws_missing_arg_short_circuits(client: TestClient, _isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "echo {x}"
args = ["x"]
""")
    with client.websocket_connect("/ws/tool/t") as ws:
        ws.send_text(json.dumps({"args": {}}))
        events = _drain(ws)
    assert events[-1]["kind"] == "done"
    assert events[-1]["error_code"] == "TOOL_ARG_MISSING"


def test_ws_progress_events_emitted(client: TestClient, _isolated: Path) -> None:
    _write_tools(_isolated, r"""
[tool.t]
interpreter = "raw"
command_template = "printf '0%%\n50%%\n100%%\n'"

[tool.t.regex]
progress = '^(\d{1,3})%'
""")
    with client.websocket_connect("/ws/tool/t") as ws:
        ws.send_text(json.dumps({"args": {}}))
        events = _drain(ws)
    percents = [e["percent"] for e in events if e["kind"] == "progress"]
    assert percents == [0, 50, 100]


def test_ws_failure_re_marks_done_failed(client: TestClient, _isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "echo before; echo ERROR: kaboom; echo after"

[tool.t.regex]
failure = '^ERROR:'
""")
    with client.websocket_connect("/ws/tool/t") as ws:
        ws.send_text(json.dumps({"args": {}}))
        events = _drain(ws)
    done = events[-1]
    assert done["kind"] == "done"
    assert done["ok"] is False
    assert done["output"]["failure_match"] == "ERROR:"


def test_ws_bad_first_frame_yields_done(client: TestClient, _isolated: Path) -> None:
    _write_tools(_isolated, '[tool.t]\ninterpreter = "raw"\ncommand_template = "echo x"\n')
    with client.websocket_connect("/ws/tool/t") as ws:
        ws.send_text("not-json")
        events = _drain(ws)
    assert events[-1]["kind"] == "done"
    assert events[-1]["ok"] is False
