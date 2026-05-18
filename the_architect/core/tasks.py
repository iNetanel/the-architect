"""Task discovery and state management."""

from __future__ import annotations

import re
from collections.abc import Sequence
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
    depends_on: list[str] = Field(
        default_factory=list,
        description="Task prefixes this task depends on (e.g. ['T01', 'T02'])",
    )

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

    Supports the full prefix grammar:
    - ``T01``      — plain planned task
    - ``T01A``     — split variant A of T01 (reassessment)
    - ``T01B``     — split variant B of T01 (reassessment)
    - ``T01R1``    — first retrospective fix for T01
    - ``T01R2``    — second retrospective fix for T01

    Args:
        name: Task name like ``"T09_foo"``, ``"T01A_bar"``, ``"T04R1_fix"``.

    Returns:
        The prefix part, e.g. ``"T09"``, ``"T01A"``, ``"T04R1"``.

    Examples:
        >>> task_prefix("T09_foo")
        'T09'
        >>> task_prefix("T01A_split")
        'T01A'
        >>> task_prefix("T04R1_fix_bugs")
        'T04R1'
        >>> task_prefix("T01")
        'T01'
    """
    # Full grammar: T<digits>[R<digits>|<uppercase-letter>]
    # Retro suffix and split letter are mutually exclusive.
    match = re.match(r"^(T\d+(?:R\d+|[A-Z])?)(?:_|$)", name)
    if match:
        return match.group(1)
    return name


def task_number(name: str) -> int:
    """Extract the base task number from a task name.

    Always returns the leading integer regardless of split letter or
    retro suffix, so ``T01``, ``T01A``, and ``T01R1`` all return ``1``.

    Args:
        name: Task name like ``"T09_foo"``, ``"T01A_bar"``, ``"T04R1_fix"``.

    Returns:
        The task number as an integer.

    Examples:
        >>> task_number("T09_foo")
        9
        >>> task_number("T01A_split")
        1
        >>> task_number("T04R1_fix_bugs")
        4
        >>> task_number("T01")
        1
    """
    match = re.search(r"T(\d+)", name)
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
        # Strip optional prefix like "T01 — ", "T01A - ", "T04R1 "
        heading = re.sub(r"^T\d+(?:R\d+|[A-Z])?\s*[—–\-]\s*", "", heading)
        heading = re.sub(r"^T\d+(?:R\d+|[A-Z])?\s+", "", heading)
        if heading:
            return heading

    # Fallback: derive from filename stem
    # "T01_changelog_and_version" → "Changelog and version"
    # "T01A_split_part_a"        → "Split part a"
    # "T04R1_fix_missing_tests"  → "Fix missing tests"
    stripped = re.sub(r"^T\d+(?:R\d+|[A-Z])?_", "", fallback_name)
    return stripped.replace("_", " ").capitalize()


def _extract_dependencies(file_path: Path) -> list[str]:
    """Extract dependency declarations from a task file.

    Reads the task file looking for a ``## Dependencies`` section.
    Each dependency is a line starting with ``- `` followed by a task
    prefix (e.g. ``T01``, ``T02``, ``T04R1``).

    Returns an empty list if the section is not found or the file
    cannot be read.

    Args:
        file_path: Absolute path to the task file.

    Returns:
        List of task prefix strings this task depends on.

    Examples:
        Given a task file containing:

        ```markdown
        ## Dependencies
        - T01
        - T02
        ```

        Returns ``["T01", "T02"]``.
    """
    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    dependencies: list[str] = []
    in_section = False

    for line in content.splitlines():
        stripped = line.strip()

        # Check for section header
        if re.match(r"^##\s+Dependencies\s*$", stripped, re.IGNORECASE):
            in_section = True
            continue

        # End of section on next heading
        if in_section and stripped.startswith("##"):
            break

        if not in_section:
            continue

        # Parse dependency line: "- TXX" or "- TXXR1" or "- TXXA"
        dep_match = re.match(r"^-\s+(T\d+(?:R\d+|[A-Z])?)\s*$", stripped)
        if dep_match:
            dependencies.append(dep_match.group(1))

    return dependencies


def task_sort_key(t: Task) -> tuple[int, int, int]:
    """Return a stable sort key for a task, ordering by base number then variant.

    Sort order within the same base number:
      - plain ``T01``              → slot 0
      - split ``T01A``, ``T01B``   → slot 1, sub-sorted by letter ordinal
      - retro ``T01R1``, ``T01R2`` → slot 2, sub-sorted by retro number

    This is the canonical ordering used by :func:`discover_tasks` and must be
    used anywhere :class:`Task` lists are re-sorted — notably when new tasks
    are merged into a live plan after reassessment.

    Args:
        t: The task to produce a sort key for.

    Returns:
        A ``(base_number, variant_slot, sub_sort)`` tuple.

    Examples:
        >>> from pathlib import Path
        >>> t = Task(name='T01_foo', prefix='T01', number=1, path=Path('/tmp/T01_foo.md'))
        >>> task_sort_key(t)
        (1, 0, 0)
        >>> ta = Task(name='T01A_bar', prefix='T01A', number=1, path=Path('/tmp/T01A_bar.md'))
        >>> task_sort_key(ta)
        (1, 1, 65)
        >>> tr = Task(name='T01R1_fix', prefix='T01R1', number=1, path=Path('/tmp/T01R1_fix.md'))
        >>> task_sort_key(tr)
        (1, 2, 1)
    """
    # Strip the leading "T<digits>" from the prefix to get the variant suffix.
    # Regex is safer than slicing by length because zero-padding means "T01"
    # has 3 chars for number=1 while "T10" also has 3 chars for number=10.
    m = re.match(r"^T\d+(.*)$", t.prefix, re.IGNORECASE)
    suffix = m.group(1) if m else ""
    if not suffix:
        return (t.number, 0, 0)
    if suffix.upper().startswith("R"):
        retro_part = suffix[1:]
        retro_num = int(retro_part) if retro_part.isdigit() else 1
        return (t.number, 2, retro_num)
    # Split letter
    return (t.number, 1, ord(suffix.upper()[0]))


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
    # Full grammar: T<digits>[R<digits>|<uppercase-letter>]_<name>.md
    # The retro suffix (R<n>) and the split letter are mutually exclusive.
    # Examples: T01_foo.md, T01A_split.md, T04R1_fix.md
    # re.IGNORECASE so T01_example.MD is discovered on case-preserving filesystems
    pattern = re.compile(r"^(T(\d+)(R\d+|[A-Z])?)_.+\.md$", re.IGNORECASE)

    for entry in tasks_dir.iterdir():
        if entry.name.startswith("architect_eval_"):
            continue
        if entry.is_file() and entry.suffix.lower() == ".md":
            match = pattern.match(entry.name)
            if match:
                prefix_raw = match.group(1).upper()
                number = int(match.group(2))
                title = _extract_title(entry.resolve(), entry.stem)
                deps = _extract_dependencies(entry.resolve())
                tasks.append(
                    Task(
                        name=entry.stem,
                        prefix=prefix_raw,
                        number=number,
                        path=entry.resolve(),
                        status=TaskStatus.PENDING,
                        title=title,
                        depends_on=deps,
                    )
                )

    tasks.sort(key=task_sort_key)
    return tasks


# ---------------------------------------------------------------------------
# Prefix classification helpers
# ---------------------------------------------------------------------------

#: Compiled pattern for the full task prefix grammar.
_PREFIX_PATTERN = re.compile(r"^(T\d+)(R\d+|[A-Z])?$")


def is_retro_task(prefix: str) -> bool:
    """Return True when *prefix* is a retrospective fix task (e.g. ``T04R1``).

    Retrospective tasks are identified by the ``R<digits>`` suffix after the
    optional split letter.

    Args:
        prefix: A task prefix such as ``"T04R1"``, ``"T01"``, or ``"T01A"``.

    Returns:
        ``True`` for ``T04R1``, ``T04R2``, etc.; ``False`` for ``T01``, ``T01A``.

    Examples:
        >>> is_retro_task("T04R1")
        True
        >>> is_retro_task("T01A")
        False
        >>> is_retro_task("T01")
        False
    """
    m = _PREFIX_PATTERN.match(prefix)
    return bool(m and m.group(2) and m.group(2).startswith("R"))


def is_split_task(prefix: str) -> bool:
    """Return True when *prefix* is a split variant (e.g. ``T01A``, ``T01B``).

    Split tasks are created by the reassessment agent when a planned task is
    decomposed into sub-tasks.  They carry an uppercase letter suffix directly
    after the task number and before any retro suffix.

    Args:
        prefix: A task prefix such as ``"T01A"``, ``"T04R1"``, or ``"T01"``.

    Returns:
        ``True`` for ``T01A``, ``T01B``, etc.; ``False`` for ``T01``, ``T04R1``.

    Examples:
        >>> is_split_task("T01A")
        True
        >>> is_split_task("T04R1")
        False
        >>> is_split_task("T01")
        False
    """
    m = _PREFIX_PATTERN.match(prefix)
    return bool(m and m.group(2) and not m.group(2).startswith("R"))


def task_base_prefix(prefix: str) -> str:
    """Return the plain ``TXX`` base of any prefix variant.

    Strips split letters and retro suffixes so that ``T04R1``, ``T04A``,
    and ``T04`` all return ``"T04"``.

    Args:
        prefix: A full task prefix such as ``"T04R1"`` or ``"T01A"``.

    Returns:
        The plain base prefix, e.g. ``"T04"``.

    Examples:
        >>> task_base_prefix("T04R1")
        'T04'
        >>> task_base_prefix("T01A")
        'T01'
        >>> task_base_prefix("T01")
        'T01'
    """
    m = re.match(r"^(T\d+)", prefix)
    return m.group(1) if m else prefix


def duplicate_task_prefixes(tasks: Sequence[Task]) -> dict[str, list[str]]:
    """Return task prefixes that are used by more than one task file.

    Task prefixes are the runtime identity used by ``PROGRESS.md`` and the
    execution UI. Duplicate prefixes are ambiguous because two distinct files
    would share one progress row and one live status.

    Args:
        tasks: Discovered tasks to inspect.

    Returns:
        Mapping of duplicate prefix to task names using that prefix.
    """
    names_by_prefix: dict[str, list[str]] = {}
    for task in tasks:
        names_by_prefix.setdefault(task.prefix, []).append(task.name)
    return {prefix: sorted(names) for prefix, names in names_by_prefix.items() if len(names) > 1}


# ---------------------------------------------------------------------------
# Dependency graph validation
# ---------------------------------------------------------------------------


def detect_dependency_cycles(tasks: list[Task]) -> list[list[str]]:
    """Detect circular dependencies among tasks using DFS.

    Builds a directed graph from each task's ``depends_on`` field and
    searches for back-edges using depth-first search.  Each cycle is
    returned as a list of task prefixes forming the loop.

    Args:
        tasks: All discovered tasks.

    Returns:
        List of cycles, where each cycle is a list of task prefixes.
        An empty list means no cycles were found.

    Examples:
        >>> t1 = Task(name='T01_a', prefix='T01', number=1, path=Path('/x'),
        ...           depends_on=['T02'])
        >>> t2 = Task(name='T02_b', prefix='T02', number=2, path=Path('/y'),
        ...           depends_on=['T01'])
        >>> cycles = detect_dependency_cycles([t1, t2])
        >>> len(cycles)
        1
        >>> set(cycles[0]) == {'T01', 'T02'}
        True
    """
    # Build adjacency map: prefix -> list of prefixes it depends on
    graph: dict[str, list[str]] = {}
    all_prefixes: set[str] = set()
    for task in tasks:
        graph[task.prefix] = list(task.depends_on)
        all_prefixes.add(task.prefix)

    cycles: list[list[str]] = []
    visited: set[str] = set()
    # Track the current recursion stack to detect back-edges
    stack: set[str] = set()
    path: list[str] = []

    def dfs(node: str) -> None:
        visited.add(node)
        stack.add(node)
        path.append(node)

        for neighbor in graph.get(node, []):
            # Only consider neighbors that are actual tasks
            if neighbor not in all_prefixes:
                continue
            if neighbor in stack:
                # Found a cycle — extract it from the path
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:]
                cycles.append(list(cycle))
            elif neighbor not in visited:
                dfs(neighbor)

        path.pop()
        stack.discard(node)

    for prefix in sorted(all_prefixes):
        if prefix not in visited:
            dfs(prefix)

    return cycles


def detect_missing_dependencies(tasks: list[Task]) -> dict[str, list[str]]:
    """Find dependency references to non-existent task prefixes.

    Scans every task's ``depends_on`` field and checks whether each
    referenced prefix exists among the discovered tasks.

    Args:
        tasks: All discovered tasks.

    Returns:
        Mapping of task prefix to list of missing dependency prefixes.
        An empty dict means all dependencies reference existing tasks.

    Examples:
        >>> t1 = Task(name='T01_a', prefix='T01', number=1, path=Path('/x'),
        ...           depends_on=['T099'])
        >>> missing = detect_missing_dependencies([t1])
        >>> missing
        {'T01': ['T099']}
    """
    known_prefixes: set[str] = {task.prefix for task in tasks}
    missing: dict[str, list[str]] = {}

    for task in tasks:
        task_missing = [dep for dep in task.depends_on if dep not in known_prefixes]
        if task_missing:
            missing[task.prefix] = task_missing

    return missing
