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
from the_architect.core.baseline import detect_changes, read_baseline
from the_architect.core.planner import _rescue_stray_tasks, _summarize_progress_historical
from the_architect.core.progress import reconcile_progress_with_task_files, task_is_done
from the_architect.core.provider_setup import (
    ensure_provider_setup,
    existing_provider_setup_is_usable,
    provider_uses_architect_config,
)
from the_architect.core.runner import StreamRenderer, stream_provider
from the_architect.core.tasks import Task, discover_tasks, duplicate_task_prefixes

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
    validation_feedback: str = Field(
        default="",
        description="Failure details from the previous validation gate, if any",
    )
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


def _ensure_provider_setup_for_review(
    provider: ArchitectProvider,
    project_dir: Path,
    config: ArchitectConfig,
) -> None:
    """Ensure review provider setup, reusing existing setup after resource-loader glitches."""
    ensure_provider_setup(provider, project_dir, config)


def _existing_review_setup_is_usable(provider: ArchitectProvider, project_dir: Path) -> bool:
    """Return True when existing review prompts/config are complete enough to reuse."""
    return existing_provider_setup_is_usable(provider, project_dir)


def _provider_uses_architect_config(provider: ArchitectProvider) -> bool:
    """Return True when review routing depends on .architect/architect.json."""
    return provider_uses_architect_config(provider)


def _prepend_provider_prompt(
    provider: ArchitectProvider,
    instruction: str,
    prompt_getter_name: str,
) -> str:
    """Prepend a packaged provider role prompt when named agents are not used."""
    if prompt_getter_name not in dir(provider):
        return instruction
    getter = getattr(provider, prompt_getter_name, None)
    if not callable(getter):
        return instruction
    prompt = str(getter()).strip()
    if not prompt:
        return instruction
    return f"{prompt}\n\n---\n\n{instruction}"


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
# Baseline evidence gathering
# ---------------------------------------------------------------------------


