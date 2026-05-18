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

## Cycle 24 — 2026-05-18 — **Planned**

- **Internal**: The dependency system (Cycle 20) was explicitly designed with "Foundation for future parallel execution of independent tasks" — this cycle delivers on that foundation.
- **Ecosystem**: Multi-agent orchestration is a trend in coding tools (Claude Code agents, etc.). The Architect differentiates by running independent tasks in parallel within a single autonomous session, reducing wall-clock time without requiring multiple provider instances or complex coordination.
- **Chosen**: Parallel Task Execution — run independent tasks concurrently based on dependency graph
- **Scope**: Standard — 4 tasks (T01: scheduler module, T02: runner integration, T03: TUI display, T04: tests)
- **Config**: `max_parallel_tasks: int = 1` in `ArchitectConfig` (default 1 for backward compatibility)
