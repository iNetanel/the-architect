"""Tests for the cross-run token ledger model and persistence layer."""

from __future__ import annotations

import json
from pathlib import Path

from the_architect.core.runner import TaskResult, TokenUsage
from the_architect.core.token_ledger import (
    MODEL_PRICING,
    LedgerRunRecord,
    ModelTokenRecord,
    TokenLedger,
    _normalise_model,
    append_run,
    estimate_cost,
    estimate_cost_detailed,
    load_ledger,
    save_ledger,
)


class TestModelAndCost:
    """Tests for ledger Pydantic models and cost helpers."""

    def test_model_token_record_defaults_and_serialization(self) -> None:
        """ModelTokenRecord should serialize all token and cost fields."""
        record = ModelTokenRecord(model="gpt-4o", input_tokens=100, output_tokens=50)

        assert record.cache_read_tokens == 0
        assert record.cache_write_tokens == 0
        assert record.cost_estimate == 0.0
        assert record.model_dump() == {
            "model": "gpt-4o",
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_estimate": 0.0,
        }

    def test_ledger_run_record_defaults_and_dump_keys(self) -> None:
        """LedgerRunRecord should create run metadata automatically."""
        record = LedgerRunRecord(goal_summary="ship token ledger")
        dumped = record.model_dump()

        assert record.run_id
        assert record.timestamp
        assert dumped["goal_summary"] == "ship token ledger"
        assert dumped["total_tokens"] == 0
        assert dumped["outcome"] == "failure"
        assert set(dumped) == {
            "run_id",
            "timestamp",
            "goal_summary",
            "total_tokens",
            "total_cost_estimate",
            "model_breakdown",
            "task_count",
            "outcome",
            "duration_seconds",
        }

    def test_cost_helpers_handle_known_unknown_and_zero_models(self) -> None:
        """Cost helpers should estimate known models and return zero for unknowns."""
        assert estimate_cost(1_000_000, "gpt-4o") > 0
        assert estimate_cost(0, "gpt-4o") == 0.0
        assert estimate_cost(1_000_000, "unknown-model") == 0.0
        assert (
            estimate_cost_detailed(
                input_tokens=1_000,
                output_tokens=500,
                cache_read_tokens=250,
                cache_write_tokens=250,
                model="gpt-4o",
            )
            > 0
        )

    def test_model_normalisation_and_pricing_presence(self) -> None:
        """Pricing lookup should include common models and normalise names."""
        assert _normalise_model("  GPT-4O  ") == "gpt-4o"
        assert "gpt-4o" in MODEL_PRICING
        assert "claude-sonnet-4-5" in MODEL_PRICING


class TestLedgerAggregation:
    """Tests for TokenLedger query helpers."""

    def test_empty_ledger_totals_are_zero(self) -> None:
        """An empty ledger should report zero totals."""
        ledger = TokenLedger()

        assert ledger.total_tokens_all_runs() == 0
        assert ledger.total_cost_all_runs() == 0.0
        assert ledger.model_totals() == {}
        assert ledger.filter_by_date().records == []

    def test_populated_ledger_aggregates_runs_and_models(self) -> None:
        """Ledger totals should sum records and per-model token usage."""
        ledger = TokenLedger(
            records=[
                LedgerRunRecord(
                    timestamp="2026-05-01T00:00:00+00:00",
                    total_tokens=150,
                    total_cost_estimate=0.01,
                    model_breakdown=[
                        ModelTokenRecord(model="gpt-4o", input_tokens=100, output_tokens=50)
                    ],
                ),
                LedgerRunRecord(
                    timestamp="2026-05-02T00:00:00+00:00",
                    total_tokens=200,
                    total_cost_estimate=0.02,
                    model_breakdown=[
                        ModelTokenRecord(model="gpt-4o", input_tokens=125, output_tokens=75),
                        ModelTokenRecord(model="gpt-4o-mini", input_tokens=10, output_tokens=20),
                    ],
                ),
            ]
        )

        assert ledger.total_tokens_all_runs() == 350
        assert ledger.total_cost_all_runs() == 0.03
        totals = ledger.model_totals()
        assert totals["gpt-4o"].input_tokens == 225
        assert totals["gpt-4o"].output_tokens == 125
        assert totals["gpt-4o-mini"].total == 30

    def test_filter_by_date_uses_inclusive_start_and_exclusive_end(self) -> None:
        """Date filtering should keep records in [start, end)."""
        ledger = TokenLedger(
            records=[
                LedgerRunRecord(timestamp="2026-05-01T00:00:00+00:00", goal_summary="old"),
                LedgerRunRecord(timestamp="2026-05-02T00:00:00+00:00", goal_summary="kept"),
                LedgerRunRecord(timestamp="2026-05-03T00:00:00+00:00", goal_summary="end"),
            ]
        )

        filtered = ledger.filter_by_date(start="2026-05-02", end="2026-05-03")

        assert [r.goal_summary for r in filtered.records] == ["kept"]


