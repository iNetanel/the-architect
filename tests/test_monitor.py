"""Tests for the monitoring state file and monitor subcommand.

Covers:
- State file atomic writes
- Stop / kill flags
- MonitorStateWriter event hooks
- architect monitor subcommand
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from the_architect.cli import main
from the_architect.core.monitor_state import (
    KILL_FLAG_FILE,
    MONITOR_STATE_FILE,
    RUN_STATUS_COOLDOWN,
    RUN_STATUS_DONE,
    RUN_STATUS_FAILED,
    RUN_STATUS_KILLED,
    RUN_STATUS_PLANNING,
    RUN_STATUS_RUNNING,
    RUN_STATUS_STOPPING,
    STOP_FLAG_FILE,
    TASK_STATUS_DONE,
    TASK_STATUS_FAILED,
    MonitorStateWriter,
    check_kill_flag,
    check_stop_flag,
    clear_stop_flags,
    read_monitor_state,
    write_monitor_state,
    write_planning_state,
)
from the_architect.core.tasks import Task, TaskStatus

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def _make_task(tmp_path: Path, prefix: str = "T01", title: str = "Setup") -> Task:
    path = tmp_path / "tasks" / f"{prefix}_test.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {prefix} — {title}\n", encoding="utf-8")
    return Task(
        name=f"{prefix}_test",
        prefix=prefix,
        number=1,
        path=path,
        title=title,
        status=TaskStatus.PENDING,
    )


# ---------------------------------------------------------------------------
# State file — atomic write
# ---------------------------------------------------------------------------


class TestStateFileWrite:
    """Tests for atomic state file writes."""

    def test_write_creates_file(self, tmp_path: Path) -> None:
        state = {"project_name": "test", "status": "RUNNING"}
        write_monitor_state(tmp_path, state)
        assert (tmp_path / MONITOR_STATE_FILE).exists()

    def test_write_is_valid_json(self, tmp_path: Path) -> None:
        state = {"project_name": "test", "status": "RUNNING", "tasks": []}
        write_monitor_state(tmp_path, state)
        data = json.loads((tmp_path / MONITOR_STATE_FILE).read_text(encoding="utf-8"))
        assert data["project_name"] == "test"
        assert data["status"] == "RUNNING"

    def test_write_no_temp_files_left(self, tmp_path: Path) -> None:
        write_monitor_state(tmp_path, {"project_name": "test"})
        temp_files = list((tmp_path / ".architect").glob(".monitor_state_tmp_*"))
        assert temp_files == []

    def test_read_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert read_monitor_state(tmp_path) is None

    def test_read_returns_none_on_invalid_json(self, tmp_path: Path) -> None:
        state_path = tmp_path / MONITOR_STATE_FILE
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("not valid json {{{{", encoding="utf-8")
        assert read_monitor_state(tmp_path) is None

    def test_read_roundtrip(self, tmp_path: Path) -> None:
        state = {
            "project_name": "my-project",
            "status": "RUNNING",
            "current_task_id": "T03",
            "tokens": {"session_total": 12345, "last_attempt": 3456},
        }
        write_monitor_state(tmp_path, state)
        result = read_monitor_state(tmp_path)
        assert result is not None
        assert result["project_name"] == "my-project"
        assert result["tokens"]["session_total"] == 12345

    def test_write_failure_does_not_raise(self, tmp_path: Path) -> None:
        bad_path = tmp_path / "not_a_dir"
        bad_path.write_text("blocking file", encoding="utf-8")
        write_monitor_state(bad_path, {"status": "RUNNING"})


# ---------------------------------------------------------------------------
# Stop / kill flags
# ---------------------------------------------------------------------------


class TestStopKillFlags:
    def test_check_stop_flag_absent(self, tmp_path: Path) -> None:
        assert check_stop_flag(tmp_path) is False

    def test_check_stop_flag_present(self, tmp_path: Path) -> None:
        flag = tmp_path / STOP_FLAG_FILE
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("", encoding="utf-8")
        assert check_stop_flag(tmp_path) is True

    def test_check_kill_flag_absent(self, tmp_path: Path) -> None:
        assert check_kill_flag(tmp_path) is False

    def test_check_kill_flag_present(self, tmp_path: Path) -> None:
        flag = tmp_path / KILL_FLAG_FILE
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("", encoding="utf-8")
        assert check_kill_flag(tmp_path) is True

    def test_clear_stop_flags_removes_both(self, tmp_path: Path) -> None:
        for flag_path in (STOP_FLAG_FILE, KILL_FLAG_FILE):
            full = tmp_path / flag_path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text("", encoding="utf-8")
        clear_stop_flags(tmp_path)
        assert not (tmp_path / STOP_FLAG_FILE).exists()
        assert not (tmp_path / KILL_FLAG_FILE).exists()

    def test_clear_stop_flags_no_error_when_absent(self, tmp_path: Path) -> None:
        clear_stop_flags(tmp_path)


# ---------------------------------------------------------------------------
# MonitorStateWriter
# ---------------------------------------------------------------------------


class TestMonitorStateWriter:
    def _make_writer(self, tmp_path: Path, tasks: list[Task] | None = None) -> MonitorStateWriter:
        if tasks is None:
            tasks = [_make_task(tmp_path, "T01", "Setup")]
        return MonitorStateWriter(
            project_dir=tmp_path, tasks=tasks, free_rotator=None, max_retries=3
        )

    def test_init_writes_state(self, tmp_path: Path) -> None:
        writer = self._make_writer(tmp_path)
        task = _make_task(tmp_path, "T01", "Setup")
        writer.on_task_start(task)
        state = read_monitor_state(tmp_path)
        assert state is not None
        assert state["current_task_id"] == "T01"
        assert state["status"] == RUN_STATUS_RUNNING

    def test_on_task_done_marks_done(self, tmp_path: Path) -> None:
        writer = self._make_writer(tmp_path)
        task = _make_task(tmp_path, "T01", "Setup")
        writer.on_task_start(task)
        writer.on_task_done("T01", tokens=1000)
        state = read_monitor_state(tmp_path)
        assert state is not None
        task_entry = next(t for t in state["tasks"] if t["id"] == "T01")
        assert task_entry["status"] == TASK_STATUS_DONE
        assert state["tokens"]["session_total"] == 1000

    def test_on_task_failed_marks_failed(self, tmp_path: Path) -> None:
        writer = self._make_writer(tmp_path)
        task = _make_task(tmp_path, "T01", "Setup")
        writer.on_task_start(task)
        writer.on_task_failed("T01", tokens=500)
        state = read_monitor_state(tmp_path)
        assert state is not None
        task_entry = next(t for t in state["tasks"] if t["id"] == "T01")
        assert task_entry["status"] == TASK_STATUS_FAILED

    def test_on_attempt_start_updates_attempt(self, tmp_path: Path) -> None:
        writer = self._make_writer(tmp_path)
        task = _make_task(tmp_path, "T01", "Setup")
        writer.on_task_start(task)
        writer.on_attempt_start(2, "claude-sonnet")
        state = read_monitor_state(tmp_path)
        assert state is not None
        assert state["current_attempt"] == 2
        assert state["model"]["current"] == "claude-sonnet"

    def test_on_model_rotated_increments_rotation(self, tmp_path: Path) -> None:
        writer = self._make_writer(tmp_path)
        task = _make_task(tmp_path, "T01", "Setup")
        writer.on_task_start(task)
        writer.on_attempt_start(1, "model-a")
        writer.on_model_rotated("model-b")
        state = read_monitor_state(tmp_path)
        assert state is not None
        assert state["model"]["rotation_count"] == 1
        assert state["model"]["current"] == "model-b"

    def test_on_cooldown_start_sets_active(self, tmp_path: Path) -> None:
        writer = self._make_writer(tmp_path)
        task = _make_task(tmp_path, "T01", "Setup")
        writer.on_task_start(task)
        writer.on_cooldown_start("T01", wait_count=1)
        state = read_monitor_state(tmp_path)
        assert state is not None
        assert state["cooldown"]["active"] is True
        assert state["cooldown"]["wait_count"] == 1
        assert state["status"] == RUN_STATUS_COOLDOWN

    def test_on_cooldown_end_clears_active(self, tmp_path: Path) -> None:
        writer = self._make_writer(tmp_path)
        task = _make_task(tmp_path, "T01", "Setup")
        writer.on_task_start(task)
        writer.on_cooldown_start("T01", wait_count=1)
        writer.on_cooldown_end()
        state = read_monitor_state(tmp_path)
        assert state is not None
        assert state["cooldown"]["active"] is False
        assert state["status"] == RUN_STATUS_RUNNING

    def test_on_replan_marks_replanned(self, tmp_path: Path) -> None:
        writer = self._make_writer(tmp_path)
        task = _make_task(tmp_path, "T01", "Setup")
        writer.on_task_start(task)
        writer.on_replan("T01")
        state = read_monitor_state(tmp_path)
        assert state is not None
        task_entry = next(t for t in state["tasks"] if t["id"] == "T01")
        assert task_entry["replanned"] is True

    def test_on_run_done_success(self, tmp_path: Path) -> None:
        writer = self._make_writer(tmp_path)
        task = _make_task(tmp_path, "T01", "Setup")
        writer.on_task_start(task)
        writer.on_run_done(success=True)
        state = read_monitor_state(tmp_path)
        assert state is not None
        assert state["status"] == RUN_STATUS_DONE

    def test_on_run_done_failure(self, tmp_path: Path) -> None:
        writer = self._make_writer(tmp_path)
        task = _make_task(tmp_path, "T01", "Setup")
        writer.on_task_start(task)
        writer.on_run_done(success=False)
        state = read_monitor_state(tmp_path)
        assert state is not None
        assert state["status"] == RUN_STATUS_FAILED

    def test_on_graceful_stop_requested(self, tmp_path: Path) -> None:
        writer = self._make_writer(tmp_path)
        task = _make_task(tmp_path, "T01", "Setup")
        writer.on_task_start(task)
        writer.on_graceful_stop_requested()
        state = read_monitor_state(tmp_path)
        assert state is not None
        assert state["status"] == RUN_STATUS_STOPPING
        assert state["graceful_stop_requested"] is True

    def test_on_killed(self, tmp_path: Path) -> None:
        writer = self._make_writer(tmp_path)
        task = _make_task(tmp_path, "T01", "Setup")
        writer.on_task_start(task)
        writer.on_killed()
        state = read_monitor_state(tmp_path)
        assert state is not None
        assert state["status"] == RUN_STATUS_KILLED

    def test_multiple_tasks_tracked(self, tmp_path: Path) -> None:
        tasks = [
            _make_task(tmp_path, "T01", "Setup"),
            _make_task(tmp_path, "T02", "Build"),
            _make_task(tmp_path, "T03", "Test"),
        ]
        writer = MonitorStateWriter(project_dir=tmp_path, tasks=tasks, max_retries=3)
        writer.on_task_start(tasks[0])
        state = read_monitor_state(tmp_path)
        assert state is not None
        assert state["total_tasks"] == 3
        assert len(state["tasks"]) == 3

    def test_session_token_accumulation(self, tmp_path: Path) -> None:
        tasks = [_make_task(tmp_path, "T01", "Setup"), _make_task(tmp_path, "T02", "Build")]
        writer = MonitorStateWriter(project_dir=tmp_path, tasks=tasks, max_retries=3)
        writer.on_task_start(tasks[0])
        writer.on_task_done("T01", tokens=1000)
        writer.on_task_start(tasks[1])
        writer.on_task_done("T02", tokens=2000)
        state = read_monitor_state(tmp_path)
        assert state is not None
        assert state["tokens"]["session_total"] == 3000
        assert state["tokens"]["last_attempt"] == 2000

    def test_free_rotator_info_included(self, tmp_path: Path) -> None:
        mock_rotator = MagicMock()
        mock_rotator.remaining_count = 5
        task = _make_task(tmp_path, "T01", "Setup")
        writer = MonitorStateWriter(
            project_dir=tmp_path, tasks=[task], free_rotator=mock_rotator, max_retries=3
        )
        writer.on_task_start(task)
        state = read_monitor_state(tmp_path)
        assert state is not None
        assert state["model"]["free_mode"] is True
        assert state["model"]["free_remaining"] == 5

    def test_circuit_state_change(self, tmp_path: Path) -> None:
        writer = self._make_writer(tmp_path)
        task = _make_task(tmp_path, "T01", "Setup")
        writer.on_task_start(task)
        writer.on_circuit_state_change(
            state="OPEN",
            no_progress=3,
            same_error=0,
            no_progress_threshold=3,
            same_error_threshold=3,
        )
        state = read_monitor_state(tmp_path)
        assert state is not None
        assert state["circuit_breaker"]["state"] == "OPEN"
        assert state["circuit_breaker"]["no_progress_count"] == 3


# ---------------------------------------------------------------------------
# Planning state
# ---------------------------------------------------------------------------


class TestPlanningState:
    def test_write_planning_state_creates_file(self, tmp_path: Path) -> None:
        write_planning_state(tmp_path, goal="Build a REST API")
        state = read_monitor_state(tmp_path)
        assert state is not None
        assert state["status"] == RUN_STATUS_PLANNING
        assert state["goal"] == "Build a REST API"
        assert state["project_name"] == tmp_path.name

    def test_write_planning_state_no_goal(self, tmp_path: Path) -> None:
        write_planning_state(tmp_path)
        state = read_monitor_state(tmp_path)
        assert state is not None
        assert state["status"] == RUN_STATUS_PLANNING
        assert state.get("goal") == ""

    def test_write_planning_state_updates_goal(self, tmp_path: Path) -> None:
        write_planning_state(tmp_path, goal="")
        write_planning_state(tmp_path, goal="Build a REST API")
        state = read_monitor_state(tmp_path)
        assert state is not None
        assert state["goal"] == "Build a REST API"


# ---------------------------------------------------------------------------
# CLI — architect monitor subcommand
# ---------------------------------------------------------------------------


class TestMonitorCommand:
    def test_monitor_in_help(self, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "monitor" in result.output

    def test_monitor_opens_tui_screen(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        with patch("the_architect.tui.screens.run_monitor_screen") as mock_screen:
            result = cli_runner.invoke(main, ["monitor", "-p", str(tmp_path)])
        mock_screen.assert_called_once()
        assert result.exit_code == 0, result.output

    def test_monitor_tui_failure_exits_nonzero(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        with patch(
            "the_architect.tui.screens.run_monitor_screen",
            side_effect=RuntimeError("screen broken"),
        ):
            result = cli_runner.invoke(main, ["monitor", "-p", str(tmp_path)])
        assert result.exit_code == 1
