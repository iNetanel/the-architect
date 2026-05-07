# The Architect Execution Protocol

You are being run by The Architect — an autonomous task runner. This document
explains how The Architect tracks your work and detects completion.

**This protocol does not change how your agent organizes its work.** Follow your
agent prompt's workflow for delegation, tool use, and step-by-step execution.
The rules below only describe how The Architect monitors progress — they do not
override your agent's delegation or orchestration instructions.

---

## What The Architect expects from you

1. Read `ARCHITECT.md` — durable project intelligence (repo map, stack, contracts, decisions, constraints, lessons, best practices)
2. Read `tasks/INSTRUCTIONS.md` — project context, stack, conventions, and full task list
3. Read `tasks/PROGRESS.md` — current state, what is done, what is next
4. Read `AGENTS.md` or `CLAUDE.md` if either exists — the user's project rules (read it explicitly if your CLI doesn't auto-load it; OpenCode uses `AGENTS.md`, Claude Code uses `CLAUDE.md`)
5. Read your task file in `tasks/` — your specific instructions for this task
6. Follow the task's Exploration Plan before editing — inspect the smallest relevant code slice first
7. Complete every item in the task file — work autonomously without asking the human for confirmation
8. Rewrite `tasks/PROGRESS.md` when done — this is how The Architect knows you finished
9. Output `<promise>TXX_COMPLETE</promise>` when done — this is the primary completion signal

---

## Focused Codebase Discovery Before Implementation

Task files define outcomes and focused exploration lanes. They may intentionally
avoid prescribing exact internals. Before editing files, use the task's
Exploration Plan to inspect the smallest relevant part of the codebase.

You are expected to discover:

- Existing files, modules, routes, components, providers, or config patterns related to the task
- Naming conventions and data shapes already used by the project
- Existing tests and verification commands for the affected area
- Whether an existing abstraction should be extended instead of creating a new one
- Integration points with previous completed tasks and future pending tasks

Do not perform broad, unfocused repo exploration. Start with the files and areas
named or implied by the Exploration Plan, then broaden only when those files
directly point to another dependency you must understand.

Prefer existing project patterns over invented names or new structures. Do not
create new files, APIs, components, hooks, models, agents, or config keys until
you have checked whether an existing place should be extended.

If the task suggests an approach, verify it against the codebase before following
it. The task's required outcomes matter more than guessed implementation details.
If the safest implementation differs from a suggested approach, implement the
safest codebase-consistent approach and record the decision in PROGRESS.md.

If you finalize a shared contract that downstream tasks depend on — for example
an endpoint shape, event payload, data model, config key, component interface, or
agent name — record the final contract in PROGRESS.md under Last Task Summary or
Task Outcomes. Reassessment uses that record to update pending tasks.

---

## PROGRESS.md — Critical format rules

The runner parses `tasks/PROGRESS.md` with regex. The format must be exact.

### Task status vocabulary

Every task row has a **Status** cell that must hold one of four values:

| Status | Meaning | Who writes it |
|--------|---------|---------------|
| `Pending` | Task is queued or still in progress. Default for new tasks. | Planner on plan creation; you MAY set this if you restart a task. |
| `Done` | Task completed successfully, all acceptance criteria met. | You, when you genuinely finish. The runner also **reconciles** this cell — if the runner's multi-signal check says Done but you forgot to rewrite PROGRESS.md, it will stamp `Done` for you. |
| `Failed` | The runner exhausted all retries. The task has a terminal failure. | **Written by the runner only.** Do not set `Failed` yourself — leave status as `Pending` if you cannot complete and let the runner decide. |
| `Blocked` | Task could not run due to a resource constraint (rate-limit, budget, cooldown). | Written by the runner only. |

Rows in any of `Done`, `Failed`, or `Blocked` are **terminal** — the runner will NOT re-pick them on the next loop. Only `Pending` rows are considered live work.

### Runner reconciliation — you are not alone

After every task attempt, the runner re-reads PROGRESS.md and will rewrite the status cell for your row based on its own verdict (multi-signal completion check). This means:

- If you forget to rewrite PROGRESS.md but emit the promise tag and the work is actually done, the runner stamps `Done` for you. You will see this in the logs as `Persisted Done status for TXX in PROGRESS.md` — that is normal.
- If all retries are exhausted, the runner stamps `Failed (N attempts)` on the row so the next loop skips your task. A reviewer R-task (or a human) must address the root cause before the task can be re-attempted.
- You should still rewrite PROGRESS.md yourself — reconciliation is a safety net, not a substitute. A clean run has both signals.

### How "Done" is detected

After you finish, The Architect runs a **multi-signal algorithm** to determine if
your task is done. It checks four independent signals and applies these rules:

| Signal | How it fires |
|--------|-------------|
| **Promise tag** | Your output contains `<promise>TXX_COMPLETE</promise>` |
| **PROGRESS.md** | PROGRESS.md shows `Done` for your task prefix |
| **Clean exit** | The AI CLI subprocess exited with code 0 |
| **Progress phrase** | Your output contains phrases like "all tests pass" or "task complete" |

