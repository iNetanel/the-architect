"""Edge case tests for uncovered branches in retrospective.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from the_architect.config import ArchitectConfig
from the_architect.core.claude_code_provider import ClaudeCodeProvider
from the_architect.core.retrospective import (
    ReassessmentResult,
    RetrospectiveFailedError,
    RetrospectiveRequest,
    RetrospectiveResult,
    _ensure_provider_setup_for_review,
    _find_eval_snapshot_files,
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

    def test_provider_setup_reuses_existing_files_after_multiplexed_path_error(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        """Review stages should not fail if package resource setup glitches after setup exists."""
        architect_dir = tmp_path / ".architect"
        prompts_dir = architect_dir / "prompts"
        prompts_dir.mkdir(parents=True)
        for filename in (
            "architect.md",
            "intelligence.md",
            "reviewer.md",
            "execution-protocol.md",
        ):
            (prompts_dir / filename).write_text(f"{filename} prompt\n", encoding="utf-8")
        (architect_dir / "architect.json").write_text(
            '{"agent":{"architect":{"prompt":"architect.md"},'
            '"reviewer":{"prompt":"reviewer.md"}}}\n',
            encoding="utf-8",
        )

        provider = MagicMock()
        provider.name = "opencode"
        provider.ensure_setup.side_effect = NotADirectoryError(
            "MultiplexedPath only supports directories"
        )

        _ensure_provider_setup_for_review(provider, tmp_path, config)

        provider.ensure_setup.assert_called_once_with(tmp_path, config)

    def test_provider_setup_reraises_without_existing_files(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        """Initial setup failures must still surface when no reusable provider setup exists."""
        provider = MagicMock()
        provider.ensure_setup.side_effect = NotADirectoryError(
            "MultiplexedPath only supports directories"
        )

        with pytest.raises(NotADirectoryError):
            _ensure_provider_setup_for_review(provider, tmp_path, config)

    def test_provider_setup_rejects_corrupt_existing_opencode_config(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        """The fallback must not hide stale or corrupt OpenCode review setup."""
        architect_dir = tmp_path / ".architect"
        prompts_dir = architect_dir / "prompts"
        prompts_dir.mkdir(parents=True)
        for filename in (
            "architect.md",
            "intelligence.md",
            "reviewer.md",
            "execution-protocol.md",
        ):
            (prompts_dir / filename).write_text(f"{filename} prompt\n", encoding="utf-8")
        (architect_dir / "architect.json").write_text("{}\n", encoding="utf-8")

        provider = MagicMock()
        provider.name = "opencode"
        provider.ensure_setup.side_effect = NotADirectoryError(
            "MultiplexedPath only supports directories"
        )

        with pytest.raises(NotADirectoryError):
            _ensure_provider_setup_for_review(provider, tmp_path, config)

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

        mock_provider = MagicMock(spec=ClaudeCodeProvider)
        mock_provider.name = "claude-code"
        mock_provider.display_name = "Claude Code"
        mock_provider.supports_agents.return_value = True
        mock_provider.get_reviewer_prompt.return_value = "Review prompt"
        captured: dict[str, object] = {}

        async def fake_stream(**kwargs: object) -> StreamResult:
            captured.update(kwargs)
            return StreamResult(exit_code=0, tokens=TokenUsage())

        with patch("the_architect.core.retrospective.stream_provider", side_effect=fake_stream):
            result = await run_retrospective(request, config, provider=mock_provider)

        assert isinstance(result, RetrospectiveResult)
        assert str(captured["instruction"]).startswith("Review prompt\n\n---\n\n")
        assert captured["agent_override"] is None
        assert captured["config_override"] is None

    @pytest.mark.asyncio
    async def test_run_task_reassessment_prepends_architect_prompt_for_claude(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        """Claude reassessment should inject the architect prompt instead of --agent."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        (tasks_dir / "T02_followup.md").write_text("# T02\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text(
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|-----------|\n"
            "| T02 | Follow up | Pending | — |\n",
            encoding="utf-8",
        )

        mock_provider = MagicMock(spec=ClaudeCodeProvider)
        mock_provider.name = "claude-code"
        mock_provider.supports_agents.return_value = True
        mock_provider.get_architect_prompt.return_value = "Architect prompt"
        captured: dict[str, object] = {}

        async def fake_stream(**kwargs: object) -> StreamResult:
            captured.update(kwargs)
            return StreamResult(exit_code=0, tokens=TokenUsage())

        with patch("the_architect.core.retrospective.stream_provider", side_effect=fake_stream):
            await run_task_reassessment(
                project_dir=tmp_path,
                provider=mock_provider,
                config=config,
                completed_task="T01",
                outcome_summary="Downstream impact: possible",
                original_goal="test goal",
            )

        assert str(captured["instruction"]).startswith("Architect prompt\n\n---\n\n")
        assert captured["agent_override"] is None
        assert captured["config_override"] is None

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

    def test_task_file_listing_skips_architect_eval_files(self, tmp_path: Path) -> None:
        """Line 269: architect_eval_ files in tasks/ are skipped during task file listing."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_init.md").write_text("# T01 — Init\n", encoding="utf-8")
        (tasks_dir / "architect_eval_T01_init.md").write_text(
            "# T01 — Init (backup)\n", encoding="utf-8"
        )

        context = _gather_review_context(tmp_path, "test goal")

        assert "## Task Files" in context
        assert "T01_init.md" in context
        # The architect_eval_ file must NOT appear in the Task Files section
        task_files_section = context.split("## Task Files")[1].split("\n\n")[0]
        assert "architect_eval_T01_init.md" not in task_files_section

    def test_eval_snapshot_stat_oserror(self, tmp_path: Path) -> None:
        """Lines 316-317, 320-321: OSError on .stat() for eval snapshots and originals
        returns 0 for size rather than crashing."""
        # Create an eval snapshot file and its original
        (tmp_path / "app.py").write_text("real content\n", encoding="utf-8")
        eval_file = tmp_path / "architect_eval_app.py"
        eval_file.write_text("backup content\n", encoding="utf-8")

        # Create tasks dir so context is valid
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_init.md").write_text("# T01\n", encoding="utf-8")

        # Save the real stat for fallback
        real_stat = Path.stat

        # Phase control: allow discovery phase, then fail on eval/original files
        discovery_done = False

        def fake_stat(self, *args, **kwargs):
            """Raise OSError for .stat() on eval files and originals during eval section.

            Discovery phase (_find_eval_snapshot_files, file tree) must succeed.
            Eval snapshot section (lines 315, 319) should hit OSError.
            We use a flag set after _find_eval_snapshot_files returns.
            """
            nonlocal discovery_done
            if not discovery_done:
                return real_stat(self, *args, **kwargs)
            if self.name in ("architect_eval_app.py", "app.py"):
                raise OSError("Device or resource busy")
            return real_stat(self, *args, **kwargs)

        # Patch _find_eval_snapshot_files to set the flag after discovery
        original_find = _find_eval_snapshot_files

        def find_with_flag(project_dir):
            nonlocal discovery_done
            result = original_find(project_dir)
            discovery_done = True
            return result

        with patch.object(Path, "stat", fake_stat):
            with patch(
                "the_architect.core.retrospective._find_eval_snapshot_files", find_with_flag
            ):
                context = _gather_review_context(tmp_path, "test goal")

        # Should not crash; should include the eval snapshot warning with 0 sizes
        assert "## Leftover Eval Snapshot Files" in context
        assert "architect_eval_app.py" in context
        # Both sizes should be 0 because stat() raised OSError
        assert "snapshot: 0B" in context
        assert "current: 0B" in context

    def test_build_retrospective_instruction_includes_validation_feedback(
        self, tmp_path: Path
    ) -> None:
        """Line 370: Non-empty validation_feedback includes the validation failure section."""
        request = RetrospectiveRequest(
            round_number=2,
            project_dir=tmp_path,
            original_goal="Build a pipeline",
            validation_feedback="T01's tests are flaky under concurrency.",
        )

        context = "some project context"
        instruction = build_retrospective_instruction(request, context)

        assert "=== VALIDATION FAILURE FROM PREVIOUS ROUND ===" in instruction
        assert "T01's tests are flaky under concurrency." in instruction
        assert "Your next fix-up tasks must directly address this validation failure" in instruction

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


class TestProviderFailureHandling:
    @pytest.mark.asyncio
    async def test_retrospective_quota_exhausted_fails_fast(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "PROGRESS.md").write_text(
            "**Tasks completed:** 0\n**Next task to run:** T01\n",
            encoding="utf-8",
        )

        provider = MagicMock()
        provider.supports_agents.return_value = False
        provider.name = "gemini-cli"
        provider.display_name = "Gemini CLI"

        async def fake_stream(**kwargs: object) -> StreamResult:
            return StreamResult(
                exit_code=1,
                tokens=TokenUsage(),
                accumulated_text="RESOURCE_EXHAUSTED: quota exceeded; billing not enabled",
                rate_limit_hit=True,
            )

        request = RetrospectiveRequest(
            round_number=1,
            project_dir=tmp_path,
            original_goal="test",
        )
        with patch("the_architect.core.retrospective.stream_provider", side_effect=fake_stream):
            with pytest.raises(RetrospectiveFailedError, match="quota"):
                await run_retrospective(request, config, provider=provider)

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


class TestReassessmentErrorBranches:
    """Coverage for uncovered error-handling branches in run_task_reassessment."""

    @pytest.mark.asyncio
    async def test_reassessment_skips_done_tasks_and_handles_task_read_oserror(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        """Lines 671, 674-675: done tasks skipped; OSError on task file read continues."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        # T02 is Pending, T03 is Done
        task_pending = tasks_dir / "T02_next.md"
        task_pending.write_text("# T02\n", encoding="utf-8")
        task_done = tasks_dir / "T03_done.md"
        task_done.write_text("# T03\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text(
            "**Tasks completed:** 1\n"
            "**Next task to run:** T02\n"
            "## Task Log\n"
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|-----------|\n"
            "| T01 | Done thing | Done | 2026-04-29 |\n"
            "| T02 | Next | Pending | — |\n"
            "| T03 | Done task | Done | 2026-04-29 |\n",
            encoding="utf-8",
        )

        provider = MagicMock()
        provider.supports_agents.return_value = False
        provider.name = "claude-code"

        async def fake_stream(**kwargs: object) -> StreamResult:
            return StreamResult(exit_code=0, tokens=TokenUsage())

        # Make T03 appear as done (line 671 continue), and T02 raise OSError on read (line 674-675)
        original_read_text = Path.read_text

        def selective_read_text(self: Path, *args, **kwargs):
            if self.name == "T02_next.md":
                raise OSError("Permission denied")
            return original_read_text(self, *args, **kwargs)

        with patch("the_architect.core.retrospective.stream_provider", side_effect=fake_stream):
            with patch.object(Path, "read_text", selective_read_text):
                result = await run_task_reassessment(
                    project_dir=tmp_path,
                    provider=provider,
                    config=config,
                    completed_task="T01",
                    outcome_summary="Downstream impact: possible",
                    original_goal="test goal",
                )

        assert isinstance(result, ReassessmentResult)
        # Both T02 (OSError skip) and T03 (done skip) should not appear in updated
        assert result.tasks_updated == []

    @pytest.mark.asyncio
    async def test_reassessment_progress_read_oserror(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        """Lines 681-682: OSError on PROGRESS.md read sets empty string."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        task_file = tasks_dir / "T02_next.md"
        task_file.write_text("# T02\n", encoding="utf-8")
        progress_file = tasks_dir / "PROGRESS.md"
        progress_file.write_text("progress content", encoding="utf-8")

        provider = MagicMock()
        provider.supports_agents.return_value = False
        provider.name = "claude-code"

        async def fake_stream(**kwargs: object) -> StreamResult:
            return StreamResult(exit_code=0, tokens=TokenUsage())

        original_read_text = Path.read_text

        def selective_read_text(self: Path, *args, **kwargs):
            # Let the task file read succeed, but fail on PROGRESS.md
            if self.name == "PROGRESS.md":
                raise OSError("Permission denied")
            return original_read_text(self, *args, **kwargs)

        with patch("the_architect.core.retrospective.stream_provider", side_effect=fake_stream):
            with patch.object(Path, "read_text", selective_read_text):
                result = await run_task_reassessment(
                    project_dir=tmp_path,
                    provider=provider,
                    config=config,
                    completed_task="T01",
                    outcome_summary="Downstream impact: possible",
                    original_goal="test goal",
                )

        assert isinstance(result, ReassessmentResult)

    @pytest.mark.asyncio
    async def test_reassessment_architect_md_import_exception(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        """Lines 691-692: Exception on read_architect_md import is caught silently."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        task_file = tasks_dir / "T02_next.md"
        task_file.write_text("# T02\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text("progress", encoding="utf-8")

        provider = MagicMock()
        provider.supports_agents.return_value = False
        provider.name = "claude-code"

        async def fake_stream(**kwargs: object) -> StreamResult:
            return StreamResult(exit_code=0, tokens=TokenUsage())

        with patch("the_architect.core.retrospective.stream_provider", side_effect=fake_stream):
            with patch(
                "the_architect.core.architect_md.read_architect_md",
                side_effect=ImportError("no module"),
            ):
                result = await run_task_reassessment(
                    project_dir=tmp_path,
                    provider=provider,
                    config=config,
                    completed_task="T01",
                    outcome_summary="Downstream impact: possible",
                    original_goal="test goal",
                )

        assert isinstance(result, ReassessmentResult)

    @pytest.mark.asyncio
    async def test_reassessment_eval_snapshot_stat_oserror(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        """Lines 711-712, 715-716: OSError on eval_file.stat() and original_path.stat()."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        task_file = tasks_dir / "T02_next.md"
        task_file.write_text("# T02\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text("progress", encoding="utf-8")

        # Create eval snapshot and original
        (tmp_path / "app.py").write_text("real content\n", encoding="utf-8")
        eval_file = tmp_path / "architect_eval_app.py"
        eval_file.write_text("backup content\n", encoding="utf-8")

        provider = MagicMock()
        provider.supports_agents.return_value = False
        provider.name = "claude-code"

        async def fake_stream(**kwargs: object) -> StreamResult:
            return StreamResult(exit_code=0, tokens=TokenUsage())

        original_stat = Path.stat
        original_find = _find_eval_snapshot_files

        # Phase control: allow discovery phase (is_file checks), then fail on eval files
        discovery_done = False

        def fake_stat(self: Path, *args, **kwargs):
            if not discovery_done:
                return original_stat(self, *args, **kwargs)
            if self.name in ("architect_eval_app.py", "app.py"):
                raise OSError("Device busy")
            return original_stat(self, *args, **kwargs)

        def find_with_flag(project_dir):
            nonlocal discovery_done
            result = original_find(project_dir)
            discovery_done = True
            return result

        with patch("the_architect.core.retrospective.stream_provider", side_effect=fake_stream):
            with patch.object(Path, "stat", fake_stat):
                with patch(
                    "the_architect.core.retrospective._find_eval_snapshot_files",
                    find_with_flag,
                ):
                    result = await run_task_reassessment(
                        project_dir=tmp_path,
                        provider=provider,
                        config=config,
                        completed_task="T01",
                        outcome_summary="Downstream impact: none",
                        original_goal="test goal",
                    )

        assert isinstance(result, ReassessmentResult)

    @pytest.mark.asyncio
    async def test_reassessment_provider_error_raises(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        """Lines 765-766, 789-791: Provider errors (UPDATE_REQUIRED, MISCONFIGURED,
        QUOTA_EXHAUSTED) raise RetrospectiveFailedError."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        task_file = tasks_dir / "T02_next.md"
        task_file.write_text("# T02\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text("progress", encoding="utf-8")

        provider = MagicMock()
        provider.supports_agents.return_value = False
        provider.name = "claude-code"

        # Simulate QUOTA_EXHAUSTED error
        async def fake_stream_quota(**kwargs: object) -> StreamResult:
            return StreamResult(
                exit_code=1,
                tokens=TokenUsage(),
                accumulated_text="RESOURCE_EXHAUSTED: quota exceeded; billing not enabled",
                rate_limit_hit=True,
            )

        with patch(
            "the_architect.core.retrospective.stream_provider", side_effect=fake_stream_quota
        ):
            with pytest.raises(RetrospectiveFailedError, match="quota"):
                await run_task_reassessment(
                    project_dir=tmp_path,
                    provider=provider,
                    config=config,
                    completed_task="T01",
                    outcome_summary="Downstream impact: possible",
                    original_goal="test goal",
                )

    @pytest.mark.asyncio
    async def test_reassessment_post_stream_task_read_oserror(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        """Lines 797, 800-801: OSError on post-stream task file read skips gracefully;
        tasks not in before_contents are skipped (line 797)."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        task_file = tasks_dir / "T02_next.md"
        task_file.write_text("# T02\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text("progress", encoding="utf-8")

        provider = MagicMock()
        provider.supports_agents.return_value = False
        provider.name = "claude-code"

        original_read_text = Path.read_text
        stream_called = False

        def selective_read_text(self: Path, *args, **kwargs):
            # After stream_provider returns, fail on T02 task file reads
            if stream_called and self.name == "T02_next.md":
                raise OSError("File vanished after stream")
            return original_read_text(self, *args, **kwargs)

        async def fake_stream(**kwargs: object) -> StreamResult:
            nonlocal stream_called
            stream_called = True
            return StreamResult(exit_code=0, tokens=TokenUsage())

        with patch("the_architect.core.retrospective.stream_provider", side_effect=fake_stream):
            with patch.object(Path, "read_text", selective_read_text):
                result = await run_task_reassessment(
                    project_dir=tmp_path,
                    provider=provider,
                    config=config,
                    completed_task="T01",
                    outcome_summary="Downstream impact: possible",
                    original_goal="test goal",
                )

        assert isinstance(result, ReassessmentResult)
        # T02 should not appear in updated because OSError on post-stream read
        assert result.tasks_updated == []

    @pytest.mark.asyncio
    async def test_reassessment_agent_override_path(
        self, tmp_path: Path, config: ArchitectConfig
    ) -> None:
        """Lines 765-766: when provider supports agents and uses architect config,
        config_override and agent_override are set."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        task_file = tasks_dir / "T02_next.md"
        task_file.write_text("# T02\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text("progress", encoding="utf-8")

        provider = MagicMock()
        provider.supports_agents.return_value = True
        provider.name = "opencode"

        captured: dict[str, object] = {}

        async def fake_stream(**kwargs: object) -> StreamResult:
            captured.update(kwargs)
            return StreamResult(exit_code=0, tokens=TokenUsage())

        with patch("the_architect.core.retrospective.stream_provider", side_effect=fake_stream):
            with patch(
                "the_architect.core.retrospective._provider_uses_architect_config",
                return_value=True,
            ):
                await run_task_reassessment(
                    project_dir=tmp_path,
                    provider=provider,
                    config=config,
                    completed_task="T01",
                    outcome_summary="Downstream impact: possible",
                    original_goal="test goal",
                )

        assert captured.get("agent_override") == "architect"
        assert captured.get("config_override") == tmp_path / ".architect" / "architect.json"
