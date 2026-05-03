"""Tests for Phase 4 read-only TUI screens."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from textual.widgets import DataTable

from the_architect.tui.screens.circuit_screen import CircuitApp
from the_architect.tui.screens.list_screen import ListApp
from the_architect.tui.screens.logs_screen import LogsApp
from the_architect.tui.screens.monitor_screen import MonitorApp
from the_architect.tui.screens.status_screen import StatusApp


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "T01_first.md").write_text("# T01 — First\n", encoding="utf-8")
    (tmp_path / "tasks" / "T02_second.md").write_text("# T02 — Second\n", encoding="utf-8")
    (tmp_path / "PROGRESS.md").write_text(
        "# Progress\n"
        "| Task | Title | Status | Completed |\n"
        "|------|-------|--------|-----------|\n"
        "| T01 | First | Done | 2026-05-01 |\n"
        "| T02 | Second | Pending | — |\n",
        encoding="utf-8",
    )
    return tmp_path


class TestListApp:
    @pytest.mark.asyncio
    async def test_shows_tasks_and_summary(self, tmp_path: Path) -> None:
        proj = _make_project(tmp_path)
        app = ListApp(project=proj)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one(DataTable)
            assert table.row_count == 2

    @pytest.mark.asyncio
    async def test_handles_missing_tasks_dir(self, tmp_path: Path) -> None:
        app = ListApp(project=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one(DataTable)
            assert table.row_count == 0


class TestStatusApp:
    @pytest.mark.asyncio
    async def test_renders_run_state(self, tmp_path: Path) -> None:
        proj = _make_project(tmp_path)
        app = StatusApp(project=proj)
        async with app.run_test() as pilot:
            await pilot.pause()
            tasks_table = app.query_one("#tasks_table", DataTable)
            assert tasks_table.row_count == 2


class TestLogsApp:
    @pytest.mark.asyncio
    async def test_handles_empty_log_dir(self, tmp_path: Path) -> None:
        app = LogsApp(project=tmp_path, task_prefix="", tail=50)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#logs_table", DataTable)
            assert table.row_count == 0

    @pytest.mark.asyncio
    async def test_lists_log_files_when_present(self, tmp_path: Path) -> None:
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "T01_first.log").write_text("hello\nworld\n", encoding="utf-8")
        app = LogsApp(project=tmp_path, task_prefix="T01", tail=50)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#logs_table", DataTable)
            assert table.row_count == 1


class TestCircuitApp:
    @pytest.mark.asyncio
    async def test_empty_circuit_state_shows_tasks_as_closed(self, tmp_path: Path) -> None:
        proj = _make_project(tmp_path)
        app = CircuitApp(project=proj)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one(DataTable)
            # Two tasks defined; both should appear with CLOSED (no circuit state yet).
            assert table.row_count == 2


class TestMonitorApp:
    @pytest.mark.asyncio
    async def test_handles_missing_state_file(self, tmp_path: Path) -> None:
        app = MonitorApp(project=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            # No crash; content static must mention the missing state file.
            from textual.widgets import Static

            content = app.query_one("#monitor_content", Static)
            assert "No active monitor state" in str(content.render())

    @pytest.mark.asyncio
    async def test_renders_state_fields(self, tmp_path: Path) -> None:
        state = {
            "status": "RUNNING",
            "total_tasks": 5,
            "completed_tasks": 2,
            "session_tokens": 12345,
            "current_task_id": "T03",
            "current_task_title": "Demo",
            "current_attempt": 1,
            "current_model": "claude-sonnet",
            "cooldown_active": False,
            "last_attempt_tokens": 234,
            "task_statuses": {"T01": "done", "T02": "done", "T03": "running"},
            "started_at": datetime.now(tz=UTC).isoformat(),
        }
        state_dir = tmp_path / ".architect"
        state_dir.mkdir()
        (state_dir / "monitor_state.json").write_text(json.dumps(state), encoding="utf-8")

        app = MonitorApp(project=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import Static

            content = app.query_one("#monitor_content", Static)
            rendered = str(content.render())
            assert "T03" in rendered
            assert "RUNNING" in rendered
