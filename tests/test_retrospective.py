"""Edge case tests for uncovered branches in retrospective.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from the_architect.config import ArchitectConfig
from the_architect.core.claude_code_provider import ClaudeCodeProvider
from the_architect.core.retrospective import (
    ReassessmentResult,
    RetrospectiveRequest,
    RetrospectiveResult,
    _gather_review_context,
    _next_r_task_number,
    _update_progress_with_retrospective_tasks,
    build_retrospective_instruction,
    run_retrospective,
    run_task_reassessment,
)
from the_architect.core.runner import StreamResult, TokenUsage
from the_architect.core.tasks import Task, TaskStatus


@pytest.fixture
def config(tmp_path: Path) -> ArchitectConfig:
    """Create an ArchitectConfig for testing."""
    return ArchitectConfig().resolve(tmp_path)


class TestRetrospectiveEdgeCases:
    """Edge case tests for uncovered branches in retrospective.py."""

    def test_budget_limit_truncation(self, tmp_path: Path) -> None:
        """Should truncate context when char budget is exceeded (line 132)."""
        # Create a project with a very large PROGRESS.md to exceed 12000 chars
        progress_file = tmp_path / "tasks" / "PROGRESS.md"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        large_content = "A" * 15000  # Much larger than 12000 char limit
        progress_file.write_text(large_content, encoding="utf-8")

        # Create task files
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        (tasks_dir / "T01_init.md").write_text("# T01 — Init\n\n## Goal\nInit.\n", encoding="utf-8")

        # Add a git directory to test file tree skipping
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("[core]\n", encoding="utf-8")

        context = _gather_review_context(tmp_path, "Build a pipeline")

        # Check that the context was truncated (some parts should be missing)
        # The large content should not all fit, so some parts may be skipped
        assert len(context) <= 12500  # Allow some margin
        # File tree section should be excluded due to budget limits

    def test_progress_read_oserror_full_content(self, tmp_path: Path) -> None:
        """Should handle OSError when reading PROGRESS.md for full content (lines 145-146)."""
        progress_file = tmp_path / "tasks" / "PROGRESS.md"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text("test", encoding="utf-8")

        # Mock Path.read_text to raise OSError for PROGRESS.md
        with patch("pathlib.Path.read_text", side_effect=OSError("Permission denied")):
            context = _gather_review_context(tmp_path, "test goal")
            # Should not crash, should just skip the PROGRESS.md content
            assert "Original Goal" in context  # Other parts should still be there

    def test_progress_read_oserror_history(self, tmp_path: Path) -> None:
        """Should handle OSError when reading PROGRESS.md for historical summary (lines 154-155)."""
        progress_file = tmp_path / "tasks" / "PROGRESS.md"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text("test", encoding="utf-8")

        # First read should work, second should fail
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        (tasks_dir / "T01_init.md").write_text("# T01\n", encoding="utf-8")

        original_read_text = Path.read_text

        def fake_read_text(path: Path, *args: object, **kwargs: object) -> str:
            if path == progress_file:
                raise OSError("Permission denied")
            return original_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", autospec=True, side_effect=fake_read_text):
            context = _gather_review_context(tmp_path, "test goal")
            # Should handle the error gracefully
            assert "Original Goal" in context

    def test_task_file_read_oserror(self, tmp_path: Path) -> None:
        """Should handle OSError when reading task file heading (lines 169-170)."""
        # Create task files
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        task_file = tasks_dir / "T01_init.md"
        task_file.write_text("# T01 — Init\n\n## Goal\nInit.\n", encoding="utf-8")

        # Mock Path.read_text to raise OSError only for the specific task file
        original_read_text = Path.read_text

        def selective_read_text(self, *args, **kwargs):
            if "T01_init.md" in str(self):
                raise OSError("Permission denied")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", selective_read_text):
            context = _gather_review_context(tmp_path, "test goal")
            # Should not crash, should just skip the heading for that file
            assert "Original Goal" in context

    def test_skip_directory(self, tmp_path: Path) -> None:
        """Should skip directory when path.parts contains skip_dirs (line 179)."""
        # Create .git directory
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("[core]\n", encoding="utf-8")

        context = _gather_review_context(tmp_path, "test goal")

        # Should not include .git in the file tree
        assert ".git" not in context

    def test_gather_review_context_reports_eval_snapshots_separately(self, tmp_path: Path) -> None:
        """Eval snapshots should be hidden from file tree and shown in a warning section."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("print('ok')\n", encoding="utf-8")
        (src_dir / "architect_eval_app.py").write_text("print('backup')\n", encoding="utf-8")

        context = _gather_review_context(tmp_path, "test goal")

        assert "## Leftover Eval Snapshot Files" in context
        assert "architect_eval_app.py" in context

    def test_symlink_safety_with_valueerror(self, tmp_path: Path) -> None:
        """Should handle ValueError when resolving symlink (lines 181-185)."""
        # Create a symlink that will cause resolve() to raise ValueError
        real_file = tmp_path / "real.txt"
        real_file.write_text("real file", encoding="utf-8")

        bad_symlink = tmp_path / "bad_link"
        bad_symlink.symlink_to(real_file)

        # Mock Path.resolve to raise ValueError, but only for the specific case
        original_resolve = Path.resolve

        def mock_resolve(path):
            if "bad_link" in str(path):
                raise ValueError("Invalid symlink")
            return original_resolve(path)

        with patch.object(Path, "resolve", mock_resolve):
            context = _gather_review_context(tmp_path, "test goal")
            # Should not crash, should just skip the symlink
            assert "Original Goal" in context

    def test_update_progress_read_oserror(self, tmp_path: Path) -> None:
        """Should handle OSError when reading PROGRESS.md in _update_progress (lines 284-286)."""
        progress_file = tmp_path / "tasks" / "PROGRESS.md"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text(
            "# Task Log\n| Task | Title | Status |\n|------|----- -|-------|\n", encoding="utf-8"
        )

        task = Task(
            name="R01_fix",
            prefix="R01",
            number=1,
            path=tmp_path / "tasks" / "R01_fix.md",
            status=TaskStatus.PENDING,
            title="Fix",
        )

        # Use a wrapper function to patch read_text
        def mock_read_text(*args, **kwargs):
            raise OSError("Permission denied")

        with patch.object(progress_file.__class__, "read_text", mock_read_text):
            _update_progress_with_retrospective_tasks(progress_file, [task])
            # Should not crash, should handle the error gracefully

    def test_update_progress_with_done_next_task(self, tmp_path: Path) -> None:
        """Should update next task when current next task is already Done (lines 324-325)."""
        progress_file = tmp_path / "tasks" / "PROGRESS.md"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T01\n"
            "## Task Log\n| Task | Title | Status | Completed |\n"
            "|----- -|----- -|-------|----------|\n"
            "| T01   | init  | Done  | 2026-04-15 |\n",
            encoding="utf-8",
        )

        task = Task(
            name="R01_fix",
            prefix="R01",
            number=1,
            path=tmp_path / "tasks" / "R01_fix.md",
            status=TaskStatus.PENDING,
            title="Fix",
        )

        _update_progress_with_retrospective_tasks(progress_file, [task])

        content = progress_file.read_text(encoding="utf-8")
        # Next task should be updated to R01 since T01 is Done
        assert "**Next task to run:** R01" in content

    def test_update_progress_write_oserror(self, tmp_path: Path) -> None:
        """Should handle OSError when writing PROGRESS.md (lines 334-335)."""
        progress_file = tmp_path / "tasks" / "PROGRESS.md"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** —\n"
            "## Task Log\n"
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|----------|\n",
            encoding="utf-8",
        )

        task = Task(
            name="R01_fix",
            prefix="R01",
            number=1,
            path=tmp_path / "tasks" / "R01_fix.md",
            status=TaskStatus.PENDING,
            title="Fix",
        )

        # Use a wrapper function to patch write_text
        def mock_write_text(*args, **kwargs):
            raise OSError("Permission denied")

        with patch.object(progress_file.__class__, "write_text", mock_write_text):
            _update_progress_with_retrospective_tasks(progress_file, [task])
            # Should not crash, should handle the error gracefully

    @pytest.mark.asyncio
    async def test_run_task_reassessment_skips_when_no_impact(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        provider = MagicMock()
        result = await run_task_reassessment(
            project_dir=tmp_path,
            provider=provider,
            config=config,
            completed_task="T01",
            outcome_summary="Downstream impact: none",
            original_goal="test goal",
        )
        assert isinstance(result, ReassessmentResult)
        assert result.tasks_updated == []
        provider.ensure_setup.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_task_reassessment_force_runs_without_impact(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        (tmp_path / "tasks").mkdir(exist_ok=True)
        (tmp_path / "tasks" / "PROGRESS.md").write_text("", encoding="utf-8")
        provider = MagicMock()

        async def fake_stream(**kwargs: object) -> StreamResult:
            return StreamResult(exit_code=0, tokens=TokenUsage())

        with patch("the_architect.core.retrospective.stream_provider", side_effect=fake_stream):
            result = await run_task_reassessment(
                project_dir=tmp_path,
                provider=provider,
                config=config,
                completed_task="T01",
                outcome_summary="Downstream impact: none",
                original_goal="test goal",
                force=True,
            )

        assert isinstance(result, ReassessmentResult)
        provider.ensure_setup.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_task_reassessment_updates_pending_tasks(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        task_file = tasks_dir / "T02_followup.md"
        task_file.write_text("# T02 - Follow up\noriginal\n", encoding="utf-8")
        (tmp_path / "tasks" / "PROGRESS.md").write_text(
            "**Tasks completed:** 1\n"
            "**Next task to run:** T02\n"
            "## Task Log\n"
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|-----------|\n"
            "| T01 | Done thing | Done | 2026-04-29 |\n"
            "| T02 | Follow up | Pending | — |\n",
            encoding="utf-8",
        )

        provider = MagicMock()

        async def fake_stream(**kwargs: object) -> StreamResult:
            task_file.write_text("# T02 - Follow up\nupdated\n", encoding="utf-8")
            return StreamResult(exit_code=0, tokens=TokenUsage())

        with patch("the_architect.core.retrospective.stream_provider", side_effect=fake_stream):
            result = await run_task_reassessment(
                project_dir=tmp_path,
                provider=provider,
                config=config,
                completed_task="T01",
                outcome_summary="Downstream impact: possible",
                original_goal="test goal",
            )

        assert result.tasks_updated == ["T02"]

    @pytest.mark.asyncio
    async def test_run_task_reassessment_runs_for_eval_snapshot_without_downstream_impact(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        task_file = tasks_dir / "T02_followup.md"
        task_file.write_text("# T02 - Follow up\noriginal\n", encoding="utf-8")
        (tmp_path / "tasks" / "PROGRESS.md").write_text(
            "**Tasks completed:** 1\n"
            "**Next task to run:** T02\n"
            "## Task Log\n"
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|-----------|\n"
            "| T01 | Done thing | Done | 2026-04-29 |\n"
            "| T02 | Follow up | Pending | — |\n",
            encoding="utf-8",
        )
        (tmp_path / "architect_eval_problem.py").write_text("backup\n", encoding="utf-8")
        (tmp_path / "problem.py").write_text("current\n", encoding="utf-8")

        provider = MagicMock()

        async def fake_stream(**kwargs: object) -> StreamResult:
            task_file.write_text("# T02 - Follow up\nupdated\n", encoding="utf-8")
            return StreamResult(exit_code=0, tokens=TokenUsage())

        with patch("the_architect.core.retrospective.stream_provider", side_effect=fake_stream):
            result = await run_task_reassessment(
                project_dir=tmp_path,
                provider=provider,
                config=config,
                completed_task="T01",
                outcome_summary="Downstream impact: none",
                original_goal="test goal",
            )

        assert result.tasks_updated == ["T02"]
        provider.ensure_setup.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_retrospective_claude_code_provider(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        """Should handle ClaudeCodeProvider path in run_retrospective (lines 388-392)."""
        # Create minimal project setup
        progress_file = tmp_path / "tasks" / "PROGRESS.md"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** —\n"
            "| Task | Title | Status |\n"
            "|------|----- -|-------|\n"
            "| T01 | init | Done | 2026-04-15 |\n",
            encoding="utf-8",
        )

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)

        request = RetrospectiveRequest(
            round_number=1,
            project_dir=tmp_path,
            original_goal="test",
        )

        # Create a mock ClaudeCodeProvider
        mock_provider = MagicMock(spec=ClaudeCodeProvider)
        mock_provider.display_name = "Claude Code"
        mock_provider.supports_agents.return_value = False
        mock_provider.get_reviewer_prompt.return_value = "Review prompt"

        # Mock stream_provider to return a simple result
        async def fake_stream(**kwargs: object) -> StreamResult:
            return StreamResult(exit_code=0, tokens=TokenUsage())

        with patch("the_architect.core.retrospective.stream_provider", side_effect=fake_stream):
            result = await run_retrospective(request, config, provider=mock_provider)

        assert isinstance(result, RetrospectiveResult)
        # Should complete without errors even with Claude Code provider

    @pytest.mark.asyncio
    async def test_run_retrospective_nonzero_exit_code(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        """Should handle non-zero exit code from stream_provider (line 419)."""
        # Create minimal project setup
        progress_file = tmp_path / "tasks" / "PROGRESS.md"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** —\n"
            "| Task | Title | Status |\n"
            "|------|----- -|-------|\n"
            "| T01 | init | Done | 2026-04-15 |\n",
            encoding="utf-8",
        )

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)

        request = RetrospectiveRequest(
            round_number=1,
            project_dir=tmp_path,
            original_goal="test",
        )

        # Mock stream_provider to return non-zero exit code
        async def fake_stream(**kwargs: object) -> StreamResult:
            return StreamResult(exit_code=1, tokens=TokenUsage())

        with patch("the_architect.core.retrospective.stream_provider", side_effect=fake_stream):
            result = await run_retrospective(request, config)

        assert isinstance(result, RetrospectiveResult)
        # Should complete even with exit_code=1


class TestRetrospectiveCoverage:
    """Additional tests to cover remaining uncovered lines."""

    def test_next_r_task_number_empty_dir(self, tmp_path: Path) -> None:
        """Test _next_r_task_number with empty tasks directory."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        result = _next_r_task_number(tasks_dir)
        assert result == 1

    def test_next_r_task_number_with_existing_r_tasks(self, tmp_path: Path) -> None:
        """Test _next_r_task_number with existing R-prefixed tasks."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "R01_fix.md").write_text("# R01\n", encoding="utf-8")
        (tasks_dir / "R02_test.md").write_text("# R02\n", encoding="utf-8")

        result = _next_r_task_number(tasks_dir)
        assert result == 3

    def test_next_r_task_number_case_insensitive(self, tmp_path: Path) -> None:
        """Test _next_r_task_number is case-insensitive for R/r prefix."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "r01_fix.md").write_text("# r01\n", encoding="utf-8")
        (tasks_dir / "R02_test.md").write_text("# R02\n", encoding="utf-8")

        result = _next_r_task_number(tasks_dir)
        assert result == 3

    def test_next_r_task_number_ignores_non_r_tasks(self, tmp_path: Path) -> None:
        """Test _next_r_task_number ignores T and S prefixed tasks."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_init.md").write_text("# T01\n", encoding="utf-8")
        (tasks_dir / "T01_setup.md").write_text("# T01\n", encoding="utf-8")

        result = _next_r_task_number(tasks_dir)
        assert result == 1

    def test_build_retrospective_instruction(self, tmp_path: Path) -> None:
        """Test build_retrospective_instruction builds correct instruction."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        request = RetrospectiveRequest(
            round_number=1,
            project_dir=tmp_path,
            original_goal="Build a pipeline",
        )

        context = "Build a pipeline\n\nsome context"
        instruction = build_retrospective_instruction(request, context)

        assert "PROJECT ROOT:" in instruction
        assert "Build a pipeline" in instruction
        assert "RETROSPECTIVE ROUND 1" in instruction

    def test_update_progress_with_empty_task_list(self, tmp_path: Path) -> None:
        """Test _update_progress_with_retrospective_tasks with empty list."""
        progress_file = tmp_path / "tasks" / "PROGRESS.md"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** —\n", encoding="utf-8"
        )

        # Should not crash with empty list
        _update_progress_with_retrospective_tasks(progress_file, [])

    def test_update_progress_with_no_task_log(self, tmp_path: Path) -> None:
        """Test _update_progress_with_retrospective_tasks when Task Log not found."""
        progress_file = tmp_path / "tasks" / "PROGRESS.md"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text("No task log here", encoding="utf-8")

        task = Task(
            name="R01_fix",
            prefix="R01",
            number=1,
            path=tmp_path / "tasks" / "R01_fix.md",
            status=TaskStatus.PENDING,
            title="Fix",
        )

        # Should not crash when Task Log pattern not found
        _update_progress_with_retrospective_tasks(progress_file, [task])

    def test_update_progress_with_dash_next_task(self, tmp_path: Path) -> None:
        """Test _update_progress_with_retrospective_tasks with '—' as next task."""
        progress_file = tmp_path / "tasks" / "PROGRESS.md"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** —\n"
            "## Task Log\n| Task | Title | Status |\n"
            "|----- -|----- -|-------|\n",
            encoding="utf-8",
        )

        task = Task(
            name="R01_fix",
            prefix="R01",
            number=1,
            path=tmp_path / "tasks" / "R01_fix.md",
            status=TaskStatus.PENDING,
            title="Fix",
        )

        _update_progress_with_retrospective_tasks(progress_file, [task])

        content = progress_file.read_text(encoding="utf-8")
        # Next task should be updated to R01 since current is '—'
        assert "**Next task to run:** R01" in content

    def test_update_progress_with_empty_next_task(self, tmp_path: Path) -> None:
        """Test _update_progress_with_retrospective_tasks with empty next task."""
        progress_file = tmp_path / "tasks" / "PROGRESS.md"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** \n"
            "## Task Log\n| Task | Title | Status |\n"
            "|----- -|----- -|-------|\n",
            encoding="utf-8",
        )

        task = Task(
            name="R01_fix",
            prefix="R01",
            number=1,
            path=tmp_path / "tasks" / "R01_fix.md",
            status=TaskStatus.PENDING,
            title="Fix",
        )

        # Mock task_is_done to return True (current next task is Done)
        with patch("the_architect.core.retrospective.task_is_done", return_value=True):
            _update_progress_with_retrospective_tasks(progress_file, [task])

            content = progress_file.read_text(encoding="utf-8")
            # The task should be added to the task log
            assert "| R01 | R01_fix | Pending | — |" in content
            # Next task should be updated (format may have newline after colon)
            # Check that the replacement happened by looking at the structure
            assert "R01" in content
            # Make sure the update happened by checking the task was set
            lines = content.split("\n")
            next_line_idx = None
            for i, line in enumerate(lines):
                if line.strip() == "**Next task to run:**":
                    next_line_idx = i
                    break
            assert next_line_idx is not None
            # The line after "Next task to run:" should now contain R01
            assert lines[next_line_idx + 1].strip() == "R01"

    def test_gather_review_context_no_progress_file(self, tmp_path: Path) -> None:
        """Test _gather_review_context when PROGRESS.md doesn't exist."""
        context = _gather_review_context(tmp_path, "test goal")

        assert "Original Goal" in context
        assert "test goal" in context

    def test_gather_review_context_no_tasks_dir(self, tmp_path: Path) -> None:
        """Test _gather_review_context when tasks directory doesn't exist."""
        progress_file = tmp_path / "tasks" / "PROGRESS.md"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text("test", encoding="utf-8")

        context = _gather_review_context(tmp_path, "test goal")

        assert "Original Goal" in context
        assert "Current PROGRESS.md" in context

    def test_gather_review_context_empty_tasks_dir(self, tmp_path: Path) -> None:
        """Test _gather_review_context with empty tasks directory."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        progress_file = tmp_path / "tasks" / "PROGRESS.md"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text("test", encoding="utf-8")

        context = _gather_review_context(tmp_path, "test goal")

        assert "Original Goal" in context
        assert "Task Files" in context

    def test_next_r_task_number_nonexistent_dir(self, tmp_path: Path) -> None:
        """Test _next_r_task_number when tasks directory doesn't exist (line 93)."""
        tasks_dir = tmp_path / "nonexistent_tasks"
        # Don't create the directory

        result = _next_r_task_number(tasks_dir)
        assert result == 1

    def test_gather_review_context_symlink_outside_root(self, tmp_path: Path) -> None:
        """Test _gather_review_context with symlink pointing outside project root (line 183)."""
        # Create a symlink that points outside the project
        external_dir = tmp_path.parent / "external"
        external_dir.mkdir(exist_ok=True)
        external_file = external_dir / "file.txt"
        external_file.write_text("external file", encoding="utf-8")

        project_external_link = tmp_path / "external_link"
        project_external_link.symlink_to(external_file)

        # Mock the symlink resolution to always point outside
        original_resolve = Path.resolve

        def mock_resolve(path):
            if "external_link" in str(path):
                # Return a path outside the project_dir
                return Path("/tmp/outside_root")
            return original_resolve(path)

        with patch.object(Path, "resolve", mock_resolve):
            context = _gather_review_context(tmp_path, "test goal")
            # Should not crash and should skip the external symlink
            assert "Original Goal" in context

    @pytest.mark.asyncio
    async def test_run_retrospective_no_new_r_tasks(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        """Test run_retrospective when no new R tasks are created (lines 433-434)."""
        # Create minimal project setup
        progress_file = tmp_path / "tasks" / "PROGRESS.md"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** —\n"
            "## Task Log\n| Task | Title | Status |\n"
            "|----- -|----- -|-------|\n",
            encoding="utf-8",
        )

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)

        request = RetrospectiveRequest(
            round_number=1,
            project_dir=tmp_path,
            original_goal="test",
        )

        # Mock stream_provider to return a result but no tasks are created
        async def fake_stream(**kwargs: object) -> StreamResult:
            return StreamResult(exit_code=0, tokens=TokenUsage())

        with patch("the_architect.core.retrospective.stream_provider", side_effect=fake_stream):
            await run_retrospective(request, config)

    @pytest.mark.asyncio
    async def test_run_retrospective_with_new_tasks(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        """Test run_retrospective when new R tasks are created (lines 433-434)."""
        # Create minimal project setup
        progress_file = tmp_path / "PROGRESS.md"
        progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** —\n"
            "## Task Log\n| Task | Title | Status |\n"
            "|----- -|----- -|-------|\n",
            encoding="utf-8",
        )

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        request = RetrospectiveRequest(
            round_number=1,
            project_dir=tmp_path,
            original_goal="test",
        )

        # Mock stream_provider to return a result and simulate task creation
        async def fake_stream(**kwargs: object) -> StreamResult:
            return StreamResult(exit_code=0, tokens=TokenUsage())

        # Mock discover_tasks to return tasks which includes new R tasks
        with patch("the_architect.core.retrospective.stream_provider", side_effect=fake_stream):
            with patch("the_architect.core.retrospective.discover_tasks") as mock_discover:
                # Simulate that R01 task was created
                mock_discover.side_effect = [
                    [],  # First call (tasks_before)
                    [
                        Task(
                            name="R01_fix",
                            prefix="R01",
                            number=1,
                            path=tasks_dir / "R01_fix.md",
                            status=TaskStatus.PENDING,
                            title="Fix",
                        )
                    ],  # tasks_after
                ]

                result = await run_retrospective(request, config)

                assert isinstance(result, RetrospectiveResult)
                # Should have created one task
                assert "R01_fix" in result.tasks_created


