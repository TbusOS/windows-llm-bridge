# Skill packs (M3.11)

Per-tool guidance bundles an LLM client can preload before invoking
declared tools. Every tool declared in `wlb-tools.toml` automatically
gets a corresponding **skill pack** — a small Markdown document the
client can fetch via MCP Resources, MCP Tools, HTTP, or the CLI.

The aim: stop forcing the LLM to infer how each tool works from a
terse one-line description. Preload the skill pack once, the agent
knows what the tool is for, what args to provide, what the success /
failure signals look like, and any operator-specific gotchas.

---

## How a skill pack is built

```
ToolSpec from wlb-tools.toml          workspace/wlb-skills/<name>.md
       │                                       │
       │                                       │  (optional, operator-written)
       ▼                                       ▼
┌──────────────────────────┐    ┌──────────────────────────────┐
│ auto-generated header     │ +  │ raw Markdown body            │
│ (interpreter, args,       │    │ (pre-flight, recovery,       │
│  command template,        │    │  links, "the COM port        │
│  regex hits)              │    │  changes every reboot")      │
└──────────────────────────┘    └──────────────────────────────┘
                       │                │
                       └────────┬───────┘
                                ▼
                  one Markdown file the LLM preloads
```

The auto-generated header is built from the ToolSpec every time, so
operator-side `wlb-tools.toml` changes flow through immediately. The
optional author body is appended as-is — wlb doesn't parse it.

### Default file layout

| File                                     | Who writes it | Notes                                            |
|------------------------------------------|---------------|--------------------------------------------------|
| `workspace/wlb-tools.toml`               | operator      | Required for any tool to exist at all.            |
| `workspace/wlb-skills/<tool-name>.md`    | operator      | Optional. Appears under "Notes from the operator". |

---

## Surfaces

Every surface returns the same bytes — pick whichever your client
prefers.

### MCP Resource (canonical)

```
wlb-skill://<tool-name>
```

The canonical preload channel. FastMCP exposes a templated resource;
the client lists `resources/templates` to discover it, then reads
`wlb-skill://<name>` with mime type `text/markdown`. Claude Code,
Cursor, and the MCP inspector all surface this in their context panes.

### MCP Tools

For clients that don't surface Resources in their UI yet:

| Tool              | Returns                                                            |
|-------------------|--------------------------------------------------------------------|
| `wlb_skill_list`  | `{ok, data: {tools_file, skills_dir, skills: [{name, ..., skill_uri, has_author_body}], warnings}}` |
| `wlb_skill_get`   | `{ok, data: {name, skill_uri, markdown, has_author_body}}`         |

`wlb_skill_get` returns `TOOL_NOT_FOUND` for unknown names with a
suggestion pointing at `wlb_skill_list`.

### CLI

```bash
uv run wlb skill list                  # table of every skill pack
uv run wlb skill show <name>           # render the markdown with Rich
uv run wlb skill show <name> --raw     # print raw Markdown for piping
```

`--raw` skips Rich rendering so you can `wlb skill show flasher --raw > flasher.md`
and ship it elsewhere.

### HTTP API

| Method | Path                          | Returns                                                |
|--------|-------------------------------|--------------------------------------------------------|
| GET    | `/api/skills`                 | JSON list with metadata + skill URIs.                  |
| GET    | `/api/skills/{name}`          | Raw `text/markdown` body.                              |
| GET    | `/api/skills/{name}.json`     | Structured wlb Result envelope (for scripted callers). |

---

## Authoring a skill pack

The auto-generated header is fine for many tools. Add an author body
when you have operator knowledge the LLM couldn't infer from the
ToolSpec.

### Step 1 — declare the tool

```toml
# workspace/wlb-tools.toml
[tools.vendor_flasher]
description       = "Flash firmware via vendor tool"
interpreter       = "cmd"
command_template  = '"C:\Tools\vendor_flash.exe" --image "{image}" --port {port}'
args              = ["image", "port"]
timeout           = 600

[tools.vendor_flasher.regex]
progress = '^Progress:\s+(\d{1,3})%'
success  = '^Flash complete'
failure  = '^(ERROR|Failed):'
```

`wlb skill show vendor_flasher --raw` already renders a complete skill
pack at this point — interpreter, args, command template, regex hits,
example invocation, no author body.

### Step 2 — drop operator notes

```bash
mkdir -p workspace/wlb-skills
cat > workspace/wlb-skills/vendor_flasher.md <<'EOF'
## Pre-flight

1. The vendor tool holds the COM port exclusively. Stop its tray
   service first: `Stop-Service -Name VendorFlashSvc`.
2. The port name changes if you unplug the USB hub. Check with
   `wlb cmd "wmic path Win32_SerialPort get DeviceID,Description"`
   before passing `--port`.

## Recovery

- If `Progress:` stalls past 90% for more than 30s, the device is
  stuck in DFU. Power-cycle and retry.
- `ERROR: bad image` means the `.bin` was signed for a different
  variant. Re-run the signer with the right product id.

## Related artifacts

- Build artifact this consumes: `out/firmware/<variant>.bin`
- Signing config: `infra/signing/<variant>.toml`
EOF
```

Now `wlb skill show vendor_flasher` includes that body under "Notes
from the operator". The next time the LLM agent preloads
`wlb-skill://vendor_flasher`, it gets your pre-flight + recovery
guidance for free.

---

## What's emitted (default header)

For every tool, the header always includes:

- The tool name as a code heading.
- The description as a blockquote (if set).
- Quick-reference list: interpreter, required args, timeout, workdir
  (if set), `allow_dangerous` flag (if true).
- The raw command template inside a fenced code block.
- Output-parsing section (only if any regex is set): progress / success
  / failure with the regex bodies.
- An example MCP invocation as JSON.
- A "How wlb runs this" walkthrough: arg substitution, workdir
  wrapping (if set), the interpreter, the log location.
- Either the operator's body OR a placeholder comment telling them
  where to drop one.

The header layout is stable so diffs across tool edits are minimal
and clients can cache by content hash.

---

## Why this exists

Without skill packs, the LLM only sees `wlb_tool_show`'s structured
spec dump. That's enough to call the tool but not to *use it well*:

- Which args matter for which tool variant?
- What does the progress regex look like in practice?
- What's the typical failure mode and the recovery for it?

The operator already knows. Skill packs are the place to write it
down once.

---

## See also

- `wlb_tool_show` — structured spec for one tool (a strict subset of
  the skill pack's metadata).
- `docs/mcp-integration.md` — overview of MCP Resources and how clients
  consume them.
- `wlb-tools.example.toml` — starter template for declaring tools.
