# Streaming output (M3.1)

By default `wlb tool run` captures the full output of a command and
returns a single Result at the end. For long-running tools — flashers,
signers, multi-stage build packagers — that's a poor fit: the operator
or LLM agent sits blind for 30-300 seconds with no idea whether a
failure has already happened.

M3.1 adds a streaming variant. Output flows line-by-line, regex hits
fire mid-run, and the final verdict still comes back at the end with
the same structured shape.

---

## Surfaces

### CLI

```bash
wlb tool run countdown --stream
wlb tool run flasher --arg image=C:\\stage\\fw.bin --arg port=COM3 --stream
```

Lines from stdout print in the default style; stderr lines are colored
yellow. Each `progress_re` hit prints `→ progress: N%`. Each
`success_re` / `failure_re` hit prints `→ success: '...'` / `→ failure: '...'`
in green / red. A final summary block reports verdict + log path.

Programmatic consumers can ask for line-by-line JSON instead:

```bash
wlb --json tool run countdown --stream
```

One `ToolStreamEvent` JSON object per line, terminated by a `done` event.
This is the right wire format for piping into scripts or a UI process.

### Python API

```python
from wlb.capabilities.tool import run_tool_stream
from wlb.transport.local import LocalTransport

async for ev in run_tool_stream(LocalTransport(), "countdown", {"name": "world"}):
    if ev.kind == "progress":
        update_progress_bar(ev.percent)
    elif ev.kind == "match" and ev.pattern_label == "failure":
        abort_early()                             # we know it's hosed
    elif ev.kind == "done":
        if ev.ok:
            ship_to_qa(ev.output.log_path)
        else:
            file_bug(ev.output)
```

### Transport-level API

`Transport.run_streaming(cmd, *, interpreter, timeout) -> AsyncIterator[StreamEvent]`

`StreamEvent` is the low-level wire shape — capabilities translate to
their own taxonomy (e.g. `ToolStreamEvent` adds progress / match events
on top of raw lines).

---

## Event taxonomy

| Kind         | Fields                                        | Meaning                                         |
|--------------|-----------------------------------------------|-------------------------------------------------|
| `"line"`     | `line`, `stream` (`"stdout"` / `"stderr"`)    | One line of output, just arrived.               |
| `"progress"` | `percent` (0-100 int)                         | `progress_re` matched on the last line.         |
| `"match"`    | `pattern_label`, `match`                      | `success_re` or `failure_re` matched.           |
| `"done"`     | `ok`, `output` (ToolRunOutput), `error_code`  | Terminal. `output` is the same shape `run_tool` returns. |

The `done` event is always last. Capabilities short-circuit on transport
errors (timeout / connection lost / permission denied) — those produce a
`done` with `error_code` set and `output=None`.

---

## Which transports actually stream

| Transport          | Streaming impl                                  | Notes                                                |
|--------------------|-------------------------------------------------|------------------------------------------------------|
| `LocalTransport`   | Real: subprocess pipes + queue merge            | M3.1                                                 |
| `SshTransport`     | Real: `asyncssh.create_process` line readers    | M3.1; PowerShell uses `-EncodedCommand` same as `shell()` |
| `HttpTransport`    | Fallback: `shell()` + post-hoc replay           | M3.2 will add a `/v1/shell/stream` agent endpoint    |
| `HybridTransport`  | Inherits whichever transport it routes to       | n/a — still planned                                  |

`Transport.supports_streaming` reports whether the transport has real
streaming. Code that depends on low latency should check this flag and
fall back gracefully (the fallback API still works — it just doesn't
emit lines until the full output has been captured).

---

## Logging

The streaming tool runner opens the log file in append mode and writes
each line as it arrives, prefixed with `[stdout]` / `[stderr]`. Even if
the controller dies mid-run, the partial log is on disk.

Log path follows the same convention as non-streaming:
`workspace/hosts/<host>/tools/<name>/<ts>.log`.

---

## Limits

- **Line-buffered only.** Binary output (no newlines) won't surface until
  the underlying read fills its buffer. Tools that emit progress as text
  with newlines (the overwhelming majority) work fine.
- **No early abort.** Even if `failure_re` matches halfway through, the
  tool runs to completion. The capability records the match and the
  final `done` reflects the failed verdict, but the runtime doesn't
  kill the remote process. M3.2 may add an opt-in `abort_on_failure_re`.
- **No MCP support.** MCP tools are still synchronous — `wlb_tool_run`
  remains capture-then-return. MCP's `notifications/progress` extension
  is reserved for a future milestone once client support is widespread.
- **HTTP transport falls back.** Until the wlb-agent gains a streaming
  endpoint (M3.2), `HttpTransport.run_streaming` replays captured output
  rather than streaming live.
