"""Workspace path helpers.

Convention: all runtime artifacts land under
``workspace/hosts/<host>/<category>/<file>``. ``<host>`` is the resolved
SSH target name (sanitized). ``<category>`` is one of:

- ``logs``           — captured stdout/stderr of arbitrary commands
- ``tools``          — per-named-tool run logs (M2)
- ``pulls``          — files pulled from the Windows side (M2)
- ``screenshots``    — UI captures (M3)

All ``<host>`` strings are validated against ``_SAFE_HOST_RE`` to refuse
traversal attacks (``..``, embedded ``/``, etc.).
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

# Host string shape — hostname, IPv4, IPv6 (with brackets stripped already).
# Leading char must be alnum so ``..`` / ``.foo`` are rejected even though
# ``.`` and ``:`` appear inside.
_SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")

# Profile names appear in ``--profile <name>`` and ``WLB_PROFILE`` env.
# Used by future ``profile_path(name)`` to build
# ``workspace/profiles/<name>.toml``.
_SAFE_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class InvalidHost(ValueError):
    """Raised when a user-supplied host would escape the workspace."""


class InvalidProfileName(ValueError):
    """Raised when a user-supplied profile name would escape ``profiles/``."""


def is_safe_host(s: object) -> bool:
    """True if ``s`` is a safe host identifier (no traversal)."""
    return isinstance(s, str) and bool(_SAFE_HOST_RE.match(s))


def is_safe_profile_name(s: object) -> bool:
    """True if ``s`` is a safe profile name (no traversal)."""
    return isinstance(s, str) and bool(_SAFE_PROFILE_NAME_RE.match(s))


def workspace_root() -> Path:
    """Return the workspace root. Configurable via ``WLB_WORKSPACE`` env.

    Default: ``<repo>/workspace`` if it exists, else ``~/.wlb-workspace``.
    """
    env = os.environ.get("WLB_WORKSPACE")
    if env:
        return Path(env).expanduser().resolve()
    cwd_ws = Path.cwd() / "workspace"
    if cwd_ws.exists():
        return cwd_ws
    return Path.home() / ".wlb-workspace"


def iso_timestamp() -> str:
    """ISO 8601 timestamp safe for filenames (no colons).

    Example: ``'2026-05-18T10-30-00'``.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def workspace_path(
    category: str,
    filename: str,
    *,
    host: str | None = None,
    ensure_dir: bool = True,
) -> Path:
    """Build a canonical artifact path.

    Example::

        workspace_path("logs", "2026-05-18T10-30-00-cmd.txt", host="win-host")
        # → <workspace_root>/hosts/win-host/logs/2026-05-18T10-30-00-cmd.txt

    Raises ``InvalidHost`` when ``host`` contains characters that would let
    it escape the workspace. ``category`` and ``filename`` are internal
    caller-controlled inputs and not validated here.
    """
    if host and not is_safe_host(host):
        raise InvalidHost(
            f"invalid host {host!r}: must match [A-Za-z0-9._:-]{{1,64}} "
            f"with an alnum leading char"
        )
    root = workspace_root()
    base = root / "hosts" / host / category if host else root / category
    if ensure_dir:
        base.mkdir(parents=True, exist_ok=True)
    return base / filename


def profile_path(name: str) -> Path:
    """Path to ``workspace/profiles/<name>.toml``.

    Raises ``InvalidProfileName`` for any traversal-shaped input.
    """
    if not is_safe_profile_name(name):
        raise InvalidProfileName(
            f"invalid profile name {name!r}: must match [A-Za-z0-9][A-Za-z0-9_-]*"
        )
    root = workspace_root() / "profiles"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{name}.toml"
