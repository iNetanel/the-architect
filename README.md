<div align="center">

<img src="https://raw.githubusercontent.com/inetanel/the-architect/main/assets/architect.svg" alt="The Architect" width="100%">

**An autonomous development lifecycle layer for agentic AI coding tools.**
Describe a goal. Walk away. Come back to results.

[![PyPI version](https://img.shields.io/pypi/v/the-architect?color=blue&label=PyPI)](https://pypi.org/project/the-architect/)
[![Python](https://img.shields.io/pypi/pyversions/the-architect)](https://pypi.org/project/the-architect/)
[![CI](https://github.com/inetanel/the-architect/actions/workflows/ci.yml/badge.svg)](https://github.com/inetanel/the-architect/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/inetanel/the-architect?style=social)](https://github.com/inetanel/the-architect)

[Website](https://inetanel.com/projects/the-architect) · [Quickstart](#quickstart) · [Features](#features) · [CLI Reference](#cli-reference) · [Configuration](#configuration) · [Contributing](CONTRIBUTING.md)

</div>

---

## Why This Exists

AI coding agents are powerful. But left alone, they drift — they lose context, repeat mistakes, hallucinate completion, and have no memory of what they did yesterday.

**The Architect solves the orchestration problem, not the coding problem.**

It wraps your agentic AI coding tool and adds everything it lacks:

| Without The Architect | With The Architect |
|---|---|
| You manage the task list manually | Tasks planned automatically from your goal |
| Agent loses context between sessions | Persistent memory via `ARCHITECT.md` |
| Setup issues discovered mid-run | `architect doctor` checks all providers before you start |
| No recovery when agent gets stuck | Circuit breaker + smart retry + model fallback |
| You babysit every run | Fully unattended — fire and forget |
| No audit trail of what changed | Build counter + token ledger track every change and cost |
| Agent hallucinates completion | Multi-signal completion detection |
| You re-explain the project every time | Project structure auto-detected, git context injected |
| Repo knowledge starts shallow | Pre-planning intelligence learns and repairs `ARCHITECT.md` |
| One shot — no quality gate | Retrospective reviewer + validation gate until clean |
| Developer plans every task manually | AI planner decomposes the goal autonomously |
| Sequential execution only | **Parallel execution** — independent tasks run concurrently |
| No cost visibility | **Token ledger** — per-run and per-task cost tracking |
| No way to undo a bad run | **Rollback** — restore files to pre-run state |
| No pre-run cost estimate | **`architect estimate`** — predicts cost from historical data |
| Goals typed from scratch every time | **Goal templates** — reusable goals with `{variable}` placeholders |
| No desktop notification | **Notifications** — desktop alert + terminal bell on completion |
| No dependency awareness | **Task dependencies** — cycle detection, smart skipping |
| Run and hope | **`--dry-run`** — plan and review before committing tokens |

The agent does the coding. The Architect makes sure it actually finishes.

---

## Demo

```text
┌─────────────────────────────────────┬─────────────────────────────────┐
│                                     │  ▀▀▀ THE ARCHITECT ▀▀▀          │
│  $ architect                        │─────────────────────────────────│
│                                     │  TASKS                          │
│  The Architect configure run        │  ✓ T01  Init project  (done)    │
│  Free Tier    [ ]                   │  ● T02  Payment routes (running)│
│  Persistent   [ ]                   │  ○ T03  Webhook handler         │
│  Token budget/hr: 0                 │  ○ T04  Tests and docs          │
│                                     │─────────────────────────────────│
│  Planning...                        │  STATUS                         │
│                                     │  Task    : T02 / 4              │
│  T01  Init payment module           │  Attempt : 1 / 3                │
│  T02  Stripe routes and models      │  Circuit : CLOSED               │
│  T03  Webhook handler               │─────────────────────────────────│
│  T04  Tests and docs                │  TOKENS                         │
│                                     │  Session : 24.5K                │
│  Executing T01...                   │  Last    : 8.2K                 │
│  -> write app/payments/models.py    │─────────────────────────────────│
│  $ pytest tests/ -v                 │  BUILD                          │
│  12 passed                          │  v1.2.15 (build 10549)          │
│  <promise>T01_COMPLETE</promise>    │                                 │
│                                     │                                 │
└─────────────────────────────────────┴─────────────────────────────────┘
```

> **Demo GIF coming soon.** [Contribute one?](CONTRIBUTING.md)

---

## Quickstart

**Prerequisites:** Python 3.11+ and at least one supported AI coding CLI installed and configured.

```bash
# Install
pip install the-architect

# Go to your project
cd your-project

# Start The Architect
architect
```

On a fresh project, `architect` opens the configure flow and starts planning.
If pending tasks exist, it resumes automatically.
If all tasks are done, it shows the menu (or use `--plan` to start fresh).

### Common Flows

```bash
# ── Planning ──────────────────────────────────────────────────────────
architect                                    # interactive: plan or resume
architect --plan                             # force new planning
architect --plan --goal "add OAuth login"    # plan with explicit goal
architect --plan --context PRD.md            # plan from a document

# ── Dry Run (plan without executing) ─────────────────────────────────
architect --dry-run --goal "add auth"        # review plan + cost estimate
architect --dry-run --goal "add auth" --json # machine-readable output

# ── Execution ────────────────────────────────────────────────────────
architect --from T03                         # resume from specific task
architect --only T05                         # run a single task
architect --persistent                       # 30 retries, 3 review rounds
architect --headless --goal "fix mypy"       # CI / unattended mode

# ── Templates ────────────────────────────────────────────────────────
architect template create "api" --goal "Build API for {service}"
architect template run "api" --var service=payments

# ── Observability ────────────────────────────────────────────────────
architect estimate                           # predict cost before running
architect history                            # view past runs
architect token-report                       # spending breakdown
architect report                             # last run's summary
architect diff                               # workspace changes per task
architect monitor                            # live TUI monitor

# ── Safety ───────────────────────────────────────────────────────────
architect doctor                             # pre-flight diagnostics
architect doctor --project                   # project health checks
architect doctor --live                      # live provider connectivity
architect rollback                           # restore pre-run state

# ── Configuration ────────────────────────────────────────────────────
architect config                             # show current config
architect config --set max_retries=5         # update a setting
architect preset create "sprint" --field max_retries=10  # save a profile
```

---

## How It Works

```text
  Your Goal
      |
      v
  +-------------------------------------------------------------+
  |                       THE ARCHITECT                         |
  |                                                             |
  |  1. LEARN      Detects repo structure, refreshes            |
  |                ARCHITECT.md, captures git workspace state   |
  |                                                             |
  |  2. PLAN       Decomposes goal into numbered task files     |
  |                with dependency graph                        |
  |                                                             |
  |  3. EXECUTE    Runs tasks (sequentially or in parallel)     |
  |                via your AI coding CLI, streaming live       |
  |                4-signal completion detection — no           |
  |                hallucinated completions                     |
  |                                                             |
  |  4. RECOVER    Circuit breaker catches stuck agents         |
  |                Retries with model fallbacks                 |
  |                Auto-replans genuinely failing tasks         |
  |                                                             |
  |  5. REVIEW     Retrospective agent reads actual code        |
  |                Runs test suite, creates fix-up tasks        |
  |                Validation gate confirms cycle is clean      |
  |                                                             |
  |  6. REMEMBER   ARCHITECT.md stores durable intelligence     |
  |                Token ledger records costs                   |
  |                Every run builds on the last                 |
  |                                                             |
  +-------------------------------------------------------------+
      |
      v
  Results — code written, tests passing, tasks/SUMMARY.md summary
```

**The Architect never writes your application code.** Your AI coding CLI does. The Architect makes sure it finishes, doesn't get stuck, and doesn't hallucinate success.

---

## Features

### Supported Providers

The Architect wraps any of these AI coding CLIs — all share the same orchestration layer:

| Provider | Install |
|---|---|
| [OpenCode](https://opencode.ai) | `brew install opencode` or `npm i -g opencode-ai` |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | `npm install -g @anthropic-ai/claude-code` |
| [Codex CLI](https://developers.openai.com/codex/cli/) | `npm install -g @openai/codex` |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | `npm install -g @google/gemini-cli` |

When multiple are installed, The Architect detects them all and lets you choose. Lock a preference in `architect.toml` or via `ARCHITECT_PROVIDER`.

| Capability | OpenCode | Claude Code | Codex CLI | Gemini CLI |
|---|:---:|:---:|:---:|:---:|
| Planning and execution | ✅ | ✅ | ✅ | ✅ |
| Retry and circuit breaker | ✅ | ✅ | ✅ | ✅ |
| Retrospective review | ✅ | ✅ | ✅ | ✅ |
| Token usage tracking | ✅ | ❌ | ✅ | ✅ |
| Named execution agents | ✅ | ❌ | ❌ | ❌ |
| Free tier rotation | ✅ | ❌ | ❌ | ❌ |

---

### Planning and Task Management

| Feature | Description |
|---|---|
| **Goal-driven planning** | Describe what to build in plain English — the architect agent decomposes it into numbered tasks |
| **Goal templates** | Save reusable goals with `{variable}` placeholders; run with `architect template run` |
| **Context injection** | `--context PRD.md` or `--context design/` — any file or directory injected into planning |
| **Workspace context** | Git branch, uncommitted changes, and recent commits injected for continuity |
| **Task dependencies** | Declare `depends_on` per task; cycle detection; smart skipping when dependencies fail |
| **Dry-run mode** | `--dry-run` plans and shows cost estimate without executing — review before committing |
| **Scope control** | `simple` / `standard` / `complex` — controls task granularity |
| **Project intelligence** | Auto-detects repo type, languages, frameworks, components, dependency graph |
| **Pre-planning intelligence** | Deterministic scan + model-based `ARCHITECT.md` quality gate before every plan |

---

### Execution

| Feature | Description |
|---|---|
| **Sequential execution** | Tasks run in order by default — safe, predictable |
| **Parallel execution** | `max_parallel_tasks > 1` runs independent tasks concurrently via `asyncio.gather` |
| **Live streaming** | Provider output streamed directly to the terminal in real-time |
| **Multi-signal completion** | 4 corroborating signals — no single signal is trusted alone |
| **Stuck detection** | "I'm stuck" in output overrides any completion claim |
| **Retry with fallbacks** | Up to 30 retries with different models on each attempt |
| **Retry context** | Previous attempt summary injected — files written, errors found |
| **Inter-task reassessment** | Pending tasks updated after each completion to reflect new reality |
| **User feedback injection** | Write feedback to `.architect/feedback.json` — injected into the next task prompt |

---

### Failure Recovery

| Feature | Description |
|---|---|
| **Circuit breaker** | Detects no-progress, same-error patterns, and token decline across attempts |
| **Auto-replan** | Architect agent rewrites genuinely failing tasks when retries are exhausted |
| **Rate limit handling** | Detects HTTP 429/529 and text patterns — pauses without consuming retry slots |
| **Free mode rotation** | Rotates through zero-cost OpenRouter models mid-stream on rate limit |
| **Sleep/wake recovery** | Detects OS suspend gaps, kills stale subprocesses, retries with bonus attempts |
| **Idle-timeout protection** | Provider idle kills get bonus retries with cool-down — don't burn retry slots |

---

### Quality and Validation

| Feature | Description |
|---|---|
| **Retrospective review** | Separate reviewer agent examines code, runs tests, creates fix-up tasks |
| **Validation gate** | Deterministic check after each retrospective round — failed validation triggers another round |
| **File integrity defense** | Snapshot before edit, validate after write, restore on truncation |
| **Reviewer safety** | Reviewer forbidden from destructive recovery (`git reset`, `rm -rf`, etc.) |
| **Orphan task filtering** | Duplicate task prefixes from execution agents detected and filtered gracefully |

---

### Cost Tracking and Budgeting

| Feature | Description |
|---|---|
| **Token ledger** | Every run recorded to `.architect/token_ledger.json` with per-task breakdown |
| **Cost estimation** | `architect estimate` predicts cost from historical data (3-tier fallback) |
| **Token reporting** | `architect token-report` with date/model/task filtering |
| **Run history** | `architect history` with Rich table, TUI screen, and per-task view |
| **Per-hour budget** | `token_budget_per_hour` — rolling hourly cap with auto-pause |
| **Per-run budget** | `token_budget_per_run` — total cap for the entire run |
| **Budget context injection** | Agents know remaining budget and self-regulate effort |
| **Live cost display** | TUI Costs tab shows real-time session spend with per-model breakdown |

---

### Observability and Monitoring

| Feature | Description |
|---|---|
| **Full-screen TUI** | Textual TUI with Live / Progress / Diagnostics / Costs / Tasks tabs |
| **Live monitor** | `architect monitor` — TUI monitor from any terminal reading live state |
| **JSON monitor** | `architect monitor --json` — one-shot JSON snapshot for scripts |
| **Watch mode** | `architect monitor --watch` — continuous NDJSON polling for dashboards |
| **Run report** | `architect report` — view last run's summary from `tasks/SUMMARY.md` |
| **Diff viewer** | `architect diff` — per-task created/modified/deleted file tracking |
| **Circuit state** | `architect circuit` — view and reset per-task circuit breaker |
| **Task list** | `architect list` — all tasks with status, dependencies, and `--json` output |
| **Dependency graph** | `architect deps` — full dependency and reverse-dependency display |
| **Notifications** | Desktop notification + terminal bell on run completion or failure |

---

### Safety and Control

| Feature | Description |
|---|---|
| **Rollback** | `architect rollback` — restore files to pre-run state from captured baselines |
| **Lock file** | Only one run at a time; stale locks auto-cleaned; `architect cancel` for manual cleanup |
| **Premature exit guard** | Requires explicit `--plan` when all tasks are done |
| **Pending task guard** | Warns before starting new plan with unfinished tasks |
| **Doctor diagnostics** | `architect doctor` — static checks, `--project` health, `--live` provider probe |
| **Configuration presets** | Save and recall named config profiles with `architect preset` |
| **Session survival** | Infinite Loop and persistent runs survive terminal close — reconnect with `architect monitor` |

---

### Long-Running Autonomy

| Feature | Description |
|---|---|
| **Persistent mode** | 30 retries, 3 retrospective rounds — deeper recovery for unattended sessions |
| **Infinite Loop** | Keeps iterating the same goal hands-free after each successful cycle |
| **Loop safety** | Only advances after full cycle (plan → execute → review → validate) |
| **Loop diagnostics** | Persistent logs survive between iterations for failure analysis |
| **Detach** | Pause menu → Detach frees your terminal; worker continues headless |

---

## CLI Reference

### Main Command

```bash
# Start normally — fresh run, resume, or all-done guard depending on state
architect

# Force a new planning session
architect --plan

# Plan with explicit goal
architect --plan --goal "add OAuth login"

# Plan with context document
architect --plan --context PRD.md

# Dry run — plan and review without executing
architect --dry-run --goal "add auth"
architect --dry-run --goal "add auth" --json

# Resume from a specific task
architect --from T03

# Run a single task only
architect --only T05

# Persistent mode (30 retries, 3 retrospective rounds)
architect --persistent

# Free tier — rotate free OpenRouter models (OpenCode only)
architect --free

# Headless / CI mode — no interactive prompts
architect --headless --goal "fix mypy errors" --scope simple

# Standalone mode — bypass provider config entirely
architect --standalone openrouter/anthropic/claude-sonnet-4.5

# Use a specific project directory
architect --project /path/to/project
```

### Observability Commands

```bash
# ── Cost and History ─────────────────────────────────────────────────
architect estimate                              # predict cost before running
architect estimate --model claude-sonnet-4      # override model
architect history                               # past runs (Rich table)
architect history --tasks                       # per-task cost breakdown
architect history --since 2026-05-01            # date filter
architect history --tui                         # interactive TUI viewer
architect token-report                          # spending breakdown
architect token-report --tasks                  # per-task view
architect token-report --model claude-sonnet-4  # filter by model
architect report                                # last run's summary
architect report --json                         # machine-readable

# ── Workspace Changes ────────────────────────────────────────────────
architect diff                                  # per-task file changes
architect diff --task T03                       # specific task
architect diff --tui                            # interactive TUI viewer
architect rollback                              # restore pre-run state
architect rollback --task T03                   # rollback specific task
architect rollback --dry-run                    # preview without applying

# ── Task Management ──────────────────────────────────────────────────
architect list                                  # all tasks with status
architect list --tui                            # interactive TUI list
architect deps                                  # dependency graph
architect status                                # current run state
architect retry --task T03                      # retry a task
architect skip --task T03                       # mark task Done without running
architect reset                                 # reset PROGRESS.md

# ── Monitoring ───────────────────────────────────────────────────────
architect monitor                               # live TUI monitor
architect monitor --json                        # one-shot JSON snapshot
architect monitor --watch                       # continuous NDJSON polling
architect monitor --watch --interval 10         # custom interval
architect circuit                               # circuit breaker state
architect circuit --json                        # structured JSON output
architect circuit --reset T04                   # reset a task's circuit
architect logs                                  # execution logs
architect logs --task T01                       # specific task logs
```

### Goal Templates

```bash
architect template create "api" \
  --goal "Build REST API for {service} using {framework}" \
  --description "Scaffold a new API service"

architect template list                         # list all templates
architect template show "api"                   # view details
architect template run "api" --var service=payments --var framework=FastAPI
architect template delete "api"                 # remove a template
```

### Configuration Presets

```bash
architect preset create "sprint" \
  --description "Aggressive sprint settings" \
  --field max_retries=10 --field pause_between_tasks=5

architect preset list                           # list all presets
architect preset show "sprint"                  # view details
architect preset apply "sprint"                 # apply to current config
architect preset delete "sprint"                # remove a preset
```

### Health Diagnostics

```bash
architect doctor                                # static provider checks
architect doctor --project                      # project health checks
architect doctor --project --json               # structured JSON output
architect doctor --live                         # live provider connectivity
architect doctor --live --live-timeout 60       # custom timeout
```

### Utility Commands

```bash
architect config                                # show current configuration
architect config --set max_retries=5            # update a setting
architect config --tui                          # interactive config viewer
architect cancel                                # remove stale lock / stop session
architect init                                  # create AGENTS.md and architect.toml
architect version                               # show version and build number
```

### Flags That Support `--json`

Most inspection commands support `--json` for machine-readable output:

```
architect list --json
architect status --json
architect circuit --json
architect deps --json
architect history --json
architect token-report --json
architect report --json
architect diff --json
architect rollback --json
architect doctor --json
architect monitor --json
architect template list --json
architect preset list --json
architect estimate --json
architect --dry-run --json
```

### Environment Variables

```bash
ARCHITECT_HEADLESS=true architect                    # headless mode
ARCHITECT_GOAL="add dark mode" architect             # set goal
ARCHITECT_SCOPE=standard architect                   # set scope
ARCHITECT_CONTEXT="/path/to/PRD.md" architect        # set context
ARCHITECT_PROVIDER=codex architect                   # set provider
ARCHITECT_ARCHITECT_MODEL=claude-opus-4.5 architect  # set architect model
ARCHITECT_EXECUTION_MODEL=gemini-2.5-pro architect   # set execution model
```

---

## Configuration

Zero-config by default. Create `architect.toml` in your project root:

```toml
[architect]
# ── Provider ──────────────────────────────────────────────────────────
provider = "auto"                    # "auto" | "opencode" | "codex" | "claude-code" | "gemini-cli"
execution_agent = ""                 # Agent name from opencode.json (empty = default)
standalone_mode = ""                 # Bypass provider config, use this model directly

# ── Execution ─────────────────────────────────────────────────────────
max_parallel_tasks = 1               # Concurrent task limit (1 = sequential)

# ── Retry ─────────────────────────────────────────────────────────────
max_retries = 3                      # Maximum retry attempts per task
retry_pause = 30                     # Seconds between retries
pause_between_tasks = 10             # Seconds between tasks
retry_model_2 = ""                   # Fallback model for attempt 2
retry_model_3 = ""                   # Fallback model for attempt 3
carry_context = true                 # Inject previous attempt summary on retry
retry_prompt_mode = "focused"        # "focused" (structured) or "same" (identical)

# ── Retrospective ─────────────────────────────────────────────────────
retrospective_rounds = 1             # Review rounds after execution (0 = disabled)

# ── Modes ─────────────────────────────────────────────────────────────
free_mode = false                    # Rotate free OpenRouter models
persistent = false                   # 30 retries, 3 retrospective rounds
integrity = true                     # Snapshot existing files before edits
force_reassessment = true            # Reassess pending tasks after every task

# ── Circuit Breaker ───────────────────────────────────────────────────
circuit_no_progress_threshold = 3    # Zero-file-writes attempts before trip (0=off)
circuit_same_error_threshold = 3     # Same-error attempts before trip (0=off)
circuit_token_decline_pct = 60      # Token decline % to trip (0=off)
circuit_cooldown_minutes = 30        # Wait before HALF_OPEN retry
circuit_enable_replan = true         # Allow REPLAN recovery action

# ── Cooldown and Budget ──────────────────────────────────────────────
cooldown_detection = true            # Detect and wait on provider rate limits
token_budget_per_hour = 0           # Max tokens per rolling hour (0 = disabled)
token_budget_per_run = 0            # Max tokens for entire run (0 = disabled)

# ── Notifications ─────────────────────────────────────────────────────
notify_on_complete = true            # Desktop notification on success
notify_on_fail = true               # Desktop notification on failure

# ── Token Ledger ──────────────────────────────────────────────────────
token_ledger = true                  # Record run costs to .architect/token_ledger.json
```

### Full Configuration Reference

| Option | Default | Description |
|---|---|---|
| `provider` | `auto` | AI CLI provider |
| `execution_agent` | `""` | Agent name for task execution (OpenCode only) |
| `standalone_mode` | `""` | Bypass provider config, use this model |
| `max_parallel_tasks` | `1` | Maximum concurrent tasks (1 = sequential) |
| `max_retries` | `3` | Max retry attempts per task |
| `retry_pause` | `30` | Seconds between retries |
| `pause_between_tasks` | `10` | Seconds between tasks |
| `retry_model_2` | `""` | Fallback model for attempt 2 |
| `retry_model_3` | `""` | Fallback model for attempt 3 |
| `carry_context` | `true` | Inject previous attempt context on retry |
| `retry_prompt_mode` | `focused` | `focused` or `same` |
| `retrospective_rounds` | `1` | Review rounds (0 = off) |
| `free_mode` | `false` | Rotate free OpenRouter models |
| `persistent` | `false` | 30 retries, 3 retrospective rounds |
| `integrity` | `true` | Snapshot existing files before edits |
| `force_reassessment` | `true` | Reassess pending tasks after every task |
| `circuit_no_progress_threshold` | `3` | No-progress trips (0 = off) |
| `circuit_same_error_threshold` | `3` | Same-error trips (0 = off) |
| `circuit_token_decline_pct` | `60` | Token decline % to trip (0 = off) |
| `circuit_cooldown_minutes` | `30` | Wait before half-open retry |
| `circuit_enable_replan` | `true` | Allow targeted task replan |
| `cooldown_detection` | `true` | Detect and wait on rate limits |
| `token_budget_per_hour` | `0` | Max tokens per rolling hour (0 = off) |
| `token_budget_per_run` | `0` | Max tokens for entire run (0 = off) |
| `notify_on_complete` | `true` | Desktop notification on success |
| `notify_on_fail` | `true` | Desktop notification on failure |
| `token_ledger` | `true` | Record run costs to ledger |

---

## TUI (default on TTY)

When stdout is a TTY with colour support, `architect` opens a full-screen Textual TUI. It owns the screen from mode selection through planning, execution, retrospective, and reassessment.

### Execution Screen Tabs

| Tab | Key | Shows |
|---|---|---|
| **Live** | `l` | Provider stream, task banners, attempt lines, completion markers |
| **Progress** | `p` | Current task state and full task list |
| **Diagnostics** | `d` | Retries, model switches, circuit events, cooldowns, replans |
| **Costs** | `c` | Real-time session spend, per-model breakdown, budget progress |
| **Tasks** | `g` | All concurrent tasks (parallel execution) with per-task status and tokens |
| **Settings** | — | Provider, model, agent, and feature flags for this run |

### Other TUI Screens

- **Pre-run** — Goal / Provider / Models / Options tabs before planning (includes template selector)
- **Wait overlay** — Animated spinner during planning, retrospective, and reassessment
- **Inspection** — `architect list --tui`, `status --tui`, `logs --tui`, `circuit --tui`, `monitor`, `config --tui`, `diff --tui`, `history --tui`

### Key Bindings

- `l` / `p` / `d` / `g` / `c` — switch execution tabs
- `Esc` — pause menu (Detach, Exit)
- `q` / `Ctrl+C` — quit

### Opt Out

```bash
architect --no-tui               # explicit opt-out
NO_COLOR=1 architect             # honour NO_COLOR
TERM=dumb architect              # minimal-terminal environments
architect --headless             # unattended / CI runs
architect > run.log 2>&1         # piped stdout
```

### Session Survival

Infinite Loop and persistent runs spawn the worker as a non-daemon thread with `SIGHUP` handling. If the terminal closes or SSH drops, the TUI exits cleanly but the worker continues headless. Reconnect from any terminal:

```bash
architect monitor
```

---

## Run Modes

### Persistent Mode

Built for long, unattended sessions with deeper recovery:

- `max_retries = 30`
- `retrospective_rounds = 3`

```bash
architect --persistent
```

### Infinite Loop

Keeps iterating the same goal hands-free after each successful cycle. Toggle in the TUI Options tab. Stop with `Ctrl+C`, the pause menu, or `architect cancel`.

### Free Mode

Rotates through zero-cost OpenRouter models on rate limit (OpenCode only):

```bash
architect --free
```

### Standalone Mode

Bypasses provider config entirely — forces a single model for all operations:

```bash
architect --standalone openrouter/anthropic/claude-sonnet-4.5
```

---

## Context Injection

Point The Architect at any document — it extracts the goal and injects the content into planning:

```bash
architect --plan --context PRD.md
architect --plan --context design/
architect --plan --context PRD.md --context design/ --context SPEC.md
```

Supported file types: `.md`, `.txt`, `.rst`, `.json`, `.yaml`, `.toml`, `.py`, `.ts`, `.go`, `.rs`, `.java`, `.sql`, `.graphql`, and more.

---

## Files Created in Your Project

```text
your-project/
├── tasks/
│   ├── T01_init.md
│   ├── T02_feature.md
│   ├── T02A_feature_part1.md      # split sub-task (reassessment)
│   ├── T03R1_api_fix.md           # retrospective fix-up for T03
│   ├── PROGRESS.md                # Current task state
│   ├── INSTRUCTIONS.md            # Project context for the agent
│   ├── SUMMARY.md                 # Final summary for the current run
│   └── archive/                   # Previous runs preserved here
│       └── 2026-04-12_143000/
│           ├── T01_old.md
│           ├── INSTRUCTIONS.md
│           └── SUMMARY.md
├── .architect/
│   ├── architect.json             # Planning config (agents for planning roles)
│   ├── prompts/                   # Agent prompts (written from resources)
│   │   ├── architect.md
│   │   ├── intelligence.md
│   │   ├── reviewer.md
│   │   └── execution.md
│   ├── logs/                      # Execution transcripts per task and attempt
│   ├── baselines/                 # Workspace baselines for diff and rollback
│   ├── circuit.json               # Circuit breaker state (persists across restarts)
│   ├── feedback.json              # User feedback (consumed once per task)
│   ├── intelligence.json          # Cached project intelligence data
│   ├── monitor_state.json         # Live state (read by `architect monitor`)
│   ├── presets.json               # Saved configuration presets
│   ├── runner.lock                # Prevents concurrent runs
│   ├── templates.json             # Saved goal templates
│   └── token_ledger.json          # Cross-run token and cost ledger
├── ARCHITECT.md                   # Durable project intelligence (commit to git)
└── architect.toml                 # Your configuration (optional)
```

---

## Versioning

```text
v1.2.15 (build 10549)
  -----    -----------
   |           |
   |           +-- Global build counter
   |               Increments with every completed task/change
   |               Never resets. Always 5 digits.
   |
   +------------ Semantic version
                 Increments on human-tagged releases only
```

Major version build floors: `v1.x.x` → 10000+, `v2.0.0` → 20000+, `v3.0.0` → 30000+.

---

## Error Handling

| Scenario | Handling |
|---|---|
| No provider installed | Detects and shows install instructions |
| Provider not configured | Shows setup guidance |
| Concurrent runs | Lock file prevents multiple instances |
| Stale lock file | Dead PID detected and removed automatically |
| Interrupted run | Ctrl+C triggers clean shutdown, lock released |
| Malformed PROGRESS.md | Safe defaults, never crashes |
| Agent stuck | Circuit breaker detects and recovers |
| Rate limit hit | Cooldown wait or free model rotation |
| All retries exhausted | Targeted task replan attempted |
| Sleep/wake gap | Stale subprocess killed, bonus retry granted |
| Provider idle timeout | Bonus retries with cool-down, no slot burned |

---

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a PR.

```bash
git clone https://github.com/inetanel/the-architect
cd the-architect
pip install -e ".[dev]"
pytest tests/
```

Every PR must increment the build number in `version.py` — including documentation and maintenance changes.

---

## License

Apache License 2.0 — see [LICENSE](LICENSE) for full terms.
See [NOTICE](NOTICE) for attribution requirements and genesis fingerprint.

Copyright 2026 [Netanel Eliav](https://inetanel.com) · [inetanel@me.com](mailto:inetanel@me.com)

Any distribution or fork must retain the [NOTICE](NOTICE) file in full.
The canonical repository is [github.com/inetanel/the-architect](https://github.com/inetanel/the-architect).

---

<div align="center">

Built by [Netanel Eliav](https://inetanel.com)

[inetanel.com/projects/the-architect](https://inetanel.com/projects/the-architect)

*"Any sufficiently advanced automation is indistinguishable from having a really good senior engineer on call 24/7."*

</div>
