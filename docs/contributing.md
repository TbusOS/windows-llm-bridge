# Contributing

> Read [`CLAUDE.md`](../CLAUDE.md) first — the rules there are
> non-negotiable. This page is the friendly version.

---

## Setup

```bash
git clone https://github.com/TbusOS/windows-llm-bridge.git
cd windows-llm-bridge
./scripts/install.sh
pre-commit install     # so the sensitive-word guard fires on every commit
```

Verify:

```bash
uv run pytest -q
```

Should be green on a clean clone.

---

## What goes where

| If you're adding…                          | Touch these files                                                          |
|--------------------------------------------|----------------------------------------------------------------------------|
| A new transport (e.g. WinRM)               | `src/wlb/transport/<name>.py` + `transport_factory.py` + `registry.py` + tests |
| A new capability (e.g. `wlb screenshot`)   | `src/wlb/capabilities/<name>.py` + `mcp/tools/<name>.py` + `cli/<name>_cli.py` + tests + `registry.py` + README matrix |
| A new error code                           | `src/wlb/infra/errors.py` + reference from the capability + entry in `docs/errors.md` |
| A new dangerous pattern                    | `src/wlb/infra/permissions.py` + test in `tests/test_smoke.py` (or `tests/infra/`) |

A capability is **not done** until all of the above are in place. This is
the same bar alb sets, and it's what keeps wlb cohesive.

---

## Style

- Async-first. No `time.sleep`; use `await asyncio.sleep`.
- Return `Result[T]`, don't raise from public functions.
- No `print()` in library code. Use Rich console in CLI, structured
  return everywhere else.
- Default to writing no comments. Add them when the *why* is non-obvious.
- Match alb's commit style: short imperative subject; English-first.
- Never `--no-verify`. If the sensitive-word guard flags something, fix
  the text — don't bypass.

---

## Commit messages

- One-line subject, imperative mood.
- Optional body explaining *why* (not *what* — the diff shows that).
- **No `Co-Authored-By: Claude ...`**. AI-assisted authorship goes in
  the human author's identity.

Example:

```
add HttpTransport skeleton for M2

Stubs out wlb.transport.http with the same shape as ssh.py and
registers it as 'planned' in the registry. No behavior yet — the
real implementation depends on the wlb-agent shape we're still
working out in PLAN.md M2.
```

---

## Pull requests

Before opening a PR, run:

```bash
./scripts/check_sensitive_words.sh --all   # should print nothing
uv run pytest -q                            # should be green
uv run ruff check .                         # zero warnings
uv run ruff format --check .                # zero diffs
uv run mypy src                             # zero errors (M1+)
```

The CI re-runs all of the above. PRs that fail CI will not be reviewed
until they go green.

---

## Issue triage

Issues are labeled `M1` / `M2` / `M3` and `bug` / `feature` / `docs` /
`refactor`. Pick something with `good-first-issue` if you're new.

If you want to propose a substantive feature, open a discussion first.
"It would be cool if wlb could…" PRs without an issue tend to be hard
to review because the scope isn't pinned down.

---

## Doc-only contributions

These are welcome and don't require a passing test suite (though
sensitive-word check still applies). Fix typos, clarify wording, add
diagrams — go for it.
