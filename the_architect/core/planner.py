"""Planning integration for The Architect.

Uses an AI CLI provider (OpenCode, Codex CLI, Claude Code, or Gemini CLI) with the architect agent
to decompose goals into task files.
No direct AI API calls — everything goes through the user's provider setup.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import BaseModel, Field

from the_architect.config import ArchitectConfig
from the_architect.core.progress import PROGRESS_TEMPLATE
from the_architect.core.runner import StreamRenderer, stream_provider
from the_architect.core.tasks import Task, TaskScope, discover_tasks

if TYPE_CHECKING:
    from the_architect.core.provider import ArchitectProvider


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PlanningFailedError(Exception):
    """Raised when planning fails to create any tasks."""

    pass


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class PlanningRequest(BaseModel):
    """Request to plan a new development goal."""

    goal: str = Field(description="The user's development goal")
    scope: TaskScope = Field(description="Scope hint controlling task breadth")
    project_dir: Path = Field(description="The project root directory")
    model_override: str | None = Field(
        default=None,
        description="Explicit model to use for the architect (overrides opencode.json)",
    )
    context_content: str = Field(
        default="",
        description="Additional context from --context files, formatted for prompt injection",
    )
    structure_report: str = Field(
        default="",
        description="Project structure detection report for prompt injection",
    )
    architect_md_content: str = Field(
        default="",
        description="Full ARCHITECT.md content for prompt injection",
    )

    model_config = {"frozen": True}


# Maps TaskScope values to concrete guidance the architect can act on.
# Describes the SCOPE of each task — not a target number of tasks.
# The number of tasks emerges naturally from goal size ÷ task scope.
_SCOPE_GUIDE: dict[str, str] = {
    "simple": (
        "simple — Each task must be as small and atomic as possible: one function, "
        "one file, one test suite, one configuration change — ONE thing only. "
        "Smaller tasks tend to require less context per execution run, "
        "which reduces hallucination risk and works well with weak or local models. "
        "A large goal will produce many tasks. That is correct and expected — "
        "never merge tasks to keep the count low."
    ),
    "standard": (
        "standard — Each task should cover one feature area or concern: "
        "a model and its schema, a set of related routes, a module with its tests. "
        "A task groups closely related work that makes sense to build and verify together. "
        "The executor needs a moderate context window. Good for most projects and models."
    ),
    "complex": (
        "complex — Each task should cover a whole subsystem or cross-cutting concern: "
        "a full authentication system, a complete data pipeline, an entire API layer. "
        "A task is intentionally broad and will touch many files. "
        "Broader tasks tend to require more context per execution run. "
        "Only use this with frontier models (Claude Opus, GPT-4o, Gemini 2.5 Pro) "
        "that have large context windows and can reason across many files at once."
    ),
}


class PlanningResult(BaseModel):
    """Result of a planning operation."""

    tasks_created: list[str] = Field(
        default_factory=list, description="List of task names that were created"
    )
    agents_md_read: bool = Field(
        default=False,
        description=(
            "Whether user's project rules file (AGENTS.md or CLAUDE.md) "
            "was found and read for context"
        ),
    )
    instructions_md_written: bool = Field(
        default=False, description="Whether tasks/INSTRUCTIONS.md was written"
    )
    progress_md_written: bool = Field(default=False, description="Whether PROGRESS.md was written")
    summary: str = Field(default="", description="Summary of what was planned")


GOAL_FILE_NAME = "GOAL.md"


# ---------------------------------------------------------------------------
# Historical PROGRESS.md summarisation
# ---------------------------------------------------------------------------


def _summarize_progress_historical(content: str) -> str:
    """Extract useful historical context from PROGRESS.md content.

    Returns only completed tasks and permanent decisions — NOT active state
    like "Next task to run" or "Current State" which would confuse a new
    planning session by making the architect think it should continue the
    old plan instead of creating a new one.

    Args:
        content: Raw PROGRESS.md content.

    Returns:
        A concise historical summary string.
    """
    import re as _re

    parts: list[str] = []

    # Extract completed tasks from the Task Log
    done_pattern = _re.compile(
        r"^\|\s*([TS]\d+)\s*\|\s*([^|]+?)\s*\|\s*Done\s*\|\s*([^|]*?)\s*\|",
        _re.MULTILINE,
    )
    done_tasks = done_pattern.findall(content)
    if done_tasks:
        parts.append("Completed tasks from previous plans:")
        for prefix, title, date in done_tasks:
            date_clean = date.strip() or "—"
            parts.append(f"  - {prefix} {title.strip()} (done {date_clean})")

    # Extract permanent decisions
    decisions_pattern = _re.compile(
        r"## Permanent Decisions\s*\n"
        r"\|[^|\n]+\|[^|\n]+\|[^|\n]+\|[^|\n]+\|\n"
        r"\|[-\s|]+\|\n"
        r"((?:\|[^|\n]+\|[^|\n]+\|[^|\n]+\|[^|\n]+\|\n)*)",
        _re.MULTILINE,
    )
    decisions_match = decisions_pattern.search(content)
    if decisions_match:
        decision_rows = decisions_match.group(1).strip()
        if decision_rows:
            parts.append("Permanent decisions from previous plans:")
            row_pattern = _re.compile(
                r"\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
            )
            for row in row_pattern.finditer(decision_rows):
                decision, value, reason, task = [g.strip() for g in row.groups()]
                if decision:  # Skip empty placeholder rows
                    parts.append(f"  - {decision}: {value} ({reason}) [from {task}]")

    if not parts:
        return "No previous plan history found."

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Project context gathering
# ---------------------------------------------------------------------------


def gather_project_context(
    project_dir: Path,
    provider: ArchitectProvider | None = None,
) -> str:
    """Build a context string describing current project state.

    Includes:
    - File tree (filtered: no __pycache__, .git, node_modules, .venv)
    - Project rules file (``AGENTS.md`` for OpenCode, ``CLAUDE.md`` for
      Claude Code) — adapts to the selected provider
    - PROGRESS.md content if exists
    - docs/ file names and first 80 lines of each
    - tasks/ existing task names

    Symlinks pointing outside the project directory are excluded to
    prevent the agent from accessing files outside the working folder.

    Args:
        project_dir: The project root directory
        provider: The AI CLI provider.  When provided, the correct
            project-rules file is read (``AGENTS.md`` for OpenCode,
            ``CLAUDE.md`` for Claude Code).  When ``None``, both files
            are checked and the first one found is used.

    Returns:
        A context string describing the project state.
    """
    parts: list[str] = []
    total_chars = 0
    max_chars = 20000

    def add_part(header: str, content: str) -> None:
        nonlocal total_chars
        if total_chars + len(header) + len(content) + 10 > max_chars:
            return
        parts.append(f"{header}\n{content}")
        total_chars += len(header) + len(content)

    # File tree — use os.walk with topdown=True so we can prune skip_dirs
    # in-place before descending into them.  This avoids traversing node_modules,
    # .git, etc. entirely — critical for large projects with 50k+ files.
    tree_lines = ["File tree: bounded; large repos are summarized"]
    skip_dirs = {
        "__pycache__",
        ".git",
        "node_modules",
        ".venv",
        ".architect",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "dist",
        "build",
        "coverage",
    }
    resolved_root = project_dir.resolve()
    max_tree_lines = 260
    tree_truncated = False

    for dirpath_str, dirnames, filenames in os.walk(
        str(project_dir), topdown=True, followlinks=False
    ):
        if len(tree_lines) >= max_tree_lines:
            tree_truncated = True
            break

        dirpath = Path(dirpath_str)

        # Prune skip_dirs in-place so os.walk never descends into them.
        dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs)

        # Emit a line for each subdirectory entry (so the tree shows dirs too)
        rel_dir = dirpath.relative_to(project_dir)
        if rel_dir.parts:  # skip the root itself
            # Check symlink safety for the directory
            if dirpath.is_symlink():
                try:
                    if not dirpath.resolve().is_relative_to(resolved_root):
                        # Remove from traversal and skip
                        dirnames.clear()
                        continue
                except (OSError, ValueError):
                    dirnames.clear()
                    continue
            indent = "  " * (len(rel_dir.parts) - 1)
            tree_lines.append(f"{indent}{rel_dir.name}/")
            if len(tree_lines) >= max_tree_lines:
                tree_truncated = True
                dirnames.clear()
                break

        # Emit sorted file entries
        for filename in sorted(filenames):
            if len(tree_lines) >= max_tree_lines:
                tree_truncated = True
                dirnames.clear()
                break
            if filename.startswith("architect_eval_"):
                continue
            path = dirpath / filename
            # Skip symlinks that resolve outside the project root
            if path.is_symlink():
                try:
                    if not path.resolve().is_relative_to(resolved_root):
                        continue
                except (OSError, ValueError):
                    continue
            rel = path.relative_to(project_dir)
            indent = "  " * (len(rel.parts) - 1)
            tree_lines.append(f"{indent}{rel.name}")

    if tree_truncated:
        tree_lines.append("... tree truncated; use focused search for deeper files ...")

    add_part("## File Tree", "\n".join(tree_lines))

    # Project rules file — adapt to the selected provider.
    # OpenCode uses AGENTS.md, Claude Code uses CLAUDE.md.
    # When no provider is specified, check both (AGENTS.md first for backward compat).
    rules_files: list[tuple[str, Path]] = []
    if provider is not None:
        if provider.name == "claude-code":
            rules_files = [("CLAUDE.md", project_dir / "CLAUDE.md")]
        else:
            rules_files = [("AGENTS.md", project_dir / "AGENTS.md")]
    else:
        rules_files = [
            ("AGENTS.md", project_dir / "AGENTS.md"),
            ("CLAUDE.md", project_dir / "CLAUDE.md"),
        ]

    for label, rules_path in rules_files:
        if rules_path.exists():
            try:
                content = rules_path.read_text(encoding="utf-8")
                add_part(f"## {label}", content)
            except OSError as e:
                logger.warning(f"Failed to read {label}: {e}")
            break  # Only include the first one found

    # PROGRESS.md — historical summary only (not full content)
    # Including the full PROGRESS.md confuses the architect into thinking
    # it should continue the old plan. We extract only completed tasks and
    # permanent decisions — useful context that does NOT include active state
    # like "Next task to run" or "Current State".
    progress_md = project_dir / "tasks" / "PROGRESS.md"
    if progress_md.exists():
        try:
            content = progress_md.read_text(encoding="utf-8")
            summary = _summarize_progress_historical(content)
            add_part(
                "## Previous Plan History (context only — do NOT continue this plan)",
                summary,
            )
        except OSError as e:
            logger.warning(f"Failed to read PROGRESS.md: {e}")

    # Documentation directories. Support both common names; this is deliberately
    # shallow so very large docs trees do not dominate the planning prompt.
    for docs_dir_name in ("documentation", "docs"):
        docs_dir = project_dir / docs_dir_name
        if docs_dir.exists() and docs_dir.is_dir():
            docs_lines = [f"{docs_dir_name}/ directory contents:"]
            for doc_file in sorted(docs_dir.iterdir())[:12]:
                if doc_file.is_file():
                    docs_lines.append(f"- {doc_file.name}")
                    try:
                        lines = doc_file.read_text(encoding="utf-8").splitlines()[:80]
                        if lines:
                            docs_lines.append("  ```")
                            docs_lines.extend(lines)
                            docs_lines.append("  ```")
                    except OSError:
                        pass
            add_part(f"## Documentation ({docs_dir_name}/)", "\n".join(docs_lines))

    # tasks/ status — show current task files with a note that they may be
    # leftovers from a previous run (archiving happens at plan-start, so files
    # visible here are from the most recent completed or interrupted session).
    tasks_dir = project_dir / "tasks"
    if tasks_dir.exists() and tasks_dir.is_dir():
        task_lines = [
            "tasks/ — files present (may be leftover from the previous run; "
            "treat as historical context only):"
        ]
        current_tasks = [
            f
            for f in sorted(tasks_dir.iterdir())
            if f.is_file() and f.suffix == ".md" and not f.name.startswith("architect_eval_")
        ]
        if current_tasks:
            for task_file in current_tasks:
                task_lines.append(f"- {task_file.stem}")
        else:
            task_lines.append("(no task files present)")

        # Show archive summary — each timestamped folder is one past execution session
        archive_dir = tasks_dir / "archive"
        if archive_dir.exists() and archive_dir.is_dir():
            archive_sessions = sorted(
                [d for d in archive_dir.iterdir() if d.is_dir()],
                key=lambda d: d.name,
            )
            if archive_sessions:
                task_lines.append("")
                task_lines.append(
                    "tasks/archive/ — completed past execution sessions "
                    "(already executed, do NOT re-plan):"
                )
                for session_dir in archive_sessions:
                    session_tasks = [
                        f.stem
                        for f in sorted(session_dir.iterdir())
                        if f.is_file()
                        and f.suffix == ".md"
                        and f.name != "INSTRUCTIONS.md"
                        and not f.name.startswith("architect_eval_")
                    ]
                    goal_hint = ""
                    instructions = session_dir / "INSTRUCTIONS.md"
                    if instructions.exists():
                        try:
                            first_lines = instructions.read_text(encoding="utf-8").splitlines()
                            # Extract goal from ## Goal section
                            in_goal = False
                            for ln in first_lines:
                                if ln.strip() == "## Goal":
                                    in_goal = True
                                    continue
                                if in_goal and ln.startswith("## "):
                                    break
                                if in_goal and ln.strip():
                                    goal_hint = f' — "{ln.strip()[:80]}"'
                                    break
                        except OSError:
                            pass
                    tasks_str = ", ".join(session_tasks) if session_tasks else "no tasks"
                    task_lines.append(f"- {session_dir.name}{goal_hint}: [{tasks_str}]")

        add_part("## Tasks", "\n".join(task_lines))

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Instruction builder
# ---------------------------------------------------------------------------


def _next_task_number(tasks_dir: Path) -> int:
    """Return the next available task number based on existing task files.

    Scans tasks_dir for files matching TXX_*.md and returns max(existing) + 1,
    or 1 if no tasks exist yet.

    Args:
        tasks_dir: Directory containing task files.

    Returns:
        The next task number to use (1-based).
    """
    import re

    if not tasks_dir.exists():
        return 1

    highest = 0
    for f in tasks_dir.iterdir():
        m = re.match(r"[Tt](\d+)", f.name)
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


def build_planning_instruction(request: PlanningRequest, context: str) -> str:
    """Build the instruction string to send to OpenCode's architect agent.

    Includes an explicit project-root boundary so the architect never
    creates files outside the working folder.

    The instruction is structured with context in priority order:
        1. ARCHITECT.md (persistent project intelligence)
        2. Structure report (auto-detected project structure)
        3. Additional context files (user-provided)
        4. Project context (file tree, PROGRESS.md history)
        5. User's goal

    Args:
        request: The planning request with goal and model size.
        context: The gathered project context string.

    Returns:
        The complete instruction string for opencode run.
    """
    next_num = _next_task_number(request.project_dir / "tasks")
    next_prefix = f"T{next_num:02d}"
    project_root = str(request.project_dir)

    # Absolute path for tasks dir — spelled out so the architect cannot
    # misinterpret a sub-directory mentioned in the goal as the output location.
    abs_tasks_dir = str(request.project_dir / "tasks")

    lines = [
        f"PROJECT ROOT: {project_root}",
        "BOUNDARY: You MUST NOT read, write, or modify any file outside this project root. "
        "Do not use absolute paths that point outside this directory. "
        "Do not `cd` above this directory. All work must stay within the project root.",
        "",
    ]

    # 1. ARCHITECT.md — persistent project intelligence (highest priority context)
    if request.architect_md_content:
        lines.extend(
            [
                "=== ARCHITECT.md — Persistent Project Intelligence ===",
                request.architect_md_content,
                "",
            ]
        )

    # 2. Project structure report
    if request.structure_report:
        lines.extend(
            [
                "=== PROJECT STRUCTURE REPORT ===",
                request.structure_report,
                "",
            ]
        )

    # 3. Additional context files (user-provided via --context)
    if request.context_content:
        lines.extend(
            [
                "=== ADDITIONAL CONTEXT FILES ===",
                request.context_content,
                "",
            ]
        )

    # 4. Project context (file tree, PROGRESS.md history, etc.)
    lines.extend(
        [
            "=== PROJECT CONTEXT ===",
            context,
            "",
        ]
    )

    # 5. User's goal
    lines.extend(
        [
            "=== USER REQUEST ===",
            f"Goal: {request.goal}",
            f"Scope: {_SCOPE_GUIDE.get(request.scope.value, _SCOPE_GUIDE['standard'])}",
            "",
        ]
    )

    # If no explicit goal was provided but context exists, instruct the architect
    if not request.goal and request.context_content:
        lines.extend(
            [
                "NOTE: No explicit goal was provided. "
                "Derive the goal from the context files provided above.",
                "",
            ]
        )

    lines.extend(
        [
            "=== INSTRUCTIONS ===",
            "IMPORTANT: Any 'Previous Plan History' in the context above is from a "
            "PREVIOUS planning session.",
            "You are creating a NEW plan for a NEW goal. Do NOT continue the old plan.",
            "The Architect tool will write fresh PROGRESS.md after you create task files.",
            "",
            "YOUR REQUIRED OUTPUTS: write task files, write goal-specific tasks/INSTRUCTIONS.md, "
            "and curate ARCHITECT.md durable project intelligence.",
            "Do NOT write PROGRESS.md.",
            "IMPORTANT EXECUTION LIFECYCLE: task agents, not the planner, must update "
            "tasks/PROGRESS.md when they complete work.",
            "IMPORTANT BUILD LIFECYCLE: in this repository, every completed task must "
            "increment root /version.py __build__ by exactly 1, even docs-only, "
            "content-only, no-op, or simple verification tasks.",
            "Never tell task agents to skip PROGRESS.md updates or skip the build bump.",
            "tasks/INSTRUCTIONS.md is the current goal's cross-task contract: include sequence, "
            "dependencies, goal-specific assumptions, shared contracts expected during this run, "
            "verification strategy, boundaries, and what later task agents must read from "
            "PROGRESS.md. Do NOT duplicate ARCHITECT.md project-level knowledge there.",
            "",
            "ABOUT ARCHITECT.md:",
            "  ARCHITECT.md is durable project intelligence, not run history.",
            "  Use it for repo knowledge, stack, architecture, contracts, constraints, "
            "decisions, lessons, and best practices only.",
            "  Do NOT append the current goal, task list, or planning history there; "
            "run history belongs in tasks/SUMMARY.md.",
            "  When you update it, append durable rows/entries only — "
            "no extra --- dividers, no blank lines between rows.",
            "",
            "ABOUT tasks/ AND tasks/archive/:",
            "  Any task files shown in tasks/ are LEFTOVERS from the previous run.",
            "  They will be archived automatically — treat them as historical context only.",
            "  tasks/archive/ contains ALREADY-EXECUTED sessions — do NOT re-plan that work.",
            "  Start fresh task numbering from the number given below.",
            "  Historical T/R numbers in PROGRESS.md or archive summaries do NOT reserve numbers.",
            "  If the first task file below says T01, create T01 even when old history "
            "mentions T01.",
            "",
            "CRITICAL — WHERE TO WRITE TASK FILES:",
            f"  Task files MUST go in: {abs_tasks_dir}/",
            "  The goal may mention other directories (e.g. /mbi/, /maze/, /some/path/).",
            "  Those are the TARGET of the work — NOT where you write task files.",
            "  Task files always go to the path above, regardless of what the goal says.",
            "",
            f"First task file: {abs_tasks_dir}/{next_prefix}_<descriptive_name>.md",
            f"Number subsequent tasks {next_prefix[0]}{next_num + 1:02d}, "
            f"{next_prefix[0]}{next_num + 2:02d}, etc.",
            "Do NOT skip numbers. Do NOT continue numbering from previous plan history or "
            "archive entries.",
            "Do NOT read, write, or modify AGENTS.md or CLAUDE.md — "
            "those files belong to the user.",
        ]
    )
    return "\n".join(lines)


_LIFECYCLE_CONTRACT = """

