"""``wlb setup`` — interactive configuration of transport profiles.

Profiles live under ``workspace/profiles/<name>.toml`` and are the
canonical way to pin a Windows target's connection details. Env vars
still take precedence at runtime, so a profile is a *default* the user
can override per-shell.

Commands:

    wlb setup ssh [--profile NAME] [--non-interactive]   write/refresh an SSH profile
    wlb setup local [--profile NAME]                     write a local-loopback profile
    wlb setup show [--profile NAME]                      print the merged active settings
    wlb setup list                                       list available profiles
    wlb setup path [--profile NAME]                      print the on-disk profile path
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

import tomli_w
import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from wlb.infra.config import load_active
from wlb.infra.workspace import (
    InvalidProfileName,
    is_safe_profile_name,
    profile_path,
    workspace_root,
)

app = typer.Typer(help="Guided setup for each transport.", no_args_is_help=True)
console = Console()


# ─── helpers ──────────────────────────────────────────────────────


def _validate_profile_name(name: str) -> str:
    if not is_safe_profile_name(name):
        raise typer.BadParameter(
            f"invalid profile name {name!r}: must match [A-Za-z0-9][A-Za-z0-9_-]* (<= 64 chars)",
            param_hint="--profile",
        )
    return name


def _existing_profile(name: str) -> dict[str, Any]:
    """Return the parsed profile dict for ``name`` (or empty dict)."""
    path = profile_path(name)
    if not path.exists():
        return {}
    import tomllib
    try:
        with path.open("rb") as fp:
            return tomllib.load(fp)
    except Exception:  # noqa: BLE001 — corrupt file, treat as empty for prompting
        return {}


def _atomic_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically (write to tmp, fsync, rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(content)
            fp.flush()
            os.fsync(fp.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _build_ssh_toml(
    *,
    host: str,
    port: int,
    user: str,
    key: str,
    known_hosts: str,
    connect_timeout: int,
) -> str:
    """Serialize an SSH profile dict to TOML text with a header comment."""
    data: dict[str, Any] = {
        "host": {"transport": "ssh"},
        "ssh": {
            "host": host,
            "port": port,
            "user": user,
            "key": key,
        },
    }
    if known_hosts:
        data["ssh"]["known_hosts"] = known_hosts
    if connect_timeout != 10:
        data["ssh"]["connect_timeout"] = connect_timeout
    body = tomli_w.dumps(data)
    header = (
        "# windows-llm-bridge profile — written by `wlb setup ssh`\n"
        "# Hand-edits are fine. Re-running `wlb setup ssh --profile <name>`\n"
        "# overwrites this file.\n"
        "#\n"
        "# Env vars (WLB_SSH_HOST etc.) take precedence over this file at\n"
        "# runtime, so you can override one-off without editing.\n"
        "\n"
    )
    return header + body


def _validate_host(value: str) -> str:
    value = value.strip()
    if not value:
        raise typer.BadParameter("host cannot be empty")
    if any(c.isspace() for c in value):
        raise typer.BadParameter("host cannot contain whitespace")
    return value


def _validate_port(value: int) -> int:
    if not (1 <= value <= 65535):
        raise typer.BadParameter("port must be between 1 and 65535")
    return value


def _validate_key(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    resolved = Path(os.path.expanduser(value))
    if not resolved.exists():
        console.print(
            f"[yellow]warning:[/] key file [bold]{resolved}[/] does not exist yet. "
            "Generate it with: ssh-keygen -t ed25519 -f " + str(resolved)
        )
    return value


# ─── wlb setup ssh ────────────────────────────────────────────────


@app.command("ssh")
def setup_ssh(
    profile: str = typer.Option("default", "--profile", "-p", help="Profile name to write to."),
    non_interactive: bool = typer.Option(
        False, "--non-interactive", help="Skip prompts; require all values via flags."
    ),
    host: str | None = typer.Option(None, "--host", help="Windows host (name or IP)."),
    port: int | None = typer.Option(None, "--port", help="SSH port."),
    user: str | None = typer.Option(None, "--user", help="Windows user (owns authorized_keys)."),
    key: str | None = typer.Option(None, "--key", help="Path to private key on this host."),
    known_hosts: str | None = typer.Option(
        None,
        "--known-hosts",
        help="Path to known_hosts; 'none' to disable host-key check (testing only).",
    ),
    connect_timeout: int | None = typer.Option(None, "--timeout", help="Connect timeout, seconds."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the final confirmation prompt."),
) -> None:
    """Write an SSH profile to ``workspace/profiles/<name>.toml``.

    Interactive by default. With ``--non-interactive`` all values must come
    from flags (good for scripted CI bootstrap).
    """
    name = _validate_profile_name(profile)
    existing = _existing_profile(name).get("ssh", {})

    def _ask(prompt: str, *, current: object, flag: object, required: bool = True) -> str:
        """Pick value from flag → prompt-default → existing-profile-default.

        ``required=False`` lets non-interactive mode fall back to the existing
        value (or empty string) silently, without prompting or erroring.
        """
        if flag not in (None, ""):
            return str(flag)
        if non_interactive:
            if current not in (None, ""):
                return str(current)
            if not required:
                return ""
            raise typer.BadParameter(
                f"--non-interactive set but {prompt!r} not provided",
                param_hint=f"--{prompt}",
            )
        default = "" if current in (None, "") else str(current)
        return typer.prompt(prompt, default=default)

    def _ask_int(
        prompt: str, *, current: object, flag: object, fallback: int, required: bool = True
    ) -> int:
        if flag is not None:
            return int(flag)
        if non_interactive:
            if current not in (None, ""):
                try:
                    return int(current)
                except (TypeError, ValueError):
                    return fallback
            if not required:
                return fallback
            raise typer.BadParameter(
                f"--non-interactive set but {prompt!r} not provided",
                param_hint=f"--{prompt}",
            )
        default = int(current) if current not in (None, "") else fallback
        return int(typer.prompt(prompt, default=default, type=int))

    host_v = _validate_host(_ask("host", current=existing.get("host"), flag=host))
    port_v = _validate_port(
        _ask_int("port", current=existing.get("port"), flag=port, fallback=22, required=False)
    )
    user_v = _validate_host(_ask("user", current=existing.get("user"), flag=user))  # reuse non-blank rule
    key_v = _validate_key(
        _ask("key", current=existing.get("key") or "~/.ssh/wlb_ed25519", flag=key)
    )
    kh_v = _ask(
        "known_hosts", current=existing.get("known_hosts") or "", flag=known_hosts, required=False
    )
    timeout_v = _ask_int(
        "connect_timeout",
        current=existing.get("connect_timeout"),
        flag=connect_timeout,
        fallback=10,
        required=False,
    )

    toml_text = _build_ssh_toml(
        host=host_v,
        port=port_v,
        user=user_v,
        key=key_v,
        known_hosts=kh_v.strip(),
        connect_timeout=timeout_v,
    )
    path = profile_path(name)

    console.print()
    console.print(
        Panel.fit(
            Syntax(toml_text, "toml", theme="ansi_dark", line_numbers=False),
            title=f"About to write {path}",
            border_style="cyan",
        )
    )

    if not yes and not non_interactive:
        if not typer.confirm("Write this profile?", default=True):
            console.print("[yellow]aborted — nothing written[/]")
            raise typer.Exit(code=1)

    _atomic_write(path, toml_text)
    console.print(f"[green]✓ wrote[/] {path}")
    if name != "default":
        console.print(
            f"[dim]to use this profile, run:[/] WLB_PROFILE={name} wlb status  "
            f"[dim]or:[/] wlb --profile {name} status"
        )
    else:
        console.print("[dim]next:[/] uv run wlb status")


# ─── wlb setup local ──────────────────────────────────────────────


@app.command("local")
def setup_local(
    profile: str = typer.Option("default", "--profile", "-p", help="Profile name to write to."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Write a local-loopback profile (used for tests and Windows-self-use)."""
    name = _validate_profile_name(profile)
    text = (
        "# windows-llm-bridge local-loopback profile\n"
        "# Use for hermetic tests or when wlb runs ON the Windows host directly.\n"
        "\n"
    )
    text += tomli_w.dumps({"host": {"transport": "local"}})
    path = profile_path(name)
    console.print(Panel.fit(text, title=f"About to write {path}", border_style="cyan"))
    if not yes and not typer.confirm("Write this profile?", default=True):
        console.print("[yellow]aborted[/]")
        raise typer.Exit(code=1)
    _atomic_write(path, text)
    console.print(f"[green]✓ wrote[/] {path}")


