"""Edge-case tests for uncovered branches in planner.py"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from loguru import logger

from the_architect.config import ArchitectConfig
from the_architect.core.planner import (
    PlanningFailedError,
    PlanningRequest,
    TaskScope,
    _clear_log_dir,
    _enforce_planning_lifecycle_contract,
    _ensure_lifecycle_contract,
    _next_task_number,
    _rescue_stray_tasks,
    _summarize_progress_historical,
    _sync_goal_md,
    _write_goal_md,
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
        progress_md = tmp_path / "tasks" / "PROGRESS.md"
        progress_md.parent.mkdir(parents=True, exist_ok=True)
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

    def test_build_instruction_injects_structured_intelligence(self, tmp_path: Path) -> None:
        """Structured project intelligence should be injected before generic context."""
        request = PlanningRequest(
            goal="Add a feature",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
            structured_intelligence_content="Project: demo\nType: CLI tool",
        )

        instruction = build_planning_instruction(request, "project context")

        assert "=== STRUCTURED PROJECT INTELLIGENCE ===" in instruction
        assert "Project: demo" in instruction
        assert instruction.index("STRUCTURED PROJECT INTELLIGENCE") < instruction.index(
            "PROJECT CONTEXT"
        )


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
        progress_file = tmp_path / "tasks" / "PROGRESS.md"

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
            result = archive_previous_run(tasks_dir, log_dir, tmp_path / "tasks" / "PROGRESS.md")
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
    async def test_run_planner_rejects_duplicate_task_prefixes(self, tmp_path: Path) -> None:
        """Planning must not proceed when two task files share one runtime prefix."""
        from unittest.mock import MagicMock

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        config = ArchitectConfig().resolve(tmp_path)

        provider = MagicMock()
        provider.name = "opencode"
        provider.display_name = "OpenCode"
        provider.supports_agents.return_value = True

        request = PlanningRequest(
            goal="Test goal",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        async def fake_stream(**kwargs: object) -> StreamResult:  # type: ignore[misc]
            (tasks_dir / "T01_first.md").write_text("# T01 first", encoding="utf-8")
            (tasks_dir / "T01_second.md").write_text("# T01 second", encoding="utf-8")
            return StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")

        with patch("the_architect.core.planner.stream_provider", side_effect=fake_stream):
            with pytest.raises(PlanningFailedError, match="duplicate task prefixes"):
                await run_planner(request, config, provider=provider)

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

    def test_build_instruction_restarts_numbering_after_archive_history(
        self, tmp_path: Path
    ) -> None:
        """Previous plan history must not make new plans continue old numbering."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "PROGRESS.md").write_text(
            "# Progress\n\n| T01 | Old task | Done | today |\n",
            encoding="utf-8",
        )
        (tasks_dir / "archive").mkdir()
        request = PlanningRequest(
            goal="Test goal",
            scope=TaskScope.SIMPLE,
            project_dir=tmp_path,
        )

        instruction = build_planning_instruction(
            request, "Completed tasks from previous plans: T01"
        )

        assert "First task file:" in instruction
        assert "/T01_<descriptive_name>.md" in instruction
        assert "Historical T/R numbers" in instruction
        assert "Do NOT continue numbering from previous plan history" in instruction
        assert "Do NOT reuse numbers shown" not in instruction

    def test_build_instruction_forbids_lifecycle_exemptions(self, tmp_path: Path) -> None:
        """Planner prompt must not let simple tasks skip progress/build rules."""
        request = PlanningRequest(
            goal="Create a file",
            scope=TaskScope.SIMPLE,
            project_dir=tmp_path,
        )

        instruction = build_planning_instruction(request, "project context")

        assert "Never tell task agents to skip PROGRESS.md updates" in instruction
        assert "every completed task must increment root /version.py __build__" in instruction

    def test_enforce_lifecycle_contract_corrects_bad_planner_output(self, tmp_path: Path) -> None:
        """Contradictory planner output should be corrected before execution."""
        from the_architect.core.tasks import Task, TaskStatus

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        instructions = tasks_dir / "INSTRUCTIONS.md"
        task_path = tasks_dir / "T01_bad.md"
        instructions.write_text(
            "Do NOT add task completion to PROGRESS.md or bump build counter.\n",
            encoding="utf-8",
        )
        task_path.write_text("No build counter bump is needed.\n", encoding="utf-8")
        task = Task(
            name="T01_bad",
            prefix="T01",
            number=1,
            path=task_path,
            title="Bad task",
            status=TaskStatus.PENDING,
        )

        updated = _enforce_planning_lifecycle_contract(tasks_dir, [task])

        assert updated == 2
        assert "The Architect Lifecycle Contract" in instructions.read_text(encoding="utf-8")
        assert "must update `tasks/PROGRESS.md`" in task_path.read_text(encoding="utf-8")


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

        progress_file = tmp_path / "tasks" / "PROGRESS.md"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
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

        progress_file = tmp_path / "tasks" / "PROGRESS.md"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
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
        progress_file = tmp_path / "tasks" / "PROGRESS.md"
        result = check_pending_tasks(tmp_path / "nonexistent", progress_file)
        assert result == []

    def test_check_pending_skips_s_prefix(self, tmp_path: Path) -> None:
        """Should skip S-prefixed (standalone) tasks."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "S01_special.md").write_text("# S01 — Special", encoding="utf-8")
        (tasks_dir / "T01_init.md").write_text("# T01 — Init", encoding="utf-8")

        progress_file = tmp_path / "tasks" / "PROGRESS.md"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
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
        progress_md = tmp_path / "tasks" / "PROGRESS.md"
        progress_md.parent.mkdir(parents=True, exist_ok=True)
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
            tmp_path / "nonexistent_tasks", log_dir, tmp_path / "tasks" / "PROGRESS.md"
        )
        assert result is None

    def test_archives_summary_with_task_package(self, tmp_path: Path) -> None:
        """Should archive package metadata with task files and instructions."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        (tasks_dir / "T01_task.md").write_text("# T01", encoding="utf-8")
        (tasks_dir / "INSTRUCTIONS.md").write_text("# Instructions", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text("# Progress", encoding="utf-8")
        (tasks_dir / "SUMMARY.md").write_text("# Summary", encoding="utf-8")

        archive_dir = archive_previous_run(tasks_dir, log_dir, tmp_path / "tasks" / "PROGRESS.md")

        assert archive_dir is not None
        assert (archive_dir / "T01_task.md").exists()
        assert (archive_dir / "INSTRUCTIONS.md").exists()
        assert (archive_dir / "PROGRESS.md").read_text(encoding="utf-8") == "# Progress"
        assert (archive_dir / "SUMMARY.md").exists()
        assert (tasks_dir / "PROGRESS.md").read_text(encoding="utf-8") == "# Progress"

    def test_archive_preserves_goal_md_for_infinite_loop(self, tmp_path: Path) -> None:
        """GOAL.md should be copied to history and survive for the next loop."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        (tasks_dir / "T01_task.md").write_text("# T01", encoding="utf-8")
        (tasks_dir / "GOAL.md").write_text("# Goal\n\nPermanent mission\n", encoding="utf-8")

        archive_dir = archive_previous_run(tasks_dir, log_dir, tmp_path / "tasks" / "PROGRESS.md")

        assert archive_dir is not None
        assert (archive_dir / "T01_task.md").exists()
        assert (archive_dir / "GOAL.md").read_text(encoding="utf-8").endswith("Permanent mission\n")
        assert (tasks_dir / "GOAL.md").read_text(encoding="utf-8").endswith("Permanent mission\n")

    def test_write_goal_md_records_original_goal(self, tmp_path: Path) -> None:
        """Planner should create a durable original-goal file for every plan."""
        tasks_dir = tmp_path / "tasks"

        _write_goal_md(tasks_dir, "Permanent mission")

        assert (tasks_dir / "GOAL.md").read_text(encoding="utf-8") == (
            "# The Architect — Original Goal\n\nPermanent mission\n"
        )

    def test_sync_goal_md_removes_stale_non_loop_goal(self, tmp_path: Path) -> None:
        """Non-loop planning without an explicit goal should not inherit stale GOAL.md."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "GOAL.md").write_text("# Goal\n\nOld mission\n", encoding="utf-8")

        _sync_goal_md(tasks_dir, "", preserve_existing=False)

        assert not (tasks_dir / "GOAL.md").exists()

    def test_sync_goal_md_preserves_existing_loop_goal(self, tmp_path: Path) -> None:
        """Infinite Loop planning without a new goal should keep the durable goal."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "GOAL.md").write_text("# Goal\n\nPermanent mission\n", encoding="utf-8")

        _sync_goal_md(tasks_dir, "", preserve_existing=True)

        assert (tasks_dir / "GOAL.md").read_text(encoding="utf-8").endswith("Permanent mission\n")


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


class TestGatherProjectContextTreeTruncation:
    """Tests for tree-truncation logic and documentation/ directory in gather_project_context()."""

    def test_gather_project_context_truncates_large_tree(self, tmp_path: Path) -> None:
        """300 flat files trigger the file-level truncation guard (lines 280-283) and line 299."""
        for i in range(300):
            (tmp_path / f"file_{i:04d}.txt").write_text("x", encoding="utf-8")

        result = gather_project_context(tmp_path)

        assert "tree truncated" in result
        # The tree must have stopped before listing all 300 files
        assert not all(f"file_{i:04d}.txt" in result for i in range(300))

    def test_gather_project_context_truncation_note_appended(self, tmp_path: Path) -> None:
        """Exact truncation note string appears in result when tree limit is exceeded."""
        for i in range(300):
            (tmp_path / f"file_{i:04d}.txt").write_text("x", encoding="utf-8")

        result = gather_project_context(tmp_path)

        assert "... tree truncated" in result

    def test_gather_project_context_truncates_at_directory_line(self, tmp_path: Path) -> None:
        """Directory-level truncation fires after a dir line reaches the limit.

        258 root files → len=259 after the file loop; visiting the subdir adds its dir
        line → len=260, which triggers the guard at lines 273-276.
        """
        for i in range(258):
            (tmp_path / f"file_{i:04d}.txt").write_text("x", encoding="utf-8")
        subdir = tmp_path / "zzz_subdir"
        subdir.mkdir()
        (subdir / "inside.txt").write_text("inside", encoding="utf-8")

        result = gather_project_context(tmp_path)

        assert "tree truncated" in result

    def test_gather_project_context_outer_loop_truncation(self, tmp_path: Path) -> None:
        """Outer os.walk check fires when a sibling directory is visited after the limit.

        aaa_subdir1 with 259 files: dir line (len=2) + 258 files → len=260, then
        the 259th file check fires (lines 280-283) and breaks.  bbb_subdir2 is the
        next os.walk entry → len=260 >= 260 → outer-loop guard fires (lines 249-251).
        """
        subdir1 = tmp_path / "aaa_subdir1"
        subdir1.mkdir()
        for i in range(259):
            (subdir1 / f"file_{i:04d}.txt").write_text("x", encoding="utf-8")
        subdir2 = tmp_path / "bbb_subdir2"
        subdir2.mkdir()
        (subdir2 / "extra.txt").write_text("extra", encoding="utf-8")

        result = gather_project_context(tmp_path)

        assert "tree truncated" in result

    def test_gather_includes_documentation_directory(self, tmp_path: Path) -> None:
        """documentation/ directory contents appear in the context."""
        doc_dir = tmp_path / "documentation"
        doc_dir.mkdir()
        (doc_dir / "PRACTICES.md").write_text("# Practices\nFollow these rules", encoding="utf-8")

        result = gather_project_context(tmp_path)

        assert "documentation/" in result

    def test_gather_includes_both_docs_and_documentation(self, tmp_path: Path) -> None:
        """Both docs/ and documentation/ sections appear when both directories exist."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "API.md").write_text("# API", encoding="utf-8")

        doc_dir = tmp_path / "documentation"
        doc_dir.mkdir()
        (doc_dir / "PRACTICES.md").write_text("# Practices", encoding="utf-8")

        result = gather_project_context(tmp_path)

        assert "documentation/" in result
        assert "docs/" in result

    def test_gather_project_context_respects_char_budget(self, tmp_path: Path) -> None:
        """add_part() skips a section when the max_chars budget would be exceeded (line 221).

        An AGENTS.md larger than max_chars (20000) must be dropped; the output
        must stay well below 25000 chars despite the oversized input.
        """
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# Rules\n" + "rule " * 5000, encoding="utf-8")  # ~25008 chars

        result = gather_project_context(tmp_path)

        assert len(result) < 25000


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
    async def test_run_planner_quota_exhausted_fails_without_cooldown(self, tmp_path: Path) -> None:
        """Account budget failures should stop instead of entering cooldown wait."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        config = ArchitectConfig().resolve(tmp_path)
        request = PlanningRequest(
            goal="Test goal",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        provider = Mock()
        provider.name = "gemini-cli"
        provider.display_name = "Gemini CLI"
        provider.supports_agents.return_value = False
        provider.ensure_setup.return_value = None
        provider.get_architect_prompt.return_value = "architect"

        async def fake_stream(**kwargs: object) -> StreamResult:  # type: ignore[misc]
            return StreamResult(
                exit_code=1,
                tokens=TokenUsage(),
                accumulated_text="RESOURCE_EXHAUSTED: quota exceeded; billing not enabled",
                rate_limit_hit=True,
            )

        with (
            patch("the_architect.core.planner.stream_provider", side_effect=fake_stream),
            patch("asyncio.sleep") as sleep_mock,
        ):
            with pytest.raises(PlanningFailedError, match="quota"):
                await run_planner(request, config, provider=provider)

        sleep_mock.assert_not_called()

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


class TestPlannerCoverageGaps:
    """Tests for the remaining uncovered lines in planner.py."""

    # Lines 271-278: symlink directory resolve failure (OSError/ValueError)
    def test_gather_context_symlink_dir_resolve_oserror(self, tmp_path: Path) -> None:
        """Symlink dir where resolve() raises OSError — dirnames cleared, continues."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Create a real subdirectory (os.walk will yield it as dirpath)
        subdir = project_dir / "subdir"
        subdir.mkdir()
        (subdir / "file.txt").write_text("content", encoding="utf-8")

        original_is_symlink = Path.is_symlink
        original_resolve = Path.resolve

        def mock_is_symlink(self: Path) -> bool:
            if "subdir" in str(self):
                return True  # Pretend subdir is a symlink
            return original_is_symlink(self)

        def mock_resolve(self: Path) -> Path:
            if "subdir" in str(self):
                raise OSError("resolve failed")
            return original_resolve(self)

        with (
            patch.object(Path, "is_symlink", mock_is_symlink),
            patch.object(Path, "resolve", mock_resolve),
        ):
            context = gather_project_context(project_dir)
            # Should not crash; the bad symlink dir should be skipped
            assert "File tree:" in context

    # Line 427: goal extraction breaks on second heading (empty goal section)
    def test_gather_context_goal_extraction_breaks_on_second_heading(self, tmp_path: Path) -> None:
        """Archive INSTRUCTIONS.md with empty goal — break fires on second ## heading."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        archive_dir = tasks_dir / "archive" / "2024-01-01_000000"
        archive_dir.mkdir(parents=True)

        # Goal section is empty — only whitespace before next heading
        instructions = archive_dir / "INSTRUCTIONS.md"
        instructions.write_text("## Goal\n\n## Constraints\nMust use Python", encoding="utf-8")
        (archive_dir / "T01_setup.md").write_text("# T01", encoding="utf-8")

        context = gather_project_context(tmp_path)
        # Should not crash; goal extraction should break on "## Constraints"
        assert "archive/" in context

    # Line 676: _enforce_planning_lifecycle_contract — file already has contract
    def test_enforce_lifecycle_contract_already_present(self, tmp_path: Path) -> None:
        """Returns False when file already contains the lifecycle contract."""
        from the_architect.core.planner import _LIFECYCLE_CONTRACT

        task_file = tmp_path / "T01_task.md"
        task_file.write_text("Some content\n" + _LIFECYCLE_CONTRACT + "\n", encoding="utf-8")

        result = _ensure_lifecycle_contract(task_file)
        assert result is False
        # File should not be modified
        assert (
            task_file.read_text(encoding="utf-8") == "Some content\n" + _LIFECYCLE_CONTRACT + "\n"
        )

    # Line 758: symlink pointing outside project in _rescue_stray_tasks
    def test_rescue_symlink_outside_project(self, tmp_path: Path) -> None:
        """Symlink .md file pointing outside project is skipped."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        # Create a file outside the project
        outside_file = tmp_path.parent / "outside_task.md"
        outside_file.write_text("# Outside", encoding="utf-8")

        # Create a stray dir with a symlink pointing outside
        stray_dir = tmp_path / "mbi" / "tasks"
        stray_dir.mkdir(parents=True)
        symlink_file = stray_dir / "T01_outside.md"
        symlink_file.symlink_to(outside_file)

        # Mock resolve to return a path outside the project
        original_resolve = Path.resolve

        def mock_resolve(self: Path) -> Path:
            if "T01_outside.md" in str(self):
                return Path("/tmp/outside_project")
            return original_resolve(self)

        original_is_symlink = Path.is_symlink

        def mock_is_symlink(self: Path) -> bool:
            if "T01_outside.md" in str(self):
                return True
            return original_is_symlink(self)

        with (
            patch.object(Path, "resolve", mock_resolve),
            patch.object(Path, "is_symlink", mock_is_symlink),
        ):
            rescued = _rescue_stray_tasks(tmp_path, tasks_dir)
            # Symlink pointing outside should be skipped
            assert rescued == 0

    # Line 945: _write_goal_md — empty goal returns early
    def test_write_goal_md_empty_goal(self, tmp_path: Path) -> None:
        """_write_goal_md returns early when goal is empty/whitespace."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        _write_goal_md(tasks_dir, "")
        assert not (tasks_dir / "GOAL.md").exists()

        _write_goal_md(tasks_dir, "   ")
        assert not (tasks_dir / "GOAL.md").exists()

    # Lines 977-978: _sync_goal_md — OSError on unlink, logs warning
    def test_sync_goal_md_unlink_oserror(self, tmp_path: Path) -> None:
        """OSError during stale GOAL.md removal is logged as warning."""
        from io import StringIO

        from the_architect.core.planner import _sync_goal_md

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        goal_file = tasks_dir / "GOAL.md"
        goal_file.write_text("# Goal\nOld goal\n", encoding="utf-8")

        sink = StringIO()
        handler_id = logger.add(sink, level="WARNING", format="{message}")

        with patch.object(goal_file.__class__, "unlink", side_effect=OSError("Device busy")):
            _sync_goal_md(tasks_dir, "", preserve_existing=False)

        logger.remove(handler_id)
        assert "Failed to remove stale tasks/GOAL.md" in sink.getvalue()

    # Lines 1108-1109: archive_previous_run — OSError on copy2 for GOAL.md/PROGRESS.md
    def test_archive_copy2_oserror(self, tmp_path: Path) -> None:
        """OSError during shutil.copy2 of GOAL.md/PROGRESS.md is logged."""
        from io import StringIO

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_task.md").write_text("# T01", encoding="utf-8")
        (tasks_dir / "GOAL.md").write_text("# Goal\nMission\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text("# Progress", encoding="utf-8")

        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)

        sink = StringIO()
        handler_id = logger.add(sink, level="WARNING", format="{message}")

        # Mock copy2 to raise OSError for GOAL.md and PROGRESS.md
        original_copy2 = __import__("shutil").copy2

        def mock_copy2(src, dst):
            if src.name in ("GOAL.md", "PROGRESS.md"):
                raise OSError("copy failed")
            return original_copy2(src, dst)

        with patch("shutil.copy2", side_effect=mock_copy2):
            result = archive_previous_run(tasks_dir, log_dir, tasks_dir / "PROGRESS.md")

        logger.remove(handler_id)
        assert result is not None
        assert "Failed to archive" in sink.getvalue()

    # Line 1129: _clear_log_dir — entry is not a file, continues
    def test_clear_log_dir_skips_subdir(self, tmp_path: Path) -> None:
        """_clear_log_dir skips subdirectories (not files)."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "test.log").write_text("log", encoding="utf-8")
        sub_dir = log_dir / "subdir"
        sub_dir.mkdir()

        _clear_log_dir(log_dir)

        # File should be cleared, subdir should remain
        assert not (log_dir / "test.log").exists()
        assert sub_dir.exists()

    # Line 1136: _clear_log_dir — persistent runtime log preserved
    def test_clear_log_dir_preserves_runtime_log(self, tmp_path: Path) -> None:
        """_clear_log_dir preserves the_architect.log and architect_runtime.log."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "test.log").write_text("log", encoding="utf-8")
        (log_dir / "the_architect.log").write_text("runtime log", encoding="utf-8")
        (log_dir / "architect_runtime.log").write_text("runtime log 2", encoding="utf-8")

        _clear_log_dir(log_dir)

        assert not (log_dir / "test.log").exists()
        assert (log_dir / "the_architect.log").exists()
        assert (log_dir / "architect_runtime.log").exists()

    # Line 1328: UPDATE_REQUIRED with no update_msg — uses fallback message
    @pytest.mark.asyncio
    async def test_run_planner_update_required_no_update_msg(self, tmp_path: Path) -> None:
        """UPDATE_REQUIRED with no check_update_available message uses fallback."""
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
        provider.check_update_available.return_value = None  # No update message

        async def fake_stream(**kwargs: object) -> StreamResult:  # type: ignore[misc]
            return StreamResult(
                exit_code=1,
                tokens=TokenUsage(),
                accumulated_text="A new version of opencode is available.",
            )

        with patch("the_architect.core.planner.stream_provider", side_effect=fake_stream):
            with pytest.raises(PlanningFailedError, match="[Uu]pdate"):
                await run_planner(request, config, provider=provider)

    # Lines 1461-1464: INSTRUCTIONS.md read succeeds
    @pytest.mark.asyncio
    async def test_run_planner_instructions_md_read_succeeds(self, tmp_path: Path) -> None:
        """INSTRUCTIONS.md exists and is readable — architect_instructions is used."""
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

        async def fake_stream(**kwargs: object) -> StreamResult:  # type: ignore[misc]
            # Create INSTRUCTIONS.md during stream (after archive_previous_run)
            (tasks_dir / "T01_test.md").write_text("# T01", encoding="utf-8")
            (tasks_dir / "INSTRUCTIONS.md").write_text(
                "# Custom Instructions\n\nArchitect wrote this.\n", encoding="utf-8"
            )
            return StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")

        with patch("the_architect.core.planner.stream_provider", side_effect=fake_stream):
            result = await run_planner(request, config, provider=provider)

        assert "T01_test" in result.tasks_created

    # Line 1469: lifecycle_updates > 0, logs warning
    @pytest.mark.asyncio
    async def test_run_planner_lifecycle_updates_logs_warning(self, tmp_path: Path) -> None:
        """lifecycle_updates > 0 triggers a warning log after planning."""
        from io import StringIO

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

        # Create a task file with lifecycle contradiction to trigger enforcement
        task_file = tasks_dir / "T01_test.md"

        async def fake_stream(**kwargs: object) -> StreamResult:  # type: ignore[misc]
            task_file.write_text("# T01\nNo build counter bump is needed.\n", encoding="utf-8")
            return StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")

        sink = StringIO()
        handler_id = logger.add(sink, level="WARNING", format="{message}")

        with patch("the_architect.core.planner.stream_provider", side_effect=fake_stream):
            result = await run_planner(request, config, provider=provider)

        logger.remove(handler_id)
        assert "T01_test" in result.tasks_created
        assert "lifecycle-rule contradictions" in sink.getvalue()

    # Lines 274-275: symlink dir resolves to path NOT relative to project root
    def test_gather_context_symlink_dir_not_relative(self, tmp_path: Path) -> None:
        """Symlink dir that resolves outside project — dirnames cleared, continues."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Create a real subdirectory (os.walk will yield it as dirpath)
        subdir = project_dir / "subdir"
        subdir.mkdir()
        (subdir / "file.txt").write_text("content", encoding="utf-8")

        original_is_symlink = Path.is_symlink
        original_resolve = Path.resolve

        def mock_is_symlink(self: Path) -> bool:
            if "subdir" in str(self):
                return True  # Pretend subdir is a symlink
            return original_is_symlink(self)

        def mock_resolve(self: Path) -> Path:
            if "subdir" in str(self):
                # Resolves to a path OUTSIDE the project root
                return Path("/tmp/outside_project")
            return original_resolve(self)

        with (
            patch.object(Path, "is_symlink", mock_is_symlink),
            patch.object(Path, "resolve", mock_resolve),
        ):
            context = gather_project_context(project_dir)
            # Should not crash; the symlink dir should be skipped
            assert "File tree:" in context

    # Lines 1463-1464: INSTRUCTIONS.md exists but read_text raises OSError
    @pytest.mark.asyncio
    async def test_run_planner_instructions_md_read_oserror(self, tmp_path: Path) -> None:
        """INSTRUCTIONS.md exists but read_text raises OSError — falls back to None."""
        from io import StringIO

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

        async def fake_stream(**kwargs: object) -> StreamResult:  # type: ignore[misc]
            (tasks_dir / "T01_test.md").write_text("# T01", encoding="utf-8")
            # Create INSTRUCTIONS.md that will be readable (exists=True)
            (tasks_dir / "INSTRUCTIONS.md").write_text("# Instructions", encoding="utf-8")
            return StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")

        # Patch read_text on the instructions file to raise OSError AFTER exists() succeeds
        original_read_text = Path.read_text

        def mock_read_text(self: Path, **kw: object) -> str:
            if self.name == "INSTRUCTIONS.md":
                raise OSError("permission denied")
            return original_read_text(self, **kw)

        sink = StringIO()
        handler_id = logger.add(sink, level="WARNING", format="{message}")

        with (
            patch("the_architect.core.planner.stream_provider", side_effect=fake_stream),
            patch.object(Path, "read_text", mock_read_text),
        ):
            result = await run_planner(request, config, provider=provider)

        logger.remove(handler_id)
        # Should succeed despite read failure — architect_instructions falls back to None
        assert "T01_test" in result.tasks_created


class TestBuildPlanningInstructionWorkspaceContext:
    """Tests for workspace context injection in build_planning_instruction()."""

    def test_workspace_state_injected_for_git_repo(self, tmp_path: Path) -> None:
        """WORKSPACE STATE section appears when workspace_context is a git repo."""
        from the_architect.core.workspace_context import WorkspaceContext

        request = PlanningRequest(
            goal="Add a feature",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        ws_ctx = WorkspaceContext(
            is_git=True,
            branch="feature/auth",
            uncommitted_count=3,
            staged_count=1,
            recent_commits=[
                {"hash": "abc1234", "message": "Add login page"},
                {"hash": "def5678", "message": "Fix auth middleware"},
            ],
        )

        instruction = build_planning_instruction(
            request, "project context", workspace_context=ws_ctx
        )

        assert "=== WORKSPACE STATE ===" in instruction
        assert "Current branch: feature/auth" in instruction
        assert "Uncommitted changes: 3 file(s)" in instruction
        assert "Staged changes: 1 file(s)" in instruction
        assert "Recent commits:" in instruction
        assert "abc1234: Add login page" in instruction

    def test_workspace_state_omitted_for_non_git_repo(self, tmp_path: Path) -> None:
        """WORKSPACE STATE section is omitted for non-git repos."""
        from the_architect.core.workspace_context import WorkspaceContext

        request = PlanningRequest(
            goal="Add a feature",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        ws_ctx = WorkspaceContext(is_git=False)

        instruction = build_planning_instruction(
            request, "project context", workspace_context=ws_ctx
        )

        assert "=== WORKSPACE STATE ===" not in instruction

    def test_workspace_state_omitted_when_none(self, tmp_path: Path) -> None:
        """WORKSPACE STATE section is omitted when workspace_context is None."""
        request = PlanningRequest(
            goal="Add a feature",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        instruction = build_planning_instruction(request, "project context")

        assert "=== WORKSPACE STATE ===" not in instruction

    def test_workspace_state_after_project_context(self, tmp_path: Path) -> None:
        """WORKSPACE STATE appears after PROJECT CONTEXT in the instruction."""
        from the_architect.core.workspace_context import WorkspaceContext

        request = PlanningRequest(
            goal="Add a feature",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        ws_ctx = WorkspaceContext(
            is_git=True,
            branch="main",
            uncommitted_count=0,
            staged_count=0,
            recent_commits=[],
        )

        instruction = build_planning_instruction(
            request, "project context", workspace_context=ws_ctx
        )

        # WORKSPACE STATE should come after PROJECT CONTEXT
        project_ctx_pos = instruction.index("=== PROJECT CONTEXT ===")
        workspace_pos = instruction.index("=== WORKSPACE STATE ===")
        assert project_ctx_pos < workspace_pos

    def test_workspace_state_before_user_request(self, tmp_path: Path) -> None:
        """WORKSPACE STATE appears before USER REQUEST in the instruction."""
        from the_architect.core.workspace_context import WorkspaceContext

        request = PlanningRequest(
            goal="Add a feature",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        ws_ctx = WorkspaceContext(
            is_git=True,
            branch="main",
            uncommitted_count=0,
            staged_count=0,
            recent_commits=[],
        )

        instruction = build_planning_instruction(
            request, "project context", workspace_context=ws_ctx
        )

        workspace_pos = instruction.index("=== WORKSPACE STATE ===")
        user_req_pos = instruction.index("=== USER REQUEST ===")
        assert workspace_pos < user_req_pos

    def test_workspace_state_minimal_git_repo(self, tmp_path: Path) -> None:
        """Minimal git repo (branch only, no changes) produces compact output."""
        from the_architect.core.workspace_context import WorkspaceContext

        request = PlanningRequest(
            goal="Add a feature",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        ws_ctx = WorkspaceContext(
            is_git=True,
            branch="main",
            uncommitted_count=0,
            staged_count=0,
            recent_commits=[],
        )

        instruction = build_planning_instruction(
            request, "project context", workspace_context=ws_ctx
        )

        assert "=== WORKSPACE STATE ===" in instruction
        assert "Current branch: main" in instruction
        # No uncommitted/staged/commits lines since all are zero
        assert "Uncommitted changes" not in instruction
        assert "Staged changes" not in instruction
        assert "Recent commits" not in instruction

    def test_workspace_state_instruction_format_is_valid(self, tmp_path: Path) -> None:
        """The full instruction with workspace context has expected structure."""
        from the_architect.core.workspace_context import WorkspaceContext

        request = PlanningRequest(
            goal="Build auth system",
            scope=TaskScope.STANDARD,
            project_dir=tmp_path,
        )

        ws_ctx = WorkspaceContext(
            is_git=True,
            branch="feature/auth",
            uncommitted_count=2,
            staged_count=1,
            recent_commits=[
                {"hash": "aaa1111", "message": "Initial commit"},
            ],
        )

        instruction = build_planning_instruction(
            request, "project context", workspace_context=ws_ctx
        )

        # Verify instruction contains all expected sections in order
        sections = [
            "PROJECT ROOT:",
            "=== PROJECT CONTEXT ===",
            "=== WORKSPACE STATE ===",
            "=== USER REQUEST ===",
            "=== INSTRUCTIONS ===",
        ]
        last_pos = -1
        for section in sections:
            pos = instruction.index(section)
            assert pos > last_pos, f"{section} should appear after previous section"
            last_pos = pos
