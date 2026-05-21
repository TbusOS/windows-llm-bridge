"""SMB / Samba path translation.

When the controller-side Linux box has a Samba/SMB share mounted that
points at a directory the Windows side also sees as a drive path, wlb
can:

1. Accept *either* path form in ``wlb fs push|pull`` (Linux-mount form
   ``/mnt/win-share/x.bin`` or Windows form ``C:\\share\\x.bin``) and
   translate them transparently.
2. **Shortcut**: skip SFTP entirely and just copy locally on the Linux
   side, because the Windows side sees the same file through the share.

Configuration is two-layered, same precedence as the rest of wlb:

- ``WLB_SMB_MAPS`` env: semicolon-separated ``linux=windows`` pairs.
  Example: ``/mnt/win-share=C:\\share;/mnt/factory=D:\\factory``
- Profile TOML ``[[smb_maps]]`` array of tables (see ``parse_toml_array``).

Env entries are *added* to profile entries (env takes precedence on
conflicts — first match wins, so env entries are tried first).

Case sensitivity:
- Linux paths are matched case-sensitively (POSIX semantics).
- Windows paths are matched case-insensitively (NTFS semantics).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SmbMap:
    """One Linux-mount ↔ Windows-path mapping."""

    linux_mount: str    # normalized, no trailing /
    windows_path: str   # normalized, no trailing \

    def to_dict(self) -> dict[str, str]:
        return {"linux": self.linux_mount, "windows": self.windows_path}


def _normalize_linux(path: str) -> str:
    """Strip trailing slashes; preserve absolute leading slash."""
    p = path.strip()
    if not p:
        return ""
    while len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    return p


def _normalize_windows(path: str) -> str:
    """Convert ``/`` to ``\\``, strip trailing backslashes (but keep ``C:\\``)."""
    p = path.strip().replace("/", "\\")
    if not p:
        return ""
    # Drive-only paths like ``C:`` get a trailing slash so prefix checks behave.
    if len(p) == 2 and p[1] == ":":
        return p + "\\"
    while len(p) > 3 and p.endswith("\\"):
        p = p[:-1]
    return p


def parse_env_value(raw: str | None) -> list[SmbMap]:
    """Parse an env value of the form ``linux=windows;linux=windows``.

    Whitespace around tokens is trimmed; malformed entries (no ``=``,
    empty side) are silently skipped. Returns an empty list for ``None``
    or empty input.
    """
    if not raw:
        return []
    out: list[SmbMap] = []
    for entry in raw.split(";"):
        if "=" not in entry:
            continue
        linux, _, windows = entry.partition("=")
        linux_n = _normalize_linux(linux)
        win_n = _normalize_windows(windows)
        if linux_n and win_n:
            out.append(SmbMap(linux_mount=linux_n, windows_path=win_n))
    return out


def parse_toml_array(arr: object) -> list[SmbMap]:
    """Parse a profile ``[[smb_maps]]`` array of tables.

    Each table is expected to have ``linux`` and ``windows`` string keys.
    Malformed entries are silently skipped.
    """
    if not isinstance(arr, list):
        return []
    out: list[SmbMap] = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        linux = item.get("linux")
        windows = item.get("windows")
        if not isinstance(linux, str) or not isinstance(windows, str):
            continue
        linux_n = _normalize_linux(linux)
        win_n = _normalize_windows(windows)
        if linux_n and win_n:
            out.append(SmbMap(linux_mount=linux_n, windows_path=win_n))
    return out


def merge(env_maps: list[SmbMap], profile_maps: list[SmbMap]) -> list[SmbMap]:
    """Combine env + profile maps. Env entries come first (higher priority)."""
    seen_linux: set[str] = set()
    seen_windows: set[str] = set()
    out: list[SmbMap] = []
    for m in [*env_maps, *profile_maps]:
        # Dedup by either side: a Linux mount or Windows path should map only once.
        if m.linux_mount in seen_linux or m.windows_path.lower() in seen_windows:
            continue
        out.append(m)
        seen_linux.add(m.linux_mount)
        seen_windows.add(m.windows_path.lower())
    return out


def translate_linux_to_windows(path: str, maps: list[SmbMap]) -> str | None:
    """If ``path`` lies under one of the Linux mounts, return the Windows form.

    Returns ``None`` if no map matches.
    """
    p = _normalize_linux(path)
    for m in maps:
        if p == m.linux_mount:
            return m.windows_path
        prefix = m.linux_mount + "/"
        if p.startswith(prefix):
            remainder = p[len(m.linux_mount):]  # keeps leading /
            return m.windows_path + remainder.replace("/", "\\")
    return None


def translate_windows_to_linux(path: str, maps: list[SmbMap]) -> str | None:
    """If ``path`` lies under one of the Windows mapped paths, return the Linux form.

    Returns ``None`` if no map matches. Windows comparison is case-insensitive.
    """
    p = _normalize_windows(path)
    p_lower = p.lower()
    for m in maps:
        m_win_lower = m.windows_path.lower()
        if p_lower == m_win_lower:
            return m.linux_mount
        # Drive-root windows_path ends with backslash (`C:\`); plain paths don't.
        prefix = m_win_lower if m_win_lower.endswith("\\") else m_win_lower + "\\"
        if p_lower.startswith(prefix):
            remainder = p[len(m.windows_path):]
            # Drop any leading backslash before joining with /.
            remainder = remainder.lstrip("\\")
            if remainder:
                return m.linux_mount + "/" + remainder.replace("\\", "/")
            return m.linux_mount
    return None


def looks_like_linux_path(path: str) -> bool:
    """Heuristic: ``path`` starts with ``/`` or ``~``."""
    if not path:
        return False
    return path.startswith("/") or path.startswith("~")


def looks_like_windows_path(path: str) -> bool:
    """Heuristic: drive-letter prefix (``C:\\``) or UNC (``\\\\``)."""
    if not path:
        return False
    if path.startswith("\\\\"):
        return True
    if len(path) >= 2 and path[1] == ":" and path[0].isalpha():
        return True
    return False
