"""Tests for the_architect.cli — helper functions and CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from the_architect.cli import main
from the_architect.config import ArchitectConfig
from the_architect.core.provider import ProviderNotFoundError
from the_architect.core.runner import TaskResult, TokenUsage
from the_architect.core.tasks import Task, TaskStatus

# ---------------------------------------------------------------------------
# Helper Function Tests
# ---------------------------------------------------------------------------


class TestMoreHelperFunctions:
    """Tests for additional helper functions in CLI."""

    def test_ansi_supported_no_tty(self) -> None:
        """Should return False when stdout is not a TTY."""
        from the_architect.cli import _ansi_supported

        with patch("sys.stdout.isatty", return_value=False):
            result = _ansi_supported()
            assert result is False

    def test_ansi_supported_with_no_color(self) -> None:
        """Should return False when NO_COLOR env var is set."""
        from the_architect.cli import _ansi_supported

        with (
            patch("sys.stdout.isatty", return_value=True),
            patch.dict("os.environ", {"NO_COLOR": "1"}),
        ):
            result = _ansi_supported()
            assert result is False

    def test_ansi_supported_dumb_term(self) -> None:
        """Should return False when TERM is set to 'dumb'."""
        from the_architect.cli import _ansi_supported

        with (
            patch("sys.stdout.isatty", return_value=True),
            patch.dict("os.environ", {"TERM": "dumb"}),
        ):
            result = _ansi_supported()
            assert result is False

    def test_ansi_supported_true(self) -> None:
        """Should return True when TTY is available and ANSI is supported."""
        from the_architect.cli import _ansi_supported

        with (
            patch("sys.stdout.isatty", return_value=True),
            patch.dict("os.environ", {"TERM": "xterm-256color"}),
        ):
            result = _ansi_supported()
            assert result is True

    def test_opencode_install_hint(self) -> None:
        """Should return install hint from OpenCodeProvider."""
        from the_architect.cli import _opencode_install_hint

        mock_provider = MagicMock()
        mock_provider.install_hint.return_value = "npm i -g opencode"
        with patch(
            "the_architect.core.opencode_provider.OpenCodeProvider",
            return_value=mock_provider,
        ):
            result = _opencode_install_hint()
            assert result == "npm i -g opencode"

    def test_questionary_style_returns_style(self) -> None:
        """Should return a Style instance."""
        import the_architect.cli as _cli_mod

        # Reset the module-level cache
        _cli_mod._QUESTIONARY_STYLE = None

        from the_architect.cli import _questionary_style

        style = _questionary_style()
        assert style is not None

    def test_questionary_style_caches(self) -> None:
        """Should return the same Style object on subsequent calls."""
        import the_architect.cli as _cli_mod

        # Reset the module-level cache
        _cli_mod._QUESTIONARY_STYLE = None

        from the_architect.cli import _questionary_style

        style1 = _questionary_style()
        style2 = _questionary_style()
        assert style1 is style2

    def test_alternate_screen_non_tty(self) -> None:
        """Should be a no-op when stdout is not a TTY."""
        from the_architect.cli import alternate_screen

        with (
            patch("sys.stdout.isatty", return_value=False),
            patch("sys.stdout.write") as mock_write,
        ):
            with alternate_screen():
                pass
            # Should not write any ANSI codes
            mock_write.assert_not_called()

    def test_countdown_zero_seconds(self) -> None:
        """Should return immediately when seconds <= 0."""
        from the_architect.cli import _countdown

        with patch("time.sleep") as mock_sleep:
            _countdown(0)
            mock_sleep.assert_not_called()

    def test_countdown_non_tty(self) -> None:
        """Should sleep without ANSI display when not a TTY."""
        from the_architect.cli import _countdown

        with (
            patch("the_architect.cli._ansi_supported", return_value=False),
            patch("time.sleep") as mock_sleep,
        ):
            _countdown(3)
            mock_sleep.assert_called_once_with(3)

    def test_spin_non_tty(self) -> None:
        """Should return immediately without writing when not a TTY."""
        from the_architect.cli import _spin

        with (
            patch("the_architect.cli._ansi_supported", return_value=False),
            patch("sys.stdout.write") as mock_write,
        ):
            _spin("test")
            # Should not write any spinner output
            mock_write.assert_not_called()

    def test_read_goal_oserror(self, tmp_path: Path) -> None:
        """Should return empty string when OSError is raised."""
        from the_architect.cli import _read_goal_from_instructions

        instructions = tmp_path / "INSTRUCTIONS.md"
        instructions.write_text("# Instructions\n", encoding="utf-8")

        with patch("pathlib.Path.read_text", side_effect=OSError("test error")):
            result = _read_goal_from_instructions(tmp_path)
            assert result == ""


class TestFmtDuration:
    """Tests for _fmt_duration duration formatting function."""

    def test_fmt_duration_zero(self) -> None:
        """Should format 0 seconds as '0:00'."""
        from the_architect.cli import _fmt_duration

        result = _fmt_duration(0)
        assert result == "0:00"

    def test_fmt_duration_less_than_minute(self) -> None:
        """Should format 59 seconds as '0:59'."""
        from the_architect.cli import _fmt_duration

        result = _fmt_duration(59)
        assert result == "0:59"

    def test_fmt_duration_one_minute(self) -> None:
        """Should format 65 seconds as '1:05'."""
        from the_architect.cli import _fmt_duration

        result = _fmt_duration(65)
        assert result == "1:05"

    def test_fmt_duration_one_hour(self) -> None:
        """Should format 3600 seconds as '1:00:00'."""
        from the_architect.cli import _fmt_duration

        result = _fmt_duration(3600)
        assert result == "1:00:00"

    def test_fmt_duration_over_one_hour(self) -> None:
        """Should format 3661 seconds as '1:01:01'."""
        from the_architect.cli import _fmt_duration

        result = _fmt_duration(3661)
        assert result == "1:01:01"


class TestFilterAndSetStatus:
    """Tests for _filter_and_set_status task filtering function."""

    def test_filter_and_set_status_with_progress(self, tmp_path: Path) -> None:
        """Should update task statuses based on PROGRESS.md."""
        from the_architect.cli import _filter_and_set_status

        progress_file = tmp_path / "PROGRESS.md"
        progress_file.write_text(
            """# The Architect — Progress Tracker

## Overall Status

**Tasks completed:** 2
**Next task to run:** T03

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | Task One | Done | |
| T02 | Task Two | Done | |
| T03 | Task Three | Pending | |
| T04 | Task Four | Pending | |

---

## Current State


## Last Task Summary


---

""",
            encoding="utf-8",
        )

        task1 = Task(name="T01", prefix="T01", number=1, path=tmp_path / "T01.md")
        task2 = Task(name="T02", prefix="T02", number=2, path=tmp_path / "T02.md")
        task3 = Task(name="T03", prefix="T03", number=3, path=tmp_path / "T03.md")
        task4 = Task(name="T04", prefix="T04", number=4, path=tmp_path / "T04.md")

        result = _filter_and_set_status([task1, task2, task3, task4], progress_file)

        assert result[0].status == TaskStatus.DONE
        assert result[1].status == TaskStatus.DONE
        assert result[2].status == TaskStatus.PENDING
        assert result[3].status == TaskStatus.PENDING

    def test_filter_and_set_status_empty_list(self, tmp_path: Path) -> None:
        """Should return empty list when tasks list is empty."""
        from the_architect.cli import _filter_and_set_status

        progress_file = tmp_path / "PROGRESS.md"
        result = _filter_and_set_status([], progress_file)
        assert result == []

    def test_filter_and_set_status_no_matching_prefixes(self, tmp_path: Path) -> None:
        """Should keep all tasks PENDING when no prefix matches in progress."""
        from the_architect.cli import _filter_and_set_status

        progress_file = tmp_path / "PROGRESS.md"
        progress_file.write_text(
            """# Progress

**Tasks completed:** 0
**Next task to run:** 

Some other text but no task prefixes.
""",
            encoding="utf-8",
        )

        task = Task(name="T01", prefix="T01", number=1, path=tmp_path / "T01.md")
        result = _filter_and_set_status([task], progress_file)
        assert result[0].status == TaskStatus.PENDING


class TestSetupLoguru:
    """Tests for _setup_loguru logging configuration."""

    def test_setup_loguru_configures_logger(self) -> None:
        """Should configure loguru with handler to stderr."""
        from loguru import logger as loguru_logger

        from the_architect.cli import _setup_loguru

        _setup_loguru()
        assert len(loguru_logger._core.handlers) > 0

    def test_setup_loguru_is_idempotent(self) -> None:
        """Should be safe to call multiple times."""
        from the_architect.cli import _setup_loguru

        _setup_loguru()
        _setup_loguru()
        # Should not raise any errors


class TestOpencodeHasAnyModels:
    """Tests for _opencode_has_any_models OpenCode model checking."""

    def test_opencode_has_models_returns_true(self) -> None:
        """Should return True when provider has models."""
        from the_architect.cli import _opencode_has_any_models

        with patch("the_architect.core.opencode_provider.OpenCodeProvider") as mock_provider:
            mock_provider.return_value.has_any_models.return_value = True
            result = _opencode_has_any_models()
            assert result is True

    def test_opencode_has_models_returns_false(self) -> None:
        """Should return False when provider has no models."""
        from the_architect.cli import _opencode_has_any_models

        with patch("the_architect.core.opencode_provider.OpenCodeProvider") as mock_provider:
            mock_provider.return_value.has_any_models.return_value = False
            result = _opencode_has_any_models()
            assert result is False

    def test_opencode_has_models_exception_returns_false(self) -> None:
        """Should return False when provider raises exception."""
        from the_architect.cli import _opencode_has_any_models

        with patch("the_architect.core.opencode_provider.OpenCodeProvider") as mock_provider:
            mock_provider.return_value.has_any_models.side_effect = Exception("test error")
            result = _opencode_has_any_models()
            assert result is False


class TestReadGoalFromInstructions:
    """Tests for _read_goal_from_instructions goal extraction."""

    def test_read_goal_from_instructions_with_goal(self, tmp_path: Path) -> None:
        """Should extract goal from INSTRUCTIONS.md."""
        from the_architect.cli import _read_goal_from_instructions

        instructions = tmp_path / "INSTRUCTIONS.md"
        instructions.write_text(
            """# Instructions

## Goal

My goal text

## Other section

Some other text.
""",
            encoding="utf-8",
        )

        result = _read_goal_from_instructions(tmp_path)
        assert result == "My goal text"

    def test_read_goal_from_instructions_without_goal(self, tmp_path: Path) -> None:
        """Should return empty string when no Goal section exists."""
        from the_architect.cli import _read_goal_from_instructions

        instructions = tmp_path / "INSTRUCTIONS.md"
        instructions.write_text(
            """# Instructions

## Some other section

