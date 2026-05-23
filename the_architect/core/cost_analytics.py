"""Cross-run cost analytics from the token ledger.

Aggregates historical run data from :class:`TokenLedger` into summary views:
total spending, per-model breakdown, top expensive tasks, and daily spending
trends.  All functions are pure — they accept a :class:`TokenLedger` instance
and return computed results without side effects.

This module is consumed by the ``architect cost`` CLI command (T02) and the
mode-selection TUI spending summary (T03).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from pydantic import BaseModel, Field

from the_architect.core.token_ledger import (
    LedgerRunRecord,
    TokenLedger,
    _normalise_model,
)

# ---------------------------------------------------------------------------
# Public result models
# ---------------------------------------------------------------------------


class ModelCostSummary(BaseModel):
    """Per-model cost and token summary across filtered runs.

    Attributes:
        total_cost: Total estimated cost for this model in USD.
        total_tokens: Total tokens consumed by this model.
        run_count: Number of runs that used this model.
        avg_cost_per_run: Average cost per run for this model in USD.
    """

    total_cost: float = Field(description="Total estimated cost in USD")
    total_tokens: int = Field(description="Total tokens consumed")
    run_count: int = Field(description="Number of runs using this model")
    avg_cost_per_run: float = Field(description="Average cost per run in USD")


class TaskCostEntry(BaseModel):
    """Individual task cost entry for the top-expensive-tasks ranking.

    Attributes:
        run_id: The run identifier this task belonged to.
        task_id: Task prefix (e.g. ``"T01"``).
        title: Human-readable task title.
        cost_estimate: Estimated cost for this task in USD.
        model: Model identifier used by this task.
        tokens: Total tokens consumed by this task.
    """

    run_id: str = Field(description="Run identifier")
    task_id: str = Field(description="Task prefix identifier (e.g. T01)")
    title: str = Field(default="", description="Human-readable task title")
    cost_estimate: float = Field(description="Estimated cost in USD")
    model: str = Field(default="", description="Model identifier string")
    tokens: int = Field(description="Total tokens consumed")


class DailySpendingEntry(BaseModel):
    """Daily spending summary entry.

    Attributes:
        date: ISO date string (e.g. ``"2026-05-14"``).
        cost: Total estimated cost for that day in USD.
        tokens: Total tokens consumed that day.
        runs: Number of runs that completed on that day.
    """

    date: str = Field(description="ISO date string (YYYY-MM-DD)")
    cost: float = Field(description="Total cost for the day in USD")
    tokens: int = Field(description="Total tokens for the day")
    runs: int = Field(description="Number of runs on that day")


class CostAnalytics(BaseModel):
    """Top-level cost analytics result from aggregating a token ledger.

    Attributes:
        total_cost: Sum of all run costs in USD.
        total_tokens: Sum of all tokens across runs.
        run_count: Number of runs in scope.
        model_breakdown: Per-model cost and token summary, keyed by model name.
        top_expensive_tasks: Tasks sorted by cost descending.
        daily_spending: Daily spending entries sorted by date ascending.
    """

    total_cost: float = Field(description="Sum of all run costs in USD")
    total_tokens: int = Field(description="Sum of all tokens across runs")
    run_count: int = Field(description="Number of runs in scope")
    model_breakdown: dict[str, ModelCostSummary] = Field(
        default_factory=dict,
        description="Per-model cost summary keyed by model name",
    )
    top_expensive_tasks: list[TaskCostEntry] = Field(
        default_factory=list,
        description="Tasks sorted by cost descending",
    )
    daily_spending: list[DailySpendingEntry] = Field(
        default_factory=list,
        description="Daily spending sorted by date ascending",
    )


# ---------------------------------------------------------------------------
# Core aggregation function
# ---------------------------------------------------------------------------


def aggregate_costs(
    ledger: TokenLedger,
    since: str | None = None,
    until: str | None = None,
    model: str | None = None,
    top_n: int = 10,
) -> CostAnalytics:
    """Compute cross-run cost analytics from a token ledger.

    Aggregates filtered ledger records into totals, per-model breakdown,
    top expensive tasks, and daily spending trends.  Returns an empty
    :class:`CostAnalytics` when the ledger has no matching records.

    Args:
        ledger: The token ledger containing historical run records.
        since: Inclusive ISO start date or timestamp filter.
        until: Exclusive ISO end date or timestamp filter.
        model: Optional model identifier to filter by.
        top_n: Maximum number of tasks in the expensive-tasks list.

    Returns:
        A :class:`CostAnalytics` instance with computed aggregations.
    """
    # Step 1: filter records by date and model
    filtered_records = _filter_records(ledger.records, since=since, until=until, model=model)

    if not filtered_records:
        return CostAnalytics(
            total_cost=0.0,
            total_tokens=0,
            run_count=0,
            model_breakdown={},
            top_expensive_tasks=[],
            daily_spending=[],
        )

    # Step 2: compute totals
    total_cost = round(sum(r.total_cost_estimate for r in filtered_records), 6)
    total_tokens = sum(r.total_tokens for r in filtered_records)
    run_count = len(filtered_records)

    # Step 3: compute model breakdown
    model_breakdown = _compute_model_breakdown(filtered_records)

    # Step 4: compute top expensive tasks
    all_tasks = _collect_all_tasks(filtered_records)
    top_expensive_tasks = _top_tasks(all_tasks, top_n=top_n)

    # Step 5: compute daily spending
    daily_spending = _compute_daily_spending(filtered_records)

    return CostAnalytics(
        total_cost=total_cost,
        total_tokens=total_tokens,
        run_count=run_count,
        model_breakdown=model_breakdown,
        top_expensive_tasks=top_expensive_tasks,
        daily_spending=daily_spending,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _filter_records(
    records: list[LedgerRunRecord],
    since: str | None = None,
    until: str | None = None,
    model: str | None = None,
) -> list[LedgerRunRecord]:
    """Return records matching the date and model filters.

    Args:
        records: All ledger run records.
        since: Inclusive start timestamp.
        until: Exclusive end timestamp.
        model: Model identifier to filter by.

    Returns:
        Filtered list of :class:`LedgerRunRecord` instances.
    """
    result = records

    # Date filters
    if since is not None:
        result = [r for r in result if r.timestamp >= since]
    if until is not None:
        result = [r for r in result if r.timestamp < until]

    # Model filter
    if model is not None:
        normalised = _normalise_model(model)
        result = [
            r
            for r in result
            if any(_normalise_model(mb.model) == normalised for mb in r.model_breakdown)
        ]

    return result


def _compute_model_breakdown(
    records: list[LedgerRunRecord],
) -> dict[str, ModelCostSummary]:
    """Aggregate cost and token data by model across filtered records.

    Each run may have multiple model entries in ``model_breakdown``.  A run
    counts toward a model's ``run_count`` if that model appears in the run's
    breakdown, even if the cost is zero.

    Args:
        records: Filtered ledger run records.

    Returns:
        Mapping of model name to :class:`ModelCostSummary`.
    """
    cost_by_model: dict[str, float] = defaultdict(float)
    tokens_by_model: dict[str, int] = defaultdict(int)
    runs_by_model: dict[str, set[str]] = defaultdict(set)

    for record in records:
        for mb in record.model_breakdown:
            cost_by_model[mb.model] += mb.cost_estimate
            tokens_by_model[mb.model] += (
                mb.input_tokens + mb.output_tokens + mb.cache_read_tokens + mb.cache_write_tokens
            )
            runs_by_model[mb.model].add(record.run_id)

    breakdown: dict[str, ModelCostSummary] = {}
    for model_name in cost_by_model:
        total_cost = round(cost_by_model[model_name], 6)
        total_tokens = tokens_by_model[model_name]
        run_count = len(runs_by_model[model_name])
        avg_cost = round(total_cost / run_count, 6) if run_count > 0 else 0.0
        breakdown[model_name] = ModelCostSummary(
            total_cost=total_cost,
            total_tokens=total_tokens,
            run_count=run_count,
            avg_cost_per_run=avg_cost,
        )

    return breakdown


def _collect_all_tasks(records: list[LedgerRunRecord]) -> list[TaskCostEntry]:
    """Flatten all task-level records from filtered runs into entries.

    Tasks with ``None`` or zero cost estimates are included (with cost 0.0)
    so the ranking is complete.

    Args:
        records: Filtered ledger run records.

    Returns:
        List of :class:`TaskCostEntry` instances (unsorted).
    """
    entries: list[TaskCostEntry] = []
    for record in records:
        for task in record.task_breakdown:
            tokens = (
                task.input_tokens
                + task.output_tokens
                + task.cache_read_tokens
                + task.cache_write_tokens
            )
            entries.append(
                TaskCostEntry(
                    run_id=record.run_id,
                    task_id=task.task_id,
                    title=task.title,
                    cost_estimate=task.cost_estimate,
                    model=task.model,
                    tokens=tokens,
                )
            )
    return entries


def _top_tasks(
    tasks: list[TaskCostEntry],
    top_n: int = 10,
) -> list[TaskCostEntry]:
    """Return the *top_n* most expensive tasks sorted by cost descending.

    Args:
        tasks: All task cost entries.
        top_n: Maximum number of entries to return.

    Returns:
        Sorted list of the most expensive :class:`TaskCostEntry` instances.
    """
    sorted_tasks = sorted(tasks, key=lambda t: t.cost_estimate, reverse=True)
    return sorted_tasks[:top_n]


def _compute_daily_spending(
    records: list[LedgerRunRecord],
) -> list[DailySpendingEntry]:
    """Aggregate spending by calendar date.

    The date is extracted from each record's ISO timestamp.  Records with
    timestamps that cannot be parsed are silently skipped.

    Args:
        records: Filtered ledger run records.

    Returns:
        List of :class:`DailySpendingEntry` sorted by date ascending.
    """
    cost_by_date: dict[str, float] = defaultdict(float)
    tokens_by_date: dict[str, int] = defaultdict(int)
    runs_by_date: dict[str, int] = defaultdict(int)

    for record in records:
        date_str = _extract_date(record.timestamp)
        if date_str is None:
            continue
        cost_by_date[date_str] += record.total_cost_estimate
        tokens_by_date[date_str] += record.total_tokens
        runs_by_date[date_str] += 1

    entries: list[DailySpendingEntry] = []
    for date_str in sorted(cost_by_date.keys()):
        entries.append(
            DailySpendingEntry(
                date=date_str,
                cost=round(cost_by_date[date_str], 6),
                tokens=tokens_by_date[date_str],
                runs=runs_by_date[date_str],
            )
        )

    return entries


def _extract_date(timestamp: str) -> str | None:
    """Extract an ISO date string (YYYY-MM-DD) from an ISO timestamp.

    Handles both full ISO format (``2026-05-14T12:00:00+00:00``) and bare
    date strings (``2026-05-14``).  Returns ``None`` if parsing fails.

    Args:
        timestamp: ISO 8601 timestamp or date string.

    Returns:
        Date string (``YYYY-MM-DD``) or ``None`` on parse failure.
    """
    try:
        # Handle bare date strings
        if len(timestamp) == 10 and timestamp[4] == "-" and timestamp[7] == "-":
            return timestamp
        # Try full ISO parse
        dt = datetime.fromisoformat(timestamp)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, IndexError):
        return None
