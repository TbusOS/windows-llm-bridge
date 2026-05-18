"""Load .env / .env.local into ``os.environ`` at startup.

Mirrors what most modern python tooling does without pulling in
``python-dotenv`` as a hard runtime dep. Variables already set in the
real environment win over .env values.
"""

from __future__ import annotations

import os
from pathlib import Path


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip matching quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def load_env_files(repo_root: Path | None = None) -> None:
    """Load ``.env`` then ``.env.local`` from ``repo_root`` (or CWD).

    Real environment variables are preserved; .env values only fill gaps.
    """
    root = repo_root or Path.cwd()
    for fname in (".env", ".env.local"):
        for k, v in _parse_env_file(root / fname).items():
            os.environ.setdefault(k, v)
