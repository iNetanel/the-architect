# The Architect Execution Protocol

You are being run by The Architect — an autonomous task runner. This document
explains how The Architect tracks your work and detects completion.

**This protocol does not change how your agent organizes its work.** Follow your
agent prompt's workflow for delegation, tool use, and step-by-step execution.
The rules below only describe how The Architect monitors progress — they do not
override your agent's delegation or orchestration instructions.

---

## What The Architect expects from you

1. Read `ARCHITECT.md` — persistent project intelligence (decisions, constraints, lessons, best practices)
2. Read `tasks/INSTRUCTIONS.md` — project context, stack, conventions, and full task list
3. Read `PROGRESS.md` — current state, what is done, what is next
4. Read `AGENTS.md` or `CLAUDE.md` if either exists — the user's project rules (read it explicitly if your CLI doesn't auto-load it; OpenCode uses `AGENTS.md`, Claude Code uses `CLAUDE.md`)
5. Read your task file in `tasks/` — your specific instructions for this task
6. Complete every item in the task file — work autonomously without asking the human for confirmation
7. Rewrite `PROGRESS.md` when done — this is how The Architect knows you finished
8. Output `<promise>TXX_COMPLETE</promise>` when done — this is the primary completion signal

---

## PROGRESS.md — Critical format rules

The runner parses PROGRESS.md with regex. The format must be exact.

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

---

## Task file format

Your task file is in `tasks/TXX_name.md`. It follows this structure:

```markdown
# TXX — Task Title

## Goal
One sentence describing what this task accomplishes.

## Context
Prior decisions, architecture notes, or constraints.

## Tasks

### TXX.1 — Sub-task title
[Specific instruction]

### TXX.2 — Sub-task title
[Specific instruction]
```

Ensure every sub-task is completed. Do not skip any. If a sub-task depends on a previous
one that was not completed, note it in your summary but continue with what
you can do.

---

## Anti-Hallucination Guard

**CRITICAL — read this before marking any task Done.**

You MUST NOT mark a task as Done, and you MUST NOT output the completion
promise, unless ALL of the following are true:

- ✅ Every sub-task in the task file has been implemented
- ✅ Tests have been **RUN and verified** (do not assume — confirm they actually pass)
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

ARCHITECT.md is The Architect's long-term memory. It accumulates knowledge
across all planning sessions and execution cycles. When you discover something
that future tasks should know about, update ARCHITECT.md.

### When to update ARCHITECT.md

Update ARCHITECT.md **after** completing your task, **before** marking it Done:

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
- Do NOT modify the Project Structure section — The Architect tool manages that
- Add rows to tables (Permanent Decisions, Planning History)
- Add list items (Known Constraints, Lessons Learned, Best Practices)
- Replace placeholder text (`_No ... recorded yet._`) with real entries

### What NOT to put in ARCHITECT.md

- Task-specific details that are already in PROGRESS.md
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