class TestRendererPassthrough:
    """TUI callers must be able to forward a ``WaitLogRenderer`` through
    ``run_retrospective`` and ``run_task_reassessment`` so provider
    output lands in the wait-screen log tail — otherwise the output
    is swallowed by Textual's alt-screen and the user sees an empty
    spinner for the whole review.
    """

    @pytest.mark.asyncio
    async def test_retrospective_forwards_renderer(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tmp_path / "tasks").mkdir(exist_ok=True)
        (tmp_path / "tasks" / "PROGRESS.md").write_text(
            "**Tasks completed:** 0\n"
            "**Next task to run:** T01\n"
            "## Task Log\n"
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|-----------|\n",
            encoding="utf-8",
        )

        provider = MagicMock()
        provider.supports_agents.return_value = False
        provider.get_reviewer_prompt.return_value = "reviewer"
        provider.name = "claude-code"
        provider.display_name = "Claude Code"
        renderer = MagicMock()

        captured: dict[str, object] = {}

        async def fake_stream(**kwargs: object) -> StreamResult:
            captured.update(kwargs)
            return StreamResult(exit_code=0, tokens=TokenUsage())

        request = RetrospectiveRequest(
            round_number=1,
            project_dir=tmp_path,
            original_goal="test",
        )
        with patch("the_architect.core.retrospective.stream_provider", side_effect=fake_stream):
            await run_retrospective(request, config, provider=provider, renderer=renderer)

        assert captured.get("renderer") is renderer

    @pytest.mark.asyncio
    async def test_reassessment_forwards_renderer(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        task_file = tasks_dir / "T02_followup.md"
        task_file.write_text("# T02\n", encoding="utf-8")
        (tmp_path / "tasks").mkdir(exist_ok=True)
        (tmp_path / "tasks" / "PROGRESS.md").write_text(
            "**Tasks completed:** 1\n"
            "**Next task to run:** T02\n"
            "## Task Log\n"
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|-----------|\n"
            "| T01 | Thing | Done | 2026-04-29 |\n"
            "| T02 | Follow up | Pending | — |\n",
            encoding="utf-8",
        )

        provider = MagicMock()
        provider.supports_agents.return_value = False
        provider.name = "claude-code"
        renderer = MagicMock()

        captured: dict[str, object] = {}

        async def fake_stream(**kwargs: object) -> StreamResult:
            captured.update(kwargs)
            return StreamResult(exit_code=0, tokens=TokenUsage())

        with patch("the_architect.core.retrospective.stream_provider", side_effect=fake_stream):
            await run_task_reassessment(
                project_dir=tmp_path,
                provider=provider,
                config=config,
                completed_task="T01",
                outcome_summary="Downstream impact: possible",
                original_goal="test",
                renderer=renderer,
            )

        assert captured.get("renderer") is renderer