# ─── wlb setup show ───────────────────────────────────────────────


@app.command("show")
def setup_show(
    ctx: typer.Context,
    profile: str | None = typer.Option(None, "--profile", "-p", help="Profile name to inspect."),
) -> None:
    """Print the merged active settings (env > profile > defaults).

    Profile name resolution: subcommand --profile > global --profile (set on
    the `wlb` callback) > ``WLB_PROFILE`` env > literal ``"default"``.
    """
    effective = profile or (ctx.obj or {}).get("profile")
    try:
        settings = load_active(effective)
    except InvalidProfileName as e:
        raise typer.BadParameter(str(e), param_hint="--profile") from None

    table = Table(title="wlb setup show — merged active settings")
    table.add_column("key")
    table.add_column("value")
    table.add_column("source", style="dim")

    profile_marker = f"{settings.profile_name} ({'loaded' if settings.profile_loaded else 'no file'})"
    table.add_row("profile", profile_marker, str(settings.profile_path))
    table.add_row(
        "transport",
        settings.primary_transport,
        _source_of("WLB_TRANSPORT", profile_loaded=settings.profile_loaded),
    )

    for env, attr in (
        ("WLB_SSH_HOST", "host"),
        ("WLB_SSH_PORT", "port"),
        ("WLB_SSH_USER", "user"),
        ("WLB_SSH_KEY", "key_path"),
        ("WLB_SSH_KNOWN_HOSTS", "known_hosts"),
        ("WLB_SSH_TIMEOUT", "connect_timeout"),
    ):
        value = getattr(settings.ssh, attr)
        table.add_row(env, _safe_str(value), _source_of(env, profile_loaded=settings.profile_loaded))

    console.print(table)

    if settings.profile_warnings:
        console.print()
        for w in settings.profile_warnings:
            console.print(f"[yellow]warning:[/] {w}")

    # Also print the resolved dataclass for machine consumption when --json is set
    # at the global level; setup_show doesn't honor --json itself (intentional —
    # it's a human-oriented inspector).
    _ = asdict(settings)  # touched only for type completeness