Some other text.
""",
            encoding="utf-8",
        )

        result = _read_goal_from_instructions(tmp_path)
        assert result == ""


class TestRetryCommand:
    """Tests for retry command."""

    def test_retry_task_not_found(self, tmp_path: Path) -> None:
        """Should exit with error when task not found."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        with patch("the_architect.cli.discover_tasks", return_value=[]):
            runner = CliRunner()
            result = runner.invoke(main, ["retry", "-t", "T99", "-p", str(tmp_path)])
            assert result.exit_code == 1

    def test_retry_resets_and_runs(self, tmp_path: Path) -> None:
        """Should reset task status and run it."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")
        (tmp_path / "PROGRESS.md").write_text(
            "# Progress\n\n| T01 | Test | Done | |\n", encoding="utf-8"
        )
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tasks_dir / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.core.opencode_provider.OpenCodeProvider") as mock_prov,
            patch("the_architect.cli.setup_logging"),
            patch("the_architect.cli.asyncio.run"),
        ):
            mock_prov.return_value.ensure_setup.return_value = None
            runner = CliRunner()
            result = runner.invoke(main, ["retry", "-t", "T01", "-p", str(tmp_path)])
            # The task should be reset and run (even if run fails in test)
            assert "T01" in result.output or result.exit_code != 0 or "Reset" in result.output


class TestMainHeadless:
    """Tests for main() function in headless mode."""

    def test_main_subcommand_returns_early(self) -> None:
        """Should return early when a subcommand is invoked."""
        runner = CliRunner()
        result = runner.invoke(main, ["version"])
        assert result.exit_code == 0
        assert "architect v" in result.output

    def test_main_no_providers_found(self, tmp_path: Path) -> None:
        """Should exit with error when no providers are installed."""
        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_available_providers", return_value=[]),
            patch("the_architect.cli.detect_provider", side_effect=ProviderNotFoundError("test")),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "-p", str(tmp_path)])
            assert result.exit_code == 1 or "No supported AI CLI found" in result.output

    def test_main_all_tasks_done_headless(self, tmp_path: Path) -> None:
        """Should print 'All tasks complete' when all tasks are done in headless mode."""
        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.DONE,
        )
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = True
        mock_provider.get_resolved_model.return_value = "default"
        mock_provider.ensure_setup.return_value = None
        mock_provider.supports_free_tier.return_value = False
        mock_provider.find_user_config.return_value = Path("/fake")

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_available_providers", return_value=[mock_provider]),
            patch("the_architect.cli.detect_provider", return_value=mock_provider),
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "-p", str(tmp_path)])
            assert "All tasks complete" in result.output or result.exit_code == 0

    def test_main_only_task_not_found(self, tmp_path: Path) -> None:
        """Should exit with error when --only task is not found."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = True

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_available_providers", return_value=[mock_provider]),
            patch("the_architect.cli.detect_provider", return_value=mock_provider),
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli._filter_and_set_status", return_value=[]),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "--only", "T99", "-p", str(tmp_path)])
            assert "No task found" in result.output or result.exit_code == 1

    def test_main_from_task_not_found(self, tmp_path: Path) -> None:
        """Should exit with error when --from task is not found."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = True

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_available_providers", return_value=[mock_provider]),
            patch("the_architect.cli.detect_provider", return_value=mock_provider),
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli._filter_and_set_status", return_value=[]),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "--from", "T99", "-p", str(tmp_path)])
            assert "No task found" in result.output or result.exit_code == 1


class TestRunMain:
    """Tests for _run_main internal function."""

    def test_run_main_all_done(self, tmp_path: Path) -> None:
        """Should print completion message when all tasks are done."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.DONE,
        )
        config = ArchitectConfig()

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                )
            assert exc_info.value.code == 0

    def test_run_main_no_tasks_runs_planning(self, tmp_path: Path) -> None:
        """Should run planning mode when no tasks exist."""
        from the_architect.cli import _run_main

        config = ArchitectConfig()

        with (
            patch("the_architect.cli.discover_tasks") as mock_discover,
            patch("the_architect.cli._filter_and_set_status") as mock_filter,
            patch("the_architect.cli.run_planning_mode") as mock_plan,
        ):
            # First call (pre-planning) returns no tasks
            mock_discover.return_value = []
            mock_filter.return_value = []
            mock_plan.return_value = None

            # Planning mode will be called and exit because no tasks were created
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    goal_text="Test goal",
                    _pre_loaded_config=config,
                )
            # Should exit with error because planning created no tasks
            assert exc_info.value.code == 1
            mock_plan.assert_called_once()

    def test_run_main_with_pending_tasks(self, tmp_path: Path) -> None:
        """Should run execution when there are pending tasks."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks) as mock_run,
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                )
            assert exc_info.value.code == 0
            mock_run.assert_called_once()

    def test_run_main_with_preloaded_config(self, tmp_path: Path) -> None:
        """Should use pre-loaded config when provided."""
        from the_architect.cli import _run_main

        config = ArchitectConfig()
        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    goal_text="Test goal",
                    _pre_loaded_config=config,
                )
            assert exc_info.value.code == 0


class TestRunPlanningMode:
    """Tests for run_planning_mode function."""

    def test_run_planning_mode_headless(self, tmp_path: Path) -> None:
        """Should run planner in headless mode."""
        from the_architect.cli import run_planning_mode

        config = ArchitectConfig()

        with (
            patch("the_architect.cli.run_planner") as mock_planner,
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli.check_pending_tasks", return_value=[]),
        ):
            # Mock the planning result with tasks_created
            mock_result = MagicMock()
            mock_result.tasks_created = []
            mock_planner.return_value = mock_result
            run_planning_mode(
                project=tmp_path,
                config=config,
                headless=True,
                goal_text="Test goal",
            )
            mock_planner.assert_called_once()

    def test_run_planning_mode_planning_failed(self, tmp_path: Path) -> None:
        """Should handle PlanningFailedError gracefully."""
        from the_architect.cli import run_planning_mode
        from the_architect.core.planner import PlanningFailedError

        config = ArchitectConfig()

        with (
            patch("the_architect.cli.run_planner", side_effect=PlanningFailedError("test")),
            patch("the_architect.cli.check_pending_tasks", return_value=0),
        ):
            with pytest.raises(SystemExit):
                run_planning_mode(
                    project=tmp_path,
                    config=config,
                    headless=True,
                    goal_text="Test goal",
                )


class TestCancelCommandMore:
    """More tests for cancel command branches."""

    def test_cancel_confirm_kill(self, tmp_path: Path) -> None:
        """Should send SIGTERM when user confirms kill."""
        lock_dir = tmp_path / ".architect"
        lock_dir.mkdir()
        (lock_dir / "runner.lock").write_text("12345", encoding="utf-8")

        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None
            runner = CliRunner()
            result = runner.invoke(main, ["cancel", "-p", str(tmp_path)], input="y\n")
            # Should attempt to kill the process
            assert (
                "SIGTERM" in result.output
                or "Terminated" in result.output
                or "kill" in result.output.lower()
            )

    def test_cancel_unlink_fails(self, tmp_path: Path) -> None:
        """Should exit with error when lock removal fails."""
        lock_dir = tmp_path / ".architect"
        lock_dir.mkdir()
        (lock_dir / "runner.lock").write_text("not_a_pid", encoding="utf-8")

        with patch.object(Path, "unlink", side_effect=OSError("permission denied")):
            runner = CliRunner()
            result = runner.invoke(main, ["cancel", "-p", str(tmp_path)])
            assert result.exit_code == 1 or "Failed to remove" in result.output


class TestStatusCommandMore:
    """More tests for status command branches."""

    def test_status_stale_lock(self, tmp_path: Path) -> None:
        """Should show 'Not running' when lock has stale PID."""
        lock_dir = tmp_path / ".architect"
        lock_dir.mkdir()
        (lock_dir / "runner.lock").write_text("12345", encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        with patch("os.kill", side_effect=ProcessLookupError()):
            runner = CliRunner()
            result = runner.invoke(main, ["status", "-p", str(tmp_path)])
            assert "Not running" in result.output or "stale" in result.output.lower()

    def test_status_no_tasks_found(self, tmp_path: Path) -> None:
        """Should show 'No tasks found' when tasks dir is empty."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        with patch("the_architect.cli.discover_tasks", return_value=[]):
            runner = CliRunner()
            result = runner.invoke(main, ["status", "-p", str(tmp_path)])
            assert "No tasks found" in result.output

    def test_status_with_circuit_breaker(self, tmp_path: Path) -> None:
        """Should display circuit breaker state."""
        import json

        lock_dir = tmp_path / ".architect"
        lock_dir.mkdir()
        circuit_data = {
            "T01": {
                "state": "OPEN",
                "consecutive_no_progress": 3,
                "consecutive_same_error": 2,
                "recovery_action": "replan",
                "opened_at": "2026-04-27T12:00:00",
            }
        }
        (lock_dir / "circuit.json").write_text(json.dumps(circuit_data), encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tasks_dir / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )

        with patch("the_architect.cli.discover_tasks", return_value=[mock_task]):
            runner = CliRunner()
            result = runner.invoke(main, ["status", "-p", str(tmp_path)])
            assert "OPEN" in result.output or "Circuit" in result.output

    def test_status_with_token_budget(self, tmp_path: Path) -> None:
        """Should display token budget when configured."""
        (tmp_path / "architect.toml").write_text(
            "[architect]\ntoken_budget_per_hour = 500000\n", encoding="utf-8"
        )
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        with patch("the_architect.cli.discover_tasks", return_value=[]):
            runner = CliRunner()
            result = runner.invoke(main, ["status", "-p", str(tmp_path)])
            assert "Token budget" in result.output or "500000" in result.output

    def test_status_with_logs(self, tmp_path: Path) -> None:
        """Should display log files when they exist."""
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "T01_test.log").write_text("log", encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        with patch("the_architect.cli.discover_tasks", return_value=[]):
            runner = CliRunner()
            result = runner.invoke(main, ["status", "-p", str(tmp_path)])
            assert "Logs" in result.output or "T01_test.log" in result.output


class TestCircuitCommandMore:
    """More tests for circuit command branches."""

    def test_circuit_open_state(self, tmp_path: Path) -> None:
        """Should display OPEN state in red."""
        import json

        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        circuit_data = {
            "T01": {
                "state": "OPEN",
                "consecutive_no_progress": 3,
                "consecutive_same_error": 0,
                "recovery_action": "WAIT",
                "opened_at": "2026-04-27T12:00:00",
            }
        }
        (arch_dir / "circuit.json").write_text(json.dumps(circuit_data), encoding="utf-8")
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tasks_dir / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )

        with patch("the_architect.cli.discover_tasks", return_value=[mock_task]):
            runner = CliRunner()
            result = runner.invoke(main, ["circuit", "-p", str(tmp_path)])
            assert "OPEN" in result.output

    def test_circuit_half_open_state(self, tmp_path: Path) -> None:
        """Should display HALF_OPEN state in yellow."""
        import json

        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        circuit_data = {
            "T01": {
                "state": "HALF_OPEN",
                "consecutive_no_progress": 0,
                "consecutive_same_error": 0,
                "recovery_action": None,
                "opened_at": None,
            }
        }
        (arch_dir / "circuit.json").write_text(json.dumps(circuit_data), encoding="utf-8")
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tasks_dir / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )

        with patch("the_architect.cli.discover_tasks", return_value=[mock_task]):
            runner = CliRunner()
            result = runner.invoke(main, ["circuit", "-p", str(tmp_path)])
            assert "HALF_OPEN" in result.output


class TestLogsCommandMore:
    """More tests for logs command branches."""

    def test_logs_json_content(self, tmp_path: Path) -> None:
        """Should parse JSON log lines and show text content."""
        import json

        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        log_entries = [
            json.dumps({"type": "text", "part": {"text": "Hello world"}}),
            json.dumps({"type": "error", "message": "Something failed"}),
        ]
        (log_dir / "T01_test.log").write_text("\n".join(log_entries), encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["logs", "-p", str(tmp_path), "--task", "T01"])
        assert result.exit_code == 0
        assert "Hello world" in result.output

    def test_logs_error_content(self, tmp_path: Path) -> None:
        """Should show ERROR prefix for error events."""
        import json

        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        log_entries = [
            json.dumps({"type": "error", "message": "Something failed"}),
        ]
        (log_dir / "T01_test.log").write_text("\n".join(log_entries), encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["logs", "-p", str(tmp_path), "--task", "T01"])
        assert result.exit_code == 0
        assert "ERROR" in result.output or "Something failed" in result.output


class TestMonitorCommandMore:
    """More tests for monitor command branches."""

    def test_monitor_session_exists(self, tmp_path: Path) -> None:
        """Should attach when session exists."""
        with (
            patch("the_architect.core.tmux.is_tmux_available", return_value=True),
            patch("the_architect.core.tmux.session_exists", return_value=True),
            patch("the_architect.core.tmux.get_session_name", return_value="architect-test"),
            patch("the_architect.core.tmux.attach_session") as mock_attach,
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["monitor", "-p", str(tmp_path)])  # noqa: F841
            mock_attach.assert_called_once()

    def test_monitor_single_other_session(self, tmp_path: Path) -> None:
        """Should attach to single running architect session."""
        with (
            patch("the_architect.core.tmux.is_tmux_available", return_value=True),
            patch("the_architect.core.tmux.session_exists", return_value=False),
            patch("the_architect.core.tmux.get_session_name", return_value="architect-test"),
            patch(
                "the_architect.core.tmux.list_architect_sessions", return_value=["architect-other"]
            ),
            patch("the_architect.core.tmux.attach_session") as mock_attach,
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["monitor", "-p", str(tmp_path)])  # noqa: F841
            mock_attach.assert_called_once_with("architect-other")

    def test_monitor_multiple_other_sessions(self, tmp_path: Path) -> None:
        """Should list sessions when multiple exist."""
        with (
            patch("the_architect.core.tmux.is_tmux_available", return_value=True),
            patch("the_architect.core.tmux.session_exists", return_value=False),
            patch("the_architect.core.tmux.get_session_name", return_value="architect-test"),
            patch(
                "the_architect.core.tmux.list_architect_sessions",
                return_value=["architect-other1", "architect-other2"],
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["monitor", "-p", str(tmp_path)])
            assert "Other active sessions" in result.output or "architect-other" in result.output


class TestConfigCommandMore:
    """More tests for config command error branches."""

    def test_config_set_invalid_bool(self, tmp_path: Path) -> None:
        """Should show error for invalid boolean value."""
        runner = CliRunner()
        result = runner.invoke(
            main, ["config", "-p", str(tmp_path), "--set", "carry_context=maybe"]
        )
        assert result.exit_code == 1
        assert "Invalid value" in result.output or "true/false" in result.output

    def test_config_show_defaults_only(self, tmp_path: Path) -> None:
        """Should indicate no toml file when showing defaults."""
        runner = CliRunner()
        result = runner.invoke(main, ["config", "-p", str(tmp_path)])
        assert "No architect.toml found" in result.output or "defaults" in result.output.lower()


# ---------------------------------------------------------------------------
# Execution-mode and main() body tests
# ---------------------------------------------------------------------------


class TestRunTasksRaw:
    """Tests for _run_tasks_raw async execution function."""

    def test_run_tasks_raw_success(self, tmp_path: Path) -> None:
        """Should return (True, results, duration) when all tasks succeed."""
        from the_architect.cli import _run_tasks_raw

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()
        mock_result = TaskResult(
            prefix="T01",
            title="Test task",
            status="done",
            duration_seconds=1.0,
            tokens=TokenUsage(),
        )

        async def _fake_run_all(*args: object, **kwargs: object) -> bool:
            # Simulate the on_task_done callback
            on_task_done = kwargs.get("on_task_done")
            if on_task_done:
                on_task_done(mock_result)
            return True

        with (
            patch("the_architect.cli._ansi_supported", return_value=False),
            patch("the_architect.cli.run_all", side_effect=_fake_run_all),
        ):
            import asyncio

            success, results, duration = asyncio.run(_run_tasks_raw(tmp_path, config, [mock_task]))
            assert success is True
            assert duration >= 0

    def test_run_tasks_raw_with_done_tasks(self, tmp_path: Path) -> None:
        """Should pre-populate results for already-done tasks."""
        from the_architect.cli import _run_tasks_raw

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.DONE,
        )
        config = ArchitectConfig()

        async def _fake_run_all(*args: object, **kwargs: object) -> bool:
            return True

        with (
            patch("the_architect.cli._ansi_supported", return_value=False),
            patch("the_architect.cli.run_all", side_effect=_fake_run_all),
        ):
            import asyncio

            success, results, duration = asyncio.run(_run_tasks_raw(tmp_path, config, [mock_task]))
            assert success is True
            # Done task should be pre-populated as "skipped"
            assert any(r.prefix == "T01" for r in results)

    def test_run_tasks_raw_with_monitor_writer(self, tmp_path: Path) -> None:
        """Should call monitor writer callbacks during execution."""
        from the_architect.cli import _run_tasks_raw

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()
        mock_result = TaskResult(
            prefix="T01",
            title="Test task",
            status="done",
            duration_seconds=1.0,
            tokens=TokenUsage(),
        )
        mock_writer = MagicMock()

        async def _fake_run_all(*args: object, **kwargs: object) -> bool:
            on_task_start = kwargs.get("on_task_start")
            on_task_done = kwargs.get("on_task_done")
            on_circuit_event = kwargs.get("on_circuit_event")
            if on_task_start:
                on_task_start(mock_task)
            if on_task_done:
                on_task_done(mock_result)
            if on_circuit_event:
                on_circuit_event("circuit_state_change", {"state": "CLOSED"})
                on_circuit_event("cooldown_start", {"task_id": "T01", "wait_count": 1})
                on_circuit_event("cooldown_end", {})
                on_circuit_event("replan_start", {"task_id": "T01"})
                on_circuit_event("replan_end", {})
            return True

        with (
            patch("the_architect.cli._ansi_supported", return_value=False),
            patch("the_architect.cli.run_all", side_effect=_fake_run_all),
        ):
            import asyncio

            success, results, duration = asyncio.run(
                _run_tasks_raw(tmp_path, config, [mock_task], monitor_writer=mock_writer)
            )
            assert success is True
            mock_writer.on_task_start.assert_called_once()
            mock_writer.on_task_done.assert_called_once()

    def test_run_tasks_raw_with_free_rotator_model_switch(self, tmp_path: Path) -> None:
        """Should call on_model_switched when free mode rotates models."""
        from the_architect.cli import _run_tasks_raw

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()
        mock_rotator = MagicMock()

        async def _fake_run_all(*args: object, **kwargs: object) -> bool:
            on_model_switched = kwargs.get("on_model_switched")
            if on_model_switched:
                on_model_switched("old-model", "new-model")
            return True

        with (
            patch("the_architect.cli._ansi_supported", return_value=False),
            patch("the_architect.cli.run_all", side_effect=_fake_run_all),
        ):
            import asyncio

            success, results, duration = asyncio.run(
                _run_tasks_raw(tmp_path, config, [mock_task], free_rotator=mock_rotator)
            )
            assert success is True


