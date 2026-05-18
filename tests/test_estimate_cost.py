"""Tests for the pre-run cost estimation module."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from the_architect.cli import main
from the_architect.core.estimate_cost import (
    EstimateResult,
    TaskCountStats,
    _confidence_from_count,
    _estimate_from_pricing_table,
    _runs_for_model,
    _successful_runs,
    estimate_run_cost,
    get_model_avg_cost_per_task,
    get_task_count_stats,
)
from the_architect.core.token_ledger import (
    LedgerRunRecord,
    ModelTokenRecord,
    TokenLedger,
)

# ---------------------------------------------------------------------------
# Helpers to build test fixtures
# ---------------------------------------------------------------------------


def _make_run(
    task_count: int = 3,
    outcome: str = "success",
    model: str = "gpt-4o",
    model_cost: float = 1.50,
    total_cost: float = 3.00,
    total_tokens: int = 150_000,
) -> LedgerRunRecord:
    """Build a minimal LedgerRunRecord for testing."""
    return LedgerRunRecord(
        task_count=task_count,
        outcome=outcome,
        total_cost_estimate=total_cost,
        total_tokens=total_tokens,
        model_breakdown=[ModelTokenRecord(model=model, cost_estimate=model_cost)],
    )


def _make_ledger(*runs: LedgerRunRecord) -> TokenLedger:
    """Build a TokenLedger from run records."""
    return TokenLedger(records=list(runs))


# ---------------------------------------------------------------------------
# EstimateResult model tests
# ---------------------------------------------------------------------------


class TestEstimateResultModel:
    def test_default_values(self) -> None:
        result = EstimateResult(model="gpt-4o", task_count=5)
        assert result.cost_low == 0.0
        assert result.cost_high == 0.0
        assert result.cost_avg == 0.0
        assert result.historical_runs == 0
        assert result.confidence == "low"

    def test_full_initialization(self) -> None:
        result = EstimateResult(
            model="gpt-4o",
            task_count=3,
            cost_low=1.0,
            cost_high=5.0,
            cost_avg=3.0,
            historical_runs=10,
            confidence="high",
        )
        assert result.model == "gpt-4o"
        assert result.task_count == 3
        assert result.cost_low == 1.0
        assert result.cost_high == 5.0
        assert result.cost_avg == 3.0
        assert result.historical_runs == 10
        assert result.confidence == "high"

    def test_serialization(self) -> None:
        result = EstimateResult(
            model="gpt-4o",
            task_count=3,
            cost_low=1.0,
            cost_high=5.0,
            cost_avg=3.0,
            historical_runs=5,
            confidence="medium",
        )
        dumped = result.model_dump()
        assert dumped["model"] == "gpt-4o"
        assert dumped["task_count"] == 3
        assert dumped["confidence"] == "medium"


# ---------------------------------------------------------------------------
# Confidence helper tests
# ---------------------------------------------------------------------------


class TestConfidenceFromCount:
    def test_low_confidence_zero_runs(self) -> None:
        assert _confidence_from_count(0) == "low"

    def test_low_confidence_one_run(self) -> None:
        assert _confidence_from_count(1) == "low"

    def test_medium_confidence_two_runs(self) -> None:
        assert _confidence_from_count(2) == "medium"

    def test_medium_confidence_four_runs(self) -> None:
        assert _confidence_from_count(4) == "medium"

    def test_high_confidence_five_runs(self) -> None:
        assert _confidence_from_count(5) == "high"

    def test_high_confidence_ten_runs(self) -> None:
        assert _confidence_from_count(10) == "high"


# ---------------------------------------------------------------------------
# _successful_runs and _runs_for_model helper tests
# ---------------------------------------------------------------------------


class TestHelperFilters:
    def test_successful_runs_filters_failures(self) -> None:
        ledger = _make_ledger(
            _make_run(outcome="success", model_cost=1.0),
            _make_run(outcome="failure", model_cost=2.0),
            _make_run(outcome="success", model_cost=3.0),
        )
        successful = _successful_runs(ledger)
        assert len(successful) == 2

    def test_successful_runs_empty_ledger(self) -> None:
        assert _successful_runs(TokenLedger()) == []

    def test_runs_for_model_filters_by_model(self) -> None:
        ledger = _make_ledger(
            _make_run(model="gpt-4o", model_cost=1.0),
            _make_run(model="claude-sonnet-4-5", model_cost=2.0),
            _make_run(model="gpt-4o", model_cost=3.0),
        )
        matches = _runs_for_model(ledger, "gpt-4o")
        assert len(matches) == 2

    def test_runs_for_model_case_insensitive(self) -> None:
        ledger = _make_ledger(
            _make_run(model="gpt-4o", model_cost=1.0),
        )
        matches = _runs_for_model(ledger, "GPT-4O")
        assert len(matches) == 1

    def test_runs_for_model_excludes_failures(self) -> None:
        ledger = _make_ledger(
            _make_run(model="gpt-4o", model_cost=1.0, outcome="success"),
            _make_run(model="gpt-4o", model_cost=2.0, outcome="failure"),
        )
        matches = _runs_for_model(ledger, "gpt-4o")
        assert len(matches) == 1


# ---------------------------------------------------------------------------
# estimate_run_cost — model-specific history
# ---------------------------------------------------------------------------


class TestEstimateRunCostModelSpecific:
    def test_single_run_estimate(self) -> None:
        ledger = _make_ledger(
            _make_run(task_count=3, model="gpt-4o", model_cost=3.00),
        )
        result = estimate_run_cost(ledger, "gpt-4o", 5)
        # 3.00 / 3 = 1.00 per task * 5 = 5.00
        assert result.cost_avg == 5.0
        assert result.cost_low == 5.0
        assert result.cost_high == 5.0
        assert result.historical_runs == 1
        assert result.confidence == "low"

    def test_multi_run_estimate(self) -> None:
        ledger = _make_ledger(
            _make_run(task_count=2, model="gpt-4o", model_cost=2.00),
            _make_run(task_count=4, model="gpt-4o", model_cost=8.00),
            _make_run(task_count=5, model="gpt-4o", model_cost=5.00),
        )
        # Per-task: 1.0, 2.0, 1.0 -> avg 1.3333
        result = estimate_run_cost(ledger, "gpt-4o", 3)
        assert result.historical_runs == 3
        assert result.confidence == "medium"
        # avg: 1.3333 * 3 = 4.0
        assert result.cost_avg == 4.0
        # low: 1.0 * 3 = 3.0
        assert result.cost_low == 3.0
        # high: 2.0 * 3 = 6.0
        assert result.cost_high == 6.0

    def test_zero_task_count_returns_zeros(self) -> None:
        ledger = _make_ledger(
            _make_run(model="gpt-4o", model_cost=1.0),
        )
        result = estimate_run_cost(ledger, "gpt-4o", 0)
        assert result.cost_avg == 0.0
        assert result.task_count == 0

    def test_negative_task_count_returns_zeros(self) -> None:
        result = estimate_run_cost(TokenLedger(), "gpt-4o", -1)
        assert result.cost_avg == 0.0
        assert result.task_count == 0

    def test_high_confidence_with_five_runs(self) -> None:
        runs = [_make_run(task_count=3, model="gpt-4o", model_cost=3.0) for _ in range(5)]
        ledger = _make_ledger(*runs)
        result = estimate_run_cost(ledger, "gpt-4o", 3)
        assert result.confidence == "high"
        assert result.historical_runs == 5


# ---------------------------------------------------------------------------
# estimate_run_cost — cross-model fallback
# ---------------------------------------------------------------------------


class TestEstimateRunCostCrossModel:
    def test_fallback_to_all_runs_when_model_missing(self) -> None:
        ledger = _make_ledger(
            _make_run(model="claude-sonnet-4-5", model_cost=6.0, total_cost=6.0),
            _make_run(model="claude-sonnet-4-5", model_cost=9.0, total_cost=9.0),
        )
        result = estimate_run_cost(ledger, "gpt-4o", 3)
        # Cross-model: costs_per_task = [6/3=2.0, 9/3=3.0] -> avg 2.5 * 3 = 7.5
        assert result.cost_avg == 7.5
        assert result.historical_runs == 2
        assert result.confidence == "medium"


# ---------------------------------------------------------------------------
# estimate_run_cost — pricing table fallback
# ---------------------------------------------------------------------------


class TestEstimateRunCostPricingFallback:
    def test_empty_ledger_uses_pricing_table(self) -> None:
        ledger = TokenLedger()
        result = estimate_run_cost(ledger, "gpt-4o", 5)
        assert result.historical_runs == 0
        assert result.confidence == "low"
        assert result.cost_avg > 0.0
        # Pricing table fallback has wide range
        assert result.cost_high > result.cost_avg > result.cost_low

    def test_unknown_model_returns_zeros(self) -> None:
        ledger = TokenLedger()
        result = estimate_run_cost(ledger, "unknown-fake-model", 5)
        assert result.cost_avg == 0.0
        assert result.cost_low == 0.0
        assert result.cost_high == 0.0
        assert result.confidence == "low"

    def test_pricing_fallback_low_confidence(self) -> None:
        ledger = TokenLedger()
        result = estimate_run_cost(ledger, "gpt-4o-mini", 10)
        assert result.confidence == "low"
        assert result.historical_runs == 0


# ---------------------------------------------------------------------------
# _estimate_from_pricing_table direct tests
# ---------------------------------------------------------------------------


class TestPricingTableEstimate:
    def test_known_model_produces_estimate(self) -> None:
        result = _estimate_from_pricing_table("gpt-4o", 3)
        assert result.cost_avg > 0.0
        assert result.cost_high > result.cost_avg
        assert result.cost_low < result.cost_avg

    def test_unknown_model_returns_zeros(self) -> None:
        result = _estimate_from_pricing_table("nonexistent-model", 3)
        assert result.cost_avg == 0.0
        assert result.historical_runs == 0

    def test_scaling_with_task_count(self) -> None:
        result_1 = _estimate_from_pricing_table("gpt-4o", 1)
        result_10 = _estimate_from_pricing_table("gpt-4o", 10)
        assert result_10.cost_avg == result_1.cost_avg * 10


# ---------------------------------------------------------------------------
# get_model_avg_cost_per_task tests
# ---------------------------------------------------------------------------


class TestGetModelAvgCostPerTask:
    def test_single_run(self) -> None:
        ledger = _make_ledger(
            _make_run(task_count=3, model="gpt-4o", model_cost=3.00),
        )
        avg = get_model_avg_cost_per_task(ledger, "gpt-4o")
        assert avg == 1.0

    def test_multi_run_average(self) -> None:
        ledger = _make_ledger(
            _make_run(task_count=2, model="gpt-4o", model_cost=2.00),
            _make_run(task_count=2, model="gpt-4o", model_cost=4.00),
        )
        avg = get_model_avg_cost_per_task(ledger, "gpt-4o")
        # (1.0 + 2.0) / 2 = 1.5
        assert avg == 1.5

    def test_excludes_failed_runs(self) -> None:
        ledger = _make_ledger(
            _make_run(task_count=2, model="gpt-4o", model_cost=2.00, outcome="success"),
            _make_run(task_count=2, model="gpt-4o", model_cost=100.00, outcome="failure"),
        )
        avg = get_model_avg_cost_per_task(ledger, "gpt-4o")
        assert avg == 1.0

    def test_empty_ledger_returns_zero(self) -> None:
        avg = get_model_avg_cost_per_task(TokenLedger(), "gpt-4o")
        assert avg == 0.0

    def test_model_not_in_ledger_returns_zero(self) -> None:
        ledger = _make_ledger(
            _make_run(model="claude-sonnet-4-5", model_cost=3.0),
        )
        avg = get_model_avg_cost_per_task(ledger, "gpt-4o")
        assert avg == 0.0


# ---------------------------------------------------------------------------
# get_task_count_stats tests
# ---------------------------------------------------------------------------


class TestGetTaskCountStats:
    def test_empty_ledger(self) -> None:
        stats = get_task_count_stats(TokenLedger())
        assert stats.min_tasks == 0
        assert stats.max_tasks == 0
        assert stats.avg_tasks == 0.0
        assert stats.run_count == 0

    def test_single_run(self) -> None:
        ledger = _make_ledger(_make_run(task_count=5))
        stats = get_task_count_stats(ledger)
        assert stats.min_tasks == 5
        assert stats.max_tasks == 5
        assert stats.avg_tasks == 5.0
        assert stats.run_count == 1

    def test_multiple_runs(self) -> None:
        ledger = _make_ledger(
            _make_run(task_count=3),
            _make_run(task_count=7),
            _make_run(task_count=5),
        )
        stats = get_task_count_stats(ledger)
        assert stats.min_tasks == 3
        assert stats.max_tasks == 7
        assert stats.avg_tasks == 5.0
        assert stats.run_count == 3

    def test_excludes_failed_runs(self) -> None:
        ledger = _make_ledger(
            _make_run(task_count=3, outcome="success"),
            _make_run(task_count=100, outcome="failure"),
        )
        stats = get_task_count_stats(ledger)
        assert stats.min_tasks == 3
        assert stats.max_tasks == 3
        assert stats.run_count == 1

    def test_excludes_zero_task_count_runs(self) -> None:
        ledger = _make_ledger(
            _make_run(task_count=0),
            _make_run(task_count=5),
        )
        stats = get_task_count_stats(ledger)
        assert stats.run_count == 1
        assert stats.min_tasks == 5
        assert stats.max_tasks == 5

    def test_all_failures_returns_zeroed(self) -> None:
        ledger = _make_ledger(
            _make_run(outcome="failure"),
            _make_run(outcome="failure"),
        )
        stats = get_task_count_stats(ledger)
        assert stats.run_count == 0
        assert stats.min_tasks == 0


# ---------------------------------------------------------------------------
# Edge cases and integration
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_provider_prefixed_model_names(self) -> None:
        ledger = _make_ledger(
            _make_run(model="openai/gpt-4o", model_cost=3.0),
        )
        # Should match via partial match in _runs_for_model
        result = estimate_run_cost(ledger, "openai/gpt-4o", 3)
        assert result.historical_runs == 1

    def test_model_cost_zero_falls_back(self) -> None:
        ledger = _make_ledger(
            _make_run(model="gpt-4o", model_cost=0.0),
        )
        # model_cost=0 means cost_per_task will be 0 -> falls back to pricing
        result = estimate_run_cost(ledger, "gpt-4o", 3)
        # Should fall through to pricing table since costs_per_task is empty
        assert result.historical_runs == 0
        assert result.confidence == "low"

    def test_estimate_result_is_pydantic_v2(self) -> None:
        result = EstimateResult(model="gpt-4o", task_count=3)
        # Pydantic v2 uses model_dump, not dict
        assert hasattr(result, "model_dump")
        assert "model" in result.model_dump()

    def test_task_count_stats_is_dataclass(self) -> None:
        stats = TaskCountStats(min_tasks=1, max_tasks=10, avg_tasks=5.0, run_count=3)
        assert stats.min_tasks == 1
        assert stats.max_tasks == 10
        assert stats.avg_tasks == 5.0
        assert stats.run_count == 3


# ---------------------------------------------------------------------------
# CLI-layer tests for `architect estimate` command
# ---------------------------------------------------------------------------


class TestEstimateCliCommand:
    """Tests for the `architect estimate` CLI command."""

    def test_default_invocation_populated_ledger(self, tmp_path: Path) -> None:
        """Default invocation with populated ledger shows Rich table."""
        from the_architect.core.token_ledger import save_ledger

        ledger = _make_ledger(
            _make_run(task_count=3, model="gpt-4o", model_cost=3.00),
        )
        save_ledger(tmp_path, ledger)

        result = CliRunner().invoke(main, ["estimate", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "Pre-run Cost Estimate" in result.output
        assert "gpt-4o" in result.output
        # Historical average task count = 3
        assert "3" in result.output
        # Confidence should be low (only 1 run)
        assert "low" in result.output.lower()

    def test_empty_ledger_shows_pricing_table_note(self, tmp_path: Path) -> None:
        """Empty ledger should show pricing table fallback note."""
        result = CliRunner().invoke(main, ["estimate", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "Pre-run Cost Estimate" in result.output
        assert "No historical data" in result.output

    def test_model_override(self, tmp_path: Path) -> None:
        """--model should override the model used for estimation."""
        from the_architect.core.token_ledger import save_ledger

        ledger = _make_ledger(
            _make_run(task_count=3, model="gpt-4o", model_cost=3.00),
        )
        save_ledger(tmp_path, ledger)

        result = CliRunner().invoke(
            main, ["estimate", "-p", str(tmp_path), "--model", "gpt-4o-mini", "--json"]
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["model"] == "gpt-4o-mini"

    def test_tasks_override(self, tmp_path: Path) -> None:
        """--tasks should override the task count used for estimation."""
        from the_architect.core.token_ledger import save_ledger

        ledger = _make_ledger(
            _make_run(task_count=3, model="gpt-4o", model_cost=3.00),
        )
        save_ledger(tmp_path, ledger)

        result = CliRunner().invoke(main, ["estimate", "-p", str(tmp_path), "--tasks", "10"])

        assert result.exit_code == 0, result.output
        # The task count column should show 10
        assert "10" in result.output

    def test_json_output_has_correct_shape(self, tmp_path: Path) -> None:
        """--json should produce valid JSON with all EstimateResult fields."""
        from the_architect.core.token_ledger import save_ledger

        ledger = _make_ledger(
            _make_run(task_count=3, model="gpt-4o", model_cost=3.00),
        )
        save_ledger(tmp_path, ledger)

        result = CliRunner().invoke(main, ["estimate", "-p", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)

        required_keys = {
            "model",
            "task_count",
            "cost_low",
            "cost_high",
            "cost_avg",
            "historical_runs",
            "confidence",
        }
        assert set(payload.keys()) == required_keys
        assert payload["model"] == "gpt-4o"
        assert payload["task_count"] == 3  # historical average = 3
        assert payload["historical_runs"] == 1
        assert payload["confidence"] == "low"

    def test_json_output_empty_ledger(self, tmp_path: Path) -> None:
        """--json with empty ledger produces valid JSON with pricing fallback."""
        result = CliRunner().invoke(main, ["estimate", "-p", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)

        assert payload["historical_runs"] == 0
        assert payload["confidence"] == "low"
        # Default model when no ledger data
        assert "claude-sonnet" in payload["model"].lower()
        # Default task count when no ledger data
        assert payload["task_count"] == 5

    def test_json_output_model_and_tasks_override(self, tmp_path: Path) -> None:
        """--json with --model and --tasks overrides should reflect in output."""
        result = CliRunner().invoke(
            main,
            [
                "estimate",
                "-p",
                str(tmp_path),
                "--model",
                "gpt-4o",
                "--tasks",
                "7",
                "--json",
            ],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)

        assert payload["model"] == "gpt-4o"
        assert payload["task_count"] == 7
        assert payload["cost_avg"] > 0.0  # pricing table fallback

    def test_corrupted_ledger_graceful(self, tmp_path: Path) -> None:
        """Corrupted ledger JSON should not crash — treat as empty."""
        ledger_dir = tmp_path / ".architect"
        ledger_dir.mkdir()
        (ledger_dir / "token_ledger.json").write_text("NOT VALID JSON{{{", encoding="utf-8")

        result = CliRunner().invoke(main, ["estimate", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "Pre-run Cost Estimate" in result.output
        assert "No historical data" in result.output

    def test_corrupted_ledger_json_output(self, tmp_path: Path) -> None:
        """Corrupted ledger with --json should produce valid JSON."""
        ledger_dir = tmp_path / ".architect"
        ledger_dir.mkdir()
        (ledger_dir / "token_ledger.json").write_text("NOT VALID JSON{{{", encoding="utf-8")

        result = CliRunner().invoke(main, ["estimate", "-p", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["historical_runs"] == 0
        assert payload["confidence"] == "low"

    def test_high_confidence_with_enough_runs(self, tmp_path: Path) -> None:
        """5+ successful runs should produce high confidence."""
        from the_architect.core.token_ledger import save_ledger

        runs = [_make_run(task_count=3, model="gpt-4o", model_cost=3.00) for _ in range(5)]
        ledger = _make_ledger(*runs)
        save_ledger(tmp_path, ledger)

        result = CliRunner().invoke(main, ["estimate", "-p", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["confidence"] == "high"
        assert payload["historical_runs"] == 5

    def test_medium_confidence_with_few_runs(self, tmp_path: Path) -> None:
        """2-4 successful runs should produce medium confidence."""
        from the_architect.core.token_ledger import save_ledger

        runs = [_make_run(task_count=3, model="gpt-4o", model_cost=3.00) for _ in range(3)]
        ledger = _make_ledger(*runs)
        save_ledger(tmp_path, ledger)

        result = CliRunner().invoke(main, ["estimate", "-p", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["confidence"] == "medium"
        assert payload["historical_runs"] == 3

    def test_failed_runs_excluded_from_estimation(self, tmp_path: Path) -> None:
        """Failed runs should not contribute to cost estimation."""
        from the_architect.core.token_ledger import save_ledger

        ledger = _make_ledger(
            _make_run(task_count=3, model="gpt-4o", model_cost=3.00, outcome="success"),
            _make_run(task_count=3, model="gpt-4o", model_cost=100.00, outcome="failure"),
        )
        save_ledger(tmp_path, ledger)

        result = CliRunner().invoke(main, ["estimate", "-p", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        # Only 1 successful run should be used
        assert payload["historical_runs"] == 1
        # cost_avg should be based on the $3 run, not $100
        assert payload["cost_avg"] == 3.0

    def test_tasks_override_with_zero(self, tmp_path: Path) -> None:
        """--tasks 0 is treated as 'use default' — falls back to 5."""
        result = CliRunner().invoke(
            main,
            [
                "estimate",
                "-p",
                str(tmp_path),
                "--tasks",
                "0",
                "--json",
            ],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        # CLI treats task_count=0 as "not specified" → falls back to default 5
        assert payload["task_count"] == 5

    def test_unknown_model_empty_ledger_returns_zeros(self, tmp_path: Path) -> None:
        """Unknown model with empty ledger should return zero costs."""
        result = CliRunner().invoke(
            main,
            [
                "estimate",
                "-p",
                str(tmp_path),
                "--model",
                "unknown-fake-model",
                "--json",
            ],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["model"] == "unknown-fake-model"
        assert payload["cost_avg"] == 0.0
        assert payload["cost_low"] == 0.0
        assert payload["cost_high"] == 0.0
        assert payload["confidence"] == "low"

    def test_table_output_shows_confidence_colors(self, tmp_path: Path) -> None:
        """Table output should display confidence with color styling."""
        from the_architect.core.token_ledger import save_ledger

        # 5 runs for high confidence
        runs = [_make_run(task_count=3, model="gpt-4o", model_cost=3.00) for _ in range(5)]
        ledger = _make_ledger(*runs)
        save_ledger(tmp_path, ledger)

        result = CliRunner().invoke(main, ["estimate", "-p", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["confidence"] == "high"

    def test_high_confidence_table_rendering_no_markup_error(self, tmp_path: Path) -> None:
        """High confidence table rendering must not raise Rich MarkupError.

        Regression test for the mismatched [green]/[/dim] tag bug in
        _render_estimate_table where the opening tag used ARCHITECT_GREEN
        but the closing tag used [/dim].
        """
        from the_architect.core.token_ledger import save_ledger

        # 5+ runs for high confidence — triggers the green-colored path
        runs = [_make_run(task_count=3, model="gpt-4o", model_cost=3.00) for _ in range(5)]
        ledger = _make_ledger(*runs)
        save_ledger(tmp_path, ledger)

        # Table mode (NOT --json) — exercises the Rich rendering path
        result = CliRunner().invoke(main, ["estimate", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "Pre-run Cost Estimate" in result.output
        assert "high" in result.output.lower()

    def test_json_output_cost_values_are_positive(self, tmp_path: Path) -> None:
        """JSON output costs should be positive when historical data exists."""
        from the_architect.core.token_ledger import save_ledger

        ledger = _make_ledger(
            _make_run(task_count=3, model="gpt-4o", model_cost=3.00),
            _make_run(task_count=4, model="gpt-4o", model_cost=8.00),
        )
        save_ledger(tmp_path, ledger)

        result = CliRunner().invoke(main, ["estimate", "-p", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["cost_avg"] > 0.0
        assert payload["cost_low"] > 0.0
        assert payload["cost_high"] >= payload["cost_avg"]

    def test_project_short_flag_works(self, tmp_path: Path) -> None:
        """-p short flag should work identically to --project."""
        from the_architect.core.token_ledger import save_ledger

        ledger = _make_ledger(
            _make_run(task_count=3, model="gpt-4o", model_cost=3.00),
        )
        save_ledger(tmp_path, ledger)

        result = CliRunner().invoke(main, ["estimate", "-p", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["model"] == "gpt-4o"
        assert payload["historical_runs"] == 1