def _safe_str(value: object) -> str:
    if value is None:
        return "<unset>"
    return str(value)


def _source_of(env_name: str, *, profile_loaded: bool = True) -> str:
    if os.environ.get(env_name):
        return "env"
    if profile_loaded:
        return "profile"
    return "default"


# ─── wlb setup list ───────────────────────────────────────────────


@app.command("list")
def setup_list() -> None:
    """List all profiles under ``workspace/profiles/``."""
    profiles_dir = workspace_root() / "profiles"
    if not profiles_dir.exists():
        console.print("[yellow]no profiles directory yet[/] — run `wlb setup ssh` to create one")
        return

    files = sorted(p for p in profiles_dir.iterdir() if p.suffix == ".toml" and p.is_file())
    if not files:
        console.print("[yellow]no profiles found[/] — run `wlb setup ssh` to create one")
        return

    table = Table(title=f"profiles in {profiles_dir}")
    table.add_column("name")
    table.add_column("size", justify="right")
    table.add_column("modified", style="dim")
    for f in files:
        stat = f.stat()
        import datetime as _dt
        table.add_row(
            f.stem,
            f"{stat.st_size}B",
            _dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)


# ─── wlb setup path ───────────────────────────────────────────────


@app.command("path")
def setup_path(
    ctx: typer.Context,
    profile: str | None = typer.Option(None, "--profile", "-p", help="Profile name."),
) -> None:
    """Print the on-disk path for ``<profile>.toml`` (existing or not).

    Profile resolution: subcommand --profile > global --profile > WLB_PROFILE > "default".
    """
    effective = profile or (ctx.obj or {}).get("profile") or os.environ.get("WLB_PROFILE") or "default"
    name = _validate_profile_name(effective)
    print(profile_path(name))