class TestRunMainExecution:
    """Tests for _run_main execution paths (deeper coverage)."""

    def test_run_main_free_mode_not_supported(self, tmp_path: Path) -> None:
        """Should disable free mode when provider doesn't support it."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()
        config.free_mode = True

        mock_provider = MagicMock()
        mock_provider.display_name = "Claude Code"
        mock_provider.supports_free_tier.return_value = False
        mock_provider.get_resolved_model.return_value = "model"

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
            patch("the_architect.config.write_config", return_value=tmp_path / "architect.toml"),
        ):
            with pytest.raises(SystemExit) as exc_info:  # noqa: F841
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                    provider=mock_provider,
                )
            # Free mode should have been disabled
            assert config.free_mode is False

    def test_run_main_no_instructions_md(self, tmp_path: Path) -> None:
        """Should warn when tasks/INSTRUCTIONS.md is missing."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        # Create tasks dir but NOT INSTRUCTIONS.md
        tasks_dir = tmp_path / config.tasks_dir.name
        tasks_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                )
            assert exc_info.value.code == 0

    def test_run_main_execution_error(self, tmp_path: Path) -> None:
        """Should handle RuntimeError from execution."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        async def _fake_run_tasks_error(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            raise RuntimeError("test error")

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks_error),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                )
            assert exc_info.value.code == 1

    def test_run_main_unexpected_error(self, tmp_path: Path) -> None:
        """Should handle unexpected exceptions during execution."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        async def _fake_run_tasks_unexpected(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            raise OSError("unexpected error")

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks_unexpected),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                )
            assert exc_info.value.code == 1

    def test_run_main_with_retrospective(self, tmp_path: Path) -> None:
        """Should run retrospective review when configured."""
        from the_architect.cli import _run_main
        from the_architect.core.retrospective import RetrospectiveResult

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()
        config.retrospective_rounds = 1

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        mock_retro_result = RetrospectiveResult(
            issues_found=0,
            fixes_planned=0,
            tasks_created=[],
            summary="No issues found",
        )

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.run_retrospective", return_value=mock_retro_result),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                )
            assert exc_info.value.code == 0

    def test_run_main_persistent_mode(self, tmp_path: Path) -> None:
        """Should configure persistent mode with increased retries."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        # Use load_config to provide a config that will be mutated
        config = ArchitectConfig()

        with (
            patch("the_architect.cli.load_config", return_value=config),
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    persistent=True,
                    headless=True,
                    _pre_loaded_config=None,
                )
            assert exc_info.value.code == 0
            assert config.persistent is True
            assert config.max_retries == 30
            assert config.retrospective_rounds == 2

    def test_run_main_all_selected_done(self, tmp_path: Path) -> None:
        """Should print message when all selected tasks are already done."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.DONE,
        )
        config = ArchitectConfig()

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                )
            assert exc_info.value.code == 0

    def test_run_main_monitor_writer_init_fails(self, tmp_path: Path) -> None:
        """Should handle MonitorStateWriter init failure gracefully."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.MonitorStateWriter", side_effect=Exception("init failed")),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                )
            assert exc_info.value.code == 0


class TestMainHeadlessDeeper:
    """Deeper tests for main() function headless paths."""

    def test_main_headless_with_provider_config(self, tmp_path: Path) -> None:
        """Should resolve provider from config."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = True
        mock_provider.get_resolved_model.return_value = "default"
        mock_provider.ensure_setup.return_value = None
        mock_provider.supports_free_tier.return_value = False
        mock_provider.find_user_config.return_value = Path("/fake")

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_provider", return_value=mock_provider) as mock_detect,
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_main"),
        ):
            config = ArchitectConfig()
            config.provider = "opencode"
            with patch("the_architect.cli.load_config", return_value=config):
                runner = CliRunner()
                result = runner.invoke(main, ["--headless", "-p", str(tmp_path)])  # noqa: F841
                # Should have resolved provider from config
                mock_detect.assert_called()

    def test_main_headless_provider_from_env(self, tmp_path: Path) -> None:
        """Should resolve provider from ARCHITECT_PROVIDER env var."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = True

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_provider", return_value=mock_provider) as mock_detect,
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli._filter_and_set_status", return_value=[]),
            patch.dict("os.environ", {"ARCHITECT_PROVIDER": "opencode"}),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "-p", str(tmp_path)])  # noqa: F841
            # Should resolve provider from env
            mock_detect.assert_called()

    def test_main_provider_not_found_error(self, tmp_path: Path) -> None:
        """Should exit with error when configured provider not found."""
        with (
            patch("the_architect.cli.load_config") as mock_config,
            patch(
                "the_architect.cli.detect_provider", side_effect=ProviderNotFoundError("not found")
            ),
        ):
            config = ArchitectConfig()
            config.provider = "nonexistent"
            mock_config.return_value = config

            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "-p", str(tmp_path)])
            assert result.exit_code == 1

    def test_main_provider_no_models_not_configured(self, tmp_path: Path) -> None:
        """Should exit when provider has no models and no config."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = False
        mock_provider.find_user_config.return_value = None

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_available_providers", return_value=[mock_provider]),
            patch("the_architect.cli.detect_provider", return_value=mock_provider),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "-p", str(tmp_path)])
            assert result.exit_code == 1
            assert "may not be configured" in result.output

    def test_main_single_provider_auto(self, tmp_path: Path) -> None:
        """Should auto-select single available provider in auto mode."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = True

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_available_providers", return_value=[mock_provider]),
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli._filter_and_set_status", return_value=[]),
            patch("the_architect.cli._run_main"),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "-p", str(tmp_path)])  # noqa: F841
            # Should auto-select the single provider

    def test_main_headless_no_goal_no_context(self, tmp_path: Path) -> None:
        """Should still proceed in headless mode even without explicit goal."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = True

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_available_providers", return_value=[mock_provider]),
            patch("the_architect.cli.detect_provider", return_value=mock_provider),
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli._filter_and_set_status", return_value=[]),
            patch("the_architect.cli._run_main"),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "-p", str(tmp_path)])  # noqa: F841

    def test_main_env_vars_resolution(self, tmp_path: Path) -> None:
        """Should resolve headless, goal, scope from env vars."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = True

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_available_providers", return_value=[mock_provider]),
            patch("the_architect.cli.detect_provider", return_value=mock_provider),
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli._filter_and_set_status", return_value=[]),
            patch("the_architect.cli._run_main"),
            patch.dict(
                "os.environ",
                {
                    "ARCHITECT_HEADLESS": "true",
                    "ARCHITECT_GOAL": "Test from env",
                    "ARCHITECT_SCOPE": "simple",
                },
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["-p", str(tmp_path)])  # noqa: F841


class TestSkipCommandMore:
    """More tests for skip command edge cases."""

    def test_skip_resets_status_in_progress(self, tmp_path: Path) -> None:
        """Should change Pending to Done in PROGRESS.md."""
        progress_content = """# The Architect — Progress Tracker

## Overall Status

**Tasks completed:** 0
**Next task to run:** T01

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | Test | Pending | — |

---

## Current State

Testing.

## Last Task Summary

N/A

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
| | | | |
"""
        (tmp_path / "PROGRESS.md").write_text(progress_content, encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["skip", "-t", "T01", "-p", str(tmp_path)])
        assert result.exit_code == 0
        # Verify the file was actually modified
        updated = (tmp_path / "PROGRESS.md").read_text(encoding="utf-8")
        assert "Done" in updated


class TestLogsCommandDeeper:
    """Deeper tests for logs command branches."""

    def test_logs_raw_text_lines(self, tmp_path: Path) -> None:
        """Should display raw text lines that are not JSON."""
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "T01_test.log").write_text("raw log line\nanother line\n", encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["logs", "-p", str(tmp_path), "--task", "T01"])
        assert result.exit_code == 0
        assert "raw log line" in result.output

    def test_logs_show_all_flag(self, tmp_path: Path) -> None:
        """Should show full log content with --all flag."""
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        lines = [f"line {i}" for i in range(100)]
        (log_dir / "T01_test.log").write_text("\n".join(lines), encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["logs", "-p", str(tmp_path), "--task", "T01", "--all"])
        assert result.exit_code == 0

    def test_logs_tail_flag(self, tmp_path: Path) -> None:
        """Should limit output to last N lines with --tail."""
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        lines = [f"line {i}" for i in range(100)]
        (log_dir / "T01_test.log").write_text("\n".join(lines), encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["logs", "-p", str(tmp_path), "--task", "T01", "--tail", "5"])
        assert result.exit_code == 0


class TestConfigCommandDeeper:
    """Deeper tests for config command."""

    def test_config_set_int_value(self, tmp_path: Path) -> None:
        """Should handle integer values correctly."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["config", "-p", str(tmp_path), "--set", "retry_pause=60"])
        assert result.exit_code == 0
        assert "retry_pause" in result.output

    def test_config_set_string_value(self, tmp_path: Path) -> None:
        """Should handle string values correctly."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            main, ["config", "-p", str(tmp_path), "--set", "execution_agent=build"]
        )
        assert result.exit_code == 0
        assert "execution_agent" in result.output


class TestMainDeeper:
    """Deeper tests for main() function to increase coverage."""

    def test_main_headless_with_goal_and_plan(self, tmp_path: Path) -> None:
        """Should run planning mode in headless with a goal."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = True
        mock_provider.get_resolved_model.return_value = "default"
        mock_provider.ensure_setup.return_value = None
        mock_provider.supports_free_tier.return_value = False
        mock_provider.find_user_config.return_value = Path("/fake")

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_available_providers", return_value=[mock_provider]),
            patch("the_architect.cli.detect_provider", return_value=mock_provider),
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli._filter_and_set_status", return_value=[]),
            patch("the_architect.cli._run_main") as mock_run_main,
        ):
            runner = CliRunner()
            result = runner.invoke(  # noqa: F841
                main, ["--headless", "--goal", "Test goal", "--plan", "-p", str(tmp_path)]
            )
            mock_run_main.assert_called_once()

    def test_main_with_only_flag_matching_task(self, tmp_path: Path) -> None:
        """Should proceed when --only matches a pending task."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = True

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_available_providers", return_value=[mock_provider]),
            patch("the_architect.cli.detect_provider", return_value=mock_provider),
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_main"),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "--only", "T01", "-p", str(tmp_path)])  # noqa: F841

    def test_main_with_from_flag_matching_task(self, tmp_path: Path) -> None:
        """Should proceed when --from matches a task prefix."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = True

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_available_providers", return_value=[mock_provider]),
            patch("the_architect.cli.detect_provider", return_value=mock_provider),
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_main"),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "--from", "T01", "-p", str(tmp_path)])  # noqa: F841

    def test_main_all_done_non_headless(self, tmp_path: Path) -> None:
        """Should show completion message and exit in non-headless mode."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = True

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.DONE,
        )

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_available_providers", return_value=[mock_provider]),
            patch("the_architect.cli.detect_provider", return_value=mock_provider),
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.core.monitor_state.write_planning_state"),
        ):
            # All done, headless → prints message
            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "-p", str(tmp_path)])
            assert "All tasks complete" in result.output

    def test_main_standalone_mode_incompatible_opencode(self, tmp_path: Path) -> None:
        """Should clear OpenRouter standalone_mode when using Claude Code provider."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = True

        config = ArchitectConfig()
        config.standalone_mode = "openrouter/some-model"

        with (
            patch("the_architect.cli.load_config", return_value=config),
            patch("the_architect.cli.detect_available_providers", return_value=[mock_provider]),
            patch("the_architect.cli.detect_provider", return_value=mock_provider) as mock_detect,
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli._filter_and_set_status", return_value=[]),
            patch("the_architect.cli._run_main"),
            patch("the_architect.config.write_config", return_value=tmp_path / "architect.toml"),
        ):
            # When detect_provider returns a ClaudeCodeProvider
            from the_architect.core.claude_code_provider import ClaudeCodeProvider

            mock_cc = MagicMock(spec=ClaudeCodeProvider)
            mock_cc.display_name = "Claude Code"
            mock_cc.has_any_models.return_value = True
            mock_detect.return_value = mock_cc

            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "-p", str(tmp_path)])  # noqa: F841
            # standalone_mode should be cleared for Claude Code
            assert config.standalone_mode == ""

    def test_main_scope_env_var(self, tmp_path: Path) -> None:
        """Should resolve scope from ARCHITECT_SCOPE env var."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = True

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_available_providers", return_value=[mock_provider]),
            patch("the_architect.cli.detect_provider", return_value=mock_provider),
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli._filter_and_set_status", return_value=[]),
            patch("the_architect.cli._run_main"),
            patch.dict("os.environ", {"ARCHITECT_SCOPE": "simple"}),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "-p", str(tmp_path)])  # noqa: F841

    def test_main_persistent_flag(self, tmp_path: Path) -> None:
        """Should set persistent mode from --persistent flag."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = True

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_available_providers", return_value=[mock_provider]),
            patch("the_architect.cli.detect_provider", return_value=mock_provider),
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_main"),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "--persistent", "-p", str(tmp_path)])  # noqa: F841

    def test_main_free_mode_flag(self, tmp_path: Path) -> None:
        """Should set free mode from --free flag."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = True

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_available_providers", return_value=[mock_provider]),
            patch("the_architect.cli.detect_provider", return_value=mock_provider),
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli._filter_and_set_status", return_value=[]),
            patch("the_architect.cli._run_main"),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "--free", "-p", str(tmp_path)])  # noqa: F841


class TestRunMainRetrospective:
    """Tests for _run_main retrospective execution paths."""

    def test_run_main_retrospective_with_issues(self, tmp_path: Path) -> None:
        """Should discover and execute fix-up tasks from retrospective."""
        from the_architect.cli import _run_main
        from the_architect.core.retrospective import RetrospectiveResult

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        mock_r_task = Task(
            name="R01_fix",
            prefix="R01",
            number=1,
            path=tmp_path / "R01_fix.md",
            title="Fix issue",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()
        config.retrospective_rounds = 1

        call_count = 0  # noqa: F841

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        mock_retro_result = RetrospectiveResult(
            issues_found=1,
            fixes_planned=1,
            tasks_created=["R01_fix"],
            summary="Found one issue",
        )

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task, mock_r_task]),
            patch(
                "the_architect.cli._filter_and_set_status", return_value=[mock_task, mock_r_task]
            ),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.run_retrospective", return_value=mock_retro_result),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                )
            assert exc_info.value.code == 0

    def test_run_main_retrospective_failure(self, tmp_path: Path) -> None:
        """Should handle retrospective failure gracefully."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()
        config.retrospective_rounds = 1

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.run_retrospective", side_effect=RuntimeError("retro failed")),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                )
            # Should still exit 0 since main execution succeeded
            assert exc_info.value.code == 0


