"""Tests for the parallel task scheduler module.

Covers all public API surface of :class:`ParallelScheduler` and
:class:`SchedulerState` against the acceptance criteria in T01.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from the_architect.core.parallel_scheduler import (
    ParallelScheduler,
    SchedulerState,
    SchedulerTaskState,
)
from the_architect.core.tasks import Task, TaskPlan

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task(prefix: str, number: int, depends_on: list[str] | None = None) -> Task:
    """Create a minimal Task for testing."""
    return Task(
        name=f"{prefix}_test",
        prefix=prefix,
        number=number,
        path=Path(f"/tmp/{prefix}_test.md"),
        depends_on=depends_on or [],
    )


# ---------------------------------------------------------------------------
# SchedulerState tests
# ---------------------------------------------------------------------------


class TestSchedulerState:
    """Pure state machine transitions."""

    def test_initial_state_empty(self) -> None:
        state = SchedulerState()
        assert len(state.pending) == 0
        assert len(state.running) == 0
        assert len(state.completed) == 0
        assert len(state.failed) == 0

    def test_transition_to_running(self) -> None:
        state = SchedulerState(pending={"T01"})
        result = state.transition_to_running("T01")
        assert result is True
        assert "T01" not in state.pending
        assert "T01" in state.running

    def test_transition_to_running_not_pending(self) -> None:
        state = SchedulerState()
        result = state.transition_to_running("T01")
        assert result is False

    def test_transition_to_completed(self) -> None:
        state = SchedulerState(running={"T01"})
        result = state.transition_to_completed("T01")
        assert result is True
        assert "T01" not in state.running
        assert "T01" in state.completed

    def test_transition_to_completed_not_running(self) -> None:
        state = SchedulerState()
        result = state.transition_to_completed("T01")
        assert result is False

    def test_transition_to_failed(self) -> None:
        state = SchedulerState(running={"T02"})
        result = state.transition_to_failed("T02")
        assert result is True
        assert "T02" not in state.running
        assert "T02" in state.failed

    def test_transition_to_failed_not_running(self) -> None:
        state = SchedulerState()
        result = state.transition_to_failed("T02")
        assert result is False

    def test_terminal_prefixes(self) -> None:
        state = SchedulerState(
            completed={"T01", "T03"},
            failed={"T02"},
        )
        assert state.terminal_prefixes == {"T01", "T02", "T03"}

    def test_has_remaining_work_pending(self) -> None:
        state = SchedulerState(pending={"T01"})
        assert state.has_remaining_work is True

    def test_has_remaining_work_running(self) -> None:
        state = SchedulerState(running={"T01"})
        assert state.has_remaining_work is True

    def test_has_remaining_work_terminal(self) -> None:
        state = SchedulerState(completed={"T01"}, failed={"T02"})
        assert state.has_remaining_work is False

    def test_has_remaining_work_empty(self) -> None:
        state = SchedulerState()
        assert state.has_remaining_work is False

    def test_get_all_completed_sorted(self) -> None:
        state = SchedulerState(completed={"T03", "T01", "T02"})
        assert state.get_all_completed() == ["T01", "T02", "T03"]

    def test_get_all_completed_empty(self) -> None:
        state = SchedulerState()
        assert state.get_all_completed() == []


# ---------------------------------------------------------------------------
# ParallelScheduler tests
# ---------------------------------------------------------------------------


class TestParallelSchedulerInit:
    """Initialisation and configuration."""

    def test_basic_init(self) -> None:
        tasks = [_task("T01", 1), _task("T02", 2)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan)
        assert scheduler.total_tasks == 2
        assert scheduler.pending_count == 2
        assert scheduler.running_count == 0

    def test_max_concurrency_default_is_1(self) -> None:
        tasks = [_task("T01", 1), _task("T02", 2)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan)
        batch = scheduler.get_next_batch()
        assert len(batch) == 1

    def test_max_concurrency_2(self) -> None:
        tasks = [_task("T01", 1), _task("T02", 2)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan, max_concurrency=2)
        batch = scheduler.get_next_batch()
        assert len(batch) == 2

    def test_max_concurrency_zero_raises(self) -> None:
        tasks = [_task("T01", 1)]
        plan = TaskPlan(tasks=tasks)
        with pytest.raises(ValueError, match="max_concurrency"):
            ParallelScheduler(plan, max_concurrency=0)

    def test_max_concurrency_negative_raises(self) -> None:
        tasks = [_task("T01", 1)]
        plan = TaskPlan(tasks=tasks)
        with pytest.raises(ValueError, match="max_concurrency"):
            ParallelScheduler(plan, max_concurrency=-1)

    def test_empty_plan(self) -> None:
        plan = TaskPlan(tasks=[])
        scheduler = ParallelScheduler(plan)
        assert scheduler.total_tasks == 0
        assert scheduler.get_next_batch() == []


class TestGetReadyTasks:
    """Dependency-aware readiness detection."""

    def test_no_deps_all_ready(self) -> None:
        tasks = [_task("T01", 1), _task("T02", 2)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan, max_concurrency=2)
        ready = scheduler.get_ready_tasks()
        assert len(ready) == 2
        prefixes = {t.prefix for t in ready}
        assert prefixes == {"T01", "T02"}

    def test_depends_on_completed(self) -> None:
        t1 = _task("T01", 1)
        t2 = _task("T02", 2, depends_on=["T01"])
        plan = TaskPlan(tasks=[t1, t2])
        scheduler = ParallelScheduler(plan, max_concurrency=2)

        # Initially only T01 is ready
        ready = scheduler.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].prefix == "T01"

        # Complete T01 → T02 becomes ready
        scheduler.complete_task("T01")
        ready = scheduler.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].prefix == "T02"

    def test_depends_on_failed_is_terminal(self) -> None:
        """Failed dependencies are terminal — downstream tasks become ready
        (they will be skipped by the runner, not blocked forever)."""
        t1 = _task("T01", 1)
        t2 = _task("T02", 2, depends_on=["T01"])
        plan = TaskPlan(tasks=[t1, t2])
        scheduler = ParallelScheduler(plan, max_concurrency=2)

        scheduler.fail_task("T01")  # transition T01 to failed

        # T02 is ready because T01 is terminal (failed counts as terminal)
        ready = scheduler.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].prefix == "T02"

    def test_multiple_deps_all_satisfied(self) -> None:
        t1 = _task("T01", 1)
        t2 = _task("T02", 2)
        t3 = _task("T03", 3, depends_on=["T01", "T02"])
        plan = TaskPlan(tasks=[t1, t2, t3])
        scheduler = ParallelScheduler(plan, max_concurrency=3)

        ready = scheduler.get_ready_tasks()
        prefixes = {t.prefix for t in ready}
        assert prefixes == {"T01", "T02"}

        scheduler.complete_task("T01")
        scheduler.complete_task("T02")

        ready = scheduler.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].prefix == "T03"

    def test_multiple_deps_one_unsatisfied(self) -> None:
        t1 = _task("T01", 1)
        t2 = _task("T02", 2)
        t3 = _task("T03", 3, depends_on=["T01", "T02"])
        plan = TaskPlan(tasks=[t1, t2, t3])
        scheduler = ParallelScheduler(plan, max_concurrency=3)

        scheduler.complete_task("T01")
        # T02 is still pending → T03 not ready
        ready = scheduler.get_ready_tasks()
        prefixes = {t.prefix for t in ready}
        assert prefixes == {"T02"}

    def test_retro_task_prefix(self) -> None:
        """Full prefix grammar: T01R1 depends on T01."""
        t1 = _task("T01", 1)
        tr1 = _task("T01R1", 1, depends_on=["T01"])
        plan = TaskPlan(tasks=[t1, tr1])
        scheduler = ParallelScheduler(plan, max_concurrency=2)

        ready = scheduler.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].prefix == "T01"

        scheduler.complete_task("T01")
        ready = scheduler.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].prefix == "T01R1"

    def test_split_task_prefix(self) -> None:
        """Split tasks T01A and T01B with no deps on each other."""
        ta = _task("T01A", 1)
        tb = _task("T01B", 1)
        plan = TaskPlan(tasks=[ta, tb])
        scheduler = ParallelScheduler(plan, max_concurrency=2)

        ready = scheduler.get_ready_tasks()
        assert len(ready) == 2

    def test_running_tasks_not_ready(self) -> None:
        """Tasks that are currently running should not appear as ready."""
        tasks = [_task("T01", 1), _task("T02", 2)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan, max_concurrency=2)

        # Start T01
        scheduler.start_tasks([tasks[0]])

        ready = scheduler.get_ready_tasks()
        prefixes = {t.prefix for t in ready}
        assert "T01" not in prefixes
        assert "T02" in prefixes

    def test_completed_tasks_not_ready(self) -> None:
        """Tasks that completed should not appear as ready."""
        tasks = [_task("T01", 1), _task("T02", 2)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan, max_concurrency=2)

        scheduler.complete_task("T01")
        ready = scheduler.get_ready_tasks()
        prefixes = {t.prefix for t in ready}
        assert "T01" not in prefixes

    def test_ready_tasks_in_plan_order(self) -> None:
        """Ready tasks are returned sorted by task_sort_key."""
        t3 = _task("T03", 3)
        t1 = _task("T01", 1)
        t2 = _task("T02", 2)
        plan = TaskPlan(tasks=[t3, t1, t2])
        scheduler = ParallelScheduler(plan, max_concurrency=3)

        ready = scheduler.get_ready_tasks()
        assert [t.prefix for t in ready] == ["T01", "T02", "T03"]


class TestGetNextBatch:
    """Concurrency-limited batch selection."""

    def test_sequential_mode_returns_one(self) -> None:
        tasks = [_task("T01", 1), _task("T02", 2), _task("T03", 3)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan, max_concurrency=1)

        batch = scheduler.get_next_batch()
        assert len(batch) == 1
        assert batch[0].prefix == "T01"

    def test_parallel_mode_returns_multiple(self) -> None:
        tasks = [_task("T01", 1), _task("T02", 2), _task("T03", 3)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan, max_concurrency=3)

        batch = scheduler.get_next_batch()
        assert len(batch) == 3

    def test_batch_respects_running_count(self) -> None:
        """When tasks are already running, batch size is reduced."""
        tasks = [_task("T01", 1), _task("T02", 2), _task("T03", 3)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan, max_concurrency=2)

        # Start T01 and T02
        batch = scheduler.get_next_batch()
        scheduler.start_tasks(batch)
        assert scheduler.running_count == 2

        # No slots available
        batch = scheduler.get_next_batch()
        assert len(batch) == 0

    def test_batch_releases_on_complete(self) -> None:
        """Completing a running task frees a slot for the next ready task."""
        t1 = _task("T01", 1)
        t2 = _task("T02", 2)
        t3 = _task("T03", 3)
        plan = TaskPlan(tasks=[t1, t2, t3])
        scheduler = ParallelScheduler(plan, max_concurrency=2)

        batch = scheduler.get_next_batch()
        scheduler.start_tasks(batch)
        assert scheduler.running_count == 2

        # Complete T01 → one slot opens
        scheduler.complete_task("T01")
        batch = scheduler.get_next_batch()
        assert len(batch) == 1
        assert batch[0].prefix == "T03"

    def test_override_max_concurrency(self) -> None:
        """Per-call max_concurrency override."""
        tasks = [_task("T01", 1), _task("T02", 2), _task("T03", 3)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan, max_concurrency=3)

        batch = scheduler.get_next_batch(max_concurrency=1)
        assert len(batch) == 1

    def test_no_ready_tasks_returns_empty(self) -> None:
        """When all tasks are terminal, batch is empty."""
        tasks = [_task("T01", 1)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan)

        scheduler.start_tasks([tasks[0]])
        scheduler.complete_task("T01")

        batch = scheduler.get_next_batch()
        assert batch == []


class TestLifecycleMethods:
    """complete_task, fail_task, has_remaining_work, get_all_completed."""

    def test_complete_task(self) -> None:
        tasks = [_task("T01", 1)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan)

        scheduler.start_tasks([tasks[0]])
        result = scheduler.complete_task("T01")
        assert result is True
        assert scheduler.completed_count == 1
        assert scheduler.running_count == 0

    def test_complete_task_not_running(self) -> None:
        tasks = [_task("T01", 1)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan)

        # T01 is pending — complete_task accepts pending → completed
        result = scheduler.complete_task("T01")
        assert result is True
        assert scheduler.get_state("T01") == SchedulerTaskState.COMPLETED

    def test_complete_task_unknown_prefix(self) -> None:
        tasks = [_task("T01", 1)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan)

        result = scheduler.complete_task("T99")
        assert result is False

    def test_fail_task(self) -> None:
        tasks = [_task("T01", 1)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan)

        scheduler.start_tasks([tasks[0]])
        result = scheduler.fail_task("T01")
        assert result is True
        assert scheduler.failed_count == 1
        assert scheduler.running_count == 0

    def test_fail_task_not_running(self) -> None:
        tasks = [_task("T01", 1)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan)

        # T01 is pending — fail_task accepts pending → failed
        result = scheduler.fail_task("T01")
        assert result is True
        assert scheduler.get_state("T01") == SchedulerTaskState.FAILED

    def test_fail_task_unknown_prefix(self) -> None:
        tasks = [_task("T01", 1)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan)

        result = scheduler.fail_task("T99")
        assert result is False

    def test_has_remaining_work_true(self) -> None:
        tasks = [_task("T01", 1), _task("T02", 2)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan)
        assert scheduler.has_remaining_work() is True

    def test_has_remaining_work_false(self) -> None:
        tasks = [_task("T01", 1)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan)
        scheduler.start_tasks([tasks[0]])
        scheduler.complete_task("T01")
        assert scheduler.has_remaining_work() is False

    def test_get_all_completed(self) -> None:
        tasks = [_task("T01", 1), _task("T02", 2), _task("T03", 3)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan, max_concurrency=3)

        scheduler.start_tasks(tasks)
        scheduler.complete_task("T01")
        scheduler.complete_task("T03")
        scheduler.fail_task("T02")

        assert scheduler.get_all_completed() == ["T01", "T03"]

    def test_full_lifecycle_three_tasks(self) -> None:
        """End-to-end: three independent tasks through full lifecycle."""
        tasks = [_task("T01", 1), _task("T02", 2), _task("T03", 3)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan, max_concurrency=2)

        # Round 1: start T01 and T02
        batch = scheduler.get_next_batch()
        scheduler.start_tasks(batch)
        assert scheduler.running_count == 2
        assert scheduler.pending_count == 1

        # Complete T01, fail T02
        scheduler.complete_task("T01")
        scheduler.fail_task("T02")
        assert scheduler.completed_count == 1
        assert scheduler.failed_count == 1
        assert scheduler.running_count == 0

        # Round 2: T03 is now ready
        batch = scheduler.get_next_batch()
        assert len(batch) == 1
        assert batch[0].prefix == "T03"
        scheduler.start_tasks(batch)

        # Complete T03
        scheduler.complete_task("T03")
        assert scheduler.has_remaining_work() is False
        assert scheduler.get_all_completed() == ["T01", "T03"]


class TestInspectionHelpers:
    """Property accessors and get_state."""

    def test_counts(self) -> None:
        tasks = [_task("T01", 1), _task("T02", 2), _task("T03", 3)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan, max_concurrency=3)

        assert scheduler.total_tasks == 3
        assert scheduler.pending_count == 3
        assert scheduler.running_count == 0
        assert scheduler.completed_count == 0
        assert scheduler.failed_count == 0

    def test_get_state_pending(self) -> None:
        tasks = [_task("T01", 1)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan)
        assert scheduler.get_state("T01") == SchedulerTaskState.PENDING

    def test_get_state_running(self) -> None:
        tasks = [_task("T01", 1)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan)
        scheduler.start_tasks([tasks[0]])
        assert scheduler.get_state("T01") == SchedulerTaskState.RUNNING

    def test_get_state_completed(self) -> None:
        tasks = [_task("T01", 1)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan)
        scheduler.start_tasks([tasks[0]])
        scheduler.complete_task("T01")
        assert scheduler.get_state("T01") == SchedulerTaskState.COMPLETED

    def test_get_state_failed(self) -> None:
        tasks = [_task("T01", 1)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan)
        scheduler.start_tasks([tasks[0]])
        scheduler.fail_task("T01")
        assert scheduler.get_state("T01") == SchedulerTaskState.FAILED

    def test_get_state_unknown(self) -> None:
        tasks = [_task("T01", 1)]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan)
        assert scheduler.get_state("T99") is None


class TestDependencyChain:
    """Longer dependency chains and diamond patterns."""

    def test_linear_chain(self) -> None:
        """T01 → T02 → T03 → T04"""
        tasks = [
            _task("T01", 1),
            _task("T02", 2, depends_on=["T01"]),
            _task("T03", 3, depends_on=["T02"]),
            _task("T04", 4, depends_on=["T03"]),
        ]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan, max_concurrency=4)

        # Only T01 ready
        ready = scheduler.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].prefix == "T01"

        scheduler.complete_task("T01")
        ready = scheduler.get_ready_tasks()
        assert ready[0].prefix == "T02"

        scheduler.complete_task("T02")
        ready = scheduler.get_ready_tasks()
        assert ready[0].prefix == "T03"

        scheduler.complete_task("T03")
        ready = scheduler.get_ready_tasks()
        assert ready[0].prefix == "T04"

    def test_diamond_pattern(self) -> None:
        """T01 and T02 independent, T03 depends on both."""
        tasks = [
            _task("T01", 1),
            _task("T02", 2),
            _task("T03", 3, depends_on=["T01", "T02"]),
        ]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan, max_concurrency=3)

        # T01 and T02 ready in parallel
        ready = scheduler.get_ready_tasks()
        prefixes = {t.prefix for t in ready}
        assert prefixes == {"T01", "T02"}

        scheduler.complete_task("T01")
        # T03 not ready yet — T02 still pending
        ready = scheduler.get_ready_tasks()
        assert ready[0].prefix == "T02"

        scheduler.complete_task("T02")
        ready = scheduler.get_ready_tasks()
        assert ready[0].prefix == "T03"

    def test_mixed_parallel_and_sequential(self) -> None:
        """T01, T02, T03 independent; T04 depends on all three."""
        tasks = [
            _task("T01", 1),
            _task("T02", 2),
            _task("T03", 3),
            _task("T04", 4, depends_on=["T01", "T02", "T03"]),
        ]
        plan = TaskPlan(tasks=tasks)
        scheduler = ParallelScheduler(plan, max_concurrency=3)

        batch = scheduler.get_next_batch()
        prefixes = {t.prefix for t in batch}
        assert prefixes == {"T01", "T02", "T03"}

        scheduler.start_tasks(batch)
        # No more slots
        assert scheduler.get_next_batch() == []

        # Complete all three
        scheduler.complete_task("T01")
        scheduler.complete_task("T02")
        scheduler.complete_task("T03")

        # T04 ready
        batch = scheduler.get_next_batch()
        assert len(batch) == 1
        assert batch[0].prefix == "T04"


class TestRegisterTasks:
    """Tests for the register_tasks() method that handles mid-run task additions."""

    def test_register_tasks_adds_to_pending(self) -> None:
        """New tasks registered via register_tasks appear in pending state."""
        t1 = _task("T01", 1)
        plan = TaskPlan(tasks=[t1])
        scheduler = ParallelScheduler(plan)

        # T01 is pending, T02A does not exist yet
        assert scheduler.get_state("T01") == SchedulerTaskState.PENDING
        assert scheduler.get_state("T02A") is None

        # Register a new task added by reassessment
        t2a = _task("T02A", 2)
        scheduler.register_tasks([t2a])

        assert scheduler.get_state("T02A") == SchedulerTaskState.PENDING
        assert scheduler.has_remaining_work() is True

    def test_register_tasks_adds_deps(self) -> None:
        """Registered tasks carry their dependency declarations."""
        t1 = _task("T01", 1)
        plan = TaskPlan(tasks=[t1])
        scheduler = ParallelScheduler(plan)

        # Complete T01 first
        scheduler.complete_task("T01")

        # Register T02A that depends on T01
        t2a = _task("T02A", 2, depends_on=["T01"])
        scheduler.register_tasks([t2a])

        # T02A is in pending state with deps tracked
        assert scheduler.get_state("T02A") == SchedulerTaskState.PENDING
        # T02A is ready because T01 is terminal (completed)
        # Note: register_tasks adds to _state.pending and _deps,
        # but get_ready_tasks iterates _plan.tasks. The runner syncs
        # the plan via _sync_plan_from_disk. Here we verify the
        # scheduler's internal dep tracking is correct.
        assert "T02A" in scheduler._state.pending
        assert scheduler._deps.get("T02A") == ["T01"]

    def test_register_tasks_blocks_on_unmet_deps(self) -> None:
        """Registered tasks with unmet dependencies are not ready."""
        t1 = _task("T01", 1)
        plan = TaskPlan(tasks=[t1])
        scheduler = ParallelScheduler(plan)

        # Register T02A that depends on T01 (still pending)
        t2a = _task("T02A", 2, depends_on=["T01"])
        scheduler.register_tasks([t2a])

        # T02A should NOT be ready — T01 is still pending
        ready = scheduler.get_ready_tasks()
        prefixes = {t.prefix for t in ready}
        assert "T02A" not in prefixes
        assert "T01" in prefixes

    def test_register_tasks_idempotent(self) -> None:
        """Registering the same task twice does not duplicate it."""
        t1 = _task("T01", 1)
        plan = TaskPlan(tasks=[t1])
        scheduler = ParallelScheduler(plan)

        t2a = _task("T02A", 2)
        scheduler.register_tasks([t2a])
        scheduler.register_tasks([t2a])

        assert scheduler.pending_count == 2  # T01 + T02A
        assert "T02A" in scheduler._state.pending

    def test_register_tasks_multiple_at_once(self) -> None:
        """Registering multiple tasks at once works."""
        t1 = _task("T01", 1)
        plan = TaskPlan(tasks=[t1])
        scheduler = ParallelScheduler(plan)

        scheduler.register_tasks(
            [
                _task("T02A", 2),
                _task("T02B", 2),
                _task("T03R1", 3),
            ]
        )

        assert scheduler.pending_count == 4
        ready = scheduler.get_ready_tasks()
        assert len(ready) == 1  # Only T01 (max_concurrency=1)
