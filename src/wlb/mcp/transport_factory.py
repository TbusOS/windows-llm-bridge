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


def active_settings(force_reload: bool = False) -> ActiveSettings:
    global _cached_settings
    if _cached_settings is None or force_reload:
        _cached_settings = load_active()
    return _cached_settings


def build_transport(*, override: str | None = None) -> Transport:
    """Resolve and instantiate the active transport.

    Precedence: explicit ``override`` > ``WLB_TRANSPORT`` env > settings.
    """
    settings = active_settings()
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
            base_url=os.environ.get("WLB_HTTP_URL"),
            token=os.environ.get("WLB_HTTP_TOKEN"),
        )
    if which == "hybrid":
        return HybridTransport()
    raise ValueError(f"unknown transport: {which}")