class TestPersistence:
    """Tests for loading and saving token ledgers."""

    def test_load_missing_file_returns_empty_ledger(self, tmp_path: Path) -> None:
        """Missing ledger file should be treated as an empty ledger."""
        ledger = load_ledger(tmp_path)

        assert isinstance(ledger, TokenLedger)
        assert ledger.records == []

    def test_save_creates_ledger_file(self, tmp_path: Path) -> None:
        """save_ledger should create .architect/token_ledger.json."""
        save_ledger(tmp_path, TokenLedger())

        assert (tmp_path / ".architect" / "token_ledger.json").exists()

    def test_save_load_round_trip(self, tmp_path: Path) -> None:
        """A saved ledger should load back with matching data."""
        ledger = TokenLedger(records=[LedgerRunRecord(goal_summary="round trip", total_tokens=123)])

        save_ledger(tmp_path, ledger)
        loaded = load_ledger(tmp_path)

        assert len(loaded.records) == 1
        assert loaded.records[0].goal_summary == "round trip"
        assert loaded.records[0].total_tokens == 123

    def test_load_corrupt_json_returns_empty_ledger(self, tmp_path: Path) -> None:
        """Corrupt ledger JSON should not crash callers."""
        ledger_path = tmp_path / ".architect" / "token_ledger.json"
        ledger_path.parent.mkdir()
        ledger_path.write_text("not json", encoding="utf-8")

        assert load_ledger(tmp_path).records == []

    def test_load_non_list_json_returns_empty_ledger(self, tmp_path: Path) -> None:
        """Unexpected top-level JSON shape should be ignored."""
        ledger_path = tmp_path / ".architect" / "token_ledger.json"
        ledger_path.parent.mkdir()
        ledger_path.write_text(json.dumps({"foo": 1}), encoding="utf-8")

        assert load_ledger(tmp_path).records == []


class TestAppendRun:
    """Tests for appending run data from TaskResult values."""

    def test_append_run_single_result(self) -> None:
        """append_run should create a run record from one task result."""
        ledger = TokenLedger()
        result = TaskResult(
            prefix="T01",
            status="done",
            tokens=TokenUsage(input_tokens=100, output_tokens=200),
            model="gpt-4o",
        )

        append_run(ledger, [result], "goal", 10.0)

        record = ledger.records[0]
        assert record.total_tokens == 300
        assert record.task_count == 1
        assert record.outcome == "success"
        assert record.model_breakdown[0].model == "gpt-4o"

    def test_append_run_aggregates_two_models(self) -> None:
        """append_run should preserve per-model token breakdowns."""
        ledger = TokenLedger()
        results = [
            TaskResult(
                prefix="T01",
                status="done",
                tokens=TokenUsage(input_tokens=100, output_tokens=200),
                model="gpt-4o",
            ),
            TaskResult(
                prefix="T02",
                status="done",
                tokens=TokenUsage(input_tokens=50, output_tokens=75),
                model="claude-sonnet-4-5",
            ),
        ]

        append_run(ledger, results, "goal", 10.0)

        breakdown = {item.model: item for item in ledger.records[0].model_breakdown}
        assert set(breakdown) == {"gpt-4o", "claude-sonnet-4-5"}
        assert breakdown["gpt-4o"].input_tokens == 100
        assert breakdown["claude-sonnet-4-5"].output_tokens == 75

    def test_append_run_truncates_goal_and_records_failure(self) -> None:
        """Long goals should be truncated and failure outcome should persist."""
        ledger = TokenLedger()

        append_run(ledger, [], "x" * 300, 5.0, outcome="failure")

        record = ledger.records[0]
        assert len(record.goal_summary) == 200
        assert record.outcome == "failure"

    def test_full_append_save_load_round_trip(self, tmp_path: Path) -> None:
        """append_run output should survive JSON persistence."""
        ledger = TokenLedger()
        append_run(
            ledger,
            [
                TaskResult(
                    prefix="T01",
                    status="done",
                    tokens=TokenUsage(input_tokens=1_000, output_tokens=500),
                    model="gpt-4o",
                )
            ],
            "persisted goal",
            12.5,
        )

        save_ledger(tmp_path, ledger)
        loaded = load_ledger(tmp_path)

        assert len(loaded.records) == 1
        assert loaded.records[0].goal_summary == "persisted goal"
        assert loaded.records[0].total_tokens == 1_500
        assert loaded.records[0].model_breakdown[0].cost_estimate > 0
