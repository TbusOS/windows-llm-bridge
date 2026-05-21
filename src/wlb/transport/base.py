"""Transport ABC — the architectural core interface.

All concrete transports (ssh, local, http, hybrid) implement this ABC.
Capabilities call into transports; capabilities know nothing about which
transport they're running on.

Design echoes ``alb.transport.base`` so contributors who've worked on alb
feel at home here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from wlb.infra.permissions import PermissionResult, default_check

Interpreter = Literal["cmd", "powershell", "raw"]


@dataclass(frozen=True)
class ShellResult:
    """Result of a shell command execution via any transport."""

    ok: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    artifacts: list[Path] = field(default_factory=list)
    error_code: str | None = None  # Matches infra/errors.ERROR_CODES


StreamKind = Literal["line", "progress", "match", "done"]


@dataclass(frozen=True)
class StreamEvent:
    """One event from a streaming shell run (M3).

    Yielded by :meth:`Transport.run_streaming` and consumed by capabilities
    that want live progress (e.g. :func:`wlb.capabilities.tool.run_tool_stream`).

    Event shapes by ``kind``:

    - ``"line"``: one line off stdout / stderr.
      ``line`` is the text (without the trailing newline);
      ``stream`` is ``"stdout"`` or ``"stderr"``.

    - ``"progress"``: a regex hit on the configured progress pattern.
      ``percent`` is the parsed 0-100 integer.

    - ``"match"``: a regex hit on the configured success / failure pattern.
      ``pattern_label`` is ``"success"`` or ``"failure"``; ``match`` is the
      matched substring.

    - ``"done"``: terminal event. ``exit_code`` and ``error_code`` describe
      the outcome (same conventions as :class:`ShellResult`).

    Capabilities map these to their own domain-level events; transports
    only emit ``"line"`` / ``"done"`` natively (progress / match are
    capability-level enrichments).
    """

    kind: StreamKind
    line: str | None = None
    stream: Literal["stdout", "stderr"] | None = None
    percent: int | None = None
    pattern_label: Literal["progress", "success", "failure"] | None = None
    match: str | None = None
    exit_code: int = 0
    error_code: str | None = None
    duration_ms: int = 0


@dataclass(frozen=True)
class TransferEvent:
    """One progress / completion event from a streaming file transfer.

    Yielded by ``Transport.push_stream`` / ``pull_stream`` (M2).
    Two ``kind`` values:

    - ``"progress"`` — intermediate update. ``percent`` may be ``None``
      when the underlying tool doesn't emit progress.
    - ``"done"`` — terminal event. ``ok=True`` on success, ``ok=False``
      with ``error`` populated on failure.
    """

    kind: str  # "progress" | "done"
    bytes_transferred: int = 0
    percent: float | None = None
    file: str | None = None
    duration_ms: int = 0
    ok: bool = True
    error: str | None = None


class Transport(ABC):
    """All concrete transports implement this ABC.

    Guarantees:

    - Methods are async.
    - Errors are returned structurally (do not raise from public methods).
    - ``check_permissions()`` is called before any state-changing op
      (capabilities enforce this; transports trust their callers but
      provide the hook for transport-specific overlays).
    """

    name: str = "base"
    supports_files: bool = False        # True for ssh / http / hybrid (M2+)
    supports_streaming: bool = False    # True for ssh (M2+)

    # ── Shell ─────────────────────────────────────────────────────
    @abstractmethod
    async def shell(
        self,
        cmd: str,
        *,
        interpreter: Interpreter = "cmd",
        timeout: int = 30,
    ) -> ShellResult:
        """Run ``cmd`` through ``interpreter`` (cmd / powershell / raw).

        ``interpreter="cmd"`` means the implementation wraps the command
        in ``cmd.exe /c ...`` semantics. ``interpreter="powershell"`` means
        ``powershell.exe`` or ``pwsh.exe`` with ``-Command``. ``raw`` means
        the implementation runs the command string verbatim (used by
        capabilities that have already built the full invocation).
        """

    # ── Streaming shell (M3) ──────────────────────────────────────
    async def run_streaming(
        self,
        cmd: str,
        *,
        interpreter: Interpreter = "cmd",
        timeout: int = 30,
    ) -> AsyncIterator[StreamEvent]:
        """Run ``cmd`` and yield :class:`StreamEvent`s as output arrives.

        The default fallback simply calls :meth:`shell` and replays the
        captured output as ``"line"`` events followed by a ``"done"``
        event. Transports that have real streaming (Local subprocess,
        SSH via ``conn.create_process``) override this with line-by-line
        emission so progress / failure regex can fire mid-run.

        Capabilities that consume this iterator should:

        1. Treat ``"line"`` events as cumulative output (append to a log).
        2. Run their regex parsing on each line as it arrives.
        3. Trust ``"done"`` as the terminal event.

        Yielding from a default fallback is correct but defeats the
        streaming benefit — code that cares about latency should check
        whether ``supports_streaming`` is True on the transport.
        """
        result = await self.shell(cmd, interpreter=interpreter, timeout=timeout)
        for line in (result.stdout or "").splitlines():
            yield StreamEvent(kind="line", line=line, stream="stdout")
        for line in (result.stderr or "").splitlines():
            yield StreamEvent(kind="line", line=line, stream="stderr")
        yield StreamEvent(
            kind="done",
            exit_code=result.exit_code,
            error_code=result.error_code,
            duration_ms=result.duration_ms,
        )

    # ── File transfer (M2) ────────────────────────────────────────
    async def push(self, local: Path, remote: str) -> ShellResult:
        """Push a local file/dir to the Windows host."""
        raise NotImplementedError(
            f"{self.name} transport does not implement push() yet"
        )

    async def pull(self, remote: str, local: Path) -> ShellResult:
        """Pull a remote file/dir from the Windows host to local."""
        raise NotImplementedError(
            f"{self.name} transport does not implement pull() yet"
        )

    async def push_stream(
        self, local: Path, remote: str
    ) -> AsyncIterator[TransferEvent]:
        """Streamed push — yields TransferEvent updates. M2."""
        raise NotImplementedError(
            f"{self.name} transport does not support push_stream() yet"
        )
        yield  # pragma: no cover — async-generator marker

    async def pull_stream(
        self, remote: str, local: Path
    ) -> AsyncIterator[TransferEvent]:
        """Streamed pull — yields TransferEvent updates. M2."""
        raise NotImplementedError(
            f"{self.name} transport does not support pull_stream() yet"
        )
        yield  # pragma: no cover — async-generator marker

    # ── Permission hook ───────────────────────────────────────────
    async def check_permissions(
        self, action: str, input_data: dict[str, Any]
    ) -> PermissionResult:
        """Return an allow / ask / deny decision for ``action``.

        Default consults the global permission engine. Subclasses can
        override to add transport-specific layers (e.g. an HTTP transport
        may layer rate-limit-style rules on top).
        """
        return await default_check(self.name, action, input_data)

    # ── Health / info ─────────────────────────────────────────────
    @abstractmethod
    async def health(self) -> dict[str, Any]:
        """Connectivity & state snapshot for ``wlb status``.

        Returns a flat dict suitable for direct ``json.dumps()``. Keys are
        transport-specific but always include ``ok: bool`` and ``transport: <name>``.
        """
