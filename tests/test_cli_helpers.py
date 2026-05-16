"""Tests for helper functions in the_architect.cli."""

from pathlib import Path
from unittest.mock import patch

from the_architect.core.tasks import Task, TaskStatus


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

        # PROGRESS.md format is the canonical Markdown table that
        # ``_task_status_pattern`` in progress.py parses.
        progress_file = tmp_path / "PROGRESS.md"
        progress_file.write_text(
            """# The Architect — Progress Tracker

**Tasks completed:** 2
**Next task to run:** T03

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | First  | Done    | 2024-01-01 |
| T02  | Second | Done    | 2024-01-02 |
| T03  | Third  | Pending | — |
| T04  | Fourth | Pending | — |
""",
            encoding="utf-8",
        )

        # Create task objects
        task1 = Task(name="T01", prefix="T01", number=1, path=tmp_path / "T01.md")
        task2 = Task(name="T02", prefix="T02", number=2, path=tmp_path / "T02.md")
        task3 = Task(name="T03", prefix="T03", number=3, path=tmp_path / "T03.md")
        task4 = Task(name="T04", prefix="T04", number=4, path=tmp_path / "T04.md")

        # Call the function
        result = _filter_and_set_status([task1, task2, task3, task4], progress_file)

        # Check results
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
        """Should configure loguru without raising and be safe to re-run."""
        from the_architect.cli import _setup_loguru

        # Loguru's Logger object does not expose a public handler list; the
        # contract here is simply that the function runs cleanly and can be
        # invoked repeatedly without side effects leaking between tests.
        _setup_loguru()
        _setup_loguru()

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

    def test_read_goal_from_instructions_nonexistent_dir(self, tmp_path: Path) -> None:
        """Should return empty string when directory doesn't exist."""
        from the_architect.cli import _read_goal_from_instructions

        result = _read_goal_from_instructions(tmp_path / "nonexistent")
        assert result == ""

    def test_read_goal_from_instructions_goal_without_following_section(
        self, tmp_path: Path
    ) -> None:
        """Should extract goal text even without following section."""
        from the_architect.cli import _read_goal_from_instructions

        instructions = tmp_path / "INSTRUCTIONS.md"
        instructions.write_text(
            """# Instructions

## Goal

My goal text without following section.
""",
            encoding="utf-8",
        )

        result = _read_goal_from_instructions(tmp_path)
        assert result == "My goal text without following section."


class TestAlternateScreenTTY:
    """Tests for alternate_screen context manager in TTY mode."""

    def test_alternate_screen_tty_path(self) -> None:
        """Should write ANSI codes for entering and exiting alternate screen."""
        from the_architect.cli import alternate_screen

        with (
            patch("sys.stdout.isatty", return_value=True),
            patch("sys.stdout.write") as mock_write,
            patch("sys.stdout.flush"),
        ):
            with alternate_screen():
                pass

            # Check that enter/exit codes were written
            assert mock_write.call_count == 2
            enter_call = mock_write.call_args_list[0]
            exit_call = mock_write.call_args_list[1]
            assert enter_call[0][0] == "\033[?1049h"
            assert exit_call[0][0] == "\033[?1049l"


class TestCountdownANSI:
    """Legacy ``_countdown`` helper was removed along with all stdout-ANSI
    spinners (build 10136) — between-task waits are now just ``time.sleep``
    while the Textual execution screen owns the UI. Kept as an empty class
    so nobody re-adds it later without noticing the architectural change.
    """


class TestSpinANSI:
    """Legacy ``_spin`` helper was removed — see ``TestCountdownANSI``."""


class TestExecutionStartupStatus:
    """Legacy startup / task-start spinner was removed (build 10136).
    The previous test asserted spinner frames and an erase-line ANSI sequence
    were written to stdout during task start. Those no longer happen at all
    because the Textual execution screen is the only UI surface.
    """


# ═══════════════════════════════════════════════════════════════════════════
# _infinite_loop_reset_sleep_interrupted_tasks
# ═══════════════════════════════════════════════════════════════════════════


