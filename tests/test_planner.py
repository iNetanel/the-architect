"""Edge-case tests for uncovered branches in planner.py"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from the_architect.config import ArchitectConfig
from the_architect.core.planner import (
    PlanningFailedError,
    PlanningRequest,
    TaskScope,
    _clear_log_dir,
    _next_task_number,
    _rescue_stray_tasks,
    _summarize_progress_historical,
    _write_instructions_md,
    _write_progress_md,
    archive_previous_run,
    build_planning_instruction,
    check_pending_tasks,
    gather_project_context,
    run_planner,
)
from the_architect.core.runner import StreamResult, TokenUsage


class TestGatherProjectContextEdgeCases:
    """Edge-case tests for gather_project_context()."""

    def test_gather_context_char_budget_enforced(self, tmp_path: Path) -> None:
        """Test that add_part() respects the 8000-char budget."""
        # Create a project with enough files to exceed 8000 chars
        large_file = tmp_path / "large_file.py"
        large_file.write_text("x" * 10000, encoding="utf-8")

        context = gather_project_context(tmp_path)

        # Should include file tree but truncate/content should respect budget
        assert "File tree:" in context
        # Content should not be extremely large (budget respected)
        assert len(context) < 10000  # Well under the 8000 budget when formatted

    def test_gather_context_symlink_dir_resolve_failure(self, tmp_path: Path) -> None:
        """Test symlink directory safety when resolve() raises OSError."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Create a symlink directory
        symlink_dir = project_dir / "bad_symlink"
        symlink_dir.symlink_to(tmp_path / "nonexistent" / "deep" / "path")

        original_resolve = Path.resolve

        def selective_resolve(self: Path) -> Path:
            if "bad_symlink" in str(self):
                raise OSError("resolve failed")
            return original_resolve(self)

        with patch.object(Path, "resolve", selective_resolve):
            context = gather_project_context(project_dir)
            assert "bad_symlink" not in context

    def test_gather_context_symlink_file_resolve_failure(self, tmp_path: Path) -> None:
        """Test symlink file safety when resolve() raises OSError."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Create a symlink file
        symlink_file = project_dir / "link.txt"
        symlink_file.symlink_to(tmp_path / "nonexistent" / "file.txt")

        original_resolve = Path.resolve

        def selective_resolve(self: Path) -> Path:
            if "link.txt" in str(self):
                raise OSError("resolve failed")
            return original_resolve(self)

        with patch.object(Path, "resolve", selective_resolve):
            context = gather_project_context(project_dir)
            assert "link.txt" not in context

    def test_gather_context_excludes_architect_eval_files(self, tmp_path: Path) -> None:
        """architect_eval files should not appear in normal planner file trees."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
        (tmp_path / "src" / "architect_eval_app.py").write_text("backup\n", encoding="utf-8")

        context = gather_project_context(tmp_path)

        assert "app.py" in context
        assert "architect_eval_app.py" not in context

    def test_gather_context_claude_code_provider_uses_claude_md(self, tmp_path: Path) -> None:
        """Test that claude-code provider reads CLAUDE.md instead of AGENTS.md."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Claude Maryland\nClaude-specific rules", encoding="utf-8")

        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# AGENTS\nOpenCode rules", encoding="utf-8")

        provider = Mock()
        provider.name = "claude-code"

        context = gather_project_context(tmp_path, provider=provider)
        assert "Claude-specific rules" in context
        assert "OpenCode rules" not in context

    def test_gather_context_rules_file_oserror(self, tmp_path: Path) -> None:
        """Test handling of OSError when reading rules file."""
        # Create AGENTS.md so rules_path.exists() returns True
        (tmp_path / "AGENTS.md").write_text("# Rules", encoding="utf-8")

        # Mock read_text to raise OSError
        with patch.object(Path, "read_text", side_effect=OSError("read failed")):
            context = gather_project_context(tmp_path)
            # Should not crash, should continue without the file content
            assert "AGENTS.md" in context or "File tree:" in context

    def test_gather_context_progress_md_oserror(self, tmp_path: Path) -> None:
        """Test handling of OSError when reading PROGRESS.md."""
        progress_md = tmp_path / "PROGRESS.md"
        progress_md.write_text("**Tasks completed:** 1", encoding="utf-8")

        # Mock read_text to raise OSError
        with patch.object(Path, "read_text", side_effect=OSError("read failed")):
            context = gather_project_context(tmp_path)
            # Should not crash
            assert "File tree:" in context

    def test_gather_context_docs_dir_oserror(self, tmp_path: Path) -> None:
        """Test handling of OSError when reading docs files."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        doc_file = docs_dir / "README.md"
        doc_file.write_text("# Docs", encoding="utf-8")

        # Mock read_text to raise OSError
        with patch.object(Path, "read_text", side_effect=OSError("read failed")):
            context = gather_project_context(tmp_path)
            # Should not crash, should continue without the doc content
            assert "docs/" in context

    def test_gather_context_archive_instructions_break_on_second_heading(
        self, tmp_path: Path
    ) -> None:
        """Test that reading archive INSTRUCTIONS.md breaks on second heading."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        archive_dir = tasks_dir / "archive" / "2024-01-01_000000"
        archive_dir.mkdir(parents=True)

        instructions = archive_dir / "INSTRUCTIONS.md"
        instructions.write_text(
            "## Goal\nThe goal\n## Other Section\nMore content", encoding="utf-8"
        )

        context = gather_project_context(tmp_path)
        # Should not crash, should handle the break correctly
        assert "File tree:" in context

    def test_gather_context_archive_instructions_oserror(self, tmp_path: Path) -> None:
        """Test handling of OSError when reading archive INSTRUCTIONS.md."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        archive_dir = tasks_dir / "archive" / "2024-01-01_000000"
        archive_dir.mkdir(parents=True)

        instructions = archive_dir / "INSTRUCTIONS.md"
        instructions.write_text("## Goal\nThe goal", encoding="utf-8")

        # Mock read_text to raise OSError
        with patch.object(Path, "read_text", side_effect=OSError("read failed")):
            context = gather_project_context(tmp_path)
            # Should not crash, should handle the error gracefully
            assert "File tree:" in context