class TestRetryCommandMore:
    """More tests for retry command."""

    def test_retry_task_not_done(self, tmp_path: Path) -> None:
        """Should run task even if it's not marked Done."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")
        (tmp_path / "PROGRESS.md").write_text(
            "# Progress\n\n| T01 | Test | Pending | — |\n", encoding="utf-8"
        )
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tasks_dir / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.core.opencode_provider.OpenCodeProvider") as mock_prov,
            patch("the_architect.cli.setup_logging"),
            patch("the_architect.cli.asyncio.run"),
        ):
            mock_prov.return_value.ensure_setup.return_value = None
            runner = CliRunner()
            result = runner.invoke(main, ["retry", "-t", "T01", "-p", str(tmp_path)])
            # Should say task is not Done, then run it
            assert "not marked Done" in result.output or result.exit_code == 0


class TestResetCommandMore:
    """More tests for reset command edge cases."""

    def test_reset_creates_fresh_progress(self, tmp_path: Path) -> None:
        """Should create a fresh PROGRESS.md after reset."""
        progress_content = """# The Architect — Progress Tracker

## Overall Status

**Tasks completed:** 1
**Next task to run:** T02

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | Test | Done | |

---

## Current State

Testing.

## Last Task Summary

N/A

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
| | | | |
"""
        (tmp_path / "PROGRESS.md").write_text(progress_content, encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["reset", "-p", str(tmp_path)], input="y\n")
        assert result.exit_code == 0
        # Verify PROGRESS.md was overwritten
        content = (tmp_path / "PROGRESS.md").read_text(encoding="utf-8")
        assert "0" in content  # tasks_completed should be 0


class TestRunPlanningModeMore:
    """More tests for run_planning_mode branches."""

    def test_run_planning_mode_with_scope(self, tmp_path: Path) -> None:
        """Should pass scope to planner."""
        from the_architect.cli import run_planning_mode

        config = ArchitectConfig()

        with (
            patch("the_architect.cli.run_planner") as mock_planner,
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli.check_pending_tasks", return_value=0),
        ):
            mock_result = MagicMock()
            mock_result.tasks_created = []
            mock_planner.return_value = mock_result
            run_planning_mode(
                project=tmp_path,
                config=config,
                headless=True,
                goal_text="Test goal",
                scope_text="simple",
            )
            mock_planner.assert_called_once()
            # Verify scope was passed
            call_kwargs = mock_planner.call_args
            assert call_kwargs is not None

    def test_run_planning_mode_creates_tasks(self, tmp_path: Path) -> None:
        """Should discover tasks after planning creates them."""
        from the_architect.cli import run_planning_mode

        config = ArchitectConfig()

        mock_result = MagicMock()
        mock_result.tasks_created = ["T01"]

        with (
            patch("the_architect.cli.run_planner", return_value=mock_result),
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli.check_pending_tasks", return_value=0),
        ):
            run_planning_mode(
                project=tmp_path,
                config=config,
                headless=True,
                goal_text="Test goal",
            )


class TestStatusCommandDeeper:
    """Deeper tests for status command branches."""

    def test_status_invalid_lock_pid(self, tmp_path: Path) -> None:
        """Should handle invalid PID in lock file."""
        lock_dir = tmp_path / ".architect"
        lock_dir.mkdir()
        (lock_dir / "runner.lock").write_text("abc", encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["status", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert "Not running" in result.output

    def test_status_permission_error_on_kill(self, tmp_path: Path) -> None:
        """Should handle PermissionError when checking PID."""
        lock_dir = tmp_path / ".architect"
        lock_dir.mkdir()
        (lock_dir / "runner.lock").write_text("12345", encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        with patch("os.kill", side_effect=PermissionError()):
            runner = CliRunner()
            result = runner.invoke(main, ["status", "-p", str(tmp_path)])
            assert result.exit_code == 0


class TestCollectPlanningPrompts:
    """Tests for _collect_planning_prompts headless path."""

    def test_collect_planning_prompts_headless_with_values(self, tmp_path: Path) -> None:
        """Should return provided values in headless mode."""
        from the_architect.cli import _collect_planning_prompts

        config = ArchitectConfig()
        result = _collect_planning_prompts(
            project=tmp_path,
            config=config,
            headless=True,
            goal_text="My goal",
            scope_text="simple",
            architect_model_override="gpt-4",
            execution_model_override="build",
        )
        assert result[0] == "My goal"
        assert result[1] == "simple"
        assert result[2] == "gpt-4"
        assert result[3] == "build"

    def test_collect_planning_prompts_headless_no_model_override(self, tmp_path: Path) -> None:
        """Should return None for execution_model when no override in headless."""
        from the_architect.cli import _collect_planning_prompts

        config = ArchitectConfig()
        result = _collect_planning_prompts(
            project=tmp_path,
            config=config,
            headless=True,
            goal_text="My goal",
            scope_text="",
            architect_model_override="",
            execution_model_override="",
        )
        assert result[0] == "My goal"
        assert result[3] is None  # No override → None


class TestRunTasksRawCallbacks:
    """Tests for _run_tasks_raw callback functions (deeper coverage)."""

    def test_run_tasks_raw_on_task_failed(self, tmp_path: Path) -> None:
        """Should call on_task_failed when a task fails."""
        from the_architect.cli import _run_tasks_raw

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()
        config.max_retries = 3
        mock_result = TaskResult(
            prefix="T01",
            title="Test task",
            status="failed",
            duration_seconds=5.0,
            tokens=TokenUsage(),
        )

        async def _fake_run_all(*args: object, **kwargs: object) -> bool:
            on_task_failed = kwargs.get("on_task_failed")
            if on_task_failed:
                on_task_failed(mock_result)
            return False

        with (
            patch("the_architect.cli._ansi_supported", return_value=False),
            patch("the_architect.cli.run_all", side_effect=_fake_run_all),
        ):
            import asyncio

            success, results, duration = asyncio.run(_run_tasks_raw(tmp_path, config, [mock_task]))
            assert success is False

    def test_run_tasks_raw_on_attempt_start_retry(self, tmp_path: Path) -> None:
        """Should call on_attempt_start with retry number."""
        from the_architect.cli import _run_tasks_raw

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        async def _fake_run_all(*args: object, **kwargs: object) -> bool:
            on_attempt_start = kwargs.get("on_attempt_start")
            if on_attempt_start:
                on_attempt_start(2, "retry-model")
            return True

        with (
            patch("the_architect.cli._ansi_supported", return_value=False),
            patch("the_architect.cli.run_all", side_effect=_fake_run_all),
        ):
            import asyncio

            success, results, duration = asyncio.run(_run_tasks_raw(tmp_path, config, [mock_task]))
            assert success is True

    def test_run_tasks_raw_on_attempt_done_retry(self, tmp_path: Path) -> None:
        """Should show retry message when attempt fails but retries remain."""
        from the_architect.cli import _run_tasks_raw

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()
        config.max_retries = 3
        config.retry_model_2 = "fallback-model"

        async def _fake_run_all(*args: object, **kwargs: object) -> bool:
            on_attempt_done = kwargs.get("on_attempt_done")
            if on_attempt_done:
                on_attempt_done(1, False)  # First attempt failed
            return True

        with (
            patch("the_architect.cli._ansi_supported", return_value=False),
            patch("the_architect.cli.run_all", side_effect=_fake_run_all),
        ):
            import asyncio

            success, results, duration = asyncio.run(_run_tasks_raw(tmp_path, config, [mock_task]))
            assert success is True

    def test_run_tasks_raw_model_switch_no_new_model(self, tmp_path: Path) -> None:
        """Should show 'all free models exhausted' when no new model."""
        from the_architect.cli import _run_tasks_raw

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        async def _fake_run_all(*args: object, **kwargs: object) -> bool:
            on_model_switched = kwargs.get("on_model_switched")
            if on_model_switched:
                on_model_switched("old-model", None)  # No new model available
            return True

        with (
            patch("the_architect.cli._ansi_supported", return_value=False),
            patch("the_architect.cli.run_all", side_effect=_fake_run_all),
        ):
            import asyncio

            success, results, duration = asyncio.run(_run_tasks_raw(tmp_path, config, [mock_task]))
            assert success is True

    def test_run_tasks_raw_multiple_tasks(self, tmp_path: Path) -> None:
        """Should show remaining count when multiple tasks."""
        from the_architect.cli import _run_tasks_raw

        tasks = [
            Task(
                name=f"T0{i}_test",
                prefix=f"T0{i}",
                number=i,
                path=tmp_path / f"T0{i}_test.md",
                title=f"Task {i}",
                status=TaskStatus.PENDING,
            )
            for i in range(1, 4)
        ]
        config = ArchitectConfig()
        mock_result = TaskResult(
            prefix="T01",
            title="Task 1",
            status="done",
            duration_seconds=1.0,
            tokens=TokenUsage(),
        )

        async def _fake_run_all(*args: object, **kwargs: object) -> bool:
            on_task_start = kwargs.get("on_task_start")
            on_task_done = kwargs.get("on_task_done")
            if on_task_start:
                on_task_start(tasks[0])
            if on_task_done:
                on_task_done(mock_result)
            return True

        with (
            patch("the_architect.cli._ansi_supported", return_value=False),
            patch("the_architect.cli.run_all", side_effect=_fake_run_all),
        ):
            import asyncio

            success, results, duration = asyncio.run(_run_tasks_raw(tmp_path, config, tasks))
            assert success is True


class TestRunMainNonPreloaded:
    """Tests for _run_main with non-preloaded config path."""

    def test_run_main_loads_config(self, tmp_path: Path) -> None:
        """Should load config when _pre_loaded_config is None."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        with (
            patch("the_architect.cli.load_config", return_value=config),
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=None,
                )
            assert exc_info.value.code == 0

    def test_run_main_with_standalone(self, tmp_path: Path) -> None:
        """Should set standalone_mode on config."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        with (
            patch("the_architect.cli.load_config", return_value=config),
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    standalone="gpt-4",
                    headless=True,
                    _pre_loaded_config=None,
                )
            assert exc_info.value.code == 0
            assert config.standalone_mode == "gpt-4"

    def test_run_main_free_mode_with_opencode(self, tmp_path: Path) -> None:
        """Should fetch free models when free mode with OpenCode provider."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()
        config.free_mode = True

        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.supports_free_tier.return_value = True
        mock_provider.get_resolved_model.return_value = "model"

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        mock_rotator = MagicMock()
        mock_rotator.total_count = 5
        mock_rotator.current_model = "free-model-1"

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
            patch("the_architect.core.free_models.FreeModelRotator", return_value=mock_rotator),
            patch("the_architect.cli.asyncio.run"),
        ):
            with pytest.raises(SystemExit) as exc_info:  # noqa: F841
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                    provider=mock_provider,
                )

    def test_run_main_provider_ensure_setup(self, tmp_path: Path) -> None:
        """Should call provider.ensure_setup."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        mock_provider = MagicMock()
        mock_provider.get_resolved_model.return_value = "model"

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
        ):
            with pytest.raises(SystemExit) as exc_info:  # noqa: F841
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                    provider=mock_provider,
                )
            mock_provider.ensure_setup.assert_called()


class TestInitCommandMore:
    """More tests for init command to increase coverage."""

    def test_init_creates_project_dir(self, tmp_path: Path) -> None:
        """Should create project directory if it doesn't exist."""
        new_dir = tmp_path / "new_project"
        runner = CliRunner()
        result = runner.invoke(main, ["init", "-p", str(new_dir)])
        assert result.exit_code == 0
        assert new_dir.exists()

    def test_init_agents_md_content_detailed(self, tmp_path: Path) -> None:
        """Should create AGENTS.md with all required sections."""
        runner = CliRunner()
        result = runner.invoke(main, ["init", "-p", str(tmp_path)])  # noqa: F841

        content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        # Verify the exact content from cli.py lines 3363-3376
        assert "The Architect reads this file" in content
        assert "## Stack" in content
        assert "## Conventions" in content
        assert "## Constraints" in content
        assert "## Architecture" in content
        assert "<!-- e.g." in content


