"""HttpTransport unit tests.

httpx.MockTransport stands in for the wlb-agent. We verify the wire
contract from the controller's perspective:
- happy paths (shell / health / push / pull)
- exception mapping (401 / 404 / 5xx / connect timeout / non-JSON)
- token loading from a file vs. inline override
- config validation (unset URL, unset token)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from wlb.transport.http import HttpTransport


def _build_transport_with_handler(handler, *, token_file: Path | None = None, **kw: Any) -> HttpTransport:
    """Inject an httpx.MockTransport via monkey-patching the client builder.

    HttpTransport._client() returns a fresh AsyncClient each call; we override
    it to return a client with our MockTransport plumbed in.
    """
    t = HttpTransport(
        base_url=kw.pop("base_url", "https://win-host:8443"),
        token_file=str(token_file) if token_file else None,
        token=kw.pop("token", None if token_file else "tok-XXX"),
        connect_timeout=kw.pop("connect_timeout", 5),
        verify_tls=kw.pop("verify_tls", True),
    )

    def _patched() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=t.base_url or "",
            headers={"Authorization": f"Bearer {t._token() or ''}"},
            transport=httpx.MockTransport(handler),
        )

    t._client = _patched   # type: ignore[method-assign]
    return t


# ─── config validation ──────────────────────────────────────────


async def test_unconfigured_base_url() -> None:
    t = HttpTransport(base_url=None, token="x")
    r = await t.shell("ver")
    assert not r.ok
    assert r.error_code == "TRANSPORT_NOT_CONFIGURED"


async def test_unconfigured_token() -> None:
    t = HttpTransport(base_url="https://win-host:8443", token=None)
    r = await t.shell("ver")
    assert not r.ok
    assert r.error_code == "TRANSPORT_NOT_CONFIGURED"


async def test_token_loaded_from_file(tmp_path: Path) -> None:
    token_path = tmp_path / "token"
    token_path.write_text("filed-token-123", encoding="utf-8")
    t = HttpTransport(base_url="https://win-host:8443", token_file=str(token_path))
    assert t._token() == "filed-token-123"


# ─── shell happy + errors ───────────────────────────────────────


async def test_shell_happy_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/shell"
        assert request.headers["authorization"] == "Bearer tok-XXX"
        body = json.loads(request.content)
        assert body == {"cmd": "ver", "interpreter": "cmd", "timeout": 30}
        return httpx.Response(
            200,
            json={
                "ok": True,
                "exit_code": 0,
                "stdout": "Microsoft Windows [Version 10.0]",
                "stderr": "",
                "duration_ms": 42,
            },
        )

    t = _build_transport_with_handler(handler)
    r = await t.shell("ver", interpreter="cmd")
    assert r.ok, r
    assert r.exit_code == 0
    assert "Microsoft Windows" in r.stdout


async def test_shell_401_maps_to_auth_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid token"})

    t = _build_transport_with_handler(handler)
    r = await t.shell("ver")
    assert not r.ok
    assert r.error_code == "HTTP_AUTH_FAILED"


async def test_shell_500_maps_to_agent_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    t = _build_transport_with_handler(handler)
    r = await t.shell("ver")
    assert not r.ok
    assert r.error_code == "HTTP_AGENT_ERROR"


async def test_shell_403_maps_to_permission_denied() -> None:
    """Agent's deny-list returns 403."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "deny-list: format a drive"})

    t = _build_transport_with_handler(handler)
    r = await t.shell("format c:")
    assert not r.ok
    assert r.error_code == "PERMISSION_DENIED"


async def test_shell_non_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    t = _build_transport_with_handler(handler)
    r = await t.shell("ver")
    assert not r.ok
    assert r.error_code == "HTTP_BAD_RESPONSE"