def _gather_baseline_evidence(project_dir: Path) -> str | None:
    """Read workspace baselines and produce a review-context section.

    Discovers JSON baseline files in ``.architect/baselines/``, runs
    :func:`baseline.detect_changes` for each, and formats a per-task
    summary of created/modified/deleted files.  Returns ``None`` when
    the directory is missing or empty so that the caller can skip adding
    a baseline section to the review context.

    Errors during baseline reading or change detection are logged as
    warnings and the offending file is silently skipped.

    Args:
        project_dir: The project root directory.

    Returns:
        A markdown-formatted string for the review context, or ``None``
        when no baseline data is available.
    """
    baselines_dir = project_dir / ".architect" / "baselines"
    if not baselines_dir.is_dir():
        return None

    json_files = sorted(baselines_dir.glob("*.json"))
    if not json_files:
        return None

    lines: list[str] = []
    for json_file in json_files:
        try:
            baseline = read_baseline(json_file)
        except (OSError, ValueError) as exc:
            logger.warning(f"Baseline: cannot read {json_file.name}: {exc!r}")
            continue

        try:
            changes = detect_changes(baseline, project_dir)
        except OSError as exc:
            logger.warning(f"Baseline: cannot detect changes for {json_file.name}: {exc!r}")
            continue

        created = changes.get("created", [])
        modified = changes.get("modified", [])
        deleted = changes.get("deleted", [])
        task_prefix = baseline.task_prefix or json_file.stem

        lines.append(f"### {task_prefix} Baseline")
        lines.append(
            f"- **Created:** {len(created)} file(s)" if created else "- **Created:** 0 file(s)"
        )
        lines.append(
            f"- **Modified:** {len(modified)} file(s)" if modified else "- **Modified:** 0 file(s)"
        )
        lines.append(
            f"- **Deleted:** {len(deleted)} file(s)" if deleted else "- **Deleted:** 0 file(s)"
        )
        if created:
            lines.append("- Created files:")
            for p in created:
                lines.append(f"  - {p}")
        if modified:
            lines.append("- Modified files:")
            for p in modified:
                lines.append(f"  - {p}")
        if deleted:
            lines.append("- Deleted files:")
            for p in deleted:
                lines.append(f"  - {p}")
        lines.append("")

    if not lines:
        return None

    return "\n".join(lines)


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
    progress_md = project_dir / "tasks" / "PROGRESS.md"
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

    # Baseline evidence — per-task file change summaries
    baseline_evidence = _gather_baseline_evidence(project_dir)
    if baseline_evidence:
        add_part("## Task Baseline Evidence", baseline_evidence)

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
    ]

    if request.validation_feedback.strip():
        lines.extend(
            [
                "=== VALIDATION FAILURE FROM PREVIOUS ROUND ===",
                request.validation_feedback.strip(),
                "",
                "Your next fix-up tasks must directly address this validation failure. "
                "If you believe it is a false positive, create a narrowly scoped R-task "
                "that verifies and documents why the cycle is actually clean.",
                "",
            ]
        )

    lines.extend(
        [
            "=== YOUR ROLE ===",
            "You are the retrospective reviewer — a supervisor and advisor, not a planner.",
            "Review the work that was done, assess quality, and create fix-up tasks "
            "ONLY if needed.",
            "If everything looks good, do not write any task files.",
            "",
            "=== HARD SAFETY RULES ===",
            "Do NOT inspect or reason from git history, git status, or git diffs unless "
            "the original task explicitly made git inspection part of the implementation work. "
            "Even when a task mentions git status as a verification command, treat dirty "
            "worktree findings as diagnostic only unless The Architect provides a task-start "
            "baseline proving the current task caused those changes.",
            "Do NOT create fix-up tasks that run destructive git commands or discard work.",
            "Forbidden in R-task instructions: git checkout, git reset, git restore, "
            "git clean, rm -rf, deleting user files, or reverting broad worktree changes.",
            "Dirty worktree findings are diagnostic unless The Architect provides a "
            "task-start baseline proving the current task created those changes.",
            "If destructive recovery might be needed, write a human-action note in the "
            "review summary instead of an executable R-task.",
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
            "  Use only the R prefix for all fix-up task files.",
            f"  First fix-up task: {abs_tasks_dir}/{next_prefix}_<descriptive_name>.md",
            f"  Number subsequent tasks R{next_num + 1:02d}, R{next_num + 2:02d}, etc.",
            "  Do NOT skip numbers. Do NOT reuse numbers from existing task files.",
            "  Create exactly one fix-up task file per RXX prefix. Before finishing, "
            "verify no RXX prefix appears on more than one task file.",
            "  Do NOT modify existing non-R task files.",
            "  Do NOT write PROGRESS.md or INSTRUCTIONS.md — The Architect handles those.",
            "",
            "IMPORTANT: Do NOT write any task files if your review finds no issues.",
            "The Architect will detect that no new tasks were created "
            "and skip the next execution round.",
        ]
    )

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

    # Build new rows. Preserve existing rows/statuses; repeated reviews and
    # interrupted runs must not duplicate R-task entries.
    new_rows = ""
    for task in new_tasks:
        if re.search(rf"^\|\s*{re.escape(task.prefix)}\s+\|", content, re.MULTILINE):
            continue
        new_rows += f"| {task.prefix} | {task.name} | Pending | — |\n"

    if not new_rows:
        logger.info("PROGRESS.md already contained retrospective task row(s)")
        return

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
    _ensure_provider_setup_for_review(provider, project_dir, config)

    # Build the instruction with review context embedded
    context = _gather_review_context(project_dir, request.original_goal)
    instruction = build_retrospective_instruction(request, context)

    logger.info(
        f"Running retrospective round {request.round_number} via reviewer agent "
        f"({provider.display_name})"
    )

    model_override = request.model_override or config.standalone_mode or None

    # Config override for OpenCode (points to .architect/architect.json)
    config_override: Path | None = None
    agent_override: str | None = None
    if provider.supports_agents() and _provider_uses_architect_config(provider):
        config_override = project_dir / ".architect" / "architect.json"
        agent_override = "reviewer"
    else:
        instruction = _prepend_provider_prompt(provider, instruction, "get_reviewer_prompt")

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

    from the_architect.core.circuit import ProviderErrorKind, detect_provider_error

    provider_error = detect_provider_error(stream_result.accumulated_text, stream_result.exit_code)
    if provider_error is not None and provider_error.kind in (
        ProviderErrorKind.UPDATE_REQUIRED,
        ProviderErrorKind.MISCONFIGURED,
        ProviderErrorKind.QUOTA_EXHAUSTED,
    ):
        msg = f"{provider_error.message}\n  -> {provider_error.action}"
        logger.error(f"Retrospective aborted — provider error: {provider_error.message}")
        raise RetrospectiveFailedError(msg)

    # Rescue any task files the reviewer wrote outside the canonical tasks_dir.
    _rescue_stray_tasks(project_dir, tasks_dir)

    # Discover tasks — find new R-prefixed ones
    tasks_after = discover_tasks(tasks_dir)
    duplicates = duplicate_task_prefixes(tasks_after)
    if duplicates:
        details = "; ".join(
            f"{prefix}: {', '.join(names)}" for prefix, names in sorted(duplicates.items())
        )
        raise RetrospectiveFailedError(
            "Retrospective created duplicate task prefixes. Task prefixes are the runtime "
            f"identity and must be unique: {details}"
        )
    new_task_names = [t.name for t in tasks_after if t.name not in tasks_before]
    new_r_tasks = [t for t in tasks_after if t.name in new_task_names]
    invalid_new_tasks = [t.name for t in new_r_tasks if not t.prefix.startswith("R")]
    if invalid_new_tasks:
        raise RetrospectiveFailedError(
            "Retrospective created non-R task files. Reviewers may create only "
            f"R-prefixed fix-up tasks: {', '.join(sorted(invalid_new_tasks))}"
        )

    # Update PROGRESS.md with any new R-prefixed tasks
    if new_r_tasks:
        progress_md = project_dir / "tasks" / "PROGRESS.md"
        _update_progress_with_retrospective_tasks(progress_md, new_r_tasks)
        repaired = reconcile_progress_with_task_files(progress_md, tasks_after)
        if repaired:
            logger.info("Reconciled PROGRESS.md rows after retrospective: " + ", ".join(repaired))

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
    _ensure_provider_setup_for_review(provider, project_dir, config)

    pending_tasks = discover_tasks(tasks_dir)
    before_contents: dict[str, str] = {}
    task_sections: list[str] = []
    for task in pending_tasks:
        if task_is_done(project_dir / "tasks" / "PROGRESS.md", task.prefix):
            continue
        try:
            text = task.path.read_text(encoding="utf-8")
        except OSError:
            continue
        before_contents[task.name] = text
        task_sections.append(f"## {task.path.name}\n{text}")

    try:
        progress_content = (project_dir / "tasks" / "PROGRESS.md").read_text(encoding="utf-8")
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

    config_override = None
    agent_override = None
    if provider.supports_agents() and _provider_uses_architect_config(provider):
        config_override = project_dir / ".architect" / "architect.json"
        agent_override = "architect"
    else:
        instruction = _prepend_provider_prompt(provider, instruction, "get_architect_prompt")

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

    from the_architect.core.circuit import ProviderErrorKind, detect_provider_error

    provider_error = detect_provider_error(stream_result.accumulated_text, stream_result.exit_code)
    if provider_error is not None and provider_error.kind in (
        ProviderErrorKind.UPDATE_REQUIRED,
        ProviderErrorKind.MISCONFIGURED,
        ProviderErrorKind.QUOTA_EXHAUSTED,
    ):
        msg = f"{provider_error.message}\n  -> {provider_error.action}"
        logger.error(f"Reassessment aborted — provider error: {provider_error.message}")
        raise RetrospectiveFailedError(msg)

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
