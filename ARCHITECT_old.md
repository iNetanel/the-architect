# ARCHITECT.md — Project Intelligence

> This file is The Architect's persistent memory for this project.
> It is read at the start of every planning session and every task execution.
> It stores durable project intelligence only — not run history.
> Run/package history belongs in tasks/SUMMARY.md and archived task packages.
> The Repository Map section is updated automatically on each plan.
> Other sections accumulate durable knowledge over time — never add temporary
> task notes here unless they will help future unrelated work.

---

## Project Overview

> What this product/project is, who it serves, and the main capabilities it owns.

The Architect is a pure-Python CLI tool that adds an autonomous development lifecycle on top of any AI coding CLI (OpenCode, Codex CLI, Claude Code, Gemini CLI). It never writes application code itself — it orchestrates the chosen CLI to plan, execute, retry, and review work. The target audience is developers who want fire-and-forget autonomous development: describe a goal (or point to a PRD/spec), walk away, and come back to results.

Core capabilities: goal decomposition into numbered task files (T01…), unattended task execution with live streaming, multi-signal completion detection, stuck detection, automatic retry with model fallbacks, circuit breaker, retrospective review with fix-up tasks (R01…), persistent project intelligence (ARCHITECT.md), and tmux/Textual TUI dashboards.

### Auto-Detected Project Intelligence

- Project shape: Single repo with 4 detected components.
- Treat the Repository Map as the source of truth for detected paths, dependencies, and verification commands; this section is refreshed on each plan.
- Root Python project: `the-architect` - An autonomous development lifecycle layer for agentic AI coding tools.

---

## Repository Map

**Type:** Single repo
**Scanned:** 2026-05-13

### Components

