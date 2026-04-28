"""Comprehensive tests for the_architect.cli to reach 70%+ coverage.

This file contains tests for helper functions, Click commands, and execution modes.
Tests are organized by coverage priority and mock external dependencies heavily.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from the_architect.cli import main


class TestHelperFunctions:
    """Tests for helper functions in CLI."""

    def test_filter_and_set_status_with_progress(self, tmp_path: Path) -> None:
        """Should return tasks with status based on PROGRESS.md."""
        from the_architect.cli import _filter_and_set_status
        from the_architect.core.tasks import Task, TaskStatus

        # Create mock tasks
        task1 = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task 1",
            status=TaskStatus.PENDING,
        )
        task2 = Task(
            name="T02_test",
            prefix="T02",
            number=2,
            path=tmp_path / "T02_test.md",
            title="Test task 2",
            status=TaskStatus.PENDING,
        )

        # Create PROGRESS.md with T01 marked as Done
        progress_content = """# The Architect — Progress Tracker

| Task | Title | Status | Estimated
|------|-------|--------|-----------
| T01 | Test task 1 | Done | 10m
| T02 | Test task 2 | Pending | 15m
"""
        progress_file = tmp_path / "PROGRESS.md"
        progress_file.write_text(progress_content, encoding="utf-8")

        result = _filter_and_set_status([task1, task2], progress_file)

        assert len(result) == 2
        assert result[0].status == TaskStatus.DONE
        assert result[1].status == TaskStatus.PENDING

    def test_filter_and_set_status_empty_tasks(self, tmp_path: Path) -> None:
        """Should return empty list when tasks list is empty."""
        from the_architect.cli import _filter_and_set_status

        progress_file = tmp_path / "PROGRESS.md"
        result = _filter_and_set_status([], progress_file)

        assert len(result) == 0

    def test_filter_and_set_status_nonexistent_progress(self, tmp_path: Path) -> None:
        """Should return tasks unchanged when PROGRESS.md doesn't exist."""
        from the_architect.cli import _filter_and_set_status
        from the_architect.core.tasks import Task, TaskStatus

        task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )

        progress_file = tmp_path / "PROGRESS.md"
        result = _filter_and_set_status([task], progress_file)

        assert len(result) == 1
        assert result[0].status == TaskStatus.PENDING

    def test_opencode_has_any_models_true(self) -> None:
        """Should return True when provider has models."""
        from the_architect.cli import _opencode_has_any_models

        mock_provider = MagicMock()
        mock_provider.has_any_models.return_value = True

        with patch(
            "the_architect.core.opencode_provider.OpenCodeProvider",
            return_value=mock_provider,
        ):
            result = _opencode_has_any_models()
            assert result is True

    def test_opencode_has_any_models_false(self) -> None:
        """Should return False when provider has no models."""
        from the_architect.cli import _opencode_has_any_models

        mock_provider = MagicMock()
        mock_provider.has_any_models.return_value = False

        with patch(
            "the_architect.core.opencode_provider.OpenCodeProvider",
            return_value=mock_provider,
        ):
            result = _opencode_has_any_models()
            assert result is False

    def test_opencode_has_any_models_exception(self) -> None:
        """Should return False when exception is raised."""
        from the_architect.cli import _opencode_has_any_models

        with patch(
            "the_architect.core.opencode_provider.OpenCodeProvider",
            side_effect=Exception("test error"),
        ):
            result = _opencode_has_any_models()
            assert result is False

    def test_setup_loguru(self) -> None:
        """Should configure loguru logger without raising."""
        from the_architect.cli import _setup_loguru

        # Loguru does not expose a public handler registry — just verify the
        # call is idempotent and does not raise.
        _setup_loguru()
        _setup_loguru()

    def test_fmt_duration_zero(self) -> None:
        """Should format 0 seconds correctly."""
        from the_architect.cli import _fmt_duration

        result = _fmt_duration(0)
        assert result == "0:00"

    def test_fmt_duration_minutes(self) -> None:
        """Should format duration with minutes."""
        from the_architect.cli import _fmt_duration

        result = _fmt_duration(65)
        assert result == "1:05"

    def test_fmt_duration_hours(self) -> None:
        """Should format duration with hours."""
        from the_architect.cli import _fmt_duration

        result = _fmt_duration(3661)
        assert result == "1:01:01"

    def test_fmt_duration_exact_hour(self) -> None:
        """Should format exact hour."""
        from the_architect.cli import _fmt_duration

        result = _fmt_duration(3600)
        assert result == "1:00:00"

    def test_fmt_duration_under_hour(self) -> None:
        """Should format duration under hour."""
        from the_architect.cli import _fmt_duration

        result = _fmt_duration(59)
        assert result == "0:59"

    def test_read_goal_from_instructions_with_goal(self, tmp_path: Path) -> None:
        """Should extract goal from INSTRUCTIONS.md."""
        from the_architect.cli import _read_goal_from_instructions

        instructions = tmp_path / "INSTRUCTIONS.md"
        instructions.write_text(
            """# Instructions

## Goal

Build a CLI tool.

## Context

Details here.
""",
            encoding="utf-8",
        )

        result = _read_goal_from_instructions(tmp_path)
        assert "Build a CLI tool" in result

    def test_read_goal_from_instructions_no_goal(self, tmp_path: Path) -> None:
        """Should return empty string when no Goal section exists."""
        from the_architect.cli import _read_goal_from_instructions

        instructions = tmp_path / "INSTRUCTIONS.md"
        instructions.write_text(
            """# Instructions

No goal section here.
""",
            encoding="utf-8",
        )

        result = _read_goal_from_instructions(tmp_path)
        assert result == ""

    def test_read_goal_from_instructions_nonexistent(self, tmp_path: Path) -> None:
        """Should return empty string when INSTRUCTIONS.md doesn't exist."""
        from the_architect.cli import _read_goal_from_instructions

        result = _read_goal_from_instructions(tmp_path)
        assert result == ""

    def test_countdown_zero_seconds(self) -> None:
        """Should return immediately when seconds <= 0."""
        from the_architect.cli import _countdown

        with patch("time.sleep") as mock_sleep:
            _countdown(0)
            mock_sleep.assert_not_called()

    def test_countdown_non_tty(self) -> None:
        """Should sleep without ANSI display when not a TTY."""
        from the_architect.cli import _countdown

        with patch("the_architect.cli._ansi_supported", return_value=False):
            with patch("time.sleep") as mock_sleep:
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
            mock_write.assert_not_called()

    def test_alternate_screen_non_tty(self) -> None:
        """Should be a no-op when stdout is not a TTY."""
        from the_architect.cli import alternate_screen

        with (
            patch("sys.stdout.isatty", return_value=False),
            patch("sys.stdout.write") as mock_write,
        ):
            with alternate_screen():
                pass
            mock_write.assert_not_called()

    def test_maybe_kill_own_tmux_session_not_in_tmux(self) -> None:
        """Should do nothing when not in tmux."""
        from the_architect.cli import _maybe_kill_own_tmux_session

        with patch.dict("os.environ", {}, clear=True):
            _maybe_kill_own_tmux_session(Path("/fake"))
            # No exception raised

    def test_maybe_kill_own_tmux_session_different_session(self, tmp_path: Path) -> None:
        """Should do nothing when in different tmux session."""
        from the_architect.cli import _maybe_kill_own_tmux_session

        with patch.dict("os.environ", {"TMUX": "%1,234,5"}):
            _maybe_kill_own_tmux_session(tmp_path)
            # No exception raised

    def test_maybe_kill_own_tmux_session_matching(self, tmp_path: Path) -> None:
        """Should kill matching tmux session when names align."""
        from the_architect.cli import _maybe_kill_own_tmux_session

        with (
            patch.dict("os.environ", {"TMUX": "/tmp/tmux-1000/default,1234,0"}),
            patch("subprocess.run") as mock_run,
            patch("the_architect.core.tmux.get_session_name") as mock_get_name,
            patch("the_architect.core.tmux.session_exists", return_value=True) as mock_exists,
            patch("the_architect.core.tmux.kill_session") as mock_kill,
        ):
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "architect-proj\n"
            mock_get_name.return_value = "architect-proj"

            _maybe_kill_own_tmux_session(tmp_path)

            mock_exists.assert_called_once()
            mock_kill.assert_called_once_with("architect-proj")


