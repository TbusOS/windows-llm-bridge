"""Local transport — runs commands on the host that wlb is installed on.

Primary purpose: hermetic unit tests (capability code paths can be exercised
without a real Windows machine). Also useful for dry-running wlb against
itself: a contributor on a Windows box can ``wlb cmd "ver"`` locally to
confirm the capability layer is wired before configuring SSH.

Caveat: when used on Linux/macOS, ``interpreter="cmd"`` / ``"powershell"``
fall back to ``/bin/sh`` since the underlying ``cmd.exe`` / ``pwsh`` may
not be available. This keeps tests cross-platform; the capability layer
catches the Linux-side fallback and skips Windows-specific assertions.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import time
from typing import Any

from wlb.transport.base import Interpreter, ShellResult, Transport


class LocalTransport(Transport):
    name = "local"
    supports_files = False
    supports_streaming = False

    def __init__(self, *, on_windows: bool | None = None) -> None:
        # Allow tests to force the "we're on Windows" code path.
        self._force_windows = on_windows
        self._is_windows = (
            on_windows if on_windows is not None else sys.platform == "win32"
        )

    def _resolve_executable(self, interpreter: Interpreter) -> tuple[str, list[str]]:
        """Return the ``(argv0, prefix_args)`` pair for the given interpreter.

        On Windows: real ``cmd.exe`` / ``powershell.exe`` / ``pwsh.exe``.
        On non-Windows: fall back to ``/bin/sh`` so unit tests stay portable.
        """
        if interpreter == "raw":
            return ("/bin/sh", ["-c"]) if not self._is_windows else ("cmd.exe", ["/c"])
        if not self._is_windows:
            return ("/bin/sh", ["-c"])
        if interpreter == "cmd":
            return ("cmd.exe", ["/c"])
        if interpreter == "powershell":
            for candidate in ("pwsh.exe", "powershell.exe"):
                if shutil.which(candidate):
                    return (candidate, ["-NoProfile", "-NonInteractive", "-Command"])
            return ("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command"])
        raise ValueError(f"unknown interpreter: {interpreter}")

    async def shell(
        self,
        cmd: str,
        *,
        interpreter: Interpreter = "cmd",
        timeout: int = 30,
    ) -> ShellResult:
        argv0, prefix = self._resolve_executable(interpreter)
        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                argv0,
                *prefix,
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            return ShellResult(
                ok=False,
                exit_code=-1,
                stderr=str(e),
                duration_ms=int((time.monotonic() - started) * 1000),
                error_code="SYSTEM_DEPENDENCY_MISSING",
            )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ShellResult(
                ok=False,
                exit_code=-1,
                stderr=f"command exceeded {timeout}s timeout",
                duration_ms=int((time.monotonic() - started) * 1000),
                error_code="TIMEOUT_SHELL",
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        exit_code = proc.returncode or 0
        return ShellResult(
            ok=(exit_code == 0),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            error_code=None if exit_code == 0 else "SHELL_NONZERO_EXIT",
        )

    async def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "transport": self.name,
            "host": "localhost",
            "is_windows": self._is_windows,
        }