**./** — Python
> An autonomous development lifecycle layer for agentic AI coding tools
> Stack: questionary, loguru, rich, click, pydantic, httpx, textual
> test: `pytest tests/ -v --tb=short` | lint: `ruff check .`

**the_architect/** — Python · CLI package
> Python import package owned by the root pyproject.toml

**dev/** — Development environment
>
> Sub-components:
> **opencode/** — JavaScript/TypeScript
> > Stack: @opencode-ai/plugin

---

## Tech Stack

> Languages, frameworks, package managers, runtimes, databases, storage,
> and external services by repo/component.

- **Runtime:** Python 3.11+ (tested on 3.11, 3.12, 3.13); no database, no web server
- **CLI layer:** Click 8+
- **Terminal output:** Rich 13+ (panels, tables, live display); Textual 0.80+ (full TUI)
- **Logging:** Loguru — `print()` is forbidden for internal logging
- **Validation:** Pydantic v2 (`model_validate`, not `parse_obj`)
- **HTTP:** httpx 0.27+ (used for OpenRouter free-model fetch and self-update checks)
- **Interactive prompts:** questionary 2+
- **TOML parsing:** `tomllib` built-in (Python 3.11+) — never add `tomli` as a dep
- **Build backend:** Hatchling; `pyproject.toml` is the SemVer source of truth
- **Linting/formatting:** Ruff (line length 100); **not** Black
- **Type checking:** mypy strict mode
- **Test framework:** pytest + pytest-asyncio (`asyncio_mode = "auto"`) + pytest-cov
- **Dev/OpenCode plugin:** `dev/opencode/` — JavaScript/TypeScript (`@opencode-ai/plugin`)

### Auto-Detected Project Intelligence

- `./` — Python: Python; stack: questionary, loguru, rich, click, pydantic, httpx, textual.
- `the_architect/` — Python · CLI package: Python.
- `opencode/` — JavaScript/TypeScript: JavaScript/TypeScript; stack: @opencode-ai/plugin.
- Python build backend: `hatchling.build`.

---

## Architecture

> Major systems, ownership boundaries, and how components connect.

**CLI (`the_architect/cli.py`)** — Click entry point; all commands defined here. Wires together config, provider detection, TUI/tmux, and the planning/execution engine.

**Config (`the_architect/config.py`)** — `ArchitectConfig` (Pydantic v2 model); loaded from `architect.toml` under `[architect]` section; written back by `write_config()`. Path fields are resolved at runtime and not persisted to TOML.

**Provider layer (`the_architect/core/provider.py`)** — Abstract `ArchitectProvider`; concrete implementations: `opencode_provider.py`, `claude_code_provider.py`, `codex_cli_provider.py`, `gemini_cli_provider.py`. Auto-detection order: OpenCode → Codex CLI → Claude Code → Gemini CLI.

**Runner (`the_architect/core/runner.py`)** — Streams provider subprocess output; exposes `StreamRenderer` seam for TUI/tmux adapters; returns `StreamResult` with exit code and token/file metrics.

**Planner (`the_architect/core/planner.py`)** — Calls provider with the architect prompt to decompose the goal into T01…TNN task files; also runs targeted reassessment after tasks.

**Circuit breaker (`the_architect/core/circuit.py`)** — State machine (CLOSED → OPEN → HALF_OPEN); tracks no-progress, same-error fingerprint, and token-decline signals; persisted to `.architect/` so state survives process kills.

**Retrospective (`the_architect/core/retrospective.py`)** — Runs reviewer prompt after execution; creates R01…RNN fix-up tasks if quality issues are found.

**Project intelligence (`the_architect/core/intelligence.py`)** — Pre-planning quality gate: checks `ARCHITECT.md` for placeholder sections; if shallow, runs a model-based refresh pass before planning.

**TUI (`the_architect/tui/`)** — Textual-based full-terminal UI; `ArchitectApp` is a persistent app with execution and wait overlays; `TextualStreamRenderer` plugs into the `StreamRenderer` seam.

**Structure detection (`the_architect/core/structure.py`)** — Deterministic repo-shape detection (languages, frameworks, test/lint commands, sub-components); result injected into every prompt.

**Workspace baseline (`the_architect/core/baseline.py`)** — Captures file checksums of key project areas (tasks/, root files) before each task execution; detects created/modified/deleted files afterward; stored as JSON in `.architect/baselines/`; provides concrete evidence for retrospective review.

### Auto-Detected Project Intelligence

- Component authority: each component owns implementation under its path; cross-component behavior should be coordinated through explicit contracts or integration tasks.
- No explicit inter-component dependencies were detected automatically.

---

## Key Flows

> Important runtime flows such as auth, lifecycle transitions, streaming,
> agents, scheduling, persistence, and deployment.

**Planning flow:** User provides a goal (text or `--context` path) → structure detection → ARCHITECT.md quality gate → optional project intelligence refresh → architect prompt + context → provider streams task files (T01…TNN) into `tasks/` → `PROGRESS.md` initialized.

**Execution flow:** Task files loaded → for each task: inject execution-protocol prompt + task content → stream provider output → multi-signal completion detection (promise tag + PROGRESS.md update + exit code + progress keywords) → inter-task reassessment (always if `force_reassessment=true`; else on failure or downstream-impact signal) → next task.

**Retry/circuit flow:** On failure → check circuit state → if CLOSED: retry up to `max_retries` with optional model fallbacks (`retry_model_2`, `retry_model_3`) and carry-context injection → cooldown detection (HTTP 429/529, "rate limit") pauses without consuming retry slots → circuit opens on no-progress/same-error/token-decline thresholds → replan or WAIT recovery.

**Retrospective flow:** After all tasks done → reviewer prompt → if fix-up tasks generated (R01…) → execute them → repeat up to `retrospective_rounds` (default 1).

**Persistent mode:** `max_retries=30`, `retrospective_rounds=3`; designed for unattended long sessions with deeper validation/recovery capacity.

**Release flow:** Bump `__version__` in root `version.py` and `pyproject.toml` → bump `__build__` → add `CHANGELOG.md` entry → push to main → CI creates a canonical GitHub release and requests PyPI approval only if SemVer changed; build-only pushes create a `v<version>+<build>` prerelease and do not publish.

### Auto-Detected Project Intelligence

- CLI entry point `architect` resolves to `the_architect.cli:main`.

---

## Shared Contracts

> Stable API shapes, schemas, events, config keys, stage names, agent names,
> and cross-component contracts.

**Task naming:** `T01`, `T02`, … for planned tasks; `R01`, `R02`, … for retrospective fix-up tasks.

**Completion signals (multi-signal, ≥2 required):**
1. Promise tag: `<promise>TXX_COMPLETE</promise>` — strongest signal
2. PROGRESS.md updated to Done for the task
3. Provider CLI exit code 0
4. Progress keywords in output ("all tests pass", "task is done")
- If output contains "I'm stuck" / "can't proceed", that overrides all completion claims.

**Downstream impact signal:** Agent reports `Downstream impact: possible` or `Downstream impact: none` in its completion block; drives conditional reassessment when `force_reassessment=false`.

**Build counter contract:** `__build__` in root `/version.py` — increments once per completed task (including R-tasks and merged PRs). Never resets. Always 5 digits. Build floor: v1.x.x → 10000+; v2.x.x → 20000+.

**Config contract:** `architect.toml` under `[architect]` section; all scalar fields from `ArchitectConfig`; path fields resolved at runtime, not stored.

**PROGRESS.md format:** Strict regex-parsed format; see `documentation/PRACTICES.md` for exact syntax. Do not invent new status strings.

**Agent names (this repo's OpenCode config):** `master` (default), `backend`, `frontend`, `qa-fast`, `qa-deep`, `explore`, `debug`, `docs`, `knowledge`. `build` and `plan` are disabled.

**Prompt files (packaged):** `architect.md` (planner), `reviewer.md` (retrospective), `execution-protocol.md` (injected into every task run), `intelligence.md` (project intelligence pass).

**Workspace baseline contract:** JSON files in `.architect/baselines/<task_name>.json` containing timestamp, task prefix, and file checksums (SHA-256). `TaskResult.baseline_path` stores the absolute path to the baseline file. `ArchitectConfig.workspace_baseline` (default: true) enables/disables capture. Change detection returns `{"created": [...], "modified": [...], "deleted": [...]}` with relative file paths.

**Status JSON output contract:** `architect status --json` outputs a deterministic JSON object to stdout with fields: `project` (string path), `running` (bool), `pid` (int|null), `tasks` (array of `{prefix, title, status}`), `task_summary` (`{total, done, failed, pending, blocked}`), `circuit_breakers` (array of `{task, state, no_progress, same_error}` for non-CLOSED entries), `token_budget` (object with `limit` or null), `log_dir` (string or null), `log_files` (array of `{name, size_kb}` or null). Status strings must match PROGRESS.md values exactly: "Done", "Failed", "Blocked", "Pending".

### Auto-Detected Project Intelligence

- CLI entry point `architect` resolves to `the_architect.cli:main`.
- `ARCHITECT.md` stores durable project intelligence; current run state belongs in `tasks/PROGRESS.md` and package history in `tasks/SUMMARY.md`.

---

## Code Locations

> Where important systems live so agents can start focused exploration quickly.

### Auto-Detected Project Intelligence

- `./` — mission: An autonomous development lifecycle layer for agentic AI coding tools; authority: files and behavior inside this path unless a task states a cross-component contract.
- `the_architect/` — mission: Python import package owned by the root pyproject.toml; authority: files and behavior inside this path unless a task states a cross-component contract.
- `dev/` — mission: Development environment; authority: files and behavior inside this path unless a task states a cross-component contract.
- `opencode/` — mission: detected project component; authority: files and behavior inside this path unless a task states a cross-component contract.
- `documentation/` - project documentation and durable technical references: `documentation/ARCHITECTURE.md`, `documentation/CONCEPTS.md`, `documentation/PRACTICES.md`.
- `README.md` - user-facing overview and CLI/reference documentation.
- `the_architect/resources/prompts/` - packaged prompts injected into provider runs.
- `tests/` - automated test suite; mirror source module names when adding coverage.

---

## Build, Test, and Verification

> Commands and verification expectations by repo/component.

### Auto-Detected Project Intelligence

- `./`: test `pytest tests/ -v --tb=short`; lint `ruff check .`.
- Python tests: `pytest tests/ -v --tb=short`.
- Python lint/format: `ruff check .` and `ruff format --check .`.
- Python typecheck: `mypy the_architect/` when this package path exists; otherwise inspect pyproject for the typed package path.
- CI workflows: `.github/workflows/ci.yml`.

---

## Style and Code Standards

> Coding style, naming, file-size guidance, class/function boundaries, logging,
> typing, testing, comments, and frontend/backend conventions.

- **Line length:** 100 (configured in `pyproject.toml` for ruff; not 88/79)
- **Logging:** Loguru only — `print()` allowed only for user-facing CLI output where Rich is not appropriate; forbidden for internal/debug logging
- **Pydantic:** v2 API — use `model_validate()`, not `parse_obj()`
- **TOML:** use `tomllib` (built-in Python 3.11+) — never add `tomli`
- **Type hints:** required on all public functions and classes; mypy strict is on
- **Docstrings:** required on all public functions and classes
- **Async tests:** `pytest-asyncio` with `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` decorator needed)
- **Test structure:** one test file per source module — `tests/test_circuit.py` covers `the_architect/core/circuit.py`
- **Test isolation:** `conftest.py` auto-clears `OPENCODE_CONFIG` and `OPENCODE_CONFIG_DIR` for every test; no external services required
- **CI check order:** `ruff check .` → `mypy the_architect/` → `pytest tests/ -v --tb=short` (run in this sequence)
- **Commit message format:** `type: short description (build XXXX)` — types: fix, feat, docs, refactor, test, chore, perf, ci

---

## Agent and AI Conventions

> Agent configs, prompt locations, model routing, tool metadata,
> AI communication patterns, and provider-specific conventions.

### Auto-Detected Project Intelligence

- Check `documentation/` for canonical project practices before broad changes.
- `AGENTS.md` is a provider/user rule file; read and follow it, but do not treat it as generated project memory.
- `the_architect/resources/prompts/` contains packaged Architect prompts; prompt changes affect planner/reviewer/executor behavior and need extra review.
- `dev/opencode/` contains this repo's OpenCode development config and agent prompt files.

---

## Data and Storage

> Databases, buckets, collections, object paths, persistence conventions,
> and data ownership boundaries.

### Auto-Detected Project Intelligence

- `tasks/` stores Architect task packages and `tasks/PROGRESS.md` for current run state.
- `.architect/` stores Architect runtime state such as logs, locks, circuit state, prompts, and monitor data.

---

## Environment and Secrets

> Environment files, required variables, secret-handling rules, local services,
> and setup constraints.

### Auto-Detected Project Intelligence

- Environment files are present; never commit secrets and prefer documented sample values.

---

## Operational Constraints

> Ports, background services, rate limits, dangerous commands,
> deployment assumptions, and runtime limits.

### Auto-Detected Project Intelligence

- Stay inside the project root and follow component ownership boundaries unless the task explicitly requires integration work.
- `CHANGELOG.md` records user-visible changes; update it when project rules require release notes.
- Root `version.py` exists; inspect it for project-specific version/build rules before release or task completion work.
- Keep generated task state in `tasks/`; do not mix run history into `ARCHITECT.md`.

---

## Permanent Decisions

> Decisions made during planning that must not be revisited.

| Decision | Value | Reason | Added |
|----------|-------|--------|-------|
| `architect doctor` uses static checks only | No live provider probe in doctor command | Live probe already exists in `provider_health.py`; doctor is a fast pre-flight check, not a health endpoint | 2026-05-12 |
| Workspace baseline scope limited to tasks/ and root files | Full workspace checksumming is too slow | Baselines must be fast; tasks/ and root files cover where deliverables typically live | 2026-05-13 |
| Baseline capture is non-fatal | Errors during baseline capture/detection log a warning and continue | Task execution must not be blocked by filesystem issues | 2026-05-13 |
| Change detection only on success path | Failed tasks capture baseline but skip change detection | Change summary only meaningful when the task actually completed | 2026-05-13 |
| Status JSON uses `--json` flag with `as_json` param_name | `json` is a reserved module name in Click | Prevents naming conflict | 2026-05-13 |
| Status JSON field `no_progress` maps to `consecutive_no_progress` | Shorter JSON field names for machine readability | Schema contract | 2026-05-13 |
| Status JSON field `same_error` maps to `consecutive_same_error` | Shorter JSON field names for machine readability | Schema contract | 2026-05-13 |
| JSON output commands use `click.echo()` not Rich console | Pure JSON output must not contain Rich markup | Scripting compatibility | 2026-05-13 |
| JSON output uses `sort_keys=True` and `indent=2` | Deterministic, human-readable JSON | Schema contract | 2026-05-13 |

---

## Known Constraints

> Things the architect and execution agents must always respect.

- **Never write application code directly** — The Architect only orchestrates the AI CLI; it does not call AI APIs itself
- **Build counter is mandatory** — every completed task (including R-tasks) must increment `__build__` in root `version.py` by exactly 1; check with `grep "__build__" version.py` before marking Done
- **Two version files, two roles** — root `version.py` holds `__build__` + `__version__` + `__banner__`; `the_architect/version.py` reads SemVer from `importlib.metadata` at runtime — never edit it for build bumps; `pyproject.toml` SemVer is human-only, never agent-modified
- **No `tomli` dependency** — `tomllib` is built-in from Python 3.11+; adding `tomli` is redundant and wrong
- **Prompt files require human approval** — changes to `the_architect/resources/prompts/*.md` and `dev/opencode/prompts/*.md` affect all planner/reviewer/executor behavior; flag with `PROMPT UPDATE SUGGESTED` and get explicit sign-off
- **Never touch git unless explicitly asked** — no commits, no pushes, no tags, no branch operations
- **Tests must pass before marking any task Done** — never skip or suppress a failing test
- **No destructive commands without explicit approval** — `rm -rf`, database drops, force pushes, history rewrites all require human confirmation
- **No hardcoded secrets, API keys, or URLs** — use environment variables; never commit `.env`
- **OpenCode dev config location** — this repo's OpenCode config lives at `dev/opencode/opencode.json`, not the project root; OpenCode must be launched from `dev/opencode/` or with `OPENCODE_CONFIG` pointing there
- **CHANGELOG entries** — every user-visible change needs an entry under `## [Unreleased]`; internal refactors, test-only, and doc-only changes do not, but still need a build bump
- **New behavior needs test coverage** — PRs without tests will be asked to add them
- **`the_architect/core/intelligence.py` pass is non-fatal** — if the provider fails during the pre-planning intelligence refresh, normal planning continues with deterministic memory rather than blocking the user
- **Full pytest runtime can exceed short command timeouts** — `pytest tests/ -v --tb=short` has completed successfully in this workspace but can take about 3 minutes; use a timeout of at least 300 seconds before treating it as hung
- **CLI commands use `@main.command()` decorator pattern** — new subcommands are registered via `@main.command(name="<verb>")` on functions in `cli.py`; follow existing patterns for options, Rich output, and `SystemExit` for non-zero exit codes

---

## Lessons Learned

> Discovered during execution. Informs future planning.

- **`architect_eval_*` files are corruption signals** — any `architect_eval_*` file left behind after a task indicates the file integrity protocol detected a truncated write; the retrospective and reassessment passes both check for these and flag them
- **`conftest.py` env isolation is intentional** — it clears `OPENCODE_CONFIG` and `OPENCODE_CONFIG_DIR` before every test to prevent the dev OpenCode config (at `dev/opencode/`) from leaking into tests that create their own config; never remove this fixture
- **Completion multi-signal requirement prevents hallucination** — a single "task complete" string in output is not trusted; the stuck-detection override ("I'm stuck") takes precedence over any completion claim made in the same output
- **Build counter doubles as a lie detector** — if an agent forgets to bump `__build__`, the task is not considered done; the protocol enforces honesty about cumulative effort
- **Some test_cli.py classes hang when not all async entry points are patched** — `_run_main` with pending tasks calls `asyncio.run()` internally; any unpatched async function (e.g. `_read_goal_from_instructions`, `detect_provider`, `ensure_setup`) can block the test process; always patch the full call chain or use headless=True paths that skip the blocking section
- **`test_project_intelligence.py` covers `intelligence.py` assess logic; `test_intelligence.py` covers structure/architect_md** — the naming is historical; `intelligence.py` (async refresh pass) needs its own dedicated test coverage in `test_project_intelligence.py`
- **New private helpers in architect_md.py need direct import tests** — `_read_toml`, `_read_json`, `_as_dict`, `_as_list`, `_script_lines_from_pyproject`, `_verification_lines`, `_detected_project_intelligence_sections` are all importable and should be unit-tested directly to avoid relying solely on integration-level coverage
- **No-op validation packages require task-start baselines for workspace checks** — a no-op task with "zero code changes" claim creates a contract, but reviewers must not blame the task for dirty working-tree state unless The Architect captured a task-start baseline proving the task introduced those changes. Without a baseline, dirty-worktree findings are diagnostic only and must not produce destructive recovery R-tasks.
- **History files in project root follow a single-paragraph pattern** — `history1.md` through `history5.md` are all single-paragraph narratives (~100 words each) on distinct periods of American history; existing covered periods: broad historical sweep, post-WWII, Early Republic, Progressive Era, Great Depression/New Deal; future "create next history file" tasks should pick uncovered periods and maintain this consistent narrative style
- **PROGRESS.md "Done" status does not guarantee file/artifact existence** — task can show as complete (green tests, build bump recorded) while primary deliverables remain absent from disk; retrospective review catches these by verifying stated outcomes against filesystem reality; future tasks with file/artifact deliverables must include explicit `ls -l <file>` verification in their completion record before claiming Done
- **Workspace baselines provide proactive evidence** — `baseline.py` captures file checksums before each task and detects changes after; baseline data in TaskResult gives retrospective reviewer concrete evidence of what each task actually changed, replacing filesystem-only checks with task-scoped baselines
- **R-tasks increment build counter exactly once per task** — R01 bumped `__build__` by 1 independent of T01's bump; every completed task (planned T-task or retrospective R-task) gets one build increment, never zero, never more than one
- **`config.standalone_mode` acts as model override in `refresh_project_intelligence`** — `intelligence.py` line 182 uses `model_override or config.standalone_mode or None` to pass the model to `stream_provider`; this fallback path was untested until R01 added `test_refresh_uses_standalone_mode_as_model_override`
- **`provider_health.py` guards against mock providers via module prefix check** — `check_provider_health` returns early if `provider.__class__.__module__` does not start with `"the_architect.core."`; test fakes must spoof `__module__ = "the_architect.core.<provider_name>"` to exercise real paths
- **Fallback paths behind always-available dependencies need explicit mock tests** — when a code path depends on a library being unavailable (e.g. `self_update._is_newer` falls back to tuple comparison when `packaging` is missing), the fallback has zero coverage in normal test environments; mock the import to force the fallback path
- **`sys.modules.pop` alone does not block cached submodule imports** — `packaging.version` is cached in the parent `packaging` module's `__dict__` after any prior import; `sys.modules.pop("packaging.version")` is insufficient. Use `patch.object(__import__("packaging.version", fromlist=["Version"]), "Version", side_effect=ImportError(...))` to force fallback paths (T01)
- **`progress.py` public functions follow a consistent defensive pattern** — every public function accepting `Path | str` converts to Path first, then handles OSError/UnicodeDecodeError on file I/O; tests must cover the str-to-Path branch AND both error branches for each function; the established test pattern uses `TestStringPathBranches` and `TestUnreadableFileExceptionPaths` classes with `patch.object(Path, "read_text", side_effect=OSError(...))` patterns
- **Coverage requires dotted module path for pytest-cov** — `--cov=the_architect.core.progress` works; `--cov=the_architect/core/progress` (slash) triggers "module not imported" warning (T01)
- **`circuit.py` uncovered lines are all defensive error paths** — the 21 uncovered lines (as of build 10352) are `except` blocks, early-return guards, and threshold-trigger branches; covering them requires crafting specific failure inputs (malformed JSON state, OSError on file I/O, timezone-naive datetimes, zero token counts)
- **`_replan` is async and imports `stream_provider` at runtime** — testing `_replan` requires async test methods and mocking `stream_provider` from `the_architect.core.runner`; the method also imports `discover_tasks` at runtime; patch both imports in tests
- **Patch `io.open` not `builtins.open` to intercept `Path.read_text`** — `pathlib.Path.read_text()` calls `io.open()` internally (not `builtins.open` directly), so patching `builtins.open` has no effect; use `patch("io.open", side_effect=...)` instead (T03)
- **`patch.object` with `return_value` avoids mypy method-assign errors** — assigning lambdas directly to instance methods (e.g. `obj.method = lambda: ...`) triggers mypy's `method-assign` error; use `patch.object(obj, "method", return_value=...)` instead (T03)
- **`retrospective.py` uncovered lines are all defensive error paths** — the 30 uncovered lines (as of build 10364) are `except` blocks, early-return guards, and provider-error branches; lines 269, 316-321, 370 were covered in T02; remaining: 116, 134-136, 139, 144-145, 152, 155, 174, 177 (provider setup helper, T01), 671+ (reassessment, T03)
- **`run_task_reassessment` requires async stream_provider mocking** — testing reassessment paths requires `patch("the_architect.core.retrospective.stream_provider", side_effect=fake_stream)` where `fake_stream` is an async function returning `StreamResult`; the method also reads task files and PROGRESS.md after the stream call, so post-stream file reads need separate OSError patches
- **`Path.stat` patching requires phase awareness** — when testing OSError on `.stat()` in `_gather_review_context`, the same `.stat()` is called during file discovery (`is_file()`, `is_symlink()`, `exists()`) and during eval snapshot processing (lines 315, 319). Use a phase flag (set after `_find_eval_snapshot_files` returns) to let discovery succeed while failing the eval section. Also distinguish `follow_symlinks=False` (from `is_symlink()`) vs direct `.stat()` calls (T02)
- **`_find_eval_snapshot_files` is importable for mocking** — it can be patched via `patch("the_architect.core.retrospective._find_eval_snapshot_files", ...)` to control the discovery-vs-eval phase boundary in tests (T02)
- **`success.py` validation_passed=True requires explicit RetrospectiveRound construction** — the `write_success_md` retrospective table builder has three branches for `validation_passed` (None, True, False); tests must construct `RetrospectiveRound(validation_passed=True)` to exercise the "✓ Passed" branch (line 224)
- **`write_summary_md` is a thin alias for `write_success_md`** — calling it directly in tests exercises line 306; it delegates to `write_success_md` with the same arguments
- **`context.py` architect_eval_ skip requires a prefixed file in scanned directory** — `read_context_directory` silently skips files starting with `architect_eval_`; test must create such a file and verify exclusion from results (line 161)
- **Testing CLI commands with provider detection — mock `detect_provider` at function level** — when testing new CLI commands that call `detect_provider()`, patch `the_architect.cli.detect_provider` to return a `MagicMock` with configured attributes (`name`, `display_name`, `is_installed`, `get_version`, `has_any_models`, `check_update_available`). Do NOT try to build fake provider objects that satisfy the full `ArchitectProvider` protocol — that adds unnecessary complexity and was the root cause of T02's 3 failures.
- **`load_config` returns defaults when no `architect.toml` exists** — it does NOT raise; use `config_file.exists()` to check for the file before calling `load_config()`. A missing config file is not an error — The Architect runs with defaults.
- **Failed tasks benefit from exact code templates, not just descriptions** — when a task has failed multiple attempts, provide complete code templates for both implementation and tests rather than just describing the desired behavior. Agents tend to "improve" open-ended descriptions in ways that introduce failures.
- **Implementation tasks must be implementation-only** — when a task has failed multiple attempts, the recovery task must contain ONLY implementation instructions with no planning subtasks. Agents will treat tasks with extensive context, boundaries, and exploration plans as planning exercises and rewrite task files instead of editing source code. Keep recovery tasks under 100 lines with direct "edit this file, insert this code" instructions.
- **Recovery tasks must use flat "Edit N" structure, not "Task N.N" subtasks** — even after stripping context/boundaries, agents still treated `### R02.1 — Add doctor_cmd function to cli.py` as a planning subtask and rewrote the task file. Use flat numbered edits (`## Edit 1`, `## Edit 2`) with code blocks, not nested task headings. The pattern that worked: Goal → Edit 1 (file + code) → Edit 2 (file + code) → Edit 3 (file + code) → Verify (commands). No Context, Boundaries, or Tasks sections.
- **Baseline capture in run_task_once must be non-fatal** — all baseline operations (capture, write, detect changes) are wrapped in try/except with `logger.warning` on failure; a filesystem error must never crash the task execution flow. Baseline capture happens before `start_time = time.monotonic()` so it is excluded from task duration. Change detection only runs on the success path (exit_code=0).
- **`cli.py` is the largest coverage gap at 63%** — all core modules are at 95%+; the remaining uncovered lines in `cli.py` are mostly interactive prompt screens (provider selection, mode selection, update prompts) and command entry points that require full Click test harnesses; new CLI features should include direct function tests for helper methods and Click `CliRunner` integration tests for command entry points.
- **`status_cmd` already imports `json` as `_json` internally** — the function had `import json as _json` at runtime; consolidate to module-level `import json` and replace all `_json.` references to avoid shadowing (T01)
- **JSON output commands follow a consistent pattern** — `--json` flag with `as_json` param_name, `_format_<cmd>_json()` helper returning `json.dumps(..., sort_keys=True, indent=2)`, `click.echo()` for pure output, early exit when JSON mode active (T01)

---

## Best Practices

> Patterns that emerged from working with this codebase.

- **Read `documentation/PRACTICES.md` first** — it is the canonical, tool-agnostic rule file; `AGENTS.md`, `CLAUDE.md`, and `ARCHITECT.md` are thin pointers to it
- **CI check sequence matters** — always run ruff → mypy → pytest in that order; linting catches problems that confuse the type checker, type errors surface before running slow tests
- **Install editable with dev extras** — `pip install -e ".[dev]"` is the standard dev setup; ensures the CLI entry point and all test dependencies are available
- **One-test-per-module mirroring** — keep `tests/test_<module>.py` paired with `the_architect/core/<module>.py`; this makes it easy to find coverage gaps and avoids test sprawl
- **`ArchitectConfig` is frozen by default in tests** — when constructing configs in tests, use `ArchitectConfig(...).resolve(tmp_path)` to get absolute paths; never hardcode paths
- **Pydantic `model_config = {"extra": "ignore"}`** — `ArchitectConfig` silently drops unknown keys from `architect.toml`; this allows forward-compatibility when older configs are loaded against newer code
- **Avoid adding runtime dependencies** — the dependency list is intentionally minimal; prefer stdlib solutions (e.g. `tomllib`, `importlib.resources`) over new packages
- **No-op smoke tasks are valid pipeline checks** — keep them as standard task packages that verify execution/progress plumbing without changing application behavior, aside from repository-mandated task completion bookkeeping.
- **No-op task packages still follow completion protocol** — even when the intended product change is intentionally empty, execution agents must satisfy normal verification, PROGRESS.md handoff, and root `version.py` build-bump requirements.
- **No-op smoke tests should not require eliminating intentional skips** — passing tests with documented/environmental skips is acceptable for no-op validation; do not install optional packages or change dependencies solely to turn existing skips into executed tests unless the task explicitly targets that dependency.
- **Retrospective fix-up tasks must not require git commits or destructive restores** — R-tasks must not instruct agents to run `git checkout`, `git reset`, `git restore`, `git clean`, broad file deletion, commits, tags, pushes, or other mutating git/file recovery unless the human explicitly requested it. Dirty workspace concerns should be reported as human-action notes unless a safe task-start baseline proves the task caused the change.
- **Use coverage reports as evidence for improvement cycles** — run `pytest tests/ --cov=the_architect --cov-report=term-missing` to identify exact uncovered line numbers; target modules with the lowest coverage percentages first; prefer covering real error handling paths over adding new feature tests when choosing between improvements
- **Coverage requires dotted module path** — use `--cov=the_architect.core.progress` (dots) not `--cov=the_architect/core/progress` (slashes) to avoid "module not imported" warnings under pytest-cov
- **Coverage improvement cycles target one module at a time** — run full coverage report, pick the next-highest-impact module (lowest % or most defensive uncovered lines), decompose into small tasks by method group, execute sequentially. Each cycle produces measurable progress toward 100% on that module.
- **Transition from coverage to features when core modules reach 95%+** — once core modules (circuit, progress, architect_md, planner, provider, success, context, intelligence) are all at 95%+ coverage, shift focus from coverage improvement to new features that strengthen The Architect's core role: planning, orchestration, validation, retries, recovery, memory, review, observability, or developer control.
- **Reviewer must inspect actual code before creating fix-up tasks** — SUMMARY.md `Failed` rows do not always mean implementation is missing; the runner's completion-signal check may have missed a successful edit. Always grep for the expected symbol/function in the source file before concluding the implementation was never written. If the code exists, tests pass, and CI is clean, do not create a fix-up task — the cycle is complete.
- **Baseline capture is scoped to tasks/ and root files only** — workspace baselines track checksums for the `tasks/` directory and text files with common extensions (`.py`, `.toml`, `.json`, `.md`) in the project root; binary files and symlinks are skipped; hidden directories are excluded; this keeps capture fast while covering the areas where deliverables typically live
