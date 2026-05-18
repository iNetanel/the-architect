"""Textual history screen — past run history from the token ledger."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.events import Key
from textual.widgets import DataTable, Footer, Header, Static

from the_architect.core.success import _fmt_cost, _fmt_duration, _fmt_tokens
from the_architect.core.token_ledger import LedgerRunRecord, load_ledger


class HistoryApp(App[None]):
    """History screen — past run history from the token ledger with task detail view."""

    CSS = """
    Screen { background: $surface; }
    #history_body { height: 1fr; padding: 1 2; }
    #history_title { color: $accent; text-style: bold; }
    DataTable { border: round $panel; }
    #history_hint { color: $text-muted; padding: 1 0 0 0; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, project: Path) -> None:
        super().__init__()
        self._project = project
        self._selected_row: int | None = None
        self._selected_record: LedgerRunRecord | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="history_body"):
            yield Static(
                f"Run History  —  {self._project}",
                id="history_title",
            )
            with VerticalScroll():
                table: DataTable[str] = DataTable(zebra_stripes=True)
                yield table
            yield Static(
                "Press Enter on a row for task details. 'r' to refresh.",
                id="history_hint",
            )
        yield Footer()

    def on_mount(self) -> None:
        from the_architect.tui.app import apply_architect_theme

        apply_architect_theme(self)
        self._refresh()

    def action_refresh(self) -> None:
        self._refresh()

    # --- Override quit/escape to return from task detail view ---

    async def action_quit(self) -> None:
        if self._selected_record is not None:
            self._return_to_run_level()
            return
        self.exit()

    # --- Enter to show task detail ---

    def on_key(self, event: Key) -> None:
        if event.key != "enter":
            return
        event.prevent_default()
        # Auto-select first row if nothing selected yet
        if self._selected_record is None:
            ledger = load_ledger(self._project)
            if ledger.records:
                self._selected_row = 0
                self._selected_record = ledger.records[0]
        if self._selected_record is not None:
            self._show_task_detail()

    def _show_task_detail(self) -> None:
        record = self._selected_record
        assert record is not None  # guarded by caller
        table = self.query_one(DataTable)
        table.clear(columns=True)
        table.add_columns("Task", "Title", "Tokens", "Cost", "Model", "Status", "Duration")

        self.query_one("#history_hint", Static).update(
            "Press Escape or 'q' to return to run list. 'r' to refresh.",
        )

        if not record.task_breakdown:
            table.add_row(
                "—",
                "No task-level data available",
                "—",
                "—",
                "—",
                "—",
                "—",
            )
            return

        for t in record.task_breakdown:
            total_tokens = t.input_tokens + t.output_tokens
            table.add_row(
                t.task_id,
                t.title or "(untitled)",
                _fmt_tokens(total_tokens),
                _fmt_cost(t.cost_estimate),
                t.model or "(unknown)",
                t.status,
                _fmt_duration(t.duration_seconds),
            )

    # --- Return to run-level view ---

    def _return_to_run_level(self) -> None:
        self._selected_row = None
        self._selected_record = None
        self._refresh()

    def _refresh(self) -> None:
        ledger = load_ledger(self._project)

        table = self.query_one(DataTable)
        table.clear()
        table.add_columns("Date", "Goal", "Tasks", "Tokens", "Cost", "Duration", "Outcome")

        self.query_one("#history_hint", Static).update(
            "Press Enter on a row for task details. 'r' to refresh.",
        )

        if not ledger.records:
            table.add_row(
                "—",
                "No run history found",
                "—",
                "—",
                "—",
                "—",
                "—",
            )
            return

        for r in ledger.records:
            # Truncate goal to 40 characters for the TUI table
            goal = r.goal_summary[:40]
            if len(r.goal_summary) > 40:
                goal = goal.rstrip() + "…"

            # Outcome display
            if r.outcome == "success":
                outcome_str = "✓ success"
            else:
                outcome_str = "✗ failure"

            table.add_row(
                r.timestamp[:10] if r.timestamp else "—",
                goal or "(no goal)",
                str(r.task_count),
                _fmt_tokens(r.total_tokens),
                _fmt_cost(r.total_cost_estimate),
                _fmt_duration(r.duration_seconds),
                outcome_str,
            )


def run_history_screen(project: Path) -> None:
    """Launch the Textual history viewer."""
    HistoryApp(project=project).run()
