"""Smoke tests — verify the skeleton imports and basic invariants."""

from __future__ import annotations


def test_package_imports() -> None:
    import wlb

    assert wlb.__version__
    assert wlb.__license__ == "MIT"


def test_registry_non_empty() -> None:
    from wlb.infra.registry import CAPABILITIES, TRANSPORTS

    assert len(TRANSPORTS) > 0
    assert len(CAPABILITIES) > 0

    transport_names = {t.name for t in TRANSPORTS}
    # SSH is the M1 primary; local is the test loopback. Both must exist.
    assert {"ssh", "local"}.issubset(transport_names)

    cap_names = {c.name for c in CAPABILITIES}
    required_caps = {"status", "cmd", "powershell"}
    assert required_caps.issubset(cap_names)


def test_error_codes_registered() -> None:
    from wlb.infra.errors import ERROR_CODES, lookup

    assert "TRANSPORT_NOT_CONFIGURED" in ERROR_CODES
    assert "PERMISSION_DENIED" in ERROR_CODES
    spec = lookup("SHELL_NONZERO_EXIT")
    assert spec is not None
    assert spec.category == "transport"


def test_result_helpers() -> None:
    from wlb.infra.result import fail, ok

    r = ok(data={"x": 1})
    assert r.ok
    assert r.to_dict()["data"] == {"x": 1}

    r2 = fail(code="SHELL_NONZERO_EXIT", message="x", suggestion="y")
    assert not r2.ok
    assert r2.error is not None
    assert r2.error.code == "SHELL_NONZERO_EXIT"
    assert r2.to_dict()["error"]["suggestion"] == "y"


async def test_permission_blocklist_denies_format() -> None:
    from wlb.infra.permissions import default_check

    r = await default_check("ssh", "cmd.execute", {"cmd": "format c:"})
    assert r.behavior == "deny"
    assert r.matched_rule is not None


async def test_permission_blocklist_allows_safe() -> None:
    from wlb.infra.permissions import default_check

    r = await default_check("ssh", "cmd.execute", {"cmd": "ver"})
    assert r.behavior == "allow"


async def test_permission_denies_powershell_format_volume() -> None:
    from wlb.infra.permissions import default_check

    r = await default_check("ssh", "powershell.execute", {"cmd": "Format-Volume -DriveLetter D"})
    assert r.behavior == "deny"


def test_workspace_path_structure() -> None:
    from wlb.infra.workspace import iso_timestamp, workspace_path

    p = workspace_path("logs", f"{iso_timestamp()}-test.txt", host="win-host")
    assert p.parent.name == "logs"
    assert p.parent.parent.name == "win-host"
    assert p.parent.parent.parent.name == "hosts"


def test_workspace_rejects_traversal() -> None:
    import pytest

    from wlb.infra.workspace import InvalidHost, workspace_path

    with pytest.raises(InvalidHost):
        workspace_path("logs", "x.txt", host="..")
    with pytest.raises(InvalidHost):
        workspace_path("logs", "x.txt", host="../etc")
