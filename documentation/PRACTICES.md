# PRACTICES — The Architect

> **Canonical, tool-agnostic rules for every agent and contributor.**
> Every agent — regardless of which CLI invoked it (OpenCode, Claude Code, or any
> future provider) — reads this document **before every task**.
> This is the single source of truth. `AGENTS.md`, `CLAUDE.md`, and `ARCHITECT.md`
> are convention files for specific tools and all point here.

---

## Why This Document Exists

Different AI CLIs use different convention files:

| Tool | Convention file |
|------|-----------------|
| OpenCode | `AGENTS.md` |
| Claude Code | `CLAUDE.md` |
| The Architect (itself) | `ARCHITECT.md` |
| Future tools | probably their own `*.md` |

If each tool's convention file held the real rules, we would have to maintain the
same rules in three places and they would drift. Instead:

1. **This file** holds the canonical rules
2. Every tool's convention file is a thin pointer that says *"read
   `documentation/PRACTICES.md` first"*
3. Tool-specific notes (file layout quirks, which flag to use, etc.) stay in the
   tool's own convention file

If you are an AI agent reading this: treat every rule here as non-negotiable
unless the human explicitly overrides it in the current request.

---

## Non-Negotiable Rules

These apply to every task, every agent, every PR. Violations are always CRITICAL.

1. **No hardcoded secrets, API keys, or URLs** — use environment variables
2. **No `print()` for logging** — use `loguru` (see Logging section)
3. **No new dependency without stating the reason** in the completion report
4. **Never contradict an existing architecture decision silently** — flag conflicts first
5. **Never create a new file if an existing file should be modified instead** — check first
6. **Never touch git unless the human explicitly asks** — no commits, no pushes, no tags
7. **Never ask "should I proceed?"** — the human's request is the approval
8. **Tests must pass before marking any task Done** — never skip a failing test
9. **Never run destructive commands** (`rm -rf`, database drops, force pushes,
   history rewrites) without explicit human approval
10. **Bump the build number on every completed task** — see the Build Number section
11. **Follow the File Integrity Protocol when `integrity = true`** — see the File Integrity section

---

## Build Number — MANDATORY BUMP

The Architect uses a **global build counter** in the root `version.py` that tracks
cumulative effort across every session, every task, every PR.
It is **not** just a release counter — it is the project's permanent audit trail.

### The rule

**Every completed task MUST increment `__build__` by 1.**

| Action | Bump? |
|--------|-------|
| Task finishes successfully | ✅ +1 |
| Task fails (even after retries) | ❌ no bump |
| Retrospective fix-up task (R01, R02, …) completes | ✅ +1 |
| Agent reads a file | ❌ no bump |
| Agent writes or edits a file | ❌ no bump (bump only at end of task) |
| PR merged (even docs-only) | ✅ +1 |

One task = one bump. Not one per file written. Not one per tool call.
The bump is the **final act of a completed task**, just before the completion report.

### File location

The build counter lives in the **project root** `version.py`, NOT in
`the_architect/version.py` (which only exports SemVer from pyproject metadata).

```
/workspaces/the-architect/
├── version.py              ← EDIT THIS — contains __build__ = <N>
├── the_architect/
│   └── version.py          ← DO NOT EDIT for build bumps
```

### How to bump

```python
# /version.py — find this line and add 1:
__build__   = 1042   # was 1041
```

That's the only change needed. `__full_version__` and `__banner__` are derived
strings — they will pick up the new build automatically.

### Verifying the bump

Before marking a task Done:

```bash
grep "__build__" /workspaces/the-architect/version.py
```

The value should be exactly one higher than before your task started.
If it is not, bump it now.

### Why this matters

The build number is a monotonic, never-resetting record of cumulative effort.
By the time v1.0.1 ships, the build number reflects every single task done to
get there — not just the release tag. It makes PRs traceable, runs comparable,
and the project's history honest.

**If you are an AI agent and you forget this bump, your completion report is
incomplete and the task is not Done.**

