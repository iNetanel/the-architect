"""Tests for the Textual HistoryApp screen — task cost detail view."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.widgets import DataTable, Static

from the_architect.core.token_ledger import (
    LedgerRunRecord,
    LedgerTaskRecord,
)
from the_architect.tui.screens.history_screen import HistoryApp, run_history_screen

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_ledger(project: Path, records: list[LedgerRunRecord]) -> None:
    """Write a token ledger JSON file for testing.

    Args:
        project: Project root path.
        records: List of run records to write.
    """
    ledger_dir = project / ".architect"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    data = [r.model_dump() for r in records]
    (ledger_dir / "token_ledger.json").write_text(
        json.dumps(data),
        encoding="utf-8",
    )


def _make_run(
    goal: str = "Test goal",
    task_count: int = 3,
    outcome: str = "success",
    total_tokens: int = 15000,
    total_cost: float = 0.12,
    duration: float = 120.0,
    task_breakdown: list[LedgerTaskRecord] | None = None,
) -> LedgerRunRecord:
    """Create a LedgerRunRecord for testing.

    Args:
        goal: Goal summary text.
        task_count: Number of tasks.
        outcome: Run outcome.
        total_tokens: Total token count.
        total_cost: Total cost estimate.
        duration: Duration in seconds.
        task_breakdown: Optional per-task breakdown records.

    Returns:
        A configured LedgerRunRecord instance.
    """
    return LedgerRunRecord(
        run_id="abc123",
        timestamp="2026-05-18T10:00:00+00:00",
        goal_summary=goal,
        total_tokens=total_tokens,
        total_cost_estimate=total_cost,
        task_count=task_count,
        outcome=outcome,
        duration_seconds=duration,
        task_breakdown=task_breakdown or [],
    )


def _make_task(
    task_id: str = "T01",
    title: str = "Test task",
    status: str = "done",
    input_tokens: int = 5000,
    output_tokens: int = 3000,
    model: str = "anthropic/claude-sonnet-4",
    cost: float = 0.04,
    duration: float = 40.0,
) -> LedgerTaskRecord:
    """Create a LedgerTaskRecord for testing.

    Args:
        task_id: Task prefix identifier.
        title: Human-readable task title.
        status: Task outcome.
        input_tokens: Input token count.
        output_tokens: Output token count.
        model: Model identifier.
        cost: Cost estimate.
        duration: Duration in seconds.

    Returns:
        A configured LedgerTaskRecord instance.
    """
    return LedgerTaskRecord(
        task_id=task_id,
        title=title,
        status=status,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
        cost_estimate=cost,
        duration_seconds=duration,
    )


# ---------------------------------------------------------------------------
# Tests — run-level view (default)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_screen_no_ledger(tmp_path: Path) -> None:
    """Screen shows 'No run history found' when no ledger exists."""
    app = HistoryApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count == 1
        cell = str(table.get_cell_at((0, 0)))
        assert cell == "—"


@pytest.mark.asyncio
async def test_history_screen_run_rows(tmp_path: Path) -> None:
    """Screen shows run-level data for each ledger record."""
    records = [
        _make_run(goal="First run", task_count=2, outcome="success"),
        _make_run(goal="Second run", task_count=5, outcome="failure"),
    ]
    _write_ledger(tmp_path, records)

    app = HistoryApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count == 2
        # First row: success
        assert str(table.get_cell_at((0, 2))) == "2"  # task count
        assert "success" in str(table.get_cell_at((0, 6)))
        # Second row: failure
        assert str(table.get_cell_at((1, 2))) == "5"
        assert "failure" in str(table.get_cell_at((1, 6)))


@pytest.mark.asyncio
async def test_history_screen_goal_truncation(tmp_path: Path) -> None:
    """Goal text longer than 40 characters is truncated with ellipsis."""
    long_goal = "A" * 60
    records = [_make_run(goal=long_goal)]
    _write_ledger(tmp_path, records)

    app = HistoryApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        goal_cell = str(table.get_cell_at((0, 1)))
        assert len(goal_cell) <= 41  # 40 chars + ellipsis
        assert goal_cell.endswith("…")


# ---------------------------------------------------------------------------
# Tests — task detail view
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_screen_task_detail_view(tmp_path: Path) -> None:
    """Pressing Enter on a run row shows task-level cost details."""
    task_breakdown = [
        _make_task(task_id="T01", title="Setup", cost=0.04, duration=40.0),
        _make_task(task_id="T02", title="Implement", cost=0.06, duration=60.0),
        _make_task(task_id="T03", title="Test", cost=0.02, duration=20.0),
    ]
    records = [_make_run(task_count=3, task_breakdown=task_breakdown)]
    _write_ledger(tmp_path, records)

    app = HistoryApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        # Select the first row
        await pilot.press("enter")
        await pilot.pause(0.05)
        # Should be in task detail view with 3 rows
        table = app.query_one(DataTable)
        assert table.row_count == 3
        # Check task IDs
        assert str(table.get_cell_at((0, 0))) == "T01"
        assert str(table.get_cell_at((1, 0))) == "T02"
        assert str(table.get_cell_at((2, 0))) == "T03"
        # Check titles
        assert str(table.get_cell_at((0, 1))) == "Setup"
        assert str(table.get_cell_at((1, 1))) == "Implement"
        # Check costs
        assert str(table.get_cell_at((0, 3))) == "$0.04"
        assert str(table.get_cell_at((1, 3))) == "$0.06"


@pytest.mark.asyncio
async def test_history_screen_task_detail_empty_breakdown(tmp_path: Path) -> None:
    """Old ledger records with no task_breakdown show friendly message."""
    records = [_make_run(task_count=2, task_breakdown=[])]
    _write_ledger(tmp_path, records)

    app = HistoryApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        # Select the first row and press Enter
        await pilot.press("enter")
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count == 1
        # Should show the friendly message
        assert "No task-level data available" in str(table.get_cell_at((0, 1)))


@pytest.mark.asyncio
async def test_history_screen_task_detail_columns(tmp_path: Path) -> None:
    """Task detail view shows all required columns."""
    task_breakdown = [_make_task(task_id="T01")]
    records = [_make_run(task_breakdown=task_breakdown)]
    _write_ledger(tmp_path, records)

    app = HistoryApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        await pilot.press("enter")
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        # Verify all 7 columns: Task, Title, Tokens, Cost, Model, Status, Duration
        # DataTable.clear(columns=True) removes old columns, so only 7 remain
        assert len(table.columns) == 7
        # Check data row values
        assert str(table.get_cell_at((0, 0))) == "T01"
        assert str(table.get_cell_at((0, 5))) == "done"


@pytest.mark.asyncio
async def test_history_screen_task_detail_tokens(tmp_path: Path) -> None:
    """Task detail view shows correct token totals."""
    task_breakdown = [
        _make_task(
            task_id="T01",
            input_tokens=5000,
            output_tokens=3000,
        )
    ]
    records = [_make_run(task_breakdown=task_breakdown)]
    _write_ledger(tmp_path, records)

    app = HistoryApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        await pilot.press("enter")
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        # Total tokens = 5000 + 3000 = 8000, formatted as "8.0K"
        tokens_cell = str(table.get_cell_at((0, 2)))
        assert tokens_cell == "8.0K"


@pytest.mark.asyncio
async def test_history_screen_task_detail_duration(tmp_path: Path) -> None:
    """Task detail view shows formatted duration."""
    task_breakdown = [_make_task(task_id="T01", duration=90.0)]
    records = [_make_run(task_breakdown=task_breakdown)]
    _write_ledger(tmp_path, records)

    app = HistoryApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        await pilot.press("enter")
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        # 90 seconds = 1:30
        duration_cell = str(table.get_cell_at((0, 6)))
        assert duration_cell == "1:30"


# ---------------------------------------------------------------------------
# Tests — return to run-level view
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_screen_return_to_run_level(tmp_path: Path) -> None:
    """Pressing 'q' returns from task detail to run-level view."""
    task_breakdown = [_make_task(task_id="T01")]
    records = [_make_run(task_breakdown=task_breakdown)]
    _write_ledger(tmp_path, records)

    app = HistoryApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        # Go to task detail
        await pilot.press("enter")
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count == 1
        # Task detail view should show task_id in first column
        assert str(table.get_cell_at((0, 0))) == "T01"

        # Return to run-level via 'q'
        await pilot.press("q")
        await pilot.pause(0.05)
        # Should be back to run-level view with 1 row
        assert table.row_count == 1
        # The first column should be the date, not task_id
        assert str(table.get_cell_at((0, 0))) == "2026-05-18"


@pytest.mark.asyncio
async def test_history_screen_return_via_escape(tmp_path: Path) -> None:
    """Pressing Escape returns from task detail to run-level view."""
    task_breakdown = [_make_task(task_id="T01")]
    records = [_make_run(task_breakdown=task_breakdown)]
    _write_ledger(tmp_path, records)

    app = HistoryApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        await pilot.press("enter")
        await pilot.pause(0.05)

        # Return via Escape
        await pilot.press("escape")
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert str(table.get_cell_at((0, 0))) == "2026-05-18"


# ---------------------------------------------------------------------------
# Tests — hint text updates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_screen_hint_run_level(tmp_path: Path) -> None:
    """Hint text mentions task details in run-level view."""
    records = [_make_run()]
    _write_ledger(tmp_path, records)

    app = HistoryApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        hint = app.query_one("#history_hint", Static)
        hint_text = str(hint.render())
        assert "Enter" in hint_text


@pytest.mark.asyncio
async def test_history_screen_hint_task_detail(tmp_path: Path) -> None:
    """Hint text mentions Escape/q in task detail view."""
    task_breakdown = [_make_task(task_id="T01")]
    records = [_make_run(task_breakdown=task_breakdown)]
    _write_ledger(tmp_path, records)

    app = HistoryApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        await pilot.press("enter")
        await pilot.pause(0.05)
        hint = app.query_one("#history_hint", Static)
        hint_text = str(hint.render())
        assert "Escape" in hint_text or "q" in hint_text


# ---------------------------------------------------------------------------
# Tests — refresh binding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_screen_refresh(tmp_path: Path) -> None:
    """Pressing 'r' refreshes the view without error."""
    records = [_make_run()]
    _write_ledger(tmp_path, records)

    app = HistoryApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        await pilot.press("r")
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count == 1


# ---------------------------------------------------------------------------
# Tests — run_history_screen function
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_history_screen_launches_app(tmp_path: Path) -> None:
    """run_history_screen creates and runs a HistoryApp instance."""
    import unittest.mock as mock

    with mock.patch.object(HistoryApp, "run") as mock_run:
        run_history_screen(project=tmp_path)
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — multiple runs with mixed breakdown data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_screen_multiple_runs_mixed_breakdown(tmp_path: Path) -> None:
    """Screen handles runs with and without task_breakdown data."""
    records = [
        _make_run(goal="Old run", task_breakdown=[]),
        _make_run(
            goal="New run",
            task_breakdown=[
                _make_task(task_id="T01", title="Feature A"),
                _make_task(task_id="T02", title="Feature B"),
            ],
        ),
    ]
    _write_ledger(tmp_path, records)

    app = HistoryApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count == 2

        # Select first run (no breakdown) — press Enter (auto-selects first row)
        await pilot.press("enter")
        await pilot.pause(0.05)
        # Should show empty breakdown message
        assert "No task-level data available" in str(table.get_cell_at((0, 1)))

        # Return to run-level
        await pilot.press("q")
        await pilot.pause(0.05)

        # Navigate to second run by directly selecting it
        app._selected_row = 1
        app._selected_record = records[1]
        app._show_task_detail()
        await pilot.pause(0.05)
        # Should show 2 tasks
        assert table.row_count == 2
        assert str(table.get_cell_at((0, 0))) == "T01"
        assert str(table.get_cell_at((1, 0))) == "T02"
