# Changelog

All notable changes to The Architect are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/) with a global build counter.
See [README — Versioning](README.md#versioning) for the full scheme.
Full rules in [`documentation/PRACTICES.md`](documentation/PRACTICES.md).

---

## [Unreleased]

*(nothing yet)*

---

## [1.2.15] (build 10462) — 2026-05-16

### Fixed

- **Execution runs no longer lose the user's OpenCode config** — "model not found"
  regression introduced in build 10460 is resolved. The child process environment
  now correctly inherits `OPENCODE_CONFIG` and `OPENCODE_CONFIG_DIR` from the
  parent session instead of stripping them unconditionally. Planning runs continue
  to override `OPENCODE_CONFIG` via `get_env_overrides()` as intended; only the
  true OpenCode worker-session variables (`OPENCODE_PROCESS_ROLE`, `OPENCODE_RUN_ID`,
  `OPENCODE_PID`, `OPENCODE`) are stripped. (build 10462)

---

## [1.2.14] (build 10460) — 2026-05-16

### Fixed

- **OpenCode ≥ 1.15 compatibility — planning and execution no longer crash.**
  OpenCode 1.15.0 introduced two regressions that broke The Architect completely:

  1. Child `opencode run` processes inherited `OPENCODE_PROCESS_ROLE=worker` and
     `OPENCODE_RUN_ID` from the parent session, causing them to attempt to attach
     to a non-existent server and immediately exit with "InstanceRef not provided".
     Fixed by stripping all OpenCode session environment variables before spawning
     any child process.

  2. The `--agent` CLI flag raises "InstanceRef not provided" on startup in OpenCode
     ≥ 1.15 regardless of whether the named agent exists in config.  Fixed with a
     version-gated workaround: on ≥ 1.15, `--agent` is not passed and planning agent
     selection is handled via `default_agent` in the injected `architect.json`; on
     < 1.15 the flag is used as before so `execution_agent` remains fully honoured.

- **`COMPATIBILITY.md` added.** Tracks active provider workarounds with exact
  revert instructions and test-verification steps for when upstream fixes land.

---

## [1.2.13] (build 10451) — 2026-05-16

### Fixed

- **Execution agents no longer read every task file in `tasks/`.** Previously, the runner passed only the bare filename (`T04_foo.md`) to the agent rather than a full relative path (`tasks/T04_foo.md`). Without a directory prefix the agent couldn't locate its task file and would glob `tasks/T0*` to find it — accidentally reading all sibling task files and sometimes wandering into `tasks/archive/`. The instruction now supplies the exact path, so no discovery is needed.

- **Execution prompt no longer invites agents to explore the task list.** The step that described `tasks/INSTRUCTIONS.md` as containing the "full task list" has been reworded to "cross-task sequencing rules", and the step that said "read your task file in `tasks/`" now explicitly names the assigned file and prohibits globbing or listing the directory. Agents stay focused on their assigned task.

### Changed

- **Test coverage improved from 85% to 86%.** Added 135 new tests across five critical-path modules: `project_intelligence.py` (82% → 100%), `tui/runner.py` (72% → 95%), `provider_setup.py` (83% → 100%), `baseline.py` (88% → 100%), and `monitor_state.py` (91% → 100%). Full test suite: 3,151 passed, 1 skipped (builds 10443–10451).

---

## [1.2.12] (build 10441) — 2026-05-15

### Removed

- **tmux is gone.** The Architect no longer requires, installs, or launches tmux.
  No more split-pane dashboard, no more `--no-monitor` flag, no more auto-install
  prompts. Every feature tmux provided is now built in.

### Added

- **Session survival — no tmux needed.** Infinite Loop and `--persistent` runs
  survive terminal close and SSH drops natively. If the connection drops, the TUI
  exits cleanly and the worker keeps running headless, writing to
  `.architect/logs/`. Reconnect any time with `architect monitor`.

- **Detach from the pause menu (Esc → Detach).** Frees your terminal instantly
  while the run continues in the background. Works on every run — no flags needed.

- **`architect monitor` always opens the live TUI monitor.** No flags, no tmux
  session — just reads `.architect/monitor_state.json` directly from any terminal.

### Fixed

- **Esc and all key bindings now respond on the first keypress.** Tab-switch keys
  (`l`/`p`/`d`/`g`/`c`), Esc, and all execution screen bindings are now
  `priority=True` — they fire before any focused child widget (RichLog, tab bar)
  can swallow them. Clicking a tab header immediately refocuses the content area.
  ESC delay reduced from 100ms to 30ms so the pause menu feels instant.

- **Quitting mid-run (q / Ctrl+C / Esc → Exit) no longer crashes** with
  `ValueError: signal only works in main thread`. The worker is always non-daemon
  now; when you quit the TUI the active provider subprocess is killed immediately
  so the worker unblocks and finishes cleanly.

- **Quitting on the startup splash no longer crashes** — same root cause. Provider
  health-check and wait screens no longer try to boot a new Textual app from a
  background thread.

- **Scope/pre-run screen spinning on every Infinite Loop iteration** fixed.

- **Back on provider screen now exits cleanly** instead of silently picking the
  first provider.

- **Infinite Loop Detach activates SIGHUP survival** without needing `--persistent`
  on the CLI — selecting Infinite Loop in the pre-run screen is enough.

---

## [1.2.11] (build 10410) — 2026-05-14

### Added

- **Live cost display in the TUI — new "Costs" tab on the Execution screen.** A fifth tab (press `c`) shows the running session spend in real time: total tokens used, estimated USD cost, last-task cost, and a per-model breakdown. The tab updates automatically after every task completes — no need to wait until the run finishes or run a separate command.

- **Estimated spend on the Success screen.** After all tasks complete, the summary line now includes `~$X.XXXX est.` so you know what the run cost at a glance, right where you already look.

- **Cost data written to `monitor_state.json`.** The `.architect/monitor_state.json` file now includes `session_cost_usd`, `last_task_cost_usd`, and a `model_costs` map in the `tokens` section. External dashboards and scripts that read this file can surface live cost data without any additional API calls.

- **All four token types used for accurate cost estimation.** Input, output, cache-read, and cache-write tokens are each sent individually to the pricing engine. Previously only the total token count was forwarded, making cache discounts invisible. Claude models with prompt caching now show the correct (lower) cost rather than a worst-case estimate.

- **CI now runs the test suite on Ubuntu, Windows, and macOS** across Python 3.11, 3.12, and 3.13 (9 matrix combinations per push). Previously tests only ran on Ubuntu.

### Fixed

- **TUI tests no longer hang on Windows CI.** All `app.run()` call sites in `ArchitectAppRunner`, `run_single_screen`, and the standalone `tui_execution_session` / `tui_wait_session` fallback paths now pass `headless=True` when running under pytest (`PYTEST_CURRENT_TEST` is set). Textual's `HeadlessDriver` skips Windows console initialisation while keeping the full event loop, screen stack, and threading model intact — so the tests continue to exercise real TUI lifecycle behaviour.

### Added

- **Live cost display in the TUI — new "Costs" tab on the Execution screen.** A fifth tab (press `c`) shows the running session spend in real time: total tokens used, estimated USD cost, last-task cost, and a per-model breakdown. The tab updates automatically after every task completes — no need to wait until the run finishes or run a separate command.

- **Estimated spend on the Success screen.** After all tasks complete, the summary line now includes `~$X.XXXX est.` so you know what the run cost at a glance, right where you already look.

- **Cost data written to `monitor_state.json`.** The `.architect/monitor_state.json` file now includes `session_cost_usd`, `last_task_cost_usd`, and a `model_costs` map in the `tokens` section. External dashboards and scripts that read this file can surface live cost data without any additional API calls.

- **All four token types used for accurate cost estimation.** Input, output, cache-read, and cache-write tokens are each sent individually to the pricing engine. Previously only the total token count was forwarded, making cache discounts invisible. Claude models with prompt caching now show the correct (lower) cost rather than a worst-case estimate.

## [1.2.10] (build 10406) — 2026-05-14

### Fixed

- **Claude Code tasks no longer crash on Windows with "filename or extension is too long" (error 206).** Windows limits `CreateProcess` command lines to 32 767 characters. The Architect's planning prompts (`architect.md` ~23 KB + `execution.md` ~19 KB + `ARCHITECT.md` ~16 KB + task file) routinely exceed that limit, causing every task attempt to fail immediately. Claude Code now receives its instruction via **stdin** instead of a command-line argument, eliminating the limit entirely. The provider protocol gains a new `instruction_via_stdin` flag; all other providers (OpenCode, Codex, Gemini) are unaffected.

- **Windows PowerShell and Windows Terminal now show the modern interactive TUI** instead of the old legacy text fallback. PowerShell never sets the `TERM` environment variable, which the auto-detection logic previously treated as `TERM=dumb`, silently disabling the entire Textual UI. The check now only disables the TUI when `TERM` is *explicitly* set to `dumb`.

- **Rich colours and box characters render correctly in PowerShell.** The Rich console now passes `legacy_windows=False` on Windows so it emits VT escape sequences instead of the old Win32 console API path that produced plain, uncoloured output.

- **Alternate screen buffer (the full-screen view) works in PowerShell and Windows Terminal.** The `alternate_screen()` context manager now enables VT processing via `SetConsoleMode` before writing the ANSI escape, so the screen correctly switches instead of printing raw escape bytes.

- **Task files with uppercase extensions (`.MD`, `.Py`) are now discovered correctly.** All file-extension comparisons used bare `== ".md"` which is case-sensitive. On Windows, files can be stored with any extension case and would be found by directory listing but silently rejected. All comparisons now use `.suffix.lower()` and `re.IGNORECASE`.

- **Project type detection (game, mobile, IaC, CLI, etc.) is case-insensitive on all platforms.** Marker filenames like `main.tf`, `project.godot`, and `AndroidManifest.xml` are now matched regardless of how the OS stored the filename.

- **File paths inside planning prompts, task instructions, and baseline change reports always use forward slashes.** On Windows, `Path.relative_to()` produced backslash-separated strings, which appeared in prompts sent to providers and could confuse them, and caused baseline change detection to silently mark every file as created or deleted.

- **"Press any key to exit" works on Windows.** The POSIX-only `termios`/`tty` modules are replaced by a cross-platform `_wait_for_keypress()` helper that uses `msvcrt.getch()` on Windows.

- **Atomic file writes are safe when a reader has the file open.** On Windows, `os.replace(tmp, dst)` raises `PermissionError` when another process (e.g. the dashboard) has the destination file open — something POSIX allows. A shared `fileutil.py` helper retries the rename with brief exponential backoff. All persistence paths (`monitor_state.json`, `token_ledger.json`, `ARCHITECT.md`) use this helper.

- **Model status text in the pre-run configuration screen no longer logs a `StyleValueError`.** The loading indicator was setting `styles.color = "$text-muted"` at runtime, which is not a valid colour value outside of CSS context. The widget's existing CSS class already provides the correct muted colour.

- **`signal.SIGKILL` AttributeError on Windows is eliminated.** A direct `signal.SIGKILL` reference in exit-code comparison was replaced with the module-level constant that is already guarded with `getattr(signal, "SIGKILL", signal.SIGTERM)`.

- **`/dev/tty` access is explicitly skipped on Windows** (it doesn't exist there). The terminal-cleanup helper now checks `sys.platform != "win32"` before attempting to open it.

### Added

- **Cross-platform atomic file I/O helper (`the_architect.core.fileutil`).** `atomic_write_text` and `atomic_write_json` write to a temp file then rename, with a `PermissionError` retry loop so the pattern is safe on Windows where a reader holding a file open blocks the rename.

## [1.2.9] (build 10402) — 2026-05-14

### Fixed

- **Windows PowerShell and Windows Terminal now use the modern Textual TUI instead of the legacy prompt_toolkit fallback screens.** The TUI auto-detection previously treated an unset `TERM` environment variable as equivalent to `TERM=dumb`, silently disabling the TUI on every Windows terminal (PowerShell 5.1, PowerShell 7, Windows Terminal, cmd.exe). Windows never sets `TERM` but fully supports VT/ANSI output. The gate now only blocks the TUI when `TERM` is *explicitly* set to `dumb`. The new `_is_dumb_terminal()` helper enforces this across both `_resolve_tui_default` and `_ansi_supported` (build 10402).
- **Rich console output is no longer degraded in Windows PowerShell.** `PaddedConsole` (the global Rich Console used throughout the CLI) now passes `legacy_windows=False` on Windows, forcing Rich to emit VT escape sequences for colour and box characters instead of the old Win32 console API path that produced plain uncoloured output (build 10402).
- **Alternate screen buffer works correctly in PowerShell and Windows Terminal.** The `alternate_screen()` context manager now calls `SetConsoleMode` to activate VT processing on Windows before writing the `\033[?1049h` escape sequence. Without this, the escape bytes appeared as literal characters in the classic Windows console host rather than switching to the alternate screen (build 10402).

## [1.2.8] (build 10399) — 2026-05-14

### Added

- **Cross-run token and cost ledger.** The Architect now records each completed run to `.architect/token_ledger.json`, including total tokens, estimated USD cost, outcome, duration, task count, and per-model token/cost breakdowns. Ledger writes are atomic and best-effort, so reporting cannot crash an execution run (builds 10394-10395).
- **`architect token-report` command.** Users can now inspect historical token usage and estimated cost directly from the CLI, with a readable Rich table by default, `--json` for automation, `--since` for date filtering, and `--top-models` to focus on the most expensive models (build 10396).
- **Token ledger research record.** Added `cycle-research.md` to document why cross-run cost visibility matters across Codex CLI, Claude Code, OpenCode, and adjacent LLM cost-tracking tools (build 10393).

### Changed

- **Provider diagnostics are clearer.** `architect doctor` now reports every supported provider, while provider selection only shows installed and configured providers that can actually run. This makes setup issues easier to diagnose without offering unusable providers during execution (build 10392).
- **Token ledger recording is enabled by default.** Projects automatically collect run-level cost history unless `token_ledger = false` is set in `architect.toml` (build 10395).

### Fixed

- **More resilient execution after computer sleep or suspend.** The provider streaming loop now detects large wall-clock gaps in an OS- and terminal-agnostic way, terminates stale provider subprocesses after wake, retries the attempt, and avoids counting local sleep interruptions against circuit breaker no-progress/same-error thresholds (build 10398).
- **Fixed pre-task exits from packaged resource loading.** Task execution now prefers the project-local `.architect/prompts/execution.md` before reading packaged prompt resources, preventing `MultiplexedPath` resource-loader glitches from aborting tasks before OpenCode, Codex, Claude Code, or Gemini starts (build 10397).
- **More reliable local and CI test behavior.** OpenCode config discovery tests now isolate host-level config paths so a developer's real `~/.config/opencode` cannot create false failures during full-suite runs (build 10397).

## [1.2.7] (build 10390) — 2026-05-13

### Added

- **Smarter project understanding before planning.** The Architect now builds a structured `.architect/intelligence.json` cache from deterministic repository signals and injects it into planning alongside `ARCHITECT.md`, giving planners better context about project type, components, commands, relationships, and known gaps before task files are written (build 10388).
- **Workspace change evidence for every task.** The Architect can now capture a workspace baseline before execution and compare it afterward, giving each task concrete created/modified/deleted file evidence instead of relying only on provider summaries (builds 10378, 10385).
- **Evidence-backed retrospective review.** Retrospectives now include task baseline evidence so reviewers can see exactly what changed during execution and create more targeted fix-up tasks when work is incomplete, risky, or unexpectedly broad (build 10386).
- **Machine-readable status output.** `architect status --json` now returns a deterministic JSON snapshot of project state, including lock state, task statuses, summary counts, circuit breakers, token budget, and log files for dashboards, scripts, and automation (build 10387).

### Fixed

- **Cleaner long-term memory.** Planning, execution, learning, and review prompts now consistently keep `ARCHITECT.md` limited to durable project knowledge instead of goal-specific task notes, run history, or temporary implementation details (builds 10376-10382).
- **Safer retrospective task creation.** Retrospective reviewers are now constrained to R-prefixed fix-up task files, and The Architect refuses unsafe or malformed retrospective output before it can pollute the task queue (builds 10374-10375).
- **Duplicate task protection.** Plans and retrospectives with duplicate task prefixes are now rejected so the TUI, `PROGRESS.md`, and execution engine always agree on which task is running or complete (builds 10372-10373).
- **More reliable recovery prompts.** Executor guidance for R-prefixed recovery tasks now uses the correct task identity and keeps memory updates durable, reducing confusion during follow-up fix rounds (build 10377).

## [1.2.6] (build 10370) — 2026-05-13

### Added

- `architect doctor` command for static pre-flight diagnostics (build 10367)

### Fixed

- **Infinite Loop reliability.** Long-running Infinite Loop sessions now stay active through planning/execution handoffs instead of stopping after a later iteration.
- **TUI stability.** Wait overlays now close by revealing the existing execution screen instead of replacing already-mounted screens, preventing the app from disappearing between Infinite Loop iterations.
- **TUI recovery diagnostics.** The app now repairs an empty screen stack during active transitions and records structured lifecycle logs for screen changes, making future terminal/UI issues easier to diagnose.
- **Terminal cleanup.** Fixed leaked mouse-tracking mode after TUI exits, preventing raw `35;...M` mouse-event text from appearing at the shell prompt.
- **Release workflow.** Simplified releases so SemVer updates create the real GitHub release, while build-only pushes run CI without publishing noise.
- Hardened failed Infinite Loop recovery by reusing verified provider setup after `MultiplexedPath` resource glitches, reconciling missing R-task progress rows, classifying killed provider processes, and restoring terminal mouse modes on all CLI exit paths (build 10368).
- Added Infinite Loop safeguards for clean false-negative exits and stalled provider subprocesses so unattended runs recover missing summaries and fail/retry instead of hanging indefinitely (build 10369).
- Fixed the release workflow so an already-bumped SemVer that is still missing from PyPI can create the canonical release and publish instead of being treated as build-only (build 10370).

## [1.2.5] (build 10354) — 2026-05-12

### Added

- Added the project-local OpenCode `live-test-architect` skill, documented `demi_project/` smoke-test sandbox, and included a dependency-free terminal dashboard demo with unittest coverage for realistic headless Architect validation (builds 10333-10335).
- Expanded coverage for project intelligence, progress-state helpers, and self-update fallback version comparison paths, including unreadable `PROGRESS.md`, string-path handling, provider prompt routing, and missing-`packaging` behavior (builds 10339, 10350-10351).

### Changed

- CI now publishes to PyPI only from `v*` tag pushes; normal `main` pushes still lint, test, build, and create GitHub prereleases without waiting on PyPI environment approval (build 10331).
- Infinite Loop now archives completed package snapshots of `GOAL.md` and final `PROGRESS.md` with task files, instructions, and summaries while preserving live root files for the next planner cycle (build 10352).
- Refreshed the project SVG artwork with a denser Matrix-style Architect wordmark treatment.

### Fixed

- Restored the release workflow so a SemVer change on `main` creates the canonical GitHub release and triggers the PyPI publishing job behind the `pypi` approval environment; build-only pushes still create prereleases only (build 10354).
- Hardened Infinite Loop continuation and recovery so completed task state survives nested nonzero exits, post-task false negatives, transient resource-loader failures, unexpected Textual exits, and stale monitor finalization (builds 10342, 10348-10349).
- Persisted Infinite Loop goals in `tasks/GOAL.md`, reused them across planning iterations, and removed stale goals for non-loop planning so completed-loop context does not leak into unrelated runs (builds 10343-10344).
- Git-installed builds now ship and display the build counter in `architect --version`, `architect version`, and the TUI header (build 10345).
- Formatted the SuccessScreen test module so CI's `ruff format --check .` gate passes (build 10346).

## [1.2.4] (build 10327) — 2026-05-11

### Added

- **Infinite Loop mode.** Enable Infinite Loop in the TUI Options tab to keep rerunning the same goal with the same provider, model, scope, persistent/free flags, and integrity settings after each successful planning → execution → retrospective → validation cycle. The loop preserves the original goal across iterations, restarts task numbering each iteration, and shows the planning screen for every new iteration so it always feels like a fresh manual run. Stop it with Ctrl+C, the pause menu, or `architect cancel`.
- **Retrospective validation gate.** Each retrospective round now ends with a deterministic validation check. Validation results — passed/failed, reason, and unresolved tasks — are written to `tasks/PROGRESS.md` (`## Cycle Validation`) and `tasks/SUMMARY.md` (`### Validation Details`), giving every run a clear, auditable post-execution verdict.
- **Reviewer safety guardrails.** The retrospective reviewer is now explicitly forbidden from inspecting git history or producing destructive recovery (`git checkout`, `git reset`, `git restore`, `git clean`, `rm -rf`, etc.) unless the original task asked for it; any reviewer-created fix-up task containing such instructions is refused before execution.
- **Persistent runtime diagnostics.** New `.architect/logs/the_architect.log` and `.architect/logs/architect_runtime.log` capture loop driver and TUI runner lifecycle events (iteration entry, post-iteration pending check, planning-to-execution handoff, unexpected TUI exits) and survive per-iteration log archive cleanup.

### Changed

- **Persistent mode is deeper.** Persistent mode now uses 3 retrospective rounds (up from 2) so long-running unattended sessions get an extra review/fix/validate pass before completing.
- **Infinite Loop minimum review depth.** Without Persistent mode, Infinite Loop automatically raises retrospective depth to 2 rounds so a failed validation can trigger one recovery retrospective without silently turning into 30-retry persistent mode.
- **Pre-run selection visuals.** Pre-run pickers consistently render committed choices as `●` and unselected choices as `○`. Arrow keys only move focus/highlight; Space is the explicit commit key for model and option selection. Mouse click still works.
- **Planning lifecycle contract.** Planning prompts now explicitly forbid lifecycle exemptions and append an authoritative execution contract whenever a planner-written task or `INSTRUCTIONS.md` claims simple/content/no-op work can skip `PROGRESS.md` updates or the mandatory build bump.

### Fixed

- **Loop continuation reliability.** Infinite Loop now drives planning and execution as explicit separate phases, keeps a dedicated loop-chain flag across nested returns, supports `## Goal Summary` for goal recovery, forces execution if a replanned iteration leaves pending tasks, and switches the persistent TUI back to the execution screen between iterations instead of popping its final screen. Together these eliminate the "second iteration plans then exits" failure.
- **Persistent TUI runner survives unexpected exits.** If the Textual app exits while the worker flow is still active, the runner now waits for the flow to finish, treats `active_runner()` as unavailable so later phases stop reusing a dead UI, and does not kill provider subprocesses unless the user explicitly shuts down.
- **Pre-run keyboard navigation.** Up/down navigation in the pre-run Options tab now includes the Infinite Loop control and never silently changes scope, provider, action, model, or agent selections; commit requires Space or mouse click.
- **Infinite Loop confirmation flow.** Re-enabling Infinite Loop after canceling its warning now requires a fresh confirmation, and the loop suppresses the completion summary so it can keep rerunning when enabled from the pending-task resume screen.
- **Startup splash spacing.** The startup splash now keeps a one-row gap between the Matrix rain animation and the "Starting up…" subtitle and reserves enough body height for the subtitle to remain visible.

## [1.2.3] (build 10268) — 2026-05-09

### Added

- Added a stronger pre-planning learning stage that builds and repairs `ARCHITECT.md` before task planning, using fast repo detection plus an optional model-backed intelligence pass when project memory is still shallow (build 10259).

### Changed

- Provider setup now fails safer and earlier: update warnings appear before model-backed work, provider health issues are surfaced while the user is still present, and outdated providers can be updated directly with `U` (builds 10262-10266).
- Planning now has better first-run and large-repo awareness, including root package manifests, Python source packages, docs, CI workflows, provider rule files, prompt locations, and Architect runtime storage (build 10259).
- Current run state now lives in `tasks/PROGRESS.md`, keeping each goal's task state beside its instructions, task files, summary, and archive (build 10259).

### Fixed

- Provider and model selection stays in sync when switching providers, with cached model/agent lists, no wrong-provider model rows, and no blocking wait when provider defaults are sufficient (builds 10267-10268).
- Long execution TUI tabs are now scrollable, including Live output, Progress, Diagnostics, and Settings (build 10264).
- Planning, execution, retrospective, and reassessment now stop cleanly on provider quota, billing, budget, update, or configuration failures instead of retrying misleadingly or reporting false success (builds 10262-10265).
- Reassessment output is visible again during TUI execution runs, so downstream task checks no longer look like a blank wait screen (build 10259).
- Single-package projects are no longer misidentified as only secondary dev config directories when their main manifest lives at the repository root (build 10259).

## [1.2.2] (build 10236) — 2026-05-06

### Fixed

- Persistent mode now consistently applies its intended execution settings when
  enabled from config or the setup UI, including 30 task retries and 2
  retrospective rounds.

### Changed

- Execution agents now receive stronger verification requirements, including
  focused tests, broader validation for shared behavior, UI/TUI checks where
  practical, and local project setup when needed for existing test tooling.
- Project memory is clearer and more useful: `ARCHITECT.md` owns durable
  project-level knowledge such as tech stack, component ownership, code
  locations, commands, constraints, best practices, and durable lessons.
- Current-run handoffs are better separated: `tasks/INSTRUCTIONS.md` now focuses
  on the active goal's cross-task context, while `PROGRESS.md` captures real
  progress, missing work, verification results, lessons learned, and notes for
  the next task agent.

## [1.2.1] (build 10231) — 2026-05-06

### Added

- Added Force Reassessment, enabled by default, so pending tasks stay aligned
  after every completed or failed task.
- Added richer execution context in the TUI, including run settings visibility
  and the shared Matrix animation on the execution screen.

### Changed

- Reworked project memory so `ARCHITECT.md` now stores durable project
  intelligence, while run history is written to `tasks/SUMMARY.md` and archived
  with each task package.
- Improved planning and execution guidance so tasks are outcome-first, include
  bounded exploration plans, and avoid over-prescribing implementation details.
- Moved final run summaries from root `SUCCESS.md` to `tasks/SUMMARY.md`.

### Fixed

- Fixed pending-task replan flow so it hides stale pending details, focuses the
  goal input, skips duplicate prompts, and uses the settings already chosen.
- Fixed TUI provider-tab focus crashes and headless execution-screen timing.
- Fixed Claude Code tool-use visibility, stale integration fixtures, and async
  test mock warnings that were causing noisy CI runs.

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