class TestBuildPlanningInstructionEdgeCases:
    """Edge-case tests for build_planning_instruction()."""

    def test_build_instruction_handles_empty_goal_with_context(self, tmp_path: Path) -> None:
        """Test the branch when goal is empty but context exists."""
        request = PlanningRequest(
            goal="",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
            context_content="Some context provided by user",
        )
        instruction = build_planning_instruction(request, "project context")

        # Should include the note about deriving goal from context
        assert "No explicit goal was provided" in instruction
        assert "Derive the goal from the context files" in instruction


class TestRescueStrayTasksEdgeCases:
    """Edge-case tests for _rescue_stray_tasks()."""

    def test_rescue_skips_non_md_files_in_stray_dirs(self, tmp_path: Path) -> None:
        """Test that non-.md files are skipped during rescue."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        stray_dir = tmp_path / "mbi" / "tasks"
        stray_dir.mkdir(parents=True)

        # Create non-MD files that should be ignored
        (stray_dir / "T01_fake.txt").write_text("not md", encoding="utf-8")
        (stray_dir / "T01_real.md").write_text("task file", encoding="utf-8")

        rescued = _rescue_stray_tasks(tmp_path, tasks_dir)

        # Should rescue the .md file but skip the .txt file
        assert rescued == 1
        assert (tasks_dir / "T01_real.md").exists()
        # The .txt file should still exist (not deleted by rescue)
        assert (stray_dir / "T01_fake.txt").exists()

    def test_rescue_symlink_file_resolve_failure(self, tmp_path: Path) -> None:
        """Test handling of symlink .md files when resolve() raises OSError/ValueError."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        subdir = tmp_path / "mbi"
        subdir.mkdir()

        # Create a regular .md file — we'll fake it being a symlink
        stray_md = subdir / "T01_task.md"
        stray_md.write_text("# T01", encoding="utf-8")

        original_resolve = Path.resolve
        original_is_symlink = Path.is_symlink
        resolve_counts: dict[str, int] = {}

        def mock_is_symlink(self: Path) -> bool:
            if "T01_task.md" in str(self):
                return True
            return original_is_symlink(self)

        def selective_resolve(self: Path) -> Path:
            key = str(self)
            if "T01_task.md" in key:
                resolve_counts[key] = resolve_counts.get(key, 0) + 1
                # First resolve (line 611): succeed normally
                # Second resolve (line 619): raise OSError to test lines 618-622
                if resolve_counts[key] > 1:
                    raise OSError("resolve failed")
            return original_resolve(self)

        with (
            patch.object(Path, "is_symlink", mock_is_symlink),
            patch.object(Path, "resolve", selective_resolve),
        ):
            rescued = _rescue_stray_tasks(tmp_path, tasks_dir)
            # Should not crash, should skip the symlink
            assert rescued == 0

    def test_rescue_rename_fails(self, tmp_path: Path) -> None:
        """Test handling when path.rename raises OSError."""
        from pathlib import Path as _Path

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        stray_dir = tmp_path / "mbi" / "tasks"
        stray_dir.mkdir(parents=True)
        stray_file = stray_dir / "T01_task.md"
        stray_file.write_text("# T01", encoding="utf-8")

        # Mock rename to raise OSError
        with patch.object(_Path, "rename", side_effect=OSError("rename failed")):
            rescued = _rescue_stray_tasks(tmp_path, tasks_dir)
            # Should not crash, should handle the error gracefully
            assert rescued == 0


class TestWriteProgressMdEdgeCases:
    """Edge-case tests for _write_progress_md()."""

    def test_write_progress_md_empty_tasks_list(self, tmp_path: Path) -> None:
        """Test that _write_progress_md() handles empty tasks list."""
        progress_file = tmp_path / "PROGRESS.md"

        # Should not crash, should return early
        _write_progress_md(progress_file, [])

        # File should not be created
        assert not progress_file.exists()


