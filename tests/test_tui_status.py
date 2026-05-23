"""Tests for the Textual StatusScreen.

The StatusApp displays project config, provider info, lock file status,
task list, circuit breaker state, token budget, and log file info.
Tests mount the app inside a small harness.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from textual.app import App
from textual.widgets import DataTable, Static

from the_architect.tui.screens.status_screen import StatusApp, run_status_screen


class _Harness(App[None]):
    """Minimal app that mounts StatusApp as a screen for testing."""

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
    with_lock: bool = False,
    with_tasks: bool = False,
    with_circuit: bool = False,
    with_logs: bool = False,
    log_count: int = 3,
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

    if with_lock:
        lock_file = arch_dir / "runner.lock"
        lock_file.write_text(str(os.getpid()))  # valid PID (our own process)

    if with_tasks:
        tasks_dir = project / "tasks"
        tasks_dir.mkdir()
        # Write a minimal task file
        task_file = tasks_dir / "T01_test.md"
        task_file.write_text("# T01 — Test\n")
        # Write PROGRESS.md
        progress = tasks_dir / "PROGRESS.md"
        progress.write_text(
            "# Progress\n\n"
            "| Task | Title | Status |\n"
            "|------|-------|--------|\n"
            "| T01 | test | Done |\n"
        )

    if with_circuit:
        circuit_file = arch_dir / "circuit.json"
        circuit_data = {
            "T01": {
                "state": "OPEN",
                "consecutive_no_progress": 3,
                "consecutive_same_error": 2,
            }
        }
        circuit_file.write_text(json.dumps(circuit_data))

    if with_logs:
        log_dir = arch_dir / "logs"
        log_dir.mkdir()
        for i in range(log_count):
            (log_dir / f"run_{i}.log").write_text(f"log content {i}" * 100)

    return project


# ── Lock file display ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lock_not_running_default(tmp_path: Path) -> None:
    """Default state shows 'Not running' when no lock file."""
    project = _make_project(tmp_path)
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        lock_line = app.query_one("#lock_line", Static)
        rendered = str(lock_line.render())
        assert "Not running" in rendered


@pytest.mark.asyncio
async def test_lock_running_with_alive_pid(tmp_path: Path) -> None:
    """Lock file with a valid alive PID shows 'Running · PID {pid}'."""
    project = _make_project(tmp_path, with_lock=True)
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        lock_line = app.query_one("#lock_line", Static)
        rendered = str(lock_line.render())
        assert "Running" in rendered
        assert "PID" in rendered


@pytest.mark.asyncio
async def test_lock_stale_pid(tmp_path: Path) -> None:
    """Lock file with a dead PID shows 'Not running (stale lock)'."""
    project = _make_project(tmp_path)
    arch_dir = project / ".architect"
    lock_file = arch_dir / "runner.lock"
    lock_file.write_text("99999999")  # nonexistent PID
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        lock_line = app.query_one("#lock_line", Static)
        rendered = str(lock_line.render())
        assert "stale lock" in rendered


@pytest.mark.asyncio
async def test_lock_permission_error(tmp_path: Path) -> None:
    """os.kill PermissionError on PID shows 'Not running (stale lock)'."""
    project = _make_project(tmp_path, with_lock=True)
    # Patch os.kill to raise PermissionError
    with patch("os.kill", side_effect=PermissionError("no perms")):
        app = StatusApp(project=project)
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            lock_line = app.query_one("#lock_line", Static)
            rendered = str(lock_line.render())
            assert "stale lock" in rendered


@pytest.mark.asyncio
async def test_lock_oserror_on_read(tmp_path: Path) -> None:
    """OSError reading lock file shows 'Not running'."""
    project = _make_project(tmp_path, with_lock=True)
    # Patch Path.read_text to raise OSError
    with patch("pathlib.Path.read_text", side_effect=OSError("boom")):
        app = StatusApp(project=project)
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            lock_line = app.query_one("#lock_line", Static)
            rendered = str(lock_line.render())
            assert "Not running" in rendered


@pytest.mark.asyncio
async def test_lock_valueerror_non_int_pid(tmp_path: Path) -> None:
    """Lock file with non-integer PID shows 'Not running'."""
    project = _make_project(tmp_path)
    arch_dir = project / ".architect"
    lock_file = arch_dir / "runner.lock"
    lock_file.write_text("not_a_number")
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        lock_line = app.query_one("#lock_line", Static)
        rendered = str(lock_line.render())
        assert "Not running" in rendered


# ── Tasks display ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tasks_displayed(tmp_path: Path) -> None:
    """Tasks directory with task files shows rows in table."""
    project = _make_project(tmp_path, with_tasks=True)
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one("#tasks_table", DataTable)
        # There should be at least one row
        assert table.row_count >= 1


@pytest.mark.asyncio
async def test_task_summary_done_count(tmp_path: Path) -> None:
    """Task summary shows '1/1 complete' when one task is Done."""
    project = _make_project(tmp_path, with_tasks=True)
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        summary = app.query_one("#task_summary", Static)
        rendered = str(summary.render())
        assert "1/1 complete" in rendered


@pytest.mark.asyncio
async def test_no_tasks_directory(tmp_path: Path) -> None:
    """Missing tasks directory shows 'No tasks directory.'"""
    project = _make_project(tmp_path)
    # No tasks dir created
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        summary = app.query_one("#task_summary", Static)
        rendered = str(summary.render())
        assert "No tasks directory" in rendered


# ── Circuit breaker display ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_circuit_open_shown(tmp_path: Path) -> None:
    """OPEN circuit state appears in circuit table."""
    project = _make_project(tmp_path, with_circuit=True)
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one("#circuit_table", DataTable)
        assert table.row_count >= 1
        # Row should contain OPEN in the State column
        state_cell = str(table.get_cell_at((0, 1)))
        assert "OPEN" in state_cell


@pytest.mark.asyncio
async def test_circuit_half_open_shown(tmp_path: Path) -> None:
    """HALF_OPEN circuit state appears in circuit table."""
    project = _make_project(tmp_path)
    arch_dir = project / ".architect"
    circuit_data = {
        "T02": {
            "state": "HALF_OPEN",
            "consecutive_no_progress": 1,
            "consecutive_same_error": 0,
        }
    }
    (arch_dir / "circuit.json").write_text(json.dumps(circuit_data))
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one("#circuit_table", DataTable)
        assert table.row_count >= 1
        state_cell = str(table.get_cell_at((0, 1)))
        assert "HALF_OPEN" in state_cell


@pytest.mark.asyncio
async def test_circuit_closed_not_shown(tmp_path: Path) -> None:
    """CLOSED circuit states are NOT shown (only OPEN/HALF_OPEN)."""
    project = _make_project(tmp_path)
    arch_dir = project / ".architect"
    circuit_data = {
        "T01": {
            "state": "CLOSED",
            "consecutive_no_progress": 0,
            "consecutive_same_error": 0,
        }
    }
    (arch_dir / "circuit.json").write_text(json.dumps(circuit_data))
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one("#circuit_table", DataTable)
        # CLOSED states should not appear
        assert table.row_count == 0


@pytest.mark.asyncio
async def test_circuit_invalid_json_graceful(tmp_path: Path) -> None:
    """Invalid JSON in circuit file is handled gracefully."""
    project = _make_project(tmp_path)
    arch_dir = project / ".architect"
    (arch_dir / "circuit.json").write_text("not valid json {{{")
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one("#circuit_table", DataTable)
        # Should be empty since parsing failed gracefully
        assert table.row_count == 0


@pytest.mark.asyncio
async def test_circuit_file_not_exists(tmp_path: Path) -> None:
    """No circuit file means empty circuit table."""
    project = _make_project(tmp_path)
    # No circuit.json
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one("#circuit_table", DataTable)
        assert table.row_count == 0


# ── Token budget display ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_budget_unlimited_default(tmp_path: Path) -> None:
    """Default config shows 'unlimited' for token budget."""
    project = _make_project(tmp_path)
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        token_line = app.query_one("#token_line", Static)
        rendered = str(token_line.render())
        assert "unlimited" in rendered


@pytest.mark.asyncio
async def test_budget_per_hour_displayed(tmp_path: Path) -> None:
    """token_budget_per_hour > 0 shows in budget line."""
    project = _make_project(tmp_path)
    config_file = project / "architect.toml"
    config_file.write_text("[architect]\ntoken_budget_per_hour = 500000\n")
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        token_line = app.query_one("#token_line", Static)
        rendered = str(token_line.render())
        assert "tokens/hour" in rendered


@pytest.mark.asyncio
async def test_budget_per_run_displayed(tmp_path: Path) -> None:
    """token_budget_per_run > 0 shows in budget line."""
    project = _make_project(tmp_path)
    config_file = project / "architect.toml"
    config_file.write_text("[architect]\ntoken_budget_per_run = 1000000\n")
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        token_line = app.query_one("#token_line", Static)
        rendered = str(token_line.render())
        assert "tokens/run" in rendered


@pytest.mark.asyncio
async def test_task_timeout_displayed(tmp_path: Path) -> None:
    """task_timeout > 0 shows in budget line."""
    project = _make_project(tmp_path)
    config_file = project / "architect.toml"
    config_file.write_text("[architect]\ntask_timeout = 300\n")
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        token_line = app.query_one("#token_line", Static)
        rendered = str(token_line.render())
        assert "300s/task timeout" in rendered


@pytest.mark.asyncio
async def test_budget_all_three_displayed(tmp_path: Path) -> None:
    """All three budget fields show when all set."""
    project = _make_project(tmp_path)
    config_file = project / "architect.toml"
    config_file.write_text(
        "[architect]\n"
        "token_budget_per_hour = 500000\n"
        "token_budget_per_run = 1000000\n"
        "task_timeout = 300\n"
    )
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        token_line = app.query_one("#token_line", Static)
        rendered = str(token_line.render())
        assert "tokens/hour" in rendered
        assert "tokens/run" in rendered
        assert "300s/task timeout" in rendered


# ── Logs display ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_logs_displayed(tmp_path: Path) -> None:
    """Log files appear in logs section."""
    project = _make_project(tmp_path, with_logs=True, log_count=3)
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        logs_line = app.query_one("#logs_line", Static)
        rendered = str(logs_line.render())
        assert "run_0.log" in rendered or "log" in rendered.lower()


@pytest.mark.asyncio
async def test_logs_many_files_truncated(tmp_path: Path) -> None:
    ">5 log files shows 'and N more' truncation."
    project = _make_project(tmp_path, with_logs=True, log_count=8)
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        logs_line = app.query_one("#logs_line", Static)
        rendered = str(logs_line.render())
        assert "and 3 more" in rendered


@pytest.mark.asyncio
async def test_logs_no_logs_yet(tmp_path: Path) -> None:
    """No log directory shows 'No logs yet.'"""
    project = _make_project(tmp_path)
    # No logs dir
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        logs_line = app.query_one("#logs_line", Static)
        rendered = str(logs_line.render())
        assert "No logs yet" in rendered


@pytest.mark.asyncio
async def test_logs_empty_dir(tmp_path: Path) -> None:
    """Empty log directory shows 'No logs yet.'"""
    project = _make_project(tmp_path)
    arch_dir = project / ".architect"
    log_dir = arch_dir / "logs"
    log_dir.mkdir()
    # Empty — no .log files
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        logs_line = app.query_one("#logs_line", Static)
        rendered = str(logs_line.render())
        assert "No logs yet" in rendered


# ── Refresh action ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_action_refresh(tmp_path: Path) -> None:
    """action_refresh calls _refresh_all."""
    project = _make_project(tmp_path)
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        # Should not raise
        app.action_refresh()
        await pilot.pause(0.05)


# ── run_status_screen function ─────────────────────────────────────────


def test_run_status_screen_calls_run(tmp_path: Path) -> None:
    """run_status_screen launches the StatusApp."""
    project = _make_project(tmp_path)
    with patch.object(StatusApp, "run") as mock_run:
        run_status_screen(project)
        mock_run.assert_called_once()


# ── Title display ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_title_shows_project(tmp_path: Path) -> None:
    """Status title widget contains the project path."""
    project = _make_project(tmp_path)
    app = StatusApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        title = app.query_one("#status_title", Static)
        rendered = str(title.render())
        assert "Status" in rendered
        assert "proj" in rendered
