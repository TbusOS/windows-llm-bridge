"""Profile-loading + env-override tests for wlb.infra.config."""

from __future__ import annotations

from pathlib import Path

import pytest

from wlb.infra.config import load_active
from wlb.infra.workspace import InvalidProfileName


def _write_profile(workspace: Path, name: str, body: str) -> Path:
    pdir = workspace / "profiles"
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / f"{name}.toml"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect WLB_WORKSPACE to a tmpdir and clear all wlb env vars."""
    monkeypatch.setenv("WLB_WORKSPACE", str(tmp_path))
    for k in (
        "WLB_PROFILE", "WLB_TRANSPORT",
        "WLB_SSH_HOST", "WLB_SSH_PORT", "WLB_SSH_USER",
        "WLB_SSH_KEY", "WLB_SSH_KNOWN_HOSTS", "WLB_SSH_TIMEOUT",
        "WLB_PTY_RECORD", "WLB_PTY_RECORD_INPUT", "WLB_PTY_RECORD_DIR",
    ):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def test_missing_profile_falls_back_to_defaults(tmp_path: Path) -> None:
    settings = load_active()
    assert settings.profile_name == "default"
    assert settings.profile_loaded is False
    assert settings.profile_warnings == []
    assert settings.primary_transport == "ssh"     # built-in default
    assert settings.ssh.host is None
    assert settings.ssh.port == 22
    assert settings.ssh.connect_timeout == 10


def test_profile_values_used_when_present(tmp_path: Path) -> None:
    _write_profile(
        tmp_path,
        "default",
        """
[host]
transport = "ssh"

[ssh]
host = "win-host"
port = 2222
user = "admin"
key = "~/.ssh/some_key"
known_hosts = "/etc/ssh/known_hosts"
connect_timeout = 25
""",
    )
    settings = load_active()
    assert settings.profile_loaded is True
    assert settings.ssh.host == "win-host"
    assert settings.ssh.port == 2222
    assert settings.ssh.user == "admin"
    assert settings.ssh.key_path == "~/.ssh/some_key"
    assert settings.ssh.known_hosts == "/etc/ssh/known_hosts"
    assert settings.ssh.connect_timeout == 25


def test_env_overrides_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_profile(
        tmp_path,
        "default",
        """
[ssh]
host = "from-profile"
port = 22
""",
    )
    monkeypatch.setenv("WLB_SSH_HOST", "from-env")
    monkeypatch.setenv("WLB_SSH_PORT", "2200")
    settings = load_active()
    assert settings.ssh.host == "from-env"
    assert settings.ssh.port == 2200


def test_named_profile_picked_up(tmp_path: Path) -> None:
    _write_profile(tmp_path, "homelab", '[ssh]\nhost = "homelab-host"\n')
    settings = load_active("homelab")
    assert settings.profile_name == "homelab"
    assert settings.profile_loaded is True
    assert settings.ssh.host == "homelab-host"


def test_wlb_profile_env_picked_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_profile(tmp_path, "from-env-profile", '[ssh]\nhost = "via-env-profile-var"\n')
    monkeypatch.setenv("WLB_PROFILE", "from-env-profile")
    settings = load_active()
    assert settings.profile_name == "from-env-profile"
    assert settings.ssh.host == "via-env-profile-var"


def test_explicit_arg_wins_over_wlb_profile_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_profile(tmp_path, "via-env", '[ssh]\nhost = "wrong"\n')
    _write_profile(tmp_path, "via-arg", '[ssh]\nhost = "right"\n')
    monkeypatch.setenv("WLB_PROFILE", "via-env")
    settings = load_active("via-arg")
    assert settings.profile_name == "via-arg"
    assert settings.ssh.host == "right"


def test_invalid_profile_name_raises(tmp_path: Path) -> None:
    with pytest.raises(InvalidProfileName):
        load_active("../etc")
    with pytest.raises(InvalidProfileName):
        load_active("..")


def test_corrupt_profile_collects_warning(tmp_path: Path) -> None:
    _write_profile(tmp_path, "default", "this = is = not = toml\n[unclosed")
    settings = load_active()
    assert settings.profile_loaded is False    # parse failed → not loaded
    assert len(settings.profile_warnings) == 1
    assert "failed to parse" in settings.profile_warnings[0]
    # Falls back to defaults
    assert settings.ssh.host is None


def test_empty_string_treated_as_unset(tmp_path: Path) -> None:
    """Profile value of empty string falls through to default, not stored as ''."""
    _write_profile(tmp_path, "default", '[ssh]\nhost = ""\n')
    settings = load_active()
    assert settings.ssh.host is None


# ─── pty_record (M3.7) ──────────────────────────────────────────────


def test_pty_record_defaults_to_disabled(tmp_path: Path) -> None:
    settings = load_active()
    assert settings.pty_record.enabled is False
    assert settings.pty_record.record_input is False
    assert settings.pty_record.dir is None


def test_pty_record_picked_up_from_profile(tmp_path: Path) -> None:
    _write_profile(
        tmp_path, "default",
        '[pty]\nrecord = true\nrecord_input = true\ndir = "/custom/path"\n',
    )
    settings = load_active()
    assert settings.pty_record.enabled is True
    assert settings.pty_record.record_input is True
    assert settings.pty_record.dir == "/custom/path"


def test_pty_record_env_overrides_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_profile(
        tmp_path, "default",
        '[pty]\nrecord = false\nrecord_input = false\n',
    )
    monkeypatch.setenv("WLB_PTY_RECORD", "1")
    monkeypatch.setenv("WLB_PTY_RECORD_INPUT", "yes")
    monkeypatch.setenv("WLB_PTY_RECORD_DIR", "/from-env")
    settings = load_active()
    assert settings.pty_record.enabled is True
    assert settings.pty_record.record_input is True
    assert settings.pty_record.dir == "/from-env"


def test_pty_record_env_can_disable_when_profile_enables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_profile(tmp_path, "default", "[pty]\nrecord = true\n")
    monkeypatch.setenv("WLB_PTY_RECORD", "0")
    settings = load_active()
    assert settings.pty_record.enabled is False


def test_pty_record_unknown_env_string_falls_through_to_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_profile(tmp_path, "default", "[pty]\nrecord = true\n")
    monkeypatch.setenv("WLB_PTY_RECORD", "maybe?")        # unknown → ignore
    settings = load_active()
    assert settings.pty_record.enabled is True