---

## Versioning Scheme

```
MAJOR.MINOR.PATCH (build BUILD)

v1.0.0 (build 10042)
 ----    -----------
  |           |
  |           +-- Global build counter — monotonic, never resets
  |               Increments once per completed task or PR
  |               Always at least 5 digits
  |
  +------------ Semantic version
                Increments on human-tagged releases only
```

### When to bump each component

| Component | When | Who |
|-----------|------|-----|
| `MAJOR` | Breaking change | Human only — on release |
| `MINOR` | New feature, backwards compatible | Human only — on release |
| `PATCH` | Bug fix | Human only — on release |
| `BUILD` | Every completed task | **Every agent, every task** |

### Major version build floor

Each major version bumps the build floor so the numbers stay readable across
long project lifetimes:

| Version | Build floor |
|---------|-------------|
| v1.x.x | 10000+ |
| v2.0.0 | 20000 (jumps) |
| v3.0.0 | 30000 (jumps) |

Only the human bumps the floor — agents only increment by 1.

### Source of truth

- `__version__` (SemVer) lives in `pyproject.toml` and is mirrored in `/version.py`
- `__build__` lives **only** in `/version.py`
- `the_architect/version.py` reads `__version__` via `importlib.metadata` — do
  not edit it to change the version; edit `pyproject.toml` and `/version.py`
  together

---

## CHANGELOG — How to Write Entries

The changelog follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
format, with our build-counter extension in the release header.

### Unreleased section (always present)

Every PR adds an entry under `## [Unreleased]` at the top of `CHANGELOG.md`.
When the human cuts a release, `[Unreleased]` gets renamed to the version and
a fresh empty `[Unreleased]` is added.

```markdown
## [Unreleased]

### Added
- New `--watch` flag for re-planning on file changes (build 10043)

### Fixed
- Circuit breaker no longer trips on cooldown waits (build 10044)
```

### Release entry format

```markdown
## [1.0.1] (build 10058) — 2026-05-10

### Added
- Feature description — one bullet per user-visible change

### Changed
- Change description

### Fixed
- Bug-fix description

### Removed
- Removal description
```

### Section headings (Keep a Changelog)

Use only these, in this order when present:

1. `### Added` — new features
2. `### Changed` — changes to existing functionality
3. `### Deprecated` — features that will be removed
4. `### Removed` — features removed in this version
5. `### Fixed` — bug fixes
6. `### Security` — security fixes

### When to add a CHANGELOG entry

| Change | CHANGELOG entry? |
|--------|------------------|
| User-facing feature | ✅ Yes |
| User-facing bug fix | ✅ Yes |
| CLI flag added/removed | ✅ Yes |
| New config option | ✅ Yes |
| Dependency added | ✅ Yes — under Changed |
| Internal refactor (no user impact) | ❌ No |
| Test-only change | ❌ No |
| Doc-only change | ❌ No (but still bump build) |
| Typo fix in a user-facing string | ✅ Yes — under Fixed |

The build counter is bumped **regardless** of whether a CHANGELOG entry is added.

---

## Commit Message Format (when the human asks for commits)

```
type: short description (build XXXX)
```

Allowed types: `fix`, `feat`, `docs`, `refactor`, `test`, `chore`, `perf`, `ci`.
Always include the build number in parentheses at the end.

Examples:
```
feat: add --watch flag for auto-replanning (build 10043)
fix: circuit breaker no longer trips on cooldown wait (build 10044)
docs: clarify build bump rule in Best Practices (build 10045)
```

Agents never commit unless the human explicitly asks.

---

## Before Every Task — Checklist

1. Read this document (`documentation/PRACTICES.md`)
2. Read the tool's convention file (e.g. `AGENTS.md`) for tool-specific notes
3. Read `PROGRESS.md` if executing a task in a run
4. Read the task file in full
5. Read relevant project documentation (`documentation/`)
6. Read the files you are about to modify, in full

