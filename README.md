<div align="center">

<img src="https://raw.githubusercontent.com/inetanel/the-architect/main/assets/architect.svg" alt="The Architect" width="100%">

**An autonomous development lifecycle layer for agentic AI coding tools.**  
Describe a goal. Walk away. Come back to results.

[![PyPI version](https://img.shields.io/pypi/v/the-architect?color=blue&label=PyPI&v=1)](https://pypi.org/project/the-architect/)
[![Python](https://img.shields.io/pypi/pyversions/the-architect?v=1)](https://pypi.org/project/the-architect/)
[![CI](https://github.com/inetanel/the-architect/actions/workflows/ci.yml/badge.svg)](https://github.com/inetanel/the-architect/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/inetanel/the-architect?style=social)](https://github.com/inetanel/the-architect)

[Website](https://inetanel.com/projects/the-architect) · [Quickstart](#quickstart) · [Features](#features) · [CLI Reference](#cli-reference) · [Configuration](#configuration) · [Contributing](CONTRIBUTING.md)

</div>

---

## Why This Exists

AI coding agents are powerful. But left alone, they drift — they lose context, repeat mistakes, hallucinate completion, and have no memory of what they did yesterday.

**The Architect solves the orchestration problem, not the coding problem.**

It wraps your agentic AI coding tool and adds everything it lacks out of the box:

| Without The Architect | With The Architect |
|---|---|
| You manage the task list manually | Tasks planned automatically from your goal |
| Agent loses context between sessions | Persistent memory via `ARCHITECT.md` |
| Setup issues discovered mid-run | `architect doctor` checks all providers before you start |
| No recovery when agent gets stuck | Circuit breaker + smart retry + model fallback |
| You babysit every run | Fully unattended — fire and forget |
| No audit trail of what changed | Build counter tracks every completed task/change |
| Agent hallucinates completion | Multi-signal completion detection |
| You re-explain the project every time | Project structure auto-detected and injected |
| Repo knowledge starts shallow | Pre-planning intelligence learns and repairs `ARCHITECT.md` |
| One shot — no quality gate | Retrospective reviewer + validation gate creates fix-up tasks until clean |
| Developer plans every task manually | AI planner decomposes the goal autonomously |
| One run, then you start over | **Infinite Loop** — keep iterating the same goal hands-free until you stop it |

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
│  12 passed                          │  v1.0.0 (build 10042)           │
│  <promise>T01_COMPLETE</promise>    │                                 │
│                                     │                                 │
└─────────────────────────────────────┴─────────────────────────────────┘
```

> **Demo GIF coming soon.** [Contribute one?](CONTRIBUTING.md)

---

## Quickstart

**Prerequisites:** Python 3.11+ and at least one supported agentic AI coding tool installed and configured.

```bash
# Install
pip install the-architect

# Go to your project
cd your-project

# Start The Architect
architect
```

On a fresh project, `architect` opens the configure flow and starts planning.

If pending tasks already exist, `architect` resumes and continues executing them.

If all tasks are complete, The Architect shows the menu unless you explicitly start a new plan with `--plan`.

### Common flows

```bash
# Fresh planning flow or resume pending tasks
architect

# Check setup before a run
architect doctor

# Force a new plan sequence
architect --plan

# Force a new plan with a specific goal
architect --plan --goal "add Stripe payment integration"

# Headless / CI mode
architect --headless --goal "fix mypy errors" --scope simple
```

That is it. The Architect plans, executes, retries, reviews, and reports — unattended.

---

## How It Works

```text
  Your Goal
      |
      v
  +-------------------------------------------------------------+
  |                       THE ARCHITECT                         |
  |                                                             |
  |  1. LEARN      Detects repo structure and refreshes         |
  |                ARCHITECT.md before planning                 |
  |                                                             |
  |  2. PLAN       Decomposes goal into numbered task files     |
  |                Injects persistent project intelligence      |
  |                                                             |
  |  3. EXECUTE    Runs each task via your AI coding tool       |
  |                Streams output live to terminal              |
  |                Tracks completion with 4 corroborating       |
  |                signals — no hallucinated completions        |
  |                                                             |
  |  4. RECOVER    Circuit breaker catches stuck agents         |
  |                Retries with model fallbacks                 |
  |                Auto-replans genuinely failing tasks         |
  |                                                             |
  |  5. REVIEW     Retrospective agent reads the actual code    |
  |                Runs your test suite                         |
  |                Creates fix-up tasks if issues found         |
  |                                                             |
  |  6. REMEMBER   ARCHITECT.md stores durable project          |
  |                intelligence across sessions                 |
  |                Every run builds on the last                 |
  |                                                             |
  +-------------------------------------------------------------+
      |
      v
  Results — code written, tests passing, tasks/SUMMARY.md summary
```

**The Architect never writes your application code.** Your AI coding tool does. The Architect makes sure it actually finishes.

---

## Features

### Supported Providers

The Architect works with the major AI coding CLIs. Install any one of them and The Architect wraps it:

| Provider | Install |
|---|---|
| [OpenCode](https://opencode.ai) | `brew install opencode` or `npm i -g opencode-ai` |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | `npm install -g @anthropic-ai/claude-code` |
| [Codex CLI](https://developers.openai.com/codex/cli/) | `npm install -g @openai/codex` |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | `npm install -g @google/gemini-cli` |

When multiple providers are installed, The Architect detects them all and lets you choose. Set a preference in `architect.toml` or via `ARCHITECT_PROVIDER` env var to skip the prompt.

| Capability | OpenCode | Claude Code | Codex CLI | Gemini CLI |
|---|:---:|:---:|:---:|:---:|
| Planning and execution | ✅ | ✅ | ✅ | ✅ |
| Retry and circuit breaker | ✅ | ✅ | ✅ | ✅ |
| Retrospective review | ✅ | ✅ | ✅ | ✅ |
| Token usage tracking | ✅ | ❌ | ✅ | ✅ |
| Named execution agents | ✅ | ✅ | ✅ | ❌ |
| Free tier model rotation | ✅ | ❌ | ❌ | ❌ |

---

### What The Architect Adds

Everything below is what you get on top of your AI coding CLI — none of it exists if you run the CLI directly.

#### Planning

| | Raw CLI | With The Architect |
|---|---|---|
| Task creation | You write task files manually | Goal → numbered task files, automatically |
| Scope control | Fixed | `simple` / `standard` / `complex` — controls task granularity |
| Project awareness | None | Repo type, languages, frameworks, components, dependency graph auto-detected |
| Pre-planning memory | None | Fast deterministic scan plus model-based `intelligence` pass when `ARCHITECT.md` is shallow |
| Context injection | Paste manually | `--context PRD.md` or `--context design/` — any file or directory injected into planning |
| Memory across sessions | None | `ARCHITECT.md` stores durable project intelligence |

#### Execution

| | Raw CLI | With The Architect |
|---|---|---|
| Retries | None — one shot | Up to 30 retries with configurable pause between attempts |
| Model fallbacks | None | Different model on attempt 2, different model on attempt 3 |
| Retry context | Agent starts blind | Previous attempt summary injected — files written, errors found |
| Unattended | You watch the terminal | Fully autonomous — fire and forget |
| Resume | Manual | `--from T03` or automatic resume on next run |
| Targeted runs | All or nothing | `--only T05` runs a single task |
| Inter-task pause | None | Configurable pause between tasks |

#### Failure Recovery

| | Raw CLI | With The Architect |
|---|---|---|
| Stuck agent | You notice eventually | Circuit breaker detects no-progress, same-error pattern, token decline |
| Failing task | You debug and rerun | Auto-replan: architect agent rewrites the failing task and retries |
| Rate limit | Run dies | Cooldown detection: pauses the run, waits, resumes automatically |
| Free model rotation | Manual | Free mode: rotates through zero-cost OpenRouter models mid-stream |
| Provider cooldown | Run dies | Detects HTTP 429/529, waits the suggested reset time, retries |

#### Completion Trust

| | Raw CLI | With The Architect |
|---|---|---|
| Completion signal | Exit code 0 | 4-signal corroboration: promise tag + PROGRESS.md + exit code + output analysis |
| False completion | Trusted | Stuck detection — "I'm stuck" in output overrides any completion claim |
| Hallucinated success | No defense | Multi-signal requirement — no single signal is ever trusted alone |

#### Quality

| | Raw CLI | With The Architect |
|---|---|---|
| Code review | Manual | Retrospective reviewer runs after execution, reads actual code and tests |
| Fix-up tasks | Manual | Reviewer creates R-prefixed fix-up tasks; they execute through the same pipeline |
| Validation gate | None | Deterministic validation after each retrospective round; failures trigger another round and are recorded in `tasks/PROGRESS.md` and `tasks/SUMMARY.md` |
| Cross-task drift | Silent | Inter-task reassessment updates pending tasks when completed work changes interfaces |
| File corruption | Silent | Integrity defense: snapshot before edit, validate after write, restore on truncation |

#### Long-Running Autonomy

| | Raw CLI | With The Architect |
|---|---|---|
| Long unattended sessions | One run, then you start over | Persistent mode (30 retries, 3 retrospective rounds) for deeper recovery |
| Repeating the same goal | Manual rerun every time | **Infinite Loop** — keeps planning, executing, reviewing, and validating the same goal with the same settings until you stop it |
| Loop safety | Easy to spiral | Loop only advances after a successful planning → execution → retrospective → validation cycle; reviewer is forbidden from destructive git/file recovery |
| Loop diagnostics | None | Lifecycle traces in `.architect/logs/the_architect.log` and `.architect/logs/architect_runtime.log` survive between iterations |

#### Observability

| | Raw CLI | With The Architect |
|---|---|---|
| UI | Raw terminal scroll | Full-screen Textual TUI with Live / Progress / Diagnostics / Settings tabs |
| Live monitoring | None | `architect monitor` — TUI monitor screen reads live state from `.architect/monitor_state.json` |
| Run summary | None | `tasks/SUMMARY.md` — tasks, attempts, models, tokens, duration, retrospective rounds |
| Task history | None | Every run archived to `tasks/archive/YYYY-MM-DD_HHMMSS/` |
| Logs | Wherever the CLI writes | Per-task logs in `.architect/logs/`, per-attempt, per-reassessment |
| Circuit state | None | `architect circuit` — view and reset per-task circuit breaker state |
| Build tracking | None | `__build__` counter increments on every completed task/change — full effort traceability |

#### Safety

| | Raw CLI | With The Architect |
|---|---|---|
| Concurrent runs | Two runs corrupt each other | Lock file — only one run at a time, stale locks auto-cleaned |
| Re-planning guard | Easy to re-plan a finished project | Premature exit guard — requires explicit `--plan` when all tasks are done |
| Pending task guard | New plan silently discards old work | Warns before overwriting unfinished tasks |
| API cost | Can spiral | Token budget — configurable hourly spend cap |

#### Developer Experience

| | Raw CLI | With The Architect |
|---|---|---|
| Setup | Configure CLI manually | `architect init` — creates `AGENTS.md` and `architect.toml` |
| Config | Edit files manually | `architect config --set key=value` |
| Pre-flight diagnostics | Discover issues mid-run | `architect doctor` checks every supported provider and project health before execution; only runnable providers appear in provider selection |
| CI/headless | Manual scripting | `--headless` + env vars, no interactive prompts |
| Self-update | Manual `pip install` | Checks PyPI on startup, one keypress to update and re-exec |
| Provider lock-in | One CLI | Switch provider per run, or lock one in config |

---

## CLI Reference

### Main Commands

```bash
# Start normally (fresh run, resume, or all-done guard depending on project state)
architect

# Plan a new goal explicitly (interactive)
architect --plan

# Plan with flags (non-interactive)
architect --plan --goal "add OAuth login" --scope standard
architect --plan --context PRD.md
architect --plan --context design/ --architect-model openrouter/anthropic/claude-opus-4.5

# Resume from a specific task
architect --from T03

# Run a single task only
architect --only T05

# Persistent mode (30 retries, 3 retrospective rounds)
architect --persistent

# Free tier — rotate free OpenRouter models on rate limit (OpenCode only)
architect --free

# Headless / CI mode — no interactive prompts
architect --headless --goal "fix mypy errors" --scope simple

# Check project and provider setup before running
architect doctor

# Standalone mode — bypass provider config, use this model directly
architect --standalone openrouter/anthropic/claude-sonnet-4.5

# Use a specific project directory
architect --project /path/to/project
```

### Subcommands

```bash
# List all tasks and their status
architect list

# Show current run state, circuit breaker, and token budget
architect status

# Retry a specific task (resets Done status and re-runs)
architect retry --task T03

# Mark a task as Done without running it
architect skip --task T03

# Reset PROGRESS.md to initial state
architect reset

# Remove a stale lock file / stop a running session
architect cancel

# Initialise a project (creates AGENTS.md and architect.toml)
architect init

# Run pre-flight diagnostics for project and provider setup
architect doctor

# Show or update configuration
architect config
architect config --set max_retries=5
architect config --set retry_model_2="openrouter/google/gemini-2.5-pro"

# Show and reset circuit breaker state
architect circuit
architect circuit --reset T04

# Show execution logs
architect logs
architect logs --task T01
architect logs --task T01 --tail 100

# Open the live monitor screen (reads .architect/monitor_state.json)
architect monitor

# Show version and build number
architect version
architect --version
```

### Environment Variables

All planning flags can be set via environment variables — useful for CI and headless runs:

| Variable | Equivalent Flag | Example |
|---|---|---|
| `ARCHITECT_HEADLESS` | `--headless` | `true` |
| `ARCHITECT_GOAL` | `--goal` | `"add dark mode"` |
| `ARCHITECT_SCOPE` | `--scope` | `standard` |
| `ARCHITECT_CONTEXT` | `--context` | `/path/to/spec.md` |
| `ARCHITECT_PROVIDER` | Provider preference | `codex`, `gemini-cli` |
| `ARCHITECT_ARCHITECT_MODEL` | `--architect-model` | `openrouter/anthropic/claude-opus-4.5` |
| `ARCHITECT_EXECUTION_MODEL` | `--execution-model` | `openrouter/google/gemini-2.5-pro` |

---

## Context Injection

Point The Architect at any document and it will extract the goal and inject the content into planning:

```bash
# From a PRD
architect --plan --context PRD.md

# From a directory of design docs
architect --plan --context design/

# Multiple sources
architect --plan --context PRD.md --context design/ --context SPEC.md

# CI / headless — colon-separated on Unix
ARCHITECT_CONTEXT="/path/to/PRD.md:/path/to/design/" architect --plan --headless
```

Supported file types: `.md`, `.txt`, `.rst`, `.json`, `.yaml`, `.toml`, `.py`, `.ts`, `.go`, `.rs`, `.java`, `.sql`, `.graphql`, and more.

---

## Configuration

Zero-config by default. Create `architect.toml` in your project root to customise:

```toml
[architect]
# Provider
provider = "auto"                    # "auto" | "opencode" | "codex" | "claude-code" | "gemini-cli"

# Retry
max_retries = 3
retry_pause = 30                     # seconds between retries
pause_between_tasks = 10
retry_model_2 = ""                   # fallback model for attempt 2
retry_model_3 = ""                   # fallback model for attempt 3
carry_context = true                 # inject previous attempt summary on retry
retry_prompt_mode = "focused"        # "focused" or "same"

# Retrospective
retrospective_rounds = 1             # 0 = disabled

# Modes
free_mode = false
persistent = false
integrity = true                     # snapshot existing files before edits (default: true)
force_reassessment = true            # reassess pending tasks after every task
standalone_mode = ""                 # bypass provider config, use this model directly

# Circuit breaker
circuit_no_progress_threshold = 3
circuit_same_error_threshold = 3
circuit_token_decline_pct = 60
circuit_cooldown_minutes = 30
circuit_enable_replan = true

# Cost control
cooldown_detection = true
token_budget_per_hour = 0            # 0 = unlimited
```

### Full Configuration Reference

| Option | Default | Description |
|---|---|---|
| `provider` | `auto` | AI CLI provider — `auto`, `opencode`, `codex`, `claude-code`, or `gemini-cli` |
| `max_retries` | `3` | Max retry attempts per task |
| `retry_pause` | `30` | Seconds between retries |
| `pause_between_tasks` | `10` | Seconds between tasks |
| `retry_model_2` | `""` | Fallback model for attempt 2 |
| `retry_model_3` | `""` | Fallback model for attempt 3 |
| `carry_context` | `true` | Inject previous attempt context on retry |
| `retry_prompt_mode` | `focused` | `focused` or `same` |
| `retrospective_rounds` | `1` | Review rounds after execution (0 = off) |
| `free_mode` | `false` | Rotate free OpenRouter models |
| `persistent` | `false` | 30 retries, 3 retrospective rounds |
| `integrity` | `true` | Snapshot existing files before edits (`architect_eval_*`) |
| `force_reassessment` | `true` | Reassess pending tasks after every task; when false, only failed/downstream-impact tasks trigger reassessment |
| `standalone_mode` | `""` | Bypass provider config, use this model for all operations |
| `circuit_no_progress_threshold` | `3` | No-progress trips before circuit opens |
| `circuit_same_error_threshold` | `3` | Same-error trips before circuit opens |
| `circuit_token_decline_pct` | `60` | Token decline % to trip circuit |
| `circuit_cooldown_minutes` | `30` | Wait before half-open retry |
| `circuit_enable_replan` | `true` | Allow targeted task replan on exhaustion |
| `cooldown_detection` | `true` | Detect and wait on provider rate limits |
| `token_budget_per_hour` | `0` | Max tokens per rolling hour (0 = unlimited) |

---

## TUI (default)

When stdout is a TTY and colour is supported, `architect` opens a Textual TUI in the current terminal. The TUI owns the screen from mode selection through planning, execution, retrospective review, and reassessment — no separate tmux pane, no orphaned spinners.

Screens and what they show:

- **Execution** — tabbed viewport
  - **Live** — provider stream, task-start banners, attempt lines, done/failed markers
  - **Progress** — current task state and task list
  - **Diagnostics** — retries, model switches, circuit events, cooldowns, and replans
  - **Settings** — provider, model, agent, and feature flags used for the run
- **Wait screen overlay** — animated spinner, title, detail block, log tail. Pushed onto the running app for planning, retrospective rounds, and between-task reassessment.
- **Mode selection / Resume** — provider/model choices, free tier, persistent mode, integrity defense, force reassessment, and token budget.
- **Inspection** — `architect list --tui`, `architect status --tui`, `architect logs --tui`, `architect circuit --tui`, `architect monitor`, `architect config --tui`.

Key bindings inside the execution screen: `l` / `p` / `d` / `g` switch tabs, `q` or `Ctrl+C` quit.

Opt out of the TUI when you need plain output:

```bash
architect --no-tui               # explicit opt-out
NO_COLOR=1 architect             # globally honour NO_COLOR
TERM=dumb architect              # minimal-terminal environments
architect --headless             # unattended runs (CI, cron)
architect > run.log 2>&1         # piped / redirected stdout
```

### Surviving SSH disconnect / terminal close

Infinite Loop (`--infinite-loop`) and persistent mode (`--persistent`) runs spawn the worker as a **non-daemon thread** and install a `SIGHUP` handler. If your terminal closes or SSH drops, the TUI exits cleanly but the worker keeps running headless, writing all output to `.architect/logs/`. Reconnect from any terminal:

```bash
architect monitor
```

The monitor screen reads `.architect/monitor_state.json` live — no tmux required.

You can also wrap the run in your own tmux/screen if you prefer to manage the session yourself:

```bash
tmux new -s arch 'architect --infinite-loop'
# later, from any terminal:
tmux attach -t arch
```

---

## Run Modes

### Persistent Mode

Persistent mode is built for long, unattended sessions where you want The Architect to keep trying until the work is genuinely complete:

- `max_retries = 30`
- `retrospective_rounds = 3`

Enable it any of these ways:

```bash
architect --persistent                          # CLI flag
ARCHITECT_PERSISTENT=true architect             # environment variable
```

```toml
[architect]
persistent = true
```

Or toggle **Persistent** in the TUI Options tab before starting a run.

### Infinite Loop

Infinite Loop tells The Architect to keep iterating the same goal with the same provider, model, scope, and feature flags after each successful planning → execution → retrospective → validation cycle. The next iteration starts automatically and shows the planning screen, so it always feels like a fresh manual run.

Use it when you want the agent to keep working a long-running goal hands-free:

- generating a series of similar artifacts
- running unattended improvement passes against the same brief
- using the goal as a smoke / heartbeat task while you monitor the system

**Enabling Infinite Loop**

Open the **Options** tab on the pre-run / resume screen and toggle **Infinite Loop**. The TUI shows a confirmation warning before enabling it, because the loop will keep running until you stop it.

**Stopping Infinite Loop**

- Press `Ctrl+C` inside the TUI.
- Use the pause menu and choose Stop.
- From another terminal: `architect cancel`.

**What carries over between iterations**

- Original goal text (or `## Goal Summary` from `tasks/INSTRUCTIONS.md` when starting from existing tasks)
- Provider, model, agent, scope, persistent / free / integrity / force-reassessment flags
- Token budget and circuit-breaker configuration

Each iteration starts task numbering at `T01` and writes a fresh `tasks/PROGRESS.md`, so successive runs do not pile up on top of each other. Previous iterations are preserved under `tasks/archive/YYYY-MM-DD_HHMMSS/`.

**Loop safety**

- Infinite Loop only advances after a fully successful cycle. A failing task, failed retrospective fix-up, or failed validation gate stops the loop.
- Without Persistent mode, Infinite Loop automatically uses at least 2 retrospective rounds so a failed validation can trigger one recovery retrospective.
- The retrospective reviewer is not allowed to issue destructive recovery (`git checkout`, `git reset`, `git restore`, `git clean`, `rm -rf`, etc.); fix-up tasks containing those instructions are refused before execution.
- Validation results — passed/failed, reason, and unresolved tasks — are written to `tasks/PROGRESS.md` (`## Cycle Validation`) and `tasks/SUMMARY.md` (`### Validation Details`).

**Diagnostics**

Two persistent log files capture loop and TUI lifecycle events and survive between iterations:

- `.architect/logs/the_architect.log`
- `.architect/logs/architect_runtime.log`

If a run ever exits unexpectedly mid-loop, those logs show exactly which iteration and phase it stopped in.

### Free Mode

Free mode rotates through zero-cost OpenRouter models when rate limits hit, so a session with no budget can keep running without manual intervention. Currently OpenCode-only.

```bash
architect --free
```

```toml
[architect]
free_mode = true
```

### Standalone Mode

Standalone mode bypasses the provider's own configuration entirely and forces a single model for planning, execution, and retrospective. Useful for CI runs that must be deterministic or for quickly trying a model without reconfiguring the provider.

```bash
architect --standalone openrouter/anthropic/claude-sonnet-4.5
```

---

## Live Monitoring

`architect monitor` opens the TUI monitor screen from any terminal. It reads `.architect/monitor_state.json` which the runner updates after every significant event (task start/done/failed, attempt start, circuit state change, cooldown, model rotation, replan):

```text
┌─────────────────────────────────────────────────────┐
│  THE ARCHITECT  ·  my-project  ·  RUNNING           │
├─────────────────────────────────────────────────────┤
│  TASKS                                              │
│  ✓ T01 Setup           (done)                       │
│  ● T02 Build API       (running)  attempt 1/3       │
│  ○ T03 Frontend        (pending)                    │
├─────────────────────────────────────────────────────┤
│  Circuit: CLOSED  ·  Tokens: 48,230  ·  claude-s…  │
└─────────────────────────────────────────────────────┘
```

Works from any terminal — useful after detaching from a persistent or Infinite Loop run.

---

## Files Created in Your Project

```text
your-project/
├── tasks/
│   ├── T01_init.md
│   ├── T02_feature.md
│   ├── PROGRESS.md         # Current task state
│   ├── INSTRUCTIONS.md     # Project context for the agent
│   ├── SUMMARY.md          # Final summary for the current task package
│   └── archive/            # Previous runs preserved here
├── .architect/
│   ├── logs/                 # Full execution transcripts per task
│   ├── prompts/              # Architect-owned planner/reviewer/intelligence prompts
│   ├── architect.json        # Architect-owned provider config for planning roles
│   ├── circuit.json          # Circuit breaker state (persists across restarts)
│   ├── monitor_state.json    # Live monitor state (read by `architect monitor`)
│   └── runner.lock           # Prevents concurrent runs
├── ARCHITECT.md              # Durable project intelligence (curated project brain)
└── architect.toml            # Your configuration (optional)
```

`ARCHITECT.md` is worth committing to git — it is your project's long-term memory and improves with every session.

---

## Versioning

The Architect uses a dual-track versioning scheme built for AI-assisted development:

```text
v1.0.0 (build 10042)
 ----    -----------
  |           |
  |           +-- Global build counter
  |               Increments with every completed task/change
  |               (reads, writes, renames, test runs)
  |               Never resets across versions
  |               Always at least 5 digits
  |
  +------------ Semantic version
                Increments on human-tagged releases only
```

The build number is a continuous record of effort. By the time you ship a release, the build number reflects every single operation performed to get there — not just the release tag.

**Major version alignment:**

```text
v1.x.x   build 10000 onwards
v2.0.0   build 20000         (major version, build floor jumps)
v3.0.0   build 30000         (major version, build floor jumps)
```

---

## Error Handling

| Scenario | How it is handled |
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

---

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a PR.

```bash
# Quick setup
git clone https://github.com/inetanel/the-architect
cd the-architect
pip install -e ".[dev]"
pytest tests/
```

Every PR must increment the build number in `version.py` — including documentation and maintenance changes. See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow.

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
