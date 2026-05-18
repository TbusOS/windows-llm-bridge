"""Unified Result[T] return type for every capability and transport call.

Conventions:
- Every public capability/MCP/CLI function returns a Result.
- Success: ``Result.ok == True``, ``data`` populated, ``error == None``.
- Failure: ``Result.ok == False``, ``data == None``, ``error`` populated
  with a structured ErrorInfo (code + message + actionable suggestion).
- ``to_dict()`` yields a JSON-safe dict that the MCP / Web API layer can
  send directly to an LLM client.

See docs/architecture.md §3 for the rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar

T = TypeVar("T")

ErrorCategory = Literal[
    "transport",
    "host",
    "permission",
    "timeout",
    "io",
    "input",
    "system",
    "capability",
]


@dataclass(frozen=True)
class ErrorInfo:
    """Structured error. LLM-friendly: code is an enum-like identifier,
    suggestion is an actionable next step the caller can take."""

    code: str
    message: str
    suggestion: str = ""
    category: ErrorCategory = "capability"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "suggestion": self.suggestion,
            "category": self.category,
            "details": self.details,
        }


@dataclass(frozen=True)
class Result(Generic[T]):
    """Canonical return type for every wlb call.

    Fields:
        ok:         True if the call succeeded.
        data:       Domain payload on success. Dataclasses with ``to_dict()``
                    are serialized automatically.
        error:      ErrorInfo on failure. None on success.
        artifacts:  Files written to the workspace during the call (so the
                    caller can find / paginate them).
        timing_ms:  How long the call took (best-effort).
    """

    ok: bool
    data: T | None = None
    error: ErrorInfo | None = None
    artifacts: list[Path] = field(default_factory=list)
    timing_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self._serialize_data(),
            "error": self.error.to_dict() if self.error else None,
            "artifacts": [str(p) for p in self.artifacts],
            "timing_ms": self.timing_ms,
        }

    def _serialize_data(self) -> Any:
        if self.data is None:
            return None
        if hasattr(self.data, "to_dict"):
            return self.data.to_dict()  # type: ignore[no-any-return]
        if hasattr(self.data, "__dict__"):
            return vars(self.data)
        return self.data


def ok(  # noqa: A001 — intentional shadow of builtin in the helper API
    data: T | None = None,
    artifacts: list[Path] | None = None,
    timing_ms: int = 0,
) -> Result[T]:
    """Success helper."""
    return Result(
        ok=True,
        data=data,
        error=None,
        artifacts=artifacts or [],
        timing_ms=timing_ms,
    )


def fail(
    code: str,
    message: str = "",
    suggestion: str = "",
    category: ErrorCategory = "capability",
    details: dict[str, Any] | None = None,
    timing_ms: int = 0,
) -> Result[Any]:
    """Failure helper."""
    return Result(
        ok=False,
        data=None,
        error=ErrorInfo(
            code=code,
            message=message or code,
            suggestion=suggestion,
            category=category,
            details=details or {},
        ),
        artifacts=[],
        timing_ms=timing_ms,
    )
