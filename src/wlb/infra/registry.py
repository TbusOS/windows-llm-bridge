"""Metadata-driven registry for transports and capabilities.

Single source of truth for the support matrix shown by:

- ``wlb describe`` (CLI)
- ``wlb_describe`` (MCP tool)
- README capability matrix (manually kept in sync; tests verify both
  registries non-empty so the matrix can't silently empty out)

Adding a new transport or capability requires an entry here, even when the
implementation is still ``planned``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Status = Literal["stable", "beta", "planned"]


@dataclass(frozen=True)
class TransportSpec:
    name: str
    impl_path: str
    status: Status
    requires: list[str] = field(default_factory=list)
    description: str = ""


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    impl_path: str
    cli_command: str
    mcp_tools: list[str]
    supported_transports: list[str]
    status: Status
    description: str = ""


# ─── Transport registry ──────────────────────────────────────────────
TRANSPORTS: list[TransportSpec] = [
    TransportSpec(
        name="ssh",
        impl_path="wlb.transport.ssh.SshTransport",
        status="beta",
        requires=["asyncssh", "Windows OpenSSH Server (TCP 22)"],
        description="SSH to Windows OpenSSH Server. cmd / powershell (pwsh "
                    "preferred with -EncodedCommand). Pooled connections "
                    "keyed by (host, port, user, key, known_hosts, timeout).",
    ),
    TransportSpec(
        name="local",
        impl_path="wlb.transport.local.LocalTransport",
        status="beta",
        requires=[],
        description="Loopback transport used by unit tests and dry runs.",
    ),
    TransportSpec(
        name="http",
        impl_path="wlb.transport.http.HttpTransport",
        status="beta",
        requires=["httpx", "wlb-agent running on the Windows side"],
        description="HTTP fallback when SSH is blocked. Talks to "
                    "scripts/windows-agent/wlb_agent.py over HTTPS with a "
                    "bearer token loaded from a mode-600 file.",
    ),
    TransportSpec(
        name="hybrid",
        impl_path="wlb.transport.hybrid.HybridTransport",
        status="planned",
        requires=["at least one concrete sub-transport"],
        description="Smart router: pick best transport per op (M2).",
    ),
]


# ─── Capability registry ─────────────────────────────────────────────
CAPABILITIES: list[CapabilitySpec] = [
    CapabilitySpec(
        name="status",
        impl_path="wlb.capabilities.status",
        cli_command="wlb status / wlb describe",
        mcp_tools=["wlb_status", "wlb_describe"],
        supported_transports=["ssh", "local", "http"],
        status="beta",
        description="Host info, transport health, capability self-description.",
    ),
    CapabilitySpec(
        name="cmd",
        impl_path="wlb.capabilities.cmd",
        cli_command="wlb cmd <args>",
        mcp_tools=["wlb_cmd"],
        supported_transports=["ssh", "local", "http"],
        status="beta",
        description="Execute via cmd.exe /c with structured stdout/stderr/exit.",
    ),
    CapabilitySpec(
        name="powershell",
        impl_path="wlb.capabilities.powershell",
        cli_command="wlb powershell <args>",
        mcp_tools=["wlb_powershell"],
        supported_transports=["ssh", "local", "http"],
        status="beta",
        description="Execute via pwsh.exe or powershell.exe; auto-detect.",
    ),
    CapabilitySpec(
        name="filesync",
        impl_path="wlb.capabilities.filesync",
        cli_command="wlb fs push / pull",
        mcp_tools=["wlb_push", "wlb_pull"],
        supported_transports=["ssh", "local", "http"],
        status="beta",
        description="File transfer via SFTP (ssh), shutil (local), or "
                    "HTTP multipart (http, single-file in M2.4).",
    ),
    CapabilitySpec(
        name="tool",
        impl_path="wlb.capabilities.tool",
        cli_command="wlb tool list / show / run",
        mcp_tools=["wlb_tool_list", "wlb_tool_show", "wlb_tool_run"],
        supported_transports=["ssh", "local"],
        status="beta",
        description="Run user-declared Windows tools by name with progress/"
                    "success/failure regex parsing and full log capture.",
    ),
    CapabilitySpec(
        name="web",
        impl_path="wlb.api.server",
        cli_command="wlb web / wlb-api",
        mcp_tools=[],
        supported_transports=["ssh", "local", "http"],
        status="beta",
        description="Local dashboard (FastAPI + WebSocket) over the active "
                    "transport. Localhost-only by default, no auth in M3.3.",
    ),
]


def transports_by_status(status: Status) -> list[TransportSpec]:
    return [t for t in TRANSPORTS if t.status == status]


def capabilities_by_status(status: Status) -> list[CapabilitySpec]:
    return [c for c in CAPABILITIES if c.status == status]
