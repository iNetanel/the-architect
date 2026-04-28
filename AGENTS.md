# AGENTS.md — The Architect

> **Canonical rules live in `documentation/Best Practices.md`. Read it before every task.**
> This file adds tool-specific notes for OpenCode sessions working on this repo.

---

## Stack

- **Python 3.11+** — single package `the_architect/`, CLI entry point `the_architect/cli.py`
- **Click** (CLI), **Rich** (terminal output), **Loguru** (logging), **Pydantic v2** (models), **httpx** (HTTP), **questionary** (interactive prompts)
- **Hatchling** build backend; `pyproject.toml` is the source of truth for SemVer
- No database, no web server — pure CLI tool that shells out to OpenCode or Claude Code

---

## Developer Commands

```bash
# Install (editable + dev deps)
pip install -e ".[dev]"

# Full CI check order — run in this sequence
ruff check .
mypy the_architect/
pytest tests/ -v --tb=short

# Single test file
pytest tests/test_circuit.py -v

# With coverage
pytest tests/ --cov=the_architect --cov-report=term-missing

# Format (ruff, not black)
ruff format .
```

CI runs: `ruff check .` → `mypy the_architect/` → `pytest tests/` on Python 3.11 and 3.12.

---

## Build Counter — MANDATORY

**Every completed task must increment `__build__` in `/version.py` (project root) by 1.**

```python
# /version.py — find this line and add 1:
__build__ = 10001   # was 10000
```

- File: `/workspaces/the-architect/version.py` — **NOT** `the_architect/version.py`
- `the_architect/version.py` reads SemVer from `importlib.metadata` — do not edit it for build bumps
- One task = one bump. Not one per file. Not one per tool call.
- Verify before marking Done: `grep "__build__" version.py`

---

## Versioning — Two Files, Two Roles

| File | Contains | Edit when |
|------|----------|-----------|
| `/version.py` | `__build__`, `__version__`, `__banner__` | Every task (build bump); human-only for SemVer |
| `pyproject.toml` | SemVer (`version = "..."`) | Human-only on release |
| `the_architect/version.py` | Reads SemVer via `importlib.metadata` | Never edit directly |

---

## Code Standards (non-defaults)

- **Line length: 100** (ruff configured in `pyproject.toml`)
- **Loguru only** — `print()` is forbidden for internal logging; allowed only for user-facing CLI output where Rich is not appropriate
- **Pydantic v2** — use `model_validate`, not `parse_obj`
- **`tomllib`** (built-in, Python 3.11+) — never add `tomli` as a dependency
- **Type hints on all public functions** — mypy strict mode is on
- **Docstrings on all public functions and classes**
- Async tests use `pytest-asyncio` with `asyncio_mode = "auto"` (no decorator needed)

---

## Directory Layout

```
the_architect/
  cli.py              # Click CLI, all commands defined here
  config.py           # ArchitectConfig (Pydantic), load_config(), write_config()
  exceptions.py       # Custom exception hierarchy
  core/
    runner.py         # Task execution engine — streams provider output
    planner.py        # Planning integration — calls provider with architect agent
    circuit.py        # Circuit breaker state machine
    provider.py       # Provider detection and abstraction
    opencode_provider.py   # OpenCode-specific runner
    claude_code_provider.py # Claude Code-specific runner
    progress.py       # PROGRESS.md read/write
    tasks.py          # Task discovery, Task/TaskPlan models
    retrospective.py  # Retrospective review runner
    architect_md.py   # ARCHITECT.md read/write
    structure.py      # Project structure detection
    tmux.py           # tmux dashboard integration
    dashboard.py      # Monitor state and display
    ...
  resources/
    opencode_template.json   # Template written to user projects on `architect init`
    prompts/
      architect.md    # Prompt for the planning agent
      reviewer.md     # Prompt for the retrospective reviewer agent
      execution-protocol.md  # Injected into every task run

dev/opencode/         # OpenCode config for developing The Architect itself
  opencode.json       # Agent definitions, model assignments, permissions
  prompts/            # Agent prompt files (base.md, master.md, backend.md, …)

documentation/
  Best Practices.md   # CANONICAL RULES — read before every task
  The Architect Project.md  # Full architecture reference
tests/                # One test file per module, e.g. test_circuit.py
version.py            # BUILD COUNTER lives here — edit this for every task
```

---

## OpenCode Setup for This Repo

The repo's own OpenCode config is at `dev/opencode/opencode.json` (not the project root).
OpenCode must be launched from `dev/opencode/` or with `OPENCODE_CONFIG` pointing there.

Agents defined: `master` (default), `backend`, `frontend`, `qa-fast`, `qa-deep`, `explore`, `debug`, `docs`, `knowledge`. `build` and `plan` are disabled.

**Prompt files** are in `dev/opencode/prompts/`. Changes to these files or to `dev/opencode/opencode.json` require human approval — flag with `PROMPT UPDATE SUGGESTED`.

---

## CHANGELOG

Every user-visible change needs an entry under `## [Unreleased]` in `CHANGELOG.md`.
Format: `### Added / Changed / Fixed / Removed` with build number in parentheses.

Internal refactors, test-only changes, and doc-only changes do **not** need a CHANGELOG entry — but still need a build bump.

---

## Testing Quirks

- `conftest.py` auto-clears `OPENCODE_CONFIG` and `OPENCODE_CONFIG_DIR` env vars for every test — prevents dev config leaking into tests that create their own `opencode.json`
- No external services required for the test suite
- New behaviour needs test coverage — PRs without tests will be asked to add them

---

## What The Architect Does (context for working on it)

The Architect wraps OpenCode or Claude Code to add autonomous task planning, execution, retry/circuit-breaker, retrospective review, and persistent memory (`ARCHITECT.md`) to any project. It never writes application code itself — it orchestrates the AI CLI that does.

Key runtime files it creates in user projects (not this repo):
- `tasks/` — numbered task files (T01, T02, …) and `INSTRUCTIONS.md`
- `PROGRESS.md` — parsed by regex; format is strict (see `documentation/Best Practices.md`)
- `ARCHITECT.md` — persistent project intelligence, grows across sessions
- `.architect/` — logs, circuit breaker state, lock file, monitor state

---

## Commit Messages (when human asks)

```
type: short description (build XXXX)
```

Types: `fix`, `feat`, `docs`, `refactor`, `test`, `chore`, `perf`, `ci`. Always include the build number.
