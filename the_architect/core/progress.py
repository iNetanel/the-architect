"""PROGRESS.md read/write utilities.

Status vocabulary
-----------------

PROGRESS.md tracks every planned task's lifecycle in its "Task Log" table.
The status cell in each row uses one of the following values:

- ``Pending`` — task exists but has not yet been attempted (or is queued).
- ``Done`` — terminal success. The task was verified complete and will not
  be re-attempted on subsequent runs.
- ``Failed`` — terminal failure. The runner exhausted all retries.  The
  row's status cell contains ``Failed`` (optionally with an annotation such
  as ``Failed (3 attempts)``).  Failed tasks are NOT automatically
  re-attempted — they require a reviewer-created R-task or human
  intervention to make progress.
- ``Blocked`` — terminal non-execution. The task could not be attempted due
  to a resource constraint (rate-limit ceiling, token budget, circuit
  breaker cooldown that outlasted the run).  Like ``Failed``, blocked
  tasks are not re-picked automatically.

Only ``Done``, ``Failed``, and ``Blocked`` are considered *terminal* —
i.e. the task is resolved and the main execution loop will not re-run it.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Canonical PROGRESS.md regex helpers
# ---------------------------------------------------------------------------

#: Statuses that mark a task as terminal — the main loop will skip tasks
#: whose PROGRESS.md row shows any of these.  Anything NOT in this set is
#: treated as "still needs work" (Pending, or a malformed/missing row).
TERMINAL_STATUSES: tuple[str, ...] = ("Done", "Failed", "Blocked")


def _task_status_pattern(prefix: str, status: str) -> re.Pattern[str]:
    r"""Return a compiled regex matching a PROGRESS.md row with the given prefix and status.

    The format written by ``_write_progress_md`` in ``planner.py`` is::

        | T01 | Task Name | Pending | — |

    The pattern is flexible enough to handle variable whitespace around each
    field while still requiring an exact status match.  The status match
    only needs to be a **prefix** of the status cell contents, which lets
    us persist annotated terminals like ``Failed (3 attempts)`` while
    still matching them with the base word ``Failed``.  Any trailing
    characters inside the cell (spaces, parenthetical notes) are captured
    into group 2 alongside the cell terminator.

    Args:
        prefix: Task prefix, e.g. ``"T01"`` or ``"R02"``.
        status: Status string, e.g. ``"Done"``, ``"Failed"``, ``"Blocked"``
            or ``"Pending"``.

    Returns:
        Compiled multiline regex.  Match group 1 captures everything before
        the status cell; group 2 captures the trailing cell content plus
        the cell terminator ``|``.  This makes substitution trivial::

            pattern.sub(r"\g<1> NewStatus \g<2>", content)

        Using group 2 to restore the trailing terminator keeps the
        substitution lossless for simple rows and overwrites any
        annotation for previously-annotated rows.
    """
    escaped_prefix = re.escape(prefix)
    escaped_status = re.escape(status)
    # Status match is anchored on the word boundary after the status token
    # so "Done" does not match inside a longer word.  The remainder of the
    # cell (and the pipe terminator) is captured into group 2 so a
    # replacement cleanly overwrites any annotation.
    return re.compile(
        rf"^(\|\s*{escaped_prefix}\s+\|.*\|)\s*{escaped_status}\b[^|]*(\|)",
        re.MULTILINE,
    )


def _task_any_status_pattern(prefix: str) -> re.Pattern[str]:
    r"""Return a compiled regex matching a PROGRESS.md row for ``prefix`` with ANY status.

    Used by :func:`reconcile_task_status` to find the row regardless of what
    the current status cell contains — critical for the runner-driven
    reconciliation path, where we don't know (and don't care) what the
    agent wrote before overwriting it.

    Args:
        prefix: Task prefix, e.g. ``"T01"``.

    Returns:
        Compiled multiline regex with three groups:

        - group 1: everything before the status cell (up to the opening ``|``)
        - group 2: the current status cell contents (without the surrounding pipes)
        - group 3: the trailing cell terminator ``|``
    """
    escaped_prefix = re.escape(prefix)
    return re.compile(
        rf"^(\|\s*{escaped_prefix}\s+\|[^|]*\|)([^|]*)(\|)",
        re.MULTILINE,
    )


def replace_task_status(
    content: str,
    prefix: str,
    old_status: str,
    new_status: str,
) -> str:
    """Replace the status cell for a task row in PROGRESS.md content.

    Finds the row for *prefix* with *old_status* and rewrites it with
    *new_status*.  Returns the content unchanged if no matching row is found.

    This function requires the caller to know the current status.  For the
    runner-driven reconciliation path (where the caller only knows the new
    status), use :func:`reconcile_task_status` instead.

    Args:
        content: The full text of a PROGRESS.md file.
        prefix: Task prefix, e.g. ``"T01"``.
        old_status: The current status to match, e.g. ``"Done"``.
        new_status: The replacement status, e.g. ``"Pending"``.

    Returns:
        Updated content string.
    """
    pattern = _task_status_pattern(prefix, old_status)
    # Restore a single space on either side of the new status so the output
    # matches the formatting produced by ``_write_progress_md``.
    return pattern.sub(rf"\g<1> {new_status} \g<2>", content)


def reconcile_task_status(
    progress_file: Path | str,
    prefix: str,
    new_status: str,
    completed: str | None = None,
) -> bool:
    """Authoritatively rewrite a task row's status cell in PROGRESS.md.

    Unlike :func:`replace_task_status`, this helper does not need to know
    the previous status — it locates the row by ``prefix`` alone and
    overwrites the status cell with ``new_status``.  This is the tool the
    Python runner uses to *reconcile* PROGRESS.md after each task attempt,
    so an agent that forgets to update its own row cannot cause the main
    loop to re-pick the task on the next iteration.

    Also rewrites the ``Completed`` cell (the fourth column) when
    ``completed`` is provided — typically a date for ``Done`` rows or a
    short note like ``3 attempts`` for ``Failed`` rows.

    If the row does not exist in PROGRESS.md (for example, the task file
    was created on disk but never made it into the Task Log) the file is
    left untouched and the function returns ``False``.  The runner logs a
    warning in that case rather than failing — missing rows are never
    fabricated here, to keep this helper safe to call from many code
    paths without risk of corrupting the table.

    Args:
        progress_file: Path to PROGRESS.md.
        prefix: Task prefix, e.g. ``"T01"`` or ``"R02"``.
        new_status: The new status cell value (e.g. ``"Done"``, ``"Failed"``,
            ``"Blocked"``, ``"Pending"``).  Callers should typically pass one
            of the canonical values in :data:`TERMINAL_STATUSES` or
            ``"Pending"``.
        completed: Optional value for the Completed (fourth) cell.  When
            ``None``, the existing Completed cell is preserved.

    Returns:
        ``True`` if the file was updated on disk, ``False`` otherwise.
        ``False`` is returned when the file is missing, unreadable, or
        contains no matching row for ``prefix``.
    """
    if isinstance(progress_file, str):
        progress_file = Path(progress_file)

    if not progress_file.exists():
        return False

    try:
        content = progress_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    pattern = _task_any_status_pattern(prefix)
    match = pattern.search(content)
    if match is None:
        return False

    # Replace the status cell in place.  Restore normal spacing around the
    # new status so the resulting row matches the canonical template format
    # produced by ``_write_progress_md``.
    updated = pattern.sub(rf"\g<1> {new_status} \g<3>", content, count=1)

    # If the caller supplied a Completed value, rewrite the 4th cell too.
    # We locate the row again (now with the new status in place) and edit
    # the cell that follows the status pipe.  This is done as a second pass
    # so the logic for optional Completed edits stays orthogonal.
    if completed is not None:
        completed_pattern = re.compile(
            rf"^(\|\s*{re.escape(prefix)}\s+\|[^|]*\|\s*"
            rf"{re.escape(new_status)}\b[^|]*\|)([^|]*)(\|)",
            re.MULTILINE,
        )
        updated = completed_pattern.sub(rf"\g<1> {completed} \g<3>", updated, count=1)

    if updated == content:
        return False

    try:
        progress_file.write_text(updated, encoding="utf-8")
    except OSError:
        return False
    return True


PROGRESS_TEMPLATE = """\
# The Architect — Progress Tracker

