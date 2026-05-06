"""Retrospective review stage for The Architect.

After execution completes — whether all tasks succeeded or some failed —
the reviewer agent assesses the work done, identifies quality issues,
and can create fix-up tasks (R-prefixed) for the next execution round.

Flow:
    Planning → Execution → Retrospective 1 → Execution → Retrospective 2 → Execution → Done

The reviewer acts as a supervisor and advisor — it does not design new features,
it reviews what was built, verifies quality, and prescribes targeted fixes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import BaseModel, Field

from the_architect.config import ArchitectConfig
from the_architect.core.planner import _rescue_stray_tasks, _summarize_progress_historical
from the_architect.core.progress import task_is_done
from the_architect.core.runner import StreamRenderer, stream_provider
from the_architect.core.tasks import Task, discover_tasks

if TYPE_CHECKING:
    from the_architect.core.provider import ArchitectProvider


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RetrospectiveFailedError(Exception):
    """Raised when a retrospective review fails critically."""

    pass


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class RetrospectiveRequest(BaseModel):
    """Request to run a retrospective review round."""

    round_number: int = Field(ge=1, description="Which retrospective round (1-based)")
    project_dir: Path = Field(description="The project root directory")
    original_goal: str = Field(default="", description="The user's original planning goal")
    model_override: str | None = Field(
        default=None,
        description="Model to use for the reviewer — same as the architect model by default",
    )

    model_config = {"frozen": True}


class RetrospectiveResult(BaseModel):
    """Result of a retrospective review round."""

    tasks_created: list[str] = Field(
        default_factory=list,
        description="List of R-prefixed task names that were created",
    )
    summary: str = Field(default="", description="Summary of what was reviewed and found")
    issues_found: int = Field(default=0, description="Number of issues identified")
    fixes_planned: int = Field(default=0, description="Number of fix-up tasks created")


class ReassessmentResult(BaseModel):
    """Result of a lightweight architect reassessment pass."""

    tasks_updated: list[str] = Field(default_factory=list, description="Pending tasks updated")
    summary: str = Field(default="", description="What the reassessment changed")


def _find_eval_snapshot_files(project_dir: Path) -> list[Path]:
    """Return leftover architect_eval snapshot files outside skipped directories."""
    skip_dirs = {"__pycache__", ".git", "node_modules", ".venv", ".architect", ".pytest_cache"}
    return [
        path
        for path in sorted(project_dir.rglob("architect_eval_*"))
        if path.is_file() and not any(skip in path.parts for skip in skip_dirs)
    ]


# ---------------------------------------------------------------------------
# Next R-task number
# ---------------------------------------------------------------------------


def _next_r_task_number(tasks_dir: Path) -> int:
    """Return the next available R-prefixed task number.

    Scans tasks_dir for files matching RXX_*.md and returns max(existing) + 1,
    or 1 if no R-prefixed tasks exist yet.

    Args:
        tasks_dir: Directory containing task files.

    Returns:
        The next R-task number to use (1-based).
    """
    if not tasks_dir.exists():
        return 1

    highest = 0
    for f in tasks_dir.iterdir():
        m = re.match(r"^[Rr](\d+)", f.name)
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


# ---------------------------------------------------------------------------
# Context gathering for reviewer
# ---------------------------------------------------------------------------


def _gather_review_context(project_dir: Path, original_goal: str) -> str:
    """Build a context string describing what the reviewer should assess.

    Includes:
    - Original planning goal
    - PROGRESS.md historical summary (completed tasks, decisions)
    - Current PROGRESS.md state (what's done, what failed)
    - List of all task files and their headings
    - Recent file changes (file tree)

    Args:
        project_dir: The project root directory.
        original_goal: The user's original planning goal.

    Returns:
        A context string for the reviewer agent.
    """
    parts: list[str] = []
    total_chars = 0
    max_chars = 12000  # Reviewer needs more context than planner

    def add_part(header: str, content: str) -> None:
        nonlocal total_chars
        if total_chars + len(header) + len(content) + 10 > max_chars:
            return
        parts.append(f"{header}\n{content}")
        total_chars += len(header) + len(content)

    # Original goal
    add_part("## Original Goal", original_goal)

    # PROGRESS.md — full current state (reviewer needs to see what failed)
    progress_md = project_dir / "PROGRESS.md"
    if progress_md.exists():
        try:
            content = progress_md.read_text(encoding="utf-8")
            add_part("## Current PROGRESS.md (full content)", content)
        except OSError as e:
            logger.warning(f"Failed to read PROGRESS.md: {e}")

    # Historical summary (completed tasks, permanent decisions)
    if progress_md.exists():
        try:
            content = progress_md.read_text(encoding="utf-8")
            summary = _summarize_progress_historical(content)
            add_part("## Previous Plan History (context only)", summary)
        except OSError as e:
            logger.warning(f"Failed to read PROGRESS.md for history: {e}")

    # Task files — list with headings
    tasks_dir = project_dir / "tasks"
    if tasks_dir.exists() and tasks_dir.is_dir():
        task_lines = ["Existing task files:"]
        for task_file in sorted(tasks_dir.iterdir()):
            if task_file.name.startswith("architect_eval_"):
                continue
            if task_file.is_file() and task_file.suffix == ".md":
                task_lines.append(f"- {task_file.name}")
                # Read first line (heading) for context
                try:
                    first_line = task_file.read_text(encoding="utf-8").split("\n", 1)[0].strip()
                    if first_line.startswith("#"):
                        task_lines.append(f"  → {first_line}")
                except OSError:
                    pass
        add_part("## Task Files", "\n".join(task_lines))

    # File tree — filtered
    tree_lines = ["File tree:"]
    skip_dirs = {"__pycache__", ".git", "node_modules", ".venv", ".architect", ".pytest_cache"}
    resolved_root = project_dir.resolve()
    for path in sorted(project_dir.rglob("*")):
        if any(skip in path.parts for skip in skip_dirs):
            continue
        if path.name.startswith("architect_eval_"):
            continue
        if path.is_symlink():
            try:
                if not path.resolve().is_relative_to(resolved_root):
                    continue
            except (OSError, ValueError):
                continue
        rel = path.relative_to(project_dir)
        indent = "  " * (len(rel.parts) - 1)
        tree_lines.append(f"{indent}{rel.name}")
    add_part("## File Tree", "\n".join(tree_lines))

    eval_files = _find_eval_snapshot_files(project_dir)
    if eval_files:
        eval_lines = [
            "WARNING: architect_eval snapshot files remain in the project.",
            "These files should have been deleted by the executor after successful validation.",
            "Their presence indicates possible truncation corruption or incomplete task execution.",
            "Investigate each snapshot against its corresponding original file.",
            "",
        ]
        for eval_file in eval_files:
            rel = eval_file.relative_to(project_dir)
            original_name = eval_file.name.replace("architect_eval_", "", 1)
            original_path = eval_file.parent / original_name
            try:
                snapshot_size = eval_file.stat().st_size
            except OSError:
                snapshot_size = 0
            try:
                original_size = original_path.stat().st_size if original_path.exists() else 0
            except OSError:
                original_size = 0
            pct = int((original_size / snapshot_size) * 100) if snapshot_size > 0 else 0
            eval_lines.append(
                f"- {rel} -> original: {original_name} "
                f"(snapshot: {snapshot_size}B, current: {original_size}B, {pct}% of snapshot)"
            )
        add_part("## Leftover Eval Snapshot Files", "\n".join(eval_lines))

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Instruction builder
# ---------------------------------------------------------------------------


def build_retrospective_instruction(request: RetrospectiveRequest, context: str) -> str:
    """Build the instruction string to send to OpenCode's reviewer agent.

    Includes an explicit project-root boundary so the reviewer never
    creates files outside the working folder.

    Args:
        request: The retrospective request with round number and project info.
        context: The gathered review context string.

    Returns:
        The complete instruction string for opencode run.
    """
    tasks_dir = request.project_dir / "tasks"
    next_num = _next_r_task_number(tasks_dir)
    next_prefix = f"R{next_num:02d}"
    project_root = str(request.project_dir)
    abs_tasks_dir = str(tasks_dir)

    lines = [
        f"PROJECT ROOT: {project_root}",
        "BOUNDARY: You MUST NOT read, write, or modify any file outside this project root. "
        "Do not use absolute paths that point outside this directory. "
        "Do not `cd` above this directory. All work must stay within the project root.",
        "",
        f"=== RETROSPECTIVE ROUND {request.round_number} ===",
        "",
        "=== PROJECT CONTEXT ===",
        context,
        "",
        "=== YOUR ROLE ===",
        "You are the retrospective reviewer — a supervisor and advisor, not a planner.",
        "Review the work that was done, assess quality, and create fix-up tasks ONLY if needed.",
        "If everything looks good, do not write any task files.",
        "",
        "=== INSTRUCTIONS ===",
        "1. Read PROGRESS.md to understand what was done and what (if anything) failed",
        "2. Read the task files in tasks/ to understand what was planned",
        "3. Read the actual code that was written or modified",
        "4. Run the test suite (e.g., pytest) to verify everything passes",
        "5. Assess: completeness, quality, tests, consistency, correctness",
        "6. If you find issues, write R-prefixed fix-up task files",
        "7. If everything is clean, write no task files at all",
        "",
        "CRITICAL — WHERE TO WRITE TASK FILES:",
        f"  Task files MUST go in: {abs_tasks_dir}/",
        "  Use the R prefix for all fix-up tasks — never T or S.",
        f"  First fix-up task: {abs_tasks_dir}/{next_prefix}_<descriptive_name>.md",
        f"  Number subsequent tasks R{next_num + 1:02d}, R{next_num + 2:02d}, etc.",
        "  Do NOT skip numbers. Do NOT reuse numbers from existing task files.",
        "  Do NOT modify existing T or S task files.",
        "  Do NOT write PROGRESS.md or INSTRUCTIONS.md — The Architect handles those.",
        "",
        "IMPORTANT: Do NOT write any task files if your review finds no issues.",
        "The Architect will detect that no new tasks were created "
        "and skip the next execution round.",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PROGRESS.md updater for retrospective tasks
# ---------------------------------------------------------------------------


def _update_progress_with_retrospective_tasks(
    progress_file: Path,
    new_tasks: list[Task],
) -> None:
    """Update PROGRESS.md to include new R-prefixed retrospective tasks.

    Reads the current PROGRESS.md, adds the new tasks as Pending rows,
    and updates the Next task to run to the first new task if there are
    no other pending tasks.

    Args:
        progress_file: Path to PROGRESS.md.
        new_tasks: List of new R-prefixed tasks to add.
    """
    if not new_tasks:
        return

    try:
        content = progress_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        logger.warning("Could not read PROGRESS.md to add retrospective tasks")
        return

    # Find the last row in the Task Log table
    # We need to insert new rows after existing ones
    task_log_pattern = re.compile(
        r"(## Task Log\s*\n\s*\|.*\n\|[-\s|]+\|\n)((?:\|.*\|\n)*)",
        re.MULTILINE,
    )
    match = task_log_pattern.search(content)
    if not match:
        logger.warning("Could not find Task Log in PROGRESS.md")
        return

    existing_rows = match.group(2)

    # Build new rows
    new_rows = ""
    for task in new_tasks:
        new_rows += f"| {task.prefix} | {task.name} | Pending | — |\n"

    # Insert new rows after existing ones
    updated_rows = existing_rows + new_rows
    content = content[: match.start(2)] + updated_rows + content[match.end(2) :]

    # Update "Next task to run" — if it was "—" or a task that's now Done,
    # point to the first new R task
    next_task_match = re.search(r"\*\*Next task to run:\*\*\s*(.+)", content)
    if next_task_match:
        current_next = next_task_match.group(1).strip()
        # If current next is "—" or a Done task, update to first new R task
        if current_next == "—" or current_next == "":
            content = (
                content[: next_task_match.start(1)]
                + new_tasks[0].prefix
                + content[next_task_match.end(1) :]
            )
        else:
            # Check if the current next task is already Done
            if task_is_done(progress_file, current_next):
                content = (
                    content[: next_task_match.start(1)]
                    + new_tasks[0].prefix
                    + content[next_task_match.end(1) :]
                )

    try:
        progress_file.write_text(content, encoding="utf-8")
        logger.info(f"Updated PROGRESS.md with {len(new_tasks)} retrospective task(s)")
    except OSError as e:
        logger.warning(f"Failed to update PROGRESS.md: {e}")


# ---------------------------------------------------------------------------
# OpenCode-based retrospective runner
# ---------------------------------------------------------------------------


async def run_retrospective(
    request: RetrospectiveRequest,
    config: ArchitectConfig,
    log_path: Path | None = None,
    provider: ArchitectProvider | None = None,
    renderer: StreamRenderer | None = None,
) -> RetrospectiveResult:
    """Run the reviewer agent via the configured provider to assess completed work.

    For OpenCode: uses ``--agent reviewer`` with the planning config.
    For Claude Code: injects the reviewer prompt as a prefix in the instruction.

    Args:
        request: The retrospective request with round number and project info.
        config: The The Architect configuration.
        log_path: Optional path to capture the retrospective session transcript.
        provider: The AI CLI provider to use.  Defaults to OpenCode when
            not specified (backward-compatible behaviour).
        renderer: Optional :class:`StreamRenderer` for live output. TUI
            callers should pass a
            :class:`~the_architect.tui.renderer.WaitLogRenderer` bound
            to the active wait session so reviewer output is visible
            in the wait-screen log tail instead of being swallowed by
            Textual's alt-screen.

    Returns:
        RetrospectiveResult with created tasks and review summary.

    Raises:
        RetrospectiveFailedError: If the reviewer fails critically.
    """
    if provider is None:
        from the_architect.core.opencode_provider import OpenCodeProvider

        provider = OpenCodeProvider()

    project_dir = request.project_dir
    tasks_dir = project_dir / "tasks"
    tasks_dir.mkdir(exist_ok=True)

    # Snapshot tasks before review so we can report what's new
    tasks_before = {t.name for t in discover_tasks(tasks_dir)}

    # Ensure prompts (and provider-specific planning config) are written
    provider.ensure_setup(project_dir, config)

    # Build the instruction with review context embedded
    context = _gather_review_context(project_dir, request.original_goal)
    instruction = build_retrospective_instruction(request, context)

    # For Claude Code: prepend the reviewer prompt since there are no named agents
    if not provider.supports_agents():
        from the_architect.core.claude_code_provider import ClaudeCodeProvider

        if isinstance(provider, ClaudeCodeProvider):
            reviewer_prompt = provider.get_reviewer_prompt()
            instruction = f"{reviewer_prompt}\n\n---\n\n{instruction}"

    logger.info(
        f"Running retrospective round {request.round_number} via reviewer agent "
        f"({provider.display_name})"
    )

    model_override = request.model_override or config.standalone_mode or None

    # Config override for OpenCode (points to .architect/architect.json)
    config_override: Path | None = None
    agent_override: str | None = None
    if provider.supports_agents():
        config_override = project_dir / ".architect" / "architect.json"
        agent_override = "reviewer"

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
            f"{provider.display_name} reviewer exited with code {stream_result.exit_code}"
        )

    # Rescue any task files the reviewer wrote outside the canonical tasks_dir.
    _rescue_stray_tasks(project_dir, tasks_dir)

    # Discover tasks — find new R-prefixed ones
    tasks_after = discover_tasks(tasks_dir)
    new_task_names = [t.name for t in tasks_after if t.name not in tasks_before]
    new_r_tasks = [t for t in tasks_after if t.name in new_task_names]

    # Update PROGRESS.md with any new R-prefixed tasks
    if new_r_tasks:
        progress_md = project_dir / "PROGRESS.md"
        _update_progress_with_retrospective_tasks(progress_md, new_r_tasks)

    # Count issues from the log if possible (heuristic: count R-tasks created)
    fixes_planned = len(new_r_tasks)

    result = RetrospectiveResult(
        tasks_created=new_task_names,
        summary=(
            f"Retrospective round {request.round_number} complete. "
            f"{fixes_planned} fix-up task(s) created."
            if fixes_planned > 0
            else f"Retrospective round {request.round_number} complete. No issues found."
        ),
        issues_found=fixes_planned,  # Each fix task represents at least one issue
        fixes_planned=fixes_planned,
    )

    logger.info(
        f"Retrospective round {request.round_number}: "
        f"{len(new_task_names)} new task(s), {len(tasks_after)} total"
    )
    return result


async def run_task_reassessment(
    project_dir: Path,
    provider: ArchitectProvider,
    config: ArchitectConfig,
    completed_task: str,
    outcome_summary: str,
    original_goal: str,
    model_override: str | None = None,
    log_path: Path | None = None,
    renderer: StreamRenderer | None = None,
    task_status: str = "done",
    force: bool = False,
) -> ReassessmentResult:
    """Run a targeted architect reassessment after a task with downstream impact."""
    eval_files = _find_eval_snapshot_files(project_dir) if config.integrity else []
    needs_reassessment = bool(outcome_summary) and "Downstream impact: possible" in outcome_summary
    failed_task = task_status == "failed"
    if not force and not needs_reassessment and not failed_task and not eval_files:
        return ReassessmentResult(summary="No reassessment needed.")

    tasks_dir = project_dir / "tasks"
    provider.ensure_setup(project_dir, config)

    pending_tasks = discover_tasks(tasks_dir)
    before_contents: dict[str, str] = {}
    task_sections: list[str] = []
    for task in pending_tasks:
        if task_is_done(project_dir / "PROGRESS.md", task.prefix):
            continue
        try:
            text = task.path.read_text(encoding="utf-8")
        except OSError:
            continue
        before_contents[task.name] = text
        task_sections.append(f"## {task.path.name}\n{text}")

    try:
        progress_content = (project_dir / "PROGRESS.md").read_text(encoding="utf-8")
    except OSError:
        progress_content = ""

    # Load ARCHITECT.md so the reassessment agent has full project memory:
    # permanent decisions, constraints, and lessons learned.
    architect_md_content = ""
    try:
        from the_architect.core.architect_md import read_architect_md

        architect_md_content = read_architect_md(project_dir) or ""
    except Exception:
        pass  # Non-fatal — reassessment proceeds without it

    eval_warning = ""
    if eval_files:
        eval_lines = [
            f"WARNING: {len(eval_files)} architect_eval snapshot file(s) were found "
            f"after task {completed_task} completed.",
            "These snapshots should have been deleted by the executor after validation.",
            "Their presence is a strong signal of truncated or corrupted output, "
            "or an interrupted task.",
            "",
            "Leftover snapshot files:",
        ]
        for eval_file in eval_files:
            rel = eval_file.relative_to(project_dir)
            original_name = eval_file.name.replace("architect_eval_", "", 1)
            original_path = eval_file.parent / original_name
            try:
                snapshot_size = eval_file.stat().st_size
            except OSError:
                snapshot_size = 0
            try:
                original_size = original_path.stat().st_size if original_path.exists() else 0
            except OSError:
                original_size = 0
            pct = int((original_size / snapshot_size) * 100) if snapshot_size > 0 else 0
            eval_lines.append(
                f"  - {rel} (snapshot {snapshot_size}B -> current {original_size}B = {pct}%)"
            )
        eval_lines.append("")
        eval_lines.append(
            "Treat these leftover snapshots as corruption signals. Update pending "
            "work so later tasks do not build on suspicious files."
        )
        eval_warning = "\n".join(eval_lines)

    instruction = "\n".join(
        [
            f"PROJECT ROOT: {project_dir}",
            "You are doing a targeted post-task reassessment, not a full re-plan.",
            f"Task status: {task_status}",
            f"Force reassessment: {'yes' if force else 'no'}",
            *([eval_warning, "---", ""] if eval_warning else []),
            *(
                [
                    "=== ARCHITECT.md — Persistent Project Intelligence ===",
                    architect_md_content,
                    "---",
                    "",
                ]
                if architect_md_content
                else []
            ),
            "Read PROGRESS.md and pending task files only.",
            "Only update pending task files in tasks/ when the completed task materially "
            "changes future work.",
            "Do not modify completed tasks. Do not rewrite the whole plan. Preserve "
            "numbering and intent whenever possible.",
            f"Original goal: {original_goal}",
            f"Task: {completed_task}",
            "=== Outcome Summary ===",
            outcome_summary,
            "=== Current PROGRESS.md ===",
            progress_content,
            "=== Pending Task Files ===",
            "\n\n".join(task_sections),
            "If no changes are needed, make no edits.",
        ]
    )

    await stream_provider(
        instruction=instruction,
        project_dir=project_dir,
        provider=provider,
        model_override=model_override,
        agent_override="architect" if provider.supports_agents() else None,
        log_path=log_path,
        config_override=(project_dir / ".architect" / "architect.json")
        if provider.supports_agents()
        else None,
        renderer=renderer,
    )

    updated: list[str] = []
    for task in discover_tasks(tasks_dir):
        before = before_contents.get(task.name)
        if before is None:
            continue
        try:
            after = task.path.read_text(encoding="utf-8")
        except OSError:
            continue
        if after != before:
            updated.append(task.prefix)

    return ReassessmentResult(
        tasks_updated=updated,
        summary=(
            f"Updated pending tasks after {completed_task}: {', '.join(updated)}"
            if updated
            else f"No pending task changes needed after {completed_task}."
        ),
    )
