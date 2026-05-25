"""wlb_skill_list / wlb_skill_get + wlb-skill://{name} resource (M3.11)."""

from __future__ import annotations

from pathlib import Path

import pytest


class _MockMcp:
    """Captures @tool and @resource registrations for assertion + invocation."""

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}
        self.resources: dict[str, object] = {}

    def tool(self):                          # noqa: ANN202
        def deco(fn):                         # noqa: ANN202
            self.tools[fn.__name__] = fn
            return fn
        return deco

    def resource(self, uri, **kw):           # noqa: ANN001, ANN202
        def deco(fn):                         # noqa: ANN202
            self.resources[uri] = fn
            return fn
        return deco


@pytest.fixture
def registered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _MockMcp:
    monkeypatch.setenv("WLB_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("WLB_TOOLS_FILE", raising=False)
    from wlb.mcp.tools.skill import register
    mock = _MockMcp()
    register(mock)
    return mock


def _write_tools(tmp_path: Path, body: str) -> None:
    (tmp_path / "wlb-tools.toml").write_text(body, encoding="utf-8")


# ─── wlb_skill_list ─────────────────────────────────────────────


async def test_skill_list_empty_when_no_tools(registered: _MockMcp) -> None:
    r = await registered.tools["wlb_skill_list"]()
    assert r["ok"] is True
    assert r["data"]["skills"] == []


async def test_skill_list_returns_uri_per_tool(
    registered: _MockMcp, tmp_path: Path,
) -> None:
    _write_tools(tmp_path, """
[tool.echo]
interpreter = "raw"
command_template = "echo hi"
""")
    r = await registered.tools["wlb_skill_list"]()
    assert r["ok"] is True
    skills = r["data"]["skills"]
    assert len(skills) == 1
    assert skills[0]["name"] == "echo"
    assert skills[0]["skill_uri"] == "wlb-skill://echo"


# ─── wlb_skill_get ──────────────────────────────────────────────


async def test_skill_get_returns_markdown(
    registered: _MockMcp, tmp_path: Path,
) -> None:
    _write_tools(tmp_path, """
[tool.echo]
description = "trivial"
interpreter = "raw"
command_template = "echo hi"
""")
    r = await registered.tools["wlb_skill_get"]("echo")
    assert r["ok"] is True
    assert r["data"]["name"] == "echo"
    assert "# `echo`" in r["data"]["markdown"]
    assert r["data"]["has_author_body"] is False


async def test_skill_get_unknown_tool_returns_tool_not_found(
    registered: _MockMcp,
) -> None:
    r = await registered.tools["wlb_skill_get"]("ghost")
    assert r["ok"] is False
    assert r["error"]["code"] == "TOOL_NOT_FOUND"
    assert "wlb skill list" in r["error"]["suggestion"]


# ─── wlb-skill://{name} resource handler ───────────────────────


async def test_skill_resource_template_registered(registered: _MockMcp) -> None:
    assert "wlb-skill://{name}" in registered.resources


async def test_skill_resource_handler_returns_raw_markdown(
    registered: _MockMcp, tmp_path: Path,
) -> None:
    _write_tools(tmp_path, """
[tool.echo]
description = "trivial"
interpreter = "raw"
command_template = "echo hi"
""")
    handler = registered.resources["wlb-skill://{name}"]
    md = await handler("echo")
    assert isinstance(md, str)
    assert md.startswith("# `echo`")
    assert "echo hi" in md


async def test_skill_resource_handler_raises_on_missing_tool(
    registered: _MockMcp,
) -> None:
    handler = registered.resources["wlb-skill://{name}"]
    with pytest.raises(FileNotFoundError):
        await handler("ghost")
