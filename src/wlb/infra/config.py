"""Minimal config loader.

M0 ships a tiny env-only loader so the CLI / MCP / tests can run end-to-end
without a profile file. M1 will add a TOML profile under
``workspace/profiles/<name>.toml`` and pick it up here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


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


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def load_active() -> ActiveSettings:
    """Load active settings from the environment.

    Future: layer a TOML profile under env. For M0, env is the only source.
    """
    return ActiveSettings(
        primary_transport=os.environ.get("WLB_TRANSPORT", "ssh"),
        ssh=SshSettings(
            host=os.environ.get("WLB_SSH_HOST"),
            port=_int_env("WLB_SSH_PORT", 22),
            user=os.environ.get("WLB_SSH_USER"),
            key_path=os.environ.get("WLB_SSH_KEY"),
            known_hosts=os.environ.get("WLB_SSH_KNOWN_HOSTS"),
            connect_timeout=_int_env("WLB_SSH_TIMEOUT", 10),
        ),
    )
