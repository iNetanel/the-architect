# The Architect

> **Fire-and-forget autonomous development powered by OpenCode or Claude Code.**

The Architect is an open-source Python CLI application that wraps [OpenCode](https://opencode.ai) or [Claude Code](https://docs.anthropic.com/en/docs/claude-code) to provide fully autonomous development agents. You describe what you want to build — in plain English, or by pointing to a PRD, spec, or any document — The Architect plans it into numbered tasks, executes them unattended, detects and recovers from failures automatically, and shows live progress in the terminal with an optional tmux split-pane dashboard.

It is published to PyPI as `the-architect` and works on any project regardless of language, framework, or stack.

---

## Table of Contents

1. [What The Architect Does](#1-what-the-architect-does)
2. [Supported Providers — OpenCode and Claude Code](#2-supported-providers--opencode-and-claude-code)
3. [How It Works](#3-how-it-works)
4. [Context Injection — PRD, Spec, and Any Document Import](#4-context-injection--prd-spec-and-any-document-import)
5. [Project Structure Detection](#5-project-structure-detection)
6. [Planning Phase](#6-planning-phase)
7. [Execution Phase](#7-execution-phase)
8. [Completion Detection — Signals and Methods](#8-completion-detection--signals-and-methods)
9. [Stuck Detection — How The Architect Knows the Agent Is Blocked](#9-stuck-detection--how-the-architect-knows-the-agent-is-blocked)
10. [Retry Logic](#10-retry-logic)
11. [Circuit Breaker](#11-circuit-breaker)
12. [Rate Limit Detection — Provider Cooldowns and Free Mode](#12-rate-limit-detection--provider-cooldowns-and-free-mode)
13. [Free Mode — Zero-Cost OpenRouter Rotation](#13-free-mode--zero-cost-openrouter-rotation)
14. [Persistent Mode](#14-persistent-mode)
15. [Headless Mode — CI/Automated Execution](#15-headless-mode--ciautomated-execution)
16. [Interactive Screens](#16-interactive-screens)
17. [Token Budget](#17-token-budget)
18. [Retrospective Review](#18-retrospective-review)
19. [Premature Exit Guard](#19-premature-exit-guard)
20. [Lock File — Preventing Concurrent Runs](#20-lock-file--preventing-concurrent-runs)
21. [Configuration](#21-configuration)
22. [Task Files](#22-task-files)
23. [PROGRESS.md](#23-progressmd)
24. [SUCCESS.md — Run Summary](#24-successmd--run-summary)
25. [ARCHITECT.md — Persistent Project Intelligence](#25-architectmd--persistent-project-intelligence)
26. [tmux Dashboard — Live Monitoring](#26-tmux-dashboard--live-monitoring)
27. [Error Handling](#27-error-handling)
28. [Project Structure — What The Architect Creates](#28-project-structure--what-the-architect-creates)
29. [Dependencies](#29-dependencies)

---

## 1. What The Architect Does

The Architect automates the entire development lifecycle:

- **Planning** — Decomposes your goal into numbered task files (T01, T02, …) using an AI architect agent. You can describe the goal in plain English, or point to a PRD, SPEC.md, design doc, or any file or directory via `--context`
- **Project Detection** — Automatically detects your repo type (monorepo, multi-repo, single repo), languages, frameworks, components, dependency graph, project descriptions, key dependencies, test/lint commands, and sub-components — and injects this into the architect's planning prompt and every execution instruction
- **Execution** — Runs each task via the AI CLI provider (OpenCode or Claude Code), streaming output live to the terminal
- **Smart Retry** — Automatically retries failed tasks with model fallbacks, previous-attempt context injection, and circuit breaker protection
- **Stuck Detection** — Monitors agent output for "I'm stuck", "can't proceed", and similar patterns; the circuit breaker reacts to no-progress, repeated errors, and token decline signals
- **Cooldown Handling** — Detects provider rate limits (HTTP 429, "rate limit" in output) and pauses automatically without consuming retry slots
- **Retrospective Review** — After execution, runs a reviewer agent that examines completed work, runs tests, and creates fix-up tasks (R01, R02, …) if quality issues are found
- **Persistent Memory** — Maintains `PROGRESS.md` and `ARCHITECT.md` as persistent memory between runs; ARCHITECT.md accumulates architectural decisions, lessons learned, and constraints over time; it is read and updated by the planner, build agent, and reviewer
- **Token Budget** — Optional hourly spend cap prevents runaway API costs
- **Premature Exit Guard** — When all tasks are already done, refuses to re-enter planning mode without explicit `--plan`, preventing accidental re-Archictecting of an already-complete project

Your involvement is minimal: describe a goal (or just point to a doc), answer a few questions, then walk away. Come back to results.

---

## 2. Supported Providers — OpenCode and Claude Code

The Architect supports two AI CLI backends. Both provide the same core features — planning, execution, retry, circuit breaker, retrospective, and cooldown detection — with a few provider-specific differences.

### Provider Selection

At startup, The Architect auto-detects which provider is installed:

| Scenario | Behaviour |
|----------|-----------|
| Only OpenCode installed | Uses OpenCode silently |
| Only Claude Code installed | Uses Claude Code silently |
| Both installed | Interactive arrow-key selection screen |
| Neither installed | Error with install instructions for both |

You can also set a preference explicitly in `architect.toml`:

```toml
[architect]
provider = "auto"         # default — detect and prompt if both present
# provider = "opencode"   # require OpenCode
# provider = "claude-code"  # require Claude Code
```

Or via environment variable: `ARCHITECT_PROVIDER=claude-code`

### Installing the Providers

**OpenCode:**
```bash
brew install opencode      # macOS (Homebrew)
npm i -g opencode-ai       # npm (cross-platform)
```

**Claude Code:**
```bash
npm install -g @anthropic-ai/claude-code
```

### Provider Differences

| Feature | OpenCode | Claude Code |
|---------|----------|-------------|
| Binary | `opencode` | `claude` |
| Named agents | Yes (`--agent build`) | No — prompt injected as prefix |
| Output format | Structured JSON events | Plain text |
| Token counts | Yes (per event) | Not available |
| Config file | `opencode.json` | `CLAUDE.md` |
| Model list | `opencode models` | Static list of known Claude models |
| Free Tier (OpenRouter) | Yes (if OpenRouter configured) | **Never** |
| Planning config | `.architect/architect.json` | Not needed — prompt prefix |

### What Works the Same on Both Providers

- Planning (architect agent decomposes goals into tasks)
- Execution (tasks run unattended)
- Retry logic (`max_retries`, `retry_model_2/3`, `carry_context`)
- Circuit breaker (no-progress, same-error, token-decline detection)
- Cooldown detection (rate limit / quota exhaustion pause)
- Retrospective review (reviewer agent creates fix-up tasks)
- Persistent mode
- Token budget
- Headless / CI mode
- tmux dashboard
- All configuration options except `free_mode` (OpenCode + OpenRouter only)

### Token Counts with Claude Code

Claude Code outputs plain text — there are no structured JSON events carrying token usage. Token counts will show as `0` in SUCCESS.md and the dashboard when using Claude Code. This is a provider limitation, not a bug.

### Free Tier with Claude Code

Free Tier (`--free`) is **not available** with Claude Code. Claude Code uses Anthropic's API directly — there is no OpenRouter integration. If `--free` is passed with Claude Code active, a warning is shown and the flag is cleared.

---

## 3. How It Works

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         THE ARCHITECT FLOW                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. CONTEXT (optional)                                                       │
│     ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                │
│     │ PRD / SPEC  │    │ --context    │    │ Auto-detect  │                │
│     │ any .md /   │───▶│ file / dir  │───▶│ repo type,   │                │
│     │ design doc  │    │ import      │    │ framework,   │                │
│     └──────────────┘    └──────────────┘    │ components   │                │
│                                            └──────────────┘                │
│                                                                              │
│  2. PLANNING                                                                 │
│     ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                │
│     │  User Goal   │───▶│  Architect   │───▶│  Task Files  │                │
│     │  (text or   │    │  (Provider   │    │  (T01_*.md,  │                │
│     │   --context)│    │   agent)     │    │   T02_*.md)  │                │
│     └──────────────┘    └──────────────┘    └──────────────┘                │
│                                                                              │
│  3. EXECUTION                                                                 │
│     ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                │
│     │   Tasks      │───▶│   Runner     │───▶│   Provider   │                │
│     │  (pending)   │    │  (executes   │    │  (does the   │                │
│     │              │    │   in order)  │    │   work)      │                │
│     └──────────────┘    └──────────────┘    └──────────────┘                │
│                              │                                            │
│                              │ ┌──────────────────────────────────┐        │
│                              │ │ Circuit Breaker                  │        │
│                              │ │ • No-progress detection          │        │
│                              │ │ • Same-error detection           │        │
│                              │ │ • Token-decline detection        │        │
│                              │ │ • Cooldown / rate-limit wait    │        │
│                              │ │ • Auto-replan on exhaustion     │        │
│                              │ └──────────────────────────────────┘        │
│                              │                                            │
│  4. STUCK DETECTION          │                                            │
│     ┌──────────────┐          │                                            │
│     │  Output     │──────────▶│ 2+ "I'm stuck" patterns = stuck signal       │
│     │  analysis   │          │                                            │
│     └──────────────┘          │                                            │
│                              │                                            │
│  5. RETRY (on failure)        │                                            │
│     ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                │
│     │  Attempt 1   │───▶│  Attempt 2   │───▶│  Attempt 3  │                │
│     │  (failed)    │    │  (fallback    │    │ (different   │                │
│     │              │    │   model)      │    │   model)     │                │
│     └──────────────┘    └──────────────┘    └──────────────┘                │
│                                                                              │
│  6. RETROSPECTIVE (after execution)                                          │
│     ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                │
│     │  Reviewer     │───▶│  Fix-up      │───▶│  Re-execute  │                │
│     │  (runs tests,│    │  Tasks       │    │  fix-up      │                │
│     │  reviews     │    │  (R01_*.md)  │    │  tasks       │                │
│     │  code)       │    │              │    │              │                │
│     └──────────────┘    └──────────────┘    └──────────────┘                │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Key Design Principles

- **Model-agnostic** — Uses your existing OpenCode or Claude Code setup. No separate API key management
- **Fire-and-forget** — Set a goal, walk away, come back to results
- **Zero-config by default** — Works without any configuration file. All settings have sensible defaults
- **Never modifies your provider config** — The Architect writes its own planning config to `.architect/architect.json` (OpenCode) or injects prompts directly (Claude Code). Your config is used untouched during execution
- **No direct AI API calls** — Everything goes through the provider CLI. No Anthropic SDK, no OpenAI SDK
- **Never crash silently** — All exceptions are logged with full context

---

## 4. Context Injection — PRD, Spec, and Any Document Import

The `--context` flag is how you import external documents — PRDs, specs, design docs, any Markdown file, or even entire directories — into The Architect's planning prompt.

### What It Does

Any file or directory path passed via `--context` is read and injected into the architect agent's planning prompt, labelled with its path so the agent knows where each piece of context came from. This allows you to:

- Point to a `PRD.md` describing what to build
- Point to a `SPEC.md` with detailed requirements
- Point to a `design/` directory containing multiple design documents
- Point to any combination of files and directories

### Goal Extraction from Context

If you provide `--context` files but no `--goal`, The Architect automatically extracts a goal by scanning for common patterns:

- `## Goal` section
- `## Objective` section
- `## Requirements` section

The first matching section (from any file, in order) becomes the goal. This lets you run `architect --plan --context PRD.md` with no other arguments and have the goal extracted automatically.

### Supported File Types

When scanning a directory recursively, The Architect reads files with these extensions:

```
.md, .txt, .rst, .json, .yaml, .yml, .toml,
.py, .ts, .tsx, .js, .jsx, .go, .rs, .rb,
.java, .kt, .cs, .php, .cfg, .ini, .env,
.sh, .bash, .zsh, .sql, .graphql, .proto, .tf, .hcl
```

Binary files are skipped silently.

### Context File Size Limits

- **Per file**: Truncated at **50,000 characters** if too large (with a note appended showing truncation)
- **Planner budget**: Capped at **8,000 characters** for the planning prompt (to keep the architect agent's context manageable)
- **Reviewer budget**: Capped at **12,000 characters** for retrospective (reviewer needs more context to read code)

### Skip Directories

These directories are never scanned for context (even when using `--context ./`):

```
.git, .venv, node_modules, __pycache__,
.architect, .pytest_cache, dist, build, .next, .cache
```

Symlinks pointing outside the project directory are also excluded.

### Headless Mode with Context

In headless/CI mode, context injection is especially powerful:

```bash
ARCHITECT_CONTEXT="/path/to/PRD.md:/path/to/design/" architect --plan --headless
```

Multiple context paths are separated by the OS path separator (`:` on Unix, `;` on Windows).

---

## 5. Project Structure Detection

During every planning session, The Architect automatically scans the project to understand its structure. This detection runs fresh on every `--plan` and the results are:

1. **Injected into the architect's planning prompt** — so the architect knows what languages, frameworks, and components exist before creating tasks
2. **Written into `ARCHITECT.md`** — persistent project intelligence that accumulates across sessions

### What Is Detected

#### Repo Type

| Type | Detection |
|------|----------|
| **Single repo** | Standard project with one `.git` folder |
| **Monorepo** | Single repo containing 2+ component signal directories (e.g., `frontend/`, `backend/`) |
| **Multi-repo** | Project root contains 2+ subdirectories each with their own `.git` |
| **Untracked** | No `.git` found |

#### Languages

Detected via signal files:

| Signal File | Language |
|------------|----------|
| `package.json` | JavaScript/TypeScript |
| `pyproject.toml`, `setup.py` | Python |
| `Cargo.toml` | Rust |
| `go.mod` | Go |
| `pom.xml`, `build.gradle` | Java/Kotlin |
| `composer.json` | PHP |
| `Gemfile` | Ruby |
| `*.csproj`, `*.fsproj` | C# |

#### Frameworks

Framework detection is language-aware and uses a two-pass approach: config-file-based detection first (most reliable), then package.json dependency scanning.

**JavaScript/TypeScript:**
- Config files: `next.config.js/ts/mjs` → Next.js, `nuxt.config.js/ts` → Nuxt.js, `vite.config.js/ts` → Vite
- Package.json deps: `react-native` → React Native, `@angular/core` → Angular, `next` → Next.js, `nuxt` → Nuxt.js, `vue` → Vue, `svelte` → Svelte, `express` → Express, `fastify` → Fastify, `react` → React

**Python:**
- Scans `pyproject.toml` dependencies and `requirements.txt`
- `fastapi` → FastAPI, `django` → Django, `flask` → Flask

**Rust:**
- Scans `Cargo.toml`
- `axum` → Axum, `actix-web` → Actix, `tokio` → async runtime (tokio)

**Go:**
- Scans `go.mod`
- `gin-gonic/gin` → Gin, `labstack/echo` → Echo

#### Component Enrichment

After detecting language and framework, each component is enriched with metadata from its project config files:

**From `pyproject.toml`:**
- **Description** — extracted from `description = "..."` in `[project]`
- **Key dependencies** — top 8 from `dependencies = [...]` (build/test tooling filtered out)
- **Test command** — auto-detected: `pytest tests/ -v --tb=short` when pytest is in deps
- **Lint command** — auto-detected: `ruff check .` when ruff is in deps

**From `package.json`:**
- **Description** — extracted from `"description"` field
- **Key dependencies** — top 8 from `dependencies` + `devDependencies` (dev tooling filtered out)
- **Test command** — `npm test` when `"test"` script exists
- **Lint command** — `npm run lint` when `"lint"` script exists

#### Sub-Component Detection

When a directory has no signal files at its root but contains sub-directories that do, The Architect recurses one level deep to detect sub-components. For example, an `app/` directory containing `backend/` (with `pyproject.toml`) and `frontend/` (with `package.json`) will be detected as a single component with two sub-components — each enriched with its own language, framework, description, stack, and commands.

#### Component Roles

Roles are inferred from directory name, detected framework, and sub-component composition:

| Directory Name | Inferred Role |
|---------------|--------------|
| `frontend/`, `web/`, `client/` | Web UI |
| `mobile/` | Mobile UI |
| `backend/`, `api/`, `server/` | API server |
| `engine/`, `core/` | Core library |
| `worker/`, `jobs/` | Background worker |
| `packages/`, `shared/`, `common/`, `libs/` | Shared library |
| `infra/`, `deploy/`, `terraform/` | Infrastructure |
| `app/` (with both frontend + backend subs) | Full-stack application |
| `dev/` | Development environment |
| `documentation/` | Documentation |
| Next.js / Nuxt.js / React / Vue / Angular | Web UI |
| FastAPI / Django / Flask / Express | API server |
| React Native | Mobile UI |

#### Dependency Graph

Detects inter-component dependencies from:

1. **docker-compose.yml** — parses `depends_on` relationships (supports both list and dict formats). Falls back to regex line-by-line parsing if PyYAML is unavailable
2. **package.json workspaces** — detects `workspaces: ["packages/*"]` patterns
3. **package.json local path deps** — detects `dependencies: { "my-lib": "file:../my-lib" }`
4. **Cargo.toml path dependencies** — detects `path = "../other-component"`
5. **pyproject.toml / requirements.txt** — detects `-e ./other-component` or path references
6. **Shared root directories** — detects `packages/`, `shared/`, `common/`, `libs/` at project root

### Output Example

The structure report is written into `ARCHITECT.md`'s Project Structure section as rich component blocks:

```markdown
**Type:** Multi-repo
**Detected:** multiple repositories detected
**Scanned:** 2026-04-19

### Components

**app/** — Full-stack application
>
> Sub-components:
> **backend/** — Python · FastAPI · API server
> > Example Backend API
> > Stack: fastapi, uvicorn
> > test: `pytest tests/ -v --tb=short`

> **frontend/** — JavaScript/TypeScript · Next.js · Web UI
> > Stack: next, react, next-sanity, lucide-react
> > lint: `npm run lint`

**the_architect/** — Python
> Autonomous development lifecycle layer for agentic AI coding tools
> Stack: questionary, loguru, rich, click, pydantic, httpx
> test: `pytest tests/ -v --tb=short` | lint: `ruff check .`

### Dependency Graph

- frontend/ → backend/  (via: docker-compose depends_on)
```

---

## 6. Planning Phase

When you run `architect --plan`, The Architect enters interactive planning mode:

1. **Pending task guard** — Warns if unfinished tasks exist; in headless mode automatically archives them; in interactive mode asks for confirmation
2. **Goal prompt** — "What do you want to build?" (or extracted automatically from `--context` files)
3. **Scope selection** — Choose task granularity
4. **Architect model selection** — Pick from available provider models
5. **Execution agent selection** — Pick which agent runs the tasks (OpenCode only; Claude Code has no named-agent system)

### What The Architect Does During Planning

1. **Detects project structure** — runs the full detection pipeline (Section 4)
2. **Reads existing `ARCHITECT.md`** — for persistent project intelligence (previous decisions, constraints, lessons)
3. **Reads `PROGRESS.md`** — extracts completed tasks and permanent decisions only (active state is excluded to prevent the architect from confusing "continue old plan" with "start new plan")
4. **Gathers context** — reads `--context` files and directories if provided
5. **Detects docs/** — reads file names and first 80 lines of each doc in `docs/` directory
6. **Runs the provider CLI with the architect role** — For OpenCode: `opencode run --agent architect` with `OPENCODE_CONFIG` set to `.architect/architect.json`. For Claude Code: `claude --print` with the architect prompt prepended to the instruction.
7. **Rescues stray task files** — if the architect wrote task files outside `tasks/`, moves them to the canonical location
8. **Updates `ARCHITECT.md`** — rewrites the structure section, preserves all other sections
9. **Writes `PROGRESS.md`** and `tasks/INSTRUCTIONS.md`

### Planning Retries

If planning fails to create any tasks (e.g. due to a transient network error), The Architect automatically retries up to **3 attempts** with a **30-second pause** between attempts. A red warning is shown in the terminal so fire-and-forget users can see the retry if they glance at the screen.

If all 3 attempts fail, planning exits with an error.

### Model Resolution

| Agent | Model source |
|-------|-------------|
| **Architect** | User's interactive selection → provider default |
| **Reviewer** | Same model as architect → provider default |
| **Execution** | User's provider default agent (OpenCode: from `opencode.json`; Claude Code: from `ANTHROPIC_MODEL` env var or `CLAUDE.md`) |

The architect and reviewer both perform high-reasoning work (planning and critique), so they use the same model. The execution agent is separate — it's the workhorse that runs tasks, managed by the user's opencode config.

### Planning Instruction Priority Order

The architect prompt is structured with context in priority order:

1. **ARCHITECT.md** — persistent project intelligence (highest priority)
2. **Project Structure Report** — auto-detected repo type, languages, frameworks, components
3. **Additional Context Files** — user-provided via `--context`
4. **Project Context** — file tree, PROGRESS.md history (completed tasks + decisions only), docs, tasks status
5. **User's Goal** — the actual request

### Scope Guide

| Scope | Task Breadth | Context Per Run | Best For |
|-------|-------------|-----------------|----------|
| `simple` | One function, one file, one test | Small | Weak/local models, large codebases |
| `standard` | One feature area, related routes, module + tests | Moderate | Most projects and models |
| `complex` | Whole subsystem, cross-cutting concern, full API layer | Large | Frontier models (Opus, GPT-4o, Gemini 2.5 Pro) |

The number of tasks is never fixed — it emerges from goal size ÷ scope. The same goal produces more tasks at `simple` scope and fewer at `complex`.

### Previous Run Archiving

When a new planning session starts, previous task files (T, R, S prefixes) and `INSTRUCTIONS.md` are moved to `tasks/archive/YYYY-MM-DD_HHMMSS/` — history is preserved but the new session starts clean. `INSTRUCTIONS.md` is archived alongside the task files because it contains the original goal, stack information, and plan context that makes the archived tasks meaningful. A fresh `INSTRUCTIONS.md` is generated for the new plan. The log directory (`.architect/logs/`) is also cleared before each run.

---

## 7. Execution Phase

When you run `architect`, The Architect enters execution mode:

1. Discovers all task files in `tasks/`
2. Filters out tasks already marked Done in `PROGRESS.md`
3. Acquires a lock file (`.architect/runner.lock`) to prevent concurrent runs
4. Runs each pending task via the active provider CLI
5. Streams provider output directly to the terminal in real-time — no piping, no reformatting, pure TTY-like output
6. For OpenCode: parses JSON events for token usage tracking and display rendering (supports both v1.4+ and legacy formats). For Claude Code: plain text output is displayed as-is; token counts are not available
7. After each task: 2-second pause to allow file writes to flush, then checks PROGRESS.md for completion
8. Pauses `pause_between_tasks` seconds between tasks (configurable, default: 10)
9. Writes `SUCCESS.md` with the final summary

### Execution Instruction Composition

Each task's execution instruction is composed of three parts:

1. **Execution Protocol** — The Architect's operating rules (from `resources/prompts/execution-protocol.md`), explaining how PROGRESS.md works, completion detection, and anti-hallucination guards
2. **ARCHITECT.md content** — The full ARCHITECT.md is injected so the build agent has access to accumulated project intelligence: permanent decisions, known constraints, lessons learned, best practices, and the enriched project structure (descriptions, stack, test/lint commands)
3. **Task-specific instruction** — Project root boundary, PROGRESS.md and task file pointers, retry context (if applicable)

The build agent is also instructed to **update ARCHITECT.md** when it discovers new constraints, lessons, or decisions during execution — making project intelligence truly cumulative.

### How Provider Output Is Rendered

**OpenCode** is invoked with `--format json` so each stdout line is a structured JSON event. The Architect parses these to:

- **Extract token usage** from `step_finish` events (supports both v1.4+ `part.tokens` and legacy `usage` formats)
- **Render tool calls** with filenames and commands: `→ read foo.py`, `$ pytest tests/`, `→ write bar.py`, `→ glob "**/*.py"`, `→ grep "pattern" (*.ts)`
- **Render tool results**: match counts, file previews, bash output truncated to 10 lines, todo list items
- **Detect rate limits mid-stream** from error JSON events — enables immediate model rotation in free mode
- **Detect completion promises** from `<promise>TXX_COMPLETE</promise>` tags in text output
- **Mid-stream model rotation** in free mode — switches when a 429 error event is detected

Non-JSON lines (ANSI codes, status output) are passed through to the terminal as-is.

**Claude Code** is invoked with `--print` (non-interactive mode). Output is plain text — each line is displayed directly. Token counts are not available from Claude Code's plain-text output.

### Subprocess Buffer Limit

The stdout reader uses a **10 MB buffer limit** (`_SUBPROCESS_READ_LIMIT`) to handle very long JSON event lines (e.g., large tool outputs). If a line exceeds this limit, the reader stops gracefully with a logged warning rather than crashing.

### Provider Configuration

**OpenCode** config resolution order:

```
OPENCODE_CONFIG env var → OPENCODE_CONFIG_DIR env var →
project_root/opencode.json → ~/.config/opencode/opencode.json
```

During planning, `OPENCODE_CONFIG` is set to `.architect/architect.json` so the architect agent is available. During execution, `OPENCODE_CONFIG` is **not set** — OpenCode uses your own config untouched.

**Claude Code** config resolution order:

```
ANTHROPIC_API_KEY env var → CLAUDE_MODEL env var →
CLAUDE.md in project root → ~/.claude/CLAUDE.md
```

### Provider Timeout

The Architect sets `OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS=900000` (15 minutes) for OpenCode so long-running build commands don't time out prematurely. Claude Code manages its own timeouts.

---

## 8. Completion Detection — Signals and Methods

The Architect uses **multiple corroborating signals** to determine whether a task is complete. No single signal is trusted in isolation.

### The Four Signals

| Signal | Source | Strength | Notes |
|--------|--------|----------|-------|
| **Promise tag** | Agent outputs `<promise>TXX_COMPLETE</promise>` in text | **Strong** | Explicit, agent-declared completion |
| **PROGRESS.md** | Task marked `Done` in PROGRESS.md | Moderate | Could be premature |
| **Clean exit** | Provider CLI exited with code 0 | Weak | Provider may exit 0 even on timeout |
| **Progress signal** | Agent says "all tests pass", "task complete" | Weak | Could be from earlier text |

### Decision Rules

| Signals Fired | Result |
|--------------|--------|
| 2+ signals positive | **Done** (unambiguous) |
| Promise tag alone | **Done** (strong enough on its own) |
| PROGRESS.md alone | **Done** with warning logged |
| Exit code alone | **NOT done** |
| Progress signal alone | **NOT done** |

### Output Analysis Signals

Beyond the 4 main signals, The Architect also scans all agent text output for patterns via `analyze_output()`:

**Error signals** (agent is stuck/blocked — 2+ required to flag as stuck):
- `"I'm stuck"`, `"I am stuck"`, `"I am blocked"`, `"I am unable"`
- `"I can't proceed"`, `"I can't continue"`, `"I can't figure out"`, `"I can't resolve"`
- `"no clear path forward"`, `"no obvious path forward"`
- `"this seems impossible"`, `"this appears impossible"`
- `"unable to resolve"`, `"unable to fix"`, `"unable to complete"`
- `"blocked by an error"`, `"blocked by an issue"`, `"blocked by a problem"`, `"blocked by a dependency"`

**Progress signals** (agent reports forward momentum):
- `"all tests pass"`, `"all tests are passing"`, `"all tests green"`
- `"N tests passing"`, `"N tests passed"`, `"N tests green"`
- `"no errors found"`, `"no failures found"`, `"no issues found"`
- `"task is complete"`, `"task is done"`, `"task is finished"`
- `"all items complete"`, `"all sub-tasks complete"`, `"all requirements complete"`

**Self-assessment** (priority order: stuck > complete > in_progress > unknown):
- `"task is complete"`, `"task is done"` → `complete`
- `"I'm stuck"`, `"I can't proceed"` → `stuck`
- `"still working"`, `"still need to"`, `"still have to"` → `in_progress`

### Why "Stuck" Overrides "Complete"

If the agent says "task is complete" but also says "I'm stuck", The Architect classifies the assessment as **stuck** — because a stuck agent that claims completion is likely hallucinating.

---

## 9. Stuck Detection — How The Architect Knows the Agent Is Blocked

Stuck detection operates at two levels: within the output analyzer and as part of the circuit breaker.

### Level 1: Output Analysis (per attempt)

During each attempt, The Architect scans all text output from the agent for error signal patterns. If **2 or more different error patterns** are found in the output, the agent is flagged as **stuck**.

This is tracked in `OutputAnalysis.is_stuck` — a property that returns `True` when `len(self.error_signals) >= 2`.

### Level 2: Circuit Breaker (across attempts)

The circuit breaker tracks three failure patterns across retry attempts:

**1. No-progress counter** — incremented when an attempt produces zero file writes. Threshold: 3 by default (`circuit_no_progress_threshold`). Set to 0 to disable.

**2. Same-error counter** — incremented when an attempt's bash error has the same normalised fingerprint as the previous attempt. The fingerprint is created by:
- Stripping file paths (`/home/user/project/foo.py` → `<path>`)
- Stripping line numbers (`:42` → `:<N>`)
- Normalising whitespace, lowercasing

This means `"Error in /a/b/c.py line 42"` and `"Error in /x/y/z.py line 99"` produce the **same fingerprint**, so repeated logical errors (not just identical text) are caught. Threshold: 3 by default (`circuit_same_error_threshold`). Set to 0 to disable.

**3. Token decline** — if the latest attempt used less than 40% (configurable) of attempt-1's tokens **AND** at least one other counter is elevated. This detects the agent giving up earlier each attempt.

Token history stores the last 10 attempts (`_TOKEN_HISTORY_CAP`). Threshold: 60% decline by default (`circuit_token_decline_pct`). Set to 0 to disable.

### Combined Effect

When any threshold is breached, the circuit transitions from CLOSED → OPEN and a recovery action is chosen:

```
Recovery action decision tree:
1. circuit_enable_replan = false? → WAIT
2. replan already attempted? → WAIT (prevent infinite replanning)
3. retry models still available? → WAIT (let rotation happen first)
4. all models exhausted + no file progress ever? → REPLAN
5. all models exhausted + some file progress? → WAIT (task may just be hard)
```

---

## 10. Retry Logic

If a task fails (not marked Done after an attempt), The Architect retries automatically:

| Attempt | Model Used |
|---------|------------|
| 1 | opencode.json default |
| 2 | `retry_model_2` (configurable) |
| 3 | `retry_model_3` (configurable) |

### Context Carry (`carry_context`)

By default (`carry_context=true`), a structured summary of the previous attempt is injected into the retry instruction via `summarize_previous_attempt()`:

- Files written or edited (from write/edit tool calls in JSON event log)
- Files read (from read/view tool calls)
- Bash commands run (count)
- Test failures (pytest FAILED/ERROR output extracted from bash tool output)

The summary is parsed from the previous attempt's JSON event log file (not from memory — from the `.log` file on disk). This means even if The Architect is killed and restarted mid-retry, it can reconstruct what happened from the log.

### Retry Prompt Modes

**`retry_prompt_mode=focused`** (default) — structured step-by-step guidance on retry:
1. Read PROGRESS.md — check which sub-tasks are already done. Do NOT redo them
2. Run the test suite — diagnose what is actually failing. Do not guess
3. Fix only what is broken
4. When all items complete and tests pass: update PROGRESS.md, then output `<promise>TXX_COMPLETE</promise>`

**`retry_prompt_mode=same`** — identical prompt each retry, relying on files on disk for state (Ralph-style).

### Retry Pause

Between retry attempts, The Architect waits `retry_pause` seconds (default: 30). This is skipped when a cooldown wait was triggered (the cooldown wait takes precedence).

### What Triggers a Retry

A task is retried when:
- Completion was not detected (based on multi-signal rules in `is_task_complete()`)
- The circuit breaker state is CLOSED or HALF_OPEN (if CB is present)
- Attempts remain (`attempt < max_retries`)

---

## 11. Circuit Breaker

The circuit breaker is a per-task failure pattern detector that runs **alongside** the retry logic. While retries handle model failures, the circuit breaker catches patterns retries cannot detect:

### States

| State | Meaning |
|-------|---------|
| **CLOSED** | Normal operation — monitoring for failure patterns |
| **OPEN** | Failure pattern detected — task skipped, recovery action chosen |
| **HALF_OPEN** | After circuit cooldown, one test attempt is allowed |

### What Transitions CLOSED → OPEN

Any of these (when threshold > 0):

1. **No-progress**: `consecutive_no_progress >= circuit_no_progress_threshold` (default: 3)
2. **Same-error**: `consecutive_same_error >= circuit_same_error_threshold` (default: 3)
3. **Token decline + corroboration**: token decline > `circuit_token_decline_pct` (default: 60%) **AND** at least one other counter is elevated

### Recovery Actions

| Action | When Triggered | Behaviour |
|--------|----------------|-----------|
| **WAIT** | Retry models still available, or some file progress was made | Normal retry/model rotation continues |
| **REPLAN** | All models exhausted, zero file progress ever | Architect rewrites the failing task only |
| **COOLDOWN_WAIT** | Provider rate limit signal detected | Pause 1 hour, then retry. No retry slot consumed |

### HALF_OPEN

After the circuit opens, it waits `circuit_cooldown_minutes` (default: 30 minutes). Then transitions to HALF_OPEN, which allows exactly **one** test attempt. If that attempt succeeds → CLOSED. If it fails → OPEN again (with `opened_at` reset to now).

### Replan — Targeted Task Rewrite

When recovery is REPLAN, The Architect sends a **targeted replan instruction** to the architect agent (not a full project replan):

```
=== TARGETED TASK REPLAN ===

Task T03 has failed repeatedly and the circuit breaker has opened.
Your job is to FIX THIS ONE TASK ONLY — do NOT replan the entire project.
Do NOT modify any other task files.

=== ORIGINAL TASK FILE CONTENT ===
[content of T03]

=== WHAT WAS TRIED AND WHAT WENT WRONG ===
no_progress_count=3, same_error_count=3, last_error_fingerprint=...

=== CURRENT PROGRESS.MD ===
[last 3000 chars of PROGRESS.md]

Instructions:
1. Analyse why the task is failing
2. Either rewrite T03 with corrected assumptions, OR split T03 into two smaller tasks
3. Write to tasks/ directory
4. Do NOT change any other task files
5. Do NOT rewrite PROGRESS.md — The Architect will handle that.
```

Replan is only attempted **once** per task — `replan_attempted` is set to True in circuit state and never reset except by manual reset or task success.

### State Persistence

Circuit state is persisted to `.architect/circuit.json` after every attempt. This means:
- If the process is killed mid-run, circuit state survives
- If the machine restarts during a cooldown wait, the remaining cooldown time is calculated from the persisted timestamp
- `architect circuit --reset T04` manually resets a task's circuit state to CLOSED

The circuit breaker never crashes the run — all errors are logged and fallen through from.

---

## 12. Rate Limit Detection — Provider Cooldowns and Free Mode

When `cooldown_detection=true` (the default), The Architect detects provider cooldown / rate-limit signals and pauses the run automatically.

### What Is Detected

**HTTP status codes** (from subprocess exit code):
- **429** — Too Many Requests
- **529** — Service Unavailable (used by some providers)

**Text patterns** (case-insensitive substring match in agent accumulated text):
- `"rate limit"`, `"rate_limit"`
- `"too many requests"`
- `"usage limit"`, `"quota exceeded"`, `"quota_exceeded"`
- `"please wait"`, `"try again in"`, `"retry after"`
- `"overloaded"`, `"capacity"`, `"temporarily unavailable"`, `"server is busy"`
- **Claude Code specific:** `"out of extra usage"`, `"usage limit reached"`, `"credit balance is too low"`, `"your account has run out"`, `"exceeded your current quota"`, `"billing hard limit"`

### Suggested Wait Time Extraction

If the provider's message includes a suggested wait time, The Architect extracts it with a regex:

```
"retry after 3600 seconds"   → 3600s
"please wait 1 hour"         → 3600s
"try again in 30 minutes"    → 1800s
"retry after 2h"             → 7200s
```

If the suggested time is **less than 1 hour**, The Architect waits 1 hour anyway (minimum cooldown `_COOLDOWN_MIN_SECONDS = 3600`). If the suggested time is **more than 1 hour**, that longer duration is used.

### Behaviour During Cooldown

1. The run pauses for the cooldown duration
2. **No retry slot is consumed** — the attempt does not count against `max_retries`
3. **No circuit breaker counters are incremented** — the circuit stays CLOSED
4. After the wait, execution resumes automatically from the same attempt
5. The cooldown wait is logged every 60 seconds so you know the run is alive
6. If the process is killed during the cooldown wait and restarted, it resumes from the remaining cooldown time (persisted in `circuit.json`)

The cooldown state fields (`cooldown_waiting`, `cooldown_wait_started_at`, `cooldown_wait_count`) are persisted in the circuit state JSON.

### Mid-Stream Rate Limit Detection

In free mode, rate limits can be detected **mid-attempt** — before the full run completes — by parsing OpenCode's JSON error events as they stream. When a rate limit error event is detected:
1. The current model is marked as exhausted
2. The rotator immediately switches to the next free model
3. The remaining work continues with the new model (no restart needed)

### Disabling Cooldown Detection

```toml
cooldown_detection = false
```

When disabled, rate limit errors fall through to normal circuit breaker evaluation.

### Cooldown Detection with Claude Code

Claude Code outputs plain text. The Architect reads the accumulated text output directly from the stream result (not by re-parsing JSON events from the log file). This means cooldown detection works correctly for both providers.

Common Claude Code quota messages that trigger cooldown detection:
- `"You're out of extra usage · resets 11pm (UTC)"` — daily quota exhausted
- `"Credit balance is too low"` — account credit exhausted
- `"Exceeded your current quota"` — API quota exceeded

---

## 13. Free Mode — Zero-Cost OpenRouter Rotation

> **Note:** Free Mode requires OpenCode with OpenRouter configured. It is not available with Claude Code. See [Section 2](#2-supported-providers--opencode-and-claude-code) for provider differences.

When `--free` is enabled:

1. **Fetches** all models from `https://openrouter.ai/api/v1/models`
2. **Filters** for models where `pricing.prompt == "0"` AND `pricing.completion == "0"` (string zero)
3. **Excludes** non-text-output models (e.g. audio generators like Lyria) — only models whose modality ends with `->text` are included
4. **Sorts** by context length descending — larger context models first (better for coding)
5. **Rotates** through models during execution — switching immediately when a rate limit is detected mid-stream
6. **Rotates** on model-not-found errors — if a free model returns `ProviderModelNotFoundError` or `Model not found`, the rotator skips to the next model (the model is permanently unusable, not just rate-limited)
7. **Exhausts** free models one by one as each hits rate limits or is unavailable
8. **Falls back** to the user's default model from opencode.json when all free models are exhausted

### Model Sorting

Free models are sorted so the most capable (largest context) free models are tried first. The prefix `openrouter/` is added to model IDs for opencode compatibility.

### Rate Limit Detection Patterns (Free Mode)

Used both for mid-stream detection (from JSON error events) and post-run assessment (from accumulated text):

- HTTP 429 exit code
- `"rate limit"`, `"rate_limit"`, `"429"`, `"too many requests"`, `"quota exceeded"`, `"capacity"`, `"overloaded"`, `"temporarily unavailable"`, `"server is busy"`, `"try again later"`

### Model-Not-Found Detection Patterns (Free Mode)

When a free model doesn't exist or isn't available for the requested use, the rotator skips it and moves to the next model:

- `"model not found"`, `"providermodelnotfounderror"`, `"not available for this provider"`, `"invalid model"`, `"unknown model"`

These are permanent failures (the model is unusable), not transient rate limits.

### Free Mode vs Normal Mode

| Aspect | Normal Mode | Free Mode |
|--------|------------|-----------|
| Model selection | `retry_model_2/3` from config | Rotates through OpenRouter free models |
| Rate limit handling | Cooldown wait (1 hour) | Immediate rotation to next free model |
| Model-not-found handling | Fails the attempt | Immediate rotation to next free model |
| Mid-stream detection | Yes (error JSON events) | Yes (same mechanism) |
| Exhausted models | N/A | Falls back to provider's default model |
| Free model count display | N/A | Shown at startup and on dashboard |

### Free Mode Dashboard Info

When free mode is active, the dashboard shows:
- Current free model being used
- Number of free models remaining
- Model rotation count (how many times a model was switched due to rate limit)

---

## 14. Persistent Mode

When `--persistent` is enabled:

| Setting | Normal Default | Persistent Mode |
|---------|--------------|----------------|
| `max_retries` | 3 | **30** |
| `retrospective_rounds` | 1 | **2** |

This is designed for long-running autonomous sessions where you want The Architect to keep trying until the work is genuinely complete — with a deeper second retrospective review pass.

### Full Flow with Persistent Mode

```
Planning → Execution → Retrospective 1 → Execution (R-tasks) → Retrospective 2 → Execution (R-tasks) → Done
```

Persistent mode is available as:
- CLI flag: `architect --persistent`
- Config file: set `persistent = true` in `architect.toml`
- Interactive checkbox in the configure run screen (shown when running `architect` without `--plan`)
- Resume screen checkbox (shown when pending tasks exist from a previous run)

When set via the interactive screen or resume screen, the `persistent` setting is automatically saved to `architect.toml` so it persists across runs.

---

## 15. Headless Mode — CI/Automated Execution

Headless mode skips all interactive prompts. All values must come from flags or environment variables.

```bash
architect --headless --goal "add dark mode" --scope standard
```

### Environment Variable Equivalents

All CLI flags can be set via environment variables, making headless mode fully CI-friendly:

| Variable | Equivalent Flag | Example |
|----------|----------------|---------|
| `ARCHITECT_HEADLESS` | `--headless` | `true` |
| `ARCHITECT_GOAL` | `--goal` | `"add dark mode"` |
| `ARCHITECT_SCOPE` | `--scope` | `standard` |
| `ARCHITECT_CONTEXT` | `--context` | `/path/to/spec.md` (path separator: `:` on Unix, `;` on Windows) |
| `ARCHITECT_PROVIDER` | `--provider` | `claude-code` |
| `ARCHITECT_ARCHITECT_MODEL` | `--architect-model` | `openrouter/anthropic/claude-opus-4.5` |
| `ARCHITECT_EXECUTION_MODEL` | `--execution-model` | `openrouter/google/gemini-2.5-pro` |

### What Is Skipped in Headless Mode

| What | Behaviour |
|------|-----------|
| Mode selection (Free/Persistent) | Skipped — must be set via flags |
| Goal prompt | Skipped — must come from `--goal` or `--context` with goal extraction |
| Scope prompt | Defaults to `standard` |
| Architect model prompt | Uses opencode default |
| Execution agent prompt | Uses opencode default |
| Pending task guard confirmation | Archives automatically with a warning logged |
| Post-run pause ("Press any key") | Skipped |

### Headless + All Tasks Done

When all tasks are already complete (`all_done == True`) and `--plan` is not passed:
- In **interactive mode**: shows a welcome screen with "Start a new goal" or "Exit" options
- In **headless mode**: prints `✓ All tasks complete. Use --plan to start a new goal.` and exits cleanly (exit code 0)

This is the **Premature Exit Guard** — preventing accidental re-Architecting of an already-complete project.

---

## 16. Interactive Screens

The Architect shows different interactive screens depending on the state of the project.

### Configure Run Screen (Fresh Start)

When no pending tasks exist, the configure run screen is shown:

```
 The Architect  configure run

  › [ ] Free Tier        (OpenRouter free models, rotate on rate limit)
    [ ] Persistent       (30 retries, deeper retrospective)
    Token budget/hr: 0  (0 = unlimited)

  ↑↓ navigate   Space toggle   Enter confirm
```

Navigate with ↑/↓, toggle checkboxes with Space, type digits for the budget field, and press Enter to confirm.

> **Provider-aware:** The Free Tier option is only shown when the active provider supports OpenRouter (OpenCode + OpenRouter configured). Claude Code users and OpenCode users without OpenRouter see only Persistent and Token Budget. Token Budget is always shown for all providers.

### Resume Screen (Pending Tasks)

When pending tasks exist from a previous run, the resume screen is shown:

```
 The Architect  resume run

  3 pending tasks to execute
    T01  Fix mypy errors
    T02  Update README
    T03  Add dark mode

  Settings
  › [ ] Free Tier        (OpenRouter free models)
    [x] Persistent       (30 retries, deeper retrospective)
    Token budget/hr: 500000  (0 = unlimited)

  Replan               (start fresh with a new goal)
  Execute              (continue running pending tasks)
```

- **Execute** (default) — press Enter to continue running pending tasks with the same settings
- **Replan** — navigate to Replan and press Enter/Space to discard old tasks and start planning fresh
- **Cancel** — Ctrl+C to exit without doing anything

Settings are pre-filled from `architect.toml` (saved from the previous run). Change them or press Enter to accept as-is.

> **Provider-aware:** Same as the configure run screen — Free Tier is only shown when the active provider supports OpenRouter. Token Budget is always shown.

### Provider Selection Screen

When both OpenCode and Claude Code are installed and `provider = "auto"` (default), a provider selection screen is shown before any other prompts:

```
 The Architect  select provider

  Both OpenCode and Claude Code are installed.
  Select which provider to use for this run.

  › OpenCode  (v1.4.0)
    Claude Code  (v1.2.3)

  ↑↓ navigate   Enter confirm
```

The selection is saved to `ARCHITECT_PROVIDER` env var and forwarded into the tmux session so it is not asked again during the same run.

### Settings Persistence

When settings are changed via either interactive screen, they are automatically saved to `architect.toml`:

- `free_mode`
- `persistent`
- `token_budget_per_hour`

This means the next run pre-fills the same settings — no need to re-enter them.

### When Screens Are Skipped

| Scenario | Screen shown |
|----------|-------------|
| No tasks (fresh start) | Configure run screen |
| Pending tasks exist | Resume screen |
| All tasks Done | "All complete" message |
| `--plan` flag | Skip all screens, go straight to planning |
| `--headless` flag | Skip all screens, use config/flags |
| `--persistent` or `--free` | Skip interactive screen, use flag values |
| `--only` or `--from` | Skip resume screen, run specified tasks |

---

## 17. Token Budget

The optional hourly token budget prevents runaway API costs:

```toml
[architect]
token_budget_per_hour = 500000    # Max tokens per rolling hour (0 = disabled)
```

Token budget can be set in three ways:
- **Interactive prompt** — the configure run screen asks for token budget per hour (default: `0` = unlimited). Shown when running `architect` without mode flags.
- **Resume screen** — the budget field is pre-filled from the previous run's setting. Change it or leave it as-is.
- **Config file** — set `token_budget_per_hour` in `architect.toml`
- **CLI command** — `architect config --set token_budget_per_hour=500000`

When set via the interactive screen or resume screen, the token budget is automatically saved to `architect.toml` so it persists across runs.

### How It Works

The `HourlyTokenBudget` class tracks usage against a rolling hour window:

1. A **rolling hour window** starts when the first tokens are recorded
2. After each task completes, tokens are added via `add()`
3. If `exceeded()` returns True, The Architect **pauses** until the window resets
4. The window resets **automatically** after 1 hour elapses (tracked via `time.monotonic()`)
5. After the wait, execution resumes with the next task

### Key Methods

- `add(tokens)` — Record tokens used; auto-resets window if 1 hour has elapsed
- `exceeded()` — True when tokens this hour exceed the budget
- `seconds_until_reset()` — Seconds remaining until the window resets
- `wait_for_reset()` — Async pause until reset; logs progress every 60 seconds

### Budget Behaviour

- Budget pauses **do not consume retry slots**
- Budget pauses **do not affect circuit breaker state**
- A single Claude call can use 100k+ tokens — set to e.g. `500000` for a ~5-call-per-hour budget
- When `token_budget_per_hour = 0` (default), the tracker is fully disabled

### Use Cases

- **Cost control** — prevent a long run from exceeding a monthly budget
- **Shared API accounts** — limit usage when others share the same API key
- **CI environments** — ensure deterministic cost per pipeline run

---

## 18. Retrospective Review

After execution completes, The Architect runs retrospective review rounds using the **reviewer agent** (defined in `resources/prompts/reviewer.md`):

```
Execution → Retrospective 1 → Execution (R-tasks) → Retrospective 2 → Execution (R-tasks) → Done
```

### What the Reviewer Does

The reviewer is a **supervisor and advisor — not a planner**. It:

1. Reads `PROGRESS.md` to understand what was done and what failed (full content, not just summary)
2. Reads all task files in `tasks/` to understand what was planned
3. Reads the actual code that was written or modified
4. **Runs the test suite** (`pytest` or equivalent) to verify everything passes
5. Assesses: completeness, quality, tests, consistency, correctness
6. Creates **R-prefixed fix-up task files** only if issues are found
7. If everything is clean: writes no task files at all

### Reviewer Context Budget

The reviewer receives more context than the planner:
- **12,000 character budget** (vs 8,000 for planner) because the reviewer needs to read code
- Full `PROGRESS.md` content (including failed state, not just historical summary)
- All task file names and headings
- File tree (filtered, no __pycache__, .git, etc.)
- Original goal

### How Fix-Up Tasks Work

After the reviewer creates R-prefixed tasks:

1. Tasks are **discovered** (scanned from `tasks/`)
2. `PROGRESS.md` is **updated** via `_update_progress_with_retrospective_tasks()` — new R tasks added as Pending rows, "Next task to run" updated to the first R task if no other pending tasks exist
3. The R tasks are **executed** automatically in the next execution round
4. After execution, the **next retrospective round** runs (if `retrospective_rounds > 1`)

### Rounds Configuration

| Config Value | Rounds | Flow |
|-------------|--------|------|
| `retrospective_rounds = 0` | None | Retrospective disabled |
| `retrospective_rounds = 1` (default) | 1 | Execution → Review → Done |
| `retrospective_rounds = 2` (--persistent) | 2 | Execution → Review → Fix → Review → Fix → Done |

### Retrospective Skip Conditions

Retrospective is **skipped** when:
- `retrospective_rounds = 0`
- `--only` flag was used (targeted single-task run)
- The reviewer finds **no issues** (no new R tasks created) — remaining rounds are skipped immediately

### Retrospective Request Model

```python
class RetrospectiveRequest(BaseModel):
    round_number: int          # 1-based round number
    project_dir: Path
    original_goal: str         # User's original goal for context
    model_override: str | None # Optional explicit model for reviewer
```

### What the Reviewer Is NOT Allowed to Do

- Write `PROGRESS.md` or `INSTRUCTIONS.md` (The Architect handles these)
- Modify existing T-prefixed task files
- Write task files if no issues are found (write nothing — silence is success)

---

## 19. Premature Exit Guard

The Premature Exit Guard prevents The Architect from accidentally re-planning an already-complete project.

### The Problem

If all tasks in `PROGRESS.md` are marked Done, and a user runs `architect` (without `--plan`), what should happen?

### The Guard Behaviour

**Interactive mode** (default):
- Shows a welcome screen: "✓ All tasks complete"
- Offers two choices: "Start a new goal — plan something new" or "Exit"
- User must explicitly choose to plan

**Headless mode** (`--headless`):
- Prints: `✓ All tasks complete. Use --plan to start a new goal.`
- Exits with code 0
- Does NOT start planning

**With `--plan` flag** (any mode):
- Always forces planning mode, even if all tasks are done

### Decision Logic

```python
all_done = bool(tasks) and all(t.status == TaskStatus.DONE for t in tasks)
no_tasks = not tasks

if plan or no_tasks or (all_done and not only_task and not from_task):
    # → Enter planning mode
    if all_done and not plan:
        # Interactive: ask user | Headless: exit with message
```

### Pending Task Guard (Related Safeguard)

Before starting a **new** plan (even when all current tasks are done), The Architect checks for unfinished tasks from the previous session:
- **Interactive mode**: Asks "Start a new goal anyway? (previous tasks will be archived)"
- **Headless mode**: Archives automatically with a warning log message

This prevents accidentally starting a new goal on top of incomplete work.

### Other Safeguards

1. **Task file numbering** — task numbers are never reused within a session; new plan always starts from the next available number
2. **PROGRESS.md active state excluded from planning context** — the architect cannot "see" "Next task to run" or "Current State", preventing confusion about whether to continue vs. start fresh
3. **AGENTS.md ownership** — The Architect reads AGENTS.md for context but never writes it; it belongs to the user

---

## 20. Lock File — Preventing Concurrent Runs

The Architect uses a lock file at `.architect/runner.lock` to prevent concurrent runs of The Architect on the same project.

### How It Works

1. **Atomic creation**: Uses `os.open(path, O_CREAT | O_EXCL | O_WRONLY)` — the file is only created if it doesn't exist. If it already exists, `FileExistsError` is raised immediately (no TOCTOU race)
2. **PID tracking**: The lock file contains the current process ID
3. **Stale lock detection**: On acquisition failure, The Architect reads the PID and calls `os.kill(pid, 0)` — if the process is gone (raises `ProcessLookupError` or `OSError`), the lock is stale and automatically removed, then acquisition is retried once

### Manual Cleanup

```bash
architect cancel   # Remove stale lock and optionally terminate running process
```

If a lock exists and the process is still alive, `cancel` asks whether to send SIGTERM to stop it gracefully.

### Lock File Contents

The lock file is just a plain text file containing the PID as a string (e.g., `12345`). No other metadata is stored.

---

## 21. Configuration

The Architect is zero-config by default. All settings have sensible defaults.

### architect.toml

Create `architect.toml` in your project root:

```toml
[architect]
# ── Directories ────────────────────────────────────────────────────────────────
tasks_dir = "tasks"                  # Directory containing task files
progress_file = "PROGRESS.md"        # Path to progress tracker
log_dir = ".architect/logs"          # Directory for log files

# ── Retry Settings ────────────────────────────────────────────────────────────
max_retries = 3                      # Maximum retry attempts per task
retry_pause = 30                     # Seconds to wait between retries
pause_between_tasks = 10             # Seconds to wait between tasks

# ── Model Fallbacks ──────────────────────────────────────────────────────────
retry_model_2 = ""                   # Fallback model for attempt 2
                                       # e.g. "openrouter/anthropic/claude-sonnet-4"
retry_model_3 = ""                  # Fallback model for attempt 3

# ── Provider ────────────────────────────────────────────────────────────────
provider = "auto"                    # "auto" | "opencode" | "claude-code"

# ── Execution ───────────────────────────────────────────────────────────────
execution_agent = ""                 # Agent name from opencode.json (empty = default)
standalone_mode = ""                 # Use this model directly (bypasses provider config)
                                       # e.g. "openrouter/anthropic/claude-sonnet-4.5"

# ── Retry Prompt Style ───────────────────────────────────────────────────────
carry_context = true                 # Inject previous attempt context on retry
retry_prompt_mode = "focused"       # "focused" (structured) or "same" (identical prompt)

# ── Retrospective ────────────────────────────────────────────────────────────
retrospective_rounds = 1             # Review rounds after execution (0 = disabled)

# ── Free Mode ────────────────────────────────────────────────────────────────
free_mode = false                    # Use free OpenRouter models, rotate on rate limit

# ── Persistent Mode ──────────────────────────────────────────────────────────
persistent = false                   # 30 retries, 2 retrospective rounds

# ── Circuit Breaker ──────────────────────────────────────────────────────────
circuit_no_progress_threshold = 3    # Zero-file-writes attempts before trip (0=off)
circuit_same_error_threshold = 3     # Same-error attempts before trip (0=off)
circuit_token_decline_pct = 60      # Token decline % to trip (0=off)
circuit_cooldown_minutes = 30        # Wait before HALF_OPEN retry
circuit_enable_replan = true         # Allow REPLAN recovery action

# ── Cooldown Detection ────────────────────────────────────────────────────────
cooldown_detection = true            # Detect and wait on provider rate limits

# ── Token Budget ──────────────────────────────────────────────────────────────
token_budget_per_hour = 0           # Max tokens/rolling hour (0 = disabled)
                                       # e.g. 500000 for ~5-Claude-call-per-hour budget
```

### All Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `tasks_dir` | Path | `tasks` | Directory containing task files |
| `progress_file` | Path | `PROGRESS.md` | Path to progress tracker |
| `log_dir` | Path | `.architect/logs` | Directory for log files |
| `max_retries` | int | `3` | Maximum retry attempts per task |
| `retry_pause` | int | `30` | Seconds to wait between retries |
| `pause_between_tasks` | int | `10` | Seconds to wait between tasks |
| `retry_model_2` | str | `""` | Fallback model for attempt 2 |
| `retry_model_3` | str | `""` | Fallback model for attempt 3 |
| `provider` | str | `"auto"` | AI CLI provider: `"auto"`, `"opencode"`, or `"claude-code"` |
| `execution_agent` | str | `""` | Agent name for task execution (OpenCode only) |
| `standalone_mode` | str | `""` | Use this model directly (bypasses provider config) |
| `carry_context` | bool | `true` | Inject previous attempt context on retry |
| `retry_prompt_mode` | str | `"focused"` | `"focused"` (structured) or `"same"` (identical) |
| `retrospective_rounds` | int | `1` | Retrospective review rounds (0 = disabled) |
| `free_mode` | bool | `false` | Use free OpenRouter models |
| `persistent` | bool | `false` | Persistent mode (30 retries, 2 retrospective rounds) |
| `circuit_no_progress_threshold` | int | `3` | No-progress threshold (0 = disabled) |
| `circuit_same_error_threshold` | int | `3` | Same-error threshold (0 = disabled) |
| `circuit_token_decline_pct` | int | `60` | Token decline % to trip (0 = disabled) |
| `circuit_cooldown_minutes` | int | `30` | Circuit cooldown before HALF_OPEN |
| `circuit_enable_replan` | bool | `true` | Allow REPLAN recovery action |
| `cooldown_detection` | bool | `true` | Detect and wait on provider rate limits |
| `token_budget_per_hour` | int | `0` | Max tokens per rolling hour (0 = disabled) |

### Config CLI

```bash
# Show current configuration (source: architect.toml or defaults)
architect config

# Update a config value (writes to architect.toml)
architect config --set max_retries=5
architect config --set carry_context=false
architect config --set retry_model_2="openrouter/google/gemini-2.5-pro"
architect config --set circuit_no_progress_threshold=5
architect config --set token_budget_per_hour=500000
```

### `architect init`

Creates `AGENTS.md` and `architect.toml` in the project directory:

```bash
architect init                  # Create with defaults
architect init --force          # Overwrite existing files
```

---

## 22. Task Files

Tasks are Markdown files in `tasks/` with a specific naming format:

```
tasks/
├── T01_init.md
├── T02_feature.md
├── T03_api.md
├── R01_fix_tests.md         ← retrospective fix-up task
├── INSTRUCTIONS.md         ← project context (auto-generated)
└── archive/
    └── 2026-04-12_143000/  ← previous run archived
        ├── T01_old.md
        ├── T02_old.md
        └── INSTRUCTIONS.md ← plan context from previous run
```

### Naming Convention

| Prefix | Type | Created By |
|--------|------|-----------|
| `T01`, `T02`, … | Normal tasks | Architect agent during planning |
| `R01`, `R02`, … | Retrospective fix-up tasks | Reviewer agent during retrospective |
| `S01`, `S02`, … | Standalone / special tasks | Reserved for manual use |

Numbers are sequential and never reused within a planning session.

### Task File Format

```markdown
# T01 — Feature Name

## Goal
Brief description of what this task accomplishes.

## Context
Any relevant background information.

## Tasks

### T01.1 — Do the first thing
- Step 1
- Step 2

### T01.2 — Do the second thing
- Another step
```

### INSTRUCTIONS.md

Auto-generated after planning by The Architect (not by the architect agent). Contains:
- The original goal
- Stack information
- Architecture notes
- Conventions and constraints
- Task list with scope

The execution agent reads this file for project context before starting each task.

### Archive

When a new planning session starts, previous task files and `INSTRUCTIONS.md` are moved to `tasks/archive/YYYY-MM-DD_HHMMSS/` — history is preserved but the new session starts clean. A fresh `INSTRUCTIONS.md` is generated for the new plan.

### Stray Task File Rescue

The architect model sometimes writes task files into a subdirectory mentioned in the goal (e.g., `mbi/tasks/T01_foo.md`) instead of the canonical `tasks/` directory. After planning, The Architect scans the entire project tree for files matching `TXX_*.md`, `RXX_*.md`, or `SXX_*.md` that are outside `tasks/`, and moves them to the canonical location automatically. Conflicting filenames are skipped.

---

## 23. PROGRESS.md

`PROGRESS.md` is The Architect's persistent memory between runs. It tracks which tasks are complete and what to run next.

### Format

```markdown
# The Architect — Progress Tracker

> This file is the memory between tasks.
> Every task MUST read this at the start and rewrite it completely at the end.

---

## Overall Status

**Tasks completed:** 3
**Next task to run:** T04

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | Init | Done | 2026-04-12 |
| T02 | Core | Done | 2026-04-12 |
| T03 | API  | Done | 2026-04-12 |
| T04 | Frontend | Pending | |

---

## Current State

T03 complete. API routes implemented and tested.

## Last Task Summary

Created API routes with full test coverage...

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
| database | SQLite | Lightweight, no server needed | T01 |
```

### How The Architect Reads PROGRESS.md

The runner detects task state from exactly two lines:

```
**Tasks completed:** N
**Next task to run:** TXX
```

Task Done status is detected by grepping: `TXX.*Done`

Both must be present and correctly formatted. The Architect **always** writes PROGRESS.md itself — never delegated to the architect agent, guaranteeing it is always at the correct project-root path.

### Ownership Rules

| File | Owner | Notes |
|------|-------|-------|
| `PROGRESS.md` | The Architect | Always written by The Architect |
| `tasks/INSTRUCTIONS.md` | The Architect | Auto-generated, never by the architect agent |
| `ARCHITECT.md` | The Architect + append-only sections | Structure section rewritten; others append-only |
| `AGENTS.md` | The User | The Architect reads it but never writes it |
| `TXX_*.md` task files | Architect agent | Created during planning |

---

## 24. SUCCESS.md — Run Summary

After every run, The Architect writes `SUCCESS.md` to the project root with a complete summary of what happened.

### Format

```markdown
# The Architect — Run Summary

**Date:** 2026-04-19 14:30
**Duration:** 43:59
**Result:** ✓ All tasks completed

## Tasks

| Task | Title | Status | Attempts | Model | Duration | Tokens |
|------|-------|--------|----------|-------|----------|--------|
| T01 | Fix mypy type error | ✓ Done | 1 | claude-sonnet-4 | 2:11 | 8.5K |
| T02 | Fix README branding | ✓ Done | 3 | claude-sonnet-4 | 5:33 | 2.6K |
| R01 | Fix missing test edge case | ✓ Done | 1 | claude-sonnet-4 | 1:45 | 3.1K |

## Totals

- **Tasks:** 12/12 done
- **Duration:** 43:59
- **Total tokens:** 50.8K
- **Token breakdown:** input 207 · output 50.6K · cache read 5210.4K · cache write 343.7K
- **Models:** anthropic/claude-sonnet-4
- **Retries:** 2 across 12 tasks
- **Rate limits hit:** 1 (T05)

## Retrospective

| Round | Issues Found | Fix-up Tasks | Duration |
|-------|-------------|-------------|----------|
| 1 | 2 | R01, R02 | 1:30 |
| 2 | 0 | — | 0:45 |

## Insights

- **Avg duration per task:** 2:42
- **Slowest task:** T07 Complex refactor (8:15)
- **Avg tokens per task:** 4.2K
- **Throughput:** 1.2K tokens/min
- **Most tokens:** T01 Setup (12.3K)
- **Most retries:** T02 Fix README branding (3 attempts)
```

### Task Table Columns

| Column | Meaning |
|--------|---------|
| `Task` | Task ID — T01, T02, … for planned tasks; R01, R02, … for retrospective fix-up tasks |
| `Title` | Task description from the task file |
| `Status` | `✓ Done`, `✗ Failed`, or `○ Skipped` |
| `Attempts` | How many tries the task took (1 = first attempt success) |
| `Model` | Which AI model was used (provider prefix stripped) |
| `Duration` | Wall-clock time for that task |
| `Tokens` | Input + output tokens for that task (cache tokens excluded) |

### Totals Section

| Field | Meaning |
|-------|---------|
| `Total tokens` | Input + output only — the "thinking work" number |
| `Token breakdown` | input, output, cache read (~10% price), cache write (~125% price, one-time) |
| `Retries` | Total attempts minus total tasks — how much extra work was needed |
| `Rate limits hit` | Which tasks were rate-limited (triggers model rotation in free mode) |

### Retrospective Section

Only present when retrospective rounds were configured. Shows what the reviewer found and what fix-up tasks were created. If a round shows `—` for fix-up tasks, the reviewer found no issues.

### Token Explanation

`Total tokens` = input + output only. Cache tokens are **not** included because they represent infrastructure efficiency, not thinking work. A large `cache read` number is **good** — it means the system is efficiently reusing context instead of re-sending it fresh.

---

## 25. ARCHITECT.md — Persistent Project Intelligence

`ARCHITECT.md` is The Architect's long-term memory for a specific project. It accumulates knowledge across all planning sessions and execution cycles. It is **read at the start of every planning session and every task execution**, and updated by the planner, the build agent, and the reviewer.

### Sections

The file is organized into append-only sections (except Project Structure):

| Section | Managed By | Update Frequency |
|---------|-----------|-----------------|
| **Project Structure** | The Architect | Rewritten fresh on every `--plan` |
| **Permanent Decisions** | Append-only | New entries added during planning and execution |
| **Known Constraints** | Append-only | New entries added during execution and retrospective |
| **Lessons Learned** | Append-only | Discovered during execution and retrospective |
| **Best Practices** | Append-only | Emerged patterns from the codebase |
| **Planning History** | Append-only | One row per planning session (auto-appended) |

### Project Structure Section

Written fresh on every `--plan`. Contains:
- Repo type (single repo, monorepo, multi-repo, untracked)
- Detected components as rich blocks — each showing:
  - Language, framework, and inferred role
  - Project description (from `pyproject.toml` or `package.json`)
  - Key dependencies (top 8, build/test tooling filtered out)
  - Test and lint commands
  - Sub-components (one level deep, e.g. `app/backend`, `app/frontend`)
- Dependency graph (from docker-compose, package.json, Cargo.toml, pyproject.toml)
- Shared resources

### How ARCHITECT.md Flows Through The System

**Planning phase:**
1. ARCHITECT.md content is injected into the architect agent's planning prompt (highest priority context)
2. The architect agent is instructed to update ARCHITECT.md with new decisions and constraints discovered during planning
3. The structure section is rewritten fresh
4. Planning history is **auto-appended** by The Architect after each successful plan (using `append_planning_history()`)

**Execution phase:**
1. ARCHITECT.md content is injected into every build agent's execution instruction
2. The build agent reads it for project context (decisions, constraints, lessons, stack info)
3. The build agent is instructed to update ARCHITECT.md with new constraints, lessons, or decisions discovered during execution

**Retrospective phase:**
1. The reviewer agent is instructed to update ARCHITECT.md with cross-task patterns, repeated failures, and quality issues discovered during review

### Atomic Writes

All writes to ARCHITECT.md use atomic writes (temp file + `os.replace`) so readers never see partial content.

### What Gets Appended

During planning, execution, and retrospective, entries are appended to:

- **Permanent Decisions**: Architecture choices (e.g., "database: SQLite — because lightweight, no server needed")
- **Known Constraints**: Constraints discovered during work (e.g., "must use Python 3.9+ for compatibility")
- **Lessons Learned**: What went wrong and why (e.g., "don't skip tests — T02 failed because tests weren't run")
- **Best Practices**: Patterns that emerged (e.g., "always write tests before implementing feature")
- **Planning History**: One row per plan with date, goal, task count, and notes (auto-appended after each plan)

---

## 26. tmux Dashboard — Live Monitoring

When **tmux** is installed and you are not already inside a tmux session, The Architect automatically opens a split-pane session:

```
┌─────────────────────────────────────┬─────────────────────────────────┐
│                                     │ THE ARCHITECT                   │
│   opencode live output              │─────────────────────────────────│
│   streams here in real-time         │ TASKS                           │
│                                     │ ✓ T01 Setup (done)              │
│   ══ T02  Build API  (2/3 remain) │ ● T02 Build API (RUNNING)      │
│   ⠋  starting T02…                │ ○ T03 Frontend (pending)        │
│                                     │─────────────────────────────────│
│   [opencode output scrolls here]    │ STATUS                          │
│                                     │ Task: T02 / 3                  │
│                                     │ Status: RUNNING                │
│                                     │ Attempt: 1 / 3                 │
│                                     │─────────────────────────────────│
│                                     │ CIRCUIT                         │
│                                     │ State: CLOSED                   │
│                                     │ No-progress: 0/3                │
│                                     │ Same-error: 0/3                │
│                                     │─────────────────────────────────│
│                                     │ TOKENS                          │
│                                     │ Session: 24.5K                  │
│                                     │ Last task: 8.2K                 │
└─────────────────────────────────────┴─────────────────────────────────┘
```

### How It Works

The runner writes a state file at `.architect/monitor_state.json` after every significant event (task start, task done, attempt start, attempt done, circuit state change, cooldown start/end, model rotation, replan). The dashboard process reads this file every 2 seconds and renders the live view.

Writes are **atomic** (temp file + rename) so the dashboard never reads a partial file.

### Dashboard Features

- Live streaming of opencode output (left pane)
- Color-coded task states (`✓ Done`, `● Running`, `○ Pending`)
- Circuit breaker state in real-time (CLOSED / OPEN / HALF_OPEN + counters)
- Cooldown wait countdown when active (e.g., "Cooldown: 2,847s remaining")
- Model rotation counter in free mode ("Free model 3/12")
- Token usage tracking (session total + last task)
- Graceful stop / kill flag monitoring

### Monitor State Writer

`MonitorStateWriter` is the class responsible for writing state. It receives callbacks from the runner:
- `on_task_start`, `on_task_done`, `on_task_failed`
- `on_attempt_start`, `on_attempt_done`
- `on_cooldown_start`, `on_cooldown_end`
- `on_circuit_state_change`
- `on_model_rotated`, `on_replan`, `on_replan_done`
- `on_run_done`, `on_graceful_stop_requested`, `on_killed`

All writes are best-effort — failures are logged at debug level and silently swallowed.

### tmux Controls

- **Detach** from the session: press `Ctrl+B` then `D`
- **Reattach**: `tmux attach-session -t architect-<project-name>`
- **List sessions**: `tmux ls | grep architect`

### tmux Auto-Install

If tmux is not installed, The Architect offers to install it automatically using your system's package manager (`apt`, `brew`, `pacman`, `dnf`, `apk`, `zypper`, `nix-env`, `port`, `choco`, `winget`, `scoop`). If the install fails, a one-time hint is shown and the run continues in the current terminal.

### Own-Window Fallback

When tmux is unavailable and a GUI is detected, The Architect can launch itself in a new terminal window (gnome-terminal, konsole, kitty, alacritty, xfce4-terminal, mate-terminal, xterm, or macOS Terminal/iTerm2). This is also best-effort — failures are silently swallowed.

### No Monitor Mode

Use `--no-monitor` to skip all tmux and window-launching logic. The Architect runs in the current terminal with no dashboard.

---

## 27. Error Handling

The Architect handles failures robustly at every layer:

| Scenario | Handling |
|----------|----------|
| **No provider installed** | Detects and shows install instructions for both OpenCode and Claude Code |
| **Provider not configured** | Shows setup guidance with config file locations for the active provider |
| **`--free` with Claude Code** | Warning shown, flag cleared, `free_mode=false` saved to `architect.toml` |
| **Concurrent runs** | Lock file prevents multiple instances |
| **Stale lock file** | Detects dead PID, removes automatically; `architect cancel` for manual cleanup |
| **Interrupted runs** | Ctrl+C (SIGINT) or SIGTERM triggers clean shutdown; lock released |
| **Malformed PROGRESS.md** | Safe defaults, never crashes |
| **Non-writable directories** | Clear error messages |
| **Model fallbacks** | Automatic retry with `retry_model_2/3` |
| **Rate limits (free mode)** | Immediate model rotation, no restart needed |
| **Rate limits (normal)** | Cooldown wait (1 hour), no retry slot consumed |
| **Subprocess failures** | Catch-all exception handling, process cleanup, error logging |
| **Stdout buffer overflow** | 10 MB read limit; graceful stop on LimitOverrunError |
| **Stray task files** | Automatically rescued from subdirectories |
| **Circuit breaker errors** | Never crash the run — logged and fallen through from |
| **Retrospective failure** | Logged, execution continues |
| **Summary write failure** | Logged, execution completes normally |
| **Non-interactive terminal** | Detects and skips UI elements that require interactivity |

### Graceful Stop

When you press `Ctrl+C` during execution:
1. The current task attempt is interrupted
2. The lock file is released
3. The tmux session is cleaned up
4. A partial SUCCESS.md is written if possible

The stop is "graceful" — no lock file is left behind, no tmux session is orphaned.

### Dashboard Stop Flags

Two flag files enable external process control:
- `.architect/monitor_stop.flag` — requests graceful stop after current task
- `.architect/monitor_kill.flag` — requests immediate kill

### tmux Session Teardown

When The Architect launches itself inside tmux (auto-launch), it kills the tmux session when the run ends so the user lands back in their original terminal cleanly. Without this, the user would be left inside a dead tmux session. This only kills sessions matching The Architect's naming convention (`architect-<project-name>`).

---

## 28. Project Structure — What The Architect Creates

```
your-project/
├── tasks/                    # Task files (created by architect)
│   ├── T01_init.md
│   ├── T02_feature.md
│   ├── INSTRUCTIONS.md       # Project context (auto-generated)
│   └── archive/              # Previous run archives
│       └── 2026-04-12_143000/
│           ├── T01_old.md
│           └── INSTRUCTIONS.md  # Plan context from previous run
├── .architect/
│   ├── architect.json        # The Architect's planning config (architect + reviewer agents)
│   ├── prompts/             # Agent prompts (written from resources)
│   │   ├── architect.md
│   │   ├── reviewer.md
│   │   └── execution-protocol.md
│   ├── logs/                # Task execution logs
│   │   ├── the_architect.log  # General log
│   │   ├── architect.log    # Planning session transcript
│   │   ├── reviewer_round1.log  # Retrospective transcript
│   │   └── T01.attempt2.log # Per-attempt execution logs
│   ├── circuit.json          # Circuit breaker state (persisted)
│   ├── monitor_state.json    # Dashboard state (updated every event)
│   ├── runner.lock          # Lock file (prevents concurrent runs)
│   ├── monitor_stop.flag    # Graceful stop flag (Ctrl+C)
│   └── monitor_kill.flag    # Immediate kill flag
├── PROGRESS.md              # Task state tracker
├── SUCCESS.md               # Final run summary (auto-generated)
├── ARCHITECT.md             # Project architecture memory
└── architect.toml           # Optional configuration
```

### Package Layout

```
the_architect/              # Python package (published to PyPI as "the-architect")
├── __init__.py            # Package init, exports __version__
├── cli.py                 # CLI entry point (click commands)
├── config.py              # architect.toml loading + ArchitectConfig
├── exceptions.py          # Custom exceptions
├── version.py             # Version resolution
├── core/
│   ├── architect_md.py          # ARCHITECT.md read/write + append helpers
│   ├── circuit.py               # Circuit breaker (CLOSED/OPEN/HALF_OPEN per task)
│   ├── claude_code_provider.py  # Claude Code CLI provider implementation
│   ├── context.py               # Context file/directory loading + goal extraction
│   ├── dashboard.py             # tmux dashboard renderer (separate process)
│   ├── free_models.py           # Free-tier OpenRouter model rotator (OpenCode only)
│   ├── monitor_state.py         # Monitor state writer (feeds dashboard)
│   ├── opencode_config.py       # Backward-compat shim (delegates to opencode_provider.py)
│   ├── opencode_provider.py     # OpenCode CLI provider implementation
│   ├── planner.py               # Planning via provider architect agent
│   ├── progress.py              # PROGRESS.md read/write + status helpers
│   ├── provider.py              # ArchitectProvider protocol + detect_provider()
│   ├── retrospective.py         # Retrospective reviewer runner
│   ├── runner.py                # Task execution engine (stream_provider, run_task, run_all)
│   ├── structure.py             # Project structure detection (repo type, framework, deps)
│   ├── success.py               # SUCCESS.md generation + terminal summary
│   ├── tasks.py                 # Task discovery and state
│   └── tmux.py                  # tmux session management + dashboard launcher
└── resources/
    ├── opencode_template.json  # OpenCode planning config (architect + reviewer agents)
    └── prompts/
        ├── architect.md        # Architect agent prompt (used by both providers)
        ├── reviewer.md         # Retrospective reviewer agent prompt (used by both providers)
        └── execution-protocol.md  # Execution protocol (injected at runtime)
```

---

## 29. Dependencies

```toml
[project]
dependencies = [
    "questionary>=2.0.0",   # Interactive CLI prompts (arrow-key menus)
    "loguru>=0.7.0",       # Logging
    "rich>=13.0.0",        # Terminal formatting and tables
    "click>=8.0.0",        # CLI framework
    "pydantic>=2.0.0",     # Config validation and models
    "httpx>=0.27.0",       # HTTP client (OpenRouter free model fetching)
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
    "mypy>=1.0",
    "ruff>=0.4",
]
```

TOML parsing uses the built-in `tomllib` (Python 3.11+). `tomli` is intentionally
not a dependency.

**No Anthropic SDK. No OpenAI SDK. No direct AI API calls. Everything goes through the provider CLI (OpenCode or Claude Code).**

---

## Credits

The Architect is built on:

- [OpenCode](https://opencode.ai) — Autonomous coding agent
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — Anthropic's official AI coding CLI
- [Rich](https://github.com/Textualize/rich) — Terminal formatting
- [questionary](https://github.com/tmbo/questionary) — Interactive terminal prompts
- [Click](https://click.palletsprojects.com/) — CLI framework
- [Pydantic](https://docs.pydantic.dev/) — Data validation
- [Loguru](https://loguru.readthedocs.io/) — Logging

## License

MIT License