**Completion rules (in priority order):**

1. **2 or more signals fire** → task is Done.
2. **Promise tag alone** → task is Done (strong, explicit, agent-declared signal).
3. **PROGRESS.md alone** → task is Done, but a warning is logged (suspicious — may
   be a premature or false positive).
4. **Clean exit alone** → **NOT done** (opencode exits 0 even on timeout or error).
5. **Progress phrase alone** → **NOT done** (too weak — could be from earlier output).

**What this means for you:**

- **Always output the promise tag** — it is the primary, most reliable signal and is
  sufficient on its own.
- **Always update PROGRESS.md** — belt and suspenders; together with the promise tag
  it gives the runner two corroborating signals for a clean, unambiguous completion.
- Never rely on a clean exit code or progress phrases alone — they are not sufficient.

### When to output the completion promise

When you have completed ALL items in the task file:

1. Update PROGRESS.md (mark task Done, set next task)
2. Output the completion promise: `<promise>TXX_COMPLETE</promise>`

ONLY output the promise tag when ALL of these are true:

- Every item in the task file has been implemented
- Tests pass (must be verified — do not assume)
- No outstanding errors or failures remain
- PROGRESS.md has been updated

Do NOT output a false promise to signal completion early. If you are stuck or
the task is only partially done, leave the status as `Pending` in PROGRESS.md
and do NOT output the promise tag.

### How to rewrite PROGRESS.md

When you complete your task, rewrite the **entire** PROGRESS.md file.
Do not edit in place — rewrite it completely. Keep this exact structure:

```markdown
# The Architect — Progress Tracker

> This file is the memory between tasks.
> Every task MUST read this at the start and rewrite it completely at the end.

---

## Overall Status

**Tasks completed:** N
**Next task to run:** TXX

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | task_name | Done | 2026-04-13 |
| T02 | task_name | Pending | — |

---

## Current State

One sentence describing what just happened.

## Last Task Summary

What you did, what changed, what decisions you made.

---

## Task Outcomes

| Task | Outcome | Files | Verification | Impact on Next Tasks |
|------|---------|-------|--------------|----------------------|

---

## Lessons Learned

- Durable lessons discovered during execution, especially mistakes, missing
  assumptions, flaky commands, environment constraints, or verification gotchas
  that the next agent should not rediscover.

---

## Missing / Follow-up Notes

- Anything still incomplete, unverified, risky, blocked, or important for the
  next task agent to know. If nothing is missing, say so explicitly.

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
| | | | |
```

### Rules

- Increment `**Tasks completed:**` by 1 for your task
- Set `**Next task to run:**` to the next pending task prefix
- Change your task's row from `Pending` to `Done` and add today's date
- Leave other tasks' rows unchanged
- Update `Current State` and `Last Task Summary`
- Preserve the `## Task Outcomes` table — copy all existing rows when rewriting; do NOT drop them
- Update `## Lessons Learned` with real lessons from this task; preserve existing lessons
- Update `## Missing / Follow-up Notes` with gaps, unverified areas, risks, blockers,
  or information the next agent needs; say explicitly when nothing is missing

---

## Task file format

Your task file is in `tasks/TXX_name.md`. It follows this structure:

```markdown
# TXX — Task Title

## Goal
One sentence describing what this task accomplishes.

## Context
Prior decisions, architecture notes, or constraints.

## Exploration Plan
Focused areas and existing patterns to inspect before editing. Treat this as the
starting point for discovery, not as permission to wander through the whole repo.

## Tasks

### TXX.1 — Sub-task title
[Outcome to achieve; discover and follow existing implementation patterns]

### TXX.2 — Sub-task title
[Outcome to achieve; discover and follow existing implementation patterns]
```

Ensure every sub-task is completed. Do not skip any. If a sub-task depends on a previous
one that was not completed, note it in your summary but continue with what
you can do. If the task omits implementation details, that is intentional — use
focused codebase discovery to choose the correct local implementation.

---

## Verification Discipline — No Assumed Success

Do not assume anything works. You must prove it with the strongest practical
verification available for the project and the task.

Before marking Done:

1. Identify the relevant verification commands from project docs, package
   scripts, Makefiles, CI config, or existing tests.
2. Run focused tests for the code you changed.
3. Run broader validation when the change affects shared behaviour, public APIs,
   build configuration, routing, state management, or UI flows.
4. Run lint/typecheck/build commands when the project provides them and they are
   relevant to the changed area.
5. Read the command output and fix failures. A command that starts but reports
   failures is not a pass.

If a required verification tool or dependency is missing, do not skip testing by
default. Use the project's package manager and documented setup commands to
install what is needed when it is safe and local to the project. Examples:
`npm install`, `pnpm install`, `pip install -e .`, `pip install -r requirements.txt`,
or browser/test tooling such as Playwright dependencies when the repo already uses
that stack. Do not add new runtime dependencies just to make testing easier unless
the task requires them; if you add or install anything, record what you did in
PROGRESS.md and include it in your final verification summary.

