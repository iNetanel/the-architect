# The Architect — Concepts

> The "why" behind how The Architect works.
> For full technical detail on any system, see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## It Doesn't Write Code

This is the first thing people get wrong. The Architect never writes a single line of your application code. It doesn't call any AI API directly — no OpenAI SDK, no Anthropic SDK, no Google AI SDK. It shells out to your existing AI coding CLI and orchestrates it. The AI writes the code. The Architect makes sure the AI finishes the job, doesn't get stuck, and doesn't hallucinate success.

Think of it as a project manager for AI agents, not an AI agent itself.

---

## The Build Counter Is a Lie Detector

Most version numbers only change on release day. The Architect's build counter (`__build__`) increments on every single completed task — a typo fix, a new feature, a refactoring, a documentation update. It never resets. It's a monotonic, honest record of cumulative effort.

When you see `v1.0.0 (build 10042)`, you know exactly how many operations it took to get from `v1.0.0` to here. No invisible work. No silent refactors. Every change leaves a number.

The build counter is also the project's immune system against lazy AI agents. If an agent forgets to bump the build, the task is literally not done. The protocol enforces honesty.

---

## Completion Detection Is Multi-Signal — No Single Point of Trust

AI agents are convincing liars. They'll say "task complete" while the tests are still failing. The Architect uses four independent signals and requires corroboration:

1. **Promise tag** — the agent explicitly outputs `<promise>TXX_COMPLETE</promise>` — the strongest signal
2. **PROGRESS.md** — the agent marked the task Done in the progress file
3. **Clean exit** — the provider CLI exited with code 0
4. **Progress signal** — the agent said "all tests pass" or "task is done" in its output

No single signal is trusted alone. Exit code 0? Not enough. Agent says it's done? Not enough. Two or more signals must agree. And if the agent says "I'm stuck" anywhere in its output, that overrides any "task complete" claim — because a stuck agent that claims completion is hallucinating.

---

## Circuit Breaker — The Pattern Borrowed from Electrical Engineering

The circuit breaker is inspired by real-world electrical circuits. When too much current flows, a physical circuit breaker trips to protect the system. The Architect does the same:

- **No-progress detection** — the agent ran but didn't write any files. Three times in a row? Trip.
- **Same-error detection** — the agent is repeating the same logical error (not the same text — the same *pattern*, with file paths and line numbers stripped). Three times? Trip.
- **Token decline** — the agent is giving up earlier each attempt, using fewer tokens, doing less work. If it drops below 40% of the first attempt *and* another counter is elevated? Trip.

When the circuit trips, it doesn't just fail. It chooses a recovery action: wait (let model rotation handle it), replan (rewrite just the failing task), or cooldown (wait for rate limits to reset). The circuit breaker state survives process kills and machine restarts — it's persisted to disk.

---

## File Integrity Defense — Trust but Verify

AI agents occasionally truncate files mid-write. The tool call succeeds, the file exists, but the last few hundred lines are gone. Without a guard, the next task runs against a broken codebase and produces confusing failures three steps later.

The Architect's file integrity defense (on by default) adds a snapshot-and-verify step to every file edit:

1. Before touching an existing file, the agent copies it to `architect_eval_<filename>` in the same directory
2. After writing the modified file, the agent validates the rewrite against the snapshot — checking for truncation, missing sections, or unexpected size drops
3. If validation passes, the snapshot is deleted
4. If validation fails, the original is restored and the agent retries

Any `architect_eval_*` file left behind after a task is treated as a corruption signal. The reassessment and retrospective passes detect these files and flag them.

Silent file corruption gets caught at the source, not three tasks later when nothing compiles.

---

## Inter-Task Reassessment — Keeping Future Tasks In Sync

AI agents are linear thinkers. When T02 adds a new database schema, the agent that runs T03 doesn't automatically know the schema changed — unless someone tells it.

The Architect solves this with inter-task reassessment. By default, Force Reassessment is enabled, so the architect agent checks pending tasks after every completed or failed task. The build agent also reports its outcome in a structured block that includes an impact signal: `Downstream impact: possible` or `Downstream impact: none`.