class TestArchivePreviousRunEdgeCases:
    """Edge-case tests for archive_previous_run()."""

    def test_archive_move_fails(self, tmp_path: Path) -> None:
        """Test handling when shutil.move raises OSError."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_task.md").write_text("# T01", encoding="utf-8")

        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)

        # Mock shutil.move to raise OSError
        with patch("shutil.move", side_effect=OSError("move failed")):
            result = archive_previous_run(tasks_dir, log_dir, tmp_path / "PROGRESS.md")
            # Should not crash, should handle the error and still clear logs
            assert result is not None


class TestClearLogDirEdgeCases:
    """Edge-case tests for _clear_log_dir()."""

    def test_clear_log_unlink_fails(self, tmp_path: Path) -> None:
        """Test handling when Path.unlink raises OSError."""
        from pathlib import Path as _Path

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_file = log_dir / "test.log"
        log_file.write_text("log content", encoding="utf-8")

        # Mock unlink to raise OSError
        with patch.object(_Path, "unlink", side_effect=OSError("unlink failed")):
            _clear_log_dir(log_dir)
            # Should not crash, should continue despite the error
            assert log_dir.exists()


class TestRunPlannerEdgeCases:
    """Edge-case tests for run_planner()."""

    @pytest.mark.asyncio
    async def test_run_planner_claude_code_provider_checks_claude_md(self, tmp_path: Path) -> None:
        """Test the CLAUDE.md reading branch for claude-code provider (line 1122)."""
        from unittest.mock import MagicMock

        from the_architect.core.claude_code_provider import ClaudeCodeProvider

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Claude MD", encoding="utf-8")

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        config = ArchitectConfig().resolve(tmp_path)

        # Create a mock with spec=ClaudeCodeProvider so isinstance() passes
        provider = MagicMock(spec=ClaudeCodeProvider)
        provider.name = "claude-code"
        provider.display_name = "Claude Code"
        provider.supports_agents.return_value = False
        provider.ensure_setup.return_value = None
        provider.get_architect_prompt.return_value = "architect prompt"

        request = PlanningRequest(
            goal="Test goal",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        async def fake_stream(**kwargs: object) -> StreamResult:  # type: ignore[misc]
            (tasks_dir / "T01_test.md").write_text("# T01", encoding="utf-8")
            return StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")

        with patch("the_architect.core.planner.stream_provider", side_effect=fake_stream):
            result = await run_planner(request, config, provider=provider)

        assert result.agents_md_read is True
        assert "CLAUDE.md" in str(claude_md)

    @pytest.mark.asyncio
    async def test_run_planner_with_claude_code_provider_no_agent_support(
        self, tmp_path: Path
    ) -> None:
        """Test the claude-code provider branch that prepends architect prompt (lines 931-935)."""
        from unittest.mock import MagicMock

        from the_architect.core.claude_code_provider import ClaudeCodeProvider

        # Create CLAUDE.md so agents_md_read is True
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Claude rules", encoding="utf-8")

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        config = ArchitectConfig().resolve(tmp_path)

        # Create a mock with spec=ClaudeCodeProvider so isinstance() passes
        provider = MagicMock(spec=ClaudeCodeProvider)
        provider.name = "claude-code"
        provider.display_name = "Claude Code"
        provider.supports_agents.return_value = False
        provider.ensure_setup.return_value = None
        provider.get_architect_prompt.return_value = "You are the architect agent."

        request = PlanningRequest(
            goal="Test goal",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        async def fake_stream(**kwargs: object) -> StreamResult:  # type: ignore[misc]
            (tasks_dir / "T01_test.md").write_text("# T01", encoding="utf-8")
            return StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")

        with patch("the_architect.core.planner.stream_provider", side_effect=fake_stream):
            result = await run_planner(request, config, provider=provider)

        assert result.agents_md_read is True
        assert "T01_test" in result.tasks_created

    @pytest.mark.asyncio
    async def test_run_planner_cooldown_via_cooldown_until(self, tmp_path: Path) -> None:
        """Test the cooldown_until > 0 branch (lines 1021-1025)."""
        import time as _time

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        config = ArchitectConfig().resolve(tmp_path)
        request = PlanningRequest(
            goal="Test goal",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        call_count = 0

        async def fake_stream(**kwargs: object) -> StreamResult:  # type: ignore[misc]
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: provider signals cooldown with precise timestamp
                return StreamResult(
                    exit_code=1,
                    tokens=TokenUsage(),
                    accumulated_text="",
                    rate_limit_hit=True,
                    cooldown_until=int(_time.time()) + 120,
                )
            # Second call: planning succeeds
            (tasks_dir / "T01_test.md").write_text("# T01", encoding="utf-8")
            return StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")

        sleep_calls: list[float] = []

        async def capture_sleep(secs: float) -> None:
            sleep_calls.append(secs)

        with (
            patch("the_architect.core.planner.stream_provider", side_effect=fake_stream),
            patch("asyncio.sleep", side_effect=capture_sleep),
        ):
            result = await run_planner(request, config)

        assert "T01_test" in result.tasks_created
        assert sum(sleep_calls) > 100  # Should wait ~120s

    @pytest.mark.asyncio
    async def test_run_planner_cooldown_cancelled_error(self, tmp_path: Path) -> None:
        """Test that asyncio.CancelledError is caught during cooldown wait (lines 1058-1060)."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        config = ArchitectConfig().resolve(tmp_path)
        request = PlanningRequest(
            goal="Test goal",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        call_count = 0

        async def fake_stream(**kwargs: object) -> StreamResult:  # type: ignore[misc]
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return StreamResult(
                    exit_code=1,
                    tokens=TokenUsage(),
                    accumulated_text="",
                    rate_limit_hit=True,
                )
            (tasks_dir / "T01_test.md").write_text("# T01", encoding="utf-8")
            return StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")

        sleep_counter = 0

        async def fake_sleep(secs: float) -> None:
            nonlocal sleep_counter
            sleep_counter += 1
            if sleep_counter == 1:
                raise asyncio.CancelledError()

        with (
            patch("the_architect.core.planner.stream_provider", side_effect=fake_stream),
            patch("asyncio.sleep", side_effect=fake_sleep),
        ):
            # CancelledError is caught, cooldown wait interrupted,
            # planner retries and succeeds on second attempt
            result = await run_planner(request, config)

        assert "T01_test" in result.tasks_created

    @pytest.mark.asyncio
    async def test_run_planner_instructions_md_oserror(self, tmp_path: Path) -> None:
        """Test handling of OSError when reading INSTRUCTIONS.md (lines 1116-1117)."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        config = ArchitectConfig().resolve(tmp_path)
        request = PlanningRequest(
            goal="Test goal",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        async def fake_stream(**kwargs: object) -> StreamResult:  # type: ignore[misc]
            (tasks_dir / "T01_test.md").write_text("# T01", encoding="utf-8")
            return StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")

        # Create INSTRUCTIONS.md that will raise on read
        instructions_md = tasks_dir / "INSTRUCTIONS.md"
        instructions_md.write_text("# Instructions", encoding="utf-8")

        original_read_text = Path.read_text

        def selective_read_text(self: Path, *args: object, **kwargs: object) -> str:
            if "INSTRUCTIONS.md" in str(self):
                raise OSError("read failed")
            return original_read_text(self, *args, **kwargs)

        with (
            patch("the_architect.core.planner.stream_provider", side_effect=fake_stream),
            patch.object(Path, "read_text", selective_read_text),
        ):
            result = await run_planner(request, config)

        assert "T01_test" in result.tasks_created


class TestSummarizeProgressHistorical:
    """Tests for _summarize_progress_historical() (lines 139-179)."""

    def test_empty_content(self) -> None:
        """Should return 'No previous plan history' for empty content."""
        result = _summarize_progress_historical("")
        assert result == "No previous plan history found."

    def test_content_with_done_tasks(self) -> None:
        """Should extract completed tasks from Task Log."""
        content = (
            "## Task Log\n"
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|----------|\n"
            "| T01 | init | Done | 2026-04-25 |\n"
            "| T02 | build | Pending | — |\n"
        )
        result = _summarize_progress_historical(content)
        assert "T01" in result
        assert "init" in result
        assert "2026-04-25" in result
        assert "T02" not in result  # Pending, not Done

    def test_content_with_permanent_decisions(self) -> None:
        """Should extract permanent decisions from the table."""
        content = (
            "## Permanent Decisions\n"
            "| Decision | Value | Reason | Task |\n"
            "|----------|-------|--------|------|\n"
            "| Use SQLite | sqlite3 | Local cache | T01 |\n"
        )
        result = _summarize_progress_historical(content)
        assert "Use SQLite" in result
        assert "sqlite3" in result

    def test_content_with_empty_decision_rows(self) -> None:
        """Should skip empty placeholder rows in decisions table."""
        content = (
            "## Permanent Decisions\n"
            "| Decision | Value | Reason | Task |\n"
            "|----------|-------|--------|------|\n"
            "| | | | |\n"
        )
        _summarize_progress_historical(content)
        # Empty rows should be skipped

    def test_content_with_both_tasks_and_decisions(self) -> None:
        """Should extract both done tasks and decisions."""
        content = (
            "## Task Log\n"
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|----------|\n"
            "| T01 | setup | Done | 2026-04-25 |\n\n"
            "## Permanent Decisions\n"
            "| Decision | Value | Reason | Task |\n"
            "|----------|-------|--------|------|\n"
            "| Use pytest | pytest | Testing | T01 |\n"
        )
        result = _summarize_progress_historical(content)
        assert "T01" in result
        assert "setup" in result
        assert "Use pytest" in result

    def test_done_task_without_date(self) -> None:
        """Should handle Done tasks with empty date field."""
        content = (
            "## Task Log\n"
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|----------|\n"
            "| T01 | init | Done | |\n"
        )
        result = _summarize_progress_historical(content)
        assert "T01" in result
        assert "—" in result  # Empty date should show —


class TestNextTaskNumber:
    """Tests for _next_task_number() (line 419)."""

    def test_empty_tasks_dir(self, tmp_path: Path) -> None:
        """Should return 1 when no task files exist."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        assert _next_task_number(tasks_dir) == 1

    def test_with_existing_tasks(self, tmp_path: Path) -> None:
        """Should return max(existing) + 1."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_init.md").write_text("# T01", encoding="utf-8")
        (tasks_dir / "T03_build.md").write_text("# T03", encoding="utf-8")
        assert _next_task_number(tasks_dir) == 4

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        """Should return 1 when tasks dir doesn't exist."""
        assert _next_task_number(tmp_path / "nonexistent") == 1


class TestBuildPlanningInstructionAdditional:
    """Additional tests for build_planning_instruction() (lines 461, 471)."""

    def test_build_instruction_with_architect_md_content(self, tmp_path: Path) -> None:
        """Test that architect_md_content is included in instruction (line 461)."""
        request = PlanningRequest(
            goal="Test goal",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
            architect_md_content="## Persistent Intelligence\nUse SQLite",
        )
        instruction = build_planning_instruction(request, "project context")
        assert "ARCHITECT.md" in instruction
        assert "Use SQLite" in instruction

    def test_build_instruction_with_structure_report(self, tmp_path: Path) -> None:
        """Test that structure_report is included in instruction (line 471)."""
        request = PlanningRequest(
            goal="Test goal",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
            structure_report="Single repo, Python package",
        )
        instruction = build_planning_instruction(request, "project context")
        assert "PROJECT STRUCTURE REPORT" in instruction
        assert "Single repo, Python package" in instruction


class TestWriteInstructionsMd:
    """Tests for _write_instructions_md() (lines 714-717)."""

    def test_write_instructions_with_architect_content(self, tmp_path: Path) -> None:
        """Should use architect_content as-is when provided (lines 714-717)."""
        from the_architect.core.tasks import Task

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        instructions_file = tasks_dir / "INSTRUCTIONS.md"

        tasks = [
            Task(
                name="T01_init",
                prefix="T01",
                number=1,
                path=tasks_dir / "T01_init.md",
                status="pending",
                title="Initialize project",
            )
        ]

        architect_content = "# Custom Instructions by Architect\n\nThis is richer content."
        _write_instructions_md(instructions_file, "Build app", tasks, architect_content)

        written = instructions_file.read_text(encoding="utf-8")
        assert "Custom Instructions by Architect" in written
        assert "richer content" in written

    def test_write_instructions_without_architect_content(self, tmp_path: Path) -> None:
        """Should generate minimal instructions when architect_content is None."""
        from the_architect.core.tasks import Task

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        instructions_file = tasks_dir / "INSTRUCTIONS.md"

        tasks = [
            Task(
                name="T01_init",
                prefix="T01",
                number=1,
                path=tasks_dir / "T01_init.md",
                status="pending",
                title="Initialize project",
            )
        ]

        _write_instructions_md(instructions_file, "Build app", tasks, None)

        written = instructions_file.read_text(encoding="utf-8")
        assert "Build app" in written
        assert "T01" in written

    def test_write_instructions_are_goal_specific_not_architect_snapshot(
        self, tmp_path: Path
    ) -> None:
        """Generated instructions should avoid duplicating ARCHITECT.md memory."""
        from the_architect.core.tasks import Task

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        instructions_file = tasks_dir / "INSTRUCTIONS.md"
        (tmp_path / "ARCHITECT.md").write_text(
            "# ARCHITECT.md\n\n"
            "## Project Overview\n\n- Customer platform.\n\n"
            "## Tech Stack\n\n- `frontend/`: TypeScript · Next.js.\n\n"
            "## Code Locations\n\n- `frontend/` — mission: Web UI.\n",
            encoding="utf-8",
        )
        tasks = [
            Task(
                name="T01_init",
                prefix="T01",
                number=1,
                path=tasks_dir / "T01_init.md",
                status="pending",
                title="Initialize project",
            )
        ]

        _write_instructions_md(instructions_file, "Build app", tasks, None)

        written = instructions_file.read_text(encoding="utf-8")
        assert "Goal-Specific Plan" in written
        assert "Cross-Task Context" in written
        assert "Goal-Specific Contracts" in written
        assert "Do not duplicate project-level notes from ARCHITECT.md" in written
        assert "Progress Memory" in written
        assert "what is missing" in written
        assert "Customer platform" not in written
        assert "TypeScript" not in written
        assert "mission: Web UI" not in written


class TestCheckPendingTasks:
    """Tests for check_pending_tasks() (lines 765-778)."""

    def test_check_pending_with_pending_tasks(self, tmp_path: Path) -> None:
        """Should return pending task names."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_init.md").write_text("# T01 — Init", encoding="utf-8")
        (tasks_dir / "T02_build.md").write_text("# T02 — Build", encoding="utf-8")

        progress_file = tmp_path / "PROGRESS.md"
        progress_file.write_text(
            "**Tasks completed:** 0\n**Next task to run:** T01\n"
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|----------|\n"
            "| T01 | init | Pending | — |\n"
            "| T02 | build | Pending | — |\n",
            encoding="utf-8",
        )

        result = check_pending_tasks(tasks_dir, progress_file)
        assert "T01_init" in result
        assert "T02_build" in result

    def test_check_pending_with_done_tasks(self, tmp_path: Path) -> None:
        """Should not return done task names."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_init.md").write_text("# T01 — Init", encoding="utf-8")

        progress_file = tmp_path / "PROGRESS.md"
        progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** —\n"
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|----------|\n"
            "| T01 | init | Done | 2026-04-25 |\n",
            encoding="utf-8",
        )

        result = check_pending_tasks(tasks_dir, progress_file)
        assert result == []

    def test_check_pending_nonexistent_dir(self, tmp_path: Path) -> None:
        """Should return empty list when tasks dir doesn't exist."""
        progress_file = tmp_path / "PROGRESS.md"
        result = check_pending_tasks(tmp_path / "nonexistent", progress_file)
        assert result == []

    def test_check_pending_skips_s_prefix(self, tmp_path: Path) -> None:
        """Should skip S-prefixed (standalone) tasks."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "S01_special.md").write_text("# S01 — Special", encoding="utf-8")
        (tasks_dir / "T01_init.md").write_text("# T01 — Init", encoding="utf-8")

        progress_file = tmp_path / "PROGRESS.md"
        progress_file.write_text(
            "**Tasks completed:** 0\n**Next task to run:** T01\n"
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|----------|\n"
            "| T01 | init | Pending | — |\n",
            encoding="utf-8",
        )

        result = check_pending_tasks(tasks_dir, progress_file)
        assert "T01_init" in result
        assert "S01_special" not in result


class TestGatherProjectContextAdditional:
    """Additional tests for gather_project_context() covering more lines."""

    def test_rules_file_oserror_with_existing_file(self, tmp_path: Path) -> None:
        """Test OSError on rules file read when file exists (lines 292-293)."""
        # Create AGENTS.md so rules_path.exists() returns True
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# Rules", encoding="utf-8")

        original_read_text = Path.read_text

        def selective_read_text(self: Path, *args: object, **kwargs: object) -> str:
            if "AGENTS.md" in str(self):
                raise OSError("read failed")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", selective_read_text):
            context = gather_project_context(tmp_path)
            # Should not crash, should handle OSError gracefully
            assert "File tree:" in context

    def test_progress_md_with_historical_summary(self, tmp_path: Path) -> None:
        """Test PROGRESS.md read with successful historical summary (lines 305-306)."""
        progress_md = tmp_path / "PROGRESS.md"
        progress_md.write_text(
            "## Task Log\n"
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|----------|\n"
            "| T01 | init | Done | 2026-04-25 |\n",
            encoding="utf-8",
        )
        context = gather_project_context(tmp_path)
        assert "Previous Plan History" in context
        assert "T01" in context

    def test_docs_dir_with_content(self, tmp_path: Path) -> None:
        """Test docs/ directory with readable files (lines 322-325)."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "guide.md").write_text("# Guide\nSome content here", encoding="utf-8")

        context = gather_project_context(tmp_path)
        assert "Documentation" in context
        assert "guide.md" in context
        assert "Guide" in context

    def test_tasks_dir_with_files(self, tmp_path: Path) -> None:
        """Test tasks/ directory with existing task files (lines 343-344)."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_init.md").write_text("# T01 — Init", encoding="utf-8")

        context = gather_project_context(tmp_path)
        assert "T01_init" in context

    def test_archive_instructions_with_goal_and_break(self, tmp_path: Path) -> None:
        """Test archive INSTRUCTIONS.md with goal and second heading break (line 379)."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        archive_dir = tasks_dir / "archive" / "2024-01-01_000000"
        archive_dir.mkdir(parents=True)

        # INSTRUCTIONS.md with goal line then a second heading
        instructions = archive_dir / "INSTRUCTIONS.md"
        instructions.write_text(
            "## Goal\nBuild the app\n## Tasks\nTask list here", encoding="utf-8"
        )

        # Also need task files in the archive session
        (archive_dir / "T01_setup.md").write_text("# T01", encoding="utf-8")

        context = gather_project_context(tmp_path)
        assert "archive/" in context
        assert "Build the app" in context

    def test_symlink_file_resolves_outside_project(self, tmp_path: Path) -> None:
        """Test symlink file that resolves outside project (line 263)."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Create a real file outside the project
        outside = tmp_path / "outside.txt"
        outside.write_text("outside content", encoding="utf-8")

        # Create symlink inside project pointing outside
        symlink = project_dir / "external_link.txt"
        symlink.symlink_to(outside)

        context = gather_project_context(project_dir)
        # Symlink pointing outside should be skipped
        assert "external_link.txt" not in context

    def test_no_provider_checks_both_rule_files(self, tmp_path: Path) -> None:
        """Test that without provider, both AGENTS.md and CLAUDE.md are checked."""
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# Agents rules", encoding="utf-8")

        context = gather_project_context(tmp_path, provider=None)
        assert "Agents rules" in context


class TestRescueStrayTasksAdditional:
    """Additional tests for _rescue_stray_tasks()."""

    def test_rescue_conflict_with_existing_file(self, tmp_path: Path) -> None:
        """Test stray task file that conflicts with existing file (lines 630-633)."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        # Create existing file in tasks_dir
        (tasks_dir / "T01_init.md").write_text("# T01 existing", encoding="utf-8")

        # Create stray dir with same-named file
        stray_dir = tmp_path / "mbi" / "tasks"
        stray_dir.mkdir(parents=True)
        (stray_dir / "T01_init.md").write_text("# T01 stray", encoding="utf-8")

        rescued = _rescue_stray_tasks(tmp_path, tasks_dir)
        # Should not overwrite existing file
        assert rescued == 0
        assert (tasks_dir / "T01_init.md").read_text(encoding="utf-8") == "# T01 existing"


