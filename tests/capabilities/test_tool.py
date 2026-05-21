"""tool capability tests — parameterized over LocalTransport.

Because LocalTransport runs real subprocesses (sh on Linux), these are
genuine end-to-end exercises of the tool runner: TOML loading, arg
substitution + validation, transport call, log writing, regex parsing,
verdict logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wlb.capabilities.tool import list_tools, run_tool, show_tool
from wlb.transport.local import LocalTransport


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WLB_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("WLB_TOOLS_FILE", raising=False)
    return tmp_path


def _write_tools(tmp_path: Path, body: str) -> None:
    (tmp_path / "wlb-tools.toml").write_text(body, encoding="utf-8")


# ─── list / show ──────────────────────────────────────────────────


async def test_list_tools_empty_when_no_file(_isolated: Path) -> None:
    r = await list_tools()
    assert r.ok
    assert r.data is not None
    assert r.data["tools"] == []
    assert r.data["warnings"] == []


async def test_list_tools_reports_specs(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.echo]
description = "echo"
interpreter = "raw"
command_template = "echo hi"
""")
    r = await list_tools()
    assert r.ok
    assert r.data is not None
    names = [t["name"] for t in r.data["tools"]]
    assert names == ["echo"]


async def test_show_tool_returns_full_spec(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.echo]
description = "echo"
interpreter = "raw"
command_template = "echo {x}"
args = ["x"]
""")
    r = await show_tool("echo")
    assert r.ok
    assert r.data is not None
    assert r.data["spec"]["command_template"] == "echo {x}"


async def test_show_tool_missing(_isolated: Path) -> None:
    r = await show_tool("never-declared")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "TOOL_NOT_FOUND"


# ─── run: happy path ──────────────────────────────────────────────


async def test_run_tool_happy_path(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.echo]
interpreter = "raw"
command_template = "echo wlb-tool-echo: {msg}"
args = ["msg"]

[tool.echo.regex]
success = "wlb-tool-echo:"
""")
    r = await run_tool(LocalTransport(), "echo", {"msg": "hello"})
    assert r.ok, r
    assert r.data is not None
    assert r.data.success is True
    assert r.data.success_match == "wlb-tool-echo:"
    assert "hello" in r.data.stdout_tail
    assert r.data.exit_code == 0
    # Log file artifact present
    assert r.data.log_path.endswith(".log")
    assert Path(r.data.log_path).exists()


async def test_run_tool_log_contains_full_output(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.echo]
interpreter = "raw"
command_template = "echo first; echo second"
""")
    # Note: the test TOML uses a semicolon, which our arg-filter doesn't apply
    # to the static template — only to user-supplied arg VALUES.
    r = await run_tool(LocalTransport(), "echo", {})
    assert r.ok, r
    log_text = Path(r.data.log_path).read_text(encoding="utf-8")  # type: ignore[union-attr]
    assert "first" in log_text
    assert "second" in log_text


# ─── run: arg validation ──────────────────────────────────────────


async def test_run_tool_missing_required_arg(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "echo {needed}"
args = ["needed"]
""")
    r = await run_tool(LocalTransport(), "t", {})
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "TOOL_ARG_MISSING"
    assert "needed" in str(r.error.details.get("missing", []))


async def test_run_tool_template_placeholder_uncovered(_isolated: Path) -> None:
    """A template placeholder not in spec.args still raises if not supplied."""
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "echo {x} {y}"
args = ["x"]
""")
    r = await run_tool(LocalTransport(), "t", {"x": "1"})
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "TOOL_ARG_MISSING"


@pytest.mark.parametrize("bad", [
    "value;rm -rf /",
    "with\nnewline",
    "back`ticks",
    "with$dollar",
    "pipe|me",
])
async def test_run_tool_rejects_shell_meta_in_arg(_isolated: Path, bad: str) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "echo {x}"
args = ["x"]
""")
    r = await run_tool(LocalTransport(), "t", {"x": bad})
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "TOOL_ARG_INVALID"


async def test_run_tool_rejects_non_string_arg(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "echo {x}"
args = ["x"]
""")
    r = await run_tool(LocalTransport(), "t", {"x": 123})   # type: ignore[dict-item]
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "TOOL_ARG_INVALID"


# ─── run: not found ───────────────────────────────────────────────


async def test_run_tool_unknown_name(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.a]
interpreter = "cmd"
command_template = "x"
""")
    r = await run_tool(LocalTransport(), "b", {})
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "TOOL_NOT_FOUND"


# ─── run: regex parsing ───────────────────────────────────────────


async def test_run_tool_progress_regex_finds_last_percent(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "printf '0%%\\n25%%\\n50%%\\n75%%\\n100%%\\n'"

[tool.t.regex]
progress = '^(\\d{1,3})%'
""")
    r = await run_tool(LocalTransport(), "t", {})
    assert r.ok, r
    assert r.data is not None
    assert r.data.progress_percent == 100


async def test_run_tool_failure_regex_overrides_zero_exit(_isolated: Path) -> None:
    """Even with exit 0, a failure_re match marks the run as failed."""
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "echo BIG-ERROR: something bad"

[tool.t.regex]
failure = '^BIG-ERROR:'
""")
    r = await run_tool(LocalTransport(), "t", {})
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "TOOL_FAILED"
    assert "BIG-ERROR" in str(r.error.details.get("failure_match", ""))


async def test_run_tool_success_regex_missing_marks_failure(_isolated: Path) -> None:
    """If success_re is declared but doesn't match, the run is a failure."""
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "echo wrong-marker"

[tool.t.regex]
success = '^expected-marker$'
""")
    r = await run_tool(LocalTransport(), "t", {})
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "TOOL_FAILED"


async def test_run_tool_nonzero_exit_when_no_regex(_isolated: Path) -> None:
    _write_tools(_isolated, """
[tool.t]
interpreter = "raw"
command_template = "false"
""")
    r = await run_tool(LocalTransport(), "t", {})
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "TOOL_FAILED"
    assert r.error.details["exit_code"] != 0
