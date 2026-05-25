"""HTTP API: /api/skills + /api/skills/{name} (M3.11)."""

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


def _write_tools(tmp_path: Path, body: str) -> None:
    (tmp_path / "wlb-tools.toml").write_text(body, encoding="utf-8")


def test_list_skills_empty(client: TestClient) -> None:
    r = client.get("/api/skills")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["skills"] == []


def test_list_skills_returns_uri_per_tool(
    client: TestClient, tmp_path: Path,
) -> None:
    _write_tools(tmp_path, """
[tool.echo]
description = "trivial"
interpreter = "raw"
command_template = "echo hi"
""")
    r = client.get("/api/skills")
    assert r.status_code == 200
    skills = r.json()["data"]["skills"]
    assert len(skills) == 1
    assert skills[0]["skill_uri"] == "wlb-skill://echo"


def test_serve_skill_returns_text_markdown(
    client: TestClient, tmp_path: Path,
) -> None:
    _write_tools(tmp_path, """
[tool.echo]
description = "trivial"
interpreter = "raw"
command_template = "echo hi"
""")
    r = client.get("/api/skills/echo")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert r.text.startswith("# `echo`")
    assert "echo hi" in r.text


def test_serve_skill_404_when_missing(client: TestClient) -> None:
    r = client.get("/api/skills/ghost")
    assert r.status_code == 404
    body = r.json()
    # FastAPI nests our Result envelope under "detail" for non-2xx.
    assert body["detail"]["error"]["code"] == "TOOL_NOT_FOUND"


def test_serve_skill_json_variant_returns_envelope(
    client: TestClient, tmp_path: Path,
) -> None:
    _write_tools(tmp_path, """
[tool.echo]
interpreter = "raw"
command_template = "echo hi"
""")
    r = client.get("/api/skills/echo.json")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["name"] == "echo"
    assert body["data"]["skill_uri"] == "wlb-skill://echo"
    assert "# `echo`" in body["data"]["markdown"]


def test_serve_skill_includes_author_body_when_present(
    client: TestClient, tmp_path: Path,
) -> None:
    _write_tools(tmp_path, """
[tool.echo]
interpreter = "raw"
command_template = "echo hi"
""")
    body = tmp_path / "wlb-skills" / "echo.md"
    body.parent.mkdir(parents=True, exist_ok=True)
    body.write_text("Operator notes: avoid running while flasher is busy.")
    r = client.get("/api/skills/echo")
    assert r.status_code == 200
    assert "Notes from the operator" in r.text
    assert "avoid running while flasher is busy" in r.text
