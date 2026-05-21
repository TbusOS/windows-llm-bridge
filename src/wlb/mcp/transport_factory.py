"""Shared transport factory used by both CLI and MCP layers."""

from __future__ import annotations

import os

from wlb.infra.config import ActiveSettings, load_active
from wlb.transport.base import Transport
from wlb.transport.http import HttpTransport
from wlb.transport.hybrid import HybridTransport
from wlb.transport.local import LocalTransport
from wlb.transport.ssh import SshTransport


_cached_settings: ActiveSettings | None = None
_cached_profile_key: str | None = None


def active_settings(
    profile_name: str | None = None, *, force_reload: bool = False
) -> ActiveSettings:
    """Return cached settings, re-resolving when the profile name changes."""
    global _cached_settings, _cached_profile_key
    key = profile_name or os.environ.get("WLB_PROFILE") or "default"
    if force_reload or _cached_settings is None or _cached_profile_key != key:
        _cached_settings = load_active(profile_name)
        _cached_profile_key = key
    return _cached_settings


def build_transport(
    *,
    override: str | None = None,
    profile_name: str | None = None,
) -> Transport:
    """Resolve and instantiate the active transport.

    Precedence for transport selection:
        explicit ``override`` > ``WLB_TRANSPORT`` env > settings (env > profile > default).
    """
    settings = active_settings(profile_name)
    which = override or os.environ.get("WLB_TRANSPORT") or settings.primary_transport

    if which == "ssh":
        return SshTransport(
            host=settings.ssh.host,
            port=settings.ssh.port,
            user=settings.ssh.user,
            key_path=settings.ssh.key_path,
            known_hosts=settings.ssh.known_hosts,
            connect_timeout=settings.ssh.connect_timeout,
        )
    if which == "local":
        return LocalTransport()
    if which == "http":
        return HttpTransport(
            base_url=settings.http.url,
            token_file=settings.http.token_file,
            ca_bundle=settings.http.ca_bundle,
            connect_timeout=settings.http.connect_timeout,
            verify_tls=settings.http.verify_tls,
        )
    if which == "hybrid":
        return HybridTransport()
    raise ValueError(f"unknown transport: {which}")