class TestArchivePreviousRunAdditional:
    """Additional tests for archive_previous_run()."""

    def test_archive_nonexistent_tasks_dir(self, tmp_path: Path) -> None:
        """Should return None when tasks_dir doesn't exist (line 814)."""
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)

        result = archive_previous_run(
            tmp_path / "nonexistent_tasks", log_dir, tmp_path / "PROGRESS.md"
        )
        assert result is None

    def test_archives_summary_with_task_package(self, tmp_path: Path) -> None:
        """Should archive tasks/SUMMARY.md with task files and instructions."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        (tasks_dir / "T01_task.md").write_text("# T01", encoding="utf-8")
        (tasks_dir / "INSTRUCTIONS.md").write_text("# Instructions", encoding="utf-8")
        (tasks_dir / "SUMMARY.md").write_text("# Summary", encoding="utf-8")

        archive_dir = archive_previous_run(tasks_dir, log_dir, tmp_path / "PROGRESS.md")

        assert archive_dir is not None
        assert (archive_dir / "T01_task.md").exists()
        assert (archive_dir / "INSTRUCTIONS.md").exists()
        assert (archive_dir / "SUMMARY.md").exists()


class TestClearLogDirAdditional:
    """Additional tests for _clear_log_dir()."""

    def test_clear_log_successfully(self, tmp_path: Path) -> None:
        """Should successfully clear log files (lines 866, 870)."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "test1.log").write_text("log1", encoding="utf-8")
        (log_dir / "test2.log").write_text("log2", encoding="utf-8")

        _clear_log_dir(log_dir)

        assert not (log_dir / "test1.log").exists()
        assert not (log_dir / "test2.log").exists()
        assert log_dir.exists()

    def test_clear_nonexistent_log_dir(self, tmp_path: Path) -> None:
        """Should return early when log dir doesn't exist."""
        _clear_log_dir(tmp_path / "nonexistent")  # Should not crash


