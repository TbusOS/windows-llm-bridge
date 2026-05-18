"""Path validation helpers for remote (Windows-side) paths.

Used by the filesync capability (M2) to refuse paths that look likely to
escape an allow-listed area or accidentally target system locations.
"""

from __future__ import annotations

import re

# Reject anything that resembles a UNC path (``\\?\``, ``\\.\``, ``\\server\``)
# unless the caller explicitly opts in. UNC paths are valid but have
# enough foot-guns that we don't pass them through silently.
_UNC_RE = re.compile(r"^\s*\\\\")

# Drive-letter syntax: ``C:\path\to\file`` (most common in our workflow).
_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


def looks_unc(path: str) -> bool:
    return bool(_UNC_RE.match(path))


def looks_drive_path(path: str) -> bool:
    return bool(_DRIVE_PATH_RE.match(path))


def normalize_windows_path(path: str) -> str:
    """Normalize a Windows path string for storage / display.

    Converts forward slashes to backslashes (Windows accepts both, but most
    tools display backslashes). Strips redundant whitespace. Does NOT
    resolve ``..`` — that's the caller's job and depends on whether the
    path is intended to traverse.
    """
    return path.strip().replace("/", "\\")
