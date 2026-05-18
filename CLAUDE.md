# CLAUDE.md — hard rules for any AI agent working on this repo

This file is loaded automatically by Claude Code and similar AI assistants.
It lists **non-negotiable** rules that protect this project's open-source
posture. Violations here are not style preferences — they create legal risk
or betray user trust.

This repo is the **sister project** of `android-llm-bridge` (alb).
The conventions, architecture, and quality bar mirror alb.

---

## 1. What this project is

**windows-llm-bridge** (wlb) — a Windows shell / tool bridge for LLM agents.

The mission: let an LLM agent running on Linux / macOS / a CI host drive a
Windows machine — run `cmd.exe` / `powershell`, push and pull files, invoke
vendor-supplied tooling that only ships as a Windows binary — through a
single LLM-friendly surface (MCP / CLI / Web API).

Typical workflow that wlb is built to support:

1. Cross-compile firmware / binaries on a Linux build host.
2. Drop the artifact onto a Samba / SMB share that the Windows machine sees.
3. Have the LLM agent invoke a Windows-side flashing tool through `wlb`.
4. Read back structured logs (`{ok, data, error, artifacts}`) and decide
   the next step.

Out of scope (do not bolt on):

- Android-specific debugging (that's alb's job).
- A general-purpose Windows automation framework (Sysinternals / Group
  Policy / AD admin). wlb is specifically for **driving Windows-only
  developer tools** from an LLM.

---

## 2. Banned words / identifiers (ABSOLUTE)

The following strings MUST NEVER appear in:

- tracked source / documentation / config / tests
- commit messages
- pull-request titles or bodies
- issue titles / comments we author
- filenames / path components
- any asset that ships to a public surface

### Banned list

```
pax           PAX           paxsz         paxsz.com        com.pax
rk3576        RK3576        rk-sdk        RK SDK           rockchip-sdk
zhangbh       (short internal handle; word-bounded match — the public
              github handle `skyzhangbinghua` IS allowed in LICENSE /
              pyproject / author-attribution contexts, since this is
              a legitimate open-source maintainer identifier)
/home/zhangbh /home/<any-real-username>/<project>
10.0.25.*     10.0.25.46     10.0.25.71    172.16.*  (any RFC1918 internal IP that belongs to a private network)
```

### Why

This project is **open-source, brand-neutral**. Leaking employer names,
internal IPs, or customer-specific SoC identifiers exposes the maintainer
and the project to legal risk and breaks the neutrality the README
promises. The list is enforced by `scripts/check_sensitive_words.sh` and
by the `pre-commit` hook in `.pre-commit-config.yaml`.

---

## 3. How to write about hardware / vendors generically

When you need to describe a real-world setup, pick the generic form:

| Instead of                | Write                                                |
|---------------------------|------------------------------------------------------|
| `RKDevTool` / `upgrade_tool` | `your vendor's Windows flashing tool`             |
| `RK3576`                  | `an ARM SoC target board`                            |
| `Rockchip rk3576`         | `certain ARM SoCs whose vendor only ships flashing tools as Windows binaries` |
| `10.0.25.46`              | `<windows-host>` or `<win-host>`                     |
| `/home/zhangbh/xxx`       | `~/xxx` or `<your-workspace>/xxx`                    |
| `paxsz.com`               | (never mention — remove the line)                    |

Specific protocol names (`ssh`, `smb`, `cmd.exe`, `powershell`, `pwsh`)
and port numbers (`22`, `445`) are fine — they are public technical facts.

---

## 4. Before committing

1. Run `./scripts/check_sensitive_words.sh` (or let the pre-commit hook do it).
2. If the hook flags something: **stop, remove the term, re-stage**. Never
   `--no-verify`.
3. If you genuinely need to add a new word that shouldn't match, extend
   `scripts/check_sensitive_words.sh` carefully — open a PR and discuss first.

### Commit message rules

- No `Co-Authored-By: Claude ...` lines. AI-assisted authorship belongs in
  the human author's commit, not in a synthetic co-author.
- Use the `sky <skyzhangbinghua@gmail.com>` identity (this is the open-source
  maintainer identity, separate from any internal corp identity).
- Keep messages tight and English-first (project is intended for an
  international audience). Chinese is fine when the change is purely
  internal documentation in Chinese.

---

## 5. Quality bar — match alb

This repo's quality bar is whatever `android-llm-bridge` (alb) currently
ships. Concretely:

- **Every capability returns `{ok, data, error, artifacts, timing_ms}`** via
  the `Result[T]` helper in `wlb.infra.result`. No raw strings, no raw dicts,
  no raise-and-pray. Errors are structured with a `code`, a human-readable
  `message`, and an actionable `suggestion`.
- **Every transport implements the `Transport` ABC** in
  `wlb.transport.base`. No bespoke "I know better" transport — extend the
  ABC. Async-only.
- **Every command goes through `check_permissions()` before mutating
  state.** The default blocklist lives in `wlb.infra.permissions`. Add
  Windows-specific dangerous patterns there, not inline.
- **No silent capability additions.** Adding a tool means: capability
  module + MCP tool + CLI subcommand + tests + an entry in the
  registry + a line in the README capability matrix.
- **Tests on every change.** `pytest -q` on a clean clone must stay green.

---

## 6. Pattern of work for new contributions

Before opening a PR, walk through this checklist:

1. Read `REQUIREMENTS.md` to confirm the change is in scope.
2. Read `PLAN.md` to find the milestone the change belongs to (or argue
   for a new one in the PR description).
3. Read `docs/architecture.md` so the new code matches the layer model
   (transport / capability / MCP / CLI).
4. Add the capability spec in `wlb.infra.registry` so `wlb describe`
   surfaces it.
5. Write the smoke / unit test first.
6. Update the README capability matrix when the status flips
   (`planned` → `beta` → `stable`).

---

## 7. Scope of this file

These rules apply to all AI agents working on this repo (Claude Code,
Cursor, Aider, etc.). Personal / cross-project rules belong in the
contributor's `~/.claude/CLAUDE.md` or equivalent, not here.
