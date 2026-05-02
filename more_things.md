# More Things About The Architect

> Stuff worth talking about that you won't find in the documentation folder.

---

## It Doesn't Write Code

This is the first thing people get wrong. The Architect never writes a single line of your application code. It doesn't call any AI API directly — no OpenAI SDK, no Anthropic SDK, no Google AI SDK, nothing. It shells out to your existing AI CLI tool and orchestrates it. The AI writes the code. The Architect makes sure the AI finishes the job, doesn't get stuck, and doesn't hallucinate success.

Think of it as a project manager for AI agents, not an AI agent itself.

---

## The Build Counter Is a Lie Detector

Most version numbers only change on release day. The Architect's build counter (`__build__`) increments on every single completed task — a typo fix, a new feature, a refactoring, a documentation update. It never resets. It's a monotonic, honest record of cumulative effort.

Why does this matter? Because when you see `v1.0.0 (build 10042)`, you know exactly how many operations it took to get from `v1.0.0` to here. No invisible work. No silent refactors. Every change leaves a number.

The build counter is also the project's immune system against lazy AI agents. If an agent forgets to bump the build, the task is literally not done. The protocol enforces honesty.

---

## GitHub Shows Every Build, PyPI Shows Only Releases

Most projects have a gap between "code works on CI" and "users can install it." The Architect closes that gap with a dual-track release system:

- **Every green build** on `main` automatically creates a GitHub release with the wheel and source distribution attached. Same-version builds are marked as pre-release.
- **Only actual version changes** trigger a PyPI publish. And that publish requires your manual approval.

So build 10042, 10043, 10044 — all visible on GitHub as pre-releases with downloadable artifacts. But `pip install the-architect` only gets the latest stable version. The build number is for developers. The version number is for users.

---

## Circuit Breaker — The Pattern Borrowed from Electrical Engineering

The circuit breaker is inspired by real-world electrical circuits, not software patterns. When too much current flows, a physical circuit breaker trips to protect the system. The Architect does the same thing:

- **No-progress detection**: The agent ran but didn't write any files. Three times in a row? Trip.
- **Same-error detection**: The agent is repeating the same logical error (not the same text — the same *pattern*, with file paths and line numbers stripped). Three times? Trip.
- **Token decline**: The agent is giving up earlier each attempt — using fewer tokens, doing less work. If it drops below 40% of the first attempt *and* another counter is elevated? Trip.

When the circuit trips, it doesn't just fail. It chooses a recovery action: wait (let model rotation handle it), replan (rewrite just the failing task), or cooldown (wait an hour for rate limits to reset). The circuit breaker state survives process kills and machine restarts — it's persisted to disk.

---

## Completion Detection Is Multi-Signal — No Single Point of Trust

AI agents are convincing liars. They'll say "task complete" while the tests are still failing. The Architect uses four independent signals and requires corroboration:

1. **Promise tag**: The agent explicitly outputs `<promise>TXX_COMPLETE</promise>` — the strongest signal
2. **PROGRESS.md**: The agent marked the task as Done in the progress file
3. **Clean exit**: The provider CLI exited with code 0
4. **Progress signal**: The agent said "all tests pass" or "task is done" in its output

No single signal is trusted alone. Exit code 0? Not enough. Agent says it's done? Not enough. Two or more signals must agree. And if the agent says "I'm stuck" anywhere in its output, that overrides any "task complete" claim — because a stuck agent that claims completion is hallucinating.

---

## Free Mode — Zero-Cost AI Development

The Architect can run entirely on free AI models. Not "free trial" — actually free, forever. It fetches the list of zero-cost models from OpenRouter, sorts them by context length (larger context = better for coding), and rotates through them during execution. When one model hits a rate limit mid-stream, it switches to the next one immediately — no manual intervention, no wasted retry.

This means someone with zero budget can run fully autonomous development sessions. The tradeoff is speed and capability — free models are smaller and slower. But the orchestration (planning, retry, circuit breaker, retrospective) works exactly the same.

---

## ARCHITECT.md — The File That Grows Smarter Over Time

`ARCHITECT.md` is not a config file. It's a living document that accumulates project intelligence across sessions. Every time The Architect runs:

1. **During planning**: The architect agent reads it to learn what the project is, what decisions were made, what went wrong last time
2. **During execution**: The build agent reads it for context and *writes to it* when it discovers new constraints, patterns, or lessons
3. **During retrospective**: The reviewer reads it and updates it with quality findings

Over time, ARCHITECT.md becomes the project's institutional memory — the stuff that's normally locked in a senior developer's head, now captured in a file that any future AI session can read.

---

## Retrospective — The Quality Gate Most Projects Skip

Most AI coding tools are one-shot: you ask, it writes, you check. The Architect adds a retrospective review step after execution:

1. A separate reviewer agent examines all completed work
2. It runs the test suite
3. It reviews code quality and consistency
4. If it finds issues, it creates fix-up tasks (R01, R02, …)
5. Those fix-up tasks run through the same execution pipeline — with retry, circuit breaker, and completion detection

In persistent mode, it does *two* retrospective rounds — the second one reviews the first round's fixes. This is closer to how senior engineers actually work: write, review, fix, review again.

---

## Provider-Agnostic by Design

The Architect doesn't care which AI tool you use. It supports OpenCode, Codex CLI, Claude Code, and Gemini CLI today, but the provider layer is a protocol — not a hardcoded integration. Each provider implements the same interface:

- Detect if it's installed
- Get its version
- Run it with a prompt and return the output
- Parse its output format (JSON events or JSONL for structured providers, plain text for providers that do not expose structured streams)

Adding a new provider (Cursor, Aider, continue.dev, whatever comes next) means implementing that protocol. The planning, execution, retry, circuit breaker, retrospective, and all the orchestration logic stays the same.

---

## It Protects You From Yourself

Several features exist specifically to prevent common mistakes:

- **Premature exit guard**: If all tasks are already done, The Architect refuses to re-enter planning mode without an explicit `--plan` flag. No accidentally re-architecting a finished project.
- **Lock file**: Only one Architect run can happen at a time. If you try to start a second one, it tells you and exits. No concurrent runs corrupting each other's state.
- **Pending task guard**: If you start a new planning session with unfinished tasks from a previous run, it warns you first. In headless mode, it archives them automatically.
- **Token budget**: Optional hourly spend cap. Set it and forget it — The Architect will stop before burning through your API credits.

---

## Zero Config by Default

The Architect works without any configuration file. No `architect.toml` needed. No YAML. No JSON. Every setting has a sensible default, and all of them can be overridden via CLI flags, environment variables, or the interactive setup screen.

The only thing you need installed is at least one supported AI coding CLI. Everything else is optional.

---

## The tmux Dashboard — Watch AI Work in Real Time

If you have tmux installed, The Architect can split your terminal into two panes: your normal shell on the left, and a live monitoring dashboard on the right. The dashboard shows:

- Current task and attempt number
- Circuit breaker state
- Token usage
- Model being used
- Free mode model rotation status
- Elapsed time

You can watch the AI work without it filling your terminal. And when it's done, the dashboard disappears and you get your full terminal back.

---

## It Was Built by an AI Using Itself

The Architect is its own first user. During development, The Architect planned and executed tasks against its own codebase using its own orchestration loop. The circuit breaker was tested by the circuit breaker. The retrospective reviewed the retrospective.

This created a tight feedback loop: every bug in orchestration was immediately felt by the orchestration itself. The build counter you see in `version.py` is the honest record of how many operations it took — because the tool that increments it was also the tool being built.
