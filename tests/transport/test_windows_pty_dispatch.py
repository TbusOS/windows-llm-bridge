"""LocalTransport.open_pty Windows-side dispatch tests.

We can't run real ConPTY on the Linux CI host, but we can:
1. Verify LocalTransport.open_pty hits the Windows branch when
   ``sys.platform`` is forced to ``"win32"``.
2. Verify the lazy ``winpty`` import is invoked, and a fake
   ``PtyProcess.spawn`` is called with the expected argv + dimensions.
3. Verify WindowsPtySession's async wrappers shovel data to / from the
   underlying pywinpty proc.
4. Verify the helpful ImportError message when ``pywinpty`` isn't installed.

Real ConPTY behavior is covered by Windows-walkthrough integration —
documented in ``docs/pty.md``.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from wlb.transport._windows_pty import WindowsPtySession, _pick_argv, open_windows_pty
from wlb.transport.local import LocalTransport


# ─── argv selection (platform-independent — just string logic) ────


def test_pick_argv_cmd_defaults_to_cmd_exe() -> None:
    assert _pick_argv("cmd") == ["cmd.exe"]


def test_pick_argv_raw_uses_cmd_exe() -> None:
    """`raw` collapses to cmd.exe on Windows-local; ssh/raw stays through SSH."""
    assert _pick_argv("raw") == ["cmd.exe"]


def test_pick_argv_powershell_prefers_pwsh(monkeypatch: pytest.MonkeyPatch) -> None:
    """When pwsh.exe exists on PATH, pick it over powershell.exe."""
    def fake_which(name: str) -> str | None:
        return "/fake/pwsh.exe" if name == "pwsh.exe" else None

    monkeypatch.setattr("wlb.transport._windows_pty.shutil.which", fake_which)
    assert _pick_argv("powershell") == ["pwsh.exe", "-NoProfile", "-NoLogo"]


def test_pick_argv_powershell_falls_back_to_powershell_exe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("wlb.transport._windows_pty.shutil.which", lambda name: None)
    assert _pick_argv("powershell") == ["powershell.exe", "-NoProfile", "-NoLogo"]


# ─── lazy import + dispatch ──────────────────────────────────────


async def test_open_windows_pty_raises_when_not_on_windows() -> None:
    """Direct call from a non-Windows host should error — it's an internal helper."""
    with pytest.raises(NotImplementedError) as exc:
        await open_windows_pty(
            interpreter="cmd", cols=80, rows=24, term_type="xterm-256color",
        )
    assert "non-Windows" in str(exc.value)


async def test_open_windows_pty_missing_pywinpty_returns_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When pywinpty isn't installed, the user gets a clear install command."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winpty", None)   # ImportError on import
    with pytest.raises(NotImplementedError) as exc:
        await open_windows_pty(
            interpreter="cmd", cols=80, rows=24, term_type="xterm-256color",
        )
    assert "pywinpty" in str(exc.value)
    assert "windows-local-pty" in str(exc.value)


