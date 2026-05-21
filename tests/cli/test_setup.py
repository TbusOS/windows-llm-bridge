"""CLI tests for the ``wlb setup`` subcommand family."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import tomllib
from typer.testing import CliRunner

from wlb.cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect WLB_WORKSPACE → tmp; clear all WLB_* env so they don't leak in."""
    monkeypatch.setenv("WLB_WORKSPACE", str(tmp_path))
    for k in (
        "WLB_PROFILE", "WLB_TRANSPORT",
        "WLB_SSH_HOST", "WLB_SSH_PORT", "WLB_SSH_USER",
        "WLB_SSH_KEY", "WLB_SSH_KNOWN_HOSTS", "WLB_SSH_TIMEOUT",
    ):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


# ─── setup ssh (non-interactive) ─────────────────────────────────


def test_setup_ssh_writes_default_profile(_isolated_workspace: Path) -> None:
    result = runner.invoke(
        app,
        [
            "setup", "ssh",
            "--non-interactive",
            "--host", "win-test",
            "--user", "testuser",
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    path = _isolated_workspace / "profiles" / "default.toml"
    assert path.exists()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["host"]["transport"] == "ssh"
    assert data["ssh"]["host"] == "win-test"
    assert data["ssh"]["user"] == "testuser"
    assert data["ssh"]["port"] == 22


def test_setup_ssh_writes_named_profile(_isolated_workspace: Path) -> None:
    result = runner.invoke(
        app,
        [
            "setup", "ssh",
            "--profile", "homelab",
            "--non-interactive",
            "--host", "homelab-box",
            "--user", "admin",
            "--port", "2222",
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    path = _isolated_workspace / "profiles" / "homelab.toml"
    assert path.exists()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["ssh"]["host"] == "homelab-box"
    assert data["ssh"]["port"] == 2222


def test_setup_ssh_non_interactive_rejects_missing_required(_isolated_workspace: Path) -> None:
    # No --host given
    result = runner.invoke(
        app,
        [
            "setup", "ssh",
            "--non-interactive",
            "--user", "testuser",
            "--yes",
        ],
    )
    assert result.exit_code != 0
    assert "host" in result.output.lower()


def test_setup_ssh_invalid_profile_name_rejected(_isolated_workspace: Path) -> None:
    result = runner.invoke(
        app,
        [
            "setup", "ssh",
            "--profile", "../etc",
            "--non-interactive",
            "--host", "x", "--user", "y", "--yes",
        ],
    )
    assert result.exit_code != 0
    assert "invalid profile name" in result.output.lower()


def test_setup_ssh_atomic_overwrite_preserves_perms(_isolated_workspace: Path) -> None:
    # First write
    runner.invoke(
        app,
        ["setup", "ssh", "--non-interactive", "--host", "a", "--user", "u1", "--yes"],
    )
    path = _isolated_workspace / "profiles" / "default.toml"
    assert path.exists()
    mode_before = path.stat().st_mode & 0o777
    # Overwrite
    runner.invoke(
        app,
        ["setup", "ssh", "--non-interactive", "--host", "b", "--user", "u2", "--yes"],
    )
    assert path.stat().st_mode & 0o777 == mode_before == 0o600
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["ssh"]["host"] == "b"
    assert data["ssh"]["user"] == "u2"


# ─── setup show ───────────────────────────────────────────────────


def test_setup_show_reflects_named_profile(_isolated_workspace: Path) -> None:
    # Seed a profile
    runner.invoke(
        app,
        [
            "setup", "ssh", "--profile", "p1", "--non-interactive",
            "--host", "from-p1", "--user", "u1", "--yes",
        ],
    )
    # Global --profile picked up
    result = runner.invoke(app, ["--profile", "p1", "setup", "show"])
    assert result.exit_code == 0, result.output
    assert "p1 (loaded)" in result.output
    assert "from-p1" in result.output


def test_setup_show_env_overrides_profile_visible(_isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner.invoke(
        app,
        ["setup", "ssh", "--non-interactive", "--host", "from-profile", "--user", "u", "--yes"],
    )
    monkeypatch.setenv("WLB_SSH_HOST", "from-env")
    result = runner.invoke(app, ["setup", "show"])
    assert result.exit_code == 0, result.output
    assert "from-env" in result.output
    assert "from-profile" not in result.output


# ─── setup list ───────────────────────────────────────────────────


def test_setup_list_empty_directory(_isolated_workspace: Path) -> None:
    result = runner.invoke(app, ["setup", "list"])
    assert result.exit_code == 0
    assert "no profiles" in result.output.lower()


def test_setup_list_shows_written_profiles(_isolated_workspace: Path) -> None:
    for name in ("a", "b", "homelab"):
        runner.invoke(
            app,
            [
                "setup", "ssh", "--profile", name, "--non-interactive",
                "--host", f"{name}-host", "--user", "u", "--yes",
            ],
        )
    result = runner.invoke(app, ["setup", "list"])
    assert result.exit_code == 0, result.output
    assert "a" in result.output
    assert "b" in result.output
    assert "homelab" in result.output


# ─── setup path ───────────────────────────────────────────────────


def test_setup_path_prints_absolute(_isolated_workspace: Path) -> None:
    result = runner.invoke(app, ["setup", "path"])
    assert result.exit_code == 0
    printed = result.output.strip()
    assert os.path.isabs(printed)
    assert printed.endswith("default.toml")


def test_setup_path_honors_global_profile_flag(_isolated_workspace: Path) -> None:
    result = runner.invoke(app, ["--profile", "homelab", "setup", "path"])
    assert result.exit_code == 0
    assert result.output.strip().endswith("homelab.toml")


# ─── global --profile threading ──────────────────────────────────


def test_global_profile_flag_threads_to_describe(_isolated_workspace: Path) -> None:
    # describe doesn't actually use the profile but the flag must parse.
    result = runner.invoke(app, ["--profile", "default", "describe"])
    assert result.exit_code == 0


def test_global_profile_flag_invalid_name_rejected(_isolated_workspace: Path) -> None:
    result = runner.invoke(app, ["--profile", "../bad", "describe"])
    assert result.exit_code != 0
    assert "invalid profile name" in result.output.lower()
