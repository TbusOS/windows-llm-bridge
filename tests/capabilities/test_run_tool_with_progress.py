"""run_tool_with_progress — capability-layer tests (M3.10).

Exercises the wrapper that bridges streaming events to a single
aggregated Result. The MCP tool layer wraps this; tests for the
``ctx.report_progress`` plumbing live in tests/mcp/.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wlb.capabilities.tool import (
    ToolStreamEvent,
    run_tool_with_progress,
)
from wlb.transport.local import LocalTransport


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WLB_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("WLB_TOOLS_FILE", raising=False)
    return tmp_path


def _write_tools(tmp_path: Path, body: str) -> None:
    (tmp_path / "wlb-tools.toml").write_text(body, encoding="utf-8")


# ─── happy path: events forwarded + Result is ok=True ────────────


async def test_invokes_on_event_for_every_stream_event(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "printf '25%%\\n50%%\\n100%%\\nDONE\\n'"

[tool.t.regex]
progress = '^(\\d{1,3})%'
success  = '^DONE$'
""")
    seen: list[ToolStreamEvent] = []

    async def on_event(ev: ToolStreamEvent) -> None:
        seen.append(ev)

    r = await run_tool_with_progress(
        LocalTransport(), "t", {}, on_event=on_event,
    )
    assert r.ok, r.to_dict()
    assert r.data is not None
    assert r.data.success is True
    assert r.data.progress_percent == 100

    kinds = [ev.kind for ev in seen]
    assert kinds[-1] == "done"
    assert "progress" in kinds
    assert "match" in kinds
    # At least one "line" event for each printf line.
    assert kinds.count("line") >= 4


async def test_result_artifacts_include_log_path_on_success(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "echo hello"
""")
    r = await run_tool_with_progress(LocalTransport(), "t", {})
    assert r.ok
    assert r.artifacts is not None
    assert len(r.artifacts) == 1
    assert str(r.artifacts[0]).endswith(".log")


async def test_no_event_callback_still_returns_correct_result(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "echo OK"

[tool.t.regex]
success = '^OK$'
""")
    r = await run_tool_with_progress(LocalTransport(), "t", {})
    assert r.ok
    assert r.data is not None
    assert r.data.success_match == "OK"


# ─── failure paths: TOOL_FAILED / TOOL_NOT_FOUND / etc. ──────────


async def test_failure_regex_hit_returns_tool_failed(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "echo ERROR: kaboom"

[tool.t.regex]
failure = '^ERROR:'
""")
    r = await run_tool_with_progress(LocalTransport(), "t", {})
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "TOOL_FAILED"
    # The details should carry the original ToolRunOutput dict so MCP
    # clients can inspect log_path, exit_code etc.
    assert "log_path" in (r.error.details or {})


async def test_missing_tool_returns_tool_not_found(_isolated: Path) -> None:
    _write_tools(_isolated, "")          # empty tools file
    r = await run_tool_with_progress(LocalTransport(), "nope", {})
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "TOOL_NOT_FOUND"
    assert r.error.suggestion                # populated, not blank
    assert (r.error.details or {}).get("tool") == "nope"


async def test_missing_required_arg_returns_tool_arg_missing(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "echo {who}"
args             = ["who"]
""")
    r = await run_tool_with_progress(LocalTransport(), "t", {})       # missing 'who'
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "TOOL_ARG_MISSING"


# ─── robustness: callback errors don't poison the run ────────────


async def test_on_event_exception_is_swallowed(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "echo hi"
""")
    calls = 0

    async def crash(ev: ToolStreamEvent) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("test-injected callback boom")

    # Even though every event handler raises, the run still completes
    # and the final Result is the same as without a callback.
    r = await run_tool_with_progress(
        LocalTransport(), "t", {}, on_event=crash,
    )
    assert r.ok
    assert calls > 0                          # callback DID fire each event
