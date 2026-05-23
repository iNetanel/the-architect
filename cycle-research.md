# Cycle Research Log

Continuous improvement cycle research for The Architect. Each cycle records
ecosystem signals, internal evidence, chosen improvements, and implementation status.

---

## Cycle 4 — 2026-05-17 — **Implemented**

- Added `architect doctor --live` flag for provider health probing (6 tests, build 10481)

## Cycle 5 — 2026-05-17 — **Superseded**

- Planned circuit_screen.py coverage improvement; superseded by Cycle 6 (per-run token budget)

## Cycle 6 — 2026-05-17 — **Implemented**

- **Internal**: Per-run token budget (`token_budget_per_run`) — core config, runner integration, CLI, TUI surface across all 6 screens, 21 tests
- **Result**: Feature complete (build 10487). TUI budget display pattern: 6 screens updated consistently.

## Cycle 7 — 2026-05-17 — **Implemented**

- Added `architect circuit --json` flag for scriptable circuit breaker observability (build 10489)

## Cycle 8 — 2026-05-17 — **Implemented**

- Added `architect diff` command showing file changes during autonomous execution (22 tests, build 10491)

## Cycle 9 — 2026-05-17 — **Implemented**

- Token Budget Awareness: budget context injection in execution prompts, TUI Costs tab display, CLI budget command (build 10494)

## Cycle 10 — 2026-05-17 — **Implemented**

- Added `architect history` command for viewing past run history from token ledger (41 tests, build 10497)

## Cycle 11 — 2026-05-17 — **Implemented**