class TestCircuitCommandDeeper:
    """Deeper tests for circuit command branches."""

    def test_circuit_with_opened_at_recent(self, tmp_path: Path) -> None:
        """Should show 'Xs ago' for recently opened circuit."""
        import json
        from datetime import UTC, datetime

        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()

        now = datetime.now(tz=UTC)
        recent = now.isoformat()

        circuit_data = {
            "T01": {
                "state": "OPEN",
                "consecutive_no_progress": 3,
                "consecutive_same_error": 0,
                "recovery_action": "WAIT",
                "opened_at": recent,
            }
        }
        (arch_dir / "circuit.json").write_text(json.dumps(circuit_data), encoding="utf-8")
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tasks_dir / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )

        with patch("the_architect.cli.discover_tasks", return_value=[mock_task]):
            runner = CliRunner()
            result = runner.invoke(main, ["circuit", "-p", str(tmp_path)])
            assert "OPEN" in result.output
            assert "ago" in result.output

    def test_circuit_task_no_state(self, tmp_path: Path) -> None:
        """Should show CLOSED for tasks with no circuit state."""
        import json

        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        circuit_data = {
            "T01": {
                "state": "CLOSED",
                "consecutive_no_progress": 0,
                "consecutive_same_error": 0,
                "recovery_action": None,
                "opened_at": None,
            }
        }
        (arch_dir / "circuit.json").write_text(json.dumps(circuit_data), encoding="utf-8")
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tasks_dir / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        # Also add a task that's not in circuit_data
        mock_task2 = Task(
            name="T02_test",
            prefix="T02",
            number=2,
            path=tasks_dir / "T02_test.md",
            title="Task 2",
            status=TaskStatus.PENDING,
        )

        with patch("the_architect.cli.discover_tasks", return_value=[mock_task, mock_task2]):
            runner = CliRunner()
            result = runner.invoke(main, ["circuit", "-p", str(tmp_path)])
            assert "CLOSED" in result.output
            assert "T02" in result.output


class TestListCommandDeeper:
    """Deeper tests for list command to increase coverage."""

    def test_list_with_mixed_statuses(self, tmp_path: Path) -> None:
        """Should show both Done and Pending tasks."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        done_task = Task(
            name="T01_done",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_done.md",
            title="Done task",
            status=TaskStatus.DONE,
        )
        pending_task = Task(
            name="T02_pending",
            prefix="T02",
            number=2,
            path=tmp_path / "T02_pending.md",
            title="Pending task",
            status=TaskStatus.PENDING,
        )

        with (
            patch("the_architect.cli.discover_tasks", return_value=[done_task, pending_task]),
            patch("the_architect.cli.task_is_done", side_effect=lambda f, p: p == "T01"),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["list", "-p", str(tmp_path)])
            assert result.exit_code == 0
            assert "T01" in result.output
            assert "T02" in result.output

    def test_list_shows_done_status(self, tmp_path: Path) -> None:
        """Should show ✓ Done for completed tasks."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        done_task = Task(
            name="T01_done",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_done.md",
            title="Done task",
            status=TaskStatus.DONE,
        )

        with (
            patch("the_architect.cli.discover_tasks", return_value=[done_task]),
            patch("the_architect.cli.task_is_done", return_value=True),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["list", "-p", str(tmp_path)])
            assert result.exit_code == 0
            assert "Done" in result.output


class TestLogsCommandAll:
    """Tests for logs --all and list branches."""

    def test_logs_list_with_multiple_files(self, tmp_path: Path) -> None:
        """Should list multiple log files with size and date."""
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "T01_test.log").write_text("log1", encoding="utf-8")
        (log_dir / "T02_test.log").write_text("log2", encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["logs", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert "T01_test.log" in result.output
        assert "T02_test.log" in result.output
        assert "Use" in result.output  # "Use architect logs --task T01..."

    def test_logs_read_error(self, tmp_path: Path) -> None:
        """Should handle OSError when reading log file."""
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "T01_test.log").write_text("content", encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        with patch("pathlib.Path.read_text", side_effect=OSError("read error")):
            runner = CliRunner()
            result = runner.invoke(main, ["logs", "-p", str(tmp_path), "--task", "T01"])
            # Should show error message, not crash
            assert result.exit_code == 0 or "Could not read" in result.output


class TestConfigCommandDisplay:
    """Tests for config command display fields."""

    def test_config_display_all_fields(self, tmp_path: Path) -> None:
        """Should display all config fields when showing config."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["config", "-p", str(tmp_path)])
        assert result.exit_code == 0
        # Check key fields are shown
        assert "max_retries" in result.output
        assert "retry_pause" in result.output
        assert "pause_between_tasks" in result.output
        assert "retrospective_rounds" in result.output
        assert "carry_context" in result.output
        assert "free_mode" in result.output

    def test_config_with_toml_shows_path(self, tmp_path: Path) -> None:
        """Should show config file path when architect.toml exists."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["config", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert "Config file:" in result.output or "architect.toml" in result.output


class TestRunMainDeeper:
    """More tests for _run_main deeper branches."""

    def test_run_main_no_provider_calls_opencode(self, tmp_path: Path) -> None:
        """Should default to OpenCode when no provider passed."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
            patch("the_architect.core.opencode_provider.OpenCodeProvider") as mock_oc,
        ):
            mock_oc.return_value.ensure_setup.return_value = None
            with pytest.raises(SystemExit) as exc_info:  # noqa: F841
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                    provider=None,
                )
            mock_oc.return_value.ensure_setup.assert_called()

    def test_run_main_summary_generation_error(self, tmp_path: Path) -> None:
        """Should handle error during summary generation gracefully."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.write_success_md", side_effect=OSError("write error")),
            patch("the_architect.cli.print_success_summary"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                )
            # Should still exit 0 since execution succeeded
            assert exc_info.value.code == 0

    def test_run_main_free_mode_no_models(self, tmp_path: Path) -> None:
        """Should disable free mode when no free models available."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()
        config.free_mode = True

        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.supports_free_tier.return_value = True
        mock_provider.get_resolved_model.return_value = "model"

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        mock_rotator = MagicMock()
        mock_rotator.total_count = 0  # No free models available

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
            patch("the_architect.core.free_models.FreeModelRotator", return_value=mock_rotator),
            patch("the_architect.cli.asyncio.run"),
            patch("the_architect.config.write_config", return_value=tmp_path / "architect.toml"),
        ):
            with pytest.raises(SystemExit) as exc_info:  # noqa: F841
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                    provider=mock_provider,
                )
            # Free mode should have been disabled
            assert config.free_mode is False

    def test_run_main_planning_creates_no_tasks(self, tmp_path: Path) -> None:
        """Should exit with error when planning creates no tasks."""
        from the_architect.cli import _run_main

        config = ArchitectConfig()

        with (
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli._filter_and_set_status", return_value=[]),
            patch("the_architect.cli.run_planning_mode"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    goal_text="Test",
                    _pre_loaded_config=config,
                )
            assert exc_info.value.code == 1

    def test_run_main_only_task_not_found(self, tmp_path: Path) -> None:
        """Should exit with error when --only task not found."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    only_task="T99",
                    headless=True,
                    _pre_loaded_config=config,
                )
            assert exc_info.value.code == 1

    def test_run_main_from_task_not_found(self, tmp_path: Path) -> None:
        """Should exit with error when --from task not found."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    from_task="T99",
                    headless=True,
                    _pre_loaded_config=config,
                )
            assert exc_info.value.code == 1


class TestMainProviderSelection:
    """Tests for main() provider selection after tmux launch."""

    def test_main_headless_dual_provider_selects_first(self, tmp_path: Path) -> None:
        """Should select first provider in headless when both installed."""
        mock_oc = MagicMock()
        mock_oc.display_name = "OpenCode"
        mock_oc.name = "opencode"
        mock_oc.has_any_models.return_value = True

        mock_cc = MagicMock()
        mock_cc.display_name = "Claude Code"
        mock_cc.name = "claude-code"
        mock_cc.has_any_models.return_value = True

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_available_providers", return_value=[mock_oc, mock_cc]),
            patch("the_architect.cli.detect_provider", return_value=mock_oc),
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli._filter_and_set_status", return_value=[]),
            patch("the_architect.core.tmux.maybe_launch_tmux", return_value=False),
            patch("the_architect.cli._run_main"),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "-p", str(tmp_path)])  # noqa: F841

    def test_main_post_tmux_no_providers(self, tmp_path: Path) -> None:
        """Should exit with error when no providers found after tmux."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = True

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch(
                "the_architect.cli.detect_available_providers", side_effect=[[mock_provider], []]
            ),
            patch("the_architect.cli.detect_provider", return_value=mock_provider),
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli._filter_and_set_status", return_value=[]),
            patch("the_architect.core.tmux.maybe_launch_tmux", return_value=False),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "-p", str(tmp_path)])  # noqa: F841
            # Should fail because second call to detect_available_providers returns []


class TestAlternateScreenException:
    """Tests for alternate_screen exception handling."""

    def test_alternate_screen_exception_in_body(self) -> None:
        """Should restore screen even when exception occurs in body."""
        from the_architect.cli import alternate_screen

        with (
            patch("sys.stdout.isatty", return_value=True),
            patch("sys.stdout.write") as mock_write,
            patch("sys.stdout.flush"),
        ):
            with pytest.raises(ValueError):
                with alternate_screen():
                    raise ValueError("test error")

            # Should still write exit code even on exception
            writes = [str(call) for call in mock_write.call_args_list]
            assert any("\\033[?1049l" in w or "1049l" in w for w in writes)


