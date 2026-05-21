"""Loader for ``wlb-tools.toml`` — declarative named tools.

A "tool" is a named, pre-vetted Windows command that wlb can run on
behalf of an LLM agent. The operator declares each tool in a TOML file
once; afterwards the agent invokes ``wlb_tool_run("flasher", {...})``
without having to know the exact command line.

File location (in order):
    1. Path in ``WLB_TOOLS_FILE`` env, if set.
    2. ``<workspace>/wlb-tools.toml`` (default).

Schema (one ``[tool.<name>]`` table per tool):

    [tool.echo]
    description       = "Trivial smoke tool."
    interpreter       = "cmd"                  # cmd | powershell | raw
    command_template  = 'echo {message}'
    args              = ["message"]            # required arg names (optional)
    timeout           = 30                     # seconds (optional, default 300)
    workdir           = 'C:\\stage'            # optional
    allow_dangerous   = false                  # bypass ASK-level permission rules (default false)

    [tool.echo.regex]
    progress = '^(\d{1,3})%'                   # capture group 1 = percent (optional)
    success  = 'echoed:'                        # success marker (optional)
    failure  = 'ERROR:.*'                       # failure marker (optional)

Validation is lenient: malformed individual tools become warnings rather
than killing the whole load. The capability surfaces these warnings so
the operator sees what they fixed-or-didn't.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from wlb.infra.workspace import workspace_root

Interpreter = Literal["cmd", "powershell", "raw"]

DEFAULT_TIMEOUT = 300


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    interpreter: Interpreter
    command_template: str
    args: list[str] = field(default_factory=list)
    timeout: int = DEFAULT_TIMEOUT
    workdir: str | None = None
    allow_dangerous: bool = False
    progress_re: str | None = None
    success_re: str | None = None
    failure_re: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "interpreter": self.interpreter,
            "command_template": self.command_template,
            "args": list(self.args),
            "timeout": self.timeout,
            "workdir": self.workdir,
            "allow_dangerous": self.allow_dangerous,
            "regex": {
                "progress": self.progress_re,
                "success": self.success_re,
                "failure": self.failure_re,
            },
        }


def tools_file_path() -> Path:
    """Return the path to the active ``wlb-tools.toml``."""
    env = os.environ.get("WLB_TOOLS_FILE")
    if env:
        return Path(env).expanduser()
    return workspace_root() / "wlb-tools.toml"


def load_tools() -> tuple[list[ToolSpec], list[str], Path]:
    """Load the active tools file.

    Returns ``(specs, warnings, path)``. ``path`` is always returned so
    callers can display where they looked. A missing file is not an
    error — it just yields ``([], [], path)``.
    """
    path = tools_file_path()
    if not path.exists():
        return [], [], path

    try:
        with path.open("rb") as fp:
            data = tomllib.load(fp)
    except tomllib.TOMLDecodeError as e:
        return [], [f"failed to parse {path}: {e}"], path
    except OSError as e:
        return [], [f"failed to read {path}: {e}"], path

    tools_section = data.get("tool")
    if tools_section is None:
        return [], [], path
    if not isinstance(tools_section, dict):
        return [], [f"{path}: top-level [tool] must be a table"], path

    specs: list[ToolSpec] = []
    warnings: list[str] = []
    for name, raw in tools_section.items():
        if not isinstance(raw, dict):
            warnings.append(f"tool {name!r}: definition must be a table")
            continue
        try:
            specs.append(_parse_one(name, raw))
        except ValueError as e:
            warnings.append(f"tool {name!r}: {e}")
    return specs, warnings, path


def _parse_one(name: str, raw: dict[str, Any]) -> ToolSpec:
    if not name or not name.strip():
        raise ValueError("tool name is empty")
    if not all(c.isalnum() or c in "_-" for c in name):
        raise ValueError(f"tool name {name!r} may only contain [A-Za-z0-9_-]")

    interp = raw.get("interpreter", "cmd")
    if interp not in ("cmd", "powershell", "raw"):
        raise ValueError(f"interpreter must be cmd|powershell|raw, got {interp!r}")

    cmd = raw.get("command_template")
    if cmd is None:
        # Allow legacy `command = ...` as a synonym
        cmd = raw.get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        raise ValueError("command_template (or command) is required and must be a non-empty string")

    description = raw.get("description", "")
    if not isinstance(description, str):
        raise ValueError("description must be a string")

    args_raw = raw.get("args", [])
    if not isinstance(args_raw, list) or not all(isinstance(a, str) for a in args_raw):
        raise ValueError("args must be a list of strings")

    timeout = raw.get("timeout", DEFAULT_TIMEOUT)
    if not isinstance(timeout, int) or timeout <= 0:
        raise ValueError("timeout must be a positive integer (seconds)")

    workdir = raw.get("workdir")
    if workdir is not None and not isinstance(workdir, str):
        raise ValueError("workdir must be a string")

    allow_dangerous = raw.get("allow_dangerous", False)
    if not isinstance(allow_dangerous, bool):
        raise ValueError("allow_dangerous must be a boolean")

    regex_section = raw.get("regex", {})
    if not isinstance(regex_section, dict):
        raise ValueError("regex must be a table")
    progress_re = regex_section.get("progress")
    success_re = regex_section.get("success")
    failure_re = regex_section.get("failure")
    for label, val in (("progress", progress_re), ("success", success_re), ("failure", failure_re)):
        if val is not None and not isinstance(val, str):
            raise ValueError(f"regex.{label} must be a string")

    return ToolSpec(
        name=name,
        description=description,
        interpreter=interp,                                  # type: ignore[arg-type]
        command_template=cmd,
        args=list(args_raw),
        timeout=int(timeout),
        workdir=workdir,
        allow_dangerous=bool(allow_dangerous),
        progress_re=progress_re,
        success_re=success_re,
        failure_re=failure_re,
    )


def find_tool(name: str) -> tuple[ToolSpec | None, list[str], Path]:
    """Convenience: load + look up by name. Returns (spec_or_none, warnings, path)."""
    specs, warnings, path = load_tools()
    for s in specs:
        if s.name == name:
            return s, warnings, path
    return None, warnings, path
