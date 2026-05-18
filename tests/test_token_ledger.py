"""Tests for the cross-run token ledger model and persistence layer."""

from __future__ import annotations

import json
from pathlib import Path

from the_architect.core.runner import TaskResult, TokenUsage
from the_architect.core.token_ledger import (
    MODEL_PRICING,
    LedgerRunRecord,
    LedgerTaskRecord,
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
            "task_breakdown",
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


class TestEstimateCostPartialMatch:
    """Tests for partial model matching in estimate_cost and estimate_cost_detailed."""

    def test_estimate_cost_partial_match_suffix(self) -> None:
        """estimate_cost should find pricing via partial suffix match.

        A model like 'some-provider/gpt-4o' is not in MODEL_PRICING directly,
        but ends with 'gpt-4o' which is — the partial match loop should find it.
        """
        cost = estimate_cost(1_000_000, "some-provider/gpt-4o")
        assert cost > 0

    def test_estimate_cost_detailed_partial_match_suffix(self) -> None:
        """estimate_cost_detailed should find pricing via partial suffix match."""
        cost = estimate_cost_detailed(
            input_tokens=500_000,
            output_tokens=500_000,
            cache_read_tokens=0,
            cache_write_tokens=0,
            model="some-provider/gpt-4o",
        )
        assert cost > 0

    def test_estimate_cost_detailed_unknown_model_returns_zero(self) -> None:
        """estimate_cost_detailed should return 0.0 for a truly unknown model.

        A model that does not match any key (neither exact nor partial) should
        fall through to the debug-log + return 0.0 path.
        """
        cost = estimate_cost_detailed(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=0,
            cache_write_tokens=0,
            model="totally-unknown-model-xyz-12345",
        )
        assert cost == 0.0


class TestLoadLedgerOSError:
    """Tests for OSError handling in load_ledger."""

    def test_load_ledger_oserror_returns_empty(self, tmp_path: Path) -> None:
        """load_ledger should return an empty ledger when reading raises OSError."""
        from unittest.mock import patch

        # Ensure the ledger file exists so FileNotFoundError is not triggered first
        ledger_path = tmp_path / ".architect" / "token_ledger.json"
        ledger_path.parent.mkdir()
        ledger_path.write_text("[]", encoding="utf-8")

        with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
            ledger = load_ledger(tmp_path)

        assert isinstance(ledger, TokenLedger)
        assert ledger.records == []


class TestAppendRunSameModelAggregation:
    """Tests for the same-model token aggregation path in append_run."""

    def test_append_run_aggregates_same_model(self) -> None:
        """append_run should sum tokens for results sharing the same model.

        Two TaskResult objects with the same model should be aggregated into
        a single ModelTokenRecord with summed token counts — exercising the
        else-branch of the model_tokens dict update loop.
        """
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
                model="gpt-4o",
            ),
        ]

        append_run(ledger, results, "goal", 10.0)

        record = ledger.records[0]
        assert record.task_count == 2
        assert record.total_tokens == 425
        assert len(record.model_breakdown) == 1
        assert record.model_breakdown[0].input_tokens == 150
        assert record.model_breakdown[0].output_tokens == 275


class TestLedgerTaskRecord:
    """Tests for the LedgerTaskRecord per-task cost model."""

    def test_ledger_task_record_defaults_and_serialization(self) -> None:
        """LedgerTaskRecord should serialize all task-level fields."""
        record = LedgerTaskRecord(
            task_id="T01",
            status="done",
            input_tokens=100,
            output_tokens=200,
            model="gpt-4o",
            cost_estimate=0.001,
            duration_seconds=15.5,
        )

        assert record.title == ""
        assert record.cache_read_tokens == 0
        assert record.cache_write_tokens == 0
        dumped = record.model_dump()
        assert dumped["task_id"] == "T01"
        assert dumped["status"] == "done"
        assert dumped["input_tokens"] == 100
        assert dumped["output_tokens"] == 200
        assert dumped["model"] == "gpt-4o"
        assert dumped["cost_estimate"] == 0.001
        assert dumped["duration_seconds"] == 15.5

    def test_ledger_task_record_all_fields(self) -> None:
        """LedgerTaskRecord should accept all fields including title and cache."""
        record = LedgerTaskRecord(
            task_id="T02R1",
            title="Fix flaky tests",
            status="failed",
            input_tokens=500,
            output_tokens=300,
            cache_read_tokens=100,
            cache_write_tokens=50,
            model="claude-sonnet-4-5",
            cost_estimate=0.05,
            duration_seconds=45.0,
        )

        assert record.task_id == "T02R1"
        assert record.title == "Fix flaky tests"
        assert record.status == "failed"
        assert record.cache_read_tokens == 100
        assert record.cache_write_tokens == 50