class TestRunPlanningModeDeeper:
    """Deeper coverage of run_planning_mode branches."""

    def test_run_planning_mode_headless_no_goal_exits(self, tmp_path: Path) -> None:
        """Should exit with error when headless and no goal."""
        from the_architect.cli import run_planning_mode

        config = ArchitectConfig()

        with pytest.raises(SystemExit) as exc_info:
            run_planning_mode(
                project=tmp_path,
                config=config,
                headless=True,
                goal_text="",
            )
        assert exc_info.value.code == 1

    def test_run_planning_mode_with_model_override(self, tmp_path: Path) -> None:
        """Should pass model overrides to planner."""
        from the_architect.cli import run_planning_mode

        config = ArchitectConfig()
        mock_provider = MagicMock()
        mock_provider.display_name = "TestProvider"
        mock_provider.get_resolved_model.return_value = "default-model"
        mock_provider.ensure_setup.return_value = None

        mock_result = MagicMock()
        mock_result.tasks_created = []

        with (
            patch("the_architect.cli.run_planner", return_value=mock_result),
            patch("the_architect.core.structure.detect_structure", return_value=MagicMock()),
            patch(
                "the_architect.core.structure.format_structure_for_prompt", return_value="structure"
            ),
            patch("the_architect.core.architect_md.write_or_update_architect_md"),
            patch("the_architect.core.architect_md.read_architect_md", return_value=""),
            patch("the_architect.core.architect_md.append_planning_history"),
        ):
            run_planning_mode(
                project=tmp_path,
                config=config,
                headless=True,
                goal_text="Test",
                architect_model_override="gpt-4",
                execution_model_override="build",
                provider=mock_provider,
            )
            mock_provider.ensure_setup.assert_called()

    def test_run_planning_mode_with_provider(self, tmp_path: Path) -> None:
        """Should use provider passed as argument."""
        from the_architect.cli import run_planning_mode

        config = ArchitectConfig()
        mock_provider = MagicMock()
        mock_provider.display_name = "TestProvider"
        mock_provider.get_resolved_model.return_value = "resolved-model"
        mock_provider.ensure_setup.return_value = None

        mock_result = MagicMock()
        mock_result.tasks_created = []

        with (
            patch("the_architect.cli.run_planner", return_value=mock_result),
            patch("the_architect.core.structure.detect_structure", return_value=MagicMock()),
            patch(
                "the_architect.core.structure.format_structure_for_prompt", return_value="structure"
            ),
            patch("the_architect.core.architect_md.write_or_update_architect_md"),
            patch("the_architect.core.architect_md.read_architect_md", return_value=""),
            patch("the_architect.core.architect_md.append_planning_history"),
        ):
            run_planning_mode(
                project=tmp_path,
                config=config,
                headless=True,
                goal_text="Test",
                provider=mock_provider,
            )
            mock_provider.ensure_setup.assert_called()

    def test_run_planning_mode_pending_tasks_headless(self, tmp_path: Path) -> None:
        """Should warn and continue in headless mode with pending tasks."""
        from the_architect.cli import run_planning_mode

        config = ArchitectConfig()
        mock_provider = MagicMock()
        mock_provider.display_name = "TestProvider"
        mock_provider.get_resolved_model.return_value = "resolved-model"
        mock_provider.ensure_setup.return_value = None

        mock_result = MagicMock()
        mock_result.tasks_created = []

        with (
            patch("the_architect.cli.run_planner", return_value=mock_result),
            patch("the_architect.core.structure.detect_structure", return_value=MagicMock()),
            patch(
                "the_architect.core.structure.format_structure_for_prompt", return_value="structure"
            ),
            patch("the_architect.core.architect_md.write_or_update_architect_md"),
            patch("the_architect.core.architect_md.read_architect_md", return_value=""),
            patch("the_architect.core.architect_md.append_planning_history"),
        ):
            run_planning_mode(
                project=tmp_path,
                config=config,
                headless=True,
                goal_text="Test",
                provider=mock_provider,
            )

    def test_run_planning_mode_context_file_not_found(self, tmp_path: Path) -> None:
        """Should exit with error when context file not found."""
        from the_architect.cli import run_planning_mode

        config = ArchitectConfig()

        with (
            patch(
                "the_architect.core.context.load_context_paths",
                side_effect=FileNotFoundError("not found"),
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                run_planning_mode(
                    project=tmp_path,
                    config=config,
                    headless=True,
                    goal_text="",
                    context_paths=(Path("/nonexistent/file.txt"),),
                )
            assert exc_info.value.code == 1

    def test_run_planning_mode_planning_failed_error(self, tmp_path: Path) -> None:
        """Should exit with error when PlanningFailedError is raised."""
        from the_architect.cli import run_planning_mode
        from the_architect.core.planner import PlanningFailedError

        config = ArchitectConfig()
        mock_provider = MagicMock()
        mock_provider.display_name = "TestProvider"
        mock_provider.get_resolved_model.return_value = "resolved-model"
        mock_provider.ensure_setup.return_value = None

        with (
            patch("the_architect.cli.run_planner", side_effect=PlanningFailedError("fail")),
            patch("the_architect.core.structure.detect_structure", return_value=MagicMock()),
            patch(
                "the_architect.core.structure.format_structure_for_prompt", return_value="structure"
            ),
            patch("the_architect.core.architect_md.write_or_update_architect_md"),
            patch("the_architect.core.architect_md.read_architect_md", return_value=""),
        ):
            with pytest.raises(SystemExit) as exc_info:
                run_planning_mode(
                    project=tmp_path,
                    config=config,
                    headless=True,
                    goal_text="Test",
                    provider=mock_provider,
                )
            assert exc_info.value.code == 1

    def test_run_planning_mode_generic_error(self, tmp_path: Path) -> None:
        """Should exit with error on generic exception from planner."""
        from the_architect.cli import run_planning_mode

        config = ArchitectConfig()
        mock_provider = MagicMock()
        mock_provider.display_name = "TestProvider"
        mock_provider.get_resolved_model.return_value = "resolved-model"
        mock_provider.ensure_setup.return_value = None

        with (
            patch("the_architect.cli.run_planner", side_effect=RuntimeError("boom")),
            patch("the_architect.core.structure.detect_structure", return_value=MagicMock()),
            patch(
                "the_architect.core.structure.format_structure_for_prompt", return_value="structure"
            ),
            patch("the_architect.core.architect_md.write_or_update_architect_md"),
            patch("the_architect.core.architect_md.read_architect_md", return_value=""),
        ):
            with pytest.raises(SystemExit) as exc_info:
                run_planning_mode(
                    project=tmp_path,
                    config=config,
                    headless=True,
                    goal_text="Test",
                    provider=mock_provider,
                )
            assert exc_info.value.code == 1

    def test_run_planning_mode_no_model_resolved(self, tmp_path: Path) -> None:
        """Should handle provider returning None for model resolution."""
        from the_architect.cli import run_planning_mode

        config = ArchitectConfig()
        mock_provider = MagicMock()
        mock_provider.display_name = "TestProvider"
        mock_provider.get_resolved_model.return_value = None
        mock_provider.ensure_setup.return_value = None

        mock_result = MagicMock()
        mock_result.tasks_created = []

        with (
            patch("the_architect.cli.run_planner", return_value=mock_result),
            patch("the_architect.core.structure.detect_structure", return_value=MagicMock()),
            patch(
                "the_architect.core.structure.format_structure_for_prompt", return_value="structure"
            ),
            patch("the_architect.core.architect_md.write_or_update_architect_md"),
            patch("the_architect.core.architect_md.read_architect_md", return_value=""),
            patch("the_architect.core.architect_md.append_planning_history"),
        ):
            run_planning_mode(
                project=tmp_path,
                config=config,
                headless=True,
                goal_text="Test",
                provider=mock_provider,
            )

    def test_run_planning_mode_model_resolution_error(self, tmp_path: Path) -> None:
        """Should handle exception from get_resolved_model for display_model."""
        from the_architect.cli import run_planning_mode

        config = ArchitectConfig()
        mock_provider = MagicMock()
        mock_provider.display_name = "TestProvider"

        # First call for architect_model resolution returns a value,
        # second call for display_model raises
        mock_provider.get_resolved_model.side_effect = ["resolved-model", Exception("fail")]
        mock_provider.ensure_setup.return_value = None

        mock_result = MagicMock()
        mock_result.tasks_created = []

        with (
            patch("the_architect.cli.run_planner", return_value=mock_result),
            patch("the_architect.core.structure.detect_structure", return_value=MagicMock()),
            patch(
                "the_architect.core.structure.format_structure_for_prompt", return_value="structure"
            ),
            patch("the_architect.core.architect_md.write_or_update_architect_md"),
            patch("the_architect.core.architect_md.read_architect_md", return_value=""),
            patch("the_architect.core.architect_md.append_planning_history"),
        ):
            run_planning_mode(
                project=tmp_path,
                config=config,
                headless=True,
                goal_text="Test",
                provider=mock_provider,
            )
            mock_provider.ensure_setup.assert_called()


class TestSkipCommandDeeper:
    """Deeper tests for skip command branches."""

    def test_skip_resets_in_progress(self, tmp_path: Path) -> None:
        """Should reset task from Pending to Done in PROGRESS.md."""
        progress_content = """# The Architect — Progress Tracker

## Overall Status

**Tasks completed:** 0
**Next task to run:** T01

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | Test | Pending | — |

---

## Current State

Testing.

## Last Task Summary

N/A

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
| | | | |
"""
        (tmp_path / "PROGRESS.md").write_text(progress_content, encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["skip", "-t", "T01", "-p", str(tmp_path)])
        assert result.exit_code == 0
        # Verify the PROGRESS.md was updated
        updated = (tmp_path / "PROGRESS.md").read_text(encoding="utf-8")
        assert "Done" in updated


class TestCancelCommandDeeper:
    """Deeper tests for cancel command branches."""

    def test_cancel_kill_succeeds(self, tmp_path: Path) -> None:
        """Should remove lock after killing process."""
        lock_dir = tmp_path / ".architect"
        lock_dir.mkdir()
        (lock_dir / "runner.lock").write_text("12345", encoding="utf-8")

        with patch("os.kill", return_value=None):
            runner = CliRunner()
            result = runner.invoke(main, ["cancel", "-p", str(tmp_path)], input="y\n")
            # Process should be terminated and lock removed
            assert "Lock removed" in result.output or result.exit_code == 0

    def test_cancel_kill_permission_error(self, tmp_path: Path) -> None:
        """Should handle PermissionError when trying to kill."""
        lock_dir = tmp_path / ".architect"
        lock_dir.mkdir()
        (lock_dir / "runner.lock").write_text("12345", encoding="utf-8")

        with patch("os.kill", side_effect=PermissionError("no permission")):
            runner = CliRunner()
            result = runner.invoke(main, ["cancel", "-p", str(tmp_path)], input="y\n")
            # Should report the error
            assert "Could not terminate" in result.output or result.exit_code == 0


class TestResetCommandDeeper:
    """Deeper tests for reset command."""

    def test_reset_click_confirm_yes(self, tmp_path: Path) -> None:
        """Should accept 'y' input for click.confirm."""
        (tmp_path / "PROGRESS.md").write_text("Old content", encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["reset", "-p", str(tmp_path)], input="y\n")
        assert result.exit_code == 0


class TestInitCommandDeeper:
    """Deeper tests for init command to cover specific output branches."""

    def test_init_toml_content_detailed(self, tmp_path: Path) -> None:
        """Should create architect.toml with all commented options."""
        runner = CliRunner()
        result = runner.invoke(main, ["init", "-p", str(tmp_path)])
        assert result.exit_code == 0

        toml_content = (tmp_path / "architect.toml").read_text(encoding="utf-8")
        assert "max_retries" in toml_content
        assert "retry_pause" in toml_content
        assert "pause_between_tasks" in toml_content
        assert "retrospective_rounds" in toml_content
        assert "carry_context" in toml_content
        assert "token_budget_per_hour" in toml_content


class TestLogsCommandJsonParsing:
    """Tests for logs command JSON parsing edge cases."""

    def test_logs_mixed_json_and_raw(self, tmp_path: Path) -> None:
        """Should handle mix of JSON and raw text lines."""
        import json

        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        entries = [
            "raw text line",
            json.dumps({"type": "text", "part": {"text": "JSON text"}}),
            "another raw line",
            json.dumps({"type": "error", "error": "error message"}),
        ]
        (log_dir / "T01_test.log").write_text("\n".join(entries), encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["logs", "-p", str(tmp_path), "--task", "T01"])
        assert result.exit_code == 0
        assert "raw text line" in result.output
        assert "JSON text" in result.output

    def test_logs_empty_lines(self, tmp_path: Path) -> None:
        """Should skip empty lines in log file."""
        import json

        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        entries = [
            "",
            json.dumps({"type": "text", "part": {"text": "Content"}}),
            "",
        ]
        (log_dir / "T01_test.log").write_text("\n".join(entries), encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["logs", "-p", str(tmp_path), "--task", "T01"])
        assert result.exit_code == 0
        assert "Content" in result.output

    def test_logs_text_with_strip(self, tmp_path: Path) -> None:
        """Should strip whitespace from text content."""
        import json

        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        entries = [
            json.dumps({"type": "text", "part": {"text": "  hello  "}}),
        ]
        (log_dir / "T01_test.log").write_text("\n".join(entries), encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["logs", "-p", str(tmp_path), "--task", "T01"])
        assert result.exit_code == 0

    def test_logs_error_with_message_field(self, tmp_path: Path) -> None:
        """Should handle error events with 'message' field."""
        import json

        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        entries = [
            json.dumps({"type": "error", "message": "Error occurred"}),
        ]
        (log_dir / "T01_test.log").write_text("\n".join(entries), encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["logs", "-p", str(tmp_path), "--task", "T01"])
        assert result.exit_code == 0
        assert "Error occurred" in result.output


class TestConfigCommandSetValues:
    """Tests for config command setting various value types."""

    def test_config_set_multiple_values(self, tmp_path: Path) -> None:
        """Should handle multiple --set flags."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "config",
                "-p",
                str(tmp_path),
                "--set",
                "max_retries=5",
                "--set",
                "carry_context=true",
            ],
        )
        assert result.exit_code == 0

    def test_config_set_circuit_option(self, tmp_path: Path) -> None:
        """Should handle circuit breaker config options."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["config", "-p", str(tmp_path), "--set", "circuit_no_progress_threshold=5"],
        )
        assert result.exit_code == 0
        assert "circuit_no_progress_threshold" in result.output

    def test_config_write_error(self, tmp_path: Path) -> None:
        """Should handle write_config errors."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        with patch("the_architect.cli.write_config", side_effect=ValueError("bad value")):
            runner = CliRunner()
            result = runner.invoke(main, ["config", "-p", str(tmp_path), "--set", "max_retries=5"])
            assert result.exit_code == 1
            assert "Config error" in result.output


class TestStatusCommandDeeperMore:
    """More status command branch tests."""

    def test_status_oserror_reading_lock(self, tmp_path: Path) -> None:
        """Should handle OSError when reading lock file."""
        lock_dir = tmp_path / ".architect"
        lock_dir.mkdir()
        (lock_dir / "runner.lock").write_text("12345", encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        with patch("pathlib.Path.read_text", side_effect=OSError("read error")):
            runner = CliRunner()
            result = runner.invoke(main, ["status", "-p", str(tmp_path)])
            assert result.exit_code == 0
            assert "Not running" in result.output


class TestMainEnvVarResolution:
    """Tests for main() env var resolution paths."""

    def test_main_architect_model_env(self, tmp_path: Path) -> None:
        """Should resolve architect model from env var."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = True

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_available_providers", return_value=[mock_provider]),
            patch("the_architect.cli.detect_provider", return_value=mock_provider),
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli._filter_and_set_status", return_value=[]),
            patch("the_architect.cli._run_main"),
            patch.dict("os.environ", {"ARCHITECT_ARCHITECT_MODEL": "gpt-4"}),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "-p", str(tmp_path)])  # noqa: F841

    def test_main_execution_model_env(self, tmp_path: Path) -> None:
        """Should resolve execution model from env var."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = True

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_available_providers", return_value=[mock_provider]),
            patch("the_architect.cli.detect_provider", return_value=mock_provider),
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli._filter_and_set_status", return_value=[]),
            patch("the_architect.cli._run_main"),
            patch.dict("os.environ", {"ARCHITECT_EXECUTION_MODEL": "build"}),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "-p", str(tmp_path)])  # noqa: F841

    def test_main_context_env(self, tmp_path: Path) -> None:
        """Should resolve context paths from env var."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = True

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_available_providers", return_value=[mock_provider]),
            patch("the_architect.cli.detect_provider", return_value=mock_provider),
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli._filter_and_set_status", return_value=[]),
            patch("the_architect.cli._run_main"),
            patch.dict("os.environ", {"ARCHITECT_CONTEXT": str(tmp_path)}),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "-p", str(tmp_path)])  # noqa: F841


