"""CLI and run-integration tests for the token ledger feature."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from the_architect.cli import main
from the_architect.config import ArchitectConfig
from the_architect.core.runner import TaskResult, TokenUsage
from the_architect.core.tasks import Task, TaskStatus
from the_architect.core.token_ledger import (
    LedgerRunRecord,
    ModelTokenRecord,
    TokenLedger,
    save_ledger,
)


def _sample_ledger() -> TokenLedger:
    """Return a deterministic populated token ledger for CLI tests."""
    return TokenLedger(
        records=[
            LedgerRunRecord(
                timestamp="2026-05-01T12:00:00+00:00",
                goal_summary="old goal",
                total_tokens=300,
                total_cost_estimate=0.01,
                task_count=1,
                outcome="success",
                model_breakdown=[
                    ModelTokenRecord(
                        model="gpt-4o-mini",
                        input_tokens=200,
                        output_tokens=100,
                        cost_estimate=0.00009,
                    )
                ],
            ),
            LedgerRunRecord(
                timestamp="2026-05-14T12:00:00+00:00",
                goal_summary="ship token ledger reporting",
                total_tokens=1_500,
                total_cost_estimate=0.0075,
                task_count=2,
                outcome="failure",
                model_breakdown=[
                    ModelTokenRecord(
                        model="gpt-4o",
                        input_tokens=1_000,
                        output_tokens=500,
                        cost_estimate=0.0075,
                    )
                ],
            ),
        ]
    )


def _write_sample_ledger(project: Path) -> None:
    """Persist the deterministic sample ledger to a project directory."""
    save_ledger(project, _sample_ledger())


def _pending_task(tmp_path: Path) -> Task:
    """Create a pending task object for _run_main integration tests."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    path = tasks_dir / "T01_test.md"
    path.write_text("# T01 - Test\n", encoding="utf-8")
    return Task(
        name="T01_test",
        prefix="T01",
        number=1,
        path=path,
        title="Test task",
        status=TaskStatus.PENDING,
    )


async def _successful_run_tasks(
    *args: object, **kwargs: object
) -> tuple[bool, list[TaskResult], float]:
    """Return one successful task result for _run_main tests."""
    return (
        True,
        [
            TaskResult(
                prefix="T01",
                status="done",
                tokens=TokenUsage(input_tokens=100, output_tokens=50),
                model="gpt-4o",
            )
        ],
        1.0,
    )


