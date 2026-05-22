"""HttpTransport.run_streaming — NDJSON wire tests using httpx.MockTransport.

Exercises the M3.2 client: chunked POST /v1/shell/stream parsed line-by-line
into StreamEvents, with the full error-mapping matrix the non-streaming
shell() supports.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from wlb.transport.base import StreamEvent
from wlb.transport.http import HttpTransport


def _ndjson_body(events: list[dict[str, Any]]) -> bytes:
    """Encode a list of dicts as NDJSON (one per line, trailing newline)."""
    return b"".join(
        (json.dumps(ev, separators=(",", ":")) + "\n").encode("utf-8")
        for ev in events
    )


def _build_transport(handler) -> HttpTransport:
    """HttpTransport with the httpx.MockTransport plumbed into _client()."""
    t = HttpTransport(
        base_url="https://win-host:8443",
        token="tok-zzz",
        connect_timeout=5,
    )

    def _patched() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=t.base_url or "",
            headers={"Authorization": f"Bearer {t._token() or ''}"},
            transport=httpx.MockTransport(handler),
        )

    t._client = _patched   # type: ignore[method-assign]
    return t


async def _collect(it) -> list[StreamEvent]:
    return [ev async for ev in it]


# ─── happy path ──────────────────────────────────────────────────


async def test_streaming_emits_line_events_then_done() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/shell/stream"
        body = json.loads(request.content)
        assert body["cmd"] == "echo hi"
        assert body["interpreter"] == "cmd"
        return httpx.Response(
            200,
            content=_ndjson_body([
                {"kind": "line", "line": "hi", "stream": "stdout"},
                {"kind": "line", "line": "warn", "stream": "stderr"},
                {"kind": "done", "exit_code": 0, "duration_ms": 12},
            ]),
            headers={"content-type": "application/x-ndjson"},
        )

    t = _build_transport(handler)
    events = await _collect(t.run_streaming("echo hi", interpreter="cmd"))
    assert [(e.kind, e.line, e.stream) for e in events] == [
        ("line", "hi", "stdout"),
        ("line", "warn", "stderr"),
        ("done", None, None),
    ]
    assert events[-1].exit_code == 0


async def test_streaming_no_events_after_done() -> None:
    """Once a done event is yielded, the generator stops — extra lines ignored."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_ndjson_body([
                {"kind": "line", "line": "a", "stream": "stdout"},
                {"kind": "done", "exit_code": 0},
                {"kind": "line", "line": "should-not-arrive", "stream": "stdout"},
            ]),
            headers={"content-type": "application/x-ndjson"},
        )

    t = _build_transport(handler)
    events = await _collect(t.run_streaming("x", interpreter="cmd"))
    # Should be exactly: line "a", done. The trailing line is dropped.
    assert [(e.kind, e.line) for e in events] == [("line", "a"), ("done", None)]


async def test_streaming_synthesizes_done_if_server_closes_early() -> None:
    """Server closes stream without a done — client manufactures one."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_ndjson_body([
                {"kind": "line", "line": "alpha", "stream": "stdout"},
                {"kind": "line", "line": "beta", "stream": "stdout"},
                # no done
            ]),
            headers={"content-type": "application/x-ndjson"},
        )

    t = _build_transport(handler)
    events = await _collect(t.run_streaming("x", interpreter="cmd"))
    assert events[-1].kind == "done"
    assert events[-1].error_code == "HTTP_AGENT_ERROR"


# ─── error mapping ───────────────────────────────────────────────


async def test_streaming_401_maps_to_auth_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad token")

    t = _build_transport(handler)
    events = await _collect(t.run_streaming("x"))
    assert len(events) == 1
    assert events[0].kind == "done"
    assert events[0].error_code == "HTTP_AUTH_FAILED"


async def test_streaming_403_maps_to_permission_denied() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="deny-list")

    t = _build_transport(handler)
    events = await _collect(t.run_streaming("format c:"))
    assert len(events) == 1
    assert events[0].error_code == "PERMISSION_DENIED"


async def test_streaming_500_maps_to_agent_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    t = _build_transport(handler)
    events = await _collect(t.run_streaming("x"))
    assert events[0].error_code == "HTTP_AGENT_ERROR"


async def test_streaming_connect_error_maps_to_host_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    t = _build_transport(handler)
    events = await _collect(t.run_streaming("x"))
    assert events[0].error_code == "HTTP_HOST_UNREACHABLE"


async def test_streaming_garbled_ndjson_aborts_with_bad_response() -> None:
    """Malformed NDJSON aborts the stream with HTTP_BAD_RESPONSE."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"kind":"line","line":"ok","stream":"stdout"}\n'
                    b'this-is-not-json\n'
                    b'{"kind":"done","exit_code":0}\n',
            headers={"content-type": "application/x-ndjson"},
        )

    t = _build_transport(handler)
    events = await _collect(t.run_streaming("x"))
    # First line parsed, then garbled → done(HTTP_BAD_RESPONSE).
    assert events[0].kind == "line"
    assert events[0].line == "ok"
    assert events[-1].kind == "done"
    assert events[-1].error_code == "HTTP_BAD_RESPONSE"


# ─── config validation ──────────────────────────────────────────


async def test_streaming_unconfigured_yields_single_done() -> None:
    t = HttpTransport(base_url=None, token="x")
    events = await _collect(t.run_streaming("x"))
    assert len(events) == 1
    assert events[0].error_code == "TRANSPORT_NOT_CONFIGURED"


async def test_streaming_agent_done_with_error_code_preserved() -> None:
    """Agent reports its own error_code (e.g. POWERSHELL_NOT_AVAILABLE)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_ndjson_body([
                {
                    "kind": "done",
                    "exit_code": -1,
                    "error_code": "POWERSHELL_NOT_AVAILABLE",
                    "duration_ms": 5,
                },
            ]),
        )

    t = _build_transport(handler)
    events = await _collect(t.run_streaming("Get-Process", interpreter="powershell"))
    assert events[-1].kind == "done"
    assert events[-1].error_code == "POWERSHELL_NOT_AVAILABLE"


# ─── supports_streaming flag flipped ─────────────────────────────


def test_supports_streaming_is_true_now() -> None:
    """M3.2 promise: HttpTransport now reports real streaming."""
    assert HttpTransport.supports_streaming is True