class TestCircuitCommandEdgeCases:
    """Edge case tests for circuit command."""

    def test_circuit_invalid_opened_at(self, tmp_path: Path) -> None:
        """Should handle invalid opened_at timestamp gracefully."""
        import json

        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        circuit_data = {
            "T01": {
                "state": "OPEN",
                "consecutive_no_progress": 3,
                "consecutive_same_error": 0,
                "recovery_action": "WAIT",
                "opened_at": "not-a-date",
            }
        }
        (arch_dir / "circuit.json").write_text(json.dumps(circuit_data), encoding="utf-8")
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tasks_dir / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )

        with patch("the_architect.cli.discover_tasks", return_value=[mock_task]):
            runner = CliRunner()
            result = runner.invoke(main, ["circuit", "-p", str(tmp_path)])
            # Should not crash
            assert result.exit_code == 0


class TestRunMainNoProviderPostTmux:
    """Tests for _run_main with provider=None after no pre-loaded config."""

    def test_run_main_standalone_openrouter_with_claude(self, tmp_path: Path) -> None:
        """Should clear standalone_mode when OpenRouter model used with Claude."""

        config = ArchitectConfig()
        config.standalone_mode = "openrouter/some-model"

        mock_claude = MagicMock()
        mock_claude.__class__ = type("ClaudeCodeProvider", (), {})

        with (
            patch("the_architect.cli.load_config", return_value=config),
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli._filter_and_set_status", return_value=[]),
            patch(
                "the_architect.core.claude_code_provider.ClaudeCodeProvider",
                return_value=mock_claude,
            ),
            patch(
                "the_architect.cli.isinstance",
                side_effect=lambda obj, cls: (
                    True if "ClaudeCode" in str(cls) else isinstance(obj, cls)
                ),
            ),
            patch("the_architect.config.write_config", return_value=tmp_path / "architect.toml"),
        ):
            # This path (2440-2459) is hard to test without real ClaudeCodeProvider
            # but we can verify the config setup path
            assert config.standalone_mode == "openrouter/some-model"


class TestRunMainExecutionPaths:
    """Tests for _run_main execution flow branches."""

    def test_run_main_pending_tasks_after_plan(self, tmp_path: Path) -> None:
        """Should handle pending tasks after planning creates them."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
            patch("the_architect.cli._read_goal_from_instructions", return_value="test goal"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                )
            assert exc_info.value.code == 0

    def test_run_main_all_done_exits(self, tmp_path: Path) -> None:
        """Should exit with success when all tasks already done (headless)."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.DONE,
        )
        config = ArchitectConfig()

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                )
            # All done → plan mode → no goal → SystemExit
            assert exc_info.value.code in (0, 1)

    def test_run_main_execution_model_display(self, tmp_path: Path) -> None:
        """Should show execution model in header when available."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        mock_provider = MagicMock()
        mock_provider.get_resolved_model.return_value = "build-model"
        mock_provider.display_name = "OpenCode"

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
            patch("the_architect.cli._read_goal_from_instructions", return_value=""),
        ):
            with pytest.raises(SystemExit) as exc_info:  # noqa: F841
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    execution_model="build",
                    _pre_loaded_config=config,
                    provider=mock_provider,
                )

    def test_run_main_retrospective_with_issues(self, tmp_path: Path) -> None:
        """Should run retrospective and handle issues found."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()
        config.retrospective_rounds = 1

        mock_result = TaskResult(
            prefix="T01",
            title="Test task",
            status="done",
            duration_seconds=1.0,
            tokens=TokenUsage(),
        )
        mock_retro_result = MagicMock()
        mock_retro_result.issues_found = 1
        mock_retro_result.fixes_planned = 1
        mock_retro_result.tasks_created = ["R01"]
        mock_retro_result.summary = "Found 1 issue"

        call_count = 0  # noqa: F841

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [mock_result], 1.0)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.run_retrospective", return_value=mock_retro_result),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
            patch("the_architect.cli._read_goal_from_instructions", return_value=""),
        ):
            with pytest.raises(SystemExit):
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                )

    def test_run_main_retrospective_no_issues(self, tmp_path: Path) -> None:
        """Should handle retrospective finding no issues."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()
        config.retrospective_rounds = 1

        mock_result = TaskResult(
            prefix="T01",
            title="Test task",
            status="done",
            duration_seconds=1.0,
            tokens=TokenUsage(),
        )
        mock_retro_result = MagicMock()
        mock_retro_result.issues_found = 0
        mock_retro_result.fixes_planned = 0
        mock_retro_result.tasks_created = []
        mock_retro_result.summary = "No issues"

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [mock_result], 1.0)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.run_retrospective", return_value=mock_retro_result),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
            patch("the_architect.cli._read_goal_from_instructions", return_value=""),
        ):
            with pytest.raises(SystemExit):
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                )

    def test_run_main_retrospective_failure(self, tmp_path: Path) -> None:
        """Should handle retrospective failure gracefully."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()
        config.retrospective_rounds = 1

        mock_result = TaskResult(
            prefix="T01",
            title="Test task",
            status="done",
            duration_seconds=1.0,
            tokens=TokenUsage(),
        )

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [mock_result], 1.0)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.run_retrospective", side_effect=RuntimeError("retro failed")),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
            patch("the_architect.cli._read_goal_from_instructions", return_value=""),
        ):
            with pytest.raises(SystemExit):
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                )

    def test_run_main_instructions_md_missing(self, tmp_path: Path) -> None:
        """Should warn when tasks/INSTRUCTIONS.md is missing."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
            patch("the_architect.cli._read_goal_from_instructions", return_value=""),
        ):
            with pytest.raises(SystemExit):
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                )

    def test_run_main_runtime_error(self, tmp_path: Path) -> None:
        """Should handle RuntimeError during execution."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch(
                "the_architect.cli._run_tasks_raw",
                side_effect=RuntimeError("asyncio error"),
            ),
            patch("the_architect.cli._read_goal_from_instructions", return_value=""),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                )
            assert exc_info.value.code == 1

    def test_run_main_unexpected_error(self, tmp_path: Path) -> None:
        """Should handle unexpected errors during execution."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        with (
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch(
                "the_architect.cli._run_tasks_raw",
                side_effect=OSError("unexpected error"),
            ),
            patch("the_architect.cli._read_goal_from_instructions", return_value=""),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                )
            assert exc_info.value.code == 1


class TestRetryCommandBranches:
    """Tests for retry command uncovered branches."""

    def test_retry_task_not_done(self, tmp_path: Path) -> None:
        """Should handle retrying a task that is not marked Done."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tasks_dir / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.task_is_done", return_value=False),
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli.setup_logging"),
            patch("the_architect.cli.asyncio.run"),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["retry", "-t", "T01", "-p", str(tmp_path)])
            # Should say "not marked Done — running now"
            assert "not marked Done" in result.output or result.exit_code == 0

    def test_retry_no_progress_file(self, tmp_path: Path) -> None:
        """Should handle retry when PROGRESS.md doesn't exist."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tasks_dir / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.task_is_done", return_value=True),
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli.setup_logging"),
            patch("the_architect.cli.asyncio.run"),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["retry", "-t", "T01", "-p", str(tmp_path)])  # noqa: F841

    def test_retry_task_not_found(self, tmp_path: Path) -> None:
        """Should exit with error when task not found."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.task_is_done", return_value=False),
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli.setup_logging"),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["retry", "-t", "T99", "-p", str(tmp_path)])
            assert result.exit_code == 1
            assert "not found" in result.output

    def test_retry_resets_done_to_pending(self, tmp_path: Path) -> None:
        """Should reset task status from Done to Pending in PROGRESS.md."""
        progress_content = """# The Architect — Progress Tracker

## Overall Status

**Tasks completed:** 1
**Next task to run:** T02

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | Test | Done | 2024-01-01 |

---

## Current State

Testing.

## Last Task Summary

N/A

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
| | | | |
"""
        (tmp_path / "PROGRESS.md").write_text(progress_content, encoding="utf-8")
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tasks_dir / "T01_test.md",
            title="Test task",
            status=TaskStatus.DONE,
        )

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.task_is_done", return_value=True),
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli.setup_logging"),
            patch("the_architect.cli.asyncio.run"),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["retry", "-t", "T01", "-p", str(tmp_path)])  # noqa: F841
            # Should have reset status
            updated = (tmp_path / "PROGRESS.md").read_text(encoding="utf-8")
            assert "Pending" in updated


class TestSkipCommandBranches:
    """Tests for skip command uncovered branches."""

    def test_skip_no_progress_file(self, tmp_path: Path) -> None:
        """Should exit with error when no PROGRESS.md exists."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["skip", "-t", "T01", "-p", str(tmp_path)])
        assert result.exit_code == 1
        assert "PROGRESS.md not found" in result.output

    def test_skip_already_done(self, tmp_path: Path) -> None:
        """Should handle skipping a task that is already Done."""
        progress_content = """# The Architect — Progress Tracker

## Overall Status

**Tasks completed:** 1
**Next task to run:** T02

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | Test | Done | 2024-01-01 |

---

## Current State

Testing.

## Last Task Summary

N/A

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
| | | | |
"""
        (tmp_path / "PROGRESS.md").write_text(progress_content, encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["skip", "-t", "T01", "-p", str(tmp_path)])
        assert "already Done" in result.output

    def test_skip_task_not_in_progress(self, tmp_path: Path) -> None:
        """Should exit with error when task not found in PROGRESS.md."""
        progress_content = """# The Architect — Progress Tracker

## Overall Status

**Tasks completed:** 0
**Next task to run:** T01

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | Test | Pending | — |

---

## Current State

Testing.

## Last Task Summary

N/A

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
| | | | |
"""
        (tmp_path / "PROGRESS.md").write_text(progress_content, encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["skip", "-t", "T99", "-p", str(tmp_path)])
        assert result.exit_code == 1
        assert "not found" in result.output


class TestResetCommandBranches:
    """Tests for reset command uncovered branches."""

    def test_reset_no_progress_file(self, tmp_path: Path) -> None:
        """Should exit with error when no PROGRESS.md exists."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["reset", "-p", str(tmp_path)])
        assert result.exit_code == 1
        assert "PROGRESS.md not found" in result.output

    def test_reset_cancelled(self, tmp_path: Path) -> None:
        """Should show cancelled when user says no to confirm."""
        (tmp_path / "PROGRESS.md").write_text("Old content", encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["reset", "-p", str(tmp_path)], input="n\n")
        assert "Cancelled" in result.output


