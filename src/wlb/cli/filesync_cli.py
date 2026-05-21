"""``wlb fs`` — file push / pull over the active transport.

Subcommands:

    wlb fs push <local> <remote>   send a local file/dir to the Windows host
    wlb fs pull <remote> <local>   fetch a remote file/dir to the controller
    wlb fs maps                    list configured SMB / Samba path maps

Both transfers honor the global ``--profile`` / ``--transport`` flags.
``push`` / ``pull`` will use an SMB shortcut (local ``shutil`` copy)
instead of SFTP whenever the destination falls under a configured map and
the Linux-side mount is reachable.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from wlb.capabilities.filesync import pull as cap_pull
from wlb.capabilities.filesync import push as cap_push
from wlb.cli.common import get_transport, print_result, run_async
from wlb.infra.config import load_active

app = typer.Typer(help="File push / pull (SFTP or SMB shortcut).", no_args_is_help=True)
console = Console()


@app.command("push")
def push(
    ctx: typer.Context,
    local: Path = typer.Argument(..., help="Local source file or directory."),
    remote: str = typer.Argument(..., help="Remote destination path on the Windows host."),
) -> None:
    """Push a local file or directory to the Windows host."""
    transport = get_transport(ctx)
    result = run_async(cap_push(transport, local, remote))
    print_result(ctx, result)


@app.command("pull")
def pull(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote source path on the Windows host."),
    local: Path = typer.Argument(..., help="Local destination path."),
) -> None:
    """Pull a remote file or directory from the Windows host."""
    transport = get_transport(ctx)
    result = run_async(cap_pull(transport, remote, local))
    print_result(ctx, result)


@app.command("maps")
def maps(
    ctx: typer.Context,
) -> None:
    """List configured SMB / Samba path maps and whether each Linux mount is reachable.

    Shows the merged set: env (``WLB_SMB_MAPS``) entries first, then any
    additional entries from the active profile's ``[[smb_maps]]`` array.
    """
    profile_name = (ctx.obj or {}).get("profile")
    settings = load_active(profile_name)

    if not settings.smb_maps:
        console.print(
            "[yellow]no SMB maps configured.[/]\n"
            "Set [bold]WLB_SMB_MAPS[/] or add an [bold]smb_maps[/] array to your profile "
            "to enable Linux↔Windows path translation and the local-copy shortcut."
        )
        return

    table = Table(title=f"wlb fs maps  (profile: {settings.profile_name})")
    table.add_column("linux mount")
    table.add_column("windows path")
    table.add_column("mount reachable")
    for m in settings.smb_maps:
        from pathlib import Path as _P
        reachable = _P(m.linux_mount).exists()
        table.add_row(
            m.linux_mount,
            m.windows_path,
            "[green]yes[/]" if reachable else "[yellow]no[/]",
        )
    console.print(table)
