"""Transport.host_label — workspace bucket identifier (M3.7).

Used by per-host artifacts (tool logs, PTY recordings). Defaults safely
to the transport's name so a misconfigured transport can't escape the
workspace via a malicious host string.
"""

from __future__ import annotations

from wlb.transport.http import HttpTransport
from wlb.transport.local import LocalTransport
from wlb.transport.ssh import SshTransport


# ─── LocalTransport ─────────────────────────────────────────────


def test_local_host_label_is_local() -> None:
    assert LocalTransport().host_label == "local"


# ─── SshTransport ───────────────────────────────────────────────


def test_ssh_host_label_uses_configured_host() -> None:
    t = SshTransport(host="win-host.example", user="u")
    assert t.host_label == "win-host.example"


def test_ssh_host_label_falls_back_when_host_unset() -> None:
    t = SshTransport(host=None, user="u")
    assert t.host_label == "ssh"


def test_ssh_host_label_rejects_traversal_chars() -> None:
    # "../etc" must NOT propagate into workspace/hosts/<label>/...
    t = SshTransport(host="../etc", user="u")
    assert t.host_label == "ssh"


# ─── HttpTransport ──────────────────────────────────────────────


def test_http_host_label_extracts_hostname() -> None:
    t = HttpTransport(base_url="https://win-host.example:8443/", token="x")
    assert t.host_label == "win-host.example"


def test_http_host_label_handles_plain_http() -> None:
    t = HttpTransport(base_url="http://127.0.0.1:9000", token="x")
    assert t.host_label == "127.0.0.1"


def test_http_host_label_falls_back_without_base_url() -> None:
    t = HttpTransport(base_url=None, token="x")
    assert t.host_label == "http"


def test_http_host_label_falls_back_on_unsafe_hostname() -> None:
    # `urlparse` happily parses junk into .hostname; is_safe_host gates it.
    t = HttpTransport(base_url="http://-bad-leading/", token="x")
    assert t.host_label == "http"
