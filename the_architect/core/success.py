"""Run summary writer — writes tasks/SUMMARY.md and prints terminal summary.

Called after all tasks complete.  Mirrors the bash runner's final log block.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field

from the_architect.core.runner import TaskResult, TokenUsage

# ---------------------------------------------------------------------------
# Retrospective summary model
# ---------------------------------------------------------------------------


class RetrospectiveRound(BaseModel):
    """Summary of a single retrospective review round."""

    round_number: int = Field(description="Which retrospective round (1-based)")
    issues_found: int = Field(default=0, description="Number of issues identified")
    fixes_planned: int = Field(default=0, description="Number of fix-up tasks created")
    tasks_created: list[str] = Field(
        default_factory=list, description="R-prefixed task names created"
    )
    duration_seconds: float = Field(
        default=0.0, description="Wall-clock duration of the review in seconds"
    )
    validation_passed: bool | None = Field(
        default=None,
        description="Whether the deterministic validation gate passed after this round",
    )
    validation_reason: str = Field(
        default="",
        description="Validation failure reason, if the gate failed after this round",
    )
    unresolved_tasks: list[str] = Field(
        default_factory=list,
        description="Unresolved task details reported by validation",
    )


# ---------------------------------------------------------------------------
# Formatting helpers (shared with terminal output and tasks/SUMMARY.md)
# ---------------------------------------------------------------------------


def _fmt_duration(seconds: float) -> str:
    """Format seconds as M:SS or H:MM:SS.

    Args:
        seconds: Duration in seconds.

    Returns:
        Formatted duration string.
    """
    if seconds < 0:
        return "—"
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _fmt_tokens(count: int) -> str:
    """Format token count as K (thousands) or raw number.

    Args:
        count: Number of tokens.

    Returns:
        Formatted token string like ``"12.3K"`` or ``"842"``.
    """
    if count >= 1000:
        return f"{count / 1000:.1f}K"
    return str(count)


def _fmt_cost(dollars: float) -> str:
    """Format a USD cost value for display.

    Args:
        dollars: Cost amount in USD.

    Returns:
        Formatted string like ``"$0.00"`` or ``"$12.34"``.
    """
    return f"${dollars:.2f}"


def _fmt_model(model: str) -> str:
    """Format model name for display — shorten long provider prefixes.

    Args:
        model: Full model identifier (e.g. ``"anthropic/claude-sonnet-4"``).

    Returns:
        Shortened model name for table display.
    """
    if not model:
        return "—"
    # Strip common provider prefixes for readability
    for prefix in ("anthropic/", "openai/", "openrouter/"):
        if model.startswith(prefix):
            return model[len(prefix) :]
    return model


# ---------------------------------------------------------------------------
# SUMMARY.md writer
# ---------------------------------------------------------------------------


SUMMARY_FILE_NAME = "SUMMARY.md"


def write_success_md(
    project_dir: Path,
    results: list[TaskResult],
    total_duration: float,
    total_tokens: TokenUsage,
    retrospective_rounds: list[RetrospectiveRound] | None = None,
    original_goal: str = "",
) -> Path:
    """Write tasks/SUMMARY.md summarising the completed run.

    Args:
        project_dir: The project root directory.
        results: Per-task results.
        total_duration: Total wall-clock duration in seconds.
        total_tokens: Cumulative token usage across all tasks.
        retrospective_rounds: Optional list of retrospective round summaries.
        original_goal: Original user goal for this task package.

    Returns:
        Path to the written SUMMARY.md file.
    """
    done_count = sum(1 for r in results if r.status == "done")
    failed_count = sum(1 for r in results if r.status == "failed")
    total_count = len(results)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    lines: list[str] = []
    lines.append("# The Architect — Run Summary")
    lines.append("")
    lines.append(f"**Date:** {timestamp}")
    lines.append(f"**Duration:** {_fmt_duration(total_duration)}")
    lines.append(
        f"**Result:** "
        f"{'✓ All tasks completed' if failed_count == 0 else f'✗ {failed_count} task(s) failed'}"
    )
    lines.append("")

    if original_goal:
        lines.append("## Goal")
        lines.append("")
        lines.append(original_goal.strip())
        lines.append("")

    # Task table — includes attempts and model columns
    lines.append("## Tasks")
    lines.append("")
    lines.append("| Task | Title | Status | Attempts | Model | Duration | Tokens |")
    lines.append("|------|-------|--------|----------|-------|----------|--------|")

    for r in results:
        status_str = (
            "✓ Done"
            if r.status == "done"
            else ("✗ Failed" if r.status == "failed" else "○ Skipped")
        )
        attempts_str = str(r.attempts) if r.attempts > 0 else "—"
        model_str = _fmt_model(r.model)
        duration_str = _fmt_duration(r.duration_seconds) if r.duration_seconds > 0 else "—"
        tokens_str = _fmt_tokens(r.tokens.total) if r.tokens.total > 0 else "—"
        lines.append(
            f"| {r.prefix} | {r.title} | {status_str} | {attempts_str} | "
            f"{model_str} | {duration_str} | {tokens_str} |"
        )

    lines.append("")

    # Totals
    lines.append("## Totals")
    lines.append("")
    lines.append(f"- **Tasks:** {done_count}/{total_count} done")
    lines.append(f"- **Duration:** {_fmt_duration(total_duration)}")
    lines.append(f"- **Total tokens:** {_fmt_tokens(total_tokens.total)}")

    if total_tokens.total > 0:
        parts = [f"input {_fmt_tokens(total_tokens.input_tokens)}"]
        parts.append(f"output {_fmt_tokens(total_tokens.output_tokens)}")
        if total_tokens.cache_read_tokens > 0:
            parts.append(f"cache read {_fmt_tokens(total_tokens.cache_read_tokens)}")
        if total_tokens.cache_write_tokens > 0:
            parts.append(f"cache write {_fmt_tokens(total_tokens.cache_write_tokens)}")
        lines.append(f"- **Token breakdown:** {' · '.join(parts)}")

    # Models used
    models = sorted({r.model for r in results if r.model})
    if models:
        lines.append(f"- **Models:** {', '.join(models)}")

    # Retry count
    total_attempts = sum(r.attempts for r in results)
    total_retries = total_attempts - len(results)
    if total_retries > 0:
        lines.append(f"- **Retries:** {total_retries} across {len(results)} tasks")

    # Rate limit hits
    rate_limit_hits = sum(1 for r in results if r.rate_limit_hit)
    if rate_limit_hits > 0:
        hit_tasks = [r.prefix for r in results if r.rate_limit_hit]
        lines.append(f"- **Rate limits hit:** {rate_limit_hits} ({', '.join(hit_tasks)})")

    lines.append("")

    # Retrospective section
    if retrospective_rounds:
        lines.append("## Retrospective")
        lines.append("")
        lines.append("| Round | Issues Found | Fix-up Tasks | Validation | Duration |")
        lines.append("|-------|-------------|-------------|------------|----------|")
        for rr in retrospective_rounds:
            tasks_str = ", ".join(rr.tasks_created) if rr.tasks_created else "—"
            dur_str = _fmt_duration(rr.duration_seconds) if rr.duration_seconds > 0 else "—"
            if rr.validation_passed is None:
                validation_str = "—"
            elif rr.validation_passed:
                validation_str = "✓ Passed"
            else:
                validation_str = f"✗ {rr.validation_reason or 'Failed'}"
            lines.append(
                f"| {rr.round_number} | {rr.issues_found} | {tasks_str} | "
                f"{validation_str} | {dur_str} |"
            )

        failed_validation_rounds = [
            rr for rr in retrospective_rounds if rr.validation_passed is False
        ]
        if failed_validation_rounds:
            lines.append("")
            lines.append("### Validation Details")
            lines.append("")
            for rr in failed_validation_rounds:
                lines.append(f"- **Round {rr.round_number}:** {rr.validation_reason}")
                for task in rr.unresolved_tasks:
                    lines.append(f"  - {task}")
        lines.append("")

    # Insights
    timed = [r for r in results if r.status == "done" and r.duration_seconds > 0]
    done_tasks = [r for r in results if r.status == "done"]

    if timed or done_tasks:
        lines.append("## Insights")
        lines.append("")

        if timed:
            avg_dur = sum(r.duration_seconds for r in timed) / len(timed)
            lines.append(f"- **Avg duration per task:** {_fmt_duration(avg_dur)}")
            slowest = max(timed, key=lambda r: r.duration_seconds)
            lines.append(
                f"- **Slowest task:** {slowest.prefix} {slowest.title} "
                f"({_fmt_duration(slowest.duration_seconds)})"
            )

        if done_tasks and total_tokens.total > 0:
            avg_tokens = total_tokens.total / len(done_tasks)
            lines.append(f"- **Avg tokens per task:** {_fmt_tokens(int(avg_tokens))}")

        if total_duration > 0 and total_tokens.total > 0:
            tpm = total_tokens.total / (total_duration / 60)
            lines.append(f"- **Throughput:** {_fmt_tokens(int(tpm))} tokens/min")

        if done_tasks:
            heaviest = max(done_tasks, key=lambda r: r.tokens.total)
            if heaviest.tokens.total > 0:
                lines.append(
                    f"- **Most tokens:** {heaviest.prefix} {heaviest.title} "
                    f"({_fmt_tokens(heaviest.tokens.total)})"
                )

        # Most retries insight
        if done_tasks:
            most_retries = max(done_tasks, key=lambda r: r.attempts)
            if most_retries.attempts > 1:
                lines.append(
                    f"- **Most retries:** {most_retries.prefix} {most_retries.title} "
                    f"({most_retries.attempts} attempts)"
                )

        lines.append("")

    tasks_dir = project_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    out_path = tasks_dir / SUMMARY_FILE_NAME
    out_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Written run summary: {out_path}")
    return out_path


def write_summary_md(
    project_dir: Path,
    results: list[TaskResult],
    total_duration: float,
    total_tokens: TokenUsage,
    retrospective_rounds: list[RetrospectiveRound] | None = None,
    original_goal: str = "",
) -> Path:
    """Write tasks/SUMMARY.md summarising the completed run."""
    return write_success_md(
        project_dir,
        results,
        total_duration,
        total_tokens,
        retrospective_rounds=retrospective_rounds,
        original_goal=original_goal,
    )


# ---------------------------------------------------------------------------
# Terminal summary printer
# ---------------------------------------------------------------------------


def print_success_summary(
    results: list[TaskResult],
    total_duration: float,
    total_tokens: TokenUsage,
    success_md_path: Path | None = None,
    retrospective_rounds: list[RetrospectiveRound] | None = None,
) -> None:
    """Print a clean terminal summary after all tasks complete.

    Uses Rich for colour output.

    Args:
        results: Per-task results.
        total_duration: Total wall-clock duration in seconds.
        total_tokens: Cumulative token usage across all tasks.
        success_md_path: Path to the written SUMMARY.md (shown in footer).
        retrospective_rounds: Optional list of retrospective round summaries.
    """
    from rich import box
    from rich.console import Console
    from rich.table import Table

    console = Console()
    done_count = sum(1 for r in results if r.status == "done")
    failed_count = sum(1 for r in results if r.status == "failed")
    total_count = len(results)

    console.print()

    # Header
    if failed_count == 0:
        console.print(f"[bold #7cc800]✓ All {done_count} tasks completed[/bold #7cc800]")
    else:
        console.print(
            f"[bold red]✗ {failed_count} task(s) failed[/bold red]  "
            f"[dim]{done_count}/{total_count} done[/dim]"
        )

    # Build summary line with retries and rate limits
    summary_parts: list[str] = [f"Duration {_fmt_duration(total_duration)}"]
    if total_tokens.total > 0:
        summary_parts.append(f"{_fmt_tokens(total_tokens.total)} tokens")

    total_attempts = sum(r.attempts for r in results)
    total_retries = total_attempts - len(results)
    if total_retries > 0:
        summary_parts.append(f"{total_retries} retries")

    rate_limit_hits = sum(1 for r in results if r.rate_limit_hit)
    if rate_limit_hits > 0:
        summary_parts.append(f"{rate_limit_hits} rate-limited")

    console.print(f"[dim]{'  ·  '.join(summary_parts)}[/dim]")
    console.print()

    # Task table — includes attempts and model columns
    table = Table(box=box.SIMPLE, show_header=True, header_style="dim", padding=(0, 1))
    table.add_column("Task", style="dim", width=6)
    table.add_column("Title", no_wrap=False)
    table.add_column("Status", width=10)
    table.add_column("Attempts", justify="right", width=5)
    table.add_column("Model", width=18)
    table.add_column("Time", justify="right", width=6)
    table.add_column("Tokens", justify="right", width=8)

    for r in results:
        if r.status == "done":
            status_str = "[#7cc800]✓ Done[/#7cc800]"
        elif r.status == "failed":
            status_str = "[red]✗ Failed[/red]"
        else:
            status_str = "[dim]○ Skip[/dim]"

        duration_str = _fmt_duration(r.duration_seconds) if r.duration_seconds > 0 else "—"
        tokens_str = _fmt_tokens(r.tokens.total) if r.tokens.total > 0 else "—"
        attempts_str = str(r.attempts) if r.attempts > 1 else "[dim]1[/dim]"
        model_str = _fmt_model(r.model)

        # Highlight retries in yellow
        if r.attempts > 1:
            attempts_str = f"[yellow]{r.attempts}[/yellow]"

        # Highlight rate-limited tasks
        if r.rate_limit_hit:
            status_str += " [dim yellow]⚡[/dim yellow]"

        table.add_row(
            r.prefix,
            r.title,
            status_str,
            attempts_str,
            f"[dim]{model_str}[/dim]",
            f"[dim]{duration_str}[/dim]",
            f"[dim]{tokens_str}[/dim]",
        )

    console.print(table)

    # Retrospective summary
    if retrospective_rounds:
        console.print()
        for rr in retrospective_rounds:
            if rr.issues_found == 0:
                console.print(f"[dim]  Retrospective {rr.round_number}: no issues found[/dim]")
            else:
                tasks_str = ", ".join(rr.tasks_created) if rr.tasks_created else "—"
                console.print(
                    f"[yellow]  Retrospective {rr.round_number}: "
                    f"{rr.issues_found} issue(s) → {tasks_str}[/yellow]"
                )

    # Totals line
    count_str = f"{done_count}/{total_count} done"
    if failed_count:
        count_str += f", [red]{failed_count} failed[/red]"
    console.print(
        f"[bold]TOTAL[/bold]  {count_str}  [dim]·  {_fmt_duration(total_duration)}"
        + (f"  ·  {_fmt_tokens(total_tokens.total)} tokens" if total_tokens.total > 0 else "")
        + (f"  ·  {total_retries} retries" if total_retries > 0 else "")
        + "[/dim]"
    )

    if success_md_path:
        console.print()
        console.print(f"[dim]Summary written to {success_md_path}[/dim]")

    console.print()


# ---------------------------------------------------------------------------
# Notification hook — fires after run completion
# ---------------------------------------------------------------------------


def notify_run_completion(
    notify_on_complete: bool,
    notify_on_fail: bool,
    results: list[TaskResult],
    total_duration: float,
) -> None:
    """Send desktop notification and terminal bell after run completion.

    Determines whether the run succeeded or failed, then fires the
    appropriate notification if the corresponding config flag is True.
    Both desktop notification and terminal bell are triggered together.

    Errors from the notification layer are suppressed — a missing desktop
    notification tool or unsupported terminal will not affect the run.

    Args:
        notify_on_complete: Whether to notify on successful completion.
        notify_on_fail: Whether to notify on failed runs.
        results: Per-task results from the run.
        total_duration: Total wall-clock duration in seconds.
    """
    from the_architect.core.notifications import ring_terminal_bell, send_desktop_notification

    done_count = sum(1 for r in results if r.status == "done")
    failed_count = sum(1 for r in results if r.status == "failed")
    total_count = len(results)
    duration_str = _fmt_duration(total_duration)

    if failed_count == 0:
        # All tasks completed successfully
        if not notify_on_complete:
            return
        title = "The Architect — Run Complete"
        body = f"{done_count}/{total_count} tasks done in {duration_str}"
    else:
        # Some tasks failed
        if not notify_on_fail:
            return
        title = "The Architect — Run Failed"
        body = f"{done_count}/{total_count} done, {failed_count} failed. Duration: {duration_str}"

    send_desktop_notification(title, body)
    ring_terminal_bell()
