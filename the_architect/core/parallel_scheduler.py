"""Parallel task scheduler for The Architect.

Determines which tasks can run concurrently based on their dependency graph,
with configurable concurrency limits.  This is a pure logic module — it does
not import the runner, TUI, or CLI layers, so it can be unit-tested in
isolation.

The scheduler accepts a :class:`TaskPlan` and tracks each task through four
states: **pending** → **running** → **completed** or **failed**.  Tasks
become *ready* when all of their declared dependencies have reached a
terminal state (completed or failed).  The :meth:`get_next_batch` method
returns up to ``max_concurrency`` ready tasks, enabling parallel execution
of independent work.

When ``max_concurrency == 1`` (the default), the scheduler behaves
identically to sequential execution — it returns at most one task at a
time.  This ensures backward compatibility without any runner changes.

Design decisions
----------------
- **Per-task circuit breaker scope** — each parallel task gets its own
  circuit breaker instance; the scheduler does not manage circuit state.
- **Token budget atomics** — the scheduler does not track tokens; the
  runner's :class:`RunTokenBudget` is shared across concurrent tasks and
  uses ``asyncio.Lock`` for safe updates.
- **Dependency satisfaction** — a dependency is satisfied when the
  depended-on prefix is in either *completed* or *failed* state.  This
  matches the existing runner behaviour where downstream tasks are
  skipped (not blocked forever) when an upstream task fails.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import BaseModel, Field

from the_architect.core.tasks import task_sort_key

if TYPE_CHECKING:
    from the_architect.core.tasks import Task, TaskPlan


# ---------------------------------------------------------------------------
# Task states within the scheduler
# ---------------------------------------------------------------------------


class SchedulerTaskState(StrEnum):
    """Lifecycle state of a task inside the scheduler.

    These states are internal to the scheduler and do not map 1:1 to
    :class:`TaskStatus` (which lives in PROGRESS.md).  The scheduler
    translates between the two at the runner integration layer.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Scheduler state model
# ---------------------------------------------------------------------------


class SchedulerState(BaseModel):
    """Tracks the lifecycle state of every task in a plan.

    The scheduler owns this object and mutates it as tasks transition
    between states.  It is NOT persisted to disk — the runner is
    responsible for PROGRESS.md reconciliation.

    Attributes:
        pending: Tasks not yet started or running.
        running: Tasks currently being executed by the provider CLI.
        completed: Tasks that finished successfully.
        failed: Tasks that exhausted retries or were skipped.
    """

    pending: set[str] = Field(default_factory=set, description="Task prefixes still waiting")
    running: set[str] = Field(default_factory=set, description="Task prefixes currently executing")
    completed: set[str] = Field(default_factory=set, description="Task prefixes that succeeded")
    failed: set[str] = Field(
        default_factory=set, description="Task prefixes that failed or were skipped"
    )

    def transition_to_running(self, prefix: str) -> bool:
        """Move *prefix* from pending to running.

        Args:
            prefix: The task prefix to start (e.g. ``"T03"``).

        Returns:
            ``True`` if the transition succeeded, ``False`` if the prefix
            was not in the pending set.
        """
        if prefix not in self.pending:
            return False
        self.pending.discard(prefix)
        self.running.add(prefix)
        logger.debug(f"Scheduler state: {prefix} → running")
        return True

    def transition_to_completed(self, prefix: str) -> bool:
        """Move *prefix* from running to completed.

        Args:
            prefix: The task prefix that finished successfully.

        Returns:
            ``True`` if the transition succeeded, ``False`` if the prefix
            was not in the running set.
        """
        if prefix not in self.running:
            return False
        self.running.discard(prefix)
        self.completed.add(prefix)
        logger.debug(f"Scheduler state: {prefix} → completed")
        return True

    def transition_to_failed(self, prefix: str) -> bool:
        """Move *prefix* from running to failed.

        Args:
            prefix: The task prefix that failed or was skipped.

        Returns:
            ``True`` if the transition succeeded, ``False`` if the prefix
            was not in the running set.
        """
        if prefix not in self.running:
            return False
        self.running.discard(prefix)
        self.failed.add(prefix)
        logger.debug(f"Scheduler state: {prefix} → failed")
        return True

    @property
    def terminal_prefixes(self) -> set[str]:
        """Return all prefixes that have reached a terminal state.

        Both completed and failed tasks are terminal — their downstream
        dependents can proceed (even if the dependency failed, the
        dependent will be skipped rather than blocked).
        """
        return self.completed | self.failed

    @property
    def has_remaining_work(self) -> bool:
        """Return ``True`` if there are pending or running tasks."""
        return bool(self.pending or self.running)

    def get_all_completed(self) -> list[str]:
        """Return a sorted list of completed task prefixes."""
        return sorted(self.completed)