async def test_shell_agent_returned_nonzero_with_code() -> None:
    """Agent reports POWERSHELL_NOT_AVAILABLE → wlb preserves the code."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": "neither pwsh.exe nor powershell.exe found",
                "duration_ms": 5,
                "error_code": "POWERSHELL_NOT_AVAILABLE",
            },
        )

    t = _build_transport_with_handler(handler)
    r = await t.shell("Get-Process", interpreter="powershell")
    assert not r.ok
    assert r.error_code == "POWERSHELL_NOT_AVAILABLE"


async def test_shell_connect_error_maps_to_host_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    t = _build_transport_with_handler(handler)
    r = await t.shell("ver")
    assert not r.ok
    assert r.error_code == "HTTP_HOST_UNREACHABLE"


# ─── push / pull ─────────────────────────────────────────────────


async def test_push_happy_path(tmp_path: Path) -> None:
    src = tmp_path / "fw.bin"
    payload = b"firmware-bytes-here"
    src.write_bytes(payload)

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.params.get("path")
        captured["body"] = request.content
        return httpx.Response(200, json={"ok": True, "bytes": len(request.content), "path": "C:\\stage\\fw.bin"})

    t = _build_transport_with_handler(handler)
    r = await t.push(src, "C:\\stage\\fw.bin")
    assert r.ok, r
    assert captured["path"] == "C:\\stage\\fw.bin"
    assert captured["body"] == payload
    assert "transferred" in r.stdout


async def test_push_missing_local_fast_fails(tmp_path: Path) -> None:
    # MockTransport that would fail if accidentally hit.
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("agent should not be called")

    t = _build_transport_with_handler(handler)
    r = await t.push(tmp_path / "nope.bin", "C:\\stage\\x.bin")
    assert not r.ok
    assert r.error_code == "LOCAL_PATH_NOT_FOUND"


async def test_push_directory_rejected(tmp_path: Path) -> None:
    """M2.4 supports single files only."""
    (tmp_path / "dir").mkdir()
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("agent should not be called")

    t = _build_transport_with_handler(handler)
    r = await t.push(tmp_path / "dir", "C:\\stage\\dir")
    assert not r.ok
    assert r.error_code == "TRANSPORT_NOT_SUPPORTED"


async def test_pull_happy_path(tmp_path: Path) -> None:
    payload = b"some-log-content"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/file/pull"
        assert request.url.params.get("path") == "C:\\logs\\flash.log"
        return httpx.Response(200, content=payload, headers={"content-type": "application/octet-stream"})

    t = _build_transport_with_handler(handler)
    dst = tmp_path / "out.log"
    r = await t.pull("C:\\logs\\flash.log", dst)
    assert r.ok, r
    assert dst.read_bytes() == payload


async def test_pull_404_maps_to_file_not_found(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    t = _build_transport_with_handler(handler)
    r = await t.pull("C:\\nope.log", tmp_path / "x.log")
    assert not r.ok
    assert r.error_code == "FILE_NOT_FOUND"


# ─── health ──────────────────────────────────────────────────────


async def test_health_happy_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/health"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "agent_version": "0.0.1",
                "platform": "win32",
                "windows_version": "Microsoft Windows [Version 10.0.22631]",
                "powershell": "pwsh.exe",
            },
        )

    t = _build_transport_with_handler(handler)
    h = await t.health()
    assert h["ok"] is True
    assert h["configured"] is True
    assert h["powershell"] == "pwsh.exe"
    assert "connect_ms" in h


async def test_health_401_reports_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid"})

    t = _build_transport_with_handler(handler)
    h = await t.health()
    assert h["ok"] is False
    assert h["error_code"] == "HTTP_AUTH_FAILED"


async def test_health_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    t = _build_transport_with_handler(handler)
    h = await t.health()
    assert h["ok"] is False
    assert "unreachable" in h["stage"].lower()


async def test_health_not_configured() -> None:
    t = HttpTransport(base_url=None, token=None)
    h = await t.health()
    assert h["ok"] is False
    assert h["configured"] is False
    assert "WLB_HTTP_URL" in h["stage"]
