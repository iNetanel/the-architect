"""Tests for the Textual CircuitScreen.

The CircuitApp displays per-task circuit breaker state with counters,
recovery actions, and elapsed time since opening.
Tests mount the app inside a small harness.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from textual.app import App
from textual.widgets import DataTable, Static

from the_architect.tui.screens.circuit_screen import CircuitApp, run_circuit_screen


class _Harness(App[None]):
    """Minimal app that mounts CircuitApp as a screen for testing."""

    def __init__(self, screen: Any) -> None:
        super().__init__()
        self._screen = screen
        self.dismissed: Any = "<not-dismissed>"

    def on_mount(self) -> None:
        self.push_screen(self._screen, self._on_dismiss)

    def _on_dismiss(self, value: Any) -> None:
        self.dismissed = value


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_project(
    tmp_path: Path,
    *,
    with_tasks: bool = False,
    circuit_data: dict | None = None,
) -> Path:
    """Create a temporary project with optional sub-artifacts."""
    project = tmp_path / "proj"
    project.mkdir()

    # architect.toml — minimal
    config_file = project / "architect.toml"
    config_file.write_text("[architect]\n")

    # .architect/ dir
    arch_dir = project / ".architect"
    arch_dir.mkdir()

    if with_tasks:
        tasks_dir = project / "tasks"
        tasks_dir.mkdir()
        task_file = tasks_dir / "T01_test.md"
        task_file.write_text("# T01 — Test\n")
        task_file2 = tasks_dir / "T02_test2.md"
        task_file2.write_text("# T02 — Test2\n")

    if circuit_data is not None:
        circuit_file = arch_dir / "circuit.json"
        circuit_file.write_text(json.dumps(circuit_data))

    return project


def _timestamp(seconds_ago: int) -> str:
    """Return an ISO timestamp string for `seconds_ago` seconds in the past."""
    then = datetime.now(tz=UTC) - timedelta(seconds=seconds_ago)
    return then.isoformat()


# ── Basic rendering ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_circuit_table_columns(tmp_path: Path) -> None:
    """DataTable has correct columns."""
    project = _make_project(tmp_path, with_tasks=True)
    app = CircuitApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        # The table is created with 6 columns: Task, State, No-prog, Same-err, Recovery, Opened
        # Verify columns exist by checking that we can read cell positions for all 6 columns
        # when a row exists — use the discovered tasks (which all get CLOSED rows)
        assert table.row_count >= 1
        for col_idx in range(6):
            cell = table.get_cell_at((0, col_idx))
            assert cell is not None


@pytest.mark.asyncio
async def test_circuit_title_shows_project(tmp_path: Path) -> None:
    """Circuit title widget contains the project path."""
    project = _make_project(tmp_path)
    app = CircuitApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        title = app.query_one("#circuit_title", Static)
        rendered = str(title.render())
        assert "Circuit breaker" in rendered
        assert "proj" in rendered


@pytest.mark.asyncio
async def test_circuit_hint_shown(tmp_path: Path) -> None:
    """Hint text is rendered."""
    project = _make_project(tmp_path)
    app = CircuitApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        hint = app.query_one("#circuit_hint", Static)
        rendered = str(hint.render())
        assert "architect circuit --reset" in rendered


# ── Tasks without circuit state ────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_without_circuit_state_shows_closed(tmp_path: Path) -> None:
    """A discovered task with no circuit state shows CLOSED with zeros."""
    project = _make_project(tmp_path, with_tasks=True)
    # No circuit.json — tasks exist but no circuit state
    app = CircuitApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count >= 1
        state_cell = str(table.get_cell_at((0, 1)))
        assert "CLOSED" in state_cell
        recovery_cell = str(table.get_cell_at((0, 4)))
        assert "—" in recovery_cell


@pytest.mark.asyncio
async def test_no_tasks_no_circuit_empty_table(tmp_path: Path) -> None:
    """No tasks dir and no circuit file means empty table."""
    project = _make_project(tmp_path)
    # No tasks dir, no circuit.json
    app = CircuitApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count == 0


# ── CLOSED state ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_closed_state_displayed(tmp_path: Path) -> None:
    """CircuitState.CLOSED renders as 'CLOSED'."""
    circuit_data = {
        "T01": {
            "state": "CLOSED",
            "consecutive_no_progress": 0,
            "consecutive_same_error": 0,
            "recovery_action": None,
            "opened_at": None,
        }
    }
    project = _make_project(tmp_path, with_tasks=True, circuit_data=circuit_data)
    app = CircuitApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count >= 1
        state_cell = str(table.get_cell_at((0, 1)))
        assert "CLOSED" in state_cell


# ── OPEN state ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_open_state_displayed(tmp_path: Path) -> None:
    """CircuitState.OPEN renders as 'OPEN'."""
    circuit_data = {
        "T01": {
            "state": "OPEN",
            "consecutive_no_progress": 3,
            "consecutive_same_error": 2,
            "recovery_action": "REPLAN",
            "opened_at": _timestamp(120),
        }
    }
    project = _make_project(tmp_path, with_tasks=True, circuit_data=circuit_data)
    app = CircuitApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count >= 1
        state_cell = str(table.get_cell_at((0, 1)))
        assert "OPEN" in state_cell
        no_prog = str(table.get_cell_at((0, 2)))
        assert "3" in no_prog
        same_err = str(table.get_cell_at((0, 3)))
        assert "2" in same_err
        recovery = str(table.get_cell_at((0, 4)))
        assert "REPLAN" in recovery


# ── HALF_OPEN state ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_half_open_state_displayed(tmp_path: Path) -> None:
    """CircuitState.HALF_OPEN renders as 'HALF_OPEN'."""
    circuit_data = {
        "T01": {
            "state": "HALF_OPEN",
            "consecutive_no_progress": 1,
            "consecutive_same_error": 0,
            "recovery_action": "WAIT",
            "opened_at": _timestamp(30),
        }
    }
    project = _make_project(tmp_path, with_tasks=True, circuit_data=circuit_data)
    app = CircuitApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count >= 1
        state_cell = str(table.get_cell_at((0, 1)))
        assert "HALF_OPEN" in state_cell
        recovery = str(table.get_cell_at((0, 4)))
        assert "WAIT" in recovery


# ── Recovery action display ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recovery_action_none_shows_dash(tmp_path: Path) -> None:
    """recovery_action=None shows '—'."""
    circuit_data = {
        "T01": {
            "state": "CLOSED",
            "consecutive_no_progress": 0,
            "consecutive_same_error": 0,
            "recovery_action": None,
            "opened_at": None,
        }
    }
    project = _make_project(tmp_path, with_tasks=True, circuit_data=circuit_data)
    app = CircuitApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count >= 1
        # Recovery column (index 4) should be "—"
        recovery = str(table.get_cell_at((0, 4)))
        assert recovery == "—"


@pytest.mark.asyncio
async def test_recovery_action_cooldown_wait(tmp_path: Path) -> None:
    """recovery_action=COOLDOWN_WAIT shows in table."""
    circuit_data = {
        "T01": {
            "state": "OPEN",
            "consecutive_no_progress": 0,
            "consecutive_same_error": 1,
            "recovery_action": "COOLDOWN_WAIT",
            "opened_at": _timestamp(60),
        }
    }
    project = _make_project(tmp_path, with_tasks=True, circuit_data=circuit_data)
    app = CircuitApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count >= 1
        recovery = str(table.get_cell_at((0, 4)))
        assert "COOLDOWN_WAIT" in recovery


# ── Elapsed time formatting ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_opened_at_seconds(tmp_path: Path) -> None:
    """opened_at < 60 seconds shows '{elapsed}s ago'."""
    circuit_data = {
        "T01": {
            "state": "OPEN",
            "consecutive_no_progress": 1,
            "consecutive_same_error": 0,
            "recovery_action": "WAIT",
            "opened_at": _timestamp(30),
        }
    }
    project = _make_project(tmp_path, with_tasks=True, circuit_data=circuit_data)
    app = CircuitApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count >= 1
        # Opened column (index 5) should contain seconds
        opened = str(table.get_cell_at((0, 5)))
        assert "s ago" in opened


@pytest.mark.asyncio
async def test_opened_at_minutes(tmp_path: Path) -> None:
    """opened_at 60-3599 seconds shows '{elapsed}m ago'."""
    circuit_data = {
        "T01": {
            "state": "OPEN",
            "consecutive_no_progress": 1,
            "consecutive_same_error": 0,
            "recovery_action": "WAIT",
            "opened_at": _timestamp(120),
        }
    }
    project = _make_project(tmp_path, with_tasks=True, circuit_data=circuit_data)
    app = CircuitApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count >= 1
        opened = str(table.get_cell_at((0, 5)))
        assert "m ago" in opened
        # Should be minutes, not hours
        assert "h" not in opened


@pytest.mark.asyncio
async def test_opened_at_hours(tmp_path: Path) -> None:
    """opened_at >= 3600 seconds shows '{h}h {m}m ago'."""
    circuit_data = {
        "T01": {
            "state": "OPEN",
            "consecutive_no_progress": 1,
            "consecutive_same_error": 0,
            "recovery_action": "WAIT",
            "opened_at": _timestamp(7200),  # 2 hours ago
        }
    }
    project = _make_project(tmp_path, with_tasks=True, circuit_data=circuit_data)
    app = CircuitApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count >= 1
        opened = str(table.get_cell_at((0, 5)))
        assert "h" in opened
        assert "m ago" in opened


@pytest.mark.asyncio
async def test_opened_at_none_shows_dash(tmp_path: Path) -> None:
    """opened_at=None shows '—'."""
    circuit_data = {
        "T01": {
            "state": "CLOSED",
            "consecutive_no_progress": 0,
            "consecutive_same_error": 0,
            "recovery_action": None,
            "opened_at": None,
        }
    }
    project = _make_project(tmp_path, with_tasks=True, circuit_data=circuit_data)
    app = CircuitApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count >= 1
        opened = str(table.get_cell_at((0, 5)))
        assert opened == "—"


@pytest.mark.asyncio
async def test_opened_at_invalid_iso(tmp_path: Path) -> None:
    """Invalid opened_at ISO string shows truncated fallback."""
    circuit_data = {
        "T01": {
            "state": "OPEN",
            "consecutive_no_progress": 1,
            "consecutive_same_error": 0,
            "recovery_action": "WAIT",
            "opened_at": "not-a-valid-timestamp",
        }
    }
    project = _make_project(tmp_path, with_tasks=True, circuit_data=circuit_data)
    app = CircuitApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count >= 1
        opened = str(table.get_cell_at((0, 5)))
        # Should fall back to truncated string (first 16 chars)
        assert "not-a-valid-t" in opened


# ── Tz-naive datetime handling ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_opened_at_tz_naive(tmp_path: Path) -> None:
    """Tz-naive opened_at is handled gracefully (UTC assumed)."""
    circuit_data = {
        "T01": {
            "state": "OPEN",
            "consecutive_no_progress": 1,
            "consecutive_same_error": 0,
            "recovery_action": "WAIT",
            "opened_at": "2026-05-16T12:00:00",  # no timezone
        }
    }
    project = _make_project(tmp_path, with_tasks=True, circuit_data=circuit_data)
    app = CircuitApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count >= 1
        opened = str(table.get_cell_at((0, 5)))
        # Should show a large elapsed time (hours)
        assert "h" in opened


# ── Sorted task display ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tasks_sorted_alphabetically(tmp_path: Path) -> None:
    """Tasks are displayed in sorted order by task ID."""
    circuit_data = {
        "T03": {
            "state": "OPEN",
            "consecutive_no_progress": 1,
            "consecutive_same_error": 0,
            "recovery_action": "WAIT",
            "opened_at": None,
        },
        "T01": {
            "state": "CLOSED",
            "consecutive_no_progress": 0,
            "consecutive_same_error": 0,
            "recovery_action": None,
            "opened_at": None,
        },
        "T02": {
            "state": "HALF_OPEN",
            "consecutive_no_progress": 0,
            "consecutive_same_error": 0,
            "recovery_action": None,
            "opened_at": None,
        },
    }
    project = _make_project(tmp_path, with_tasks=True, circuit_data=circuit_data)
    app = CircuitApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        task_ids = [str(table.get_cell_at((i, 0))) for i in range(table.row_count)]
        assert task_ids == sorted(task_ids)


# ── Refresh action ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_action_refresh(tmp_path: Path) -> None:
    """action_refresh calls _refresh."""
    project = _make_project(tmp_path)
    app = CircuitApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        # Should not raise
        app.action_refresh()
        await pilot.pause(0.05)


# ── run_circuit_screen function ────────────────────────────────────────


def test_run_circuit_screen_calls_run(tmp_path: Path) -> None:
    """run_circuit_screen launches the CircuitApp."""
    project = _make_project(tmp_path)
    with patch.object(CircuitApp, "run") as mock_run:
        run_circuit_screen(project)
        mock_run.assert_called_once()


# ── Mixed tasks with and without circuit state ─────────────────────────


@pytest.mark.asyncio
async def test_mixed_circuit_and_discovered_tasks(tmp_path: Path) -> None:
    """Tasks discovered from files but not in circuit.json get CLOSED."""
    circuit_data = {
        "T01": {
            "state": "OPEN",
            "consecutive_no_progress": 2,
            "consecutive_same_error": 0,
            "recovery_action": "WAIT",
            "opened_at": _timestamp(60),
        }
    }
    project = _make_project(tmp_path, with_tasks=True, circuit_data=circuit_data)
    # T01 has circuit state, T02 should appear as CLOSED
    app = CircuitApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        # Both T01 and T02 should be present
        task_ids = [str(table.get_cell_at((i, 0))) for i in range(table.row_count)]
        assert "T01" in task_ids
        assert "T02" in task_ids
        # T02 should be CLOSED
        t02_idx = task_ids.index("T02")
        assert str(table.get_cell_at((t02_idx, 1))) == "CLOSED"
        assert str(table.get_cell_at((t02_idx, 4))) == "—"
        assert str(table.get_cell_at((t02_idx, 5))) == "—"
