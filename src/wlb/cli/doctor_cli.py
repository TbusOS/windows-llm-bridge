"""``wlb doctor`` — environment + transport health check.

Probes:
    1. Python version
    2. Active transport readiness (env vars set?)
    3. Transport.health() response
    4. Permission deny-list integrity (compiles cleanly, has the expected entries)

Prints a Rich table of OK / WARN / FAIL.
"""

from __future__ import annotations

import os
import platform
import sys

import typer
from rich.console import Console
from rich.table import Table

from wlb.infra.config import load_active
from wlb.infra.permissions import DANGEROUS_PATTERNS
from wlb.mcp.transport_factory import build_transport

console = Console()


def run_doctor(ctx: typer.Context) -> None:
    """Run a one-shot health check."""
    table = Table(title="wlb doctor")
    table.add_column("probe")
    table.add_column("status")
    table.add_column("detail")

    # Python
    py = ".".join(map(str, sys.version_info[:3]))
    py_ok = sys.version_info >= (3, 11)
    table.add_row(
        "python",
        "[green]OK[/]" if py_ok else "[red]FAIL[/]",
        f"{py} on {platform.system()} {platform.release()}",
    )

    # Active profile (honor --profile flag)
    profile_name = (ctx.obj or {}).get("profile")
    settings = load_active(profile_name)
    profile_state = "loaded" if settings.profile_loaded else "no file"
    table.add_row(
        "profile",
        "[green]OK[/]" if settings.profile_loaded else "[yellow]WARN[/]",
        f"{settings.profile_name} ({profile_state}) — {settings.profile_path}",
    )

    # Config
    cfg_ok = bool(settings.ssh.host) if settings.primary_transport == "ssh" else True
    table.add_row(
        "config",
        "[green]OK[/]" if cfg_ok else "[yellow]WARN[/]",
        f"transport={settings.primary_transport}, "
        f"ssh_host={settings.ssh.host or '<unset>'}, "
        f"ssh_user={settings.ssh.user or '<unset>'}",
    )

    # Transport health (honor --profile flag)
    try:
        transport = build_transport(profile_name=profile_name)
        import asyncio

        health = asyncio.run(transport.health())
        h_ok = bool(health.get("ok"))
        table.add_row(
            "transport health",
            "[green]OK[/]" if h_ok else "[yellow]WARN[/]",
            ", ".join(f"{k}={v}" for k, v in health.items()),
        )
    except Exception as e:  # noqa: BLE001
        table.add_row("transport health", "[red]FAIL[/]", str(e))

    # Permission engine
    pat_count = len(DANGEROUS_PATTERNS)
    pat_ok = pat_count >= 10
    table.add_row(
        "permissions",
        "[green]OK[/]" if pat_ok else "[yellow]WARN[/]",
        f"{pat_count} dangerous patterns loaded",
    )

    # Workspace
    ws = os.environ.get("WLB_WORKSPACE", "<default>")
    table.add_row("workspace", "[green]OK[/]", ws)

    console.print(table)