async def test_local_transport_open_pty_dispatches_to_windows_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sys.platform == "win32" → LocalTransport.open_pty goes through
    open_windows_pty, which calls winpty.PtyProcess.spawn with the right argv."""
    monkeypatch.setattr(sys, "platform", "win32")

    fake_proc = MagicMock()
    fake_proc.read = MagicMock(return_value=b"")
    fake_proc.isalive = MagicMock(return_value=False)
    fake_proc.exitstatus = 0

    fake_winpty = types.ModuleType("winpty")
    fake_winpty.PtyProcess = MagicMock()
    fake_winpty.PtyProcess.spawn = MagicMock(return_value=fake_proc)
    monkeypatch.setitem(sys.modules, "winpty", fake_winpty)

    t = LocalTransport()
    session = await t.open_pty(interpreter="cmd", cols=120, rows=40)
    assert isinstance(session, WindowsPtySession)

    fake_winpty.PtyProcess.spawn.assert_called_once()
    call = fake_winpty.PtyProcess.spawn.call_args
    args, kwargs = call.args, call.kwargs
    assert args[0] == ["cmd.exe"]
    assert kwargs["dimensions"] == (40, 120)         # (rows, cols)
    assert "TERM" in kwargs["env"]


async def test_local_transport_dispatches_powershell_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("wlb.transport._windows_pty.shutil.which", lambda name: None)

    fake_proc = MagicMock()
    fake_winpty = types.ModuleType("winpty")
    fake_winpty.PtyProcess = MagicMock()
    fake_winpty.PtyProcess.spawn = MagicMock(return_value=fake_proc)
    monkeypatch.setitem(sys.modules, "winpty", fake_winpty)

    t = LocalTransport()
    await t.open_pty(interpreter="powershell", cols=80, rows=24)
    sent_argv = fake_winpty.PtyProcess.spawn.call_args.args[0]
    assert sent_argv[0] == "powershell.exe"
    assert "-NoProfile" in sent_argv


# ─── WindowsPtySession behavior (mocked PtyProcess) ──────────────


async def test_session_read_returns_bytes_from_pywinpty() -> None:
    """pywinpty may return either str or bytes — we normalize to bytes."""
    proc = MagicMock()
    proc.read = MagicMock(return_value=b"hello-bytes")
    proc.isalive = MagicMock(return_value=True)

    session = WindowsPtySession(proc=proc)
    chunk = await session.read(64)
    assert chunk == b"hello-bytes"
    proc.read.assert_called_once_with(64)


async def test_session_read_normalizes_str_to_bytes() -> None:
    proc = MagicMock()
    proc.read = MagicMock(return_value="hello-str")
    proc.isalive = MagicMock(return_value=True)

    session = WindowsPtySession(proc=proc)
    chunk = await session.read(64)
    assert chunk == b"hello-str"


async def test_session_read_after_close_returns_empty() -> None:
    proc = MagicMock()
    session = WindowsPtySession(proc=proc)
    await session.close()
    chunk = await session.read(64)
    assert chunk == b""


async def test_session_write_calls_pywinpty_write() -> None:
    proc = MagicMock()
    proc.write = MagicMock()

    session = WindowsPtySession(proc=proc)
    await session.write(b"echo hi\n")
    proc.write.assert_called_once_with(b"echo hi\n")


async def test_session_resize_calls_setwinsize_rows_first() -> None:
    """pywinpty's setwinsize takes (rows, cols) — verify we pass the right order."""
    proc = MagicMock()
    proc.setwinsize = MagicMock()

    session = WindowsPtySession(proc=proc)
    await session.resize(cols=120, rows=40)
    proc.setwinsize.assert_called_once_with(40, 120)


async def test_session_wait_returns_exitstatus() -> None:
    proc = MagicMock()
    proc.isalive = MagicMock(return_value=False)
    proc.exitstatus = 0

    session = WindowsPtySession(proc=proc)
    assert await session.wait() == 0


async def test_session_wait_polls_until_dead() -> None:
    """Process alive on first check, dead on second → wait returns exit code."""
    proc = MagicMock()
    alive_calls = [True, True, False]
    proc.isalive = MagicMock(side_effect=lambda: alive_calls.pop(0) if alive_calls else False)
    proc.exitstatus = 137

    session = WindowsPtySession(proc=proc)
    code = await session.wait()
    assert code == 137
    assert proc.isalive.call_count >= 3


async def test_session_close_calls_terminate_with_force() -> None:
    proc = MagicMock()
    proc.terminate = MagicMock()

    session = WindowsPtySession(proc=proc)
    await session.close()
    proc.terminate.assert_called_once()


async def test_session_close_is_idempotent() -> None:
    proc = MagicMock()
    proc.terminate = MagicMock()

    session = WindowsPtySession(proc=proc)
    await session.close()
    await session.close()
    # Only the first call should hit terminate.
    assert proc.terminate.call_count == 1
