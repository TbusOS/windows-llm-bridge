"""Tests for wlb.infra.tools_config — TOML loading + validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from wlb.infra.tools_config import (
    ToolSpec,
    find_tool,
    load_tools,
    tools_file_path,
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect workspace + tools file to a tmp dir so tests are hermetic."""
    monkeypatch.setenv("WLB_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("WLB_TOOLS_FILE", raising=False)
    return tmp_path


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "wlb-tools.toml"
    p.write_text(body, encoding="utf-8")
    return p


# ─── path resolution ──────────────────────────────────────────────


def test_tools_file_path_defaults_to_workspace(_isolated: Path) -> None:
    assert tools_file_path() == _isolated / "wlb-tools.toml"


def test_tools_file_path_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    custom = tmp_path / "elsewhere.toml"
    monkeypatch.setenv("WLB_TOOLS_FILE", str(custom))
    assert tools_file_path() == custom


# ─── happy path ───────────────────────────────────────────────────


def test_load_missing_file_returns_empty(_isolated: Path) -> None:
    specs, warnings, path = load_tools()
    assert specs == []
    assert warnings == []
    assert path == _isolated / "wlb-tools.toml"


def test_load_single_tool(_isolated: Path) -> None:
    _write(_isolated, """
[tool.echo]
description       = "Echo test"
interpreter       = "cmd"
command_template  = 'echo {msg}'
args              = ["msg"]
timeout           = 15
""")
    specs, warnings, _ = load_tools()
    assert warnings == []
    assert len(specs) == 1
    s = specs[0]
    assert s.name == "echo"
    assert s.interpreter == "cmd"
    assert s.command_template == "echo {msg}"
    assert s.args == ["msg"]
    assert s.timeout == 15


def test_load_multiple_tools(_isolated: Path) -> None:
    _write(_isolated, """
[tool.a]
interpreter = "cmd"
command_template = 'a'

[tool.b]
interpreter = "powershell"
command_template = 'b'
""")
    specs, warnings, _ = load_tools()
    assert warnings == []
    assert {s.name for s in specs} == {"a", "b"}


def test_load_with_regex_section(_isolated: Path) -> None:
    _write(_isolated, """
[tool.flash]
interpreter = "raw"
command_template = 'flash'

[tool.flash.regex]
progress = '^(\\d{1,3})%'
success = 'OK'
failure = 'ERROR'
""")
    specs, _, _ = load_tools()
    s = specs[0]
    assert s.progress_re == r'^(\d{1,3})%'
    assert s.success_re == 'OK'
    assert s.failure_re == 'ERROR'


def test_load_with_workdir_and_allow_dangerous(_isolated: Path) -> None:
    _write(_isolated, r"""
[tool.t]
interpreter = "cmd"
command_template = 'x'
workdir = 'C:\stage'
allow_dangerous = true
""")
    specs, _, _ = load_tools()
    s = specs[0]
    assert s.workdir == r"C:\stage"
    assert s.allow_dangerous is True


def test_load_default_timeout_when_omitted(_isolated: Path) -> None:
    _write(_isolated, """
[tool.t]
interpreter = "cmd"
command_template = 'x'
""")
    specs, _, _ = load_tools()
    assert specs[0].timeout == 300


# ─── failure modes ────────────────────────────────────────────────


def test_load_corrupt_toml_produces_warning(_isolated: Path) -> None:
    _write(_isolated, "this = is = not = valid\n[unclosed")
    specs, warnings, _ = load_tools()
    assert specs == []
    assert len(warnings) == 1
    assert "failed to parse" in warnings[0]


def test_tool_with_bad_interpreter_skipped(_isolated: Path) -> None:
    _write(_isolated, """
[tool.bad]
interpreter = "fish"
command_template = 'x'

[tool.good]
interpreter = "cmd"
command_template = 'x'
""")
    specs, warnings, _ = load_tools()
    names = {s.name for s in specs}
    assert names == {"good"}
    assert any("interpreter" in w for w in warnings)


def test_tool_missing_command_template_skipped(_isolated: Path) -> None:
    _write(_isolated, """
[tool.x]
interpreter = "cmd"
""")
    specs, warnings, _ = load_tools()
    assert specs == []
    assert any("command_template" in w for w in warnings)


def test_tool_with_invalid_args_type_skipped(_isolated: Path) -> None:
    _write(_isolated, """
[tool.x]
interpreter = "cmd"
command_template = "x"
args = "not-a-list"
""")
    specs, warnings, _ = load_tools()
    assert specs == []
    assert any("args" in w for w in warnings)


def test_tool_with_zero_timeout_skipped(_isolated: Path) -> None:
    _write(_isolated, """
[tool.x]
interpreter = "cmd"
command_template = "x"
timeout = 0
""")
    specs, warnings, _ = load_tools()
    assert specs == []
    assert any("timeout" in w for w in warnings)


def test_tool_with_bad_name_rejected(_isolated: Path) -> None:
    _write(_isolated, """
[tool."with space"]
interpreter = "cmd"
command_template = "x"
""")
    specs, warnings, _ = load_tools()
    assert specs == []
    assert warnings  # at least one


def test_command_alias_works_as_command_template(_isolated: Path) -> None:
    """`command` is accepted as a synonym for `command_template`."""
    _write(_isolated, """
[tool.x]
interpreter = "cmd"
command = "echo legacy"
""")
    specs, warnings, _ = load_tools()
    assert specs[0].command_template == "echo legacy"
    assert warnings == []


# ─── find_tool ────────────────────────────────────────────────────


def test_find_tool_present(_isolated: Path) -> None:
    _write(_isolated, """
[tool.alpha]
interpreter = "cmd"
command_template = "x"
""")
    spec, _, _ = find_tool("alpha")
    assert isinstance(spec, ToolSpec)
    assert spec.name == "alpha"


def test_find_tool_absent(_isolated: Path) -> None:
    _write(_isolated, """
[tool.alpha]
interpreter = "cmd"
command_template = "x"
""")
    spec, _, _ = find_tool("beta")
    assert spec is None
