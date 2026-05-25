"""skill capability — per-tool "skill packs" for LLM clients to preload (M3.11).

A "skill pack" is a small Markdown document that describes how to use one
declared :class:`wlb.infra.tools_config.ToolSpec` from an LLM client's
perspective: what the tool is for, what args it needs, when to reach for
it, what the success / failure signals look like. LLMs perform much
better when this kind of context is preloaded once instead of inferred
turn-by-turn from a terse tool description.

Two layers:

1. **Auto-generated header.** Built deterministically from the ToolSpec —
   name, description, interpreter, command template, args, regex hits.
   Operators get this for free for every declared tool. The header is
   stable so clients can cache and diff.

2. **Author body.** Optional operator-written extension at
   ``workspace/wlb-skills/<tool-name>.md``. Appended to the auto-generated
   header. This is where the operator captures the local knowledge an
   LLM would otherwise have to guess at: pre-flight steps, recovery
   recipes, links to vendor docs, "the COM port number changes when you
   unplug the USB hub", etc.

The author body is plain Markdown; wlb does not parse or modify it.

Discovery surfaces (all share this capability):

- **MCP Resource** ``wlb-skill://<tool-name>`` — canonical preload
  surface, exposed via FastMCP's resource decorator.
- **MCP Tool** ``wlb_skill_list`` / ``wlb_skill_get`` — for clients that
  don't surface MCP resources in their UI yet.
- **CLI** ``wlb skill list`` / ``wlb skill show <name>``.
- **HTTP API** ``GET /api/skills`` / ``GET /api/skills/<name>``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wlb.infra.result import Result, fail, ok
from wlb.infra.tools_config import ToolSpec, find_tool, load_tools
from wlb.infra.workspace import workspace_root


def _author_body_path(name: str) -> Path:
    """Where the optional operator-written extension lives.

    ``workspace/wlb-skills/<name>.md`` — same workspace tree everything
    else lives under. The directory is created on demand; missing means
    "no author body" (not an error).
    """
    return workspace_root() / "wlb-skills" / f"{name}.md"


def _load_author_body(name: str) -> str | None:
    p = _author_body_path(name)
    if not p.exists() or not p.is_file():
        return None
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None
    return text.strip() or None


def _render_skill(spec: ToolSpec, author_body: str | None) -> str:
    """Render a complete skill markdown for ``spec``.

    The header layout is intentionally simple and stable — LLM clients
    and operators both need to parse it visually. Don't add fancy
    formatting that changes line counts; keep the structure greppable.
    """
    lines: list[str] = []
    lines.append(f"# `{spec.name}`")
    lines.append("")
    if spec.description:
        lines.append(f"> {spec.description}")
        lines.append("")

    # ── Quick reference ────────────────────────────────────────
    lines.append("## Quick reference")
    lines.append("")
    lines.append(f"- **Interpreter**: `{spec.interpreter}`")
    if spec.args:
        lines.append(f"- **Required args**: {', '.join(f'`{a}`' for a in spec.args)}")
    else:
        lines.append("- **Required args**: _(none)_")
    lines.append(f"- **Timeout**: {spec.timeout}s")
    if spec.workdir:
        lines.append(f"- **Workdir**: `{spec.workdir}`")
    if spec.allow_dangerous:
        lines.append("- **Allow dangerous**: yes — bypasses ASK-level permission rules")
    lines.append("")

    # ── Command template ───────────────────────────────────────
    lines.append("## Command template")
    lines.append("")
    lines.append("```")
    lines.append(spec.command_template)
    lines.append("```")
    lines.append("")

    # ── Output parsing ─────────────────────────────────────────
    if any((spec.progress_re, spec.success_re, spec.failure_re)):
        lines.append("## Output parsing")
        lines.append("")
        if spec.progress_re:
            lines.append(f"- **Progress regex** (group 1 = percent): `{spec.progress_re}`")
        if spec.success_re:
            lines.append(f"- **Success regex**: `{spec.success_re}`")
        if spec.failure_re:
            lines.append(f"- **Failure regex**: `{spec.failure_re}`")
        lines.append("")

    # ── Example MCP invocation ─────────────────────────────────
    lines.append("## Example invocation (MCP)")
    lines.append("")
    lines.append("```json")
    if spec.args:
        example_args = {a: f"<{a}>" for a in spec.args}
        lines.append(_json_pretty({"name": spec.name, "args": example_args}))
    else:
        lines.append(_json_pretty({"name": spec.name, "args": {}}))
    lines.append("```")
    lines.append("")

    # ── How wlb runs this ──────────────────────────────────────
    lines.append("## How wlb runs this")
    lines.append("")
    lines.append(
        "1. Substitutes `args` into the template via `str.format_map`."
    )
    if spec.workdir:
        lines.append(
            f"2. Wraps the command in a workdir prefix that switches to "
            f"`{spec.workdir}` before running and pops back after."
        )
        step = 3
    else:
        step = 2
    lines.append(
        f"{step}. Runs the result through the `{spec.interpreter}` interpreter "
        "on the active transport (ssh / local / http)."
    )
    lines.append(
        f"{step + 1}. Saves the full stdout+stderr to "
        f"`workspace/hosts/<host>/tools/{spec.name}/<ts>.log`."
    )
    if any((spec.progress_re, spec.success_re, spec.failure_re)):
        lines.append(
            f"{step + 2}. Parses the output against the regex above to "
            "decide the final verdict (success / failure / progress)."
        )
    lines.append("")

    # ── Author body (operator-written) ─────────────────────────
    if author_body:
        lines.append("## Notes from the operator")
        lines.append("")
        lines.append(author_body)
        lines.append("")
    else:
        lines.append(
            "<!-- Drop a Markdown file at "
            f"`workspace/wlb-skills/{spec.name}.md` to extend this skill "
            "with operator-authored guidance (pre-flight steps, recovery "
            "recipes, gotchas). -->"
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _json_pretty(obj: Any) -> str:
    """Stable two-space JSON for the skill examples (no external dep)."""
    import json
    return json.dumps(obj, indent=2, ensure_ascii=False)


# ─── public API ─────────────────────────────────────────────────


async def list_skills() -> Result[dict[str, Any]]:
    """Enumerate every declared tool, surfacing whether each has an author body.

    The list is the same shape as :func:`wlb.capabilities.tool.list_tools`
    but augmented with ``skill_uri`` (the MCP resource URI a client can
    fetch) and ``has_author_body`` (True if an operator dropped a file
    at ``workspace/wlb-skills/<name>.md``).
    """
    specs, warnings, tools_path = load_tools()
    skills_root = workspace_root() / "wlb-skills"
    items = []
    for s in specs:
        items.append({
            "name": s.name,
            "description": s.description,
            "interpreter": s.interpreter,
            "args": list(s.args),
            "skill_uri": f"wlb-skill://{s.name}",
            "author_body_path": str(_author_body_path(s.name)),
            "has_author_body": _author_body_path(s.name).exists(),
        })
    return ok(data={
        "tools_file": str(tools_path),
        "skills_dir": str(skills_root),
        "skills": items,
        "warnings": warnings,
    })


async def get_skill(name: str) -> Result[dict[str, Any]]:
    """Return the full skill markdown for ``name``.

    Returns the auto-generated header + the optional author body merged.
    If the tool isn't declared, surfaces ``TOOL_NOT_FOUND`` so the
    caller can suggest ``wlb skill list``.
    """
    spec, warnings, tools_path = find_tool(name)
    if spec is None:
        return fail(
            code="TOOL_NOT_FOUND",
            message=f"no tool named {name!r} in {tools_path}",
            suggestion=(
                "Run `wlb skill list` (or `wlb tool list`) to see what's "
                "declared. Skills are per-tool — declare the tool first, "
                "the skill follows."
            ),
            category="tool",
            details={"tool": name, "tools_file": str(tools_path), "warnings": warnings},
        )

    author_body = _load_author_body(name)
    markdown = _render_skill(spec, author_body=author_body)
    return ok(data={
        "name": spec.name,
        "skill_uri": f"wlb-skill://{spec.name}",
        "markdown": markdown,
        "author_body_path": str(_author_body_path(name)),
        "has_author_body": author_body is not None,
        "warnings": warnings,
    })
