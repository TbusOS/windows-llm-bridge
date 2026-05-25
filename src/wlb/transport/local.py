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
import os
import shutil
import struct
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from wlb.transport.base import (
    Interpreter,
    PtySession,
    ShellResult,
    StreamEvent,
    Transport,
)


def _path_size(path: Path) -> int:
    """File → byte size; directory → recursive sum of file sizes."""
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


class LocalPtySession(PtySession):
    """PTY backed by ``pty.openpty()`` + a subprocess. Unix-only.

    Reads / writes go through :func:`asyncio.to_thread` wrapping
    ``os.read`` / ``os.write`` on the master fd. That's slightly less
    efficient than a proper :class:`asyncio.StreamReader`, but it sidesteps
    the lifecycle complexity of connecting a pipe to the event loop and
    keeps the close-on-shutdown story simple (closing the master fd from
    the main task makes the in-flight ``os.read`` thread fail with
    ``OSError``, which we swallow).
    """

    def __init__(self, master_fd: int, proc: asyncio.subprocess.Process) -> None:
        self._master_fd = master_fd
        self._proc = proc
        self._closed = False

    async def read(self, n: int = 4096) -> bytes:
        if self._closed:
            return b""
        try:
            return await asyncio.to_thread(os.read, self._master_fd, n)
        except OSError:
            return b""

    async def write(self, data: bytes) -> None:
        if self._closed or not data:
            return
        try:
            await asyncio.to_thread(os.write, self._master_fd, data)
        except OSError:
            pass

    async def resize(self, cols: int, rows: int) -> None:
        if self._closed:
            return
        try:
            import fcntl
            import termios

            packed = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, packed)
        except (OSError, ModuleNotFoundError):
            # ModuleNotFoundError on Windows where termios is absent.
            pass

    async def wait(self) -> int:
        try:
            await self._proc.wait()
        except Exception:               # noqa: BLE001 — wait is best-effort post-close
            pass
        return self._proc.returncode if self._proc.returncode is not None else -1

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Closing the master fd kills any in-flight os.read with EBADF.
        try:
            os.close(self._master_fd)
        except OSError:
            pass
        if self._proc.returncode is None:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=2)
            except (asyncio.TimeoutError, Exception):           # noqa: BLE001
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await self._proc.wait()
                except Exception:                                # noqa: BLE001
                    pass


