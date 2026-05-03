"""Task discovery and state management."""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    """Status of a task."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class TaskScope(StrEnum):
    """Scope hint for task decomposition.

    Controls how much work goes into each task, which influences how large
    the executor's context window is likely to need to be per run.
    The actual context used depends on the goal, the codebase, and the
    model — scope is a tendency, not a guarantee.

    - ``SIMPLE``: one atomic thing per task → tends toward smaller context
      per run, safer for weak/local models and large codebases.
    - ``STANDARD``: one feature area per task → balanced context, good default.
    - ``COMPLEX``: one whole subsystem per task → tends toward larger context,
      only suitable for frontier models with big context windows.

    The number of tasks is never fixed — it emerges from goal size ÷ scope.
    The same goal will produce more tasks at SIMPLE scope and fewer at COMPLEX.
    """

    SIMPLE = "simple"
    STANDARD = "standard"
    COMPLEX = "complex"


class Task(BaseModel):
    """A single development task."""

    name: str = Field(description="Full task name e.g. T09_my_task")
    prefix: str = Field(description="Task prefix e.g. T09")
    number: int = Field(description="Task number e.g. 9")
    path: Path = Field(description="Absolute path to task file")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="Current status")
    title: str = Field(default="", description="Human-readable title from file heading")

    model_config = {"frozen": True}


class TaskPlan(BaseModel):
    """Plan containing all tasks and their state."""

    tasks: list[Task] = Field(default_factory=list, description="All discovered tasks")
    next_to_run: Task | None = Field(default=None, description="Next task to execute")

    @property
    def pending(self) -> list[Task]:
        """Return all pending tasks."""
        return [t for t in self.tasks if t.status == TaskStatus.PENDING]

    @property
    def done(self) -> list[Task]:
        """Return all completed tasks."""
        return [t for t in self.tasks if t.status == TaskStatus.DONE]

    @property
    def all_done(self) -> bool:
        """Return True if all tasks are done."""
        return len(self.pending) == 0 and len(self.tasks) > 0

    @property
    def has_pending(self) -> bool:
        """Return True if there are pending tasks."""
        return len(self.pending) > 0


def task_prefix(name: str) -> str:
    """Extract the task prefix from a task name.

    Args:
        name: Task name like "T09_foo", "R01_fix", or "T01"

    Returns:
        The prefix part like "T09", "R01", or "T01"

    Examples:
        >>> task_prefix("T09_foo")
        'T09'
        >>> task_prefix("R01_fix_bugs")
        'R01'
        >>> task_prefix("T01")
        'T01'
    """
    match = re.match(r"^(R\d+)", name)
    if match:
        return match.group(1)
    match = re.match(r"^(T\d+)", name)
    if match:
        return match.group(1)
    # Also support legacy S-prefix
    match = re.match(r"^(S\d+)", name)
    if match:
        return match.group(1)
    return name


def task_number(name: str) -> int:
    """Extract the task number from a task name.

    Args:
        name: Task name like "T09_foo", "R01_fix", or "T01"

    Returns:
        The task number as an integer

    Examples:
        >>> task_number("T09_foo")
        9
        >>> task_number("R01_fix_bugs")
        1
        >>> task_number("T01")
        1
    """
    match = re.search(r"[TRS](\d+)", name)
    if match:
        return int(match.group(1))
    return 0


def _extract_title(file_path: Path, fallback_name: str) -> str:
    """Extract a human-readable title from a task file.

    Reads the first line of the file looking for a Markdown heading like
    ``# T01 — Implement CHANGELOG``.  Strips the prefix and separator
    to return just the title portion (``Implement CHANGELOG``).

    Falls back to the filename stem with underscores replaced by spaces
    and the prefix stripped if no heading is found.

    Args:
        file_path: Absolute path to the task file.
        fallback_name: Filename stem (e.g. ``T01_changelog_and_version``).

    Returns:
        A human-readable title string.
    """
    try:
        first_line = file_path.read_text(encoding="utf-8").split("\n", 1)[0].strip()
    except (OSError, UnicodeDecodeError):
        first_line = ""

    if first_line.startswith("#"):
        # Strip leading "# " or "# " with multiple hashes
        heading = first_line.lstrip("#").strip()
        # Strip optional prefix like "T01 — ", "R01 - ", "T01 "
        heading = re.sub(r"^[TRS]\d+\s*[—–\-]\s*", "", heading)
        heading = re.sub(r"^[TRS]\d+\s+", "", heading)
        if heading:
            return heading

    # Fallback: derive from filename stem
    # "T01_changelog_and_version" → "Changelog and version"
    # "R01_fix_missing_tests" → "Fix missing tests"
    stripped = re.sub(r"^[TRS]\d+_", "", fallback_name)
    return stripped.replace("_", " ").capitalize()


def discover_tasks(tasks_dir: Path | str) -> list[Task]:
    """Discover all task files in a directory.

    Finds all files matching T[digits]_*.md pattern and returns
    them sorted numerically by task number.

    Args:
        tasks_dir: Path to the tasks directory

    Returns:
        List of Task objects sorted by number, empty list if dir missing

    Examples:
        >>> tasks = discover_tasks("/path/to/tasks")
        >>> tasks[0].name
        'T01_first'
    """
    if isinstance(tasks_dir, str):
        tasks_dir = Path(tasks_dir)

    if not tasks_dir.exists() or not tasks_dir.is_dir():
        return []

    tasks: list[Task] = []
    pattern = re.compile(r"^[TRS](\d+)_.+\.md$")

    for entry in tasks_dir.iterdir():
        if entry.name.startswith("architect_eval_"):
            continue
        if entry.is_file() and entry.suffix == ".md":
            match = pattern.match(entry.name)
            if match:
                number = int(match.group(1))
                prefix_letter = entry.name[0]
                prefix = f"{prefix_letter}{number:02d}"
                title = _extract_title(entry.resolve(), entry.stem)
                tasks.append(
                    Task(
                        name=entry.stem,
                        prefix=prefix,
                        number=number,
                        path=entry.resolve(),
                        status=TaskStatus.PENDING,
                        title=title,
                    )
                )

    tasks.sort(key=lambda t: (t.number, t.prefix))
    return tasks
