"""Main CLI entry (``wlb`` command)."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict

import typer
from rich.console import Console
from rich.table import Table

from wlb import __version__
from wlb.capabilities.cmd import execute as cmd_execute
from wlb.capabilities.powershell import execute as ps_execute
from wlb.capabilities.status import describe as cap_describe
from wlb.capabilities.status import status as cap_status
from wlb.cli.common import get_transport, print_result, run_async
from wlb.cli.doctor_cli import run_doctor
from wlb.cli.filesync_cli import app as filesync_cli
from wlb.cli.setup_cli import app as setup_cli
from wlb.infra.env_loader import load_env_files
from wlb.infra.registry import CAPABILITIES, TRANSPORTS

# Load .env / .env.local at CLI startup so subcommands see the values.
load_env_files()

app = typer.Typer(
    name="wlb",
    help="windows-llm-bridge — Windows shell / tool bridge for LLM agents.",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()


@app.callback()
def _main_options(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Structured JSON output."),
    profile: str | None = typer.Option(
        None, "--profile", "-p", help="Profile name to activate (workspace/profiles/<name>.toml)."
    ),
    transport: str | None = typer.Option(
        None, "--transport", help="Override transport: ssh|local|http|hybrid."
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose output."),
) -> None:
    # Validate the profile name early so bad input shows a friendly error
    # rather than a Python traceback from deeper in the stack.
    if profile is not None:
        from wlb.infra.workspace import is_safe_profile_name

        if not is_safe_profile_name(profile):
            raise typer.BadParameter(
                f"invalid profile name {profile!r}: must match [A-Za-z0-9][A-Za-z0-9_-]*",
                param_hint="--profile",
            )
    ctx.obj = {
        "json": json_output,
        "profile": profile,
        "transport": transport,
        "verbose": verbose,
    }


# ─── Meta commands ─────────────────────────────────────────────────
@app.command()
def version() -> None:
    """Show version."""
    console.print(f"[bold]windows-llm-bridge[/] (wlb) v{__version__}")


@app.command()
def describe(ctx: typer.Context) -> None:
    """Output the full transport / capability schema. LLM-oriented."""
    result = run_async(cap_describe())
    if (ctx.obj or {}).get("json"):
        print(json.dumps(result.to_dict(), indent=2, default=str))
        return

    schema = result.data or {}
    table = Table(title="windows-llm-bridge · transports")
    table.add_column("name")
    table.add_column("status")
    table.add_column("description")
    for t in TRANSPORTS:
        table.add_row(t.name, t.status, t.description or "")
    console.print(table)

    table = Table(title="windows-llm-bridge · capabilities")
    table.add_column("name")
    table.add_column("cli")
    table.add_column("status")
    table.add_column("description")
    for c in CAPABILITIES:
        table.add_row(c.name, c.cli_command, c.status, c.description or "")
    console.print(table)

    _ = schema  # touched only in JSON branch above


@app.command()
def status(ctx: typer.Context) -> None:
    """Current transport / health snapshot."""
    transport = get_transport(ctx)
    result = run_async(cap_status(transport))
    if (ctx.obj or {}).get("json"):
        print(json.dumps(result.to_dict(), indent=2, default=str))
        return

    if not result.ok or result.data is None:
        if result.error:
            console.print(f"[red]✗ {result.error.code}[/] — {result.error.message}")
        raise typer.Exit(code=1)

    table = Table(title=f"wlb status (transport: {result.data.transport})")
    table.add_column("key")
    table.add_column("value")
    for k, v in (result.data.health or {}).items():
        table.add_row(k, str(v))
    console.print(table)


# ─── Doctor + setup + filesync ─────────────────────────────────────
app.command("doctor", help="One-shot environment health check.")(run_doctor)
app.add_typer(setup_cli, name="setup", help="Guided setup for each transport.")
app.add_typer(filesync_cli, name="fs", help="File push / pull over the active transport.")


# ─── Shell commands ────────────────────────────────────────────────
@app.command()
def cmd(
    ctx: typer.Context,
    cmd: str = typer.Argument(..., help="Command line to run via cmd.exe /c."),  # noqa: A002
    timeout: int = typer.Option(30, "--timeout", "-t"),
    allow_dangerous: bool = typer.Option(False, "--allow-dangerous"),
) -> None:
    """Run a command via cmd.exe /c on the Windows host."""
    transport = get_transport(ctx)
    result = run_async(
        cmd_execute(
            transport, cmd, timeout=timeout, allow_dangerous=allow_dangerous
        )
    )
    print_result(ctx, result)


@app.command()
def powershell(
    ctx: typer.Context,
    script: str = typer.Argument(..., help="PowerShell script to execute."),
    timeout: int = typer.Option(60, "--timeout", "-t"),
    allow_dangerous: bool = typer.Option(False, "--allow-dangerous"),
) -> None:
    """Run a PowerShell script (pwsh.exe preferred, falls back to powershell.exe)."""
    transport = get_transport(ctx)
    result = run_async(
        ps_execute(
            transport, script, timeout=timeout, allow_dangerous=allow_dangerous
        )
    )
    print_result(ctx, result)


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[red]Interrupted[/]")
        sys.exit(130)


if __name__ == "__main__":
    main()
