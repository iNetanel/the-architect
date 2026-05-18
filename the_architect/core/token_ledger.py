"""Cross-run token & cost ledger for The Architect.

Persistently tracks token usage and estimated costs across all Architect runs
for a single project.  The ledger lives at ``.architect/token_ledger.json``
and is an append-only JSON array of :class:`LedgerRunRecord` objects.

Writes are atomic (temp file + rename) so the file is never partially written.
All operations are best-effort — a ledger write failure must never crash a run.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field

from the_architect.core.fileutil import safe_atomic_write_json
from the_architect.core.runner import TaskResult, TokenUsage

# ---------------------------------------------------------------------------
# Ledger file location
# ---------------------------------------------------------------------------

LEDGER_FILE = Path(".architect/token_ledger.json")


# ---------------------------------------------------------------------------
# Model pricing table
# ---------------------------------------------------------------------------
# Approximate per-1M-token pricing as of early 2026.  Values are rounded and
# may not reflect current rates — they are estimates for visibility, not
# billing.  Cache tokens are priced at the same rate as input tokens (a safe
# over-estimate since most providers discount cache reads).
#
# Format: model_name -> (input_per_1m, output_per_1m)  in USD

MODEL_PRICING: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "o3": (10.00, 40.00),
    "o3-mini": (1.10, 4.40),
    "o4-mini": (1.10, 4.40),
    # Anthropic
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-sonnet-3-5": (3.00, 15.00),
    "claude-opus-4": (15.00, 75.00),
    "claude-opus-3-5": (15.00, 75.00),
    "claude-opus-3": (15.00, 75.00),
    "claude-haiku-3-5": (0.80, 4.00),
    "claude-haiku-3": (0.80, 4.00),
    # Google
    "gemini-2-5-pro": (1.25, 10.00),
    "gemini-2-5-flash": (0.15, 0.60),
    "gemini-2-0-flash": (0.10, 0.40),
    "gemini-2-0-flash-lite": (0.075, 0.30),
    "gemini-2-0-flash-thinking": (0.15, 0.60),
    "gemini-2-0-pro": (1.25, 10.00),
    "gemini-1-5-pro": (1.25, 5.00),
    "gemini-1-5-flash": (0.075, 0.30),
    # OpenRouter aliases (common routing targets)
    "anthropic/claude-sonnet-4-5": (3.00, 15.00),
    "anthropic/claude-sonnet-4": (3.00, 15.00),
    "anthropic/claude-sonnet-3-5": (3.00, 15.00),
    "anthropic/claude-opus-4": (15.00, 75.00),
    "anthropic/claude-opus-3-5": (15.00, 75.00),
    "anthropic/claude-haiku-3-5": (0.80, 4.00),
    "openai/gpt-4o": (2.50, 10.00),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "openai/o3": (10.00, 40.00),
    "openai/o3-mini": (1.10, 4.40),
    "openai/o4-mini": (1.10, 4.40),
    "google/gemini-2-5-pro": (1.25, 10.00),
    "google/gemini-2-5-flash": (0.15, 0.60),
    "google/gemini-2-0-flash": (0.10, 0.40),
    "google/gemini-2-0-flash-lite": (0.075, 0.30),
    "google/gemini-1-5-pro": (1.25, 5.00),
    "google/gemini-1-5-flash": (0.075, 0.30),
}


def _normalise_model(model: str) -> str:
    """Return a lower-cased, stripped copy of *model* for pricing lookup."""
    return model.strip().lower()


def estimate_cost(token_count: int, model: str) -> float:
    """Estimate the USD cost for *token_count* tokens on *model*.

    Uses the built-in :data:`MODEL_PRICING` table.  If *model* is not found
    the function returns ``0.0`` (unknown models cost nothing — they are
    logged at debug level).

    Input and output tokens are assumed to be split evenly when only a
    total count is available.  Cache tokens are priced at the input rate.

    Args:
        token_count: Total number of tokens (input + output).
        model: Model identifier string.

    Returns:
        Estimated cost in USD.  ``0.0`` when the model is unknown.
    """
    if token_count <= 0:
        return 0.0

    normalised = _normalise_model(model)
    rates = MODEL_PRICING.get(normalised)
    if rates is None:
        # Try partial match — strip provider prefix and re-lookup
        for key in MODEL_PRICING:
            if key.endswith(normalised) or normalised.endswith(key):
                rates = MODEL_PRICING[key]
                break
        if rates is None:
            logger.debug(f"No pricing data for model: {model!r}")
            return 0.0

    input_rate, output_rate = rates
    # Split tokens evenly between input and output as a rough estimate
    half = token_count / 2
    cost = (half / 1_000_000) * input_rate + (half / 1_000_000) * output_rate
    return round(cost, 6)


def estimate_cost_detailed(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    model: str,
) -> float:
    """Estimate USD cost with a full token breakdown.

    Args:
        input_tokens: Prompt / input tokens.
        output_tokens: Completion / output tokens.
        cache_read_tokens: Tokens read from cache.
        cache_write_tokens: Tokens written to cache.
        model: Model identifier string.

    Returns:
        Estimated cost in USD.  ``0.0`` when the model is unknown.
    """
    normalised = _normalise_model(model)
    rates = MODEL_PRICING.get(normalised)
    if rates is None:
        # Try partial match
        for key in MODEL_PRICING:
            if key.endswith(normalised) or normalised.endswith(key):
                rates = MODEL_PRICING[key]
                break
        if rates is None:
            logger.debug(f"No pricing data for model: {model!r}")
            return 0.0

    input_rate, output_rate = rates
    # Cache tokens are billed at the input rate (safe over-estimate)
    cost = (
        (input_tokens / 1_000_000) * input_rate
        + (output_tokens / 1_000_000) * output_rate
        + (cache_read_tokens / 1_000_000) * input_rate
        + (cache_write_tokens / 1_000_000) * input_rate
    )
    return round(cost, 6)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ModelTokenRecord(BaseModel):
    """Per-model token breakdown within a single run record.

    Attributes:
        model: Model identifier string.
        input_tokens: Prompt / input tokens consumed.
        output_tokens: Completion / output tokens consumed.
        cache_read_tokens: Tokens read from provider cache.
        cache_write_tokens: Tokens written to provider cache.
        cost_estimate: Estimated cost for this model's usage in USD.
    """

    model: str = Field(description="Model identifier string")
    input_tokens: int = Field(default=0, description="Prompt / input tokens")
    output_tokens: int = Field(default=0, description="Completion / output tokens")
    cache_read_tokens: int = Field(default=0, description="Tokens read from cache")
    cache_write_tokens: int = Field(default=0, description="Tokens written to cache")
    cost_estimate: float = Field(default=0.0, description="Estimated cost in USD")


class LedgerTaskRecord(BaseModel):
    """Per-task cost and token record within a single run record.

    One entry per task in the run, capturing the task's individual token
    usage, model, estimated cost, and outcome.

    Attributes:
        task_id: Task prefix identifier (e.g. ``"T01"``).
        title: Human-readable task title.
        status: Task outcome — ``"done"``, ``"failed"``, or ``"skipped"``.
        input_tokens: Prompt / input tokens consumed by this task.
        output_tokens: Completion / output tokens consumed by this task.
        cache_read_tokens: Tokens read from provider cache by this task.
        cache_write_tokens: Tokens written to provider cache by this task.
        model: Model identifier string used by this task.
        cost_estimate: Estimated cost for this task in USD.
        duration_seconds: Wall-clock duration of the task.
    """

    task_id: str = Field(description="Task prefix identifier (e.g. T01)")
    title: str = Field(default="", description="Human-readable task title")
    status: str = Field(description="Task outcome: done, failed, or skipped")
    input_tokens: int = Field(default=0, description="Prompt / input tokens")
    output_tokens: int = Field(default=0, description="Completion / output tokens")
    cache_read_tokens: int = Field(default=0, description="Tokens read from cache")
    cache_write_tokens: int = Field(default=0, description="Tokens written to cache")
    model: str = Field(default="", description="Model identifier string")
    cost_estimate: float = Field(default=0.0, description="Estimated cost in USD")
    duration_seconds: float = Field(default=0.0, description="Wall-clock duration in seconds")


class LedgerRunRecord(BaseModel):
    """A single run-level record in the cross-run token ledger.

    One record is appended after each Architect run (a run = one full
    plan → execute → retrospective cycle).

    Attributes:
        run_id: Unique identifier for this run.
        timestamp: ISO 8601 UTC timestamp when the run completed.
        goal_summary: Truncated goal text (first 200 characters).
        total_tokens: Sum of all task tokens across the run.
        total_cost_estimate: Estimated total cost in USD.
        model_breakdown: Per-model token and cost breakdown.
        task_breakdown: Per-task token, cost, and outcome breakdown.
        task_count: Number of tasks executed in this run.
        outcome: ``"success"`` or ``"failure"``.
        duration_seconds: Wall-clock duration of the run.
    """

    run_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex[:12],
        description="Unique run identifier",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(tz=UTC).isoformat(),
        description="ISO 8601 UTC timestamp",
    )
    goal_summary: str = Field(default="", description="Truncated goal text (first 200 chars)")
    total_tokens: int = Field(default=0, description="Sum of all task tokens")
    total_cost_estimate: float = Field(default=0.0, description="Estimated cost in USD")
    model_breakdown: list[ModelTokenRecord] = Field(
        default_factory=list, description="Per-model token and cost breakdown"
    )
    task_breakdown: list[LedgerTaskRecord] = Field(
        default_factory=list, description="Per-task token, cost, and outcome breakdown"
    )
    task_count: int = Field(default=0, description="Number of tasks in the run")
    outcome: str = Field(default="failure", description="Run outcome: success or failure")
    duration_seconds: float = Field(default=0.0, description="Wall-clock duration in seconds")


class TokenLedger(BaseModel):
    """Append-only collection of :class:`LedgerRunRecord` objects.

    Provides helper methods for loading, saving, and querying the ledger.

    Attributes:
        records: Ordered list of run records (oldest first).
    """

    records: list[LedgerRunRecord] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Aggregation helpers
    # ------------------------------------------------------------------

    def total_tokens_all_runs(self) -> int:
        """Return the sum of ``total_tokens`` across all records."""
        return sum(r.total_tokens for r in self.records)

    def total_cost_all_runs(self) -> float:
        """Return the sum of ``total_cost_estimate`` across all records."""
        return round(sum(r.total_cost_estimate for r in self.records), 6)

    def model_totals(self) -> dict[str, TokenUsage]:
        """Aggregate token counts by model across all records.

        Returns:
            Mapping of model name to cumulative :class:`TokenUsage`.
        """
        totals: dict[str, TokenUsage] = {}
        for record in self.records:
            for mb in record.model_breakdown:
                existing = totals.get(mb.model)
                if existing is None:
                    totals[mb.model] = TokenUsage(
                        input_tokens=mb.input_tokens,
                        output_tokens=mb.output_tokens,
                        cache_read_tokens=mb.cache_read_tokens,
                        cache_write_tokens=mb.cache_write_tokens,
                    )
                else:
                    totals[mb.model] = TokenUsage(
                        input_tokens=existing.input_tokens + mb.input_tokens,
                        output_tokens=existing.output_tokens + mb.output_tokens,
                        cache_read_tokens=existing.cache_read_tokens + mb.cache_read_tokens,
                        cache_write_tokens=existing.cache_write_tokens + mb.cache_write_tokens,
                    )
        return totals

    def filter_by_date(
        self,
        start: str | None = None,
        end: str | None = None,
    ) -> TokenLedger:
        """Return a new ledger containing only records within the date range.

        Args:
            start: Inclusive ISO 8601 start date (e.g. ``"2026-01-01"``).
            end: Exclusive ISO 8601 end date.

        Returns:
            A new :class:`TokenLedger` with matching records.
        """
        filtered = [
            r
            for r in self.records
            if (start is None or r.timestamp >= start) and (end is None or r.timestamp < end)
        ]
        return TokenLedger(records=filtered)

    def filter_by_model(
        self,
        model: str,
    ) -> TokenLedger:
        """Return a new ledger containing only records that used *model*.

        The *model* argument is normalised (lower-cased, stripped) before
        comparison.  A record matches when any entry in its
        ``model_breakdown`` has a model name that equals the normalised
        target after the same normalisation.

        Args:
            model: Model identifier to filter by (e.g. ``"gpt-4o"`` or
                ``"openai/gpt-4o"``).

        Returns:
            A new :class:`TokenLedger` with matching records.
        """
        normalised = _normalise_model(model)
        filtered = [
            r
            for r in self.records
            if any(_normalise_model(mb.model) == normalised for mb in r.model_breakdown)
        ]
        return TokenLedger(records=filtered)


# ---------------------------------------------------------------------------
# Persistence — load / save / append
# ---------------------------------------------------------------------------


def load_ledger(project_dir: Path) -> TokenLedger:
    """Load the token ledger from *project_dir* / ``.architect/token_ledger.json``.

    Returns an empty :class:`TokenLedger` when the file does not exist or
    contains invalid JSON.

    Args:
        project_dir: The project root directory.

    Returns:
        A :class:`TokenLedger` instance (possibly empty).
    """
    ledger_path = project_dir / LEDGER_FILE
    try:
        raw = ledger_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, list):
            records = [LedgerRunRecord.model_validate(item) for item in data]
            return TokenLedger(records=records)
        logger.debug(f"Ledger file has unexpected top-level type: {type(data).__name__}")
        return TokenLedger()
    except FileNotFoundError:
        return TokenLedger()
    except (json.JSONDecodeError, ValueError) as exc:
        logger.debug(f"Ledger file parse error (non-fatal): {exc!r}")
        return TokenLedger()
    except OSError as exc:
        logger.debug(f"Ledger file read error (non-fatal): {exc!r}")
        return TokenLedger()


def save_ledger(project_dir: Path, ledger: TokenLedger) -> None:
    """Persist *ledger* to *project_dir* / ``.architect/token_ledger.json``.

    Uses an atomic write pattern (temp file + rename) so the file is never
    partially written.  On platforms where the destination may be held open
    by a reader (e.g. on Windows), the rename is retried briefly before
    failing.  Errors are logged at debug level and silently swallowed —
    the ledger is optional infrastructure and must never crash a run.

    Args:
        project_dir: The project root directory.
        ledger: The ledger to persist.
    """
    ledger_path = project_dir / LEDGER_FILE
    ok = safe_atomic_write_json(
        ledger_path,
        [r.model_dump() for r in ledger.records],
        prefix=".token_ledger_tmp_",
        log_label="Token ledger",
    )
    if ok:
        logger.debug(f"Token ledger persisted: {len(ledger.records)} record(s)")


def append_run(
    ledger: TokenLedger,
    results: list[TaskResult],
    goal_summary: str,
    duration_seconds: float,
    outcome: str = "success",
) -> None:
    """Create a :class:`LedgerRunRecord` from run results and append it.

    Aggregates token usage per model from *results* and computes cost
    estimates using the built-in pricing table.

    Args:
        ledger: The ledger to append to.
        results: Per-task results from the run.
        goal_summary: Goal text (will be truncated to 200 characters).
        duration_seconds: Wall-clock duration of the run.
        outcome: Run outcome — ``"success"`` or ``"failure"``.
    """
    # Aggregate tokens by model
    model_tokens: dict[str, TokenUsage] = {}
    for r in results:
        model = r.model or "unknown"
        existing = model_tokens.get(model)
        if existing is None:
            model_tokens[model] = TokenUsage(
                input_tokens=r.tokens.input_tokens,
                output_tokens=r.tokens.output_tokens,
                cache_read_tokens=r.tokens.cache_read_tokens,
                cache_write_tokens=r.tokens.cache_write_tokens,
            )
        else:
            model_tokens[model] = TokenUsage(
                input_tokens=existing.input_tokens + r.tokens.input_tokens,
                output_tokens=existing.output_tokens + r.tokens.output_tokens,
                cache_read_tokens=existing.cache_read_tokens + r.tokens.cache_read_tokens,
                cache_write_tokens=existing.cache_write_tokens + r.tokens.cache_write_tokens,
            )

    # Build model breakdown with cost estimates
    breakdown: list[ModelTokenRecord] = []
    total_cost = 0.0
    for model, usage in model_tokens.items():
        cost = estimate_cost_detailed(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            model=model,
        )
        total_cost += cost
        breakdown.append(
            ModelTokenRecord(
                model=model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                cost_estimate=cost,
            )
        )

    total_tokens = sum(r.tokens.total for r in results)

    # Build per-task breakdown
    task_breakdown: list[LedgerTaskRecord] = []
    for r in results:
        task_cost = estimate_cost_detailed(
            input_tokens=r.tokens.input_tokens,
            output_tokens=r.tokens.output_tokens,
            cache_read_tokens=r.tokens.cache_read_tokens,
            cache_write_tokens=r.tokens.cache_write_tokens,
            model=r.model or "unknown",
        )
        task_breakdown.append(
            LedgerTaskRecord(
                task_id=r.prefix,
                title=r.title,
                status=r.status,
                input_tokens=r.tokens.input_tokens,
                output_tokens=r.tokens.output_tokens,
                cache_read_tokens=r.tokens.cache_read_tokens,
                cache_write_tokens=r.tokens.cache_write_tokens,
                model=r.model or "",
                cost_estimate=task_cost,
                duration_seconds=r.duration_seconds,
            )
        )

    record = LedgerRunRecord(
        goal_summary=goal_summary[:200],
        total_tokens=total_tokens,
        total_cost_estimate=round(total_cost, 6),
        model_breakdown=breakdown,
        task_breakdown=task_breakdown,
        task_count=len(results),
        outcome=outcome,
        duration_seconds=duration_seconds,
    )
    ledger.records.append(record)