class TestClickCommands:
    """Tests for Click commands in the CLI."""

    def test_version_subcommand(self) -> None:
        """Should display version information."""
        runner = CliRunner()
        result = runner.invoke(main, ["version"])
        assert result.exit_code == 0
        assert "architect v" in result.output

    def test_version_flag(self) -> None:
        """Should display the version via the ``--version`` flag."""
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "architect v" in result.output

    def test_version_flag_short(self) -> None:
        """Should display the version via the ``-V`` short flag."""
        runner = CliRunner()
        result = runner.invoke(main, ["-V"])
        assert result.exit_code == 0
        assert "architect v" in result.output

    def test_help_flag(self) -> None:
        """Should display help with --help flag."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "The Architect" in result.output

    def test_init_command(self, tmp_path: Path) -> None:
        """Should create AGENTS.md and architect.toml."""
        runner = CliRunner()
        result = runner.invoke(main, ["init", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / "AGENTS.md").exists()
        assert (tmp_path / "architect.toml").exists()

    def test_init_command_skip_existing(self, tmp_path: Path) -> None:
        """Should skip existing files without --force."""
        runner = CliRunner()

        # First run
        result = runner.invoke(main, ["init", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / "AGENTS.md").exists()

        # Second run without --force
        result = runner.invoke(main, ["init", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert "already exists" in result.output

    def test_init_command_force(self, tmp_path: Path) -> None:
        """Should overwrite existing files with --force."""
        runner = CliRunner()

        # First run
        result = runner.invoke(main, ["init", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / "AGENTS.md").exists()

        # Second run with --force must succeed even though the files exist.
        result = runner.invoke(main, ["init", "--force", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / "AGENTS.md").exists()

    def test_list_command_no_tasks_dir(self, tmp_path: Path) -> None:
        """Should show error when no tasks directory found."""
        runner = CliRunner()
        result = runner.invoke(main, ["list", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert "No tasks directory found" in result.output

    def test_list_command_with_tasks(self, tmp_path: Path) -> None:
        """Should display table with tasks."""

        # Create tasks directory
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        # Create a task file
        task_file = tasks_dir / "T01_test_task.md"
        task_file.write_text("# T01: Test task\n\nContent here.", encoding="utf-8")

        # Create PROGRESS.md
        progress_file = tasks_dir / "PROGRESS.md"
        progress_file.write_text(
            """# The Architect — Progress Tracker