---

## The Architect Lifecycle Contract

These rules override any contrary text above:

- Every completed task must update `tasks/PROGRESS.md` with the real outcome before marking Done.
- Every completed task in this repository must increment root `/version.py` `__build__` by
  exactly 1.
- This applies to docs-only, content-only, no-op, verification-only, and simple tasks.
- Do not mark a task Done if the progress update or build bump is missing.
""".strip()


_LIFECYCLE_CONTRADICTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"do\s+not\s+[^\n]*(?:progress\.md|build|version|__build__)", re.IGNORECASE),
    re.compile(r"no\s+(?:build\s+counter\s+)?bump", re.IGNORECASE),
    re.compile(
        r"not\s+part\s+of\s+the\s+architect'?s\s+standard\s+task\s+lifecycle", re.IGNORECASE
    ),
    re.compile(r"do\s+not\s+[^\n]*task\s+completion", re.IGNORECASE),
)


def _ensure_lifecycle_contract(path: Path) -> bool:
    """Append mandatory execution lifecycle rules when planner output contradicts them."""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    if _LIFECYCLE_CONTRACT in content:
        return False

    if not any(pattern.search(content) for pattern in _LIFECYCLE_CONTRADICTION_PATTERNS):
        return False

    path.write_text(content.rstrip() + "\n\n" + _LIFECYCLE_CONTRACT + "\n", encoding="utf-8")
    logger.warning(f"Appended mandatory lifecycle contract to planner output: {path}")
    return True


def _enforce_planning_lifecycle_contract(tasks_dir: Path, tasks: list[Task]) -> int:
    """Correct planner output that exempts tasks from progress/build lifecycle rules."""
    updated = 0
    instructions_md = tasks_dir / "INSTRUCTIONS.md"
    if instructions_md.exists() and _ensure_lifecycle_contract(instructions_md):
        updated += 1
    for task in tasks:
        if task.path.exists() and _ensure_lifecycle_contract(task.path):
            updated += 1
    return updated


# ---------------------------------------------------------------------------
# Stray task file rescue
# ---------------------------------------------------------------------------


def _rescue_stray_tasks(project_dir: Path, tasks_dir: Path) -> int:
    """Find task files the architect wrote outside tasks_dir and move them in.

    The architect model sometimes writes task files into a sub-directory
    mentioned in the goal (e.g. ``mbi/tasks/``) instead of the canonical
    ``tasks/`` directory.  This function scans the entire project tree for
    any files matching the ``TXX_*.md`` pattern that are NOT already inside
    ``tasks_dir``, and moves them there.

    Skip dirs that should never be scanned: ``.git``, ``node_modules``,
    ``.venv``, ``__pycache__``, ``.architect``, ``.pytest_cache``.

    Args:
        project_dir: The project root directory (where architect was run from).
        tasks_dir: The canonical tasks directory (project_dir/tasks/).

    Returns:
        Number of files rescued (moved into tasks_dir).
    """
    import re

    skip_dirs = {".git", "node_modules", ".venv", "__pycache__", ".architect", ".pytest_cache"}
    task_pattern = re.compile(r"^[TR]\d+_.+\.md$")
    rescued = 0

    tasks_dir.mkdir(parents=True, exist_ok=True)
    resolved_tasks_dir = tasks_dir.resolve()
    resolved_project = project_dir.resolve()

    # Use os.walk with topdown=True to prune skip_dirs before descending —
    # avoids traversing node_modules, .git, etc. entirely.
    for dirpath_str, dirnames, filenames in os.walk(
        str(project_dir), topdown=True, followlinks=False
    ):
        # Prune ignored directories in-place
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]

        dirpath = Path(dirpath_str)
        for filename in filenames:
            if not filename.endswith(".md"):
                continue

            path = dirpath / filename

            # Skip anything already inside the canonical tasks dir
            try:
                path.resolve().relative_to(resolved_tasks_dir)
                continue  # already in the right place
            except ValueError:
                pass

            # Skip symlinks pointing outside the project
            if path.is_symlink():
                try:
                    if not path.resolve().is_relative_to(resolved_project):
                        continue
                except (OSError, ValueError):
                    continue

            if not task_pattern.match(path.name):
                continue

            # This is a stray task file — move it to tasks_dir
            dest = tasks_dir / path.name
            if dest.exists():
                logger.warning(
                    f"Stray task file {path} conflicts with existing {dest} — skipping move"
                )
                continue

            try:
                path.rename(dest)
                logger.info(f"Rescued stray task file: {path} → {dest}")
                rescued += 1
            except OSError as e:
                logger.warning(f"Failed to move stray task file {path}: {e}")

    if rescued:
        logger.info(f"Rescued {rescued} stray task file(s) into {tasks_dir}")

    return rescued


# ---------------------------------------------------------------------------
# PROGRESS.md writer
# ---------------------------------------------------------------------------


def _write_progress_md(progress_file: Path, tasks: list[Task]) -> None:
    """Write a fresh PROGRESS.md from the discovered task list.

    The Architect always owns PROGRESS.md — it is always written here after
    planning, never delegated to the architect.  This guarantees the file
    is always at the correct project-root path regardless of what directory
    the architect chose to work in.

    Args:
        progress_file: Absolute path to write PROGRESS.md.
        tasks: Ordered list of tasks just created by the architect.
    """
    if not tasks:
        return

    first_prefix = tasks[0].prefix

    rows = "\n".join(f"| {t.prefix} | {t.title} | Pending | — |" for t in tasks)

    content = PROGRESS_TEMPLATE.format(
        tasks_completed=0,
        next_task=first_prefix,
        task_rows=rows + "\n",
        current_state=(
            f"Planning complete. {len(tasks)} task(s) created; next agent should start "
            f"with {first_prefix}, read ARCHITECT.md, then update this file with real progress."
        ),
        last_summary=(
            "No execution work has run yet. Future task agents must record what changed, "
            "what was verified, what is missing, and any lessons learned."
        ),
    )

    progress_file.parent.mkdir(parents=True, exist_ok=True)
    progress_file.write_text(content, encoding="utf-8")
    logger.info(f"Written PROGRESS.md with {len(tasks)} tasks, starting at {first_prefix}")


# ---------------------------------------------------------------------------
# tasks/INSTRUCTIONS.md writer
# ---------------------------------------------------------------------------


def _write_instructions_md(
    instructions_file: Path,
    goal: str,
    tasks: list[Task],
    architect_content: str | None = None,
) -> None:
    """Write tasks/INSTRUCTIONS.md at the canonical project-root location.

    The Architect always owns the location of this file.  If the architect wrote
    a richer version at the correct path, that content is used as-is.
    Otherwise architect generates a minimal but complete version.

    This guarantees the file is always at the correct path regardless of
    what directory the architect chose to work in.

    Args:
        instructions_file: Absolute path to write tasks/INSTRUCTIONS.md.
        goal: The user's original planning goal.
        tasks: Ordered list of tasks created by the architect.
        architect_content: Content written by the architect at this exact
            path, if any.  Used as-is when present.
    """
    if architect_content is not None:
        # Architect wrote a rich version at the right place — keep it.
        instructions_file.parent.mkdir(parents=True, exist_ok=True)
        instructions_file.write_text(architect_content, encoding="utf-8")
        logger.info("tasks/INSTRUCTIONS.md written by architect — kept as-is")
        return

    # Architect either skipped it or wrote it somewhere else — generate a
    # goal-specific fallback. Do not duplicate ARCHITECT.md here; executors read
    # both files, and ARCHITECT.md owns durable project-level intelligence.
    rows = "\n".join(f"| {t.prefix} | {t.title or t.name} | — |" for t in tasks)
    sequence = "\n".join(
        f"{idx}. `{t.prefix}` — {t.title or t.name}" for idx, t in enumerate(tasks, start=1)
    )
    content = (
        "# The Architect — Project Instructions\n\n"
        "## Goal\n"
        f"{goal}\n\n"
        "## Goal-Specific Plan\n"
        "This file is the run-level contract for the current goal. ARCHITECT.md owns "
        "durable project knowledge; this file owns cross-task context, sequencing, "
        "goal-specific assumptions, and handoff expectations for this task package.\n\n"
        f"{sequence}\n\n"
        "## Cross-Task Context\n"
        "- Execute tasks in order unless PROGRESS.md or a reassessment changes the next task.\n"
        "- Treat each completed task's PROGRESS.md outcome as the source of truth for "
        "newly finalized contracts, missing work, and downstream impact.\n"
        "- Do not duplicate project-level notes from ARCHITECT.md here; refer to it for "
        "stack, component authority, commands, best practices, and constraints.\n\n"
        "## Goal-Specific Contracts\n"
        "- Contracts finalized during this run must be recorded in PROGRESS.md by the "
        "task that finalizes them so later tasks use the real names and shapes.\n"
        "- If no contract is finalized yet, later tasks must inspect current code and "
        "previous task outcomes before inventing names or fields.\n\n"
        "## Boundaries For This Run\n"
        "- Stay focused on the current goal. Do not opportunistically refactor unrelated "
        "systems or expand scope beyond the task package.\n"
        "- Use ARCHITECT.md for project-level rules, but record goal-specific deviations "
        "or temporary constraints in PROGRESS.md.\n\n"
        "## Progress Memory\n"
        "PROGRESS.md is the handoff between task agents. It must record real progress, "
        "what changed, what is missing, lessons learned, verification output, and any "
        "facts the next task needs. Do not use it as a vague completion note.\n\n"
        "## Verification For This Run\n"
        "- Each task must run the most relevant checks for its changed area.\n"
        "- Cross-task or integration changes must include broader validation once the "
        "dependent pieces exist.\n"
        "- If verification discovers a project-level command or constraint missing from "
        "ARCHITECT.md, update ARCHITECT.md before marking the task Done.\n\n"
        "## Execution Contract\n"
        "- Read `ARCHITECT.md` for durable project intelligence before making changes.\n"
        "- Read `PROGRESS.md` at the start of every task to understand current state, "
        "last outcome, missing work, lessons, and downstream impact notes.\n"
        "- Read this file for the current goal's task sequence and cross-task rules.\n"
        "- Treat completed tasks as historical context; do not redo them unless "
        "the current task explicitly requires a correction.\n"
        "- When a task changes architecture, contracts, or assumptions for later tasks, "
        "record that clearly in `PROGRESS.md` so follow-up planning can adjust.\n"
        "- If you discover durable repo knowledge, add it to `ARCHITECT.md` before "
        "marking the task Done.\n"
        "- Verify the task with the smallest correct set of checks before marking it done.\n\n"
        "## Reassessment Rules\n"
        "- Remaining tasks may be refined after each completed task when new facts "
        "materially change the plan.\n"
        "- Reassessment should only update pending tasks, never completed tasks.\n"
        "- Prefer minimal task edits: preserve numbering and intent unless a real "
        "downstream change is required.\n\n"
        "## Tasks\n\n"
        "| Task | Title | Scope |\n"
        "|------|-------|-------|\n"
        f"{rows}\n"
    )
    instructions_file.parent.mkdir(parents=True, exist_ok=True)
    instructions_file.write_text(content, encoding="utf-8")
    logger.info(f"Written tasks/INSTRUCTIONS.md with {len(tasks)} tasks")


def _write_goal_md(tasks_dir: Path, goal: str) -> None:
    """Write tasks/GOAL.md as the durable original goal for this planning chain."""
    if not goal.strip():
        return

    goal_file = tasks_dir / GOAL_FILE_NAME
    goal_file.parent.mkdir(parents=True, exist_ok=True)
    goal_file.write_text(
        "\n".join(
            [
                "# The Architect — Original Goal",
                "",
                goal.strip(),
                "",
            ]
        ),
        encoding="utf-8",
    )
    logger.info("Written tasks/GOAL.md with original planning goal")


def _sync_goal_md(tasks_dir: Path, goal: str, *, preserve_existing: bool = False) -> None:
    """Synchronize tasks/GOAL.md with the current planning lifecycle."""
    if goal.strip():
        _write_goal_md(tasks_dir, goal)
        return

    if preserve_existing:
        return

    goal_file = tasks_dir / GOAL_FILE_NAME
    try:
        if goal_file.exists() and goal_file.is_file():
            goal_file.unlink()
            logger.info("Removed stale tasks/GOAL.md before non-loop planning")
    except OSError as exc:
        logger.warning(f"Failed to remove stale tasks/GOAL.md: {exc}")


# ---------------------------------------------------------------------------
# Pre-planning helpers — pending task detection and archive
# ---------------------------------------------------------------------------


def check_pending_tasks(tasks_dir: Path, progress_file: Path) -> list[str]:
    """Return the names of any task files that are not yet in a terminal state.

    Used before planning to warn the user that unfinished work exists.
    Considers all T- and R-prefixed tasks.

    A task is considered *unfinished* when its PROGRESS.md row is absent
    or in any non-terminal state (typically ``Pending``).  Tasks that
    have been marked ``Done``, ``Failed``, or ``Blocked`` by the runner
    are treated as resolved and are not returned — ``Failed`` and
    ``Blocked`` rows represent deliberate resolutions the planner must
    not silently retry.  To resurface a failed task, a human (or a
    reviewer-generated R-task) must either flip its row back to
    ``Pending`` or create a follow-up task.

    Args:
        tasks_dir: Directory containing task files.
        progress_file: Path to PROGRESS.md.

    Returns:
        List of task names (e.g. ``["T03_api", "T04_tests"]``) whose
        PROGRESS.md row is missing or non-terminal.
    """
    from the_architect.core.progress import task_is_resolved

    if not tasks_dir.exists():
        return []

    pending: list[str] = []
    for task in discover_tasks(tasks_dir):
        if not task_is_resolved(progress_file, task.prefix):
            pending.append(task.name)

    return pending


def archive_previous_run(
    tasks_dir: Path,
    log_dir: Path,
    progress_file: Path,
) -> Path | None:
    """Archive task files from the previous run and clear the log directory.

    Moves all T- and R-prefixed task files, **INSTRUCTIONS.md**, and
    **SUMMARY.md**
    into ``tasks/archive/YYYY-MM-DD_HHMMSS/`` so history is preserved but
    the new planning session starts clean.

    ``GOAL.md`` is intentionally left in place. It stores the original goal
    for an Infinite Loop chain and must be readable before every new planning
    iteration, even after the previous task package is archived.

    INSTRUCTIONS.md and SUMMARY.md are archived alongside the task files because
    they contain the original goal, stack information, architecture notes, final
    outcomes, and retrospective information that make archived tasks meaningful.

    Also clears the log directory (``log_dir``) because logs are internal
    The Architect artifacts — they have no user value after a run completes and
    can mislead the IMP-05 retry context if left from a previous goal.

    Args:
        tasks_dir: Directory containing task files.
        log_dir: The Architect log directory (e.g. ``.architect/logs``).
        progress_file: Path to PROGRESS.md (used to name the archive).

    Returns:
        Path to the created archive directory, or None if there was nothing
        to archive.
    """
    import re as _re

    task_pattern = _re.compile(r"^[TRt][0-9]", _re.IGNORECASE)

    if not tasks_dir.exists():
        return None

    # Collect task files to archive (exclude subdirs)
    to_archive = [
        f
        for f in tasks_dir.iterdir()
        if f.is_file() and f.suffix == ".md" and task_pattern.match(f.name)
    ]

    # Also include INSTRUCTIONS.md — it contains the goal, stack, and plan
    # context that makes the archived task files meaningful
    instructions_md = tasks_dir / "INSTRUCTIONS.md"
    if instructions_md.exists() and instructions_md.is_file():
        to_archive.append(instructions_md)

    summary_md = tasks_dir / "SUMMARY.md"
    if summary_md.exists() and summary_md.is_file():
        to_archive.append(summary_md)

    if not to_archive:
        # Nothing to archive — still clear logs
        _clear_log_dir(log_dir)
        return None

    # Create timestamped archive directory
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    archive_dir = tasks_dir / "archive" / timestamp
    archive_dir.mkdir(parents=True, exist_ok=True)

    for f in to_archive:
        try:
            shutil.move(str(f), archive_dir / f.name)
            logger.info(f"Archived {f.name} → tasks/archive/{timestamp}/")
        except OSError as e:
            logger.warning(f"Failed to archive {f.name}: {e}")

    # Clear logs — internal artifacts, safe to remove
    _clear_log_dir(log_dir)

    logger.info(f"Archived {len(to_archive)} file(s) to tasks/archive/{timestamp}/")
    return archive_dir


def _clear_log_dir(log_dir: Path) -> None:
    """Remove all files inside log_dir without removing the directory itself.

    Args:
        log_dir: The log directory to clear.
    """
    if not log_dir.exists():
        return
    cleared = 0
    for f in log_dir.iterdir():
        if not f.is_file():
            continue
        # Preserve the persistent runtime log: the loop driver and the
        # persistent TUI runner write lifecycle traces to
        # ``the_architect.log`` and ``architect_runtime.log``. Wiping
        # them between iterations would delete the very evidence we need
        # to diagnose loop continuation regressions.
        if f.name in {"the_architect.log", "architect_runtime.log"}:
            continue
        try:
            f.unlink()
            cleared += 1
        except OSError as e:
            logger.warning(f"Failed to remove log {f.name}: {e}")
    if cleared:
        logger.info(f"Cleared {cleared} log file(s) from {log_dir}")


# ---------------------------------------------------------------------------
# OpenCode-based planner
# ---------------------------------------------------------------------------


async def run_planner(
    request: PlanningRequest,
    config: ArchitectConfig,
    log_path: Path | None = None,
    provider: ArchitectProvider | None = None,
    renderer: StreamRenderer | None = None,
) -> PlanningResult:
    """Run the architect agent via the configured provider to plan tasks.

    Ensures prompts are written first, then calls the provider's CLI with
    the architect role.  Output goes directly to the terminal.

    For OpenCode: uses ``--agent architect`` with a dedicated planning config.
    For Claude Code: injects the architect prompt as a prefix in the instruction.

    Args:
        request: The planning request with goal and project info.
        config: The The Architect configuration.
        log_path: Optional path to capture the planning session transcript.
        provider: The AI CLI provider to use.  Defaults to OpenCode when
            not specified (backward-compatible behaviour).
        renderer: Optional :class:`StreamRenderer` that receives the
            planner's streamed output. When ``None`` (the legacy
            default), output falls through to :class:`PlainStreamRenderer`
            which writes to ``stdout`` — that path is only safe in a
            plain terminal because Textual's alt-screen swallows
            ``stdout`` writes. TUI callers should pass
            :class:`~the_architect.tui.renderer.WaitLogRenderer` so
            the user actually sees what the planner is doing.

    Returns:
        PlanningResult with created tasks and status.

    Raises:
        PlanningFailedError: If no tasks were created after planning.
    """
    if provider is None:
        from the_architect.core.opencode_provider import OpenCodeProvider

        provider = OpenCodeProvider()

    project_dir = request.project_dir

    # Ensure tasks directory exists
    tasks_dir = project_dir / "tasks"
    tasks_dir.mkdir(exist_ok=True)

    # Archive task files from the previous run and clear logs.
    archive_previous_run(tasks_dir, config.log_dir, config.progress_file)

    # Persist the user's original planning goal before invoking the planner.
    # The planner may narrow INSTRUCTIONS.md to the selected cycle goal, but
    # GOAL.md remains the durable source for Infinite Loop continuation.
    # Outside Infinite Loop, an empty/derived planning request must not inherit
    # a stale goal from the previous completed run.
    preserve_goal = bool(
        getattr(config, "_infinite_loop_enabled", False)
        or getattr(config, "_infinite_loop_chain_enabled", False)
    )
    _sync_goal_md(tasks_dir, request.goal, preserve_existing=preserve_goal)

    # Snapshot tasks before planning so we can report what's new
    tasks_before = {s.name for s in discover_tasks(tasks_dir)}

    # Ensure prompts (and provider-specific planning config) are written
    provider.ensure_setup(project_dir, config)

    # Build the instruction with project context embedded
    context = gather_project_context(project_dir, provider=provider)
    instruction = build_planning_instruction(request, context)

    # For Claude Code: prepend the architect prompt since there are no named agents
    if not provider.supports_agents():
        from the_architect.core.claude_code_provider import ClaudeCodeProvider

        if isinstance(provider, ClaudeCodeProvider):
            architect_prompt = provider.get_architect_prompt()
            instruction = f"{architect_prompt}\n\n---\n\n{instruction}"

    logger.info(f"Running architect agent via {provider.display_name} for planning")

    # Priority: explicit request override > standalone mode > provider default
    model_override = request.model_override or config.standalone_mode or None

    # Config override for OpenCode planning (points to .architect/architect.json)
    # For Claude Code this is None (ignored by the provider)
    config_override: Path | None = None
    agent_override: str | None = None
    if provider.supports_agents():
        config_override = project_dir / ".architect" / "architect.json"
        agent_override = "architect"

    # ── Planning retries ────────────────────────────────────────────────
    # Up to 3 real attempts for transient failures (30s pause between them).
    # Cooldown waits (rate-limit / quota exhausted) do NOT consume a retry
    # slot — the run pauses for the full cooldown period then tries again,
    # exactly as task execution does.  This keeps fire-and-forget behaviour:
    # the user walks away and The Architect handles everything autonomously.
    #
    # Two signals are checked for cooldown:
    #   1. stream_result.rate_limit_hit — set by the provider's output parser
    #      even when accumulated_text is empty (e.g. Claude Code stream-json
    #      mode where error events don't populate accumulated_text).
    #   2. detect_cooldown_signal(accumulated_text, exit_code) — text-pattern
    #      matching for providers that embed quota messages in their output.
    # Both are checked so the detection works across all providers.
    _PLANNING_MAX_ATTEMPTS = 3
    _PLANNING_RETRY_PAUSE = 30  # seconds between transient-failure retries

    from the_architect.core.circuit import (
        ProviderErrorKind,
        detect_cooldown_signal,
        detect_provider_error,
    )
    from the_architect.core.tmux import PaddedConsole

    _console = PaddedConsole()

    attempt = 0
    task_names: list[str] = []
    tasks_after: list[Task] = []
    while attempt < _PLANNING_MAX_ATTEMPTS:
        attempt += 1
        stream_result = await stream_provider(
            instruction=instruction,
            project_dir=project_dir,
            provider=provider,
            model_override=model_override,
            agent_override=agent_override,
            log_path=log_path,
            config_override=config_override,
            renderer=renderer,
        )

        if stream_result.exit_code != 0:
            logger.warning(
                f"{provider.display_name} architect exited with code {stream_result.exit_code}"
            )

        # Rescue any task files the architect wrote outside the canonical tasks_dir.
        _rescue_stray_tasks(project_dir, tasks_dir)

        # Discover tasks — now guaranteed to be in the canonical tasks_dir.
        tasks_after = discover_tasks(tasks_dir)
        task_names = [s.name for s in tasks_after]

        if task_names:
            # Planning succeeded — at least one task was created.
            break

        # ── Actionable provider error detection ────────────────────────────────
        # Check before cooldown handling so budget/billing failures do not turn
        # into a misleading one-hour wait loop.
        provider_error = detect_provider_error(
            stream_result.accumulated_text,
            stream_result.exit_code,
        )
        if provider_error is not None and provider_error.kind in (
            ProviderErrorKind.UPDATE_REQUIRED,
            ProviderErrorKind.MISCONFIGURED,
            ProviderErrorKind.QUOTA_EXHAUSTED,
        ):
            if provider_error.kind == ProviderErrorKind.UPDATE_REQUIRED:
                # Also check the provider for a precise version message
                update_msg = provider.check_update_available()
                if update_msg:
                    error_msg = update_msg
                else:
                    error_msg = f"{provider_error.message}\n  → {provider_error.action}"
                _console.print(f"\n[bold red]⛔  {error_msg}[/bold red]")
                logger.error(
                    f"Planning aborted — provider update required: {provider_error.message}"
                )
            elif provider_error.kind == ProviderErrorKind.QUOTA_EXHAUSTED:
                error_msg = f"{provider_error.message}\n  → {provider_error.action}"
                _console.print(f"\n[bold yellow]⚠  {error_msg}[/bold yellow]")
                logger.error(
                    f"Planning aborted — provider quota exhausted: {provider_error.message}"
                )
            else:
                error_msg = f"{provider_error.message}\n  → {provider_error.action}"
                _console.print(f"\n[bold yellow]⚠  {error_msg}[/bold yellow]")
                logger.error(f"Planning aborted — provider misconfigured: {provider_error.message}")
            raise PlanningFailedError(error_msg)

        # ── Cooldown detection ───────────────────────────────────────────
        # Priority order (most precise first):
        #   1. stream_result.cooldown_until — Unix timestamp from rate_limit_event.resetsAt.
        #      This is the most accurate: the provider tells us exactly when to retry.
        #   2. stream_result.rate_limit_hit — set by the parser for any rate-limit signal
        #      (rate_limit_event, api_error_status 429/529, error="rate_limit" field).
        #      Fall back to 1-hour minimum when no timestamp is available.
        #   3. detect_cooldown_signal(accumulated_text) — text-pattern matching for
        #      providers that embed quota messages in plain-text output.
        import time as _time

        cooldown_detected = False
        cooldown_wait_secs = 3600  # default: 1 hour minimum
        cooldown_signal = ""

        if stream_result.cooldown_until and stream_result.cooldown_until > 0:
            # Precise reset time from the provider — compute exact seconds to wait.
            now_ts = int(_time.time())
            wait = stream_result.cooldown_until - now_ts
            cooldown_wait_secs = max(wait, 60)  # never wait less than 60s even if clock skew
            cooldown_detected = True
            cooldown_signal = f"resetsAt={stream_result.cooldown_until}"
        elif stream_result.rate_limit_hit:
            # rate_limit_hit set but no precise timestamp — use 1-hour minimum.
            cooldown_detected = True
            cooldown_signal = "rate_limit signal"
        else:
            # Fall back to text-pattern matching (OpenCode / plain-text providers).
            cooldown_detected, cooldown_wait_secs, cooldown_signal = detect_cooldown_signal(
                stream_result.accumulated_text,
                stream_result.exit_code,
            )

        if cooldown_detected:
            wait_mins = cooldown_wait_secs // 60
            wait_secs_remainder = cooldown_wait_secs % 60
            time_str = (
                f"{wait_mins}m {wait_secs_remainder}s" if wait_secs_remainder else f"{wait_mins}m"
            )
            logger.warning(
                f"Planning: provider cooldown detected ({cooldown_signal}) — "
                f"pausing {cooldown_wait_secs}s ({time_str}) before retry"
            )
            _console.print(
                f"[yellow]⏳  Provider cooldown detected ({cooldown_signal}). "
                f"Waiting {time_str} before retrying planning...[/yellow]"
            )
            # Wait in 60-second chunks, logging progress each minute so the
            # user can see the run is alive if they glance at the terminal.
            waited = 0
            while waited < cooldown_wait_secs:
                chunk = min(60, cooldown_wait_secs - waited)
                try:
                    await asyncio.sleep(chunk)
                except asyncio.CancelledError:
                    logger.warning("Planning cooldown wait interrupted")
                    break
                waited += chunk
                still_remaining = cooldown_wait_secs - waited
                if still_remaining > 0:
                    logger.info(
                        f"Planning cooldown wait in progress — {int(still_remaining)}s remaining"
                    )
                    _console.print(
                        f"[dim]   ⏳  {int(still_remaining)}s remaining "
                        "until planning retry...[/dim]"
                    )
            logger.info("Planning cooldown wait complete — retrying")
            _console.print("[green]✓  Cooldown elapsed. Retrying planning...[/green]")
            # Do NOT consume a retry slot — cooldown waits are free retries.
            attempt -= 1
            continue

        if provider_error is not None and provider_error.kind == ProviderErrorKind.UNKNOWN:
            # Unknown error — surface the output snippet for visibility but
            # still allow retries (the error may be transient).
            logger.warning(f"Provider error detected (attempt {attempt}): {provider_error.message}")
            _console.print(f"[dim]  Provider output: {provider_error.action}[/dim]")

        # ── Transient failure ─────────────────────────────────────────────────
        # No tasks created and no cooldown signal — transient failure.
        # Consume a retry slot and pause briefly before the next attempt.
        if attempt < _PLANNING_MAX_ATTEMPTS:
            logger.warning(
                f"Planning attempt {attempt}/{_PLANNING_MAX_ATTEMPTS} created no tasks — "
                f"retrying in {_PLANNING_RETRY_PAUSE}s"
            )
            _console.print(
                f"[red]⚠  Planning attempt {attempt}/{_PLANNING_MAX_ATTEMPTS} failed "
                f"(no tasks created). Retrying in {_PLANNING_RETRY_PAUSE}s...[/red]"
            )
            await asyncio.sleep(_PLANNING_RETRY_PAUSE)

    if not task_names:
        # All attempts exhausted — no tasks created.
        logger.error("Planning did not create any tasks after all retries")
        raise PlanningFailedError(
            f"No tasks were created during planning after {_PLANNING_MAX_ATTEMPTS} attempts"
        )

    new_tasks = [n for n in task_names if n not in tasks_before]

    # --- The Architect owns PROGRESS.md unconditionally ---
    # Always (re)write it from the discovered task list so it is guaranteed
    # to be at the correct tasks/ path with the correct content.
    # Whatever the architect may have written anywhere is irrelevant.
    progress_md = project_dir / "tasks" / "PROGRESS.md"
    _write_progress_md(progress_md, tasks_after)

    # --- The Architect owns tasks/INSTRUCTIONS.md location unconditionally ---
    # If the architect wrote it at the correct path, keep the richer content.
    # If it wrote it somewhere else (or not at all), generate a clean version.
    # Either way the file ends up exactly at project_dir/tasks/INSTRUCTIONS.md.
    instructions_md = tasks_dir / "INSTRUCTIONS.md"
    architect_instructions: str | None = None
    if instructions_md.exists():
        try:
            architect_instructions = instructions_md.read_text(encoding="utf-8")
        except OSError:
            architect_instructions = None
    _write_instructions_md(instructions_md, request.goal, tasks_after, architect_instructions)

    lifecycle_updates = _enforce_planning_lifecycle_contract(tasks_dir, tasks_after)
    if lifecycle_updates:
        logger.warning(
            f"Planning output contained lifecycle-rule contradictions; "
            f"corrected {lifecycle_updates} file(s)"
        )

    # Note: project rules file belongs to the user — we read it for context but never write it
    if provider is not None and provider.name == "claude-code":
        rules_md = project_dir / "CLAUDE.md"
    else:
        rules_md = project_dir / "AGENTS.md"

    result = PlanningResult(
        tasks_created=task_names,
        agents_md_read=rules_md.exists(),
        progress_md_written=True,
        instructions_md_written=True,
        summary=f"Planning complete. {len(new_tasks)} new task(s) created.",
    )

    logger.info(f"Planning complete: {len(new_tasks)} new tasks, {len(task_names)} total")
    return result
