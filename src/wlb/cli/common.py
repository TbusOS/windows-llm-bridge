"""Shared helpers for CLI subcommands."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, is_dataclass
from typing import Any

import typer
from rich.console import Console

from wlb.infra.result import Result
from wlb.mcp.transport_factory import build_transport
from wlb.transport import ssh_pool
from wlb.transport.base import Transport

console = Console()


def run_async(coro: Any) -> Any:
    """Run an async coroutine from a sync typer handler.

    Wraps the user coroutine so we always close the SSH connection pool on
    the way out. CLI invocations are short-lived (one process per
    ``uv run wlb …``), so the pool's main consumer is the long-lived MCP
    server — it doesn't go through ``run_async`` and keeps its pool for the
    process lifetime.
    """
    async def _wrap() -> Any:
        try:
            return await coro
        finally:
            await ssh_pool.close_all()

    try:
        return asyncio.run(_wrap())
    except KeyboardInterrupt:
        console.print("\n[red]Interrupted[/]")
        raise typer.Exit(code=130) from None


def get_transport(ctx: typer.Context, *, override: str | None = None) -> Transport:
    """Resolve the active transport. Shared with the MCP layer."""
    which = override or (ctx.obj or {}).get("transport")
    profile = (ctx.obj or {}).get("profile")
    try:
        return build_transport(override=which, profile_name=profile)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e


def print_result(ctx: typer.Context, result: Result[Any]) -> None:
    """Render a Result. Honours global ``--json`` flag."""
    json_mode = bool((ctx.obj or {}).get("json"))

    if json_mode:
        print(json.dumps(result.to_dict(), indent=2, default=_json_default))
        if not result.ok:
            raise typer.Exit(code=1)
        return

    if result.ok:
        if result.data is not None:
            _print_data_pretty(result.data)
        if result.artifacts:
            console.print("[dim]artifacts:[/]")
            for a in result.artifacts:
                console.print(f"  • {a}")
        return

    if result.error:
        console.print(f"[red]✗ {result.error.code}[/] — {result.error.message}")
        if result.error.suggestion:
            console.print(f"[yellow]suggestion:[/] {result.error.suggestion}")
        raise typer.Exit(code=1)


def _print_data_pretty(data: Any) -> None:
    if is_dataclass(data):
        for k, v in asdict(data).items():
            console.print(f"  [bold]{k}[/]: {v}")
    elif isinstance(data, dict):
        for k, v in data.items():
            console.print(f"  [bold]{k}[/]: {v}")
    elif isinstance(data, list):
        for item in data:
            console.print(f"  • {item}")
    elif hasattr(data, "to_dict"):
        for k, v in data.to_dict().items():
            console.print(f"  [bold]{k}[/]: {v}")
    else:
        console.print(str(data))


def _json_default(o: Any) -> Any:
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if is_dataclass(o):
        return asdict(o)
    return str(o)
