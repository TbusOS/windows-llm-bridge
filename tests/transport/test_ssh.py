"""SshTransport unit tests — asyncssh is monkeypatched, no real network.

For end-to-end tests against a real Windows host see test_ssh_integration.py.
"""

from __future__ import annotations

import asyncio
import base64
import socket
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncssh
import pytest

from wlb.transport.ssh import SshTransport, _encode_powershell


# ─── helpers ─────────────────────────────────────────────────────


def _fake_proc(*, stdout: str = "", stderr: str = "", exit_status: int = 0) -> Any:
    """A minimal stand-in for asyncssh.SSHCompletedProcess."""
    p = MagicMock()
    p.stdout = stdout
    p.stderr = stderr
    p.exit_status = exit_status
    p.returncode = exit_status
    return p


def _fake_conn(run_side_effect: Any = None) -> Any:
    c = MagicMock()
    c.run = AsyncMock(side_effect=run_side_effect) if run_side_effect else AsyncMock(
        return_value=_fake_proc(stdout="ok", exit_status=0)
    )
    c.close = MagicMock()
    c.wait_closed = AsyncMock()
    return c


# ─── encoding helper ─────────────────────────────────────────────


def test_encode_powershell_roundtrips_utf16le() -> None:
    encoded = _encode_powershell("Get-ComputerInfo")
    raw = base64.b64decode(encoded).decode("utf-16-le")
    assert raw == "Get-ComputerInfo"


def test_encode_powershell_unicode_safe() -> None:
    encoded = _encode_powershell('Write-Output "hello 世界"')
    raw = base64.b64decode(encoded).decode("utf-16-le")
    assert "世界" in raw


# ─── config validation ──────────────────────────────────────────


async def test_unconfigured_host_returns_structured() -> None:
    t = SshTransport(host=None, user=None)
    r = await t.shell("ver")
    assert not r.ok
    assert r.error_code == "TRANSPORT_NOT_CONFIGURED"


async def test_missing_key_file_returns_structured(tmp_path: Any) -> None:
    bogus = tmp_path / "does-not-exist"
    t = SshTransport(host="win-host", user="admin", key_path=str(bogus))
    r = await t.shell("ver")
    assert not r.ok
    assert r.error_code == "SSH_KEY_NOT_FOUND"


# ─── happy path (cmd interpreter) ───────────────────────────────


async def test_cmd_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _fake_proc(stdout="Microsoft Windows [Version 10.0.19045.1234]\n", exit_status=0)
    conn = _fake_conn()
    conn.run = AsyncMock(return_value=proc)
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))

    t = SshTransport(host="win-host", user="admin")
    r = await t.shell("ver", interpreter="cmd")

    assert r.ok, r
    assert r.exit_code == 0
    assert "Microsoft Windows" in r.stdout
    # cmd interpreter sends the command verbatim — no powershell wrapping.
    conn.run.assert_awaited_once()
    sent_cmd = conn.run.await_args.args[0]
    assert sent_cmd == "ver"
    # Connection is closed on the way out.
    conn.close.assert_called_once()


async def test_cmd_nonzero_exit_maps_to_shell_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _fake_conn()
    conn.run = AsyncMock(return_value=_fake_proc(stderr="boom", exit_status=1))
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))

    t = SshTransport(host="win-host", user="admin")
    r = await t.shell("does-not-exist", interpreter="cmd")

    assert not r.ok
    assert r.exit_code == 1
    assert r.error_code == "SHELL_NONZERO_EXIT"


# ─── powershell wrapping ────────────────────────────────────────


async def test_powershell_uses_pwsh_first_with_encoded_command(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _fake_proc(stdout="PS-output\n", exit_status=0)
    conn = _fake_conn()
    conn.run = AsyncMock(return_value=proc)
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))

    t = SshTransport(host="win-host", user="admin")
    r = await t.shell("Get-ComputerInfo", interpreter="powershell")

    assert r.ok, r
    assert "PS-output" in r.stdout
    sent_cmd = conn.run.await_args.args[0]
    assert sent_cmd.startswith("pwsh.exe ")
    assert "-NoProfile" in sent_cmd
    assert "-NonInteractive" in sent_cmd
    assert "-EncodedCommand" in sent_cmd
    # The encoded blob must round-trip back to the original script.
    encoded = sent_cmd.rsplit(" ", 1)[-1]
    assert base64.b64decode(encoded).decode("utf-16-le") == "Get-ComputerInfo"


async def test_powershell_falls_back_to_powershell_when_pwsh_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # First call (pwsh.exe) returns "is not recognized"; second (powershell.exe) succeeds.
    call_counter = {"n": 0}

    async def run(cmd: str, **kw: Any) -> Any:
        call_counter["n"] += 1
        if call_counter["n"] == 1:
            assert cmd.startswith("pwsh.exe ")
            return _fake_proc(
                stderr="'pwsh.exe' is not recognized as an internal or external command, "
                       "operable program or batch file.\n",
                exit_status=1,
            )
        assert cmd.startswith("powershell.exe ")
        return _fake_proc(stdout="legacy ok\n", exit_status=0)

    conn = _fake_conn()
    conn.run = AsyncMock(side_effect=run)
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))

    t = SshTransport(host="win-host", user="admin")
    r = await t.shell("Get-Process", interpreter="powershell")

    assert r.ok, r
    assert "legacy ok" in r.stdout
    assert call_counter["n"] == 2


