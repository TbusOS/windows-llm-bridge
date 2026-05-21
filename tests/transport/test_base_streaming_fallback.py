"""Transport.run_streaming default fallback — every transport gets the API."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncssh
import pytest

from wlb.transport.base import StreamEvent
from wlb.transport.http import HttpTransport


async def test_http_transport_fallback_replays_captured_output() -> None:
    """HttpTransport doesn't override run_streaming → falls back to shell() replay."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": True,
                "exit_code": 0,
                "stdout": "alpha\nbeta\ngamma\n",
                "stderr": "warning-text\n",
                "duration_ms": 17,
            },
        )

    t = HttpTransport(
        base_url="http://agent.test", token="tok-yyy", connect_timeout=5,
    )

    def _patched() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=t.base_url or "",
            headers={"Authorization": f"Bearer {t._token()}"},
            transport=httpx.MockTransport(handler),
        )

    t._client = _patched   # type: ignore[method-assign]

    events: list[StreamEvent] = []
    async for ev in t.run_streaming("ver", interpreter="cmd"):
        events.append(ev)

    # Three stdout lines + one stderr line + one done.
    stdout_lines = [e.line for e in events if e.kind == "line" and e.stream == "stdout"]
    stderr_lines = [e.line for e in events if e.kind == "line" and e.stream == "stderr"]
    assert stdout_lines == ["alpha", "beta", "gamma"]
    assert stderr_lines == ["warning-text"]
    assert events[-1].kind == "done"
    assert events[-1].exit_code == 0


async def test_fallback_done_preserves_error_code() -> None:
    """When shell() fails, the fallback's done event carries the error_code."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "bad token"})

    t = HttpTransport(base_url="http://agent.test", token="tok-yyy")

    def _patched() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=t.base_url or "",
            headers={"Authorization": f"Bearer {t._token()}"},
            transport=httpx.MockTransport(handler),
        )

    t._client = _patched   # type: ignore[method-assign]

    done: StreamEvent | None = None
    async for ev in t.run_streaming("ver", interpreter="cmd"):
        if ev.kind == "done":
            done = ev
    assert done is not None
    assert done.error_code == "HTTP_AUTH_FAILED"
