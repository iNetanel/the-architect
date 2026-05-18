"""Pre-run cost estimation from historical token ledger data.

Queries the cross-run token ledger to compute forward-looking cost estimates
for upcoming Architect runs.  Provides average cost per task, per-model
statistics, and confidence ranges based on historical success data.

All estimation functions are pure — they accept a :class:`TokenLedger`
instance and return computed results without side effects.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from the_architect.core.token_ledger import (
    LedgerRunRecord,
    TokenLedger,
    _normalise_model,
    estimate_cost,
)

# ---------------------------------------------------------------------------
# Public result models
# ---------------------------------------------------------------------------


class EstimateResult(BaseModel):
    """Cost estimate for a single upcoming run.

    Attributes:
        model: Model identifier used for the estimate.
        task_count: Number of tasks the estimate covers.
        cost_low: Pessimistic low-bound cost in USD.
        cost_high: Optimistic high-bound cost in USD.
        cost_avg: Average estimated cost in USD.
        historical_runs: Number of historical runs used for the estimate.
        confidence: Confidence level derived from data availability.
    """

    model: str = Field(description="Model identifier used for the estimate")
    task_count: int = Field(description="Number of tasks the estimate covers")
    cost_low: float = Field(default=0.0, description="Low-bound estimated cost in USD")
    cost_high: float = Field(default=0.0, description="High-bound estimated cost in USD")
    cost_avg: float = Field(default=0.0, description="Average estimated cost in USD")
    historical_runs: int = Field(
        default=0, description="Number of historical runs used for the estimate"
    )
    confidence: Literal["high", "medium", "low"] = Field(
        default="low",
        description="Confidence level: high (>=5 runs), medium (2-4), low (<2)",
    )


@dataclass
class TaskCountStats:
    """Task count statistics derived from historical runs.

    Attributes:
        min_tasks: Minimum task count across historical runs.
        max_tasks: Maximum task count across historical runs.
        avg_tasks: Average task count across historical runs.
        run_count: Number of runs analysed.
    """

    min_tasks: int = 0
    max_tasks: int = 0
    avg_tasks: float = 0.0
    run_count: int = 0


# ---------------------------------------------------------------------------
# Core estimation functions
# ---------------------------------------------------------------------------


def _successful_runs(ledger: TokenLedger) -> list[LedgerRunRecord]:
    """Return only the successful run records from *ledger*.

    Only runs with ``outcome == "success"`` are considered reliable for
    cost estimation.  Failed runs often have incomplete token data from
    interrupted or retried tasks.

    Args:
        ledger: The full token ledger.

    Returns:
        List of successful :class:`LedgerRunRecord` instances.
    """
    return [r for r in ledger.records if r.outcome == "success"]


def _runs_for_model(ledger: TokenLedger, model: str) -> list[LedgerRunRecord]:
    """Return successful runs that used *model*.

    Args:
        ledger: The full token ledger.
        model: Model identifier to filter by.

    Returns:
        List of successful :class:`LedgerRunRecord` instances that used *model*.
    """
    normalised = _normalise_model(model)
    successful = _successful_runs(ledger)
    return [
        r
        for r in successful
        if any(_normalise_model(mb.model) == normalised for mb in r.model_breakdown)
    ]


def estimate_run_cost(
    ledger: TokenLedger,
    model: str,
    task_count: int,
) -> EstimateResult:
    """Compute a cost estimate for an upcoming run from historical data.

    Uses successful historical runs that used *model* to compute average
    cost per task, then multiplies by the requested *task_count*.  The
    low/high bounds are derived from the minimum and maximum observed
    per-task costs.

    When no historical data exists for *model*, falls back to the built-in
    :data:`MODEL_PRICING` table with a rough token-per-task assumption
    (50,000 tokens per task).  When the ledger has data for other models
    but not *model*, uses cross-model averages as a secondary fallback.

    Args:
        ledger: The token ledger containing historical run records.
        model: The model identifier for the upcoming run.
        task_count: Expected number of tasks in the upcoming run.

    Returns:
        An :class:`EstimateResult` with cost ranges and confidence level.
    """
    if task_count <= 0:
        return EstimateResult(
            model=model,
            task_count=0,
            cost_low=0.0,
            cost_high=0.0,
            cost_avg=0.0,
            historical_runs=0,
            confidence="low",
        )

    # Try model-specific historical data first
    model_runs = _runs_for_model(ledger, model)

    if model_runs:
        return _estimate_from_model_runs(model_runs, model, task_count)

    # Fallback: use all successful runs for cross-model estimate
    all_successful = _successful_runs(ledger)
    if all_successful:
        return _estimate_from_all_runs(all_successful, model, task_count)

    # Last resort: pricing table fallback
    return _estimate_from_pricing_table(model, task_count)


def _estimate_from_model_runs(
    runs: list[LedgerRunRecord],
    model: str,
    task_count: int,
) -> EstimateResult:
    """Estimate cost from runs that specifically used *model*.

    Args:
        runs: Successful runs that used the target model.
        model: Model identifier.
        task_count: Number of tasks to estimate for.

    Returns:
        An :class:`EstimateResult` based on model-specific history.
    """
    # Compute per-task costs for the target model across runs
    costs_per_task: list[float] = []
    for r in runs:
        # Find the model's cost within this run
        model_cost = 0.0
        for mb in r.model_breakdown:
            if _normalise_model(mb.model) == _normalise_model(model):
                model_cost = mb.cost_estimate
                break
        tasks_in_run = r.task_count
        if tasks_in_run > 0 and model_cost > 0:
            costs_per_task.append(model_cost / tasks_in_run)

    if not costs_per_task:
        # Model was used but costs are zero — fall back to pricing table
        return _estimate_from_pricing_table(model, task_count)

    avg_cost = statistics.mean(costs_per_task)
    min_cost = min(costs_per_task)
    max_cost = max(costs_per_task)

    total_avg = round(avg_cost * task_count, 4)
    total_low = round(min_cost * task_count, 4)
    total_high = round(max_cost * task_count, 4)

    return EstimateResult(
        model=model,
        task_count=task_count,
        cost_low=total_low,
        cost_high=total_high,
        cost_avg=total_avg,
        historical_runs=len(runs),
        confidence=_confidence_from_count(len(runs)),
    )


def _estimate_from_all_runs(
    runs: list[LedgerRunRecord],
    model: str,
    task_count: int,
) -> EstimateResult:
    """Estimate cost from all successful runs (cross-model fallback).

    Uses overall run costs normalised by task count, then adjusts for the
    target model using the pricing table ratio when available.

    Args:
        runs: All successful runs.
        model: Target model identifier.
        task_count: Number of tasks to estimate for.

    Returns:
        An :class:`EstimateResult` based on cross-model history.
    """
    costs_per_task: list[float] = []
    for r in runs:
        tasks_in_run = r.task_count
        if tasks_in_run > 0 and r.total_cost_estimate > 0:
            costs_per_task.append(r.total_cost_estimate / tasks_in_run)

    if not costs_per_task:
        return _estimate_from_pricing_table(model, task_count)

    avg_cost = statistics.mean(costs_per_task)
    min_cost = min(costs_per_task)
    max_cost = max(costs_per_task)

    total_avg = round(avg_cost * task_count, 4)
    total_low = round(min_cost * task_count, 4)
    total_high = round(max_cost * task_count, 4)

    return EstimateResult(
        model=model,
        task_count=task_count,
        cost_low=total_low,
        cost_high=total_high,
        cost_avg=total_avg,
        historical_runs=len(runs),
        confidence=_confidence_from_count(len(runs)),
    )


def _estimate_from_pricing_table(
    model: str,
    task_count: int,
) -> EstimateResult:
    """Estimate cost using the built-in pricing table.

    Assumes 50,000 tokens per task as a rough heuristic when no historical
    data is available.  This is a placeholder estimate — actual costs may
    vary significantly based on the project and task complexity.

    Args:
        model: Model identifier.
        task_count: Number of tasks to estimate for.

    Returns:
        An :class:`EstimateResult` based on pricing table heuristics.
    """
    # Rough assumption: 50K tokens per task (25K input + 25K output)
    tokens_per_task = 50_000

    single_task_cost = estimate_cost(tokens_per_task, model)

    # If model is unknown, we can't estimate — return zeros
    if single_task_cost == 0.0:
        return EstimateResult(
            model=model,
            task_count=task_count,
            cost_low=0.0,
            cost_high=0.0,
            cost_avg=0.0,
            historical_runs=0,
            confidence="low",
        )

    # Wide confidence interval for pricing-table-only estimates
    total_avg = round(single_task_cost * task_count, 4)
    total_low = round(total_avg * 0.5, 4)
    total_high = round(total_avg * 1.5, 4)

    return EstimateResult(
        model=model,
        task_count=task_count,
        cost_low=total_low,
        cost_high=total_high,
        cost_avg=total_avg,
        historical_runs=0,
        confidence="low",
    )


def get_model_avg_cost_per_task(
    ledger: TokenLedger,
    model: str,
) -> float:
    """Return the average cost per task for *model* from historical data.

    Considers only successful runs that used *model*.  Returns ``0.0`` when
    no relevant data exists.

    Args:
        ledger: The token ledger.
        model: Model identifier.

    Returns:
        Average cost per task in USD, or ``0.0`` if no data.
    """
    model_runs = _runs_for_model(ledger, model)
    if not model_runs:
        return 0.0

    costs_per_task: list[float] = []
    normalised = _normalise_model(model)
    for r in model_runs:
        model_cost = 0.0
        for mb in r.model_breakdown:
            if _normalise_model(mb.model) == normalised:
                model_cost = mb.cost_estimate
                break
        tasks_in_run = r.task_count
        if tasks_in_run > 0 and model_cost > 0:
            costs_per_task.append(model_cost / tasks_in_run)

    if not costs_per_task:
        return 0.0

    return round(statistics.mean(costs_per_task), 6)


def get_task_count_stats(ledger: TokenLedger) -> TaskCountStats:
    """Return task count statistics from successful historical runs.

    Computes min, max, and average task counts across all successful runs.
    Returns zeroed stats when no successful runs exist.

    Args:
        ledger: The token ledger.

    Returns:
        A :class:`TaskCountStats` instance with min/max/avg task counts.
    """
    successful = _successful_runs(ledger)
    if not successful:
        return TaskCountStats()

    task_counts = [r.task_count for r in successful if r.task_count > 0]
    if not task_counts:
        return TaskCountStats()

    return TaskCountStats(
        min_tasks=min(task_counts),
        max_tasks=max(task_counts),
        avg_tasks=round(statistics.mean(task_counts), 2),
        run_count=len(task_counts),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _confidence_from_count(run_count: int) -> Literal["high", "medium", "low"]:
    """Derive a confidence label from the number of historical runs.

    Args:
        run_count: Number of historical data points.

    Returns:
        ``"high"`` if >= 5 runs, ``"medium"`` if 2-4, ``"low"`` otherwise.
    """
    if run_count >= 5:
        return "high"
    if run_count >= 2:
        return "medium"
    return "low"