---

## During Every Task

### Think before you act

For every file edit, command, or tool call, answer internally:

```
WHAT:  What exactly am I about to do?
WHY:   What problem does this solve?
RISK:  What could go wrong?
CHECK: How will I verify it worked?
```

### Work in small, verified steps

- One logical change at a time — one file, one concern, one reason
- Verify each change before moving to the next
- Never batch unrelated changes into one edit
- If a task feels large, decompose it into a numbered list first, then work
  through it one item at a time

### Prove it works

After every meaningful change:
1. Run the relevant tests — if none exist, write them first
2. If tests fail — fix them. Do NOT move on.
3. Run the linter and type checker
4. Show passing output — "it should work" is not proof

### When you're stuck, change course

Same approach fails twice → stop, write why, pick a meaningfully different
approach. Three different approaches fail → stop entirely, write what you
tried and what you need. Wait for human input.

---

## Code Standards

### Python

- **Python 3.11+** — use built-in `tomllib`, not `tomli`
- **Type hints on ALL public functions** — no exceptions
- **Docstrings on ALL public functions and classes**
- **Loguru** for logging — never `print()`
- **Pydantic v2** — use `model_validate`, not `parse_obj`
- **Ruff** for linting — `ruff check .` must pass
- **Mypy** for type checking — `mypy the_architect/` must pass (or documented
  exceptions)
- **Line length: 100** (configured in pyproject.toml)

### Testing

```bash
# During a task — run only the relevant test file
pytest tests/test_<module>.py -v --tb=short

# End of task — run full suite
pytest tests/ -v --tb=short 2>&1 | tail -20
```

- New behaviour needs test coverage. No exceptions.
- Async code uses `pytest-asyncio` (configured with `asyncio_mode = "auto"`)
- Never skip a failing test — fix it before marking Done

### Logging

```python
from loguru import logger

logger.info("Task started")
logger.warning(f"Retry {n} of {max_retries}")
logger.error(f"Failed: {exc!r}")
```

Never use `print()` for internal logging. `print()` may be used only for
user-facing CLI output where `rich` is not appropriate.

### File reading discipline

Files in `the_architect/` can be large. Never read multiple large files back-to-back.

- Use small `limit=` reads for initial overview
- Read specific sections with `offset=` and `limit=` during implementation
- Plan first, then read only what you need for each step
- Never read more than 2 large files before writing first code

---

## PROGRESS.md — Required Format

When executing task runs, the runner reads these exact lines to determine state:

```
**Tasks completed:** N
**Next task to run:** TXX
```

Task completion is detected by grepping `TXX.*Done`.

Both lines MUST be present and correctly formatted at the end of every task.

---

## File Integrity Protocol

When `integrity = true` is active (the default), every build agent MUST follow this protocol before editing any **existing** file:

```
1. Copy the existing file to architect_eval_<filename> in the same directory.
   Do NOT create snapshots for brand-new files.
2. Make your change to the original file normally.
   Never create snapshots for architect_eval_* files themselves.
3. Validate the rewritten file against the snapshot:
   check for truncation, missing sections, or large unexpected size shrinkage.
4. If validation passes — delete the architect_eval_* snapshot immediately.
5. If validation fails — restore from the snapshot, diagnose, retry, then delete.
```

**Never leave `architect_eval_*` files behind after a task completes.**
Any leftover snapshot is treated by The Architect as a corruption signal
and will block inter-task reassessment and retrospective review.

This rule applies to all agents on all providers whenever `integrity = true`.

---

## Inter-Task Reassessment

After each task, The Architect may run a lightweight reassessment pass. By default,
Force Reassessment is enabled, so pending tasks are reassessed after every task.
If Force Reassessment is disabled, the build agent triggers reassessment by
including a structured outcome block at the end of its work:

```
=== TASK OUTCOME ===
Summary:      brief description of what was done
Files:        list of created/modified files
Verification: tests run, linter result
Impact:       Downstream impact: possible   ← or "none"
```

