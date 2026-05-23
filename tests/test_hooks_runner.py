"""Tests for lifecycle hooks runner integration.

Covers the integration of hooks.py with the CLI (_run_tasks_raw, _run_main)
and the runner (_run_all_inner).  Verifies:
- pre_run hooks fire before tasks execute
- post_run_success / post_run_failure hooks fire after the run completes
- post_task hooks fire after each task in the runner
- Hook failures do not abort the run (non-fatal, silent-failure pattern)
- Empty hooks list is handled gracefully
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from the_architect.config import ArchitectConfig
from the_architect.core.hooks import (
    HookConfig,
    HookEvent,
    HookResult,
)
from the_architect.core.runner import TaskResult, TokenUsage
from the_architect.core.tasks import Task, TaskPlan, TaskStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hook_result(event: HookEvent, exit_code: int = 0) -> HookResult:
    """Create a HookResult for testing."""
    return HookResult(
        event=event,
        command="echo test",
        exit_code=exit_code,
        timestamp="2026-01-01T00:00:00+00:00",
    )


def _make_task_result(prefix: str = "T01", title: str = "Test", status: str = "done") -> TaskResult:
    """Create a TaskResult for testing."""
    return TaskResult(
        prefix=prefix,
        title=title,
        status=status,
        duration_seconds=1.0,
        attempts=1,
        tokens=TokenUsage(),
        model="",
    )


def _make_config(tmp_path: Path) -> ArchitectConfig:
    """Create an ArchitectConfig for testing."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    progress_file = tasks_dir / "PROGRESS.md"
    progress_file.write_text(
        "**Tasks completed:** 0\n**Next task to run:** T01\n",
        encoding="utf-8",
    )
    log_dir = tmp_path / ".architect" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return ArchitectConfig(
        progress_file=progress_file,
        tasks_dir=tasks_dir,
        log_dir=log_dir,
        max_retries=1,
        retry_pause=0,
        pause_between_tasks=0,
    )