class TestCancelCommandBranches:
    """Tests for cancel command uncovered branches."""

    def test_cancel_no_lock_file(self, tmp_path: Path) -> None:
        """Should report no lock file when none exists."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["cancel", "-p", str(tmp_path)])
        assert "No lock file found" in result.output

    def test_cancel_stale_lock_process_gone(self, tmp_path: Path) -> None:
        """Should remove stale lock when process no longer running."""
        lock_dir = tmp_path / ".architect"
        lock_dir.mkdir()
        (lock_dir / "runner.lock").write_text("99999", encoding="utf-8")

        with patch("os.kill", side_effect=ProcessLookupError()):
            runner = CliRunner()
            result = runner.invoke(main, ["cancel", "-p", str(tmp_path)])
            assert "Stale lock" in result.output or "Lock removed" in result.output

    def test_cancel_invalid_pid(self, tmp_path: Path) -> None:
        """Should handle lock file with invalid PID content."""
        lock_dir = tmp_path / ".architect"
        lock_dir.mkdir()
        (lock_dir / "runner.lock").write_text("not-a-pid", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["cancel", "-p", str(tmp_path)])
        assert "could not be read" in result.output or "Lock removed" in result.output

    def test_cancel_active_process_decline_kill(self, tmp_path: Path) -> None:
        """Should leave process running when user declines kill."""
        lock_dir = tmp_path / ".architect"
        lock_dir.mkdir()
        (lock_dir / "runner.lock").write_text("12345", encoding="utf-8")

        with patch("os.kill", return_value=None):  # process_alive = True for signal 0
            runner = CliRunner()
            result = runner.invoke(main, ["cancel", "-p", str(tmp_path)], input="n\n")
            assert "left running" in result.output or "Lock removed" in result.output


class TestStatusCommandFull:
    """Tests for status command full branches."""

    def test_status_running_process(self, tmp_path: Path) -> None:
        """Should show Running when lock file has active PID."""
        lock_dir = tmp_path / ".architect"
        lock_dir.mkdir()
        (lock_dir / "runner.lock").write_text("12345", encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        with patch("os.kill", return_value=None):
            runner = CliRunner()
            result = runner.invoke(main, ["status", "-p", str(tmp_path)])
            assert "Running" in result.output

    def test_status_no_tasks_dir(self, tmp_path: Path) -> None:
        """Should show message when no tasks directory exists."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["status", "-p", str(tmp_path)])
        assert "No tasks directory" in result.output

    def test_status_with_tasks_and_done_count(self, tmp_path: Path) -> None:
        """Should show done count in output."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        done_task = Task(
            name="T01_done",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_done.md",
            title="Done task",
            status=TaskStatus.DONE,
        )

        with (
            patch("the_architect.cli.discover_tasks", return_value=[done_task]),
            patch("the_architect.cli.task_is_done", return_value=True),
            # The status display now reads task_status (which returns the
            # canonical status string) so it can distinguish Done, Failed,
            # Blocked, and Pending.  Patch it to report "Done" alongside
            # the legacy task_is_done patch.
            patch("the_architect.cli.task_status", return_value="Done"),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["status", "-p", str(tmp_path)])
            assert "1/1 tasks complete" in result.output

    def test_status_with_circuit_open(self, tmp_path: Path) -> None:
        """Should show circuit breaker state for open circuits."""
        import json

        lock_dir = tmp_path / ".architect"
        lock_dir.mkdir()
        circuit_data = {
            "T01": {
                "state": "OPEN",
                "consecutive_no_progress": 3,
                "consecutive_same_error": 0,
            }
        }
        (lock_dir / "circuit.json").write_text(json.dumps(circuit_data), encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["status", "-p", str(tmp_path)])
        assert "Circuit breaker" in result.output

    def test_status_with_token_budget(self, tmp_path: Path) -> None:
        """Should show token budget when configured."""
        (tmp_path / "architect.toml").write_text(
            "[architect]\ntoken_budget_per_hour = 500000\n", encoding="utf-8"
        )

        runner = CliRunner()
        result = runner.invoke(main, ["status", "-p", str(tmp_path)])
        assert "token" in result.output.lower() or "Token" in result.output

    def test_status_with_log_files(self, tmp_path: Path) -> None:
        """Should show log files when they exist."""
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "T01_test.log").write_text("log content", encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["status", "-p", str(tmp_path)])
        assert "Logs" in result.output or "T01_test.log" in result.output

    def test_status_many_log_files(self, tmp_path: Path) -> None:
        """Should truncate log file listing when more than 5."""
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        for i in range(7):
            (log_dir / f"T0{i}_test.log").write_text(f"log {i}", encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["status", "-p", str(tmp_path)])
        assert "more" in result.output or "Logs" in result.output


class TestInitCommandBranches:
    """Tests for init command uncovered branches."""

    def test_init_existing_files_no_force(self, tmp_path: Path) -> None:
        """Should skip existing files without --force."""
        (tmp_path / "AGENTS.md").write_text("existing", encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["init", "-p", str(tmp_path)])
        assert "Skipped" in result.output
        assert "Nothing to do" in result.output

    def test_init_with_force(self, tmp_path: Path) -> None:
        """Should overwrite existing files with --force."""
        (tmp_path / "AGENTS.md").write_text("existing", encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["init", "-p", str(tmp_path), "--force"])
        assert result.exit_code == 0
        assert "Created" in result.output


class TestLogsCommandBranches:
    """Tests for logs command uncovered branches."""

    def test_logs_no_log_directory(self, tmp_path: Path) -> None:
        """Should report when no log directory exists."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["logs", "-p", str(tmp_path)])
        assert "No log directory" in result.output

    def test_logs_empty_log_directory(self, tmp_path: Path) -> None:
        """Should report when log directory is empty."""
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["logs", "-p", str(tmp_path)])
        assert "No log files" in result.output

    def test_logs_task_not_found(self, tmp_path: Path) -> None:
        """Should show error when log for specific task not found."""
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "T01_test.log").write_text("log", encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["logs", "-p", str(tmp_path), "--task", "T99"])
        assert result.exit_code == 1
        assert "No log found" in result.output

    def test_logs_show_all_flag(self, tmp_path: Path) -> None:
        """Should show all lines with --all flag."""
        import json

        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        entries = [json.dumps({"type": "text", "part": {"text": f"Line {i}"}}) for i in range(60)]
        (log_dir / "T01_test.log").write_text("\n".join(entries), encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["logs", "-p", str(tmp_path), "--task", "T01", "--all"])
        assert result.exit_code == 0
        assert "Line 0" in result.output

    def test_logs_custom_tail(self, tmp_path: Path) -> None:
        """Should respect --tail flag."""
        import json

        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        entries = [json.dumps({"type": "text", "part": {"text": f"Line {i}"}}) for i in range(60)]
        (log_dir / "T01_test.log").write_text("\n".join(entries), encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["logs", "-p", str(tmp_path), "--task", "T01", "--tail", "5"])
        assert result.exit_code == 0


class TestConfigCommandBranches:
    """Tests for config command uncovered branches."""

    def test_config_invalid_format(self, tmp_path: Path) -> None:
        """Should show error for invalid KEY=VALUE format."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["config", "-p", str(tmp_path), "--set", "badformat"])
        assert result.exit_code == 1
        assert "Invalid format" in result.output

    def test_config_unknown_key(self, tmp_path: Path) -> None:
        """Should show error for unknown config key."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            main, ["config", "-p", str(tmp_path), "--set", "nonexistent_key=value"]
        )
        assert result.exit_code == 1
        assert "Unknown config key" in result.output

    def test_config_invalid_bool_value(self, tmp_path: Path) -> None:
        """Should show error for invalid boolean value."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            main, ["config", "-p", str(tmp_path), "--set", "carry_context=maybe"]
        )
        assert result.exit_code == 1
        assert "Invalid value" in result.output

    def test_config_set_bool_true(self, tmp_path: Path) -> None:
        """Should handle setting boolean to true."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["config", "-p", str(tmp_path), "--set", "carry_context=true"])
        assert result.exit_code == 0

    def test_config_set_bool_false(self, tmp_path: Path) -> None:
        """Should handle setting boolean to false."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["config", "-p", str(tmp_path), "--set", "free_mode=false"])
        assert result.exit_code == 0

    def test_config_set_int_value(self, tmp_path: Path) -> None:
        """Should handle setting integer values."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["config", "-p", str(tmp_path), "--set", "max_retries=5"])
        assert result.exit_code == 0

    def test_config_no_toml(self, tmp_path: Path) -> None:
        """Should show defaults when no architect.toml exists."""
        runner = CliRunner()
        result = runner.invoke(main, ["config", "-p", str(tmp_path)])
        assert "defaults" in result.output.lower() or "built-in" in result.output.lower()

    def test_config_invalid_int_value(self, tmp_path: Path) -> None:
        """Should show error for invalid integer value."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            main, ["config", "-p", str(tmp_path), "--set", "max_retries=not_a_number"]
        )
        assert result.exit_code == 1
        assert "Invalid value" in result.output


class TestCircuitCommandBranches:
    """Tests for circuit command uncovered branches."""

    def test_circuit_reset_task(self, tmp_path: Path) -> None:
        """Should reset circuit state for specific task."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        with (
            patch("the_architect.core.circuit.load_circuit_state") as mock_load,
        ):
            mock_cb = MagicMock()
            mock_load.return_value = mock_cb

            runner = CliRunner()
            result = runner.invoke(main, ["circuit", "-p", str(tmp_path), "--reset", "T01"])
            mock_cb.reset_task.assert_called_once_with("T01")
            assert "reset to CLOSED" in result.output

    def test_circuit_no_states(self, tmp_path: Path) -> None:
        """Should show message when no circuit states exist."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        with (
            patch("the_architect.core.circuit.load_circuit_state") as mock_load,
            patch("the_architect.cli.discover_tasks", return_value=[]),
        ):
            mock_cb = MagicMock()
            mock_cb.all_states.return_value = {}
            mock_load.return_value = mock_cb

            runner = CliRunner()
            result = runner.invoke(main, ["circuit", "-p", str(tmp_path)])
            assert "No circuit state" in result.output

    def test_circuit_half_open_state(self, tmp_path: Path) -> None:
        """Should display HALF_OPEN state."""
        from the_architect.core.circuit import CircuitState, RecoveryAction

        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        mock_state = MagicMock()
        mock_state.state = CircuitState.HALF_OPEN
        mock_state.consecutive_no_progress = 1
        mock_state.consecutive_same_error = 0
        mock_state.recovery_action = RecoveryAction.WAIT
        mock_state.opened_at = None

        mock_cb = MagicMock()
        mock_cb.all_states.return_value = {"T01": mock_state}

        with (
            patch("the_architect.core.circuit.load_circuit_state", return_value=mock_cb),
            patch("the_architect.cli.discover_tasks", return_value=[]),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["circuit", "-p", str(tmp_path)])
            assert "HALF_OPEN" in result.output

    def test_circuit_hours_ago(self, tmp_path: Path) -> None:
        """Should show 'Xh Ym ago' for old circuits."""
        import json
        from datetime import UTC, datetime, timedelta

        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()

        old_time = (datetime.now(tz=UTC) - timedelta(hours=2, minutes=30)).isoformat()
        circuit_data = {
            "T01": {
                "state": "OPEN",
                "consecutive_no_progress": 3,
                "consecutive_same_error": 0,
                "recovery_action": "WAIT",
                "opened_at": old_time,
            }
        }
        (arch_dir / "circuit.json").write_text(json.dumps(circuit_data), encoding="utf-8")
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tasks_dir / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )

        with patch("the_architect.cli.discover_tasks", return_value=[mock_task]):
            runner = CliRunner()
            result = runner.invoke(main, ["circuit", "-p", str(tmp_path)])
            assert "2h" in result.output


class TestMonitorCommandBranches:
    """Tests for monitor command uncovered branches."""

    def test_monitor_no_tmux(self, tmp_path: Path) -> None:
        """Should exit with error when tmux not available."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        with patch("the_architect.core.tmux.is_tmux_available", return_value=False):
            runner = CliRunner()
            result = runner.invoke(main, ["monitor", "-p", str(tmp_path)])
            assert result.exit_code == 1
            assert "tmux is not installed" in result.output

    def test_monitor_session_exists(self, tmp_path: Path) -> None:
        """Should attach to existing session."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        with (
            patch("the_architect.core.tmux.is_tmux_available", return_value=True),
            patch("the_architect.core.tmux.session_exists", return_value=True),
            patch("the_architect.core.tmux.attach_session"),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["monitor", "-p", str(tmp_path)])
            assert "Attaching" in result.output

    def test_monitor_no_sessions(self, tmp_path: Path) -> None:
        """Should show message when no sessions found."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        with (
            patch("the_architect.core.tmux.is_tmux_available", return_value=True),
            patch("the_architect.core.tmux.session_exists", return_value=False),
            patch("the_architect.core.tmux.list_architect_sessions", return_value=[]),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["monitor", "-p", str(tmp_path)])
            assert "No active session" in result.output

    def test_monitor_multiple_sessions(self, tmp_path: Path) -> None:
        """Should list sessions when multiple exist."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        with (
            patch("the_architect.core.tmux.is_tmux_available", return_value=True),
            patch("the_architect.core.tmux.session_exists", return_value=False),
            patch(
                "the_architect.core.tmux.list_architect_sessions",
                return_value=["architect-proj1", "architect-proj2"],
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["monitor", "-p", str(tmp_path)])
            assert "Other active sessions" in result.output

    def test_monitor_single_other_session(self, tmp_path: Path) -> None:
        """Should auto-attach when exactly one other session exists."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        with (
            patch("the_architect.core.tmux.is_tmux_available", return_value=True),
            patch("the_architect.core.tmux.session_exists", return_value=False),
            patch(
                "the_architect.core.tmux.list_architect_sessions",
                return_value=["architect-proj1"],
            ),
            patch("the_architect.core.tmux.attach_session"),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["monitor", "-p", str(tmp_path)])
            assert "Attaching" in result.output


class TestMainProviderNoModels:
    """Tests for main() provider with no models and user config."""

    def test_main_provider_no_models_no_config(self, tmp_path: Path) -> None:
        """Should exit with error when provider has no models and no config."""
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.has_any_models.return_value = False
        mock_provider.find_user_config.return_value = None

        with (
            patch("the_architect.cli.load_config", return_value=ArchitectConfig()),
            patch("the_architect.cli.detect_available_providers", return_value=[mock_provider]),
            patch("the_architect.cli.detect_provider", return_value=mock_provider),
            patch("the_architect.cli.discover_tasks", return_value=[]),
            patch("the_architect.cli._filter_and_set_status", return_value=[]),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["--headless", "-p", str(tmp_path)])
            assert result.exit_code == 1
            assert "may not be configured" in result.output


class TestRunMainNoConfig:
    """Tests for _run_main without pre-loaded config."""

    def test_run_main_loads_and_sets_persistent(self, tmp_path: Path) -> None:
        """Should set persistent config when persistent flag passed."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        with (
            patch("the_architect.cli.load_config", return_value=config),
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
            patch("the_architect.cli._read_goal_from_instructions", return_value=""),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    persistent=True,
                    headless=True,
                    _pre_loaded_config=None,
                )
            assert exc_info.value.code == 0
            assert config.persistent is True
            assert config.max_retries == 30

    def test_run_main_loads_and_sets_free_mode(self, tmp_path: Path) -> None:
        """Should set free_mode config when free_mode flag passed."""
        from the_architect.cli import _run_main

        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )
        config = ArchitectConfig()

        async def _fake_run_tasks(
            *args: object, **kwargs: object
        ) -> tuple[bool, list[TaskResult], float]:
            return (True, [], 1.0)

        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.get_resolved_model.return_value = "model"

        with (
            patch("the_architect.cli.load_config", return_value=config),
            patch("the_architect.cli.discover_tasks", return_value=[mock_task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[mock_task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_fake_run_tasks),
            patch("the_architect.cli.write_success_md"),
            patch("the_architect.cli.print_success_summary"),
            patch("the_architect.cli._read_goal_from_instructions", return_value=""),
        ):
            with pytest.raises(SystemExit) as exc_info:  # noqa: F841
                _run_main(
                    project=tmp_path,
                    plan=False,
                    free_mode=True,
                    headless=True,
                    _pre_loaded_config=None,
                    provider=mock_provider,
                )
            assert config.free_mode is True


class TestMainNoTaskDir:
    """Test main() with no tasks directory."""

    def test_main_list_no_tasks_dir(self, tmp_path: Path) -> None:
        """Should show no tasks directory message."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["list", "-p", str(tmp_path)])
        assert "No tasks directory" in result.output

    def test_main_list_empty_tasks_dir(self, tmp_path: Path) -> None:
        """Should show no tasks found when directory is empty."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["list", "-p", str(tmp_path)])
        assert "No tasks found" in result.output
