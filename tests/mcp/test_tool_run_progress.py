"""wlb_tool_run — progress-notification wiring (M3.10).

Drives the MCP tool wrapper with a fake fastmcp.Context-shaped object,
capturing every report_progress / info / warning call. The capability
layer is tested separately in tests/capabilities/test_run_tool_with_progress.py;
this file is strictly about the MCP wrapper's translation of
ToolStreamEvent → ctx calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ─── fake fastmcp.Context that records all calls ─────────────────


class _FakeContext:
    """Capture every fastmcp.Context method the wrapper calls.

    Storage attrs are deliberately suffixed ``_calls`` so they don't
    shadow the method names on this class — ``ctx.info(...)`` must hit
    the method, not a list attribute.
    """

    def __init__(self) -> None:
        self.progress_calls: list[tuple[float, float | None, str | None]] = []
        self.info_calls: list[str] = []
        self.warning_calls: list[str] = []

    async def report_progress(
        self, progress: float, total: float | None = None, message: str | None = None,
    ) -> None:
        self.progress_calls.append((progress, total, message))

    async def info(self, message: str, **extra: object) -> None:
        self.info_calls.append(message)

    async def warning(self, message: str, **extra: object) -> None:
        self.warning_calls.append(message)


# ─── harness: register MCP tools onto a mock + extract wlb_tool_run ──


class _MockMcp:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self):                          # noqa: ANN202 — duck types fastmcp
        def deco(fn):                         # noqa: ANN202
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture
def wlb_tool_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("WLB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("WLB_TRANSPORT", "local")
    monkeypatch.delenv("WLB_TOOLS_FILE", raising=False)
    monkeypatch.delenv("WLB_PROFILE", raising=False)
    from wlb.mcp.tools.tool import register
    mock = _MockMcp()
    register(mock)
    return mock.tools["wlb_tool_run"]


def _write_tools(tmp_path: Path, body: str) -> None:
    (tmp_path / "wlb-tools.toml").write_text(body, encoding="utf-8")


# ─── tests ───────────────────────────────────────────────────────


async def test_no_context_falls_back_to_oneshot_path(
    wlb_tool_run, tmp_path: Path,
) -> None:
    """ctx=None → wrapper uses run_tool (single-shot); no progress fires."""
    _write_tools(tmp_path, """
[tool.echo]
interpreter = "raw"
command_template = "echo hi"
""")
    result = await wlb_tool_run("echo", {}, ctx=None)
    assert result["ok"] is True
    # No progress object to inspect — verifying behavior is "didn't raise".


async def test_progress_emitted_for_each_progress_event(
    wlb_tool_run, tmp_path: Path,
) -> None:
    _write_tools(tmp_path, """
[tool.echo]
interpreter = "raw"
command_template = "printf '25%%\\n50%%\\n75%%\\n100%%\\n'"

[tool.echo.regex]
progress = '^(\\d{1,3})%'
""")
    ctx = _FakeContext()
    result = await wlb_tool_run("echo", {}, ctx=ctx)
    assert result["ok"] is True

    # Each progress regex hit should fire one report_progress call, plus
    # one final 100% on done. Real percentages reported include 25/50/75/100;
    # the done event then forces a final 100.
    percents = [round(p[0]) for p in ctx.progress_calls]
    assert 25 in percents
    assert 50 in percents
    assert 75 in percents
    # Final entry is the 100% "done" cap.
    assert percents[-1] == 100
    assert "done" in (ctx.progress_calls[-1][2] or "")
    # Every call carries total=100.0.
    assert all(p[1] == 100.0 for p in ctx.progress_calls)


async def test_failure_match_emits_ctx_warning(
    wlb_tool_run, tmp_path: Path,
) -> None:
    _write_tools(tmp_path, """
[tool.broken]
interpreter = "raw"
command_template = "echo head; echo ERROR: kaboom; echo tail"

[tool.broken.regex]
failure = '^ERROR:'
""")
    ctx = _FakeContext()
    result = await wlb_tool_run("broken", {}, ctx=ctx)
    assert result["ok"] is False
    assert result["error"]["code"] == "TOOL_FAILED"

    # The warning includes the failure match text.
    assert any("ERROR:" in w for w in ctx.warning_calls)


async def test_success_match_emits_ctx_info(
    wlb_tool_run, tmp_path: Path,
) -> None:
    _write_tools(tmp_path, """
[tool.t]
interpreter = "raw"
command_template = "echo before; echo OK"

[tool.t.regex]
success = '^OK$'
""")
    ctx = _FakeContext()
    result = await wlb_tool_run("t", {}, ctx=ctx)
    assert result["ok"] is True
    assert any("success pattern matched" in m for m in ctx.info_calls)


async def test_milestone_line_count_emits_info_every_50_lines(
    wlb_tool_run, tmp_path: Path,
) -> None:
    # printf with 120 lines → 2 milestones (at 50 and 100).
    _write_tools(tmp_path, r"""
[tool.many]
interpreter = "raw"
command_template = "for i in $(seq 1 120); do echo line-$i; done"
""")
    ctx = _FakeContext()
    result = await wlb_tool_run("many", {}, ctx=ctx)
    assert result["ok"] is True

    line_milestones = [m for m in ctx.info_calls if "lines streamed" in m]
    assert len(line_milestones) == 2          # 50, 100
    assert "50 lines" in line_milestones[0]
    assert "100 lines" in line_milestones[1]


async def test_done_always_caps_progress_at_100(
    wlb_tool_run, tmp_path: Path,
) -> None:
    """Even without any progress regex, the wrapper sends a final 100%."""
    _write_tools(tmp_path, """
[tool.silent]
interpreter = "raw"
command_template = "echo hi"
""")
    ctx = _FakeContext()
    result = await wlb_tool_run("silent", {}, ctx=ctx)
    assert result["ok"] is True

    # Exactly one progress call from the "done" cap.
    assert len(ctx.progress_calls) == 1
    assert ctx.progress_calls[0][0] == 100.0
    assert ctx.progress_calls[0][1] == 100.0