def _make_task_file(tmp_path: Path, prefix: str = "T01", name: str = "T01_test") -> Task:
    """Create a task file and return the Task object."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    path = tasks_dir / f"{name}.md"
    path.write_text(f"# {prefix} - Test Task\n", encoding="utf-8")
    return Task(
        name=name,
        prefix=prefix,
        number=1,
        path=path,
        status=TaskStatus.PENDING,
    )


# ---------------------------------------------------------------------------
# Pre-run hooks in _run_tasks_raw
# ---------------------------------------------------------------------------


class TestPreRunHooks:
    """Tests for pre_run hook firing in _run_tasks_raw."""

    @pytest.mark.asyncio
    async def test_pre_run_hook_fires_before_tasks(self, tmp_path: Path) -> None:
        """pre_run hooks fire before run_all is called."""
        from the_architect.cli import _fire_hooks

        mock_load = MagicMock(
            return_value=[
                HookConfig(event=HookEvent.pre_run, command="echo pre"),
            ]
        )
        mock_exec = AsyncMock(return_value=[_make_hook_result(HookEvent.pre_run)])

        with (
            patch("the_architect.cli.load_hooks", mock_load),
            patch("the_architect.cli.execute_hooks_for_event", mock_exec),
        ):
            # _fire_hooks is the helper used by _run_tasks_raw
            results = await _fire_hooks(tmp_path, HookEvent.pre_run)

        assert len(results) == 1
        mock_load.assert_called_once_with(tmp_path)
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args
        assert call_args[0][1] == HookEvent.pre_run

    @pytest.mark.asyncio
    async def test_pre_run_hook_empty_hooks_no_error(self, tmp_path: Path) -> None:
        """Empty hooks list is handled gracefully — no error raised."""
        from the_architect.cli import _fire_hooks

        mock_load = MagicMock(return_value=[])

        with patch("the_architect.cli.load_hooks", mock_load):
            results = await _fire_hooks(tmp_path, HookEvent.pre_run)

        assert results == []
        mock_load.assert_called_once()


# ---------------------------------------------------------------------------
# Post-run hooks in _run_main
# ---------------------------------------------------------------------------


class TestPostRunHooks:
    """Tests for post_run_success and post_run_failure hooks."""

    @pytest.mark.asyncio
    async def test_post_run_success_hook_fires(self, tmp_path: Path) -> None:
        """post_run_success hooks fire when run succeeds."""
        from the_architect.cli import _fire_hooks

        mock_load = MagicMock(
            return_value=[
                HookConfig(event=HookEvent.post_run_success, command="echo success"),
            ]
        )
        mock_exec = AsyncMock(return_value=[_make_hook_result(HookEvent.post_run_success)])

        with (
            patch("the_architect.cli.load_hooks", mock_load),
            patch("the_architect.cli.execute_hooks_for_event", mock_exec),
        ):
            results = await _fire_hooks(tmp_path, HookEvent.post_run_success)

        assert len(results) == 1
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args
        assert call_args[0][1] == HookEvent.post_run_success

    @pytest.mark.asyncio
    async def test_post_run_failure_hook_fires(self, tmp_path: Path) -> None:
        """post_run_failure hooks fire when run fails."""
        from the_architect.cli import _fire_hooks

        mock_load = MagicMock(
            return_value=[
                HookConfig(event=HookEvent.post_run_failure, command="echo failure"),
            ]
        )
        mock_exec = AsyncMock(return_value=[_make_hook_result(HookEvent.post_run_failure)])

        with (
            patch("the_architect.cli.load_hooks", mock_load),
            patch("the_architect.cli.execute_hooks_for_event", mock_exec),
        ):
            results = await _fire_hooks(tmp_path, HookEvent.post_run_failure)

        assert len(results) == 1
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args
        assert call_args[0][1] == HookEvent.post_run_failure


# ---------------------------------------------------------------------------
# Post-task hooks in runner.py _run_all_inner
# ---------------------------------------------------------------------------


class TestPostTaskHooks:
    """Tests for post_task hooks in the runner."""

    @pytest.mark.asyncio
    async def test_post_task_hook_fires_after_task(self, tmp_path: Path) -> None:
        """post_task hooks fire after each task completes."""
        from the_architect.core.runner import _run_all_inner

        config = _make_config(tmp_path)
        task = _make_task_file(tmp_path)
        plan = TaskPlan(tasks=[task])

        async def mock_run_task(**kwargs):
            return _make_task_result()

        mock_hook_result = _make_hook_result(HookEvent.post_task)
        mock_hook_result.exit_code = 0
        mock_exec = AsyncMock(return_value=[mock_hook_result])
        mock_load = MagicMock(
            return_value=[
                HookConfig(event=HookEvent.post_task, command="echo post"),
            ]
        )

        with (
            patch("the_architect.core.runner.run_task", side_effect=mock_run_task),
            patch("the_architect.core.runner.acquire_lock", return_value=True),
            patch("the_architect.core.hooks.load_hooks", mock_load),
            patch("the_architect.core.hooks.execute_hooks_for_event", mock_exec),
        ):
            result = await _run_all_inner(plan, config)

        assert result is True
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args
        assert call_args[0][1] == HookEvent.post_task
        # Verify context was passed
        context = call_args[1]["context"]
        assert context["TASK_ID"] == "T01"
        assert context["TASK_STATUS"] == "done"
        assert context["TASK_TITLE"] == "Test"

    @pytest.mark.asyncio
    async def test_post_task_hook_fires_on_failure(self, tmp_path: Path) -> None:
        """post_task hooks fire even when a task fails."""
        from the_architect.core.runner import _run_all_inner

        config = _make_config(tmp_path)
        task = _make_task_file(tmp_path)
        plan = TaskPlan(tasks=[task])

        async def mock_run_task(**kwargs):
            return _make_task_result(status="failed")

        mock_hook_result = _make_hook_result(HookEvent.post_task)
        mock_exec = AsyncMock(return_value=[mock_hook_result])
        mock_load = MagicMock(
            return_value=[
                HookConfig(event=HookEvent.post_task, command="echo post"),
            ]
        )

        with (
            patch("the_architect.core.runner.run_task", side_effect=mock_run_task),
            patch("the_architect.core.runner.acquire_lock", return_value=True),
            patch("the_architect.core.hooks.load_hooks", mock_load),
            patch("the_architect.core.hooks.execute_hooks_for_event", mock_exec),
        ):
            result = await _run_all_inner(plan, config)

        assert result is False
        mock_exec.assert_called_once()
        context = mock_exec.call_args[1]["context"]
        assert context["TASK_STATUS"] == "failed"


# ---------------------------------------------------------------------------
# Non-fatal error handling
# ---------------------------------------------------------------------------


class TestHookNonFatal:
    """Tests that hook failures do not abort the run."""

    @pytest.mark.asyncio
    async def test_hook_exception_does_not_abort_cli(self, tmp_path: Path) -> None:
        """Exception in _fire_hooks does not propagate to caller."""
        from the_architect.cli import _fire_hooks

        mock_load = MagicMock(side_effect=RuntimeError("hooks file corrupted"))

        with patch("the_architect.cli.load_hooks", mock_load):
            results = await _fire_hooks(tmp_path, HookEvent.pre_run)

        assert results == []

    @pytest.mark.asyncio
    async def test_hook_execute_exception_does_not_abort_cli(self, tmp_path: Path) -> None:
        """Exception in execute_hooks_for_event does not propagate to caller."""
        from the_architect.cli import _fire_hooks

        mock_load = MagicMock(
            return_value=[
                HookConfig(event=HookEvent.pre_run, command="echo pre"),
            ]
        )
        mock_exec = AsyncMock(side_effect=RuntimeError("subprocess crash"))

        with (
            patch("the_architect.cli.load_hooks", mock_load),
            patch("the_architect.cli.execute_hooks_for_event", mock_exec),
        ):
            results = await _fire_hooks(tmp_path, HookEvent.pre_run)

        assert results == []

    @pytest.mark.asyncio
    async def test_hook_exception_does_not_abort_runner(self, tmp_path: Path) -> None:
        """Exception in runner post_task hooks does not abort the run."""
        from the_architect.core.runner import _run_all_inner

        config = _make_config(tmp_path)
        task = _make_task_file(tmp_path)
        plan = TaskPlan(tasks=[task])

        async def mock_run_task(**kwargs):
            return _make_task_result()

        mock_load = MagicMock(
            return_value=[
                HookConfig(event=HookEvent.post_task, command="echo post"),
            ]
        )
        mock_exec = AsyncMock(side_effect=RuntimeError("hook subprocess crash"))

        with (
            patch("the_architect.core.runner.run_task", side_effect=mock_run_task),
            patch("the_architect.core.runner.acquire_lock", return_value=True),
            patch("the_architect.core.hooks.load_hooks", mock_load),
            patch("the_architect.core.hooks.execute_hooks_for_event", mock_exec),
        ):
            result = await _run_all_inner(plan, config)

        # Run still completes despite hook failure
        assert result is True

    @pytest.mark.asyncio
    async def test_hook_empty_load_does_not_abort_runner(self, tmp_path: Path) -> None:
        """Empty hooks list in runner does not cause errors."""
        from the_architect.core.runner import _run_all_inner

        config = _make_config(tmp_path)
        task = _make_task_file(tmp_path)
        plan = TaskPlan(tasks=[task])

        async def mock_run_task(**kwargs):
            return _make_task_result()

        mock_load = MagicMock(return_value=[])

        with (
            patch("the_architect.core.runner.run_task", side_effect=mock_run_task),
            patch("the_architect.core.runner.acquire_lock", return_value=True),
            patch("the_architect.core.hooks.load_hooks", mock_load),
        ):
            result = await _run_all_inner(plan, config)

        assert result is True