During reassessment, The Architect invokes the architect agent on the pending task files — a targeted pass to update T03, T04, and beyond to reflect what just changed. This is not a full replan. It's a surgical adjustment. Future tasks stay in sync with what has actually been built so far.

If Force Reassessment is disabled, reassessment becomes conditional: failed tasks still trigger it, and successful tasks trigger it only when they report `Downstream impact: possible`.

---

## ARCHITECT.md — The File That Grows Smarter Over Time

`ARCHITECT.md` is not a config file and not a run log. It's a living document that stores durable project intelligence across sessions. Every time The Architect runs:

1. **During planning** — the architect agent reads it to learn the repo map, stack, contracts, decisions, constraints, and conventions
2. **During execution** — the build agent reads it for context and *writes to it* when it discovers durable project knowledge
3. **During retrospective** — the reviewer reads it and promotes durable quality findings, contracts, or lessons

Over time, ARCHITECT.md becomes the project's institutional memory — the stuff that's normally locked in a senior developer's head, now captured in a file that any future AI session can read.

Detailed run history belongs in `tasks/SUMMARY.md`, which is archived with each task package.

It's worth committing to git. It improves with every run.

---

## Retrospective — The Quality Gate Most Projects Skip

Most AI coding tools are one-shot: you ask, it writes, you check. The Architect adds a retrospective review step after execution:

1. A separate reviewer agent examines all completed work
2. It runs the test suite
3. It reviews code quality and consistency
4. If it finds issues, it creates fix-up tasks (R01, R02, …)
5. Those fix-up tasks run through the same execution pipeline — with retry, circuit breaker, and completion detection

In persistent mode, it runs *two* retrospective rounds — the second reviews the first round's fixes. This is closer to how senior engineers actually work: write, review, fix, review again.

Clean builds skip the fix-up round automatically. Silence from the reviewer is success.

---

## Free Mode — Zero-Cost AI Development

The Architect can run entirely on free AI models. Not "free trial" — actually free, forever. It fetches the list of zero-cost models from OpenRouter, sorts them by context length (larger context = better for coding), and rotates through them during execution. When one model hits a rate limit mid-stream, it switches to the next one immediately — no manual intervention, no wasted retry slot.

This means someone with zero budget can run fully autonomous development sessions. The tradeoff is speed and capability — free models are smaller and slower. But the orchestration (planning, retry, circuit breaker, retrospective) works exactly the same.

Free mode is only available with OpenCode + OpenRouter. Codex CLI, Claude Code, and Gemini CLI use their own API directly.

---

## Provider-Agnostic by Design

The Architect doesn't care which AI tool you use. It supports OpenCode, Codex CLI, Claude Code, and Gemini CLI today, but the provider layer is a protocol — not a hardcoded integration. Each provider implements the same interface: detect if installed, get its version, run it with a prompt, parse its output.

Adding a new provider means implementing that protocol. The planning, execution, retry, circuit breaker, retrospective, and all the orchestration logic stays the same.

---

## Standalone Mode — One Model for Everything

Sometimes you want to force a specific model across all operations without touching the provider's config files. `--standalone openrouter/anthropic/claude-sonnet-4.5` overrides everything — planning, execution, and retrospective all use that model. Useful for CI runs where the model must be deterministic, or for quickly testing a model without reconfiguring the full provider setup.

---

## It Protects You From Yourself

Several features exist specifically to prevent common mistakes:

- **Premature exit guard** — if all tasks are already done, The Architect refuses to re-enter planning mode without an explicit `--plan` flag. No accidentally re-architecting a finished project.
- **Lock file** — only one Architect run at a time. A second attempt tells you and exits.
- **Pending task guard** — starting a new plan with unfinished tasks warns you first; headless mode archives them automatically.
- **Token budget** — optional hourly spend cap. Set it and forget it.

---

## Zero Config by Default

The Architect works without any configuration file. No `architect.toml` needed. Every setting has a sensible default, and all of them can be overridden via CLI flags, environment variables, or the interactive setup screen.

The only requirement is at least one supported AI coding CLI installed and configured.

---

## Parallel Execution — Independent Tasks Run Together

