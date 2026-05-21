"""Active settings loader — env > TOML profile > built-in defaults.

A "profile" is a TOML file under ``workspace/profiles/<name>.toml``. It's
the canonical place to pin one target's connection details (SSH host /
port / user / key) so the user doesn't have to re-export env vars every
shell session. Multiple profiles let one controller switch between
several Windows hosts.

Resolution order (highest wins):

1. Explicit ``override`` arg on call sites (rare; used by ``--transport`` etc.)
2. Environment variables (``WLB_SSH_HOST``, ``WLB_SSH_USER``, ...)
3. Profile TOML file
4. Built-in defaults

Profile lookup:

- ``profile_name`` parameter to :func:`load_active`
- ``WLB_PROFILE`` env variable
- Literal ``"default"``

If the resolved profile file doesn't exist, that's not an error — wlb
just falls back to env + defaults. ``wlb setup ssh`` writes a profile
file the first time you configure a host.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from wlb.infra.workspace import (
    InvalidProfileName,
    is_safe_profile_name,
    profile_path,
)


@dataclass(frozen=True)
class SshSettings:
    host: str | None
    port: int
    user: str | None
    key_path: str | None
    known_hosts: str | None
    connect_timeout: int


@dataclass(frozen=True)
class ActiveSettings:
    primary_transport: str
    ssh: SshSettings
    profile_name: str = "default"
    profile_path: Path | None = None       # absolute path; None if no file
    profile_loaded: bool = False           # True if file existed and parsed
    profile_warnings: list[str] = field(default_factory=list)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _resolve_profile_name(arg: str | None) -> str:
    """Pick the active profile name from arg → env → default."""
    name = arg or os.environ.get("WLB_PROFILE") or "default"
    if not is_safe_profile_name(name):
        raise InvalidProfileName(
            f"invalid profile name {name!r}: must match [A-Za-z0-9][A-Za-z0-9_-]*"
        )
    return name


def _load_profile_file(path: Path) -> tuple[dict, list[str]]:
    """Return ``(data, warnings)`` for a profile TOML file.

    Missing file → empty dict, no warning (treated as "use env + defaults").
    Parse error → empty dict, one warning (so the user sees it in ``doctor``).
    """
    if not path.exists():
        return {}, []
    try:
        with path.open("rb") as fp:
            return tomllib.load(fp), []
    except tomllib.TOMLDecodeError as e:
        return {}, [f"failed to parse {path}: {e}"]
    except OSError as e:
        return {}, [f"failed to read {path}: {e}"]


def _layer(env_name: str, profile_section: dict, profile_key: str, default: object) -> object:
    """env > profile > default."""
    env_val = os.environ.get(env_name)
    if env_val not in (None, ""):
        return env_val
    if profile_key in profile_section and profile_section[profile_key] not in (None, ""):
        return profile_section[profile_key]
    return default


def _layer_int(env_name: str, profile_section: dict, profile_key: str, default: int) -> int:
    """env > profile > default, coerced to int."""
    env_val = os.environ.get(env_name)
    if env_val not in (None, ""):
        try:
            return int(env_val)
        except ValueError:
            return default
    if profile_key in profile_section:
        try:
            return int(profile_section[profile_key])
        except (TypeError, ValueError):
            return default
    return default


def load_active(profile_name: str | None = None) -> ActiveSettings:
    """Load active settings layered as: env > profile TOML > defaults."""
    name = _resolve_profile_name(profile_name)
    path = profile_path(name)
    data, warnings = _load_profile_file(path)
    host_section = data.get("host", {}) if isinstance(data.get("host"), dict) else {}
    ssh_section = data.get("ssh", {}) if isinstance(data.get("ssh"), dict) else {}

    transport_value = _layer("WLB_TRANSPORT", host_section, "transport", "ssh")
    transport = str(transport_value) if transport_value is not None else "ssh"

    return ActiveSettings(
        primary_transport=transport,
        ssh=SshSettings(
            host=_str_or_none(_layer("WLB_SSH_HOST", ssh_section, "host", None)),
            port=_layer_int("WLB_SSH_PORT", ssh_section, "port", 22),
            user=_str_or_none(_layer("WLB_SSH_USER", ssh_section, "user", None)),
            key_path=_str_or_none(_layer("WLB_SSH_KEY", ssh_section, "key", None)),
            known_hosts=_str_or_none(
                _layer("WLB_SSH_KNOWN_HOSTS", ssh_section, "known_hosts", None)
            ),
            connect_timeout=_layer_int("WLB_SSH_TIMEOUT", ssh_section, "connect_timeout", 10),
        ),
        profile_name=name,
        profile_path=path,
        profile_loaded=path.exists() and not warnings,
        profile_warnings=warnings,
    )


def _str_or_none(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)
