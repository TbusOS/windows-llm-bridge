"""FastAPI HTTP endpoint tests using fastapi.TestClient."""

from __future__ import annotations

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


def test_root_returns_html(client: TestClient) -> None:
    """GET / serves the bundled index.html when present."""
    r = client.get("/")
    assert r.status_code == 200
    # Either HTML or the fallback JSON banner depending on whether static/ is bundled.
    if r.headers["content-type"].startswith("text/html"):
        assert "<title>wlb" in r.text
    else:
        assert r.json().get("ok") is True


def test_version(client: TestClient) -> None:
    r = client.get("/api/version")
    assert r.status_code == 200
    assert r.json().get("wlb")


def test_describe_lists_transports_and_capabilities(client: TestClient) -> None:
    r = client.get("/api/describe")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    data = body["data"]
    transport_names = {t["name"] for t in data["transports"]}
    assert {"ssh", "local", "http"}.issubset(transport_names)
    capability_names = {c["name"] for c in data["capabilities"]}
    # M3.3 adds 'web'
    assert "web" in capability_names


def test_status_uses_local_transport(client: TestClient) -> None:
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["transport"] == "local"


def test_profile_default(client: TestClient) -> None:
    r = client.get("/api/profile")
    assert r.status_code == 200
    body = r.json()
    assert body["profile_name"] == "default"
    assert body["primary_transport"] == "local"
    assert "ssh" in body and "http" in body


def test_maps_empty(client: TestClient) -> None:
    r = client.get("/api/maps")
    assert r.status_code == 200
    body = r.json()
    assert body["maps"] == []


def test_tools_empty_when_no_config(client: TestClient) -> None:
    r = client.get("/api/tools")
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["tools"] == []


def test_tools_show_unknown_returns_404(client: TestClient) -> None:
    r = client.get("/api/tools/does-not-exist")
    assert r.status_code == 404


def test_tools_show_known(client: TestClient, _isolated: Path) -> None:
    (_isolated / "wlb-tools.toml").write_text(
        '[tool.echo]\ninterpreter = "raw"\ncommand_template = "echo hi"\n',
        encoding="utf-8",
    )
    r = client.get("/api/tools/echo")
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["spec"]["interpreter"] == "raw"
