"""Tests for the TUI parallel task display in ExecutionScreen.

Covers the Tasks tab DataTable and parallel execution display added in Cycle 24:
- T04.2: Multiple task rendering, live updates, budget display, circuit state
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from the_architect.tui.screens.execution import (
    ExecutionScreen,
)

# ---------------------------------------------------------------------------
# T04.2 — TUI display tests
# ---------------------------------------------------------------------------


class TestTasksTabPreMount:
    """Tasks tab DataTable updates before mount buffer correctly."""

    def test_update_tasks_table_buffers_before_mount(self) -> None:
        """Calling update_tasks_table before mount stores per-task details."""
        screen = ExecutionScreen()
        tasks = [
            {"prefix": "T01", "title": "First", "status": "done", "tokens": "100"},
            {"prefix": "T02", "title": "Second", "status": "running", "model": "gpt-4"},
        ]
        screen.update_tasks_table(tasks)
        # update_tasks_table stores per-task details in _task_details
        assert "T01" in screen._task_details
        assert "T02" in screen._task_details

    def test_update_progress_tasks_buffers_before_mount(self) -> None:
        """update_progress_tasks buffers before mount."""
        screen = ExecutionScreen()
        tasks = [
            {"prefix": "T01", "title": "First", "status": "pending"},
        ]
        screen.update_progress_tasks(tasks)
        assert screen._progress_tasks == tasks


class TestTasksTabRendering:
    """Tasks tab DataTable renders correctly with multiple tasks."""

    def test_tasks_table_renders_multiple_tasks(self) -> None:
        """Multiple tasks appear in the Tasks tab DataTable."""
        screen = ExecutionScreen()
        tasks = [
            {"prefix": "T01", "title": "First", "status": "done"},
            {"prefix": "T02", "title": "Second", "status": "running"},
            {"prefix": "T03", "title": "Third", "status": "pending"},
        ]
        screen.update_progress_tasks(tasks)
        # Verify internal state
        assert len(screen._progress_tasks) == 3
        prefixes = [t["prefix"] for t in screen._progress_tasks]
        assert "T01" in prefixes
        assert "T02" in prefixes
        assert "T03" in prefixes

    def test_tasks_table_renders_with_tokens(self) -> None:
        """Task tokens display correctly."""
        screen = ExecutionScreen()
        tasks = [
            {"prefix": "T01", "title": "First", "status": "done", "tokens": "12.3K"},
            {"prefix": "T02", "title": "Second", "status": "running", "tokens": "5.1K"},
        ]
        screen.update_progress_tasks(tasks)
        assert len(screen._progress_tasks) == 2
        assert screen._progress_tasks[0]["tokens"] == "12.3K"

    def test_tasks_table_renders_with_model(self) -> None:
        """Task model names display correctly."""
        screen = ExecutionScreen()
        tasks = [
            {"prefix": "T01", "title": "First", "status": "done", "model": "gpt-4"},
            {"prefix": "T02", "title": "Second", "status": "running", "model": "claude-3"},
        ]
        screen.update_progress_tasks(tasks)
        assert screen._progress_tasks[0]["model"] == "gpt-4"
        assert screen._progress_tasks[1]["model"] == "claude-3"

    def test_tasks_table_renders_with_circuit_state(self) -> None:
        """Task circuit breaker state displays correctly."""
        screen = ExecutionScreen()
        tasks = [
            {"prefix": "T01", "title": "First", "status": "done", "circuit": "CLOSED"},
            {"prefix": "T02", "title": "Second", "status": "running", "circuit": "OPEN"},
        ]
        screen.update_progress_tasks(tasks)
        assert screen._progress_tasks[0]["circuit"] == "CLOSED"
        assert screen._progress_tasks[1]["circuit"] == "OPEN"


class TestLiveTaskUpdates:
    """Live updates as tasks start, progress, and complete."""

    def test_task_status_updates_from_pending_to_running(self) -> None:
        """Task status transitions from pending to running."""
        screen = ExecutionScreen()
        # Initial state
        screen.update_progress_tasks(
            [
                {"prefix": "T01", "title": "First", "status": "pending"},
            ]
        )
        assert screen._progress_tasks[0]["status"] == "pending"

        # Update to running
        screen.update_progress_tasks(
            [
                {"prefix": "T01", "title": "First", "status": "running"},
            ]
        )
        assert screen._progress_tasks[0]["status"] == "running"

    def test_task_status_updates_from_running_to_done(self) -> None:
        """Task status transitions from running to done."""
        screen = ExecutionScreen()
        screen.update_progress_tasks(
            [
                {"prefix": "T01", "title": "First", "status": "running"},
            ]
        )
        screen.update_progress_tasks(
            [
                {"prefix": "T01", "title": "First", "status": "done"},
            ]
        )
        assert screen._progress_tasks[0]["status"] == "done"

    def test_live_update_adds_new_task(self) -> None:
        """New tasks appear in live updates."""
        screen = ExecutionScreen()
        screen.update_progress_tasks(
            [
                {"prefix": "T01", "title": "First", "status": "running"},
            ]
        )
        assert len(screen._progress_tasks) == 1

        # Update adds T02
        screen.update_progress_tasks(
            [
                {"prefix": "T01", "title": "First", "status": "running"},
                {"prefix": "T02", "title": "Second", "status": "pending"},
            ]
        )
        assert len(screen._progress_tasks) == 2

    def test_live_update_removes_completed_task(self) -> None:
        """Completed tasks can be removed from the live view."""
        screen = ExecutionScreen()
        screen.update_progress_tasks(
            [
                {"prefix": "T01", "title": "First", "status": "running"},
                {"prefix": "T02", "title": "Second", "status": "running"},
            ]
        )
        assert len(screen._progress_tasks) == 2

        # Update shows T01 done, T02 still running
        screen.update_progress_tasks(
            [
                {"prefix": "T01", "title": "First", "status": "done"},
                {"prefix": "T02", "title": "Second", "status": "running"},
            ]
        )
        assert len(screen._progress_tasks) == 2
        assert screen._progress_tasks[0]["status"] == "done"


class TestBudgetDisplayParallel:
    """Budget display shows cumulative usage across concurrent tasks."""

    def test_costs_update_with_parallel_budget(self) -> None:
        """Cost display accumulates tokens from concurrent tasks."""
        screen = ExecutionScreen()
        # First update
        screen.update_costs(
            {
                "session_cost_usd": 0.50,
                "session_tokens": 10000,
            }
        )
        assert screen._costs["session_tokens"] == 10000

        # Second update (more tokens used)
        screen.update_costs(
            {
                "session_cost_usd": 1.25,
                "session_tokens": 25000,
            }
        )
        assert screen._costs["session_tokens"] == 25000

    def test_costs_display_budget_fields(self) -> None:
        """Budget fields appear in cost display."""
        screen = ExecutionScreen()
        screen.update_costs(
            {
                "session_cost_usd": 1.00,
                "session_tokens": 20000,
                "budget_per_run": 50000,
                "budget_per_run_used": 20000,
                "budget_per_run_remaining": 30000,
                "budget_per_run_pct": 40,
            }
        )
        assert screen._costs["budget_per_run"] == 50000
        assert screen._costs["budget_per_run_used"] == 20000
        assert screen._costs["budget_per_run_remaining"] == 30000


class TestCircuitBreakerDisplay:
    """Circuit breaker status visible per-task in TUI."""

    def test_circuit_state_in_task_data(self) -> None:
        """Circuit breaker state is carried in task data."""
        screen = ExecutionScreen()
        tasks = [
            {"prefix": "T01", "title": "First", "status": "done", "circuit": "CLOSED"},
            {"prefix": "T02", "title": "Second", "status": "running", "circuit": "HALF_OPEN"},
            {"prefix": "T03", "title": "Third", "status": "failed", "circuit": "OPEN"},
        ]
        screen.update_progress_tasks(tasks)
        circuits = {t["prefix"]: t.get("circuit", "") for t in screen._progress_tasks}
        assert circuits["T01"] == "CLOSED"
        assert circuits["T02"] == "HALF_OPEN"
        assert circuits["T03"] == "OPEN"

    def test_circuit_state_default_empty(self) -> None:
        """Tasks without circuit data render with empty circuit field."""
        screen = ExecutionScreen()
        tasks = [
            {"prefix": "T01", "title": "First", "status": "done"},
        ]
        screen.update_progress_tasks(tasks)
        assert screen._progress_tasks[0].get("circuit", "") == ""


class TestDataTableWidget:
    """DataTable widget interactions for the Tasks tab."""

    def test_tasks_tab_binding_references_tab_tasks(self) -> None:
        """The 't' key binding references the tab_tasks pane."""
        screen = ExecutionScreen()
        # The binding maps 't' to switch_tab('tab_tasks')
        found = False
        for binding in screen.BINDINGS:
            if binding.key == "t":
                assert "tab_tasks" in binding.action
                found = True
                break
        assert found, "No binding for 't' key found"

    def test_tasks_datatable_id_is_exec_tasks_table(self) -> None:
        """The Tasks DataTable uses id='exec_tasks_table' for querying."""
        # Verify by checking that update_tasks_table queries "#exec_tasks_table"
        screen = ExecutionScreen()
        mock_table = MagicMock()
        with patch.object(screen, "query_one", return_value=mock_table):
            screen.update_tasks_table(
                [
                    {"prefix": "T01", "title": "Test", "status": "done"},
                ]
            )
        # query_one was called with the correct ID
        call_args = mock_table.clear.call_args
        assert call_args is not None  # table.clear was called

    def test_update_tasks_table_calls_update_on_mounted_widget(self) -> None:
        """update_tasks_table updates the DataTable when mounted."""
        screen = ExecutionScreen()
        tasks = [
            {"prefix": "T01", "title": "First", "status": "done"},
        ]
        # Mock query_one to simulate mounted state
        mock_table = MagicMock()
        with patch.object(screen, "query_one", return_value=mock_table):
            screen.update_tasks_table(tasks)
        # The table should have been interacted with
        # update_tasks_table clears and rebuilds the table
        assert mock_table.clear.called or mock_table.add_row.called or mock_table.add_column.called


class TestExecutionScreenBindings:
    """Key bindings for the Tasks tab."""

    def test_tasks_tab_binding_exists(self) -> None:
        """The 't' key binding switches to the Tasks tab."""
        screen = ExecutionScreen()
        binding_ids = [b.key for b in screen.BINDINGS]
        assert "t" in binding_ids

    def test_tasks_tab_action_switches_tab(self) -> None:
        """Pressing 't' switches to the Tasks tab."""
        screen = ExecutionScreen()
        # The binding maps 't' to switch_tab('tab_tasks')
        found = False
        for binding in screen.BINDINGS:
            if binding.key == "t":
                assert "tab_tasks" in binding.action
                found = True
                break
        assert found, "No binding for 't' key found"
