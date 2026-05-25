"""PTY recording — asciinema v2 ``.cast`` writer (M3.7).

Wraps any :class:`wlb.transport.base.PtySession` in a transparent decorator
that mirrors all I/O into an asciinema-compatible cast file. The recorded
files replay in any asciinema player (``asciinema play``, ``asciinema-player``
JS lib, ``agg`` for GIFs, ``asciinema upload``).

Format reference (asciinema cast file v2):

    https://docs.asciinema.org/manual/asciicast/v2/

In short: line 1 is a single JSON object (header); each subsequent line is
a JSON array ``[seconds_since_start, "o" | "i", "<utf-8 text>"]``. ``"o"``
events are PTY output (what the user saw); ``"i"`` events are stdin
(disabled by default because they record every keystroke incl. passwords).

Activation
----------
Default OFF. Enabled via:

- env: ``WLB_PTY_RECORD=1`` (boolean) — turns on recording for any PTY
  opened via the dashboard / programmatic API in this process.
- env: ``WLB_PTY_RECORD_INPUT=1`` — additionally record stdin keystrokes.
- env: ``WLB_PTY_RECORD_DIR=/path/to/dir`` — override the default output
  directory (default ``workspace/hosts/<host>/pty/<ts>-<interpreter>.cast``).
- profile TOML ``[pty]`` section with ``record``, ``record_input``, ``dir``
  (env wins, as everywhere).

Use from code::

    settings = load_active(profile_name).pty_record
    session = await transport.open_pty(interpreter="cmd", cols=80, rows=24)
    session = maybe_wrap(session, settings,
                        host=transport.host_label,
                        cols=80, rows=24,
                        interpreter="cmd",
                        term_type="xterm-256color")
    # ... use session normally; .cast file fills in as bytes flow.
    await session.close()  # flushes + closes the cast file.

The wrapper is fully ``PtySession``-shaped, so callers (``ws_pty`` pump in
``wlb.api.server``, tests, future MCP tools) don't know recording is on.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from wlb.infra.config import PtyRecordSettings
from wlb.infra.workspace import (
    InvalidHost,
    is_safe_host,
    iso_timestamp,
    workspace_path,
)
from wlb.transport.base import PtySession


class CastRecorder:
    """asciinema v2 cast file writer.

    Writes the header on construction; ``write_output`` / ``write_input``
    append NDJSON event lines. Times are relative to the first event
    (matches asciinema convention; the player resets to t=0 anyway).

    The writer is concurrency-safe via an internal :class:`asyncio.Lock`:
    PTY read and write happen in different tasks and may both fire
    ``write_output`` / ``write_input`` from the wrapping session.
    """

    def __init__(
        self,
        path: Path,
        *,
        cols: int,
        rows: int,
        title: str = "",
        env: dict[str, str] | None = None,
        # Time source override for deterministic tests.
        clock: Any = None,
    ) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self._start: float | None = None
        self._closed = False
        self._clock = clock or time.monotonic
        self._cols = int(cols)
        self._rows = int(rows)

        path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = path.open("w", encoding="utf-8", buffering=1)  # line-buffered
        header: dict[str, Any] = {
            "version": 2,
            "width": self._cols,
            "height": self._rows,
            "timestamp": int(time.time()),
        }
        if title:
            header["title"] = title
        if env:
            header["env"] = env
        self._fp.write(json.dumps(header, separators=(",", ":"), ensure_ascii=False) + "\n")
        self._fp.flush()

    async def write_output(self, data: bytes) -> None:
        await self._write_event("o", data)

    async def write_input(self, data: bytes) -> None:
        await self._write_event("i", data)

    async def _write_event(self, code: str, data: bytes) -> None:
        if not data or self._closed:
            return
        async with self._lock:
            if self._closed:
                return
            now = float(self._clock())
            if self._start is None:
                self._start = now
                ts = 0.0
            else:
                ts = round(now - self._start, 6)
            text = data.decode("utf-8", errors="replace")
            line = json.dumps([ts, code, text], separators=(",", ":"), ensure_ascii=False)
            self._fp.write(line + "\n")
            self._fp.flush()

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._fp.close()
            except OSError:
                pass


class RecordingPtySession(PtySession):
    """``PtySession`` wrapper that mirrors all I/O to a :class:`CastRecorder`.

    The inner session keeps full control of the underlying PTY; this layer
    only observes. ``close()`` closes both inner and recorder. ``wait()``
    delegates straight through.
    """

    def __init__(
        self,
        inner: PtySession,
        recorder: CastRecorder,
        *,
        record_input: bool = False,
    ) -> None:
        self._inner = inner
        self._recorder = recorder
        self._record_input = bool(record_input)
        self._closed = False

    @property
    def cast_path(self) -> Path:
        return self._recorder.path

    async def read(self, n: int = 4096) -> bytes:
        data = await self._inner.read(n)
        if data:
            await self._recorder.write_output(data)
        return data

    async def write(self, data: bytes) -> None:
        if self._record_input and data:
            await self._recorder.write_input(data)
        await self._inner.write(data)

    async def resize(self, cols: int, rows: int) -> None:
        await self._inner.resize(cols, rows)

    async def wait(self) -> int:
        return await self._inner.wait()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Close the inner session first so its terminal output (final exit
        # bytes, prompt) is captured before we shut the writer.
        try:
            await self._inner.close()
        finally:
            await self._recorder.close()


def cast_path_for(
    *,
    host: str,
    interpreter: str,
    override_dir: str | None = None,
) -> Path:
    """Resolve the on-disk path for a fresh cast file.

    Default: ``workspace/hosts/<host>/pty/<ts>-<interpreter>.cast``.
    ``override_dir`` (config key ``[pty].dir`` / env ``WLB_PTY_RECORD_DIR``)
    drops the workspace convention and writes straight into the given
    directory using the same filename. The directory is created if missing.

    ``host`` is sanitized via :func:`wlb.infra.workspace.is_safe_host`; if
    invalid we fall back to ``"unknown"`` so a misconfigured profile can't
    escape the workspace.
    """
    safe_host = host if is_safe_host(host) else "unknown"
    safe_interp = interpreter if interpreter in ("cmd", "powershell", "raw") else "raw"
    ts = iso_timestamp()
    filename = f"{ts}-{safe_interp}.cast"

    if override_dir:
        target_dir = Path(override_dir).expanduser()
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / filename

    try:
        return workspace_path("pty", filename, host=safe_host)
    except InvalidHost:
        # Belt + suspenders — is_safe_host already gates this.
        return workspace_path("pty", filename, host="unknown")


def maybe_wrap(
    session: PtySession,
    settings: PtyRecordSettings | None,
    *,
    host: str,
    cols: int,
    rows: int,
    interpreter: str,
    term_type: str,
) -> PtySession:
    """Return ``session`` wrapped in :class:`RecordingPtySession` if enabled.

    When ``settings`` is ``None`` or ``settings.enabled`` is False, returns
    the input session unchanged — zero overhead, zero file creation. This
    makes it safe to call unconditionally from every ``open_pty`` caller.
    """
    if settings is None or not settings.enabled:
        return session

    path = cast_path_for(
        host=host,
        interpreter=interpreter,
        override_dir=settings.dir,
    )
    recorder = CastRecorder(
        path,
        cols=cols,
        rows=rows,
        title=f"wlb {interpreter} on {host}",
        env={"TERM": term_type, "SHELL": interpreter},
    )
    return RecordingPtySession(session, recorder, record_input=settings.record_input)
