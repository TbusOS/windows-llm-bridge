"""``wlb setup`` — guided configuration for each transport.

M0 ships a stub that prints the env-var snippet a user should drop into
``.env``. M1 will replace it with an interactive flow that writes a TOML
profile under ``workspace/profiles/<name>.toml``.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel

app = typer.Typer(help="Guided setup for each transport.", no_args_is_help=True)
console = Console()


@app.command("ssh")
def setup_ssh(
    host: str = typer.Option("<win-host>", help="Windows host (name or IP)."),
    port: int = typer.Option(22),
    user: str = typer.Option("<your-windows-user>"),
    key: str = typer.Option("~/.ssh/wlb_ed25519"),
) -> None:
    """Print the .env snippet for an SSH target.

    M0 stub. M1 will optionally write to ``workspace/profiles/default.toml``.
    """
    snippet = (
        f"WLB_TRANSPORT=ssh\n"
        f"WLB_SSH_HOST={host}\n"
        f"WLB_SSH_PORT={port}\n"
        f"WLB_SSH_USER={user}\n"
        f"WLB_SSH_KEY={key}\n"
    )
    console.print(
        Panel.fit(
            f"Drop this into your .env (copy from .env.example):\n\n{snippet}",
            title="wlb setup ssh",
            border_style="green",
        )
    )
    console.print(
        "[dim]Next: enable OpenSSH Server on the Windows side — "
        "see scripts/windows-setup/enable-openssh.ps1[/]"
    )


@app.command("local")
def setup_local() -> None:
    """Configure the local transport (for tests / Windows-self use)."""
    console.print(
        Panel.fit(
            "WLB_TRANSPORT=local\n",
            title="wlb setup local",
            border_style="green",
        )
    )
