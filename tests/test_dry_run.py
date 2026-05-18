"""Tests for the --dry-run feature.

Covers CLI flag wiring, mutual exclusion, runner short-circuit,
plan summary display (Rich tables), JSON output structure, and edge cases.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from the_architect.cli import (
    _format_dry_run_json,
    _render_dry_run_summary,
    main,
)
from the_architect.config import ArchitectConfig
from the_architect.core.provider import ArchitectProvider
from the_architect.core.tasks import Task, TaskStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    prefix: str,
    number: int,
    name: str | None = None,
    title: str | None = None,
    depends_on: list[str] | None = None,
    tmp_path: Path | None = None,
) -> Task:
    """Build a minimal Task for testing."""
    return Task(
        name=name or f"{prefix}_test",
        prefix=prefix,
        number=number,
        path=tmp_path / f"{prefix}_test.md" if tmp_path else Path(f"/tmp/{prefix}_test.md"),
        title=title or f"{prefix} title",
        status=TaskStatus.PENDING,
        depends_on=depends_on or [],
    )


def _make_config(
    standalone_mode: str = "",
    last_scope: str = "standard",
    max_retries: int = 3,
    token_budget_per_hour: int = 0,
    token_budget_per_run: int = 0,
    project_root: Path | None = None,
) -> ArchitectConfig:
    """Build a minimal ArchitectConfig for testing."""
    cfg = ArchitectConfig(
        standalone_mode=standalone_mode,
        last_scope=last_scope,
        max_retries=max_retries,
        token_budget_per_hour=token_budget_per_hour,
        token_budget_per_run=token_budget_per_run,
    )
    if project_root:
        cfg = cfg.resolve(project_root)
    return cfg


def _make_provider(display_name: str = "OpenCode") -> ArchitectProvider:
    """Build a mock ArchitectProvider for testing."""
    provider = MagicMock(spec=ArchitectProvider)
    provider.display_name = display_name
    provider.name = "opencode"
    return provider


# ---------------------------------------------------------------------------
# T03.1 — CLI flag and mutual exclusion tests
# ---------------------------------------------------------------------------


class TestDryRunFlagMutualExclusion:
    """Test that --dry-run conflicts with --from, --only, --persistent."""

    def test_dry_run_mutually_exclusive_with_from(self, tmp_path: Path) -> None:
        """--dry-run and --from together must fail with exit code 1."""
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--dry-run",
                "--from",
                "T01",
                "-p",
                str(tmp_path),
                "--goal",
                "test",
                "--headless",
            ],
        )
        assert result.exit_code == 1
        assert "--dry-run is mutually exclusive" in result.output
        assert "--from" in result.output

    def test_dry_run_mutually_exclusive_with_only(self, tmp_path: Path) -> None:
        """--dry-run and --only together must fail with exit code 1."""
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--dry-run",
                "--only",
                "T01",
                "-p",
                str(tmp_path),
                "--goal",
                "test",
                "--headless",
            ],
        )
        assert result.exit_code == 1
        assert "--dry-run is mutually exclusive" in result.output
        assert "--only" in result.output

    def test_dry_run_mutually_exclusive_with_persistent(self, tmp_path: Path) -> None:
        """--dry-run and --persistent together must fail with exit code 1."""
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--dry-run",
                "--persistent",
                "-p",
                str(tmp_path),
                "--goal",
                "test",
                "--headless",
            ],
        )
        assert result.exit_code == 1
        assert "--dry-run is mutually exclusive" in result.output
        assert "--persistent" in result.output

    def test_dry_run_mutually_exclusive_multiple_conflicts(self, tmp_path: Path) -> None:
        """Multiple conflicts listed together in error message."""
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--dry-run",
                "--from",
                "T01",
                "--only",
                "T02",
                "--persistent",
                "-p",
                str(tmp_path),
                "--goal",
                "test",
                "--headless",
            ],
        )
        assert result.exit_code == 1
        assert "--from" in result.output
        assert "--only" in result.output
        assert "--persistent" in result.output


# ---------------------------------------------------------------------------
# T03.1 — Runner short-circuit tests
# ---------------------------------------------------------------------------


class TestDryRunRunnerShortCircuit:
    """Test that dry-run exits after planning without executing tasks."""

    def test_dry_run_exits_without_executing_tasks(self, tmp_path: Path) -> None:
        """dry_run=True must trigger SystemExit(0) after displaying summary."""
        from the_architect.cli import _run_main

        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[task]),
            patch("the_architect.cli._render_dry_run_summary") as mock_render,
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                    dry_run=True,
                )
            assert exc_info.value.code == 0
            mock_render.assert_called_once()

    def test_dry_run_json_exits_without_executing(self, tmp_path: Path) -> None:
        """dry_run=True with as_json=True must output JSON and exit 0."""
        from the_architect.cli import _run_main

        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[task]),
            patch("the_architect.cli._format_dry_run_json", return_value='{"tasks":[]}'),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                    dry_run=True,
                    as_json=True,
                )
            assert exc_info.value.code == 0

    def test_dry_run_skips_task_execution(self, tmp_path: Path) -> None:
        """_run_tasks_raw must never be called during dry-run."""
        from the_architect.cli import _run_main

        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[task]),
            patch("the_architect.cli._render_dry_run_summary"),
            patch("the_architect.cli._run_tasks_raw") as mock_run,
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                    dry_run=True,
                )
            assert exc_info.value.code == 0
            mock_run.assert_not_called()

    def test_dry_run_skips_retrospective(self, tmp_path: Path) -> None:
        """Retrospective must never run during dry-run."""
        from the_architect.cli import _run_main

        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[task]),
            patch("the_architect.cli._render_dry_run_summary"),
            patch("the_architect.cli.run_retrospective") as mock_retro,
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                    dry_run=True,
                )
            assert exc_info.value.code == 0
            mock_retro.assert_not_called()

    def test_dry_run_skips_success_screen(self, tmp_path: Path) -> None:
        """Success screen must not show during dry-run."""
        from the_architect.cli import _run_main

        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[task]),
            patch("the_architect.cli._render_dry_run_summary"),
            patch("the_architect.cli.write_success_md") as mock_write,
            patch("the_architect.cli.print_success_summary") as mock_print,
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                    dry_run=True,
                )
            assert exc_info.value.code == 0
            mock_write.assert_not_called()
            mock_print.assert_not_called()


# ---------------------------------------------------------------------------
# T03.2 — JSON output structure tests
# ---------------------------------------------------------------------------


class TestFormatDryRunJson:
    """Test _format_dry_run_json() output structure and values."""

    def test_json_output_is_valid_json(self, tmp_path: Path) -> None:
        """Output must be parseable JSON."""
        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with patch("the_architect.cli.load_ledger"):
            output = _format_dry_run_json([task], config)

        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_json_has_required_top_level_keys(self, tmp_path: Path) -> None:
        """JSON must have tasks, estimate, validation, config keys."""
        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with patch("the_architect.cli.load_ledger"):
            output = _format_dry_run_json([task], config)

        parsed = json.loads(output)
        assert "tasks" in parsed
        assert "estimate" in parsed
        assert "validation" in parsed
        assert "config" in parsed

    def test_json_tasks_array_structure(self, tmp_path: Path) -> None:
        """tasks array must have task_id, title, depends_on per entry."""
        task = _make_task("T01", 1, title="First task", depends_on=["T00"], tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with patch("the_architect.cli.load_ledger"):
            output = _format_dry_run_json([task], config)

        parsed = json.loads(output)
        tasks = parsed["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["task_id"] == "T01"
        assert tasks[0]["title"] == "First task"
        assert tasks[0]["depends_on"] == ["T00"]

    def test_json_tasks_empty_depends_on(self, tmp_path: Path) -> None:
        """Task without dependencies must have empty depends_on list."""
        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with patch("the_architect.cli.load_ledger"):
            output = _format_dry_run_json([task], config)

        parsed = json.loads(output)
        assert parsed["tasks"][0]["depends_on"] == []

    def test_json_estimate_section_structure(self, tmp_path: Path) -> None:
        """estimate must have model, task_count, cost fields."""
        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with patch("the_architect.cli.load_ledger"):
            output = _format_dry_run_json([task], config)

        parsed = json.loads(output)
        estimate = parsed["estimate"]
        assert "model" in estimate
        assert "task_count" in estimate
        assert "cost_low" in estimate
        assert "cost_high" in estimate
        assert "cost_avg" in estimate
        assert "historical_runs" in estimate
        assert "confidence" in estimate
        assert estimate["task_count"] == 1

    def test_json_estimate_fallback_on_error(self, tmp_path: Path) -> None:
        """When estimate fails, fallback values are used with low confidence."""
        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        # estimate_run_cost is imported inside the function — patch at source module
        with patch(
            "the_architect.core.estimate_cost.estimate_run_cost",
            side_effect=Exception("boom"),
        ):
            output = _format_dry_run_json([task], config)

        parsed = json.loads(output)
        estimate = parsed["estimate"]
        assert estimate["confidence"] == "low"
        assert estimate["cost_low"] == 0.0
        assert estimate["cost_high"] == 0.0
        assert estimate["cost_avg"] == 0.0

    def test_json_validation_section_structure(self, tmp_path: Path) -> None:
        """validation must have cycles and missing_deps keys."""
        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with patch("the_architect.cli.load_ledger"):
            output = _format_dry_run_json([task], config)

        parsed = json.loads(output)
        validation = parsed["validation"]
        assert "cycles" in validation
        assert "missing_deps" in validation
        assert isinstance(validation["cycles"], list)
        assert isinstance(validation["missing_deps"], dict)

    def test_json_config_section_structure(self, tmp_path: Path) -> None:
        """config must have model, scope, provider, max_retries."""
        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(
            standalone_mode="gpt-4o",
            last_scope="complex",
            max_retries=5,
            project_root=tmp_path,
        )
        provider = _make_provider("TestProvider")

        with patch("the_architect.cli.load_ledger"):
            output = _format_dry_run_json([task], config, provider)

        parsed = json.loads(output)
        cfg = parsed["config"]
        assert cfg["model"] == "gpt-4o"
        assert cfg["scope"] == "complex"
        assert cfg["provider"] == "TestProvider"
        assert cfg["max_retries"] == 5

    def test_json_config_defaults(self, tmp_path: Path) -> None:
        """Config defaults when values are empty."""
        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with patch("the_architect.cli.load_ledger"):
            output = _format_dry_run_json([task], config)

        parsed = json.loads(output)
        cfg = parsed["config"]
        assert cfg["model"] == ""
        assert cfg["scope"] == "standard"
        assert cfg["provider"] == "unknown"

    def test_json_config_includes_budget_fields(self, tmp_path: Path) -> None:
        """Budget fields appear in config when set."""
        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(
            token_budget_per_hour=50000,
            token_budget_per_run=100000,
            project_root=tmp_path,
        )

        with patch("the_architect.cli.load_ledger"):
            output = _format_dry_run_json([task], config)

        parsed = json.loads(output)
        cfg = parsed["config"]
        assert cfg["token_budget_per_hour"] == 50000
        assert cfg["token_budget_per_run"] == 100000

    def test_json_config_excludes_zero_budget_fields(self, tmp_path: Path) -> None:
        """Budget fields are omitted when zero (disabled)."""
        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with patch("the_architect.cli.load_ledger"):
            output = _format_dry_run_json([task], config)

        parsed = json.loads(output)
        cfg = parsed["config"]
        assert "token_budget_per_hour" not in cfg
        assert "token_budget_per_run" not in cfg


# ---------------------------------------------------------------------------
# T03.2 — Display rendering tests
# ---------------------------------------------------------------------------


class TestRenderDryRunSummary:
    """Test _render_dry_run_summary() Rich table rendering."""

    def test_render_displays_task_list(self, tmp_path: Path) -> None:
        """Task list must render in the output."""
        task = _make_task("T01", 1, title="My Task", tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with (
            patch("the_architect.cli.load_ledger"),
            patch("the_architect.cli.console") as mock_console,
        ):
            _render_dry_run_summary([task], config)

        # Verify console.print was called (Rich table rendering)
        assert mock_console.print.called

    def test_render_displays_dry_run_banner(self, tmp_path: Path) -> None:
        """Banner must contain 'DRY-RUN MODE'."""
        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with (
            patch("the_architect.cli.load_ledger"),
            patch("the_architect.cli.console") as mock_console,
        ):
            _render_dry_run_summary([task], config)

        calls = [str(c) for c in mock_console.print.call_args_list]
        banner_found = any("DRY-RUN" in c for c in calls)
        assert banner_found

    def test_render_displays_footer(self, tmp_path: Path) -> None:
        """Footer must mention task files and running without --dry-run."""
        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with (
            patch("the_architect.cli.load_ledger"),
            patch("the_architect.cli.console") as mock_console,
        ):
            _render_dry_run_summary([task], config)

        calls = [str(c) for c in mock_console.print.call_args_list]
        footer_found = any("without --dry-run" in c for c in calls)
        assert footer_found

    def test_render_displays_config_section(self, tmp_path: Path) -> None:
        """Config section must show provider and scope."""
        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(
            last_scope="complex",
            max_retries=5,
            project_root=tmp_path,
        )
        provider = _make_provider("Claude Code")

        with (
            patch("the_architect.cli.load_ledger"),
            patch("the_architect.cli.console") as mock_console,
        ):
            _render_dry_run_summary([task], config, provider)

        calls = [str(c) for c in mock_console.print.call_args_list]
        config_found = any("Configuration" in c or "Provider" in c for c in calls)
        assert config_found

    def test_render_no_dependency_issues(self, tmp_path: Path) -> None:
        """Tasks with no dependency issues show clean validation."""
        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with (
            patch("the_architect.cli.load_ledger"),
            patch("the_architect.cli.console") as mock_console,
        ):
            _render_dry_run_summary([task], config)

        calls = [str(c) for c in mock_console.print.call_args_list]
        clean_found = any("No dependency issues" in c for c in calls)
        assert clean_found


# ---------------------------------------------------------------------------
# T03.2 — Edge case tests
# ---------------------------------------------------------------------------


class TestDryRunEdgeCases:
    """Edge cases: empty tasks, no ledger, cycles, missing deps."""

    def test_json_empty_tasks_list(self, tmp_path: Path) -> None:
        """Empty task list must produce valid JSON with zero counts."""
        config = _make_config(project_root=tmp_path)

        with patch("the_architect.cli.load_ledger"):
            output = _format_dry_run_json([], config)

        parsed = json.loads(output)
        assert parsed["tasks"] == []
        assert parsed["estimate"]["task_count"] == 0

    def test_json_with_dependency_cycles(self, tmp_path: Path) -> None:
        """Dependency cycles must appear in validation.cycles."""
        t1 = _make_task("T01", 1, depends_on=["T02"], tmp_path=tmp_path)
        t2 = _make_task("T02", 2, depends_on=["T01"], tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with patch("the_architect.cli.load_ledger"):
            output = _format_dry_run_json([t1, t2], config)

        parsed = json.loads(output)
        assert len(parsed["validation"]["cycles"]) > 0

    def test_json_with_missing_dependencies(self, tmp_path: Path) -> None:
        """Missing dependencies must appear in validation.missing_deps."""
        task = _make_task("T03", 3, depends_on=["T99"], tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with patch("the_architect.cli.load_ledger"):
            output = _format_dry_run_json([task], config)

        parsed = json.loads(output)
        assert "T99" in parsed["validation"]["missing_deps"].get("T03", [])

    def test_render_with_no_ledger_data(self, tmp_path: Path) -> None:
        """Render must handle missing ledger gracefully (fallback estimate)."""
        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        # Mock load_ledger to return empty ledger — estimate falls back to pricing table
        from the_architect.core.token_ledger import TokenLedger

        with (
            patch(
                "the_architect.cli.load_ledger",
                return_value=TokenLedger(records=[]),
            ),
            patch("the_architect.cli.console") as mock_console,
        ):
            _render_dry_run_summary([task], config)

        # Should not crash; console.print must have been called
        assert mock_console.print.called

    def test_json_with_no_ledger_data(self, tmp_path: Path) -> None:
        """JSON estimate uses fallback model when no ledger exists."""
        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        from the_architect.core.token_ledger import TokenLedger

        with patch(
            "the_architect.cli.load_ledger",
            return_value=TokenLedger(records=[]),
        ):
            output = _format_dry_run_json([task], config)

        parsed = json.loads(output)
        # Should have a default model, not crash
        assert parsed["estimate"]["model"] != ""

    def test_json_with_standalone_mode_model(self, tmp_path: Path) -> None:
        """When standalone_mode is set, it is used as the estimate model."""
        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(
            standalone_mode="anthropic/claude-sonnet-4-20250514",
            project_root=tmp_path,
        )

        from the_architect.core.token_ledger import TokenLedger

        with patch(
            "the_architect.cli.load_ledger",
            return_value=TokenLedger(records=[]),
        ):
            output = _format_dry_run_json([task], config)

        parsed = json.loads(output)
        assert parsed["estimate"]["model"] == "anthropic/claude-sonnet-4-20250514"

    def test_json_multiple_tasks(self, tmp_path: Path) -> None:
        """Multiple tasks produce correct task_count and array length."""
        tasks = [
            _make_task("T01", 1, tmp_path=tmp_path),
            _make_task("T02", 2, depends_on=["T01"], tmp_path=tmp_path),
            _make_task("T03", 3, depends_on=["T01", "T02"], tmp_path=tmp_path),
        ]
        config = _make_config(project_root=tmp_path)

        with patch("the_architect.cli.load_ledger"):
            output = _format_dry_run_json(tasks, config)

        parsed = json.loads(output)
        assert len(parsed["tasks"]) == 3
        assert parsed["estimate"]["task_count"] == 3
        assert parsed["tasks"][2]["depends_on"] == ["T01", "T02"]

    def test_render_handles_estimate_exception(self, tmp_path: Path) -> None:
        """Render must not crash when cost estimation raises."""
        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        # estimate_run_cost is imported inside the function — patch at source module
        with (
            patch(
                "the_architect.core.estimate_cost.estimate_run_cost",
                side_effect=RuntimeError("pricing unavailable"),
            ),
            patch("the_architect.cli.console") as mock_console,
        ):
            _render_dry_run_summary([task], config)

        # Must have called console.print (should show fallback message)
        assert mock_console.print.called

    def test_json_task_title_fallback_to_name(self, tmp_path: Path) -> None:
        """When title is empty string, task name is used as title in output."""
        # The Task model stores title as str (default ""). The JSON formatter
        # uses `task.title or task.name` so empty string falls back to name.
        task = Task(
            name="T01_fallback",
            prefix="T01",
            number=1,
            path=tmp_path / "T01_fallback.md",
            title="",
            status=TaskStatus.PENDING,
        )
        config = _make_config(project_root=tmp_path)

        with patch("the_architect.cli.load_ledger"):
            output = _format_dry_run_json([task], config)

        parsed = json.loads(output)
        assert parsed["tasks"][0]["title"] == "T01_fallback"

    def test_render_with_provider_none(self, tmp_path: Path) -> None:
        """Render must handle provider=None gracefully."""
        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with (
            patch("the_architect.cli.load_ledger"),
            patch("the_architect.cli.console") as mock_console,
        ):
            _render_dry_run_summary([task], config, provider=None)

        assert mock_console.print.called
        # Provider should display as "unknown"
        calls = [str(c) for c in mock_console.print.call_args_list]
        assert any("unknown" in c for c in calls)


# ---------------------------------------------------------------------------
# T03.2 — CLI-level integration tests
# ---------------------------------------------------------------------------


class TestDryRunCliIntegration:
    """End-to-end CLI integration tests for --dry-run."""

    def test_dry_run_cli_exits_cleanly(self, tmp_path: Path) -> None:
        """Full dry-run flow exits with code 0 when tasks exist."""
        from the_architect.cli import _run_main

        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[task]),
            patch("the_architect.cli._render_dry_run_summary"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                    dry_run=True,
                    as_json=False,
                )
            assert exc_info.value.code == 0

    def test_dry_run_json_cli_exits_cleanly(self, tmp_path: Path) -> None:
        """Full dry-run --json flow exits with code 0 when tasks exist."""
        from the_architect.cli import _run_main

        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[task]),
            patch("the_architect.cli._format_dry_run_json", return_value='{"tasks":[]}'),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                    dry_run=True,
                    as_json=True,
                )
            assert exc_info.value.code == 0

    def test_dry_run_with_provider_display(self, tmp_path: Path) -> None:
        """Provider display name is passed to render function."""
        from the_architect.cli import _run_main

        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)
        provider = _make_provider("OpenCode")

        with (
            patch("the_architect.cli.discover_tasks", return_value=[task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[task]),
            patch("the_architect.cli._render_dry_run_summary") as mock_render,
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                    dry_run=True,
                    provider=provider,
                )
            assert exc_info.value.code == 0
            # Verify provider was passed to render
            call_args = mock_render.call_args
            assert call_args[0][2] == provider  # third positional arg is provider

    def test_dry_run_after_planning_flow(self, tmp_path: Path) -> None:
        """Dry-run intercepts after planning completes, before execution."""
        from the_architect.cli import _run_main

        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with (
            # First discover call returns empty (triggers planning)
            patch("the_architect.cli.discover_tasks", side_effect=[[], [task]]),
            patch("the_architect.cli._filter_and_set_status", side_effect=[[], [task]]),
            patch("the_architect.cli.run_planning_mode") as mock_plan,
            patch("the_architect.cli._render_dry_run_summary") as mock_render,
            patch("the_architect.cli._run_tasks_raw") as mock_run,
        ):
            mock_plan.return_value = None
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    goal_text="test goal",
                    _pre_loaded_config=config,
                    dry_run=True,
                )
            assert exc_info.value.code == 0
            mock_plan.assert_called_once()
            mock_render.assert_called_once()
            mock_run.assert_not_called()

    def test_dry_run_respects_as_json_routing(self, tmp_path: Path) -> None:
        """as_json=True routes to _format_dry_run_json, not _render_dry_run_summary."""
        from the_architect.cli import _run_main

        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[task]),
            patch("the_architect.cli._format_dry_run_json") as mock_json,
            patch("the_architect.cli._render_dry_run_summary") as mock_render,
        ):
            mock_json.return_value = '{"tasks":[]}'
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                    dry_run=True,
                    as_json=True,
                )
            assert exc_info.value.code == 0
            mock_json.assert_called_once()
            mock_render.assert_not_called()

    def test_dry_run_without_as_json_routes_to_render(self, tmp_path: Path) -> None:
        """as_json=False (default) routes to _render_dry_run_summary."""
        from the_architect.cli import _run_main

        task = _make_task("T01", 1, tmp_path=tmp_path)
        config = _make_config(project_root=tmp_path)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[task]),
            patch("the_architect.cli._format_dry_run_json") as mock_json,
            patch("the_architect.cli._render_dry_run_summary") as mock_render,
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    project=tmp_path,
                    plan=False,
                    headless=True,
                    _pre_loaded_config=config,
                    dry_run=True,
                    as_json=False,
                )
            assert exc_info.value.code == 0
            mock_render.assert_called_once()
            mock_json.assert_not_called()