class TestInfiniteLoopResetSleepInterruptedTasks:
    """Tests for the sleep-interrupted task reset helper."""

    def _make_progress(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "PROGRESS.md"
        p.write_text(content, encoding="utf-8")
        return p

    def _make_config(self, tmp_path: Path) -> object:
        from the_architect.config import ArchitectConfig

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        progress = tmp_path / "PROGRESS.md"
        progress.write_text("", encoding="utf-8")
        return ArchitectConfig(
            project_root=tmp_path,
            tasks_dir=tasks_dir,
            progress_file=progress,
            log_dir=tmp_path / ".architect" / "logs",
        )

    def setup_method(self):
        import the_architect.core.runner as _runner

        with _runner._SLEEP_INTERRUPTED_TASKS_LOCK:
            _runner._SLEEP_INTERRUPTED_TASKS.clear()

    def test_no_sleep_tasks_returns_zero(self, tmp_path: Path):
        from the_architect.cli import _infinite_loop_reset_sleep_interrupted_tasks

        config = self._make_config(tmp_path)
        result = _infinite_loop_reset_sleep_interrupted_tasks(tmp_path, config)
        assert result == 0

    def test_resets_failed_sleep_task_to_pending(self, tmp_path: Path):
        from the_architect.cli import _infinite_loop_reset_sleep_interrupted_tasks
        from the_architect.core.runner import _mark_sleep_interrupted

        _mark_sleep_interrupted("T07")
        config = self._make_config(tmp_path)
        progress_content = (
            "# Progress\n\n"
            "| Task | Title | Status | Date |\n"
            "|------|-------|--------|------|\n"
            "| T07 | lower priority | Failed | 2026-05-15 |\n"
        )
        config.progress_file.write_text(progress_content, encoding="utf-8")

        result = _infinite_loop_reset_sleep_interrupted_tasks(tmp_path, config)
        assert result == 1

        updated = config.progress_file.read_text(encoding="utf-8")
        assert "Pending" in updated
        assert "Failed" not in updated

    def test_does_not_reset_non_sleep_failed_task(self, tmp_path: Path):
        from the_architect.cli import _infinite_loop_reset_sleep_interrupted_tasks

        # T07 NOT in sleep registry — should not be reset
        config = self._make_config(tmp_path)
        progress_content = (
            "# Progress\n\n"
            "| Task | Title | Status | Date |\n"
            "|------|-------|--------|------|\n"
            "| T07 | lower priority | Failed | 2026-05-15 |\n"
        )
        config.progress_file.write_text(progress_content, encoding="utf-8")

        result = _infinite_loop_reset_sleep_interrupted_tasks(tmp_path, config)
        assert result == 0

        updated = config.progress_file.read_text(encoding="utf-8")
        assert "Failed" in updated  # unchanged

    def test_missing_progress_returns_zero(self, tmp_path: Path):
        from the_architect.cli import _infinite_loop_reset_sleep_interrupted_tasks
        from the_architect.core.runner import _mark_sleep_interrupted

        _mark_sleep_interrupted("T07")
        config = self._make_config(tmp_path)
        config.progress_file.unlink()  # delete it

        result = _infinite_loop_reset_sleep_interrupted_tasks(tmp_path, config)
        assert result == 0

    def test_resets_multiple_sleep_tasks(self, tmp_path: Path):
        from the_architect.cli import _infinite_loop_reset_sleep_interrupted_tasks
        from the_architect.core.runner import _mark_sleep_interrupted

        _mark_sleep_interrupted("T06")
        _mark_sleep_interrupted("T07")
        config = self._make_config(tmp_path)
        progress_content = (
            "# Progress\n\n"
            "| Task | Title | Status | Date |\n"
            "|------|-------|--------|------|\n"
            "| T06 | task six | Failed | 2026-05-15 |\n"
            "| T07 | task seven | Failed | 2026-05-15 |\n"
        )
        config.progress_file.write_text(progress_content, encoding="utf-8")

        result = _infinite_loop_reset_sleep_interrupted_tasks(tmp_path, config)
        assert result == 2

        updated = config.progress_file.read_text(encoding="utf-8")
        assert "Failed" not in updated
        assert updated.count("Pending") == 2
