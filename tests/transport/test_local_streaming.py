"""LocalTransport.run_streaming — real subprocess streaming tests."""

from __future__ import annotations

import pytest

from wlb.transport.base import StreamEvent
from wlb.transport.local import LocalTransport


async def _collect(it) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    async for ev in it:
        events.append(ev)
    return events


async def test_stream_emits_stdout_lines() -> None:
    t = LocalTransport()
    events = await _collect(
        t.run_streaming("printf 'a\\nb\\nc\\n'", interpreter="raw")
    )
    lines = [e for e in events if e.kind == "line"]
    assert [e.line for e in lines] == ["a", "b", "c"]
    assert all(e.stream == "stdout" for e in lines)


async def test_stream_emits_stderr_separately() -> None:
    t = LocalTransport()
    events = await _collect(
        t.run_streaming("printf 'out\\n'; printf 'err\\n' >&2", interpreter="raw")
    )
    out_lines = [e.line for e in events if e.kind == "line" and e.stream == "stdout"]
    err_lines = [e.line for e in events if e.kind == "line" and e.stream == "stderr"]
    assert "out" in out_lines
    assert "err" in err_lines


async def test_stream_terminal_done_carries_exit_code() -> None:
    t = LocalTransport()
    events = await _collect(t.run_streaming("true", interpreter="raw"))
    done = [e for e in events if e.kind == "done"]
    assert len(done) == 1
    assert done[0].exit_code == 0
    assert done[0].error_code is None


async def test_stream_nonzero_exit_maps_to_shell_nonzero() -> None:
    t = LocalTransport()
    events = await _collect(t.run_streaming("false", interpreter="raw"))
    done = events[-1]
    assert done.kind == "done"
    assert done.exit_code != 0
    assert done.error_code == "SHELL_NONZERO_EXIT"


async def test_stream_timeout_kills_process() -> None:
    t = LocalTransport()
    events = await _collect(
        t.run_streaming("sleep 5", interpreter="raw", timeout=1)
    )
    done = events[-1]
    assert done.kind == "done"
    assert done.error_code == "TIMEOUT_SHELL"


async def test_stream_done_always_last_event() -> None:
    t = LocalTransport()
    events = await _collect(
        t.run_streaming("echo one; echo two", interpreter="raw")
    )
    assert events[-1].kind == "done"
    # Exactly one done event.
    assert sum(1 for e in events if e.kind == "done") == 1


async def test_stream_interleaved_stdout_stderr() -> None:
    """Both streams flow concurrently — verify lines from each are seen."""
    t = LocalTransport()
    events = await _collect(
        t.run_streaming(
            "printf 'o1\\n'; printf 'e1\\n' >&2; printf 'o2\\n'; printf 'e2\\n' >&2",
            interpreter="raw",
        )
    )
    line_events = [e for e in events if e.kind == "line"]
    stdout_lines = {e.line for e in line_events if e.stream == "stdout"}
    stderr_lines = {e.line for e in line_events if e.stream == "stderr"}
    assert stdout_lines == {"o1", "o2"}
    assert stderr_lines == {"e1", "e2"}


async def test_stream_long_burst_preserves_order_per_stream() -> None:
    t = LocalTransport()
    events = await _collect(
        t.run_streaming(
            "for i in 1 2 3 4 5 6 7 8 9 10; do echo $i; done",
            interpreter="raw",
            timeout=5,
        )
    )
    line_events = [e.line for e in events if e.kind == "line" and e.stream == "stdout"]
    assert line_events == [str(n) for n in range(1, 11)]
