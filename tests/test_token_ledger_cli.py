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
    LedgerTaskRecord,
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
                task_breakdown=[
                    LedgerTaskRecord(
                        task_id="T01",
                        title="Setup project",
                        status="done",
                        input_tokens=200,
                        output_tokens=100,
                        cache_read_tokens=0,
                        cache_write_tokens=0,
                        model="gpt-4o-mini",
                        cost_estimate=0.00009,
                        duration_seconds=30.0,
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
                task_breakdown=[
                    LedgerTaskRecord(
                        task_id="T01",
                        title="Implement core model",
                        status="done",
                        input_tokens=800,
                        output_tokens=400,
                        cache_read_tokens=0,
                        cache_write_tokens=0,
                        model="gpt-4o",
                        cost_estimate=0.005,
                        duration_seconds=120.0,
                    ),
                    LedgerTaskRecord(
                        task_id="T02",
                        title="Add CLI reporting",
                        status="failed",
                        input_tokens=200,
                        output_tokens=100,
                        cache_read_tokens=0,
                        cache_write_tokens=0,
                        model="gpt-4o",
                        cost_estimate=0.0025,
                        duration_seconds=60.0,
                    ),
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

    # ---------------------------------------------------------------------
    # T02.1 — Date-range filter tests (--until)
    # ---------------------------------------------------------------------

    def test_until_excludes_future_runs(self, tmp_path: Path) -> None:
        """--until should exclude records on or after the specified date."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(
            main, ["token-report", "-p", str(tmp_path), "--until", "2026-05-10", "--json"]
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        # Only the May 1 record should remain (May 14 is >= 2026-05-10, excluded)
        assert len(payload["runs"]) == 1
        assert payload["runs"][0]["goal_summary"] == "old goal"

    def test_since_and_until_produce_date_range(self, tmp_path: Path) -> None:
        """--since + --until together should produce a date range [start, end)."""
        ledger = TokenLedger(
            records=[
                LedgerRunRecord(
                    timestamp="2026-05-01T00:00:00+00:00",
                    goal_summary="before range",
                ),
                LedgerRunRecord(
                    timestamp="2026-05-05T00:00:00+00:00",
                    goal_summary="in range",
                ),
                LedgerRunRecord(
                    timestamp="2026-05-14T00:00:00+00:00",
                    goal_summary="after range",
                ),
            ]
        )
        save_ledger(tmp_path, ledger)

        result = CliRunner().invoke(
            main,
            [
                "token-report",
                "-p",
                str(tmp_path),
                "--since",
                "2026-05-02",
                "--until",
                "2026-05-06",
                "--json",
            ],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload["runs"]) == 1
        assert payload["runs"][0]["goal_summary"] == "in range"

    def test_until_json_output_filters_correctly(self, tmp_path: Path) -> None:
        """--json output with --until should produce correct filtered JSON."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(
            main, ["token-report", "-p", str(tmp_path), "--until", "2026-05-10", "--json"]
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["summary"]["total_runs"] == 1
        assert payload["summary"]["total_tokens"] == 300

    # ---------------------------------------------------------------------
    # T02.2 — Model filter tests (--model)
    # ---------------------------------------------------------------------

    def test_model_exact_match_shows_only_matching_runs(self, tmp_path: Path) -> None:
        """--model with exact match should show only runs using that model."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(
            main, ["token-report", "-p", str(tmp_path), "--model", "gpt-4o"]
        )

        assert result.exit_code == 0, result.output
        assert "ship token ledger" in result.output
        assert "old goal" not in result.output

    def test_model_provider_prefix_match(self, tmp_path: Path) -> None:
        """--model with provider prefix should match records stored with the same prefix."""
        ledger = TokenLedger(
            records=[
                LedgerRunRecord(
                    timestamp="2026-05-01T00:00:00+00:00",
                    goal_summary="prefixed run",
                    model_breakdown=[
                        ModelTokenRecord(
                            model="openai/gpt-4o",
                            input_tokens=100,
                            output_tokens=50,
                        )
                    ],
                ),
                LedgerRunRecord(
                    timestamp="2026-05-02T00:00:00+00:00",
                    goal_summary="other model",
                    model_breakdown=[
                        ModelTokenRecord(
                            model="claude-sonnet-4-5",
                            input_tokens=200,
                            output_tokens=100,
                        )
                    ],
                ),
            ]
        )
        save_ledger(tmp_path, ledger)

        result = CliRunner().invoke(
            main,
            [
                "token-report",
                "-p",
                str(tmp_path),
                "--model",
                "openai/gpt-4o",
                "--json",
            ],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload["runs"]) == 1
        assert payload["runs"][0]["goal_summary"] == "prefixed run"

    def test_model_no_match_shows_friendly_message(self, tmp_path: Path) -> None:
        """--model with no matching runs should show the empty ledger message."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(
            main, ["token-report", "-p", str(tmp_path), "--model", "claude-sonnet-4-5"]
        )

        assert result.exit_code == 0, result.output
        assert "No token ledger data found." in result.output

    def test_model_json_output_filters_correctly(self, tmp_path: Path) -> None:
        """--json output with --model should produce correct filtered JSON."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(
            main, ["token-report", "-p", str(tmp_path), "--model", "gpt-4o", "--json"]
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload["runs"]) == 1
        assert payload["runs"][0]["goal_summary"] == "ship token ledger reporting"
        assert payload["summary"]["total_runs"] == 1
        assert payload["summary"]["total_tokens"] == 1_500
        assert payload["model_breakdown"][0]["model"] == "gpt-4o"


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


class TestBudgetCommand:
    """Tests for the `architect budget` command."""

    def test_empty_ledger_shows_no_data_message(self, tmp_path: Path) -> None:
        """Empty or missing ledger should show a friendly message."""
        result = CliRunner().invoke(main, ["budget", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "Token Budget Overview" in result.output
        assert "No token ledger data found." in result.output

    def test_budget_config_shows_limits(self, tmp_path: Path) -> None:
        """Budget configuration section should display per-hour and per-run limits."""
        result = CliRunner().invoke(main, ["budget", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "Budget Configuration" in result.output
        assert "unlimited" in result.output

    def test_populated_ledger_shows_summary(self, tmp_path: Path) -> None:
        """Populated ledger should show summary totals and averages."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["budget", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "Ledger Summary" in result.output
        assert "Averages" in result.output
        assert "Per-Model Breakdown" in result.output
        assert "1.8K" in result.output  # total tokens
        assert "gpt-4o" in result.output

    def test_json_output_has_correct_shape(self, tmp_path: Path) -> None:
        """JSON output should contain budget, ledger, averages, and model_breakdown."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["budget", "-p", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)

        # Budget config
        assert "budget" in payload
        assert payload["budget"]["per_hour"] == 0
        assert payload["budget"]["per_run"] == 0

        # Ledger totals
        assert "ledger" in payload
        assert payload["ledger"]["total_tokens"] == 1_800
        assert payload["ledger"]["run_count"] == 2
        assert payload["ledger"]["task_count"] == 3

        # Averages
        assert "averages" in payload
        assert payload["averages"]["tokens_per_task"] > 0
        assert payload["averages"]["tokens_per_run"] > 0
        assert payload["averages"]["cost_per_run"] >= 0

        # Model breakdown
        assert "model_breakdown" in payload
        assert len(payload["model_breakdown"]) == 2
        # Sorted by cost descending — gpt-4o should be first
        assert payload["model_breakdown"][0]["model"] == "gpt-4o"

    def test_json_model_breakdown_percentages(self, tmp_path: Path) -> None:
        """Model breakdown percentages should sum to approximately 100%."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["budget", "-p", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)

        total_pct = sum(m["pct"] for m in payload["model_breakdown"])
        assert 95.0 <= total_pct <= 105.0, f"Percentages sum to {total_pct}%"

    def test_json_empty_ledger_has_zeroed_values(self, tmp_path: Path) -> None:
        """Empty ledger JSON should have zeroed values and empty model_breakdown."""
        result = CliRunner().invoke(main, ["budget", "-p", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)

        assert payload["ledger"]["total_tokens"] == 0
        assert payload["ledger"]["run_count"] == 0
        assert payload["ledger"]["task_count"] == 0
        assert payload["averages"]["tokens_per_task"] == 0
        assert payload["averages"]["tokens_per_run"] == 0
        assert payload["averages"]["cost_per_run"] == 0.0
        assert payload["model_breakdown"] == []

    def test_json_output_has_cost_usd_key(self, tmp_path: Path) -> None:
        """Model breakdown entries must use cost_usd key name."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["budget", "-p", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)

        for entry in payload["model_breakdown"]:
            assert "cost_usd" in entry
            assert "tokens" in entry
            assert "pct" in entry
            assert "model" in entry


class TestHistoryCommand:
    """Tests for the `architect history` CLI command."""

    # ---------------------------------------------------------------------
    # T02.1 — Core history command tests
    # ---------------------------------------------------------------------

    def test_empty_ledger_shows_friendly_message(self, tmp_path: Path) -> None:
        """Missing ledger should show a friendly 'no run history' message."""
        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "No run history found" in result.output

    def test_missing_ledger_file_graceful(self, tmp_path: Path) -> None:
        """Non-existent .architect/ directory should not crash."""
        # .architect/ does not exist at all
        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "No run history found" in result.output

    def test_populated_ledger_shows_table(self, tmp_path: Path) -> None:
        """Table output should include runs with goal, tokens, cost, duration, outcome."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "Run History" in result.output
        # Goal may wrap across lines in Rich table — check parts separately
        assert "ship" in result.output
        assert "token" in result.output
        assert "ledger" in result.output
        assert "reporting" in result.output
        assert "1.5K" in result.output  # token formatting
        assert "2 run(s)" in result.output

    def test_populated_ledger_shows_outcome_icons(self, tmp_path: Path) -> None:
        """Success and failure outcomes should render with icons."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "success" in result.output
        assert "failure" in result.output

    def test_populated_ledger_shows_date_column(self, tmp_path: Path) -> None:
        """Date column should show the first 10 characters of the timestamp."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "2026-05-01" in result.output
        assert "2026-05-14" in result.output

    def test_populated_ledger_shows_task_count(self, tmp_path: Path) -> None:
        """Task count column should show correct values."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "1" in result.output  # task_count for first record
        assert "2" in result.output  # task_count for second record

    # ---------------------------------------------------------------------
    # T02.2 — JSON output tests
    # ---------------------------------------------------------------------

    def test_json_output_is_valid_array(self, tmp_path: Path) -> None:
        """--json should produce a valid JSON array."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert isinstance(payload, list)
        assert len(payload) == 2

    def test_json_output_has_correct_fields(self, tmp_path: Path) -> None:
        """Each JSON record should have all required fields."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)

        required_keys = {
            "run_id",
            "timestamp",
            "goal_summary",
            "total_tokens",
            "total_cost_estimate",
            "task_count",
            "outcome",
            "duration_seconds",
        }
        for record in payload:
            assert set(record.keys()) == required_keys, (
                f"Missing keys: {required_keys - set(record.keys())}"
            )

    def test_json_output_field_values(self, tmp_path: Path) -> None:
        """JSON output should contain correct field values from the ledger."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)

        # First record
        assert payload[0]["goal_summary"] == "old goal"
        assert payload[0]["total_tokens"] == 300
        assert payload[0]["task_count"] == 1
        assert payload[0]["outcome"] == "success"

        # Second record
        assert payload[1]["goal_summary"] == "ship token ledger reporting"
        assert payload[1]["total_tokens"] == 1_500
        assert payload[1]["task_count"] == 2
        assert payload[1]["outcome"] == "failure"

    def test_json_empty_ledger_produces_empty_array(self, tmp_path: Path) -> None:
        """--json with no ledger data should produce an empty array."""
        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload == []

    # ---------------------------------------------------------------------
    # T02.3 — Date filtering tests
    # ---------------------------------------------------------------------

    def test_since_filters_old_records(self, tmp_path: Path) -> None:
        """--since should exclude records before the requested date."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(
            main, ["history", "-p", str(tmp_path), "--since", "2026-05-10", "--json"]
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload) == 1
        assert payload[0]["goal_summary"] == "ship token ledger reporting"

    def test_until_excludes_future_records(self, tmp_path: Path) -> None:
        """--until should exclude records on or after the specified date."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(
            main, ["history", "-p", str(tmp_path), "--until", "2026-05-10", "--json"]
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload) == 1
        assert payload[0]["goal_summary"] == "old goal"

    def test_since_and_until_produce_date_range(self, tmp_path: Path) -> None:
        """--since + --until together should produce a date range [start, end)."""
        ledger = TokenLedger(
            records=[
                LedgerRunRecord(
                    timestamp="2026-05-01T00:00:00+00:00",
                    goal_summary="before range",
                ),
                LedgerRunRecord(
                    timestamp="2026-05-05T00:00:00+00:00",
                    goal_summary="in range",
                ),
                LedgerRunRecord(
                    timestamp="2026-05-14T00:00:00+00:00",
                    goal_summary="after range",
                ),
            ]
        )
        save_ledger(tmp_path, ledger)

        result = CliRunner().invoke(
            main,
            [
                "history",
                "-p",
                str(tmp_path),
                "--since",
                "2026-05-02",
                "--until",
                "2026-05-06",
                "--json",
            ],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload) == 1
        assert payload[0]["goal_summary"] == "in range"

    # ---------------------------------------------------------------------
    # T02.4 — Outcome filtering tests
    # ---------------------------------------------------------------------

    def test_outcome_success_filters_correctly(self, tmp_path: Path) -> None:
        """--outcome success should show only successful runs."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(
            main, ["history", "-p", str(tmp_path), "--outcome", "success", "--json"]
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload) == 1
        assert payload[0]["outcome"] == "success"
        assert payload[0]["goal_summary"] == "old goal"

    def test_outcome_failure_filters_correctly(self, tmp_path: Path) -> None:
        """--outcome failure should show only failed runs."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(
            main, ["history", "-p", str(tmp_path), "--outcome", "failure", "--json"]
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload) == 1
        assert payload[0]["outcome"] == "failure"
        assert payload[0]["goal_summary"] == "ship token ledger reporting"

    def test_outcase_insensitive(self, tmp_path: Path) -> None:
        """--outcome should be case-insensitive."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(
            main, ["history", "-p", str(tmp_path), "--outcome", "SUCCESS", "--json"]
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload) == 1

    def test_outcome_no_matches_returns_empty(self, tmp_path: Path) -> None:
        """--outcome with no matching runs should return empty results."""
        ledger = TokenLedger(
            records=[
                LedgerRunRecord(
                    timestamp="2026-05-01T00:00:00+00:00",
                    goal_summary="a success run",
                    outcome="success",
                ),
            ]
        )
        save_ledger(tmp_path, ledger)

        result = CliRunner().invoke(
            main, ["history", "-p", str(tmp_path), "--outcome", "failure", "--json"]
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload == []

    # ---------------------------------------------------------------------
    # T02.5 — Limit tests
    # ---------------------------------------------------------------------

    def test_limit_returns_most_recent_n(self, tmp_path: Path) -> None:
        """--limit N should return only the most recent N runs."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(
            main, ["history", "-p", str(tmp_path), "--limit", "1", "--json"]
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload) == 1
        # Most recent is the May 14 record
        assert payload[0]["goal_summary"] == "ship token ledger reporting"

    def test_limit_larger_than_total_returns_all(self, tmp_path: Path) -> None:
        """--limit larger than total records should return all records."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(
            main, ["history", "-p", str(tmp_path), "--limit", "100", "--json"]
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload) == 2

    def test_limit_zero_returns_all(self, tmp_path: Path) -> None:
        """--limit 0 should be treated as no limit (returns all)."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(
            main, ["history", "-p", str(tmp_path), "--limit", "0", "--json"]
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload) == 2

    # ---------------------------------------------------------------------
    # T02.6 — Project option tests
    # ---------------------------------------------------------------------

    def test_project_short_flag_works(self, tmp_path: Path) -> None:
        """-p short flag should work identically to --project."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload) == 2

    def test_project_flag_with_different_project(self, tmp_path: Path) -> None:
        """--project should read ledger from the specified directory."""
        project_a = tmp_path / "project_a"
        project_a.mkdir()
        project_b = tmp_path / "project_b"
        project_b.mkdir()

        # Only project_a has a ledger
        _write_sample_ledger(project_a)

        result_a = CliRunner().invoke(main, ["history", "-p", str(project_a), "--json"])
        result_b = CliRunner().invoke(main, ["history", "-p", str(project_b), "--json"])

        assert result_a.exit_code == 0
        assert result_b.exit_code == 0
        assert len(json.loads(result_a.output)) == 2
        assert json.loads(result_b.output) == []

    # ---------------------------------------------------------------------
    # T02.7 — Corrupted ledger tests
    # ---------------------------------------------------------------------

    def test_corrupted_ledger_file_graceful(self, tmp_path: Path) -> None:
        """Corrupted JSON in ledger file should not crash — treat as empty."""
        ledger_dir = tmp_path / ".architect"
        ledger_dir.mkdir()
        (ledger_dir / "token_ledger.json").write_text("NOT VALID JSON{{{", encoding="utf-8")

        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "No run history found" in result.output

    def test_corrupted_ledger_json_output_empty(self, tmp_path: Path) -> None:
        """Corrupted ledger with --json should produce empty array."""
        ledger_dir = tmp_path / ".architect"
        ledger_dir.mkdir()
        (ledger_dir / "token_ledger.json").write_text("NOT VALID JSON{{{", encoding="utf-8")

        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload == []

    # ---------------------------------------------------------------------
    # T02.8 — Combined filters
    # ---------------------------------------------------------------------

    def test_combined_since_and_outcome_filters(self, tmp_path: Path) -> None:
        """--since and --outcome together should both apply."""
        ledger = TokenLedger(
            records=[
                LedgerRunRecord(
                    timestamp="2026-05-01T00:00:00+00:00",
                    goal_summary="early success",
                    outcome="success",
                ),
                LedgerRunRecord(
                    timestamp="2026-05-10T00:00:00+00:00",
                    goal_summary="recent success",
                    outcome="success",
                ),
                LedgerRunRecord(
                    timestamp="2026-05-14T00:00:00+00:00",
                    goal_summary="recent failure",
                    outcome="failure",
                ),
            ]
        )
        save_ledger(tmp_path, ledger)

        result = CliRunner().invoke(
            main,
            [
                "history",
                "-p",
                str(tmp_path),
                "--since",
                "2026-05-05",
                "--outcome",
                "success",
                "--json",
            ],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload) == 1
        assert payload[0]["goal_summary"] == "recent success"

    def test_combined_limit_and_outcome_filters(self, tmp_path: Path) -> None:
        """--limit and --outcome together should apply both filters."""
        ledger = TokenLedger(
            records=[
                LedgerRunRecord(
                    timestamp="2026-05-01T00:00:00+00:00",
                    goal_summary="early success",
                    outcome="success",
                ),
                LedgerRunRecord(
                    timestamp="2026-05-05T00:00:00+00:00",
                    goal_summary="mid success",
                    outcome="success",
                ),
                LedgerRunRecord(
                    timestamp="2026-05-10T00:00:00+00:00",
                    goal_summary="recent success",
                    outcome="success",
                ),
            ]
        )
        save_ledger(tmp_path, ledger)

        result = CliRunner().invoke(
            main,
            [
                "history",
                "-p",
                str(tmp_path),
                "--outcome",
                "success",
                "--limit",
                "2",
                "--json",
            ],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload) == 2
        assert payload[0]["goal_summary"] == "mid success"
        assert payload[1]["goal_summary"] == "recent success"

    def test_all_filters_combined(self, tmp_path: Path) -> None:
        """--since, --until, --outcome, --limit all together should work."""
        ledger = TokenLedger(
            records=[
                LedgerRunRecord(
                    timestamp="2026-05-01T00:00:00+00:00",
                    goal_summary="early success",
                    outcome="success",
                ),
                LedgerRunRecord(
                    timestamp="2026-05-05T00:00:00+00:00",
                    goal_summary="mid failure",
                    outcome="failure",
                ),
                LedgerRunRecord(
                    timestamp="2026-05-10T00:00:00+00:00",
                    goal_summary="recent success",
                    outcome="success",
                ),
                LedgerRunRecord(
                    timestamp="2026-05-14T00:00:00+00:00",
                    goal_summary="latest success",
                    outcome="success",
                ),
            ]
        )
        save_ledger(tmp_path, ledger)

        result = CliRunner().invoke(
            main,
            [
                "history",
                "-p",
                str(tmp_path),
                "--since",
                "2026-05-03",
                "--until",
                "2026-05-13",
                "--outcome",
                "success",
                "--limit",
                "1",
                "--json",
            ],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        # --since 2026-05-03 excludes May 1
        # --until 2026-05-13 excludes May 14
        # Remaining: mid failure (excluded by --outcome success), recent success
        # --limit 1: most recent of remaining = recent success
        assert len(payload) == 1
        assert payload[0]["goal_summary"] == "recent success"

    # ---------------------------------------------------------------------
    # T02.9 — Goal truncation in table output
    # ---------------------------------------------------------------------

    def test_goal_truncation_in_table(self, tmp_path: Path) -> None:
        """Goals longer than 60 characters should be truncated with ellipsis in table."""
        long_goal = "a" * 100
        ledger = TokenLedger(
            records=[
                LedgerRunRecord(
                    timestamp="2026-05-01T00:00:00+00:00",
                    goal_summary=long_goal,
                ),
            ]
        )
        save_ledger(tmp_path, ledger)

        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        # The full 100-char goal should NOT appear in table output
        assert long_goal not in result.output
        # The ellipsis character should appear (truncation indicator)
        assert "\u2026" in result.output  # …

    # ---------------------------------------------------------------------
    # T02.1 — history --tasks flag tests
    # ---------------------------------------------------------------------

    def test_tasks_flag_shows_task_detail_table(self, tmp_path: Path) -> None:
        """--tasks should show per-task breakdown instead of run-level summary."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path), "--tasks"])

        assert result.exit_code == 0, result.output
        assert "Task Detail" in result.output
        assert "T01" in result.output
        assert "T02" in result.output
        assert "gpt-4o-mini" in result.output
        assert "gpt-4o" in result.output
        assert "2 run(s)" in result.output

    def test_tasks_flag_shows_task_status(self, tmp_path: Path) -> None:
        """Task status should render with appropriate icons."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path), "--tasks"])

        assert result.exit_code == 0, result.output
        assert "done" in result.output
        assert "failed" in result.output

    def test_tasks_flag_json_output(self, tmp_path: Path) -> None:
        """--tasks --json should produce JSON with task_breakdown arrays."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path), "--tasks", "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert isinstance(payload, list)
        assert len(payload) == 2

        # First run has 1 task
        assert len(payload[0]["task_breakdown"]) == 1
        assert payload[0]["task_breakdown"][0]["task_id"] == "T01"
        assert payload[0]["task_breakdown"][0]["title"] == "Setup project"

        # Second run has 2 tasks
        assert len(payload[1]["task_breakdown"]) == 2
        assert payload[1]["task_breakdown"][0]["task_id"] == "T01"
        assert payload[1]["task_breakdown"][1]["task_id"] == "T02"

    def test_tasks_flag_json_has_all_task_fields(self, tmp_path: Path) -> None:
        """Each task in JSON should have all LedgerTaskRecord fields."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path), "--tasks", "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)

        required_keys = {
            "task_id",
            "title",
            "status",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "model",
            "cost_estimate",
            "duration_seconds",
        }
        for run in payload:
            for task in run["task_breakdown"]:
                assert set(task.keys()) == required_keys

    def test_tasks_flag_empty_ledger(self, tmp_path: Path) -> None:
        """--tasks with no ledger data should show friendly message."""
        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path), "--tasks"])

        assert result.exit_code == 0, result.output
        assert "No run history found" in result.output

    def test_tasks_flag_empty_task_breakdown(self, tmp_path: Path) -> None:
        """Records with empty task_breakdown should display gracefully."""
        ledger = TokenLedger(
            records=[
                LedgerRunRecord(
                    timestamp="2026-05-01T00:00:00+00:00",
                    goal_summary="old run without task data",
                    task_count=1,
                    outcome="success",
                    # No task_breakdown — old ledger record
                ),
            ]
        )
        save_ledger(tmp_path, ledger)

        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path), "--tasks"])

        assert result.exit_code == 0, result.output
        assert "1 run(s)" in result.output
        assert "2026-05" in result.output

    def test_tasks_and_tui_mutually_exclusive(self, tmp_path: Path) -> None:
        """--tasks and --tui should be mutually exclusive."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["history", "-p", str(tmp_path), "--tasks", "--tui"])

        assert result.exit_code == 1
        assert "mutually exclusive" in result.output.lower()

    # ---------------------------------------------------------------------
    # T02.2 — token-report --tasks flag tests
    # ---------------------------------------------------------------------

    def test_token_report_tasks_flag_shows_task_table(self, tmp_path: Path) -> None:
        """token-report --tasks should show per-task cost breakdown."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["token-report", "-p", str(tmp_path), "--tasks"])

        assert result.exit_code == 0, result.output
        assert "Per-Task Cost Breakdown" in result.output
        assert "T01" in result.output
        assert "T02" in result.output
        assert "gpt-4o-mini" in result.output
        assert "gpt-4o" in result.output

    def test_token_report_tasks_flag_shows_summary(self, tmp_path: Path) -> None:
        """token-report --tasks should show task summary totals."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(main, ["token-report", "-p", str(tmp_path), "--tasks"])

        assert result.exit_code == 0, result.output
        assert "Summary" in result.output
        assert "3 tasks" in result.output

    def test_token_report_tasks_flag_empty_ledger(self, tmp_path: Path) -> None:
        """token-report --tasks with no ledger should show friendly message."""
        result = CliRunner().invoke(main, ["token-report", "-p", str(tmp_path), "--tasks"])

        assert result.exit_code == 0, result.output
        assert "No token ledger data found" in result.output

    def test_token_report_tasks_flag_empty_task_breakdown(self, tmp_path: Path) -> None:
        """token-report --tasks with empty task_breakdown should be graceful."""
        ledger = TokenLedger(
            records=[
                LedgerRunRecord(
                    timestamp="2026-05-01T00:00:00+00:00",
                    goal_summary="old run",
                    task_count=1,
                    outcome="success",
                ),
            ]
        )
        save_ledger(tmp_path, ledger)

        result = CliRunner().invoke(main, ["token-report", "-p", str(tmp_path), "--tasks"])

        assert result.exit_code == 0, result.output
        assert "No per-task cost data found" in result.output

    def test_token_report_tasks_json_includes_breakdown(self, tmp_path: Path) -> None:
        """token-report --tasks --json includes task_breakdown in run records."""
        _write_sample_ledger(tmp_path)

        result = CliRunner().invoke(
            main, ["token-report", "-p", str(tmp_path), "--tasks", "--json"]
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        # JSON already includes task_breakdown via model_dump()
        assert "runs" in payload
        assert len(payload["runs"]) == 2
        # Each run record should have task_breakdown
        assert "task_breakdown" in payload["runs"][0]
        assert len(payload["runs"][0]["task_breakdown"]) == 1
        assert len(payload["runs"][1]["task_breakdown"]) == 2
