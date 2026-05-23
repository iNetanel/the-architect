"""Tests for the cross-run cost analytics module.

Covers CostAnalytics models, aggregate_costs() aggregation logic,
filtering, and edge cases (empty ledger, single run, multiple models,
date filtering, model filtering, task breakdown, zero costs).
"""

from __future__ import annotations

from the_architect.core.cost_analytics import (
    CostAnalytics,
    DailySpendingEntry,
    ModelCostSummary,
    TaskCostEntry,
    aggregate_costs,
)
from the_architect.core.token_ledger import (
    LedgerRunRecord,
    LedgerTaskRecord,
    ModelTokenRecord,
    TokenLedger,
)

# ---------------------------------------------------------------------------
# Helpers to build test fixtures
# ---------------------------------------------------------------------------


def _make_run(
    run_id: str = "run-001",
    timestamp: str = "2026-05-14T12:00:00+00:00",
    total_cost: float = 3.00,
    total_tokens: int = 150_000,
    model: str = "gpt-4o",
    model_cost: float = 3.00,
    input_tokens: int = 100_000,
    output_tokens: int = 50_000,
    task_breakdown: list[LedgerTaskRecord] | None = None,
) -> LedgerRunRecord:
    """Build a minimal LedgerRunRecord for testing."""
    return LedgerRunRecord(
        run_id=run_id,
        timestamp=timestamp,
        total_cost_estimate=total_cost,
        total_tokens=total_tokens,
        model_breakdown=[
            ModelTokenRecord(
                model=model,
                cost_estimate=model_cost,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        ],
        task_breakdown=task_breakdown or [],
    )


def _make_ledger(*runs: LedgerRunRecord) -> TokenLedger:
    """Build a TokenLedger from run records."""
    return TokenLedger(records=list(runs))


# ---------------------------------------------------------------------------
# Model construction tests
# ---------------------------------------------------------------------------


class TestCostAnalyticsModel:
    """CostAnalytics model construction and defaults."""

    def test_default_values(self) -> None:
        analytics = CostAnalytics(
            total_cost=0.0,
            total_tokens=0,
            run_count=0,
        )
        assert analytics.total_cost == 0.0
        assert analytics.total_tokens == 0
        assert analytics.run_count == 0
        assert analytics.model_breakdown == {}
        assert analytics.top_expensive_tasks == []
        assert analytics.daily_spending == []

    def test_full_initialization(self) -> None:
        analytics = CostAnalytics(
            total_cost=10.50,
            total_tokens=500_000,
            run_count=3,
            model_breakdown={
                "gpt-4o": ModelCostSummary(
                    total_cost=10.50, total_tokens=500_000, run_count=3, avg_cost_per_run=3.50
                )
            },
            top_expensive_tasks=[
                TaskCostEntry(
                    run_id="r1",
                    task_id="T01",
                    title="Test",
                    cost_estimate=5.0,
                    model="gpt-4o",
                    tokens=250_000,
                )
            ],
            daily_spending=[
                DailySpendingEntry(date="2026-05-14", cost=10.50, tokens=500_000, runs=3)
            ],
        )
        assert analytics.total_cost == 10.50
        assert analytics.run_count == 3
        assert len(analytics.model_breakdown) == 1
        assert len(analytics.top_expensive_tasks) == 1
        assert len(analytics.daily_spending) == 1

    def test_serialization(self) -> None:
        analytics = CostAnalytics(
            total_cost=5.0,
            total_tokens=100_000,
            run_count=1,
        )
        dumped = analytics.model_dump()
        assert dumped["total_cost"] == 5.0
        assert dumped["total_tokens"] == 100_000
        assert dumped["run_count"] == 1
        assert "model_breakdown" in dumped
        assert "top_expensive_tasks" in dumped
        assert "daily_spending" in dumped


class TestModelCostSummaryModel:
    """ModelCostSummary model construction."""

    def test_construction(self) -> None:
        summary = ModelCostSummary(
            total_cost=6.0,
            total_tokens=300_000,
            run_count=2,
            avg_cost_per_run=3.0,
        )
        assert summary.total_cost == 6.0
        assert summary.total_tokens == 300_000
        assert summary.run_count == 2
        assert summary.avg_cost_per_run == 3.0

    def test_serialization(self) -> None:
        summary = ModelCostSummary(
            total_cost=1.5,
            total_tokens=75_000,
            run_count=1,
            avg_cost_per_run=1.5,
        )
        dumped = summary.model_dump()
        assert set(dumped.keys()) == {"total_cost", "total_tokens", "run_count", "avg_cost_per_run"}


class TestTaskCostEntryModel:
    """TaskCostEntry model construction."""

    def test_construction_with_defaults(self) -> None:
        entry = TaskCostEntry(
            run_id="r1",
            task_id="T01",
            cost_estimate=2.5,
            model="gpt-4o",
            tokens=125_000,
        )
        assert entry.title == ""
        assert entry.run_id == "r1"
        assert entry.task_id == "T01"
        assert entry.cost_estimate == 2.5

    def test_construction_with_title(self) -> None:
        entry = TaskCostEntry(
            run_id="r1",
            task_id="T02R1",
            title="Fix flaky tests",
            cost_estimate=1.2,
            model="claude-sonnet-4-5",
            tokens=60_000,
        )
        assert entry.title == "Fix flaky tests"
        assert entry.model == "claude-sonnet-4-5"

    def test_serialization(self) -> None:
        entry = TaskCostEntry(
            run_id="r1",
            task_id="T01",
            title="Test",
            cost_estimate=2.5,
            model="gpt-4o",
            tokens=125_000,
        )
        dumped = entry.model_dump()
        assert set(dumped.keys()) == {
            "run_id",
            "task_id",
            "title",
            "cost_estimate",
            "model",
            "tokens",
        }


class TestDailySpendingEntryModel:
    """DailySpendingEntry model construction."""

    def test_construction(self) -> None:
        entry = DailySpendingEntry(
            date="2026-05-14",
            cost=15.5,
            tokens=775_000,
            runs=5,
        )
        assert entry.date == "2026-05-14"
        assert entry.cost == 15.5
        assert entry.tokens == 775_000
        assert entry.runs == 5

    def test_serialization(self) -> None:
        entry = DailySpendingEntry(
            date="2026-05-14",
            cost=15.5,
            tokens=775_000,
            runs=5,
        )
        dumped = entry.model_dump()
        assert set(dumped.keys()) == {"date", "cost", "tokens", "runs"}


# ---------------------------------------------------------------------------
# aggregate_costs() — core aggregation
# ---------------------------------------------------------------------------


class TestAggregateCostsEmptyLedger:
    """aggregate_costs with empty ledger returns zeroed results."""

    def test_empty_ledger_returns_zeros(self) -> None:
        result = aggregate_costs(TokenLedger())
        assert result.total_cost == 0.0
        assert result.total_tokens == 0
        assert result.run_count == 0
        assert result.model_breakdown == {}
        assert result.top_expensive_tasks == []
        assert result.daily_spending == []


class TestAggregateCostsSingleRun:
    """aggregate_costs with single run computes correct totals."""

    def test_single_run_totals(self) -> None:
        ledger = _make_ledger(
            _make_run(total_cost=3.00, total_tokens=150_000),
        )
        result = aggregate_costs(ledger)
        assert result.total_cost == 3.00
        assert result.total_tokens == 150_000
        assert result.run_count == 1

    def test_single_run_model_breakdown(self) -> None:
        ledger = _make_ledger(
            _make_run(model="gpt-4o", model_cost=3.00, input_tokens=100_000, output_tokens=50_000),
        )
        result = aggregate_costs(ledger)
        assert "gpt-4o" in result.model_breakdown
        mb = result.model_breakdown["gpt-4o"]
        assert mb.total_cost == 3.00
        assert mb.total_tokens == 150_000
        assert mb.run_count == 1
        assert mb.avg_cost_per_run == 3.00

    def test_single_run_daily_spending(self) -> None:
        ledger = _make_ledger(
            _make_run(timestamp="2026-05-14T12:00:00+00:00", total_cost=3.00, total_tokens=150_000),
        )
        result = aggregate_costs(ledger)
        assert len(result.daily_spending) == 1
        day = result.daily_spending[0]
        assert day.date == "2026-05-14"
        assert day.cost == 3.00
        assert day.tokens == 150_000
        assert day.runs == 1


class TestAggregateCostsMultipleRuns:
    """aggregate_costs with multiple runs aggregates correctly."""

    def test_multiple_runs_totals(self) -> None:
        ledger = _make_ledger(
            _make_run(run_id="r1", total_cost=3.00, total_tokens=150_000),
            _make_run(run_id="r2", total_cost=5.00, total_tokens=250_000),
            _make_run(run_id="r3", total_cost=2.00, total_tokens=100_000),
        )
        result = aggregate_costs(ledger)
        assert result.total_cost == 10.00
        assert result.total_tokens == 500_000
        assert result.run_count == 3

    def test_multiple_runs_same_model(self) -> None:
        ledger = _make_ledger(
            _make_run(
                run_id="r1",
                model="gpt-4o",
                model_cost=3.00,
                input_tokens=100_000,
                output_tokens=50_000,
            ),
            _make_run(
                run_id="r2",
                model="gpt-4o",
                model_cost=5.00,
                input_tokens=200_000,
                output_tokens=50_000,
            ),
        )
        result = aggregate_costs(ledger)
        mb = result.model_breakdown["gpt-4o"]
        assert mb.total_cost == 8.00
        assert mb.total_tokens == 400_000
        assert mb.run_count == 2
        assert mb.avg_cost_per_run == 4.00

    def test_multiple_runs_different_models(self) -> None:
        ledger = _make_ledger(
            _make_run(
                run_id="r1",
                model="gpt-4o",
                model_cost=3.00,
                input_tokens=100_000,
                output_tokens=50_000,
            ),
            _make_run(
                run_id="r2",
                model="claude-sonnet-4-5",
                model_cost=5.00,
                input_tokens=200_000,
                output_tokens=50_000,
            ),
        )
        result = aggregate_costs(ledger)
        assert set(result.model_breakdown.keys()) == {"gpt-4o", "claude-sonnet-4-5"}
        assert result.model_breakdown["gpt-4o"].total_cost == 3.00
        assert result.model_breakdown["claude-sonnet-4-5"].total_cost == 5.00

    def test_daily_spending_groups_by_date(self) -> None:
        ledger = _make_ledger(
            _make_run(
                run_id="r1",
                timestamp="2026-05-14T10:00:00+00:00",
                total_cost=3.00,
                total_tokens=150_000,
            ),
            _make_run(
                run_id="r2",
                timestamp="2026-05-14T15:00:00+00:00",
                total_cost=5.00,
                total_tokens=250_000,
            ),
            _make_run(
                run_id="r3",
                timestamp="2026-05-15T10:00:00+00:00",
                total_cost=2.00,
                total_tokens=100_000,
            ),
        )
        result = aggregate_costs(ledger)
        assert len(result.daily_spending) == 2
        assert result.daily_spending[0].date == "2026-05-14"
        assert result.daily_spending[0].cost == 8.00
        assert result.daily_spending[0].runs == 2
        assert result.daily_spending[1].date == "2026-05-15"
        assert result.daily_spending[1].cost == 2.00
        assert result.daily_spending[1].runs == 1

    def test_daily_spending_sorted_ascending(self) -> None:
        ledger = _make_ledger(
            _make_run(
                run_id="r2",
                timestamp="2026-05-15T10:00:00+00:00",
                total_cost=2.00,
                total_tokens=100_000,
            ),
            _make_run(
                run_id="r1",
                timestamp="2026-05-14T10:00:00+00:00",
                total_cost=3.00,
                total_tokens=150_000,
            ),
        )
        result = aggregate_costs(ledger)
        assert result.daily_spending[0].date == "2026-05-14"
        assert result.daily_spending[1].date == "2026-05-15"


class TestAggregateCostsTopExpensiveTasks:
    """aggregate_costs top expensive tasks sorted by cost descending."""

    def test_top_tasks_sorted_by_cost_descending(self) -> None:
        ledger = _make_ledger(
            _make_run(
                run_id="r1",
                total_cost=10.0,
                total_tokens=500_000,
                task_breakdown=[
                    LedgerTaskRecord(
                        task_id="T01",
                        status="done",
                        input_tokens=200_000,
                        output_tokens=100_000,
                        model="gpt-4o",
                        cost_estimate=5.0,
                        duration_seconds=10.0,
                    ),
                    LedgerTaskRecord(
                        task_id="T02",
                        status="done",
                        input_tokens=100_000,
                        output_tokens=50_000,
                        model="gpt-4o",
                        cost_estimate=3.0,
                        duration_seconds=8.0,
                    ),
                    LedgerTaskRecord(
                        task_id="T03",
                        status="done",
                        input_tokens=50_000,
                        output_tokens=25_000,
                        model="gpt-4o",
                        cost_estimate=2.0,
                        duration_seconds=5.0,
                    ),
                ],
            ),
        )
        result = aggregate_costs(ledger, top_n=10)
        assert len(result.top_expensive_tasks) == 3
        assert result.top_expensive_tasks[0].cost_estimate == 5.0
        assert result.top_expensive_tasks[0].task_id == "T01"
        assert result.top_expensive_tasks[1].cost_estimate == 3.0
        assert result.top_expensive_tasks[2].cost_estimate == 2.0

    def test_top_n_limits_results(self) -> None:
        ledger = _make_ledger(
            _make_run(
                run_id="r1",
                total_cost=10.0,
                total_tokens=500_000,
                task_breakdown=[
                    LedgerTaskRecord(
                        task_id="T01",
                        status="done",
                        input_tokens=200_000,
                        output_tokens=100_000,
                        model="gpt-4o",
                        cost_estimate=5.0,
                        duration_seconds=10.0,
                    ),
                    LedgerTaskRecord(
                        task_id="T02",
                        status="done",
                        input_tokens=100_000,
                        output_tokens=50_000,
                        model="gpt-4o",
                        cost_estimate=3.0,
                        duration_seconds=8.0,
                    ),
                    LedgerTaskRecord(
                        task_id="T03",
                        status="done",
                        input_tokens=50_000,
                        output_tokens=25_000,
                        model="gpt-4o",
                        cost_estimate=2.0,
                        duration_seconds=5.0,
                    ),
                ],
            ),
        )
        result = aggregate_costs(ledger, top_n=2)
        assert len(result.top_expensive_tasks) == 2
        assert result.top_expensive_tasks[0].task_id == "T01"
        assert result.top_expensive_tasks[1].task_id == "T02"

    def test_top_tasks_empty_when_no_task_breakdown(self) -> None:
        ledger = _make_ledger(
            _make_run(total_cost=3.00, total_tokens=150_000),
        )
        result = aggregate_costs(ledger)
        assert result.top_expensive_tasks == []


class TestAggregateCostsDateFiltering:
    """aggregate_costs date filtering (since/until) works."""

    def test_since_filter_inclusive(self) -> None:
        ledger = _make_ledger(
            _make_run(
                run_id="r1",
                timestamp="2026-05-13T10:00:00+00:00",
                total_cost=1.00,
                total_tokens=50_000,
            ),
            _make_run(
                run_id="r2",
                timestamp="2026-05-14T10:00:00+00:00",
                total_cost=2.00,
                total_tokens=100_000,
            ),
            _make_run(
                run_id="r3",
                timestamp="2026-05-15T10:00:00+00:00",
                total_cost=3.00,
                total_tokens=150_000,
            ),
        )
        result = aggregate_costs(ledger, since="2026-05-14")
        assert result.run_count == 2
        assert result.total_cost == 5.00

    def test_until_filter_exclusive(self) -> None:
        ledger = _make_ledger(
            _make_run(
                run_id="r1",
                timestamp="2026-05-13T10:00:00+00:00",
                total_cost=1.00,
                total_tokens=50_000,
            ),
            _make_run(
                run_id="r2",
                timestamp="2026-05-14T10:00:00+00:00",
                total_cost=2.00,
                total_tokens=100_000,
            ),
            _make_run(
                run_id="r3",
                timestamp="2026-05-15T10:00:00+00:00",
                total_cost=3.00,
                total_tokens=150_000,
            ),
        )
        result = aggregate_costs(ledger, until="2026-05-15")
        assert result.run_count == 2
        assert result.total_cost == 3.00

    def test_since_and_until_combined(self) -> None:
        ledger = _make_ledger(
            _make_run(
                run_id="r1",
                timestamp="2026-05-13T10:00:00+00:00",
                total_cost=1.00,
                total_tokens=50_000,
            ),
            _make_run(
                run_id="r2",
                timestamp="2026-05-14T10:00:00+00:00",
                total_cost=2.00,
                total_tokens=100_000,
            ),
            _make_run(
                run_id="r3",
                timestamp="2026-05-15T10:00:00+00:00",
                total_cost=3.00,
                total_tokens=150_000,
            ),
        )
        result = aggregate_costs(ledger, since="2026-05-14", until="2026-05-15")
        assert result.run_count == 1
        assert result.total_cost == 2.00

    def test_date_filter_no_matches_returns_zeros(self) -> None:
        ledger = _make_ledger(
            _make_run(
                run_id="r1",
                timestamp="2026-05-13T10:00:00+00:00",
                total_cost=1.00,
                total_tokens=50_000,
            ),
        )
        result = aggregate_costs(ledger, since="2026-05-20")
        assert result.run_count == 0
        assert result.total_cost == 0.0


class TestAggregateCostsModelFiltering:
    """aggregate_costs model filtering works."""

    def test_model_filter_single_model(self) -> None:
        ledger = _make_ledger(
            _make_run(
                run_id="r1",
                model="gpt-4o",
                model_cost=3.00,
                input_tokens=100_000,
                output_tokens=50_000,
            ),
            _make_run(
                run_id="r2",
                model="claude-sonnet-4-5",
                model_cost=5.00,
                input_tokens=200_000,
                output_tokens=50_000,
            ),
        )
        result = aggregate_costs(ledger, model="gpt-4o")
        assert result.run_count == 1
        assert result.total_cost == 3.00
        assert "gpt-4o" in result.model_breakdown
        assert "claude-sonnet-4-5" not in result.model_breakdown

    def test_model_filter_no_matches_returns_zeros(self) -> None:
        ledger = _make_ledger(
            _make_run(
                run_id="r1",
                model="gpt-4o",
                model_cost=3.00,
                input_tokens=100_000,
                output_tokens=50_000,
            ),
        )
        result = aggregate_costs(ledger, model="claude-sonnet-4-5")
        assert result.run_count == 0
        assert result.total_cost == 0.0

    def test_model_filter_case_insensitive(self) -> None:
        ledger = _make_ledger(
            _make_run(
                run_id="r1",
                model="gpt-4o",
                model_cost=3.00,
                input_tokens=100_000,
                output_tokens=50_000,
            ),
        )
        result = aggregate_costs(ledger, model="GPT-4O")
        assert result.run_count == 1
        assert result.total_cost == 3.00


class TestAggregateCostsEdgeCases:
    """aggregate_costs handles edge cases gracefully."""

    def test_ledger_with_zero_cost(self) -> None:
        ledger = _make_ledger(
            _make_run(
                run_id="r1",
                total_cost=0.0,
                total_tokens=100_000,
                model_cost=0.0,
                input_tokens=100_000,
                output_tokens=0,
            ),
        )
        result = aggregate_costs(ledger)
        assert result.run_count == 1
        assert result.total_cost == 0.0
        assert result.total_tokens == 100_000

    def test_ledger_with_zero_tokens(self) -> None:
        ledger = _make_ledger(
            _make_run(
                run_id="r1",
                total_cost=3.00,
                total_tokens=0,
                model_cost=3.00,
                input_tokens=0,
                output_tokens=0,
            ),
        )
        result = aggregate_costs(ledger)
        assert result.run_count == 1
        assert result.total_cost == 3.00
        assert result.total_tokens == 0

    def test_task_breakdown_includes_all_tokens(self) -> None:
        ledger = _make_ledger(
            _make_run(
                run_id="r1",
                total_cost=10.0,
                total_tokens=500_000,
                task_breakdown=[
                    LedgerTaskRecord(
                        task_id="T01",
                        status="done",
                        input_tokens=100_000,
                        output_tokens=50_000,
                        cache_read_tokens=25_000,
                        cache_write_tokens=25_000,
                        model="gpt-4o",
                        cost_estimate=5.0,
                        duration_seconds=10.0,
                    ),
                ],
            ),
        )
        result = aggregate_costs(ledger)
        assert len(result.top_expensive_tasks) == 1
        task = result.top_expensive_tasks[0]
        assert task.tokens == 200_000  # sum of all token types

    def test_bare_date_string_in_timestamp(self) -> None:
        """Records with bare date strings (YYYY-MM-DD) should parse correctly."""
        ledger = _make_ledger(
            LedgerRunRecord(
                run_id="r1",
                timestamp="2026-05-14",
                total_cost_estimate=3.00,
                total_tokens=150_000,
                model_breakdown=[
                    ModelTokenRecord(
                        model="gpt-4o",
                        cost_estimate=3.00,
                        input_tokens=100_000,
                        output_tokens=50_000,
                    ),
                ],
            ),
        )
        result = aggregate_costs(ledger)
        assert result.run_count == 1
        assert len(result.daily_spending) == 1
        assert result.daily_spending[0].date == "2026-05-14"

    def test_invalid_timestamp_skipped_in_daily(self) -> None:
        """Records with invalid timestamps are silently skipped in daily spending."""
        ledger = TokenLedger(
            records=[
                LedgerRunRecord(
                    run_id="r1",
                    timestamp="not-a-date",
                    total_cost_estimate=3.00,
                    total_tokens=150_000,
                    model_breakdown=[
                        ModelTokenRecord(
                            model="gpt-4o",
                            cost_estimate=3.00,
                            input_tokens=100_000,
                            output_tokens=50_000,
                        ),
                    ],
                ),
            ]
        )
        result = aggregate_costs(ledger)
        assert result.run_count == 1
        assert result.total_cost == 3.00
        assert result.daily_spending == []  # invalid date skipped

    def test_combined_date_and_model_filter(self) -> None:
        """Date and model filters can be combined."""
        ledger = _make_ledger(
            _make_run(
                run_id="r1",
                timestamp="2026-05-14T10:00:00+00:00",
                model="gpt-4o",
                model_cost=3.00,
                total_cost=3.00,
                input_tokens=100_000,
                output_tokens=50_000,
            ),
            _make_run(
                run_id="r2",
                timestamp="2026-05-14T15:00:00+00:00",
                model="claude-sonnet-4-5",
                model_cost=5.00,
                total_cost=5.00,
                input_tokens=200_000,
                output_tokens=50_000,
            ),
            _make_run(
                run_id="r3",
                timestamp="2026-05-15T10:00:00+00:00",
                model="gpt-4o",
                model_cost=2.00,
                total_cost=2.00,
                input_tokens=50_000,
                output_tokens=25_000,
            ),
        )
        result = aggregate_costs(ledger, since="2026-05-14T00:00:00+00:00", model="gpt-4o")
        assert result.run_count == 2
        assert result.total_cost == 5.00

    def test_model_breakdown_run_count_is_unique_runs(self) -> None:
        """A model's run_count counts unique run_ids, not model entries."""
        # Two runs with the same model
        ledger = _make_ledger(
            _make_run(
                run_id="r1",
                model="gpt-4o",
                model_cost=3.00,
                input_tokens=100_000,
                output_tokens=50_000,
            ),
            _make_run(
                run_id="r2",
                model="gpt-4o",
                model_cost=5.00,
                input_tokens=200_000,
                output_tokens=50_000,
            ),
        )
        result = aggregate_costs(ledger)
        mb = result.model_breakdown["gpt-4o"]
        assert mb.run_count == 2
        assert mb.avg_cost_per_run == 4.00

    def test_multiple_models_in_single_run(self) -> None:
        """A single run with multiple model entries aggregates correctly."""
        ledger = TokenLedger(
            records=[
                LedgerRunRecord(
                    run_id="r1",
                    timestamp="2026-05-14T12:00:00+00:00",
                    total_cost_estimate=8.00,
                    total_tokens=500_000,
                    model_breakdown=[
                        ModelTokenRecord(
                            model="gpt-4o",
                            cost_estimate=3.00,
                            input_tokens=100_000,
                            output_tokens=50_000,
                        ),
                        ModelTokenRecord(
                            model="claude-sonnet-4-5",
                            cost_estimate=5.00,
                            input_tokens=200_000,
                            output_tokens=150_000,
                        ),
                    ],
                ),
            ]
        )
        result = aggregate_costs(ledger)
        assert result.run_count == 1
        assert result.total_cost == 8.00
        assert result.total_tokens == 500_000
        assert set(result.model_breakdown.keys()) == {"gpt-4o", "claude-sonnet-4-5"}
        assert result.model_breakdown["gpt-4o"].run_count == 1
        assert result.model_breakdown["claude-sonnet-4-5"].run_count == 1
