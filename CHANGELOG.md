# Changelog

All notable changes to The Architect are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/) with a global build counter.
See [README — Versioning](README.md#versioning) for the full scheme.
Full rules in [`documentation/PRACTICES.md`](documentation/PRACTICES.md).

---

## [Unreleased]

<!--
Every completed task appends a bullet here and bumps __build__ in /version.py.
When cutting a release, rename [Unreleased] to the version and add a fresh
empty [Unreleased] above it. Use Keep a Changelog section headings:
Added / Changed / Deprecated / Removed / Fixed / Security.
-->

### Fixed

- Added Force Reassessment to TUI Options, enabled by default, so executions can
  reassess after every task while still allowing conditional reassessment (build 10227).
- Removed the goal line from the execution Progress tab to keep run status compact
  while leaving goal context in planning/settings surfaces (build 10226).
- Fixed a provider-tab focus crash in the pre-run screen when navigation focus
  temporarily lands on Textual's tab strip instead of a form control (build 10225).
- Fixed pending-task replan flow so choosing planning hides stale pending-task
  details, focuses the goal input, and skips the duplicate pending guard (build 10224).
- Resized the Matrix rain animation to a centered 20-column by 6-row grid across
  TUI screens (build 10223).
- Centralized Matrix rain layout sizing so splash, wait, success, and execution
  screens all derive rain dimensions from `MatrixRain` constants (build 10222).
- Improved execution TUI context by truncating long goals in planning/progress,
  adding a settings tab, and matching the planning matrix rain animation (build 10221).
- Cleaned up async test mocks that produced unawaited coroutine warnings during CI
  runs (build 10220).
- Fixed CI regressions around stale integration task fixtures, Claude Code tool-use
  visibility expectations, and headless TUI `push_and_wait` timing (build 10219).

## [1.2.0] (build 10218) — 2026-05-05

### Added

- Gemini CLI provider support, including provider detection, configuration value support,
  non-interactive execution, JSONL stream parsing, model resolution, and documentation.
- New Textual TUI that replaces the old prompt-by-prompt flow with a persistent app, tabbed
  setup screen, execution screen, pause menu, success screen, wait screens, and clean keyboard
  navigation.
- Integrity defense mode, which snapshots files before execution and detects truncated or
  corrupted writes before a run is reported successful.
- Inter-task reassessment, where The Architect can review task outcomes between tasks and adjust
  the remaining plan when a completed task reports possible downstream impact.
- New branded animations for startup, planning, waiting, execution, and shutdown states.
- tmux control improvements for dashboard/session handling, detach behavior, monitor state, and
  long-running unattended runs.

### Changed

- Provider and model discovery is more provider-aware across OpenCode, Codex CLI, Claude Code,
  and Gemini CLI, with fewer hardcoded model assumptions and better local CLI detection.
- Execution visibility is now centered around `Live`, `Progress`, and `Diagnostics` views so users
  can see raw provider output, overall task progress, and retries/circuit/model-switch events
  separately.
- Existing-task runs now use the same main TUI setup flow as new goals, allowing provider, model,
  mode, integrity, and token-budget changes before executing or replanning.
- Reassessment and progress tracking now preserve richer task-outcome information in `PROGRESS.md`
  so later tasks and retrospective review have better context.

### Fixed

- Fixed multiple issues from the 1.1.x feature set, including provider update checks using the
  wrong selected provider, incorrect Codex fallback models, stale output placeholders, and
  dropped provider output from Codex CLI, Claude Code, and Gemini CLI stream parsers.
- Fixed TUI reliability issues including tab navigation, setup-screen footer noise, execution
  output routing, shutdown display, and hidden/incorrect setup controls.
- Fixed reassessment edge cases around missing task outcome summaries, missing project memory,
  inconsistent downstream-impact gating, and unsafe nested event-loop usage.
- Fixed progress/task ordering issues around legacy task prefixes, R-task ordering, and preserving
  task-outcome tables during executor rewrites.

## [1.1.0] (build 10033) — 2026-04-29

### Added

- Codex CLI provider support, including provider detection, configuration support, JSONL output
  parsing, model resolution, non-interactive execution, and provider-specific tests.
- Codex can be selected with `--provider codex`, `provider = "codex"`, or auto-detection.

### Changed

- Provider selection and configuration wording is now provider-agnostic instead of assuming only
  OpenCode and Claude Code.
- Interactive prompt screens received better spacing and input handling so long goals, models, and
  menus are easier to read inside the tmux dashboard layout.
- tmux dashboard panes now use cleaner, less intrusive borders.

### Fixed

- Fixed several dashboard and prompt-layout issues where streamed output or typed input could run
  into pane edges.
- Fixed provider-related test and documentation drift introduced while generalising the provider
  system.

## [1.0.1] (build 10011) — 2026-04-28

### Fixed

- Provider update-required and misconfiguration errors are now surfaced
  immediately with actionable messages instead of silently retrying 3 times
  with a generic "no tasks created" message. OpenCode and Claude Code can
  now be proactively checked for updates before planning starts (build 10010).

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

- `more_things.md` — post-style writeup covering design philosophy and
  non-obvious insights not found in the technical documentation (build 10006).

- CI auto-creates a GitHub release for every green build on `main`.
  Same-SemVer builds are marked as pre-release; new-SemVer builds are
  marked as latest and also published to PyPI after reviewer approval
  (build 10005).

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

### Added

- Provider-backed autonomous planning and execution for OpenCode and Claude Code.
- Task planning into `tasks/TNN...` files with shared instructions, `PROGRESS.md` state,
  `ARCHITECT.md` project memory, and timestamped run archives.
- Multi-attempt execution with completion detection, retry fallbacks, circuit-breaker protection,
  cooldown handling, and optional free-tier model rotation.
- Retrospective review that can create `R` fix-up tasks after an execution run.
- tmux dashboard, logs, status, config, retry, skip, reset, monitor, and circuit CLI commands.

### Changed

- Established the project baseline: Python 3.11+, no AI SDK dependencies, provider CLI execution,
  strict static checks, PEP 639 packaging, and dual SemVer/build-counter versioning.

---

*Build counter starts at 10000 for v1.0.0 and is always at least 5 digits. It never resets.*
*Each release records the exact build number at ship time.*