| Task | Title | Status | Estimated
|------|-------|--------|-----------
| T01 | Test task | Done | 10m
""",
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(main, ["list", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert "T01" in result.output

    def test_skip_command(self, tmp_path: Path) -> None:
        """Should mark task as Done in PROGRESS.md."""
        # PROGRESS.md lives at the project root per ArchitectConfig defaults.
        progress_file = tmp_path / "PROGRESS.md"
        progress_file.write_text(
            """# The Architect — Progress Tracker

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | Test task | Pending | — |
""",
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(main, ["skip", "-t", "T01", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output

        # Check that task was marked as Done
        updated_content = progress_file.read_text(encoding="utf-8")
        assert "Done" in updated_content

    def test_skip_command_not_found(self, tmp_path: Path) -> None:
        """Should fail when task not found in PROGRESS.md."""
        # Create an empty PROGRESS.md so the command runs past the
        # "file missing" guard and can fail on the lookup.
        progress_file = tmp_path / "PROGRESS.md"
        progress_file.write_text(
            """# The Architect — Progress Tracker

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
""",
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(main, ["skip", "-t", "T99", "-p", str(tmp_path)])
        assert result.exit_code == 1

    def test_reset_command(self, tmp_path: Path) -> None:
        """Should reset PROGRESS.md at project root when user confirms."""
        progress_file = tmp_path / "PROGRESS.md"
        progress_file.write_text(
            """# The Architect — Progress Tracker

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | Test task | Done | 10m |
""",
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(main, ["reset", "-p", str(tmp_path)], input="y\n")
        assert result.exit_code == 0, result.output

        # All Done rows should have been wiped out by the reset template.
        updated_content = progress_file.read_text(encoding="utf-8")
        assert "| T01  | Test task | Done" not in updated_content

    def test_reset_command_cancel(self, tmp_path: Path) -> None:
        """Should cancel reset when user declines."""
        progress_file = tmp_path / "PROGRESS.md"
        progress_file.write_text(
            """# The Architect — Progress Tracker

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | Test task | Done | 10m |
""",
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(main, ["reset", "-p", str(tmp_path)], input="n\n")
        assert result.exit_code == 0, result.output
        assert "Cancelled" in result.output

    def test_cancel_command_no_lock(self, tmp_path: Path) -> None:
        """Should show error when no lock file found."""
        runner = CliRunner()
        result = runner.invoke(main, ["cancel", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert "No lock file found" in result.output

    def test_status_command_no_tasks_dir(self, tmp_path: Path) -> None:
        """Should show error when no tasks directory found."""
        runner = CliRunner()
        result = runner.invoke(main, ["status", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert "No tasks directory found" in result.output

    def test_config_show_command(self, tmp_path: Path) -> None:
        """Should display current config values."""
        runner = CliRunner()
        result = runner.invoke(main, ["config", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert "max_retries" in result.output

    def test_config_set_command(self, tmp_path: Path) -> None:
        """Should update config values."""
        runner = CliRunner()
        result = runner.invoke(main, ["config", "-p", str(tmp_path), "--set", "max_retries=5"])
        assert result.exit_code == 0

        # Verify config file was created
        config_file = tmp_path / "architect.toml"
        assert config_file.exists()

    def test_config_set_invalid_key(self, tmp_path: Path) -> None:
        """Should fail when config key is unknown."""
        runner = CliRunner()
        result = runner.invoke(main, ["config", "-p", str(tmp_path), "--set", "unknown_key=5"])
        assert result.exit_code == 1
        assert "Unknown config key" in result.output

    def test_logs_command_no_log_dir(self, tmp_path: Path) -> None:
        """Should show error when no log directory found."""
        runner = CliRunner()
        result = runner.invoke(main, ["logs", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert "No log directory found" in result.output

    def test_logs_command_with_logs(self, tmp_path: Path) -> None:
        """Should display log files."""
        # Create log directory
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)

        # Create log file
        log_file = log_dir / "T01_test_task.log"
        log_file.write_text("Log content here.", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["logs", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert "T01_test_task.log" in result.output

    def test_circuit_command_no_state(self, tmp_path: Path) -> None:
        """Should show error when no circuit state found."""
        runner = CliRunner()
        result = runner.invoke(main, ["circuit", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert "No circuit state found" in result.output

    def test_monitor_command(self, tmp_path: Path) -> None:
        """Should run ``architect monitor`` without crashing.

        tmux may or may not be available in the environment — both paths
        are acceptable here.  The contract is simply that the command
        exits cleanly.
        """
        runner = CliRunner()
        result = runner.invoke(main, ["monitor", "-p", str(tmp_path)])
        # The command must not have raised an uncaught exception.
        assert result.exception is None or isinstance(result.exception, SystemExit)


class TestExecutionModes:
    """Tests for execution mode branches."""

    def test_main_headless_mode(self, tmp_path: Path) -> None:
        """Should run in headless mode when --headless flag is set."""
        from the_architect.core.tasks import TaskStatus

        # Mock provider
        mock_provider = MagicMock()
        mock_provider.display_name = "OpenCode"
        mock_provider.name = "opencode"
        mock_provider.get_resolved_model.return_value = "default-model"
        mock_provider.ensure_setup.return_value = None
        mock_provider.supports_free_tier.return_value = False
        mock_provider.find_user_config.return_value = tmp_path / "config"

        # Mock tasks
        mock_task = MagicMock()
        mock_task.prefix = "T01"
        mock_task.status = TaskStatus.PENDING

        with patch(
            "the_architect.cli.load_config",
            return_value=MagicMock(),
        ):
            with patch(
                "the_architect.cli.detect_provider",
                return_value=mock_provider,
            ):
                with patch(
                    "the_architect.cli.discover_tasks",
                    return_value=[mock_task],
                ):
                    with patch(
                        "the_architect.cli.run_all",
                        return_value=MagicMock(),
                    ):
                        with patch("the_architect.cli.setup_logging"):
                            runner = CliRunner()
                            result = runner.invoke(main, ["--headless", "-p", str(tmp_path)])
                            # Should complete cleanly in headless mode — either
                            # a normal SystemExit or no exception at all.
                            assert result.exception is None or isinstance(
                                result.exception, SystemExit
                            )
