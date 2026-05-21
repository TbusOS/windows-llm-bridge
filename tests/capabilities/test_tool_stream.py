"""run_tool_stream end-to-end tests over LocalTransport.

These exercise the full streaming pipeline: TOML loading, arg validation,
transport.run_streaming consumption, regex matching per line, incremental
log writing, and final verdict.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wlb.capabilities.tool import ToolStreamEvent, run_tool_stream
from wlb.transport.local import LocalTransport


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WLB_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("WLB_TOOLS_FILE", raising=False)
    return tmp_path


def _write_tools(tmp_path: Path, body: str) -> None:
    (tmp_path / "wlb-tools.toml").write_text(body, encoding="utf-8")


async def _drain(it) -> list[ToolStreamEvent]:
    events: list[ToolStreamEvent] = []
    async for ev in it:
        events.append(ev)
    return events


# ─── happy path ───────────────────────────────────────────────────


async def test_stream_emits_progress_for_each_match(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "printf '0%%\\n50%%\\n100%%\\n'"

[tool.t.regex]
progress = '^(\\d{1,3})%'
""")
    events = await _drain(run_tool_stream(LocalTransport(), "t", {}))
    progress_events = [e for e in events if e.kind == "progress"]
    assert [e.percent for e in progress_events] == [0, 50, 100]

    done = events[-1]
    assert done.kind == "done"
    assert done.ok is True
    assert done.output is not None
    assert done.output.progress_percent == 100


async def test_stream_emits_line_events_with_stream_label(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "echo o1; echo e1 >&2"
""")
    events = await _drain(run_tool_stream(LocalTransport(), "t", {}))
    out_lines = [e.line for e in events if e.kind == "line" and e.stream == "stdout"]
    err_lines = [e.line for e in events if e.kind == "line" and e.stream == "stderr"]
    assert "o1" in out_lines
    assert "e1" in err_lines


async def test_stream_match_events_on_success_and_failure(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "echo before; echo OK"

[tool.t.regex]
success = '^OK$'
""")
    events = await _drain(run_tool_stream(LocalTransport(), "t", {}))
    match_events = [e for e in events if e.kind == "match"]
    assert len(match_events) == 1
    assert match_events[0].pattern_label == "success"
    assert match_events[0].match == "OK"

    done = events[-1]
    assert done.ok is True


async def test_stream_failure_re_overrides_zero_exit(_isolated: Path) -> None:
    """Tool exits 0 but failure_re hit → done.ok=False."""
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "echo head; echo ERROR: kaboom; echo tail"

[tool.t.regex]
failure = '^ERROR:'
""")
    events = await _drain(run_tool_stream(LocalTransport(), "t", {}))
    failure_matches = [e for e in events if e.kind == "match" and e.pattern_label == "failure"]
    assert len(failure_matches) == 1

    done = events[-1]
    assert done.ok is False
    assert done.output is not None
    assert done.output.failure_match == "ERROR:"


async def test_stream_success_re_missing_marks_failure(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "echo nothing-special"

[tool.t.regex]
success = '^expected-marker$'
""")
    events = await _drain(run_tool_stream(LocalTransport(), "t", {}))
    done = events[-1]
    assert done.ok is False


async def test_stream_writes_log_incrementally(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "printf 'one\\ntwo\\nthree\\n'"
""")
    events = await _drain(run_tool_stream(LocalTransport(), "t", {}))
    done = events[-1]
    assert done.ok is True
    assert done.output is not None
    log_path = Path(done.output.log_path)
    assert log_path.exists()
    body = log_path.read_text(encoding="utf-8")
    # Header + 3 lines
    assert "# wlb tool log (stream)" in body
    assert "[stdout] one" in body
    assert "[stdout] two" in body
    assert "[stdout] three" in body


# ─── failure modes (mirrored from run_tool) ──────────────────────


async def test_stream_unknown_tool(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.real]
interpreter = "raw"
command_template = "echo hi"
""")
    events = await _drain(run_tool_stream(LocalTransport(), "missing", {}))
    assert len(events) == 1
    assert events[0].kind == "done"
    assert events[0].ok is False
    assert events[0].error_code == "TOOL_NOT_FOUND"


async def test_stream_missing_required_arg(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "echo {x}"
args = ["x"]
""")
    events = await _drain(run_tool_stream(LocalTransport(), "t", {}))
    assert len(events) == 1
    assert events[0].kind == "done"
    assert events[0].error_code == "TOOL_ARG_MISSING"


async def test_stream_shell_meta_arg_rejected(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "echo {x}"
args = ["x"]
""")
    events = await _drain(run_tool_stream(LocalTransport(), "t", {"x": "evil;rm -rf /"}))
    assert events[0].error_code == "TOOL_ARG_INVALID"


async def test_stream_done_kind_is_terminal(_isolated: Path) -> None:
    """No events emitted after done."""
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "echo only"
""")
    events = await _drain(run_tool_stream(LocalTransport(), "t", {}))
    assert events[-1].kind == "done"
    # exactly one done
    assert sum(1 for e in events if e.kind == "done") == 1