async def test_powershell_reports_unavailable_when_both_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run(cmd: str, **kw: Any) -> Any:
        return _fake_proc(stderr="is not recognized as an internal or external command", exit_status=1)

    conn = _fake_conn()
    conn.run = AsyncMock(side_effect=run)
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))

    t = SshTransport(host="win-host", user="admin")
    r = await t.shell("Get-Process", interpreter="powershell")

    assert not r.ok
    assert r.error_code == "POWERSHELL_NOT_AVAILABLE"


# ─── exception mapping ──────────────────────────────────────────


async def test_connect_timeout_maps_to_timeout_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    async def hangs(*a: Any, **kw: Any) -> Any:
        await asyncio.sleep(10)
        raise RuntimeError("should never reach")

    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", hangs)

    t = SshTransport(host="win-host", user="admin", connect_timeout=1)
    r = await t.shell("ver")
    assert not r.ok
    assert r.error_code == "TIMEOUT_CONNECT"


async def test_permission_denied_maps_to_ssh_auth_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*a: Any, **kw: Any) -> Any:
        raise asyncssh.PermissionDenied("permission denied", "en")

    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", boom)
    t = SshTransport(host="win-host", user="admin")
    r = await t.shell("ver")
    assert not r.ok
    assert r.error_code == "SSH_AUTH_FAILED"


async def test_host_key_rejected_maps_correctly(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*a: Any, **kw: Any) -> Any:
        raise asyncssh.HostKeyNotVerifiable("host key not in known_hosts", "en")

    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", boom)
    t = SshTransport(host="win-host", user="admin")
    r = await t.shell("ver")
    assert not r.ok
    assert r.error_code == "SSH_HOSTKEY_REJECTED"


async def test_oserror_maps_to_host_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*a: Any, **kw: Any) -> Any:
        raise socket.gaierror("name does not resolve")

    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", boom)
    t = SshTransport(host="not-a-real-host", user="admin")
    r = await t.shell("ver")
    assert not r.ok
    assert r.error_code == "SSH_HOST_UNREACHABLE"


async def test_run_timeout_maps_to_timeout_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow_run(*a: Any, **kw: Any) -> Any:
        # asyncssh.TimeoutError inherits ProcessError which has 8 required args.
        raise asyncssh.TimeoutError(
            None, "ping -t 8.8.8.8", None, None, None, None, b"", b"",
            reason="ran out of time",
        )

    conn = _fake_conn()
    conn.run = AsyncMock(side_effect=slow_run)
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))

    t = SshTransport(host="win-host", user="admin")
    r = await t.shell("ping -t 8.8.8.8", interpreter="cmd", timeout=1)
    assert not r.ok
    assert r.error_code == "TIMEOUT_SHELL"


async def test_connection_lost_during_run(monkeypatch: pytest.MonkeyPatch) -> None:
    async def lost(*a: Any, **kw: Any) -> Any:
        raise asyncssh.ConnectionLost("connection lost")

    conn = _fake_conn()
    conn.run = AsyncMock(side_effect=lost)
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))

    t = SshTransport(host="win-host", user="admin")
    r = await t.shell("ver")
    assert not r.ok
    assert r.error_code == "SSH_CONNECTION_LOST"


# ─── health() ───────────────────────────────────────────────────


async def test_health_unconfigured_reports_state() -> None:
    t = SshTransport(host=None, user=None)
    h = await t.health()
    assert h["ok"] is False
    assert h["configured"] is False
    assert "WLB_SSH_HOST" in h["stage"]


async def test_health_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run(cmd: str, **kw: Any) -> Any:
        if cmd == "ver":
            return _fake_proc(stdout="\nMicrosoft Windows [Version 10.0.22631.4112]\n", exit_status=0)
        if cmd.startswith("pwsh.exe "):
            return _fake_proc(stdout="7.4.0\n", exit_status=0)
        # never reach powershell.exe fallback when pwsh succeeds
        raise AssertionError(f"unexpected cmd: {cmd}")

    conn = _fake_conn()
    conn.run = AsyncMock(side_effect=fake_run)
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))

    t = SshTransport(host="win-host", user="admin")
    h = await t.health()
    assert h["ok"] is True
    assert h["configured"] is True
    assert "Microsoft Windows" in h["windows_version"]
    assert h["powershell"].startswith("pwsh.exe ")
    assert "connect_ms" in h


async def test_health_falls_back_when_pwsh_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run(cmd: str, **kw: Any) -> Any:
        if cmd == "ver":
            return _fake_proc(stdout="Microsoft Windows [Version 10.0.19045.1]\n", exit_status=0)
        if cmd.startswith("pwsh.exe "):
            return _fake_proc(stderr="'pwsh.exe' is not recognized", exit_status=1)
        if cmd.startswith("powershell.exe "):
            return _fake_proc(stdout="5.1.19041\n", exit_status=0)
        raise AssertionError(f"unexpected cmd: {cmd}")

    conn = _fake_conn()
    conn.run = AsyncMock(side_effect=fake_run)
    monkeypatch.setattr("wlb.transport.ssh.asyncssh.connect", AsyncMock(return_value=conn))

    t = SshTransport(host="win-host", user="admin")
    h = await t.health()
    assert h["ok"] is True
    assert h["powershell"].startswith("powershell.exe ")