When `Downstream impact: possible` is set, or when a task fails, the architect
agent reviews the pending task files and updates them to reflect what just
changed. This prevents later tasks from doing redundant or conflicting work.

**Rules for build agents:**

- Always include the `=== TASK OUTCOME ===` block at the end of the task
- Set `Downstream impact: possible` when you added, renamed, or significantly changed
  an interface, module, schema, or file that later tasks reference
- Set `Downstream impact: none` when your changes are self-contained (e.g., a test fix,
  a doc update, a style change)
- Never fabricate `Downstream impact: possible` when the impact is genuinely local

---

## Cycle Validation Gate

After each retrospective round, The Architect runs a deterministic validation
gate (`_validate_cycle`) before declaring the run complete. The gate confirms:

- All planned tasks are `Done`, or are `Failed`/`Blocked` with a successful
  matching R-task recovery.
- No `architect_eval_*` snapshots remain.
- `tasks/PROGRESS.md` parses cleanly.

The result is appended to `tasks/PROGRESS.md` under `## Cycle Validation` and
to `tasks/SUMMARY.md` under `### Validation Details`. A failed validation
triggers another retrospective round; if rounds are exhausted, the run is
reported as failed.

The retrospective reviewer is not allowed to issue destructive recovery
(`git checkout`, `git reset`, `git restore`, `git clean`, `rm -rf`,
broad file deletion, commits, tags, pushes) unless the original task asked
for it. Any reviewer-created fix-up task containing those instructions is
refused before execution.

---

## Infinite Loop Mode

Infinite Loop is a runtime-only TUI option that keeps rerunning the same goal
with the same provider, model, scope, and feature flags after each successful
planning → execution → retrospective → validation cycle. It is intentionally
not exposed as a CLI flag or persisted to `architect.toml`, to avoid
accidentally enabling it in CI or non-interactive runs.

Loop guarantees:

- The loop only advances after a fully successful cycle. A failing task,
  failed retrospective fix-up, or failed validation gate stops the loop.
- Without Persistent mode, Infinite Loop runtime-raises
  `retrospective_rounds` to at least 2 so a failed validation can trigger
  one recovery retrospective without silently turning into 30-retry
  Persistent mode.
- Each iteration resets task numbering at `T01` and writes a fresh
  `tasks/PROGRESS.md`. Previous iterations are archived under
  `tasks/archive/YYYY-MM-DD_HHMMSS/`.
- Lifecycle traces are written to `.architect/logs/the_architect.log` and
  `.architect/logs/architect_runtime.log`; both files survive per-iteration
  log archive cleanup so live-failure evidence is never wiped between
  iterations.

Stop the loop with `Ctrl+C`, the pause menu, or `architect cancel`.

---

## Documentation Updates

After any task that introduces or changes any of the following, update the
relevant documentation before reporting completion:

- A new module, public API, CLI flag, or config option
- A new architectural decision or pattern
- A dependency added or removed
- A discovered constraint or limitation not previously documented

Documentation updates do NOT require human approval.

Changes to agent prompts (`dev/opencode/prompts/*.md`), `opencode.json`, or
this file (`documentation/PRACTICES.md`) REQUIRE human approval.
Flag with `PROMPT UPDATE SUGGESTED` in the completion report.

---

## Definition of Done — Every Task

- [ ] Every item in the task file implemented
- [ ] Relevant tests pass (`pytest tests/ -v --tb=short`)
- [ ] Linter clean (`ruff check .`)
- [ ] Type checker clean (`mypy the_architect/`) or exceptions documented
- [ ] Type hints and docstrings on all public functions
- [ ] No `print()` for logging — loguru only
- [ ] `PROGRESS.md` updated: task marked Done, next task set
- [ ] **`__build__` in `/version.py` incremented by 1**
- [ ] `CHANGELOG.md` updated if the change is user-visible
- [ ] Completion report written with status, confidence, and what was done