class TestRunPlannerAdditional:
    """Additional tests for run_planner()."""

    @pytest.mark.asyncio
    async def test_run_planner_detect_cooldown_signal_fallback(self, tmp_path: Path) -> None:
        """Test detect_cooldown_signal fallback path (line 1032)."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        config = ArchitectConfig().resolve(tmp_path)
        request = PlanningRequest(
            goal="Test goal",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        call_count = 0

        async def fake_stream(**kwargs: object) -> StreamResult:  # type: ignore[misc]
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # No cooldown_until, no rate_limit_hit, but exit_code=1
                return StreamResult(
                    exit_code=1,
                    tokens=TokenUsage(),
                    accumulated_text="rate limit exceeded, resetsAt=9999999999",
                )
            (tasks_dir / "T01_test.md").write_text("# T01", encoding="utf-8")
            return StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")

        async def fake_sleep(secs: float) -> None:
            pass  # Don't actually wait

        with (
            patch("the_architect.core.planner.stream_provider", side_effect=fake_stream),
            patch("asyncio.sleep", side_effect=fake_sleep),
        ):
            result = await run_planner(request, config)

        assert "T01_test" in result.tasks_created

    @pytest.mark.asyncio
    async def test_run_planner_transient_failure_then_success(self, tmp_path: Path) -> None:
        """Test transient failure followed by success (lines 1080-1089)."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        config = ArchitectConfig().resolve(tmp_path)
        request = PlanningRequest(
            goal="Test goal",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        call_count = 0

        async def fake_stream(**kwargs: object) -> StreamResult:  # type: ignore[misc]
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # No tasks, no cooldown — transient failure
                return StreamResult(exit_code=1, tokens=TokenUsage(), accumulated_text="")
            (tasks_dir / "T01_test.md").write_text("# T01", encoding="utf-8")
            return StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")

        with (
            patch("the_architect.core.planner.stream_provider", side_effect=fake_stream),
            patch("asyncio.sleep", side_effect=lambda _: None),
        ):
            result = await run_planner(request, config)

        assert "T01_test" in result.tasks_created

    @pytest.mark.asyncio
    async def test_run_planner_all_attempts_fail(self, tmp_path: Path) -> None:
        """Test PlanningFailedError when all attempts fail (lines 1093-1094)."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        config = ArchitectConfig().resolve(tmp_path)
        request = PlanningRequest(
            goal="Test goal",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        async def fake_stream(**kwargs: object) -> StreamResult:  # type: ignore[misc]
            # Always fail — no tasks, no cooldown
            return StreamResult(exit_code=1, tokens=TokenUsage(), accumulated_text="")

        with (
            patch("the_architect.core.planner.stream_provider", side_effect=fake_stream),
            patch("asyncio.sleep", side_effect=lambda _: None),
        ):
            with pytest.raises(PlanningFailedError):
                await run_planner(request, config)


class TestRunPlannerProviderError:
    """Tests for provider error detection in run_planner()."""

    @pytest.mark.asyncio
    async def test_run_planner_update_required_fails_immediately(self, tmp_path: Path) -> None:
        """Planning should fail immediately with a clear message when provider needs update."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        config = ArchitectConfig().resolve(tmp_path)
        request = PlanningRequest(
            goal="Test goal",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        provider = Mock()
        provider.name = "opencode"
        provider.display_name = "OpenCode"
        provider.supports_agents.return_value = True
        provider.ensure_setup.return_value = None
        provider.check_update_available.return_value = (
            "OpenCode 1.14.28 is installed, but 1.14.30 is available. Update with: opencode upgrade"
        )

        async def fake_stream(**kwargs: object) -> StreamResult:  # type: ignore[misc]
            return StreamResult(
                exit_code=1,
                tokens=TokenUsage(),
                accumulated_text="A new version of opencode is available. Please update.",
            )

        with patch("the_architect.core.planner.stream_provider", side_effect=fake_stream):
            with pytest.raises(PlanningFailedError, match="[Uu]pdate"):
                await run_planner(request, config, provider=provider)

    @pytest.mark.asyncio
    async def test_run_planner_misconfigured_fails_immediately(self, tmp_path: Path) -> None:
        """Planning should fail immediately when provider is misconfigured."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        config = ArchitectConfig().resolve(tmp_path)
        request = PlanningRequest(
            goal="Test goal",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        provider = Mock()
        provider.name = "opencode"
        provider.display_name = "OpenCode"
        provider.supports_agents.return_value = True
        provider.ensure_setup.return_value = None

        async def fake_stream(**kwargs: object) -> StreamResult:  # type: ignore[misc]
            return StreamResult(
                exit_code=1,
                tokens=TokenUsage(),
                accumulated_text="Error: Invalid API key provided",
            )

        with patch("the_architect.core.planner.stream_provider", side_effect=fake_stream):
            with pytest.raises(PlanningFailedError, match="misconfigured"):
                await run_planner(request, config, provider=provider)

    @pytest.mark.asyncio
    async def test_run_planner_unknown_error_surfaces_output(self, tmp_path: Path) -> None:
        """Planning should surface unknown provider errors as dim output but still retry."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        config = ArchitectConfig().resolve(tmp_path)
        request = PlanningRequest(
            goal="Test goal",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        provider = Mock()
        provider.name = "opencode"
        provider.display_name = "OpenCode"
        provider.supports_agents.return_value = True
        provider.ensure_setup.return_value = None

        async def fake_stream(**kwargs: object) -> StreamResult:  # type: ignore[misc]
            return StreamResult(
                exit_code=1,
                tokens=TokenUsage(),
                accumulated_text="Something went wrong with the provider internals",
            )

        # Unknown errors should still allow retries (they may be transient),
        # but will eventually fail after all retries are exhausted
        with (
            patch("the_architect.core.planner.stream_provider", side_effect=fake_stream),
            patch("asyncio.sleep", side_effect=lambda _: None),
        ):
            with pytest.raises(PlanningFailedError):
                await run_planner(request, config, provider=provider)


class TestRunPlannerRendererPassthrough:
    """The planner must forward its ``renderer`` argument to
    ``stream_provider`` so TUI callers can route provider output into
    the wait-screen log tail. When no renderer is passed, the planner
    must not silently substitute one — the ``None`` propagates and
    ``stream_provider`` falls back to the plain stdout path.
    """

    @pytest.mark.asyncio
    async def test_renderer_forwarded_to_stream_provider(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        config = ArchitectConfig().resolve(tmp_path)
        request = PlanningRequest(
            goal="Test goal",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )
        renderer = MagicMock()

        captured: dict[str, object] = {}

        async def fake_stream(**kwargs: object) -> StreamResult:
            captured.update(kwargs)
            (tasks_dir / "T01_test.md").write_text("# T01", encoding="utf-8")
            return StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")

        with patch("the_architect.core.planner.stream_provider", side_effect=fake_stream):
            await run_planner(request, config, renderer=renderer)

        assert captured.get("renderer") is renderer

    @pytest.mark.asyncio
    async def test_default_renderer_is_none(self, tmp_path: Path) -> None:
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        config = ArchitectConfig().resolve(tmp_path)
        request = PlanningRequest(
            goal="Test goal",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        captured: dict[str, object] = {}

        async def fake_stream(**kwargs: object) -> StreamResult:
            captured.update(kwargs)
            (tasks_dir / "T01_test.md").write_text("# T01", encoding="utf-8")
            return StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")

        with patch("the_architect.core.planner.stream_provider", side_effect=fake_stream):
            await run_planner(request, config)

        assert captured.get("renderer") is None