class LocalTransport(Transport):
    name = "local"
    supports_files = True       # local cp via shutil — used by tests
    supports_streaming = True   # real line-by-line subprocess streaming (M3.1)
    # Unix uses pty.openpty(); Windows dispatches to wlb.transport._windows_pty
    # which lazy-imports pywinpty (optional `windows-local-pty` extra). The
    # actual runtime success on Windows depends on pywinpty being installed —
    # this flag reports API availability, not "will work today".
    supports_pty = True

    def __init__(self, *, on_windows: bool | None = None) -> None:
        # Allow tests to force the "we're on Windows" code path.
        self._force_windows = on_windows
        self._is_windows = (
            on_windows if on_windows is not None else sys.platform == "win32"
        )

    @property
    def host_label(self) -> str:
        return "local"

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

    async def run_streaming(
        self,
        cmd: str,
        *,
        interpreter: Interpreter = "cmd",
        timeout: int = 30,
    ) -> AsyncIterator[StreamEvent]:
        """Real subprocess streaming: yield one StreamEvent per line of output.

        Two reader tasks pump stdout / stderr into a shared queue; the main
        loop drains the queue, emits ``"line"`` events, and stops once both
        readers have hit EOF. A ``"done"`` event with the exit code is
        always the last yield, even on timeout / spawn failure.
        """
        argv0, prefix = self._resolve_executable(interpreter)
        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                argv0, *prefix, cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            yield StreamEvent(
                kind="done", exit_code=-1, error_code="SYSTEM_DEPENDENCY_MISSING",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return

        queue: asyncio.Queue = asyncio.Queue()
        EOF = object()                                         # sentinel

        async def pipe(reader: asyncio.StreamReader | None, label: str) -> None:
            if reader is None:
                await queue.put((EOF, label))
                return
            try:
                while True:
                    raw = await reader.readline()
                    if not raw:
                        break
                    text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    await queue.put((text, label))
            finally:
                await queue.put((EOF, label))

        out_task = asyncio.create_task(pipe(proc.stdout, "stdout"))
        err_task = asyncio.create_task(pipe(proc.stderr, "stderr"))

        eofs = 0
        deadline = started + max(1, int(timeout))
        try:
            while eofs < 2:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    proc.kill()
                    await proc.wait()
                    yield StreamEvent(
                        kind="done", exit_code=-1, error_code="TIMEOUT_SHELL",
                        duration_ms=int((time.monotonic() - started) * 1000),
                    )
                    return
                try:
                    item, label = await asyncio.wait_for(queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    yield StreamEvent(
                        kind="done", exit_code=-1, error_code="TIMEOUT_SHELL",
                        duration_ms=int((time.monotonic() - started) * 1000),
                    )
                    return
                if item is EOF:
                    eofs += 1
                    continue
                yield StreamEvent(kind="line", line=item, stream=label)   # type: ignore[arg-type]
        finally:
            out_task.cancel()
            err_task.cancel()

        await proc.wait()
        exit_code = proc.returncode or 0
        yield StreamEvent(
            kind="done",
            exit_code=exit_code,
            error_code=None if exit_code == 0 else "SHELL_NONZERO_EXIT",
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def open_pty(
        self,
        *,
        interpreter: Interpreter = "cmd",
        cols: int = 80,
        rows: int = 24,
        term_type: str = "xterm-256color",
    ) -> PtySession:
        """Open a local PTY-backed shell.

        Two backends:

        - Unix → :func:`pty.openpty` + child stdio on the slave fd. Always
          available on Linux / macOS controllers (M3.4).
        - Windows → :mod:`wlb.transport._windows_pty` which lazy-imports
          pywinpty (M3.5; ConPTY on Windows 10 1809+, winpty fallback on
          older). The extra ``windows-local-pty`` must be installed —
          ``uv sync --extra windows-local-pty`` — otherwise raises
          :class:`NotImplementedError` with the install hint.
        """
        if sys.platform == "win32":
            from wlb.transport._windows_pty import open_windows_pty

            return await open_windows_pty(
                interpreter=interpreter,
                cols=cols,
                rows=rows,
                term_type=term_type,
            )

        import pty

        # cmd / powershell on a Unix LocalTransport always falls back to
        # the system shell — same convention as shell() and run_streaming.
        argv0, prefix = self._resolve_executable(interpreter)
        # For PTY we want an *interactive* shell, not -c, so the prompt
        # and line editing actually fire. /bin/sh -i works without rc files.
        if argv0 == "/bin/sh":
            argv = ["/bin/sh", "-i"]
        else:                                # Windows-native (would have raised above)
            argv = [argv0, *prefix]

        master_fd, slave_fd = pty.openpty()
        try:
            try:
                import fcntl
                import termios

                packed = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(master_fd, termios.TIOCSWINSZ, packed)
            except (OSError, ModuleNotFoundError):
                pass

            env = dict(os.environ)
            env["TERM"] = term_type
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                start_new_session=True,
            )
        finally:
            # Child owns slave_fd now; parent must close to get EOF on shell exit.
            try:
                os.close(slave_fd)
            except OSError:
                pass

        return LocalPtySession(master_fd=master_fd, proc=proc)

    async def push(self, local: Path, remote: str) -> ShellResult:
        """Local push = shutil copy. ``remote`` is just another local path."""
        started = time.monotonic()
        if not local.exists():
            return ShellResult(
                ok=False,
                stderr=f"local path not found: {local}",
                duration_ms=int((time.monotonic() - started) * 1000),
                error_code="LOCAL_PATH_NOT_FOUND",
            )
        remote_path = Path(remote).expanduser()
        try:
            remote_path.parent.mkdir(parents=True, exist_ok=True)
            if local.is_dir():
                shutil.copytree(local, remote_path, dirs_exist_ok=True)
            else:
                shutil.copy2(local, remote_path)
        except OSError as e:
            return ShellResult(
                ok=False, stderr=str(e),
                duration_ms=int((time.monotonic() - started) * 1000),
                error_code="REMOTE_PATH_INVALID",
            )
        return ShellResult(
            ok=True,
            stdout=f"transferred {_path_size(remote_path)} bytes (push)",
            duration_ms=int((time.monotonic() - started) * 1000),
            artifacts=[local],
        )

    async def pull(self, remote: str, local: Path) -> ShellResult:
        """Local pull = shutil copy in reverse. ``remote`` is another local path."""
        started = time.monotonic()
        remote_path = Path(remote).expanduser()
        if not remote_path.exists():
            return ShellResult(
                ok=False,
                stderr=f"remote path not found: {remote_path}",
                duration_ms=int((time.monotonic() - started) * 1000),
                error_code="FILE_NOT_FOUND",
            )
        try:
            local.parent.mkdir(parents=True, exist_ok=True)
            if remote_path.is_dir():
                shutil.copytree(remote_path, local, dirs_exist_ok=True)
            else:
                shutil.copy2(remote_path, local)
        except OSError as e:
            return ShellResult(
                ok=False, stderr=str(e),
                duration_ms=int((time.monotonic() - started) * 1000),
                error_code="LOCAL_PATH_NOT_FOUND",
            )
        return ShellResult(
            ok=True,
            stdout=f"transferred {_path_size(local)} bytes (pull)",
            duration_ms=int((time.monotonic() - started) * 1000),
            artifacts=[local],
        )

    async def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "transport": self.name,
            "host": "localhost",
            "is_windows": self._is_windows,
        }
