"""``wlb fs`` — file push / pull over the active transport.

Two subcommands:

    wlb fs push <local> <remote>   send a local file/dir to the Windows host
    wlb fs pull <remote> <local>   fetch a remote file/dir to the controller

Both honor the global ``--profile`` / ``--transport`` flags.
"""

from __future__ import annotations

from pathlib import Path

import typer

from wlb.capabilities.filesync import pull as cap_pull
from wlb.capabilities.filesync import push as cap_push
from wlb.cli.common import get_transport, print_result, run_async

app = typer.Typer(help="File push / pull (SFTP over the active transport).", no_args_is_help=True)


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
