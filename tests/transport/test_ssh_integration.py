"""End-to-end SSH integration tests against a real Windows host.

These are skipped by default. To run:

    export WLB_TEST_SSH_HOST=<win-host>
    export WLB_TEST_SSH_USER=<your-windows-user>
    export WLB_TEST_SSH_KEY=~/.ssh/wlb_ed25519        # optional
    export WLB_TEST_SSH_PORT=22                       # optional
    export WLB_TEST_SSH_KNOWN_HOSTS=~/.ssh/known_hosts # optional; "none" disables

    uv run pytest -q tests/transport/test_ssh_integration.py -m integration

They are marked with the ``integration`` pytest marker so the default
``pytest -q`` run skips them.
"""

from __future__ import annotations

import os

import pytest

from wlb.transport.ssh import SshTransport

pytestmark = pytest.mark.integration


def _settings() -> dict[str, object]:
    host = os.environ.get("WLB_TEST_SSH_HOST")
    if not host:
        pytest.skip("WLB_TEST_SSH_HOST not set — skipping SSH integration tests")
    return {
        "host": host,
        "port": int(os.environ.get("WLB_TEST_SSH_PORT", "22")),
        "user": os.environ.get("WLB_TEST_SSH_USER"),
        "key_path": os.environ.get("WLB_TEST_SSH_KEY"),
        "known_hosts": os.environ.get("WLB_TEST_SSH_KNOWN_HOSTS"),
        "connect_timeout": int(os.environ.get("WLB_TEST_SSH_TIMEOUT", "10")),
    }


@pytest.fixture
def transport() -> SshTransport:
    return SshTransport(**_settings())  # type: ignore[arg-type]


async def test_cmd_ver_returns_windows_banner(transport: SshTransport) -> None:
    r = await transport.shell("ver", interpreter="cmd", timeout=15)
    assert r.ok, f"shell failed: {r}"
    assert "Microsoft Windows" in r.stdout


async def test_powershell_returns_psversion(transport: SshTransport) -> None:
    r = await transport.shell(
        "$PSVersionTable.PSVersion.ToString()",
        interpreter="powershell",
        timeout=30,
    )
    assert r.ok, f"shell failed: {r}"
    # PSVersionTable always reports something like "7.4.0" or "5.1.19041"
    assert any(ch.isdigit() for ch in r.stdout)


async def test_powershell_encodedcommand_preserves_unicode(transport: SshTransport) -> None:
    """Round-trip a multibyte string through ``-EncodedCommand`` to confirm
    UTF-16LE encoding survives the trip."""
    r = await transport.shell(
        'Write-Output "hello 世界"',
        interpreter="powershell",
        timeout=30,
    )
    assert r.ok, f"shell failed: {r}"
    assert "世界" in r.stdout


async def test_health_reports_reachable(transport: SshTransport) -> None:
    h = await transport.health()
    assert h["ok"] is True
    assert h["configured"] is True
    assert "windows_version" in h
    assert "powershell" in h
    assert isinstance(h["connect_ms"], int)


async def test_permission_denied_command_is_refused(transport: SshTransport) -> None:
    """The deny-list must work end-to-end. ``format c:`` is the canonical example.

    This intentionally does NOT execute on the host — the permission check
    fires before the transport call. Safe to run against a real Windows box.
    """
    from wlb.capabilities.cmd import execute as cmd_execute

    r = await cmd_execute(transport, "format c:", timeout=5)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "PERMISSION_DENIED"


async def test_sftp_round_trip(transport: SshTransport, tmp_path: Any) -> None:
    """Push a file to %TEMP% on Windows, pull it back, verify content matches.

    Uses ``%TEMP%`` so we don't need to know an a-priori writable path on
    the target host. The remote staging file is left in %TEMP% to be reaped
    by the OS — wlb deliberately doesn't ship a remote-side delete tool.
    """
    from wlb.capabilities.filesync import pull as cap_pull
    from wlb.capabilities.filesync import push as cap_push

    payload = b"wlb-sftp-roundtrip-" + os.urandom(8).hex().encode()
    src = tmp_path / "rt.bin"
    src.write_bytes(payload)
    dst = tmp_path / "rt_back.bin"

    # Use a stable filename in %TEMP% on the Windows side.
    remote = "%TEMP%\\wlb_sftp_roundtrip.bin"

    push_r = await cap_push(transport, src, remote)
    assert push_r.ok, push_r
    assert push_r.data is not None
    assert push_r.data.bytes_transferred == len(payload)

    pull_r = await cap_pull(transport, remote, dst)
    assert pull_r.ok, pull_r
    assert dst.read_bytes() == payload
