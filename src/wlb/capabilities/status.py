"""status capability — environment + transport health snapshot.

Powers ``wlb status``, ``wlb_status``, ``wlb describe``, and ``wlb_describe``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from wlb import __version__
from wlb.infra.registry import CAPABILITIES, TRANSPORTS
from wlb.infra.result import Result, ok
from wlb.transport.base import Transport


@dataclass(frozen=True)
class StatusReport:
    version: str
    transport: str
    health: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "transport": self.transport,
            "health": self.health,
        }


async def describe() -> Result[dict[str, Any]]:
    """Return the full transport + capability matrix.

    Pure metadata — no transport call. Safe to call before any setup.
    """
    return ok(
        data={
            "version": __version__,
            "transports": [asdict(t) for t in TRANSPORTS],
            "capabilities": [asdict(c) for c in CAPABILITIES],
        }
    )


async def status(transport: Transport) -> Result[StatusReport]:
    """Return a health snapshot for the active transport."""
    health = await transport.health()
    return ok(
        data=StatusReport(
            version=__version__,
            transport=transport.name,
            health=health,
        )
    )
