"""``wlb tool`` — list / show / run named tools declared in wlb-tools.toml."""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from wlb.capabilities.tool import (
    list_tools as cap_list_tools,
    run_tool as cap_run_tool,
    run_tool_stream as cap_run_tool_stream,
    show_tool as cap_show_tool,
)
from wlb.cli.common import get_transport, print_result, run_async

app = typer.Typer(help="Run named tools declared in wlb-tools.toml.", no_args_is_help=True)
console = Console()


@app.command("list")
def list_tools(ctx: typer.Context) -> None:
    """List every tool declared in ``workspace/wlb-tools.toml``."""
    result = run_async(cap_list_tools())

    if (ctx.obj or {}).get("json"):
        import json
        print(json.dumps(result.to_dict(), indent=2, default=str))
        return

    data = result.data or {}
    tools = data.get("tools", [])
    warnings = data.get("warnings", [])
    path = data.get("tools_file", "<unknown>")

    if not tools:
        console.print(f"[yellow]no tools declared[/] in {path}")
        console.print(
            "Copy [bold]wlb-tools.example.toml[/] as a starting point and edit."
        )
    else:
        table = Table(title=f"wlb tool list  ({path})")
        table.add_column("name")
        table.add_column("interpreter")
        table.add_column("args")
        table.add_column("timeout", justify="right")
        table.add_column("description")
        for t in tools:
            table.add_row(
                t["name"],
                t["interpreter"],
                ", ".join(t["args"]) or "—",
                f"{t['timeout']}s",
                t.get("description", "") or "—",
            )
        console.print(table)

    for w in warnings:
        console.print(f"[yellow]warning:[/] {w}")


@app.command("show")
def show_tool(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Tool name to inspect."),
) -> None:
    """Print the full spec for one tool."""
    result = run_async(cap_show_tool(name))

    if (ctx.obj or {}).get("json"):
        import json
        print(json.dumps(result.to_dict(), indent=2, default=str))
        return

    if not result.ok:
        print_result(ctx, result)
        return

    data = result.data or {}
    spec = data.get("spec", {})

    import tomli_w
    toml_text = tomli_w.dumps({"tool": {name: _spec_to_toml(spec)}})
    console.print(
        Panel.fit(
            Syntax(toml_text, "toml", theme="ansi_dark"),
            title=f"{name}  ({data.get('tools_file')})",
            border_style="cyan",
        )
    )


def _spec_to_toml(spec: dict[str, Any]) -> dict[str, Any]:
    """Reshape the spec dict into the TOML schema we read FROM (round-trippable)."""
    out: dict[str, Any] = {
        "description": spec.get("description", ""),
        "interpreter": spec.get("interpreter", "cmd"),
        "command_template": spec.get("command_template", ""),
        "args": list(spec.get("args") or []),
        "timeout": spec.get("timeout", 300),
        "allow_dangerous": spec.get("allow_dangerous", False),
    }
    if spec.get("workdir"):
        out["workdir"] = spec["workdir"]
    rx = spec.get("regex") or {}
    rx_clean = {k: v for k, v in rx.items() if v}
    if rx_clean:
        out["regex"] = rx_clean
    return out


@app.command("run")
def run(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Tool name to run."),
    arg: list[str] = typer.Option(
        None,
        "--arg",
        "-a",
        help="Template argument as key=value. Repeat for multiple args. "
             "Example: --arg image=C:\\stage\\fw.bin --arg port=COM3",
    ),
    stream: bool = typer.Option(
        False,
        "--stream",
        "-s",
        help="Stream output line-by-line. Live progress / match / line events "
             "print as they arrive; a final summary follows.",
    ),
) -> None:
    """Run a declared tool with the given args."""
    parsed: dict[str, str] = {}
    for raw in arg or []:
        if "=" not in raw:
            raise typer.BadParameter(
                f"--arg {raw!r}: expected key=value", param_hint="--arg"
            )
        k, _, v = raw.partition("=")
        parsed[k.strip()] = v
    transport = get_transport(ctx)

    if not stream:
        result = run_async(cap_run_tool(transport, name, parsed))
        print_result(ctx, result)
        return

    # ── streaming path ──────────────────────────────────────────
    import asyncio
    json_mode = bool((ctx.obj or {}).get("json"))

    async def _consume() -> int:
        last_pct: int | None = None
        async for ev in cap_run_tool_stream(transport, name, parsed):
            if json_mode:
                import json
                print(json.dumps(ev.to_dict(), default=str), flush=True)
                if ev.kind == "done":
                    return 0 if ev.ok else 1
                continue

            if ev.kind == "line":
                if ev.stream == "stderr":
                    console.print(f"[yellow]{ev.line}[/]", highlight=False)
                else:
                    console.print(ev.line or "", highlight=False)
            elif ev.kind == "progress":
                if ev.percent != last_pct:
                    last_pct = ev.percent
                    console.print(f"[cyan]→ progress: {ev.percent}%[/]")
            elif ev.kind == "match":
                color = "green" if ev.pattern_label == "success" else "red"
                console.print(f"[{color}]→ {ev.pattern_label}: {ev.match!r}[/]")
            elif ev.kind == "done":
                console.print()
                if ev.ok and ev.output is not None:
                    console.print(f"[green]✓ {name} succeeded[/] in {ev.output.duration_ms}ms")
                    console.print(f"  log: {ev.output.log_path}")
                    if ev.output.progress_percent is not None:
                        console.print(f"  final progress: {ev.output.progress_percent}%")
                    return 0
                if ev.error_code:
                    console.print(f"[red]✗ {ev.error_code}[/] — {ev.line or 'no detail'}")
                else:
                    out = ev.output
                    if out is not None:
                        console.print(f"[red]✗ TOOL_FAILED[/] (exit={out.exit_code})")
                        if out.failure_match:
                            console.print(f"  failure match: {out.failure_match!r}")
                        console.print(f"  log: {out.log_path}")
                return 1
        return 1

    exit_code = run_async(_consume())
    if exit_code != 0:
        raise typer.Exit(code=exit_code)