Sequential execution is safe but slow. When tasks have no dependency chain, The Architect can run them concurrently. The `max_parallel_tasks` config option controls the concurrency limit. Each parallel task gets its own circuit breaker, and token budgets are shared safely via `asyncio.Lock`.

This is not a "run everything at once" mode — it respects the dependency graph. If T03 depends on T01, T03 waits until T01 finishes. Only truly independent tasks run together. The TUI shows a live DataTable of all concurrent tasks with per-task status, token count, and circuit state.

---

## Task Dependencies — Explicit Ordering

Tasks can declare what they depend on. This gives the runner three superpowers:

1. **Cycle detection** — If T01 depends on T02 and T02 depends on T01, the run aborts before wasting tokens
2. **Smart skipping** — If T01 fails, T03 (which depends on T01) is automatically skipped instead of running and failing for unrelated reasons
3. **Parallel scheduling** — The scheduler knows which tasks can run together and which must wait

Dependencies are declared in the task file as a `## Dependencies` section. The runner parses them, validates the graph, and respects ordering during both sequential and parallel execution.

---

## Dry-Run Mode — Plan Without Committing

Before spending tokens on execution, you can plan and review with `--dry-run`. The planner runs normally (task files are created), then a summary shows the task list, estimated cost, and dependency validation results. The process exits without executing.

This is the "check the map before driving" feature. It catches planning errors, gives you a cost estimate, and lets you review the plan before committing. The `--json` flag makes it automation-friendly for CI pipelines and monitoring scripts.

---

## Goal Templates — Reusable Goals with Variables

Instead of typing the same goal structure every time, save it as a template with `{variable}` placeholders. Then run `architect template run "my-template" --var name=value` and The Architect substitutes the variables, prompts for any remaining ones, and launches.

Templates are how you productize your development patterns: "scaffold a new API service", "add authentication to {service}", "write tests for {module}". Each template can also store config overrides so the right settings come along for the ride.

---

## Rollback — Undo a Run

Every run captures a workspace baseline before execution. If the run produces unwanted changes, `architect rollback` restores files to their pre-run state. The TUI shows you exactly what will be restored or deleted, with file sizes and actions, before you approve.

This is the safety net for autonomous development. The AI made changes you don't want? Roll back to before the run started. No git magic, no manual diffing — just restore the baseline.

---

## Cost Ledger — Know What You Spend

The Architect records every run's token usage and estimated cost to a ledger file. Over time, you can see spending patterns: which models cost what, how many tasks each run produces, and how costs trend across sessions.

`architect estimate` predicts the cost of a future run using historical data. `architect history` shows past runs with cost columns. `architect token-report` breaks down spending by model and date range. The per-task cost breakdown tells you which tasks were expensive and which were cheap.

This turns token usage from a mystery into data. You can set budgets, track spending, and make informed decisions about which models to use for which tasks.

---

## Configuration Presets — Save and Recall Settings

Save a configuration profile once, apply it anywhere. Presets let you create named sets of config overrides ("sprint", "deep-review", "quick-fix") and apply them with a single command. They are stored per-project in `.architect/presets.json`.

Team presets standardize settings across developers. Environment presets adapt to CI vs local. Project presets tune for specific modules.

---

## GitHub Shows Every Build, PyPI Shows Only Releases

Every green build on `main` automatically creates a GitHub release with the wheel and source distribution attached. Same-version builds are marked as pre-release.

Only actual version changes trigger a PyPI publish — and that requires manual approval.

So build 10042, 10043, 10044 — all visible on GitHub as pre-releases with downloadable artifacts. But `pip install the-architect` only gets the latest stable version. The build number is for developers. The version number is for users.

---

## It Was Built by an AI Using Itself

The Architect is its own first user. During development, The Architect planned and executed tasks against its own codebase using its own orchestration loop. The circuit breaker was tested by the circuit breaker. The retrospective reviewed the retrospective.

This created a tight feedback loop: every bug in orchestration was immediately felt by the orchestration itself. The build counter in `version.py` is the honest record of how many operations it took — because the tool that increments it was also the tool being built.
