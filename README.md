<div align="center">

```text
  ████████╗██╗  ██╗███████╗      █████╗ ██████╗  ██████╗██╗  ██╗██╗████████╗███████╗ ██████╗████████╗
  ╚══██╔══╝██║  ██║██╔════╝     ██╔══██╗██╔══██╗██╔════╝██║  ██║██║╚══██╔══╝██╔════╝██╔════╝╚══██╔══╝
     ██║   ███████║█████╗       ███████║██████╔╝██║     ███████║██║   ██║   █████╗  ██║        ██║   
     ██║   ██╔══██║██╔══╝       ██╔══██║██╔══██╗██║     ██╔══██║██║   ██║   ██╔══╝  ██║        ██║   
     ██║   ██║  ██║███████╗     ██║  ██║██║  ██║╚██████╗██║  ██║██║   ██║   ███████╗╚██████╗   ██║   
     ╚═╝   ╚═╝  ╚═╝╚══════╝     ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚═╝   ╚═╝   ╚══════╝ ╚═════╝   ╚═╝   
```

**An autonomous development lifecycle layer for agentic AI coding tools.**  
Describe a goal. Walk away. Come back to results.

[![PyPI version](https://img.shields.io/pypi/v/the-architect?color=blue&label=PyPI)](https://pypi.org/project/the-architect/)
[![Python](https://img.shields.io/pypi/pyversions/the-architect)](https://pypi.org/project/the-architect/)
[![CI](https://github.com/inetanel/the-architect/actions/workflows/ci.yml/badge.svg)](https://github.com/inetanel/the-architect/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/inetanel/the-architect?style=social)](https://github.com/inetanel/the-architect)

[Website](https://inetanel.com/projects/the-architect) · [Quickstart](#quickstart) · [CLI Reference](#cli-reference) · [Configuration](#configuration) · [Contributing](CONTRIBUTING.md)

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
| No recovery when agent gets stuck | Circuit breaker + smart retry + model fallback |
| You babysit every run | Fully unattended — fire and forget |
| No audit trail of what changed | Build counter tracks every agent operation |
| Agent hallucinates completion | Multi-signal completion detection |
| You re-explain the project every time | Project structure auto-detected and injected |
| One shot — no quality gate | Retrospective reviewer creates fix-up tasks |
| Developer plans every task manually | AI planner decomposes the goal autonomously |

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

# Force a new plan sequence
architect --plan

# Force a new plan with a specific goal
architect --plan --goal "add Stripe payment integration"

# Headless / CI mode
architect --headless --goal "fix mypy errors" --scope simple
```

That is it. The Architect plans, executes, retries, reviews, and reports — unattended.

---

## Supported Providers

The Architect works with agentic AI coding CLIs. Currently supported:

| Provider | Install |
|---|---|
| [OpenCode](https://opencode.ai) | `brew install opencode` or `npm i -g opencode-ai` |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | `npm install -g @anthropic-ai/claude-code` |

More providers are planned. The Architect is designed to be provider-agnostic.

When both are installed, The Architect asks which to use. Set a preference in `architect.toml`:

```toml
[architect]
provider = "auto"         # detect and prompt if both present
# provider = "opencode"
# provider = "claude-code"
```

Or via environment variable: `ARCHITECT_PROVIDER=claude-code`

### Provider Feature Comparison

| Feature | OpenCode | Claude Code |
|---|---|---|
| Planning and execution | ✅ | ✅ |
| Retry and circuit breaker | ✅ | ✅ |
| Retrospective review | ✅ | ✅ |
| Token usage tracking | ✅ | ❌ plain text output |
| Free tier model rotation | ✅ via OpenRouter | ❌ |

---

## How It Works

```text
  Your Goal
      |
      v
  +-------------------------------------------------------------+
  |                       THE ARCHITECT                         |
  |                                                             |
  |  1. PLAN       Decomposes goal into numbered task files     |
  |                Detects repo type, languages, frameworks     |
  |                Injects persistent project intelligence      |
  |                                                             |
  |  2. EXECUTE    Runs each task via your AI coding tool       |
  |                Streams output live to terminal              |
  |                Tracks completion with 4 corroborating       |
  |                signals — no hallucinated completions        |
  |                                                             |
  |  3. RECOVER    Circuit breaker catches stuck agents         |
  |                Retries with model fallbacks                 |
  |                Auto-replans genuinely failing tasks         |
  |                                                             |
  |  4. REVIEW     Retrospective agent reads the actual code    |
  |                Runs your test suite                         |
  |                Creates fix-up tasks if issues found         |
  |                                                             |
  |  5. REMEMBER   ARCHITECT.md accumulates decisions,          |
  |                constraints, and lessons across sessions     |
  |                Every run builds on the last                 |
  |                                                             |
  +-------------------------------------------------------------+
      |
      v
  Results — code written, tests passing, SUCCESS.md summary
```

**The Architect never writes your application code.** Your AI coding tool does. The Architect makes sure it actually finishes.

---

## Key Features

### Autonomous Planning
The Architect decomposes your goal into numbered task files using an AI planner. You are no longer the one breaking down the work — describe the outcome, and the planner figures out the steps.

### Persistent Project Intelligence
`ARCHITECT.md` accumulates architectural decisions, known constraints, lessons learned, and best practices across every session. By your third run, The Architect knows your project as well as you do.

### Smart Retry and Circuit Breaker
Failed tasks are retried with model fallbacks. The circuit breaker detects when an agent is truly stuck — via no-progress detection, same-error fingerprinting, and token-decline signals — and either rotates models or rewrites the failing task entirely.

### Multi-Signal Completion Detection
No single signal is trusted. The Architect requires corroboration between promise tags, `PROGRESS.md` updates, exit codes, and output analysis before declaring a task done. Stuck agents that claim completion are caught automatically.

### Retrospective Review
After execution, a reviewer agent reads the actual code, runs your tests, and creates targeted fix-up tasks if quality issues are found. Clean builds skip the fix-up round automatically.

### Free Tier Rotation *(OpenCode + OpenRouter only)*
Automatically rotates through free OpenRouter models when rate limits hit — mid-stream, without restarting. Zero cost, zero interruption.

### Build Tracking
Every agent operation — reads, writes, renames, test runs — increments the build counter. Full traceability of effort across every session, not just version releases.

### Live tmux Dashboard
Split-pane terminal dashboard shows live agent output, task progress, circuit breaker state, token usage, and build number — all updating in real time.

---

## How The Architect Compares

The Architect and Ralph both help coding agents work more autonomously.

Ralph is closer to a persistent autonomous loop around Claude Code. The Architect goes further into planning, task orchestration, persistent project memory, retrospective review, and provider-agnostic execution.

| Feature | The Architect | Ralph |
|----|---:|---:|
| Goal-to-task planning | ✅ | ❌ |
| Managed task files | ✅ | ✅ |
| Progress tracking | ✅ | ✅ |
| Persistent project memory | ✅ | ❌ |
| Retrospective review | ✅ | ❌ |
| Automatic fix-up tasks | ✅ | ❌ |
| Run summary report | ✅ | ❌ |
| Autonomous execution | ✅ | ✅ |
| Resume from saved progress | ✅ | ✅ |
| Live session continuity | ➖ | ✅ |
| Automatic retries | ✅ | ✅ |
| Retry model fallback | ✅ | ❌ |
| Stuck-loop detection | ✅ | ✅ |
| Circuit breaker / safe recovery | ✅ | ✅ |
| Rate-limit / cooldown handling | ✅ | ✅ |
| Repo structure detection | ✅ | ❌ |
| Framework / component detection | ✅ | ❌ |
| Dependency graph detection | ✅ | ❌ |
| Test / lint command detection | ✅ | ❌ |
| Live monitoring | ✅ | ✅ |
| tmux support | ✅ | ✅ |
| Headless / CI mode | ✅ | ✅ |
| Token budget guard | ✅ | ❌ |
| Concurrent-run protection | ✅ | ❌ |
| Premature re-plan guard | ✅ | ❌ |
| Task/archive history | ✅ | ❌ |
| Multi-provider support | ✅ | ❌ |
| Claude Code support | ✅ | ✅ |
| OpenCode support | ✅ | ❌ |
| PRD / spec / docs input | ✅ | ✅ |
| JSON-oriented loop control | ❌ | ✅ |
| Extra CLI loop / permission controls | ❌ | ✅ |
| Project bootstrap / setup templates | ✅ | ✅ |

**Legend:** ✅ yes · ➖ partial · ❌ no

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

# Persistent mode (30 retries, 2 retrospective rounds)
architect --persistent

# Free tier — rotate free OpenRouter models on rate limit (OpenCode only)
architect --free

# Headless / CI mode — no interactive prompts
architect --headless --goal "fix mypy errors" --scope simple

# Standalone mode — bypass provider config, use this model directly
architect --standalone openrouter/anthropic/claude-sonnet-4.5

# Use a specific project directory
architect --project /path/to/project

# Skip tmux dashboard
architect --no-monitor
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

# Attach to the live tmux monitoring session
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
| `ARCHITECT_PROVIDER` | `--provider` | `claude-code` |
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
provider = "auto"                    # "auto" | "opencode" | "claude-code"

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
| `provider` | `auto` | AI CLI provider — `auto`, `opencode`, or `claude-code` |
| `max_retries` | `3` | Max retry attempts per task |
| `retry_pause` | `30` | Seconds between retries |
| `pause_between_tasks` | `10` | Seconds between tasks |
| `retry_model_2` | `""` | Fallback model for attempt 2 |
| `retry_model_3` | `""` | Fallback model for attempt 3 |
| `carry_context` | `true` | Inject previous attempt context on retry |
| `retry_prompt_mode` | `focused` | `focused` or `same` |
| `retrospective_rounds` | `1` | Review rounds after execution (0 = off) |
| `free_mode` | `false` | Rotate free OpenRouter models |
| `persistent` | `false` | 30 retries, 2 retrospective rounds |
| `circuit_no_progress_threshold` | `3` | No-progress trips before circuit opens |
| `circuit_same_error_threshold` | `3` | Same-error trips before circuit opens |
| `circuit_token_decline_pct` | `60` | Token decline % to trip circuit |
| `circuit_cooldown_minutes` | `30` | Wait before half-open retry |
| `circuit_enable_replan` | `true` | Allow targeted task replan on exhaustion |
| `cooldown_detection` | `true` | Detect and wait on provider rate limits |
| `token_budget_per_hour` | `0` | Max tokens per rolling hour (0 = unlimited) |

---

## Live Dashboard (tmux)

When tmux is installed, The Architect automatically opens a split-pane session:

```text
+-------------------------------------+---------------------------------+
|                                     |  THE ARCHITECT                  |
|   AI agent live output              |---------------------------------|
|   streams here in real-time         |  TASKS                          |
|                                     |  v T01 Setup         (done)     |
|   == T02  Build API  (2/3 remain)   |  * T02 Build API     (running)  |
|      starting T02...                |  o T03 Frontend      (pending)  |
|                                     |---------------------------------|
|   [agent output scrolls here]       |  STATUS                         |
|                                     |  Task    : T02 / 3              |
|                                     |  Attempt : 1 / 3                |
|                                     |  Circuit : CLOSED               |
|                                     |---------------------------------|
|                                     |  BUILD                          |
|                                     |  v1.0.0 (build 10042)           |
+-------------------------------------+---------------------------------+
```

### Installing tmux

```bash
# macOS
brew install tmux

# Ubuntu / Debian
sudo apt install tmux

# Arch Linux
sudo pacman -S tmux

# Fedora
sudo dnf install tmux
```

### tmux Controls

```bash
# Detach from session (leaves it running in background)
Ctrl+B then D

# Reattach after detaching
tmux attach-session -t architect-<your-project-name>

# List all Architect sessions
tmux ls | grep architect
```

No tmux? No problem — The Architect runs fine without it. Same live output, no side dashboard.

Disable the dashboard entirely:

```bash
architect --no-monitor
```

---

## Files Created in Your Project

```text
your-project/
├── tasks/                    # Task files (auto-generated by planner)
│   ├── T01_init.md
│   ├── T02_feature.md
│   ├── INSTRUCTIONS.md       # Project context for the agent
│   └── archive/              # Previous runs preserved here
├── .architect/
│   ├── logs/                 # Full execution transcripts per task
│   ├── circuit.json          # Circuit breaker state (persists across restarts)
│   ├── monitor_state.json    # Live dashboard state
│   └── runner.lock           # Prevents concurrent runs
├── ARCHITECT.md              # Persistent project intelligence (grows over time)
├── PROGRESS.md               # Current task state
├── SUCCESS.md                # Run summary (generated after each run)
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
  |               Increments with every agent operation
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