# Changelog

All notable changes to The Architect are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/) with a global build counter.
See [README — Versioning](README.md#versioning) for the full scheme.
Full rules in [`documentation/Best Practices.md`](documentation/Best%20Practices.md).

---

## [Unreleased]

<!--
Every completed task appends a bullet here and bumps __build__ in /version.py.
When cutting a release, rename [Unreleased] to the version and add a fresh
empty [Unreleased] above it. Use Keep a Changelog section headings:
Added / Changed / Deprecated / Removed / Fixed / Security.
-->

### Fixed

- README banner image now uses absolute GitHub URL so it renders correctly
  on PyPI (relative `assets/` path is not resolvable on pypi.org) (build 10004).

- `.gitignore` no longer excludes `the_architect/resources/prompts/architect.md`
  from version control. The previous rule `ARCHITECT.md` was intended to ignore
  the runtime-generated project memory file but matched case-insensitively on
  macOS/Windows filesystems, silently dropping the lowercase resource file
  from the initial commit and breaking 60+ tests on CI (build 10002).
- Circuit-breaker replan tests (`test_replan_resets_circuit_state_on_success`,
  `test_replan_discovers_new_task_files`) now patch `stream_provider` instead
  of the removed `stream_opencode` symbol. The tests passed locally on dev
  machines where `opencode` is installed (because the real call succeeded)
  but failed on CI where it is not (build 10002).
- Retry-command tests accept both legacy wording ("not marked Done") and
  current wording ("not in a terminal state") so the assertions survive the
  terminal-status vocabulary change (build 10002).

### Added

- PyPI Trusted Publishing job in CI — tagged releases (`v*`) auto-publish
  to PyPI via OIDC, no API tokens required (build 10001).
- Python 3.13 to the CI test matrix and `pyproject.toml` classifiers
  (build 10001).
- `SECURITY.md` with private vulnerability-disclosure policy (build 10001).
- `CODE_OF_CONDUCT.md` based on the Contributor Covenant 2.1
  (build 10001).
- `.github/dependabot.yml` — weekly grouped updates for pip dependencies
  and GitHub Actions, running through the existing CI pipeline before
  any merge (build 10001).
- `-V` / `--version` flag on the root `architect` command — previously
  only the `architect version` subcommand worked despite the flag being
  documented (build 10001).

### Changed

- Rewrapped 25 long lines in `the_architect/cli.py` to comply with the
  configured 100-character limit; CI now passes `ruff check` cleanly
  (build 10001).
- Reformatted `tests/test_runner.py` to satisfy `ruff format --check`
  (build 10001).

---

## [1.0.0] (build 10000) — 2026-04-27

**Initial public release of The Architect** — an autonomous development
lifecycle layer that wraps OpenCode or Claude Code to turn any coding
agent into a fire-and-forget development partner.

### Providers

- OpenCode and Claude Code supported as AI CLI backends.
- Auto-detection of installed providers with interactive selection on first run.
- Provider preference persisted via `architect.toml` or the
  `ARCHITECT_PROVIDER` environment variable.
- Pass-through for all provider API keys
  (`OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.).

### Planning

- Goal decomposition into numbered task files (`T01`, `T02`, …) with a
  shared `INSTRUCTIONS.md`.
- Three scope levels — `simple`, `standard`, `complex` — that shape
  task granularity.
- Context injection from PRDs, specs, or any document via repeatable
  `--context FILE_OR_DIR` or the `ARCHITECT_CONTEXT` env var.
- Goal auto-extraction from context files (scans for `## Goal`,
  `## Objective`, `## Requirements`).
- Project structure auto-detection — repo type, languages, frameworks,
  components, and dependency graph — captured in `ARCHITECT.md` for
  every planning session.
- Previous-run archiving with timestamped directories so no history is lost.
- Planning resilience — 3 attempts with a 30-second pause on transient failures.

### Execution

- Live streaming of provider output to the terminal.
- Multi-signal completion detection combining promise tags,
  `PROGRESS.md` state, exit codes, and output analysis.
- Anti-hallucination guard — a stuck task is never reported complete,
  even if the agent claims success.
- Configurable pause between tasks.
- Authoritative post-attempt status reconciliation — the runner writes
  `Done` or `Failed (N attempts)` into `PROGRESS.md` after every task
  attempt so the next iteration skips resolved tasks. Prevents
  re-execution of tasks the executor completed but forgot to mark.

### Task Lifecycle and Status Vocabulary

- Full terminal-status vocabulary in `PROGRESS.md`: `Pending`, `Done`,
  `Failed`, `Blocked`. `Failed` is written by the runner when all
  retries are exhausted; `Blocked` is reserved for resource-limit
  persistence.
- `reconcile_task_status()`, `task_is_resolved()`, and `task_status()`
  helpers in `the_architect.core.progress` — used by the runner,
  planner, and CLI to honour the status vocabulary consistently.
- `ProgressState.failed_tasks`, `ProgressState.blocked_tasks`, and
  `ProgressState.resolved_tasks` properties for downstream consumers
  (dashboards, summaries) that need the full lifecycle picture.
- Planner only re-plans non-terminal tasks — failed work is never
  silently re-queued as if it were fresh.
- `execution-protocol.md`, `reviewer.md`, and `architect.md` prompts
  document the expanded status vocabulary so agents do not get confused
  when the runner stamps statuses they did not write.

### Retry and Circuit Breaker

- Automatic retry with per-attempt model fallbacks
  (`retry_model_2`, `retry_model_3`).
- Context carry — a summary of the previous attempt is injected into the
  next retry's instruction.
- Circuit breaker with three independent failure detectors:
  - no-progress detection (zero file writes across attempts),
  - same-error fingerprinting (path- and line-number-normalised), and
  - token-decline detection (model output shrinking across attempts).
- Targeted task replan when all fallback models are exhausted.
- Circuit state persisted to `.architect/circuit.json` — survives process restarts.

### Rate Limit Handling

- Cooldown detection for HTTP 429, 529, and provider-specific text patterns.
- Minimum 1-hour cooldown enforced regardless of the suggested retry-after.
- Cooldown waits do not consume retry slots.
- Free-tier model rotation (OpenCode + OpenRouter only) — swaps models
  mid-stream without restarting the task.

### Retrospective Review

- Reviewer agent runs after execution completes.
- Reads the actual code, runs the test suite, and assesses completeness
  and quality.
- Emits `R`-prefixed fix-up tasks when it finds issues.
- Configurable rounds — 1 by default, 2 in persistent mode.

### Persistent Memory

- `ARCHITECT.md` — project intelligence that accumulates decisions,
  constraints, lessons, and best practices across sessions.
- `PROGRESS.md` — operational state between tasks with a strict
  regex-parsed format.
- `SUCCESS.md` — full run summary written after every execution.
- Planning history auto-appended to `ARCHITECT.md` after each session.

### Modes

- Interactive mode with arrow-key screens for configuring runs,
  resuming, and selecting providers.
- `--persistent` — up to 30 retries and 2 retrospective rounds.
- `--free` — free-tier OpenRouter rotation (OpenCode only).
- `--headless` — fully flag- and env-var-driven, suitable for CI.
- `--only` and `--from` for targeted task execution.
- `--standalone MODEL` — bypass provider config entirely.
- `--provider {auto,opencode,claude-code}` — select the AI CLI provider
  from the command line; overrides `architect.toml` and
  `ARCHITECT_PROVIDER`.
- `--project DIR` — operate on a directory other than CWD.
- `--no-monitor` — disable the tmux dashboard.

### CLI Commands

- `architect` — start a run (plan, resume, or all-done guard automatic).
- `architect --plan` — force planning mode.
- `architect list` — show all tasks and their status.
- `architect status` — show current run state.
- `architect retry --task T03` — reset and re-run a specific task.
- `architect skip --task T03` — mark a task done without running it.
- `architect reset` — reset `PROGRESS.md`.
- `architect cancel` — remove a stale lock file.
- `architect init` — initialise a project directory.
- `architect config` / `--set key=value` — view or edit configuration.
- `architect circuit` / `--reset T04` — view or reset circuit-breaker state.
- `architect logs` / `--task T01` — view execution logs.
- `architect monitor` — attach to the live tmux dashboard.
- `architect version`, `architect --version`, `architect -V` — print the
  version string.

### tmux Dashboard

- Auto-launches a split-pane session when tmux is available.
- Live task progress, circuit-breaker state, token usage, and build
  number in a single view.
- Atomic state-file writes so external readers never see a partial update.
- Offers to install tmux interactively if it's missing.

### Token Budget

- Optional hourly token-spend cap per run.
- Rolling 1-hour window with automatic reset.

### Build Tracking

- Global build counter increments with every agent operation and never resets.
- Dual-track versioning — SemVer for releases, build counter for cumulative effort.
- Major-version build-floor alignment (`v1` ≥ 10000, `v2` ≥ 20000, `v3` ≥ 30000).
  Build numbers are always at least 5 digits.

### Technical Baseline

- Python 3.11+ required; uses the built-in `tomllib` (no `tomli` dependency).
- Zero AI SDK dependencies — every model call goes through the provider CLI.
- Strict static checks — `ruff check`, `ruff format --check`, and
  `mypy --strict` all clean.
- PEP 639 compliant packaging — `license = "Apache-2.0"` SPDX expression
  with `LICENSE` and `NOTICE` explicitly attached as `license-files`.
- Defensive `setup_logging(log_dir)` — rejects non-`Path`/`str` inputs
  with a clear `TypeError` instead of silently writing log files with
  mock-derived names.

---

*Build counter starts at 10000 for v1.0.0 and is always at least 5 digits. It never resets.*
*Each release records the exact build number at ship time.*
