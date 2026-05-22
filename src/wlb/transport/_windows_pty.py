"""Windows-local PTY support for :class:`wlb.transport.local.LocalTransport`.

Imported lazily — this module references ``winpty`` (pywinpty) which is
only installed via the optional ``windows-local-pty`` extra and only on
Windows. The :class:`WindowsPtySession` itself is defined unconditionally
so type checkers and mock-based tests can see it on any platform.

When `wlb` runs on a Linux/macOS controller and talks to a remote Windows
host over SSH, you don't need this module — :class:`SshTransport.open_pty`
already gives you a PTY channel on the pooled connection. ConPTY is only
useful when `wlb` runs *on* Windows itself.

Backend selection: pywinpty uses ConPTY by default on Windows 10 1809+
and falls back to the winpty shim on older systems.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from typing import Any

from wlb.transport.base import Interpreter, PtySession


class WindowsPtySession(PtySession):
    """PTY session backed by pywinpty's ``PtyProcess``.

    pywinpty's API is sync I/O on a background process. We wrap each
    ``read`` / ``write`` / ``setwinsize`` in :func:`asyncio.to_thread` so
    they don't block the event loop. ``wait`` polls ``isalive`` /
    ``exitstatus`` because the library doesn't expose a notification.
    """

    def __init__(self, proc: Any) -> None:
        self._proc = proc
        self._closed = False

    async def read(self, n: int = 4096) -> bytes:
        if self._closed:
            return b""
        try:
            data = await asyncio.to_thread(self._proc.read, n)
        except (OSError, EOFError):
            return b""
        if data is None:
            return b""
        # pywinpty returns str by default unless spawned with encoding=None.
        # Be defensive — accept both.
        if isinstance(data, bytes):
            return data
        return data.encode("utf-8", "replace")

    async def write(self, data: bytes) -> None:
        if self._closed or not data:
            return
        try:
            await asyncio.to_thread(self._proc.write, data)
        except (OSError, EOFError):
            pass

    async def resize(self, cols: int, rows: int) -> None:
        if self._closed:
            return
        try:
            # pywinpty: setwinsize(rows, cols) — note the order.
            await asyncio.to_thread(self._proc.setwinsize, rows, cols)
        except (OSError, AttributeError):
            pass

    async def wait(self) -> int:
        # pywinpty doesn't have an awaitable wait; poll exitstatus.
        # 50 ms poll interval — generous enough not to burn CPU, tight
        # enough that the WS pump reports exit promptly.
        while True:
            if not self._proc.isalive():
                status = self._proc.exitstatus
                return status if status is not None else -1
            await asyncio.sleep(0.05)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await asyncio.to_thread(self._proc.terminate, True)   # force=True
        except (OSError, AttributeError):
            try:
                await asyncio.to_thread(self._proc.close)
            except (OSError, AttributeError):
                pass


def _pick_argv(interpreter: Interpreter) -> list[str]:
    """Pick the Windows shell argv for a given wlb interpreter."""
    if interpreter == "powershell":
        for candidate in ("pwsh.exe", "powershell.exe"):
            if shutil.which(candidate):
                return [candidate, "-NoProfile", "-NoLogo"]
        # Last resort: trust PATH to resolve at spawn time.
        return ["powershell.exe", "-NoProfile", "-NoLogo"]
    # cmd / raw both run cmd.exe — the user's wlb-side "raw" choice
    # only really matters for non-interactive command_template work.
    return ["cmd.exe"]


async def open_windows_pty(
    *,
    interpreter: Interpreter,
    cols: int,
    rows: int,
    term_type: str,
) -> PtySession:
    """Spawn a Windows ConPTY-backed shell and return a :class:`WindowsPtySession`.

    Raises :class:`NotImplementedError` if pywinpty isn't installed.
    """
    if sys.platform != "win32":
        raise NotImplementedError(
            "open_windows_pty called on a non-Windows platform — "
            "this is a bug in the dispatcher."
        )
    try:
        import winpty                # type: ignore[import-not-found]
    except ImportError as e:
        raise NotImplementedError(
            "LocalTransport PTY on Windows needs `pywinpty`. "
            "Install with: uv sync --extra windows-local-pty"
        ) from e

    argv = _pick_argv(interpreter)
    # pywinpty.PtyProcess.spawn:
    #   - argv: command + args
    #   - dimensions: (rows, cols) — Windows-style
    #   - env: optional, inherits if None
    # We want bytes-mode I/O for parity with Unix; pywinpty defaults to str
    # but accepts encoding=None for bytes. Different versions of pywinpty
    # spell this differently — try `encoding=None`, fall back to default.
    spawn_kwargs: dict[str, Any] = {
        "dimensions": (rows, cols),
    }
    # If the user has a TERM-equivalent expectation (xterm-256color), pass
    # it as env so ncurses-style apps inside cmd see something reasonable.
    # ConPTY doesn't actually parse it; this is just for the child's env.
    import os

    spawn_kwargs["env"] = {**os.environ, "TERM": term_type}

    try:
        proc = await asyncio.to_thread(
            winpty.PtyProcess.spawn, argv, **spawn_kwargs
        )
    except Exception as e:
        raise ConnectionError(f"pywinpty spawn failed: {e}") from None

    return WindowsPtySession(proc=proc)
