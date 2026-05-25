"""``wlb skill`` — list + show per-tool skill packs (M3.11).

Skill packs are operator-curated guidance bundles an LLM client can
preload before invoking a declared tool. They live as one Markdown file
per tool: auto-generated header from the :class:`ToolSpec` plus an
optional operator-written body at ``workspace/wlb-skills/<name>.md``.

This CLI surfaces the same data MCP serves via ``wlb_skill_list`` /
``wlb_skill_get`` / the ``wlb-skill://<name>`` resource template — so an
operator can preview exactly what an agent will see.
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from wlb.capabilities.skill import get_skill as cap_get_skill
from wlb.capabilities.skill import list_skills as cap_list_skills
from wlb.cli.common import print_result, run_async

app = typer.Typer(
    help="List + show per-tool skill packs an LLM client can preload.",
    no_args_is_help=True,
)
console = Console()


@app.command("list")
def list_skills(ctx: typer.Context) -> None:
    """List every available skill pack."""
    result = run_async(cap_list_skills())

    if (ctx.obj or {}).get("json"):
        print(json.dumps(result.to_dict(), indent=2, default=str))
        return

    data = result.data or {}
    skills = data.get("skills", [])
    warnings = data.get("warnings", [])
    tools_file = data.get("tools_file", "<unknown>")
    skills_dir = data.get("skills_dir", "<unknown>")

    if not skills:
        console.print(f"[yellow]no tools declared[/] in {tools_file}")
        console.print(
            "Declare a tool first (see [bold]wlb-tools.example.toml[/]); "
            "the skill follows automatically."
        )
    else:
        table = Table(title=f"wlb skill list  ({tools_file})")
        table.add_column("name")
        table.add_column("interpreter")
        table.add_column("args")
        table.add_column("resource URI")
        table.add_column("author body", justify="center")
        for s in skills:
            table.add_row(
                s["name"],
                s["interpreter"],
                ", ".join(s["args"]) or "—",
                s["skill_uri"],
                "✓" if s["has_author_body"] else "—",
            )
        console.print(table)
        console.print(
            f"\n[dim]Operator-authored extensions live under "
            f"[bold]{skills_dir}/<name>.md[/].[/]"
        )

    for w in warnings:
        console.print(f"[yellow]warning:[/] {w}")


@app.command("show")
def show_skill(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Tool name to show the skill for."),
    raw: bool = typer.Option(
        False, "--raw", help="Print raw Markdown (no rendering)."
    ),
) -> None:
    """Render one skill pack to the terminal.

    Without ``--raw`` the body is rendered with Rich (headings, lists,
    code fences). Use ``--raw`` when piping into a file or a clipboard
    tool so the markdown stays intact.
    """
    result = run_async(cap_get_skill(name))

    if (ctx.obj or {}).get("json"):
        print(json.dumps(result.to_dict(), indent=2, default=str))
        return

    if not result.ok:
        print_result(ctx, result)
        raise typer.Exit(code=1)

    md = result.data["markdown"]
    if raw:
        # print() so stdout stays free of Rich escapes for redirection.
        print(md)
    else:
        console.print(Markdown(md))
