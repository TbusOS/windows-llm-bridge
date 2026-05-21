"""MCP tools: wlb_push, wlb_pull."""

from __future__ import annotations

from typing import Any

from wlb.capabilities.filesync import pull as cap_pull
from wlb.capabilities.filesync import push as cap_push
from wlb.mcp.transport_factory import build_transport


def register(mcp) -> None:  # noqa: ANN001
    @mcp.tool()
    async def wlb_push(local: str, remote: str) -> dict[str, Any]:
        """Push a local file or directory to the Windows host via SFTP.

        When to use:
            - Stage a firmware binary, signed image, or driver for a Windows-only tool.
            - Drop a build artifact onto a known C:\\path so a subsequent
              wlb_cmd / wlb_powershell can act on it.

        When NOT to use:
            - Tiny scripts you can pass as ``-Command`` inline.
            - Anything you can already see via a shared SMB mount (M2.2 will
              add automatic SMB path translation).

        Args:
            local: source path on the controller (file or directory).
            remote: destination path on the Windows host (e.g. C:\\stage\\fw.bin).

        Returns:
            Standard Result {ok, data: {local, remote, direction, bytes_transferred,
            duration_ms}, error, artifacts, timing_ms}.
        """
        transport = build_transport()
        r = await cap_push(transport, local, remote)
        return r.to_dict()

    @mcp.tool()
    async def wlb_pull(remote: str, local: str) -> dict[str, Any]:
        """Pull a remote file or directory from the Windows host via SFTP.

        Note the argument order: ``remote`` first, ``local`` second — mirrors
        the human-natural "from → to" reading direction.

        When to use:
            - Capture flash logs / WER dumps / installer output written on Windows.
            - Round-trip a tool's stdout if the tool writes to a file instead of stdout.

        Args:
            remote: source path on the Windows host.
            local: destination path on the controller (file or directory).

        Returns:
            Standard Result {ok, data: {local, remote, direction, bytes_transferred,
            duration_ms}, error, artifacts, timing_ms}.
        """
        transport = build_transport()
        r = await cap_pull(transport, remote, local)
        return r.to_dict()