class TestTokenReportCommand:
    """Tests for the `architect token-report` command."""

    def test_empty_or_missing_ledger_prints_friendly_message(self, tmp_path: Path) -> None:
        """Missing ledger data should exit cleanly with a helpful message."""
        result = CliRunner().invoke(main, ["token-report", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "No token ledger data found." in result.output

    def test_populated_ledger_table_shows_runs_and_model_breakdown(self, tmp_path: Path) -> None:
        """Table output should include representative run and model summary text."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["token-report", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "Token Ledger Report" in result.output
        assert "ship token ledger" in result.output
        assert "reporting" in result.output
        assert "1.5K" in result.output
        assert "$0.01" in result.output
        assert "Failed" in result.output
        assert "Per-model cost breakdown" in result.output
        assert "gpt-4o" in result.output

    def test_json_output_contains_runs_summary_and_model_breakdown(self, tmp_path: Path) -> None:
        """JSON output should be parseable and contain summary data."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["token-report", "-p", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload["runs"]) == 2
        assert payload["summary"]["total_runs"] == 2
        assert payload["summary"]["total_tokens"] == 1_800
        assert payload["model_breakdown"][0]["model"] == "gpt-4o"

    def test_since_filters_old_records(self, tmp_path: Path) -> None:
        """--since should exclude records before the requested date."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(
            main, ["token-report", "-p", str(tmp_path), "--since", "2026-05-10", "--json"]
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert [run["goal_summary"] for run in payload["runs"]] == ["ship token ledger reporting"]

    def test_top_models_limits_table_breakdown(self, tmp_path: Path) -> None:
        """--top-models should limit the table's per-model breakdown rows."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(
            main, ["token-report", "-p", str(tmp_path), "--top-models", "1"]
        )

        assert result.exit_code == 0, result.output
        assert "gpt-4o" in result.output
        assert "gpt-4o-mini" not in result.output


class TestRunMainTokenLedgerIntegration:
    """Tests for run-completion ledger recording in _run_main."""

    def test_ledger_recorded_after_success_summary(self, tmp_path: Path) -> None:
        """Default config should load, append, and save the ledger after SUMMARY.md."""
        from the_architect.cli import _run_main

        task = _pending_task(tmp_path)
        config = ArchitectConfig(retrospective_rounds=0).resolve(tmp_path)
        ledger = TokenLedger()
        order: list[str] = []

        def fake_write_success(*args: object, **kwargs: object) -> Path:
            order.append("summary")
            return tmp_path / "tasks" / "SUMMARY.md"

        def fake_load(project: Path) -> TokenLedger:
            order.append("load")
            assert project == tmp_path
            return ledger

        def fake_append(
            loaded_ledger: TokenLedger,
            results: list[TaskResult],
            goal: str,
            duration: float,
            outcome: str = "success",
        ) -> None:
            order.append("append")
            assert loaded_ledger is ledger
            assert results[0].prefix == "T01"
            assert goal == "token goal"
            assert duration >= 0
            assert outcome == "success"

        def fake_save(project: Path, saved_ledger: TokenLedger) -> None:
            order.append("save")
            assert project == tmp_path
            assert saved_ledger is ledger

        with (
            patch("the_architect.cli.discover_tasks", return_value=[task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_successful_run_tasks),
            patch("the_architect.cli.write_success_md", side_effect=fake_write_success),
            patch("the_architect.cli.print_success_summary"),
            patch("the_architect.cli._read_goal_from_instructions", return_value="token goal"),
            patch("the_architect.cli.load_ledger", side_effect=fake_load),
            patch("the_architect.cli.append_run", side_effect=fake_append),
            patch("the_architect.cli.save_ledger", side_effect=fake_save),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(project=tmp_path, headless=True, _pre_loaded_config=config)

        assert exc_info.value.code == 0
        assert order == ["summary", "load", "append", "save"]

    def test_ledger_recording_can_be_disabled(self, tmp_path: Path) -> None:
        """token_ledger=False should skip all ledger persistence calls."""
        from the_architect.cli import _run_main

        task = _pending_task(tmp_path)
        config = ArchitectConfig(retrospective_rounds=0, token_ledger=False).resolve(tmp_path)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_successful_run_tasks),
            patch(
                "the_architect.cli.write_success_md", return_value=tmp_path / "tasks" / "SUMMARY.md"
            ),
            patch("the_architect.cli.print_success_summary"),
            patch("the_architect.cli._read_goal_from_instructions", return_value="token goal"),
            patch("the_architect.cli.load_ledger") as mock_load,
            patch("the_architect.cli.append_run") as mock_append,
            patch("the_architect.cli.save_ledger") as mock_save,
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(project=tmp_path, headless=True, _pre_loaded_config=config)

        assert exc_info.value.code == 0
        mock_load.assert_not_called()
        mock_append.assert_not_called()
        mock_save.assert_not_called()

    def test_ledger_recording_exception_is_non_fatal(self, tmp_path: Path) -> None:
        """Ledger recording failures should not fail an otherwise successful run."""
        from the_architect.cli import _run_main

        task = _pending_task(tmp_path)
        config = ArchitectConfig(retrospective_rounds=0).resolve(tmp_path)

        with (
            patch("the_architect.cli.discover_tasks", return_value=[task]),
            patch("the_architect.cli._filter_and_set_status", return_value=[task]),
            patch("the_architect.cli._run_tasks_raw", side_effect=_successful_run_tasks),
            patch(
                "the_architect.cli.write_success_md", return_value=tmp_path / "tasks" / "SUMMARY.md"
            ),
            patch("the_architect.cli.print_success_summary"),
            patch("the_architect.cli._read_goal_from_instructions", return_value="token goal"),
            patch("the_architect.cli.load_ledger", return_value=TokenLedger()),
            patch("the_architect.cli.append_run", side_effect=OSError("disk full")),
            patch("the_architect.cli.save_ledger") as mock_save,
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main(project=tmp_path, headless=True, _pre_loaded_config=config)

        assert exc_info.value.code == 0
        mock_save.assert_not_called()
