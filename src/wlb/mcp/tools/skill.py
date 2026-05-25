"""MCP tools + resource: wlb_skill_list / wlb_skill_get / wlb-skill://{name}.

The MCP Resources API is the canonical way to preload structured context
into an LLM client (Claude Code lists Resources in its "context"
panel; Cursor surfaces them on first connect). We expose every declared
tool's skill pack at ``wlb-skill://<tool-name>``.

Some clients still don't expose Resources in their UI, so we also
register two tools (``wlb_skill_list`` / ``wlb_skill_get``) so
discovery + fetch work via the always-supported Tools surface.
"""

from __future__ import annotations

from typing import Any

from wlb.capabilities.skill import get_skill as cap_get_skill
from wlb.capabilities.skill import list_skills as cap_list_skills


def register(mcp) -> None:  # noqa: ANN001 — FastMCP imported lazily
    # ─── tools ────────────────────────────────────────────────────

    @mcp.tool()
    async def wlb_skill_list() -> dict[str, Any]:
        """List every available skill pack (one per declared tool).

        Returns each tool's name + description + MCP resource URI
        (``wlb-skill://<name>``) so the agent can fetch the full
        markdown via Resources OR via ``wlb_skill_get(name)`` if
        the client doesn't surface Resources in its UI.

        Use this on first contact to learn what the operator has
        declared. Then preload the relevant skill packs into context
        instead of inferring usage from the terse `wlb_tool_show`
        description.

        Returns:
            Standard Result {ok, data: {tools_file, skills_dir,
            skills: [{name, description, interpreter, args, skill_uri,
            author_body_path, has_author_body}], warnings},
            error, artifacts, timing_ms}.
        """
        r = await cap_list_skills()
        return r.to_dict()

    @mcp.tool()
    async def wlb_skill_get(name: str) -> dict[str, Any]:
        """Fetch the full skill-pack markdown for one tool.

        Use this when the calling client doesn't surface MCP Resources
        in its UI, or when you want to pull a specific skill into the
        conversation mid-session. The returned markdown is the same
        text the ``wlb-skill://<name>`` resource serves.

        Args:
            name: tool name as declared in ``wlb-tools.toml``.

        Returns:
            Standard Result {ok, data: {name, skill_uri, markdown,
            author_body_path, has_author_body, warnings}, error,
            artifacts, timing_ms}.

            On failure, ``error.code = "TOOL_NOT_FOUND"`` with a
            suggestion pointing at ``wlb_skill_list``.
        """
        r = await cap_get_skill(name)
        return r.to_dict()

    # ─── resource (canonical preload surface) ─────────────────────
    #
    # Templated URI: clients call resources/templates/list to discover
    # the {name} parameter, then resources/read with a concrete URI.

    @mcp.resource(
        "wlb-skill://{name}",
        name="wlb-skill",
        description=(
            "Per-tool skill pack — auto-generated header (interpreter, "
            "args, command template, output regex) plus optional "
            "operator-written notes from workspace/wlb-skills/<name>.md."
        ),
        mime_type="text/markdown",
    )
    async def wlb_skill_resource(name: str) -> str:
        """Serve the markdown body for one skill pack."""
        r = await cap_get_skill(name)
        if not r.ok or r.data is None:
            # MCP resources can't return error envelopes; raise so the
            # client sees a proper resource error.
            raise FileNotFoundError(
                (r.error.message if r.error else f"no skill for {name!r}")
            )
        return r.data["markdown"]
