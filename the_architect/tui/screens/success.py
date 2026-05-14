"""TUI success (run-complete) screen.

Shown at the end of every TUI run — after all tasks are done and the
tasks/SUMMARY.md has been written.  Mirrors the visual language of
:class:`~the_architect.tui.screens.wait.WaitScreen` (animated Matrix-rain
title, branded green palette, structured summary body) while presenting
the same data that :func:`~the_architect.core.success.print_success_summary`
prints on a plain terminal.

The screen auto-exits when the user presses any of the advertised keys
(``q``, ``Enter``, ``Escape``), passing ``True`` back to the caller so
:meth:`ArchitectApp.push_and_wait` knows a clean exit was requested.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from the_architect.tui.widgets import MatrixRain, next_matrix_frame

if TYPE_CHECKING:
    from the_architect.core.runner import TaskResult, TokenUsage
    from the_architect.core.success import RetrospectiveRound


def _fmt_duration(seconds: float) -> str:
    """Format wall-clock seconds as M:SS or H:MM:SS."""
    if seconds < 0:
        return "—"
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _fmt_tokens(count: int) -> str:
    """Format token count as K or raw."""
    if count >= 1000:
        return f"{count / 1000:.1f}K"
    return str(count)


def _fmt_model(model: str) -> str:
    """Strip common provider prefixes for compact display."""
    if not model:
        return "—"
    for prefix in ("anthropic/", "openai/", "openrouter/anthropic/", "openrouter/"):
        if model.startswith(prefix):
            return model[len(prefix) :]
    return model


class SuccessScreen(Screen[bool]):
    """Animated run-complete summary screen.

    Args:
        results: Per-task :class:`~the_architect.core.runner.TaskResult` list.
        total_duration: Total wall-clock duration in seconds.
        total_tokens: Accumulated :class:`~the_architect.core.runner.TokenUsage`.
        success_md_path: Path to the written SUMMARY.md, or ``None``.
        retrospective_rounds: Optional list of retrospective summaries.
    """

    BINDINGS = [
        Binding("q", "exit_screen", "Exit", show=True),
        Binding("enter", "exit_screen", "Exit", show=True),
        Binding("escape", "exit_screen", "Exit", show=True),
    ]

    DEFAULT_CSS = """
    SuccessScreen {
        layout: vertical;
    }

    #success_body {
        height: 1fr;
        padding: 1 2;
    }

    #success_title {
        color: $accent;
        text-style: bold;
    }

    #success_rain_row {
        width: 100%;
        height: __MATRIX_RAIN_ROWS__;
        align-horizontal: center;
        margin: 1 0 0 0;
    }

    #success_headline {
        padding: 1 0 0 0;
        text-style: bold;
    }

    #success_summary_line {
        color: $text-muted;
        padding: 0 0 1 0;
    }

    #success_task_table {
        padding: 0 0 1 0;
    }

    #success_retro {
        color: $text-muted;
        padding: 0 0 1 0;
    }

    #success_totals {
        color: $text-muted;
    }

    #success_file {
        color: $text-muted;
        padding: 1 0 0 0;
    }

    #success_hint {
        color: $text-muted;
        padding: 1 0 0 0;
    }
    """.replace("__MATRIX_RAIN_ROWS__", str(MatrixRain.ROWS))

    def __init__(
        self,
        results: list[TaskResult],
        total_duration: float,
        total_tokens: TokenUsage,
        success_md_path: str | None = None,
        retrospective_rounds: list[RetrospectiveRound] | None = None,
        session_cost_usd: float = 0.0,
    ) -> None:
        super().__init__()
        self._results = results
        self._total_duration = total_duration
        self._total_tokens = total_tokens
        self._success_md_path = success_md_path
        self._retrospective_rounds = retrospective_rounds or []
        self._frame_index = 0
        self._current_frame = next_matrix_frame(self._frame_index)
        self._session_cost_usd = session_cost_usd
        # If cost was not supplied, try to compute it from results
        if session_cost_usd == 0.0 and results:
            try:
                from the_architect.core.token_ledger import estimate_cost_detailed

                _computed = 0.0
                for r in results:
                    if r.model and r.tokens.total > 0:
                        _computed += estimate_cost_detailed(
                            input_tokens=r.tokens.input_tokens,
                            output_tokens=r.tokens.output_tokens,
                            cache_read_tokens=r.tokens.cache_read_tokens,
                            cache_write_tokens=r.tokens.cache_write_tokens,
                            model=r.model,
                        )
                self._session_cost_usd = _computed
            except Exception:
                self._session_cost_usd = 0.0

    def compose(self) -> ComposeResult:
        """Build the success screen layout."""
        yield Header()
        with Vertical(id="success_body"):
            yield Static(self._render_title(), id="success_title")
            with Horizontal(id="success_rain_row"):
                yield MatrixRain(id="success_rain")
            yield Static(self._render_headline(), id="success_headline")
            yield Static(self._render_summary_line(), id="success_summary_line")
            yield Static(self._render_task_table(), id="success_task_table")
            if self._retrospective_rounds:
                yield Static(self._render_retro(), id="success_retro")
            yield Static(self._render_totals(), id="success_totals")
            if self._success_md_path:
                yield Static(
                    f"[dim]Summary written to {self._success_md_path}[/dim]",
                    id="success_file",
                )
            yield Static("[dim]Press Enter or Q to exit[/dim]", id="success_hint")
        yield Footer()

    def on_mount(self) -> None:
        """Start the spinner and paint the initial frame."""
        self.set_interval(0.1, self._tick_spinner)

    # ── Actions ────────────────────────────────────────────────────────

    def action_exit_screen(self) -> None:
        """Dismiss, returning True to signal a clean exit."""
        self.dismiss(True)

    # ── Internal ──────────────────────────────────────────────────────

    def _tick_spinner(self) -> None:
        """Advance the Matrix-rain frame on the title line."""
        self._frame_index += 1
        self._current_frame = next_matrix_frame(self._frame_index)
        try:
            self.query_one("#success_title", Static).update(self._render_title())
        except Exception:
            pass

    def _render_title(self) -> str:
        return f"{self._current_frame}  Run complete"

    def _render_headline(self) -> str:
        done = sum(1 for r in self._results if r.status == "done")
        failed = sum(1 for r in self._results if r.status == "failed")
        total = len(self._results)
        if failed == 0:
            return f"[bold $accent]✓  All {done} task(s) completed[/bold $accent]"
        return f"[bold red]✗  {failed} task(s) failed[/bold red]  [dim]{done}/{total} done[/dim]"

    def _render_summary_line(self) -> str:
        parts: list[str] = [f"Duration {_fmt_duration(self._total_duration)}"]
        if self._total_tokens.total > 0:
            parts.append(f"{_fmt_tokens(self._total_tokens.total)} tokens")
        total_attempts = sum(r.attempts for r in self._results)
        retries = total_attempts - len(self._results)
        if retries > 0:
            parts.append(f"{retries} retr{'y' if retries == 1 else 'ies'}")
        rl_hits = sum(1 for r in self._results if r.rate_limit_hit)
        if rl_hits > 0:
            parts.append(f"{rl_hits} rate-limited")
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        parts.append(timestamp)
        return "  ·  ".join(parts)

    def _render_task_table(self) -> str:
        """Render a text-art table of per-task results."""
        if not self._results:
            return ""
        col_widths = {
            "prefix": 5,
            "title": 22,
            "status": 10,
            "attempts": 4,
            "model": 20,
            "time": 7,
            "tokens": 7,
        }

        def _pad(s: str, w: int) -> str:
            return s[:w].ljust(w)

        header = (
            f"  {_pad('Task', col_widths['prefix'])}"
            f"  {_pad('Title', col_widths['title'])}"
            f"  {_pad('Status', col_widths['status'])}"
            f"  {_pad('Att', col_widths['attempts'])}"
            f"  {_pad('Model', col_widths['model'])}"
            f"  {_pad('Time', col_widths['time'])}"
            f"  {_pad('Tokens', col_widths['tokens'])}"
        )
        sep = "  " + "-" * (
            col_widths["prefix"]
            + col_widths["title"]
            + col_widths["status"]
            + col_widths["attempts"]
            + col_widths["model"]
            + col_widths["time"]
            + col_widths["tokens"]
            + 12
        )

        rows: list[str] = [f"[dim]{header}[/dim]", f"[dim]{sep}[/dim]"]
        for r in self._results:
            if r.status == "done":
                status_str = "[$accent]✓ Done[/$accent]"
            elif r.status == "failed":
                status_str = "[red]✗ Failed[/red]"
            else:
                status_str = "[dim]○ Skip[/dim]"

            dur = _fmt_duration(r.duration_seconds) if r.duration_seconds > 0 else "—"
            toks = _fmt_tokens(r.tokens.total) if r.tokens.total > 0 else "—"
            atts = (
                f"[yellow]{r.attempts}[/yellow]" if r.attempts > 1 else f"[dim]{r.attempts}[/dim]"
            )
            model = _fmt_model(r.model)

            row = (
                f"  {_pad(r.prefix, col_widths['prefix'])}"
                f"  {_pad(r.title or r.prefix, col_widths['title'])}"
                f"  {status_str}"
                # pad status visually — markup doesn't count for ljust so we
                # add trailing spaces manually
                f"{'':>{max(0, col_widths['status'] - len(r.status))}}"
                f"  {atts}"
                f"  [dim]{_pad(model, col_widths['model'])}[/dim]"
                f"  [dim]{_pad(dur, col_widths['time'])}[/dim]"
                f"  [dim]{_pad(toks, col_widths['tokens'])}[/dim]"
            )
            rows.append(row)

        return "\n".join(rows)

    def _render_retro(self) -> str:
        """Render a one-liner per retrospective round."""
        lines: list[str] = []
        for rr in self._retrospective_rounds:
            if rr.issues_found == 0:
                lines.append(f"[dim]  Retrospective {rr.round_number}: no issues found[/dim]")
            else:
                tasks = ", ".join(rr.tasks_created) if rr.tasks_created else "—"
                lines.append(
                    f"[yellow]  Retrospective {rr.round_number}: "
                    f"{rr.issues_found} issue(s) → {tasks}[/yellow]"
                )
        return "\n".join(lines)

    def _render_totals(self) -> str:
        done = sum(1 for r in self._results if r.status == "done")
        failed = sum(1 for r in self._results if r.status == "failed")
        total = len(self._results)
        count_str = f"{done}/{total} done"
        if failed:
            count_str += f", [red]{failed} failed[/red]"
        total_attempts = sum(r.attempts for r in self._results)
        retries = total_attempts - total

        parts = [f"[dim]{_fmt_duration(self._total_duration)}[/dim]"]
        if self._total_tokens.total > 0:
            parts.append(f"[dim]{_fmt_tokens(self._total_tokens.total)} tokens[/dim]")
        if retries > 0:
            parts.append(f"[dim]{retries} retries[/dim]")
        if self._session_cost_usd > 0:
            parts.append(f"[dim]~${self._session_cost_usd:.4f} est.[/dim]")

        return f"[bold]TOTAL[/bold]  {count_str}  " + "  ·  ".join(parts)