class TestAppendRunTaskBreakdown:
    """Tests for task_breakdown population in append_run."""

    def test_append_run_populates_task_breakdown(self) -> None:
        """append_run should create a LedgerTaskRecord per TaskResult."""
        ledger = TokenLedger()
        results = [
            TaskResult(
                prefix="T01",
                title="Core model",
                status="done",
                tokens=TokenUsage(input_tokens=100, output_tokens=200),
                model="gpt-4o",
                duration_seconds=10.0,
            ),
            TaskResult(
                prefix="T02",
                title="CLI command",
                status="done",
                tokens=TokenUsage(input_tokens=50, output_tokens=75),
                model="claude-sonnet-4-5",
                duration_seconds=20.0,
            ),
        ]

        append_run(ledger, results, "goal", 30.0)

        record = ledger.records[0]
        assert len(record.task_breakdown) == 2

        t1 = record.task_breakdown[0]
        assert t1.task_id == "T01"
        assert t1.title == "Core model"
        assert t1.status == "done"
        assert t1.input_tokens == 100
        assert t1.output_tokens == 200
        assert t1.model == "gpt-4o"
        assert t1.cost_estimate > 0
        assert t1.duration_seconds == 10.0

        t2 = record.task_breakdown[1]
        assert t2.task_id == "T02"
        assert t2.title == "CLI command"
        assert t2.status == "done"
        assert t2.model == "claude-sonnet-4-5"
        assert t2.duration_seconds == 20.0

    def test_append_run_task_breakdown_cost_matches_model(self) -> None:
        """Task cost estimates should be computed via estimate_cost_detailed."""
        ledger = TokenLedger()
        result = TaskResult(
            prefix="T01",
            status="done",
            tokens=TokenUsage(
                input_tokens=1_000,
                output_tokens=500,
                cache_read_tokens=100,
                cache_write_tokens=100,
            ),
            model="gpt-4o",
        )

        append_run(ledger, [result], "goal", 10.0)

        task = ledger.records[0].task_breakdown[0]
        expected_cost = estimate_cost_detailed(
            input_tokens=1_000,
            output_tokens=500,
            cache_read_tokens=100,
            cache_write_tokens=100,
            model="gpt-4o",
        )
        assert task.cost_estimate == expected_cost

    def test_append_run_task_breakdown_empty_results(self) -> None:
        """append_run with empty results should produce an empty task_breakdown."""
        ledger = TokenLedger()

        append_run(ledger, [], "goal", 5.0)

        record = ledger.records[0]
        assert record.task_breakdown == []
        assert record.task_count == 0

    def test_append_run_task_breakdown_with_failed_task(self) -> None:
        """Task breakdown should preserve per-task status including failures."""
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
                status="failed",
                tokens=TokenUsage(input_tokens=50, output_tokens=25),
                model="gpt-4o",
            ),
        ]

        append_run(ledger, results, "goal", 10.0)

        statuses = {t.task_id: t.status for t in ledger.records[0].task_breakdown}
        assert statuses["T01"] == "done"
        assert statuses["T02"] == "failed"


class TestBackwardCompatibility:
    """Tests for backward-compatible loading of old ledger records."""

    def test_load_record_without_task_breakdown(self, tmp_path: Path) -> None:
        """Old records missing task_breakdown should load with empty list."""
        ledger_path = tmp_path / ".architect" / "token_ledger.json"
        ledger_path.parent.mkdir()
        # Simulate an old record that has no task_breakdown field
        old_record = {
            "run_id": "abc123",
            "timestamp": "2026-05-01T00:00:00+00:00",
            "goal_summary": "old run",
            "total_tokens": 100,
            "total_cost_estimate": 0.01,
            "model_breakdown": [],
            "task_count": 1,
            "outcome": "success",
            "duration_seconds": 10.0,
        }
        ledger_path.write_text(json.dumps([old_record]), encoding="utf-8")

        loaded = load_ledger(tmp_path)

        assert len(loaded.records) == 1
        assert loaded.records[0].goal_summary == "old run"
        assert loaded.records[0].task_breakdown == []

    def test_save_load_task_breakdown_round_trip(self, tmp_path: Path) -> None:
        """task_breakdown should survive JSON persistence."""
        ledger = TokenLedger()
        append_run(
            ledger,
            [
                TaskResult(
                    prefix="T01",
                    title="test task",
                    status="done",
                    tokens=TokenUsage(input_tokens=1_000, output_tokens=500),
                    model="gpt-4o",
                    duration_seconds=12.5,
                )
            ],
            "round trip goal",
            15.0,
        )

        save_ledger(tmp_path, ledger)
        loaded = load_ledger(tmp_path)

        assert len(loaded.records) == 1
        task = loaded.records[0].task_breakdown[0]
        assert task.task_id == "T01"
        assert task.title == "test task"
        assert task.status == "done"
        assert task.input_tokens == 1_000
        assert task.output_tokens == 500
        assert task.duration_seconds == 12.5
        assert task.cost_estimate > 0