### UI and Frontend Changes

UI work is especially easy to complete only partially. For UI, frontend, TUI, or
visual interaction tasks, do the best practical verification available instead of
stopping at typechecks:

- Run component/unit tests, browser/E2E tests, snapshot tests, or TUI tests when
  the project has them.
- Run the frontend build or app compile step when available.
- If the project supports a local dev server or preview command, start it long
  enough to verify the changed route/screen/component loads without errors.
- Exercise the relevant interaction path manually or with an automated test when
  tools are available: navigation, forms, toggles, keyboard focus, responsive
  layout, loading/error/empty states, and accessibility-relevant behaviour.
- For terminal UIs, verify screen construction, key bindings, focus movement,
  and update/render paths through tests or a smoke run.

If full UI verification is impossible in the environment, still run every
available lower-level check, document exactly what could not be verified and why,
leave the task Pending if the unverified behaviour is central to the task, and do
not output the promise tag unless the remaining gap is clearly non-blocking.

---

## Anti-Hallucination Guard

**CRITICAL — read this before marking any task Done.**

You MUST NOT mark a task as Done, and you MUST NOT output the completion
promise, unless ALL of the following are true:

- ✅ Every sub-task in the task file has been implemented
- ✅ Relevant existing code paths were inspected before implementation
- ✅ Tests have been **RUN and verified** (do not assume — confirm they actually pass)
- ✅ New shared contracts follow existing conventions or are recorded in PROGRESS.md
- ✅ No `print()` statements, debug code, or `TODO` comments remain
- ✅ No outstanding errors or failures in the terminal output
- ✅ PROGRESS.md has been rewritten with the correct status

Do NOT mark a task Done because:

- ❌ You are stuck and want to move on
- ❌ You are running low on context window
- ❌ You think it "should work" but haven't verified
- ❌ You partially completed the work
- ❌ The tests exist but you haven't run them

If you are genuinely stuck: leave the task as `Pending` in PROGRESS.md,
describe what is blocking you in the "Current State" section, and do NOT
output the promise tag. The next attempt (or the runner, after it
exhausts retries) will take it from there. Do NOT write `Failed` or
`Blocked` yourself — those statuses belong to the runner.

---

## Updating ARCHITECT.md — Persistent Project Intelligence

ARCHITECT.md is The Architect's durable project brain. It should contain stable
project intelligence that future unrelated tasks need: repo responsibilities,
tech stack, architecture, key flows, shared contracts, code locations,
verification commands, style standards, agent conventions, data/storage,
environment rules, operational constraints, permanent decisions, lessons, and
best practices.

It is not run history. Task/package history belongs in PROGRESS.md while running
and tasks/SUMMARY.md when the package completes.

### When to update ARCHITECT.md

Update ARCHITECT.md **after** completing your task, **before** marking it Done:

- **Project intelligence** — If you discovered a durable repo/component role,
  important code location, key flow, shared contract, verification command,
  environment rule, or operational constraint, add it to the matching section.

- **Permanent Decisions** — If you made an architectural choice that should not
  be revisited (e.g. "use SQLite for local cache", "all API responses follow
  JSON:API spec"), add it to the Permanent Decisions table.

- **Known Constraints** — If you discovered a non-obvious limitation (e.g.
  "tests must be run from backend/ not root", "the config parser doesn't handle
  empty strings"), add it to the Known Constraints section.

- **Lessons Learned** — If something went wrong and you had to fix it, record
  the lesson (e.g. "T03: pydantic v2 uses model_validate not parse_obj"). This
  prevents future tasks from repeating the same mistake.

- **Best Practices** — If a pattern emerged that should be followed going forward
  (e.g. "always add type hints to public functions", "use loguru not print"),
  add it to the Best Practices section.

### How to update ARCHITECT.md

- Only **append** — never remove or modify existing entries
- Do NOT modify the Repository Map section — The Architect tool manages that
- Add rows to tables such as Permanent Decisions
- Add list items to durable knowledge sections such as Known Constraints,
  Lessons Learned, Best Practices, Shared Contracts, Code Locations, or
  Verification
- Replace placeholder text (`_No ... recorded yet._`) with real entries

### What NOT to put in ARCHITECT.md

- Task-specific details that are already in PROGRESS.md or tasks/SUMMARY.md
- The current goal, task list, or run summary
- Temporary state (what you're currently working on)
- Information that only applies to this one task and won't help future tasks

---

## Rules

- Read ARCHITECT.md, PROGRESS.md and AGENTS.md/CLAUDE.md before starting any work
- Never ask the human for confirmation — proceed autonomously
- Tests must pass before marking a task Done — run them, do not assume
- Rewrite PROGRESS.md completely when done — do not skip this step
- Output `<promise>TXX_COMPLETE</promise>` when all items are complete — this is the primary completion signal
- If a task is partially done but blocked, set status to `Pending` and explain in Current State — do NOT output the promise tag
- Stay inside the project directory — never read, write, or modify files outside the project root