- **Ecosystem**: Claude Code users want persistent state, inter-session communication, tiered agent coordination (#56913, #24798). MCP silently hangs after SSE drops (#60061). Underlying pain: autonomous agents run without user visibility or intervention capability.
- **Internal**: All 3425 tests pass. Coverage 95% overall. No user feedback mechanism exists — users cannot steer agents between tasks.
- **Chosen**: Add `architect feedback` command — user feedback between tasks with runner injection
- **Result**: feedback.py with FeedbackState model, architect feedback --write/--view/--clear/--json, runner injection into build_instruction(), TUI display in execution screen footer, 247 tests

## Cycle 12 — 2026-05-17 — **Implemented**

- Added `architect preset` command for saving/recalling configuration presets (64 tests, build 10506)

## Cycle 13 — 2026-05-18 — **Implemented**

- Added `architect doctor --project` flag for project-level health diagnostics (42 tests, build 10509)

## Cycle 14 — 2026-05-18 — **Implemented**

- Added task-level cost tracking in token ledger (`LedgerTaskRecord`, `task_breakdown` field, `--tasks` flag on history/token-report, TUI task detail view, build 10512)

## Cycle 15 — 2026-05-18 — **Implemented**

- Added `architect estimate` command for pre-run cost estimation from historical ledger data (58 tests, build 10515)

## Cycle 16 — 2026-05-18 — **Implemented**

- Added `architect report` command for post-run summary from tasks/SUMMARY.md (40 tests, build 10518)

## Cycle 17 — 2026-05-18 — **Implemented**

- Added `architect monitor --json` and `--watch` flags for scriptable live run monitoring (18 tests, build 10520)

## Cycle 18 — 2026-05-18 — **Implemented**

### Ecosystem Signals

- **Claude Code v2.1.139–v2.1.143** (May 2026): Added agent view (`claude agents`), `/goal` command (set completion condition, keep working until met), plugin dependency enforcement, projected context cost, background session improvements. Underlying pain: users managing multiple autonomous agents need visibility and control.
- **Common pain across ALL tools**: Users cannot audit what autonomous agents changed; cost anxiety remains dominant (#16157, 5k+ comments); multi-agent coordination fails because agents can't see their own operating conditions. Interactive tools are building session management and multi-agent orchestration. The Architect already operates at a higher level (task-based planning, execution, circuit breaker, retrospective) but the planner decomposes goals without awareness of current workspace state.

### Internal Evidence

- **All 3757 tests pass**. Coverage 95% overall. Core modules at 99-100%. cli.py at 67% (interactive prompts), app.py at 76%, pre_run_tabbed.py at 83%.
- **Planner context is comprehensive but incomplete**: `gather_project_context()` collects file tree, AGENTS.md/CLAUDE.md, PROGRESS.md history, docs/, and task names. `build_planning_instruction()` injects ARCHITECT.md, structured intelligence, user context files, and project context. But the planner has no visibility into the current workspace state — git branch, uncommitted changes, recent commits, or dirty working directory.
- **Gap identified**: When a user runs The Architect on a project with uncommitted changes, on a feature branch, or after a partial previous run, the planner decomposes the goal blind to these conditions. This leads to plans that don't account for existing work-in-progress, wrong branch assumptions, or redundant work. A human developer planning the same goal would naturally check `git status` and `git log` first. The planner should do the same.

### Chosen Improvement

**Add Context-Aware Planning — inject workspace state (git status, branch, recent commits) into the planner's context.**

**Why:** The planner currently decomposes goals without knowing the current state of the workspace. If there are uncommitted changes, a feature branch with recent commits, or a dirty working directory from a previous interrupted run, the planner doesn't know. This means it may plan redundant work, ignore existing WIP, or make wrong assumptions about the project state.

This is something only an autonomous orchestrator can provide — interactive tools like Claude Code don't have a discrete planning phase where workspace state matters. The Architect's fire-and-forget model means the planner runs once and the agent executes autonomously — the planner needs the best possible context to make good decisions.

**Value:** Better planning quality. The architect agent can account for existing uncommitted changes, understand the current branch context, and avoid planning work that was already done in recent commits. This directly improves the quality of task decomposition, which is The Architect's core value.

**Scope:** Standard — one feature area (workspace context detection and planner injection) with tests. No TUI screen needed — this is a behind-the-scenes planning improvement.

## Cycle 19 — 2026-05-18 — **Implemented**

- **Ecosystem**: Fire-and-forget gap — users have no alert when autonomous runs complete. Interactive tools don't need notifications (user IS watching), but autonomous orchestrators do.
- **Chosen**: Run Completion Notifications — desktop notifications and terminal bell when autonomous runs complete or fail
- **Result**: Feature complete (build 10527). All 3 tasks done (T01: core notification module with 17 tests, T02: config+runner integration with 14 tests, T03: TUI surface across 4 screens with 8 tests). 39 new tests total.

## Cycle 20 — 2026-05-18 — **Implemented**

- **Ecosystem**: Multi-agent orchestration tools show users need coordination and visibility across autonomous sessions.
- **Chosen**: Task Dependencies — express and enforce execution-order constraints between tasks
- **Result**: Feature complete (build 10532). All tasks done (T01: model+parser, T02: runner awareness, T03: 35 tests, T04: CLI display, T04R1: fix). `depends_on` field on Task model, cycle detection, runner skip behavior, `architect deps` command.

## Cycle 21 — 2026-05-18 — **Implemented**

- **Ecosystem**: Cost anxiety dominant — users want pre-run visibility, not just post-run reporting.
- **Chosen**: Dry-Run Mode — `--dry-run` flag on `architect` command; planner runs, runner displays plan summary, exits without executing
- **Result**: Feature complete (build 10535). All 3 tasks done (T01: core CLI+runner, T02: JSON output, T03: 41 tests).

## Cycle 22 — 2026-05-18 — **Implemented**

- **Ecosystem**: Users repeatedly formulate same goals with inconsistent phrasing, producing variable plan quality.
- **Chosen**: Goal Templates — save and reuse goal patterns with `{variable}` placeholder substitution
- **Result**: Feature complete (build 10539). All 4 tasks done (T01: core module, T02: CLI command, T03: 86 tests, T04: TUI template display).

## Cycle 23 — 2026-05-18 — **Implemented**

- **Ecosystem**: Users fear autonomous agents breaking things — no first-class undo mechanism exists. Guardrails tools enforce rules but don't provide rollback. Interactive tools rely on manual git for undo.
- **Chosen**: Run Rollback — restore files to pre-run state using captured baselines
- **Result**: Feature complete (build 10543). All 4 tasks done (T01: core module with git-based restoration, T02: CLI command with --task/--all/--dry-run/--json/--yes, T03: 71 tests, T04: TUI confirmation screen with task selection, plan review, approve/cancel/dry-run). Content restoration uses `git show <commit>:<path>` with graceful fallback for non-git repos.

## Cycle 24 — 2026-05-18 — **Implemented**

- **Internal**: The dependency system (Cycle 20) was explicitly designed with "Foundation for future parallel execution of independent tasks" — this cycle delivers on that foundation.
- **Ecosystem**: Multi-agent orchestration is a trend in coding tools (Claude Code agents, etc.). The Architect differentiates by running independent tasks in parallel within a single autonomous session, reducing wall-clock time without requiring multiple provider instances or complex coordination.
- **Chosen**: Parallel Task Execution — run independent tasks concurrently based on dependency graph
- **Result**: Feature complete (build 10557). All 4 tasks done (T01: scheduler module, T02: runner integration, T03: TUI display, T04: tests). `max_parallel_tasks: int = 1` in `ArchitectConfig` (default 1 for backward compatibility).

## Cycle 25 — 2026-05-19 — **Implemented**

- **Result**: Feature complete (build 10563). All 4 tasks done (T01: core module, T02: runner integration, T03: 41 tests, T04: TUI surface).

### Ecosystem Signals

- **Claude Code #60506 (May 19, 2026)** — Model self-reports 6 days of architectural drift on a customer project despite hooks, memory, skill enforcement, and CLAUDE.md. Critical findings: "Hook-encoded rules are 100% effective; prose-encoded rules degrade over multi-session work." Model has no drift detector. Model says "done" without verification. Customer lost 6 days + holiday to migraine. Underlying pain: autonomous agents claim completion without anyone verifying the work is actually correct.
- **Claude Code #16236 (Jan 2026)** — Conversation branching for context-preserving exploration. The Architect's task-based model already provides this naturally (each task is a "branch" of work).

### Internal Evidence

- **All 4188 tests pass**. Coverage 95% overall. Core modules at 99-100%.
- **Runner has no post-task verification** — the runner detects task completion via multi-signal (promise tag, PROGRESS.md, clean exit) but never actually runs the project's CI commands (lint, test, typecheck) to verify the agent's work. The agent claims "done" and the runner trusts it.
- **Retrospective review is AI-based** — it runs after ALL tasks complete. By then, downstream tasks may have already built on broken upstream work.
- **Circuit breaker detects no-progress patterns** but doesn't detect "task says done but CI fails" patterns.

### Chosen Improvement

**Post-Task Validation Gate — run configurable CI checks after each task to verify work before proceeding.**

**Why:** The runner currently trusts the agent's completion signal without independent verification. An agent can claim "done" while lint fails, tests break, or typecheck errors exist. The retrospective reviewer catches this eventually, but by then downstream tasks may have built on broken work. The architectural drift issue (#60506) shows this is a real, painful problem — a model drifted for 6 days because nothing verified its work between sessions.

Only an autonomous orchestrator with discrete task boundaries can provide this. Interactive tools like Claude Code don't have task completion points where automated validation naturally fits. The Architect's fire-and-forget model means the user isn't watching — automated verification is essential.

**Value:** Catches broken tasks immediately (not after all tasks complete). Prevents downstream tasks from building on broken upstream work. Gives users confidence that "Done" actually means the code works. Reduces retrospective review burden.

**Scope:** Standard — 4 tasks (T01: core module, T02: runner integration, T03: tests, T04: TUI surface)

## Cycle 26 — 2026-05-19 — **In Progress**

### Ecosystem Signals

- **Validation gate limited to Python projects** — The Cycle 25 validation gate uses hardcoded default commands (`ruff check .`, `pytest tests/ -q`, `mypy the_architect/`). Users running The Architect on JavaScript, Go, Rust, or any non-Python project get zero validation benefit. The TODO in `validation_gate.py` line 79 explicitly calls this out: "In future cycles, parse custom scripts from pyproject.toml".
- **Underlying pain**: The Architect positions itself as a project-agnostic autonomous orchestrator, but the validation gate (its answer to architectural drift) only works for one language ecosystem. This is a credibility gap — users expect "validation after each task" to work regardless of their stack.

### Internal Evidence

- **All 4229 tests pass**. Coverage 95% overall. Core modules at 99-100%.
- **Validation gate config** (`ValidationGateConfig`) has `checks: list[Literal["lint", "test", "typecheck"]]` — the Literal type restricts check names to the three built-in options.
- **`_discover_commands()`** returns hardcoded defaults with no mechanism for user override.
- **No `custom_commands` field** exists in `ValidationGateConfig` or `ArchitectConfig`.

### Chosen Improvement

**Custom Validation Gate Commands — allow users to specify arbitrary CI commands for the validation gate.**

**Why:** The validation gate is The Architect's answer to the architectural drift problem (#60506). It works brilliantly for Python projects but is useless for every other language. Making it project-agnostic is a fundamental improvement that strengthens The Architect's core value proposition: autonomous, fire-and-forget orchestration that actually works on any project.

**Value:** Every user benefits, not just Python developers. The validation gate becomes a true universal quality gate. Users can specify `npm test`, `cargo test`, `go vet`, `eslint .`, or any custom CI command.

**Scope:** Standard — 3 tasks (T01: config model + command discovery, T02: tests, T03: TUI surface)

## Cycle 27 — 2026-05-19 — **Implemented**

- **Result**: Feature complete (build 10571). All 4 tasks done (T01: core verification module, T02: runner integration, T03: 48 tests, T04: TUI surface). `resume_verification.py` with `ResumeVerificationResult` model, `verify_completed_task()`, `verify_all_completed_tasks()`. Runner integration with verification-aware scheduler pre-population. TUI shows color-coded indicators in resume screen, pre-run screen, and execution diagnostics.

## Cycle 28 — 2026-05-19 — **Implemented**

- **Result**: Feature complete (build 10574). All 3 tasks done (T01: core aggregation module with 4 models + aggregate_costs(), T02: CLI command with Rich table + JSON + filters, T03: 37 tests + TUI spending summary). `cost_analytics.py` with `CostAnalytics`, `ModelCostSummary`, `TaskCostEntry`, `DailySpendingEntry` models and `aggregate_costs()` function. CLI with `--since`, `--until`, `--model` filters. TUI shows recent spending summary in mode selection screen.

## Cycle 29 — 2026-05-19 — **Implemented**

- **Result**: Feature complete (build 10577). All 3 tasks done (T01: core timeout feature with config+runner+bonus retries, T02: CLI+TUI surface across 7 source files, T03: 26 tests). `task_timeout: int = 0` in `ArchitectConfig` (0 = disabled). Runner tracks wall-clock elapsed time per task using `time.monotonic()`, kills subprocess on timeout, grants up to 5 bonus retries (not consuming normal retry slots). Follows the proven idle-timeout pattern with `_mark_task_timeout()`, `_clear_task_timeout()`, circuit events `"task_timeout_detected"` and `"task_timeout_resumed"`. TUI surfaces: Options tab config, mode selection, resume screen, status screen, config list, and 6 display screens updated consistently.

### Ecosystem Signals

- **Cost anxiety remains the #1 pain point** — users need per-task guardrails to prevent runaway costs when a single task gets stuck.
- **Underlying pain**: Users walk away from autonomous runs and come back to find one task consumed the entire budget.

### Chosen Improvement

**Task-Level Wall-Clock Timeout — cap how long any single task can run.** Natural extension of idle-timeout and per-run budget systems. Only an autonomous orchestrator with discrete task boundaries can enforce per-task time budgets.

## Cycle 30 — 2026-05-20 — **Implemented**

- **Result**: Feature complete (build 10580). All 3 tasks done (T01: 10 tests 65%→68%, T02: 15 tests 68%→82%, T03: 21 tests 82%→100%). mode_selection.py at 100% coverage (310 statements, 0 missing).

### Internal Evidence

- **All 4377 tests pass** (5 skipped, 1 warning). Coverage 95% overall. Core modules at 99-100%.
- **TUI coverage gaps**: mode_selection.py at 65% (107 missing), pre_run_tabbed.py at 78% (201 missing), resume.py at 69% (46 missing), status_screen.py at 71% (30 missing), circuit_screen.py at 67% (24 missing), wait.py at 77% (29 missing).
- **mode_selection.py is the primary TUI entry point** — users configure run settings here (free tier, persistent mode, integrity defense, token budgets, task timeout, notifications, validation gate). Low coverage means untested exception handlers, spending summary display, preset application, and run_mode_selection function.
- **No TODOs or FIXMEs in codebase** — the code is clean, but test coverage gaps remain in TUI screens.

### Chosen Improvement

**Mode Selection TUI Coverage Improvement — improve test coverage from 65% to 85%+.**

**Why:** The mode_selection screen is where users configure every run. It has complex logic for spending summary display, preset application, validation gate configuration, and custom commands. The current 65% coverage leaves exception handlers, edge cases, and the public run_mode_selection function untested. Improving coverage makes the primary user-facing configuration experience more reliable and trustworthy.

**Value:** Fewer bugs in the configuration flow. Better confidence when modifying mode_selection.py. More reliable preset application and spending summary display.

**Scope:** Standard — 3 tasks (T01: spending/custom commands/on_mount, T02: preset application/selection, T03: run_mode_selection/submit edge cases). Test-only work, no source code changes.

## Cycle 31 — 2026-05-20 — **Implemented**

- **Result**: Feature complete (build 10584). All 4 tasks done (T01: core module, T02: runner integration, T03: 77 tests, T04: TUI surface). `artifacts.py` with `Artifact` and `ArtifactStore` models, storage in `.architect/artifacts/artifacts.json`, runner injects `=== UPSTREAM ARTIFACTS ===` into `build_instruction()` for dependency tasks, bounded to 10,000 chars. TUI shows count in execution footer and mode selection screen.

## Cycle 32 — 2026-05-20 — **In Progress**

### Ecosystem Signals

- **Cost anxiety remains the #1 pain point** — Claude Code #16157 (1470 comments, 717 reactions): users hitting usage limits instantly with Max subscription. #38335 (723 comments, 525 reactions): session limits exhausted in 1-2 hours instead of 5. The underlying pain: users have no control over which model does which work — every task burns the same expensive model tokens. Interactive tools use one model per session; there is no per-task model control in any existing tool.
- **Underlying pain for autonomous orchestrators specifically**: When The Architect runs 8 tasks autonomously, every task uses the same execution model. A simple "update README" task burns the same Opus tokens as a complex "refactor authentication system" task. Only an autonomous orchestrator with discrete task boundaries can assign different models to different tasks — interactive tools don't have task decomposition, so they can't do per-task model routing. This is The Architect's unique advantage.

### Internal Evidence

- **All 4500 tests pass** (5 skipped, 1 warning). Coverage 95% overall. Core modules at 99-100%.
- **31 cycles of continuous improvement completed** — the codebase is in excellent health.
- **No TODOs or FIXMEs** — code is clean.
- **Runner already supports model overrides** — `select_model()` in runner.py handles `model_override`, `config.standalone_mode`, and free mode rotation. The retry system (`retry_model_2/3`) already changes models mid-run. The infrastructure for per-task model routing exists — it just needs to be exposed at the task level.
- **Task model has `depends_on` field** — the planner already writes structured metadata in task files that the runner parses. Adding a `model` field follows the same pattern.
- **Token ledger already tracks per-task model** — `LedgerTaskRecord.model` captures which model ran each task. The cost analytics pipeline already aggregates by model. Per-task model assignment would feed directly into existing cost reporting.

### Chosen Improvement

**Per-Task Model Assignment — allow the planner to assign different models to different tasks based on complexity, cost, or capability needs.**

**Why:** Every task in a run currently uses the same execution model. A simple documentation update burns the same expensive model tokens as a complex system refactor. The planner sees all tasks and understands their relative complexity — it should be able to route simple tasks to cheaper models and reserve expensive models for complex work. Interactive tools like Claude Code cannot do this because they use one model per session. Only an autonomous orchestrator with discrete task boundaries can provide per-task model routing.

**Value:** Direct cost savings — simple tasks on cheaper models can save 50-80% per task. Better model utilization — complex tasks get the models they need, simple tasks don't waste frontier model capacity. The planner becomes a cost optimizer, not just a task decomposer.

**Scope:** Standard — 3 tasks (T01: task model + parser, T02: runner integration, T03: tests + TUI surface)

### Internal Evidence

- **All 4423 tests pass** (5 skipped, 1 warning). Coverage 95% overall. Core modules at 99-100%.
- **No TODOs or FIXMEs in codebase** — the code is clean.
- **30 cycles of continuous improvement completed** — the codebase is in excellent health.
- **Gap identified**: Tasks run in isolation — each task has no visibility into what upstream tasks produced. When T03 depends on T01 and T02, T03 starts blind to any artifacts (generated code, schemas, test results, configuration) that T01/T02 created. A human developer planning the same goal would naturally review upstream outputs first. The executor should do the same.
- **Existing infrastructure supports this**: `_run_all_inner` tracks `_task_result_statuses` and the full dependency graph; `build_instruction()` already injects context sections (budget, feedback, workspace state, ARCHITECT.md); `Task.depends_on` defines which tasks are upstream.

### Ecosystem Signals

- **Underlying pain**: Autonomous agents working on multi-task goals waste tokens re-doing work upstream tasks already completed, or produce inconsistent outputs because they can't see upstream results. Interactive tools don't have this problem because the user bridges the gap between sessions. Fire-and-forget orchestrators need automated inter-task communication.

### Chosen Improvement

**Inter-Task Artifacts / Workspace Sharing — enable upstream tasks to produce structured outputs that downstream tasks can consume.**

**Why:** The Architect runs tasks in isolation — each task has no visibility into what upstream tasks produced. This means downstream tasks may re-do work, produce inconsistent outputs, or miss important context. Only an autonomous orchestrator with discrete task boundaries can provide structured inter-task communication. Interactive tools like Claude Code don't have task completion points where artifacts can be captured and shared.

**Value:** Better multi-task run quality. Downstream tasks get context from upstream tasks. Enables complex multi-stage workflows (code generation → testing → documentation). Reduces redundant work across tasks.

**Scope:** Standard — 4 tasks (T01: core module, T02: runner integration, T03: tests, T04: TUI surface)

## Cycle 33 — 2026-05-20 — **In Progress**

### Internal Evidence

- **All 4530 tests pass** (5 skipped, 1 warning). Coverage 95% overall (2686 missing out of 53120 statements). Core modules at 99-100%.
- **TUI coverage gaps**: resume.py 69% (46 missing), status_screen.py 71% (30 missing), circuit_screen.py 67% (24 missing), wait.py 76% (31 missing), app.py 77% (120 missing), pre_run_tabbed.py 78% (201 missing).
- **No TODOs or FIXMEs** — the code is clean.
- **32 cycles of continuous improvement completed** — the codebase is in excellent health.
- **TUI is the primary user-facing interface** — improving its reliability directly impacts user trust and confidence in The Architect's autonomous capabilities. The resume screen is the critical flow for users returning to interrupted runs. The status and circuit screens are important observability surfaces. App.py is the core application lifecycle.

### Chosen Improvement

**TUI Coverage Improvement — improve test coverage for the lowest-coverage critical TUI screens.**

**Why:** The TUI is the primary interface where users experience The Architect. The lowest-coverage screens (resume at 69%, status at 71%, circuit at 67%) are critical user flows — resuming interrupted runs, viewing config and project state, monitoring circuit breaker health. Improving coverage from 67-71% to 90%+ makes these screens more reliable and trustworthy. The app.py (77%) and wait.py (76%) screens are also important for core lifecycle and long-run overlay behavior.

**Value:** Fewer bugs in critical user-facing screens. Better confidence when modifying TUI code. More reliable resume flow (users returning to interrupted runs). More trustworthy observability surfaces (circuit breaker state, config display).

**Scope:** Standard — 3 tasks (T01: resume.py 69%→90%+, T02: status_screen.py + circuit_screen.py 71%/67%→90%+, T03: wait.py + app.py 76%/77%→85%/82%+). Test-only work, no source code changes.

## Cycle 34 — 2026-05-20 — **In Progress**

### Ecosystem Signals

- **Codex "Hook would be a great feature"** — 207 upvotes, 122 comments. Users want to automate actions around coding sessions. Underlying pain: users want to integrate autonomous coding into their workflows — pre-run setup, post-run CI/CD triggers, custom notifications, cleanup scripts. Interactive tools don't have discrete run boundaries where hooks naturally fit.
- **Codex "Orchestration for parallel project-level coordination"** — users want multi-project/task coordination across autonomous sessions.
- **Codex "automatic /handoff skill when limit windows near 0%"** — users want graceful session transitions when hitting limits.
- **Claude Code 5k+ open issues** — active community with pain points around control, reliability, cost, and session management.

### Internal Evidence

- **All 4668 tests pass** (5 skipped, 1 warning). Coverage 95% overall. Core modules at 99-100%.
- **TUI coverage**: app.py 79% (108 missing), wait.py 95% (6 missing), resume.py 99% (1 missing).
- **No TODOs or FIXMEs** — the code is clean.
- **33 cycles of continuous improvement completed** — the codebase is in excellent health.
- **Notifications exist but are limited** — `notifications.py` sends desktop notifications and terminal bells. Users want more powerful, user-configurable automation at lifecycle points.

### Chosen Improvement

**Lifecycle Hooks — user-configurable shell commands that execute at run lifecycle points.**

**Why:** The strongest ecosystem signal (207 upvotes, 122 comments on Codex) is for hooks. Users want to automate actions around coding sessions — run setup scripts before a run, trigger CI/CD after success, run cleanup on failure. Interactive tools don't have discrete run boundaries where hooks naturally fit. Only an autonomous orchestrator with clear lifecycle points (run start, task complete, run success, run failure) can provide this.

**Value:** Users can integrate The Architect into their CI/CD pipelines, automate environment setup, trigger downstream workflows, and customize post-run behavior. This makes The Architect a first-class automation citizen in professional developer workflows.

**Scope:** Standard — 4 tasks (T01: core module, T02: CLI command, T03: runner integration, T04: TUI surface)

## Cycle 35 — 2026-05-20 — **Planned**

### Ecosystem Signals

- **Cost anxiety remains the #1 pain point** — Claude Code #16157 (1470 comments, 717 reactions): users hitting usage limits instantly with Max subscription. #38335 (723 comments, 525 reactions): session limits exhausted in 1-2 hours. The underlying pain: when resources run out, there's no way to control what gets done first.
- **Agent-kanban** (278 stars) — "agent-first task board, Mission control for your AI workforce." Users want visual task management with priority control for autonomous agents.
- **Ralph Loop pattern trending** — ralph-desktop, smart-ralph, elves all build around iterative autonomous refinement. The Architect already has Infinite Loop mode, but lacks structured priority control within a run.
- **Underlying pain for autonomous orchestrators specifically**: When The Architect runs 8 tasks and budget runs out after task 5, there's no guarantee the 5 completed tasks were the most important ones. The scheduler treats all tasks equally. Only an autonomous orchestrator with discrete task boundaries can provide priority-based scheduling — interactive tools don't have task decomposition, so they can't prioritize tasks.

### Internal Evidence

- **All 4801 tests pass** (5 skipped, 1 warning). Coverage 95% overall. Core modules at 99-100%.
- **34 cycles of continuous improvement completed** — the codebase is in excellent health.
- **No TODOs or FIXMEs** — code is clean.
- **Parallel scheduler already exists** — `ParallelScheduler.get_next_batch(n)` returns ready tasks but doesn't consider priority. All tasks are treated equally.
- **Task model has `depends_on` field** — the planner already writes structured metadata in task files that the runner parses. Adding a `priority` field follows the same pattern.
- **CLI list command already shows task metadata** — `architect list` displays task_id, title, status, deps. Adding a priority column follows the established pattern.
- **TUI execution screen already displays task information** — adding priority indicators follows the existing pattern.

### Chosen Improvement

**Task Priority System — allow the planner to assign priority levels to tasks so the scheduler can focus on critical work first.**

**Why:** When budget or timeout constraints hit during a run, there's no way to ensure critical tasks complete before nice-to-have tasks. The scheduler treats all tasks equally. A user running 8 tasks with a tight budget wants the 3 most important tasks to complete first, even if the remaining 5 are skipped. Interactive tools like Claude Code cannot do this because they don't decompose work into discrete tasks. Only an autonomous orchestrator with discrete task boundaries can provide priority-based scheduling.

**Value:** Direct cost savings — critical tasks complete first when resources are constrained. Better run outcomes — the most important work gets done even when the run doesn't finish all tasks. The planner becomes a cost optimizer that understands task importance, not just task decomposition.

**Scope:** Standard — 3 tasks (T01: task model + parser, T02: scheduler + CLI, T03: tests + TUI surface)