> This file is the memory between tasks.
> Every task MUST read this at the start and rewrite it completely at the end.

---

## Overall Status

**Tasks completed:** {tasks_completed}
**Next task to run:** {next_task}

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
{task_rows}
---

## Current State

{current_state}

## Last Task Summary

{last_summary}

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
"""


class ProgressState(BaseModel):
    """Parsed state from a PROGRESS.md file."""

    tasks_completed: int = Field(default=0, description="Number of completed tasks")
    next_task: str = Field(default="T00", description="Next task to run")
    done_tasks: list[str] = Field(
        default_factory=list, description="List of task prefixes marked ``Done``"
    )
    failed_tasks: list[str] = Field(
        default_factory=list,
        description="List of task prefixes marked ``Failed`` (terminal)",
    )
    blocked_tasks: list[str] = Field(
        default_factory=list,
        description="List of task prefixes marked ``Blocked`` (terminal, resource-limited)",
    )
    raw_content: str = Field(default="", description="Raw file content")

    model_config = {"frozen": False}

    @property
    def resolved_tasks(self) -> list[str]:
        """All terminal task prefixes — Done, Failed, or Blocked.

        Use this when deciding whether a task should be re-attempted: if a
        prefix appears here, the main execution loop should skip it.
        """
        resolved: list[str] = []
        seen: set[str] = set()
        for collection in (self.done_tasks, self.failed_tasks, self.blocked_tasks):
            for prefix in collection:
                if prefix not in seen:
                    seen.add(prefix)
                    resolved.append(prefix)
        return resolved


def init_progress(progress_file: Path | str) -> None:
    """Create a new PROGRESS.md file if it doesn't exist.

    Args:
        progress_file: Path to the PROGRESS.md file to create
    """
    if isinstance(progress_file, str):
        progress_file = Path(progress_file)

    if progress_file.exists():
        return

    progress_file.parent.mkdir(parents=True, exist_ok=True)

    default_state = ProgressState(tasks_completed=0, next_task="T00")
    content = PROGRESS_TEMPLATE.format(
        tasks_completed=default_state.tasks_completed,
        next_task=default_state.next_task,
        task_rows="",
        current_state="No tasks run yet.",
        last_summary="",
    )

    progress_file.write_text(content, encoding="utf-8")


def read_progress(progress_file: Path | str) -> ProgressState:
    """Read and parse a PROGRESS.md file.

    Returns safe defaults if the file is missing or malformed.

    Args:
        progress_file: Path to the PROGRESS.md file

    Returns:
        ProgressState with parsed values or safe defaults
    """
    if isinstance(progress_file, str):
        progress_file = Path(progress_file)

    if not progress_file.exists():
        return ProgressState()

    try:
        content = progress_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ProgressState()

    tasks_completed = 0
    next_task = "T00"
    done_tasks: list[str] = []
    failed_tasks: list[str] = []
    blocked_tasks: list[str] = []

    # Support both new (Tasks) and legacy (Sessions) format
    completed_match = re.search(r"\*\*Tasks completed:\*\*\s*(\d+)", content)
    if not completed_match:
        completed_match = re.search(r"\*\*Sessions completed:\*\*\s*(\d+)", content)
    if completed_match:
        tasks_completed = int(completed_match.group(1))

    next_match = re.search(r"\*\*Next task to run:\*\*\s*([TRS]\d+)", content)
    if not next_match:
        next_match = re.search(r"\*\*Next session to run:\*\*\s*([TRS]\d+)", content)
    if next_match:
        next_task = next_match.group(1)

    # Scan rows once and bucket prefixes by terminal status.  The status
    # match only needs to be a prefix of the cell contents so annotated
    # terminals such as "Failed (3 attempts)" are recognised correctly.
    _row_pattern = re.compile(
        r"^\|\s*([TRS]\d+)\s+\|[^|]*\|\s*(Done|Failed|Blocked)\b[^|]*\|",
        re.MULTILINE,
    )
    for match in _row_pattern.finditer(content):
        task_prefix = match.group(1)
        status = match.group(2)
        if status == "Done" and task_prefix not in done_tasks:
            done_tasks.append(task_prefix)
        elif status == "Failed" and task_prefix not in failed_tasks:
            failed_tasks.append(task_prefix)
        elif status == "Blocked" and task_prefix not in blocked_tasks:
            blocked_tasks.append(task_prefix)

    return ProgressState(
        tasks_completed=tasks_completed,
        next_task=next_task,
        done_tasks=done_tasks,
        failed_tasks=failed_tasks,
        blocked_tasks=blocked_tasks,
        raw_content=content,
    )


def task_is_done(progress_file: Path | str, prefix: str) -> bool:
    """Check if a task has been marked as Done in PROGRESS.md.

    Args:
        progress_file: Path to the PROGRESS.md file
        prefix: Task prefix like "T01" or legacy "S01"

    Returns:
        True if the task is marked as Done, False otherwise
    """
    if isinstance(progress_file, str):
        progress_file = Path(progress_file)

    if not progress_file.exists():
        return False

    try:
        content = progress_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    pattern = _task_status_pattern(prefix, "Done")
    return pattern.search(content) is not None


def task_is_resolved(progress_file: Path | str, prefix: str) -> bool:
    """Check if a task is in any *terminal* state (Done, Failed, or Blocked).

    The main execution loop uses this — not :func:`task_is_done` — to decide
    whether to skip a task.  A task with status ``Failed`` must not be
    silently re-attempted, even though it is not ``Done``; likewise a
    ``Blocked`` task should wait for the condition that blocked it to be
    addressed (usually via a reviewer-generated R-task or human action)
    rather than being re-run immediately.

    Args:
        progress_file: Path to the PROGRESS.md file.
        prefix: Task prefix like ``"T01"`` or ``"R02"``.

    Returns:
        ``True`` when the row for ``prefix`` carries one of the statuses in
        :data:`TERMINAL_STATUSES`, ``False`` otherwise (including the case
        where the file is missing, the row is absent, or the row is
        Pending).
    """
    if isinstance(progress_file, str):
        progress_file = Path(progress_file)

    if not progress_file.exists():
        return False

    try:
        content = progress_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    for status in TERMINAL_STATUSES:
        if _task_status_pattern(prefix, status).search(content) is not None:
            return True
    return False


def task_status(progress_file: Path | str, prefix: str) -> str | None:
    """Return the current status cell value for ``prefix`` in PROGRESS.md.

    Returns the canonical status name (``"Done"``, ``"Failed"``,
    ``"Blocked"``, or ``"Pending"``) when the row exists and its status
    matches a known value.  Returns ``None`` when the file is missing,
    unreadable, the row is absent, or the status cell contains an
    unrecognised value.

    This helper exists for diagnostic callers (dashboards, logs) that
    want to show the user what the current state is without re-parsing
    the whole file.

    Args:
        progress_file: Path to PROGRESS.md.
        prefix: Task prefix.

    Returns:
        The recognised status string, or ``None``.
    """
    if isinstance(progress_file, str):
        progress_file = Path(progress_file)

    if not progress_file.exists():
        return None

    try:
        content = progress_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    for status in (*TERMINAL_STATUSES, "Pending"):
        if _task_status_pattern(prefix, status).search(content) is not None:
            return status
    return None


def get_next_task(progress_file: Path | str) -> str:
    """Get the next task to run from PROGRESS.md.

    Args:
        progress_file: Path to the PROGRESS.md file

    Returns:
        Task prefix like "T01", defaults to "T00" if not found
    """
    if isinstance(progress_file, str):
        progress_file = Path(progress_file)

    if not progress_file.exists():
        return "T00"

    try:
        content = progress_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "T00"

    match = re.search(r"\*\*Next task to run:\*\*\s*([TRS]\d+)", content)
    if not match:
        match = re.search(r"\*\*Next session to run:\*\*\s*([TRS]\d+)", content)
    if match:
        return match.group(1)

    return "T00"
