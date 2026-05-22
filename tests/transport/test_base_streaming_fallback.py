"""Transport.run_streaming default fallback contract.

Verifies that a transport which doesn't override ``run_streaming`` still
gets a working iterator out of the box — output is replayed as line
events followed by a synthesized ``done``. As of M3.2 every shipped
transport (Local, SSH, HTTP) overrides with a real streaming impl, so
we exercise the fallback against a synthetic test-only transport.
"""

from __future__ import annotations

from typing import Any

import pytest

from wlb.transport.base import Interpreter, ShellResult, StreamEvent, Transport


class _CaptureOnlyTransport(Transport):
    """Test-only transport: implements shell() only; gets streaming via fallback."""

    name = "capture_only"
    supports_streaming = False     # the whole point of this fixture

    def __init__(self, result: ShellResult) -> None:
        self._result = result

    async def shell(
        self,
        cmd: str,
        *,
        interpreter: Interpreter = "cmd",
        timeout: int = 30,
    ) -> ShellResult:
        return self._result

    async def health(self) -> dict[str, Any]:
        return {"ok": True, "transport": self.name}


async def test_fallback_replays_stdout_then_stderr_then_done() -> None:
    """The base default replays captured output as line events + a final done."""
    t = _CaptureOnlyTransport(
        ShellResult(
            ok=True,
            exit_code=0,
            stdout="alpha\nbeta\ngamma\n",
            stderr="warning-text\n",
            duration_ms=17,
        )
    )

    events: list[StreamEvent] = []
    async for ev in t.run_streaming("any-cmd", interpreter="cmd"):
        events.append(ev)

    stdout_lines = [e.line for e in events if e.kind == "line" and e.stream == "stdout"]
    stderr_lines = [e.line for e in events if e.kind == "line" and e.stream == "stderr"]
    assert stdout_lines == ["alpha", "beta", "gamma"]
    assert stderr_lines == ["warning-text"]
    assert events[-1].kind == "done"
    assert events[-1].exit_code == 0


async def test_fallback_done_preserves_error_code() -> None:
    """When shell() reports a transport-level error, the fallback done carries it."""
    t = _CaptureOnlyTransport(
        ShellResult(
            ok=False,
            exit_code=-1,
            stderr="agent rejected token",
            duration_ms=5,
            error_code="HTTP_AUTH_FAILED",
        )
    )

    done: StreamEvent | None = None
    async for ev in t.run_streaming("ver", interpreter="cmd"):
        if ev.kind == "done":
            done = ev
    assert done is not None
    assert done.error_code == "HTTP_AUTH_FAILED"