# ---------------------------------------------------------------------------
# Parallel scheduler
# ---------------------------------------------------------------------------


class ParallelScheduler:
    """Dependency-aware scheduler for parallel task execution.

    Accepts a :class:`TaskPlan` and determines which tasks can run
    concurrently based on their ``depends_on`` fields.  Supports a
    configurable maximum concurrency limit (``max_concurrency``).

    When ``max_concurrency == 1`` the scheduler returns at most one task
    per call, behaving identically to sequential execution.

    Args:
        plan: The task plan containing all tasks and their dependencies.
        max_concurrency: Maximum number of tasks to run simultaneously.
            Defaults to ``1`` (sequential mode) for backward compatibility.

    Raises:
        ValueError: If ``max_concurrency`` is less than 1.

    Examples:
        >>> from pathlib import Path
        >>> from the_architect.core.tasks import Task, TaskPlan
        >>> t1 = Task(name='T01_a', prefix='T01', number=1, path=Path('/x'))
        >>> t2 = Task(name='T02_b', prefix='T02', number=2, path=Path('/y'))
        >>> plan = TaskPlan(tasks=[t1, t2])
        >>> scheduler = ParallelScheduler(plan, max_concurrency=2)
        >>> batch = scheduler.get_next_batch()
        >>> len(batch)
        2
    """

    def __init__(
        self,
        plan: TaskPlan,
        max_concurrency: int = 1,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError(f"max_concurrency must be >= 1, got {max_concurrency}")

        self._plan = plan
        self._max_concurrency = max_concurrency
        self._state = SchedulerState()
        self._deps: dict[str, list[str]] = {}

        # Build the dependency map and initialise pending set.
        for task in plan.tasks:
            self._state.pending.add(task.prefix)
            self._deps[task.prefix] = list(task.depends_on)

        logger.info(
            f"ParallelScheduler initialised: {len(plan.tasks)} tasks, "
            f"max_concurrency={max_concurrency}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_ready_tasks(self) -> list[Task]:
        """Return all tasks whose dependencies are satisfied.

        A task is ready when:
        - It is currently in the **pending** state.
        - All prefixes in its ``depends_on`` list are in the scheduler's
          terminal set (completed or failed).

        Tasks are returned in plan order (sorted by
        :func:`the_architect.core.tasks.task_sort_key`).

        Returns:
            List of :class:`Task` objects that can be started immediately.
        """
        ready: list[Task] = []
        terminal = self._state.terminal_prefixes

        for task in self._plan.tasks:
            if task.prefix not in self._state.pending:
                continue
            deps = self._deps.get(task.prefix, [])
            if all(dep in terminal for dep in deps):
                ready.append(task)

        ready.sort(key=task_sort_key)
        return ready

    def get_next_batch(self, max_concurrency: int | None = None) -> list[Task]:
        """Return up to *max_concurrency* ready tasks for parallel execution.

        This is the primary entry point for the runner.  It combines
        :meth:`get_ready_tasks` with concurrency limits:

        1. Find all ready tasks (dependencies satisfied, currently pending).
        2. Subtract the number of tasks already running.
        3. Return at most the remaining slot count.

        When ``max_concurrency == 1`` (default), at most one task is
        returned — sequential mode.

        Args:
            max_concurrency: Override the scheduler's configured maximum.
                If ``None``, uses the value from :meth:`__init__`.

        Returns:
            List of :class:`Task` objects to start immediately.  An empty
            list means no tasks are ready (all dependencies blocked or
            all tasks are already running / terminal).

        Note:
            Calling this method does NOT transition tasks to the running
            state — the caller must call :meth:`start_tasks` before
            launching the provider CLIs.
        """
        effective_max = max_concurrency if max_concurrency is not None else self._max_concurrency

        ready = self.get_ready_tasks()
        slots_available = effective_max - len(self._state.running)

        if slots_available <= 0:
            logger.debug(
                f"No scheduler slots available: {len(self._state.running)}/{effective_max} running"
            )
            return []

        batch = ready[:slots_available]
        logger.debug(
            f"Scheduler batch: {len(batch)} tasks (slots: {slots_available}/{effective_max})"
        )
        return batch

    def start_tasks(self, tasks: list[Task]) -> list[Task]:
        """Transition the given tasks from pending to running.

        Call this after :meth:`get_next_batch` and before launching the
        provider CLIs.  Tasks that are already running or not pending
        are silently skipped.

        Args:
            tasks: The tasks returned by :meth:`get_next_batch`.

        Returns:
            The subset of tasks that were successfully transitioned to
            running.  Tasks that were already in a different state are
            excluded.
        """
        started: list[Task] = []
        for task in tasks:
            if self._state.transition_to_running(task.prefix):
                started.append(task)
        return started

    def complete_task(self, prefix: str) -> bool:
        """Mark *prefix* as completed (success).

        Transitions the task to completed from either running or pending.
        Accepting pending allows the runner to skip a task without first
        starting it (e.g. when a dependency was already terminal).
        Downstream dependents will become ready on the next
        :meth:`get_next_batch` call.

        Args:
            prefix: The task prefix that finished successfully.

        Returns:
            ``True`` if the transition succeeded.
        """
        # Try running → completed first, then pending → completed.
        if self._state.transition_to_completed(prefix):
            return True
        if prefix in self._state.pending:
            self._state.pending.discard(prefix)
            self._state.completed.add(prefix)
            logger.debug(f"Scheduler state: {prefix} pending → completed")
            return True
        return False

    def fail_task(self, prefix: str) -> bool:
        """Mark *prefix* as failed.

        Transitions the task to failed from either running or pending.
        Accepting pending allows the runner to skip a task without first
        starting it (e.g. when a dependency was already terminal).
        Downstream dependents will see this dependency as satisfied
        (terminal) on the next :meth:`get_next_batch` call — matching
        the existing runner behaviour where downstream tasks are skipped
        rather than blocked forever.

        Args:
            prefix: The task prefix that failed or was skipped.

        Returns:
            ``True`` if the transition succeeded.
        """
        # Try running → failed first, then pending → failed.
        if self._state.transition_to_failed(prefix):
            return True
        if prefix in self._state.pending:
            self._state.pending.discard(prefix)
            self._state.failed.add(prefix)
            logger.debug(f"Scheduler state: {prefix} pending → failed")
            return True
        return False

    def has_remaining_work(self) -> bool:
        """Return ``True`` if there are pending or running tasks.

        Used by the runner to decide whether to continue the execution
        loop or exit.

        Returns:
            ``True`` if work remains, ``False`` if all tasks are terminal.
        """
        return self._state.has_remaining_work

    def get_all_completed(self) -> list[str]:
        """Return a sorted list of all completed task prefixes.

        Returns:
            List of prefix strings for tasks that finished successfully.
        """
        return self._state.get_all_completed()

    # ------------------------------------------------------------------
    # Inspection helpers
    # ------------------------------------------------------------------

    @property
    def pending_count(self) -> int:
        """Number of tasks still waiting to start."""
        return len(self._state.pending)

    @property
    def running_count(self) -> int:
        """Number of tasks currently executing."""
        return len(self._state.running)

    @property
    def completed_count(self) -> int:
        """Number of tasks that finished successfully."""
        return len(self._state.completed)

    @property
    def failed_count(self) -> int:
        """Number of tasks that failed or were skipped."""
        return len(self._state.failed)

    @property
    def total_tasks(self) -> int:
        """Total number of tasks in the plan."""
        return len(self._plan.tasks)

    def get_state(self, prefix: str) -> SchedulerTaskState | None:
        """Return the current scheduler state for *prefix*.

        Args:
            prefix: The task prefix to query.

        Returns:
            The :class:`SchedulerTaskState` or ``None`` if the prefix
            is not in the plan.
        """
        if prefix in self._state.pending:
            return SchedulerTaskState.PENDING
        if prefix in self._state.running:
            return SchedulerTaskState.RUNNING
        if prefix in self._state.completed:
            return SchedulerTaskState.COMPLETED
        if prefix in self._state.failed:
            return SchedulerTaskState.FAILED
        return None
