"""CLI smoke: ``wlb skill list`` / ``wlb skill show`` (M3.11)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wlb.cli.main import app


runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WLB_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("WLB_TOOLS_FILE", raising=False)
    monkeypatch.delenv("WLB_PROFILE", raising=False)
    return tmp_path


def _write_tools(tmp_path: Path, body: str) -> None:
    (tmp_path / "wlb-tools.toml").write_text(body, encoding="utf-8")


# ─── wlb skill list ────────────────────────────────────────────


def test_skill_list_when_no_tools_declared(tmp_path: Path) -> None:
    result = runner.invoke(app, ["skill", "list"])
    assert result.exit_code == 0, result.output
    assert "no tools declared" in result.output


def test_skill_list_shows_each_tool_with_uri(tmp_path: Path) -> None:
    _write_tools(tmp_path, """
[tool.echo]
interpreter = "raw"
command_template = "echo hi"

[tool.flasher]
interpreter = "cmd"
command_template = "flash {image}"
args = ["image"]
""")
    result = runner.invoke(app, ["skill", "list"])
    assert result.exit_code == 0, result.output
    assert "echo" in result.output
    assert "flasher" in result.output
    assert "wlb-skill://echo" in result.output
    assert "wlb-skill://flasher" in result.output


def test_skill_list_json_mode(tmp_path: Path) -> None:
    _write_tools(tmp_path, """
[tool.echo]
interpreter = "raw"
command_template = "echo hi"
""")
    result = runner.invoke(app, ["--json", "skill", "list"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert len(payload["data"]["skills"]) == 1
    assert payload["data"]["skills"][0]["skill_uri"] == "wlb-skill://echo"


# ─── wlb skill show ────────────────────────────────────────────


def test_skill_show_unknown_tool_exits_nonzero(tmp_path: Path) -> None:
    result = runner.invoke(app, ["skill", "show", "ghost"])
    assert result.exit_code != 0
    assert "TOOL_NOT_FOUND" in result.output or "not declared" in result.output \
        or "no tool named" in result.output


def test_skill_show_raw_outputs_markdown_for_redirection(tmp_path: Path) -> None:
    _write_tools(tmp_path, """
[tool.echo]
description = "trivial"
interpreter = "raw"
command_template = "echo hi"
""")
    result = runner.invoke(app, ["skill", "show", "echo", "--raw"])
    assert result.exit_code == 0, result.output
    # Raw markdown is suitable for piping into a file; verify the
    # signature markdown elements are present unmodified.
    assert "# `echo`" in result.output
    assert "## Quick reference" in result.output
    assert "echo hi" in result.output


def test_skill_show_renders_author_body(tmp_path: Path) -> None:
    _write_tools(tmp_path, """
[tool.echo]
interpreter = "raw"
command_template = "echo hi"
""")
    body = tmp_path / "wlb-skills" / "echo.md"
    body.parent.mkdir(parents=True, exist_ok=True)
    body.write_text("Operator pre-flight: unplug the dongle first.")
    result = runner.invoke(app, ["skill", "show", "echo", "--raw"])
    assert result.exit_code == 0, result.output
    assert "## Notes from the operator" in result.output
    assert "unplug the dongle" in result.output
