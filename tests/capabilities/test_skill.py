"""skill capability — list/get + markdown rendering (M3.11)."""

from __future__ import annotations

from pathlib import Path

import pytest

from wlb.capabilities.skill import get_skill, list_skills


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WLB_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("WLB_TOOLS_FILE", raising=False)
    return tmp_path


def _write_tools(tmp_path: Path, body: str) -> None:
    (tmp_path / "wlb-tools.toml").write_text(body, encoding="utf-8")


# ─── list_skills ────────────────────────────────────────────────


async def test_list_returns_empty_with_no_tools_file(_isolated: Path) -> None:
    r = await list_skills()
    assert r.ok
    assert r.data is not None
    assert r.data["skills"] == []
    assert r.data["warnings"] == []
    assert r.data["tools_file"].endswith("wlb-tools.toml")


async def test_list_surfaces_every_declared_tool(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.echo]
description = "trivial"
interpreter = "raw"
command_template = "echo hi"

[tool.flasher]
description = "fake flasher"
interpreter = "cmd"
command_template = "flash {image} {port}"
args = ["image", "port"]
""")
    r = await list_skills()
    assert r.ok
    names = {s["name"] for s in r.data["skills"]}
    assert names == {"echo", "flasher"}

    for s in r.data["skills"]:
        assert s["skill_uri"] == f"wlb-skill://{s['name']}"
        assert s["has_author_body"] is False
        assert s["author_body_path"].endswith(f"wlb-skills/{s['name']}.md")


async def test_list_flags_tool_with_author_body(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.echo]
interpreter = "raw"
command_template = "echo hi"
""")
    body = _isolated / "wlb-skills" / "echo.md"
    body.parent.mkdir(parents=True, exist_ok=True)
    body.write_text("Operator notes here.")

    r = await list_skills()
    assert r.ok
    echo = next(s for s in r.data["skills"] if s["name"] == "echo")
    assert echo["has_author_body"] is True


# ─── get_skill ──────────────────────────────────────────────────


async def test_get_returns_tool_not_found_for_unknown(_isolated: Path) -> None:
    _write_tools(_isolated, "")
    r = await get_skill("ghost")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "TOOL_NOT_FOUND"
    assert "wlb skill list" in r.error.suggestion


async def test_get_renders_minimal_markdown_for_simple_tool(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.echo]
description = "Trivial smoke."
interpreter = "raw"
command_template = "echo hi"
""")
    r = await get_skill("echo")
    assert r.ok
    md = r.data["markdown"]

    # Required sections present in stable order.
    assert "# `echo`" in md
    assert "> Trivial smoke." in md
    assert "## Quick reference" in md
    assert "- **Interpreter**: `raw`" in md
    assert "- **Required args**: _(none)_" in md
    assert "## Command template" in md
    assert "echo hi" in md
    assert "## Example invocation (MCP)" in md
    # Without author body, a placeholder comment is included pointing
    # operators at the right file.
    assert "wlb-skills/echo.md" in md
    assert r.data["has_author_body"] is False


async def test_get_renders_args_workdir_and_regex(_isolated: Path) -> None:
    _write_tools(_isolated, r"""
[tool.flasher]
description = "Fake flasher."
interpreter = "cmd"
command_template = "flash {image} {port}"
args             = ["image", "port"]
timeout          = 600
workdir          = "C:\\stage"

[tool.flasher.regex]
progress = '^Progress:\s+(\d{1,3})%'
success  = '^Flash complete'
failure  = '^ERROR:'
""")
    r = await get_skill("flasher")
    assert r.ok
    md = r.data["markdown"]

    assert "- **Required args**: `image`, `port`" in md
    assert "- **Timeout**: 600s" in md
    assert "- **Workdir**: `C:\\stage`" in md
    assert "## Output parsing" in md
    assert "Progress regex" in md
    assert "Success regex" in md
    assert "Failure regex" in md
    # Example invocation JSON wraps each arg in a placeholder.
    assert '"image": "<image>"' in md
    assert '"port": "<port>"' in md


async def test_get_appends_author_body_when_present(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.echo]
interpreter = "raw"
command_template = "echo hi"
""")
    body = _isolated / "wlb-skills" / "echo.md"
    body.parent.mkdir(parents=True, exist_ok=True)
    body.write_text(
        "## Pre-flight\n\n"
        "1. Make sure the cable is in COMxx (changes every reboot).\n"
        "2. Stop the vendor service if it's holding the port.\n"
    )

    r = await get_skill("echo")
    assert r.ok
    md = r.data["markdown"]
    assert "## Notes from the operator" in md
    assert "Pre-flight" in md
    assert "COMxx" in md
    # No placeholder comment when an author body exists.
    assert "Drop a Markdown file" not in md
    assert r.data["has_author_body"] is True


async def test_allow_dangerous_surfaces_in_header(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "true"
allow_dangerous = true
""")
    r = await get_skill("t")
    assert r.ok
    assert "Allow dangerous" in r.data["markdown"]
