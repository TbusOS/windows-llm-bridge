"""``wlb web`` — start the local dashboard / HTTP API.

A thin convenience around :func:`wlb.api.server.main`. Useful when the
user is already inside a ``wlb …`` CLI flow and doesn't want to remember
the ``wlb-api`` entry-point script.
"""

from __future__ import annotations

import sys

import typer

app = typer.Typer(help="Start the local wlb dashboard / HTTP API.", invoke_without_command=True)


@app.callback()
def web(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address. Default localhost-only."),
    port: int = typer.Option(8765, "--port", help="Port (default 8765)."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code change (dev only)."),
) -> None:
    """Start the wlb dashboard. Equivalent to ``wlb-api`` with the same flags."""
    if ctx.invoked_subcommand is not None:
        return

    profile = (ctx.obj or {}).get("profile")
    bind_label = f"http://{host}:{port}"
    if host not in ("127.0.0.1", "localhost", "::1"):
        typer.secho(
            f"⚠  binding to {host}. M3.3 has NO authentication — anyone who reaches "
            f"{bind_label} can run every declared tool. Restrict the network or add "
            f"a reverse proxy.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    else:
        typer.secho(f"wlb-api listening on {bind_label} (localhost only)", err=True)

    try:
        from wlb.api.server import create_app

        import uvicorn       # type: ignore[import-not-found]
    except ModuleNotFoundError as e:
        typer.secho(f"missing dependency: {e} — run: uv sync", fg=typer.colors.RED, err=True)
        sys.exit(1)

    app_ = create_app(profile_name=profile)
    uvicorn.run(app_, host=host, port=port, reload=reload, log_level="info")
