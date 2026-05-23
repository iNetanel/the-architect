"""Textual cost analytics screen — cross-run spending overview."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Static

from the_architect.core.cost_analytics import aggregate_costs
from the_architect.core.success import _fmt_cost, _fmt_tokens
from the_architect.core.token_ledger import load_ledger


class CostApp(App[None]):
    """Cost analytics screen — cross-run spending overview with multiple tables."""

    CSS = """
    Screen { background: $surface; }
    #cost_body { height: 1fr; padding: 1 2; }
    #cost_title { color: $accent; text-style: bold; }
    DataTable { border: round $panel; }
    .section-heading {
        color: $accent;
        text-style: bold underline;
        padding: 0 0 0 0;
    }
    #cost_hint { color: $text-muted; padding: 1 0 0 0; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, project: Path) -> None:
        """Initialise the cost analytics screen.

        Args:
            project: Path to the project root directory.
        """
        super().__init__()
        self._project = project

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="cost_body"):
            yield Static(
                f"Cost Analytics  —  {self._project}",
                id="cost_title",
            )
            with VerticalScroll():
                # Summary section
                yield Static("", id="summary_section")
                # Model Breakdown table
                yield Static("Model Breakdown", classes="section-heading")
                model_table: DataTable[str] = DataTable(zebra_stripes=True, id="model_table")
                model_table.add_columns("Model", "Tokens", "Cost", "Runs", "Avg Cost/Run")
                yield model_table
                # Top Expensive Tasks table
                yield Static("Top Expensive Tasks", classes="section-heading")
                tasks_table: DataTable[str] = DataTable(zebra_stripes=True, id="tasks_table")
                tasks_table.add_columns("Task", "Title", "Model", "Cost", "Tokens")
                yield tasks_table
                # Daily Spending table
                yield Static("Daily Spending", classes="section-heading")
                daily_table: DataTable[str] = DataTable(zebra_stripes=True, id="daily_table")
                daily_table.add_columns("Date", "Cost", "Tokens", "Runs")
                yield daily_table
            yield Static(
                "'r' to refresh.  q/Escape/Ctrl+C to quit.",
                id="cost_hint",
            )
        yield Footer()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        """Apply theme and load initial data."""
        from the_architect.tui.app import apply_architect_theme

        apply_architect_theme(self)
        self._refresh()

    def action_refresh(self) -> None:
        """Refresh all tables from the token ledger."""
        self._refresh()

    # ------------------------------------------------------------------
    # Data loading and rendering
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        """Reload cost data and repopulate all tables."""
        ledger = load_ledger(self._project)
        analytics = aggregate_costs(ledger)

        summary_widget = self.query_one("#summary_section", Static)
        model_table = self.query_one("#model_table", DataTable)
        tasks_table = self.query_one("#tasks_table", DataTable)
        daily_table = self.query_one("#daily_table", DataTable)

        # --- Summary ---
        if analytics.run_count == 0:
            summary_widget.update("[dim]No cost data available.[/dim]")
            model_table.clear()
            tasks_table.clear()
            daily_table.clear()
            return

        summary_lines = [
            "[bold]Summary[/bold]",
            f"  Total cost:   {_cost_cell(analytics.total_cost)}",
            f"  Total tokens: {_fmt_tokens(analytics.total_tokens)}",
            f"  Runs:         {analytics.run_count}",
        ]
        summary_widget.update("\n".join(summary_lines))

        # --- Model Breakdown ---
        model_table.clear()
        for model_name, model_summary in sorted(
            analytics.model_breakdown.items(),
            key=lambda item: item[1].total_cost,
            reverse=True,
        ):
            model_table.add_row(
                model_name or "(unknown)",
                _fmt_tokens(model_summary.total_tokens),
                _cost_cell(model_summary.total_cost),
                str(model_summary.run_count),
                _cost_cell(model_summary.avg_cost_per_run),
            )

        # --- Top Expensive Tasks ---
        tasks_table.clear()
        for task_entry in analytics.top_expensive_tasks:
            tasks_table.add_row(
                task_entry.task_id,
                task_entry.title or "(untitled)",
                task_entry.model or "(unknown)",
                _cost_cell(task_entry.cost_estimate),
                _fmt_tokens(task_entry.tokens),
            )

        # --- Daily Spending ---
        daily_table.clear()
        for day_entry in analytics.daily_spending:
            daily_table.add_row(
                day_entry.date,
                _cost_cell(day_entry.cost),
                _fmt_tokens(day_entry.tokens),
                str(day_entry.runs),
            )


def _cost_cell(amount: float) -> str:
    """Return a Rich markup string for a cost value with colour coding.

    Green for low (<$1), yellow for medium ($1-$10), red for high (>$10).

    Args:
        amount: Cost in USD.

    Returns:
        A Rich markup string like ``"[green]$0.50[/green]"``.
    """
    formatted = _fmt_cost(amount)
    if amount < 1.0:
        return f"[green]{formatted}[/green]"
    if amount <= 10.0:
        return f"[yellow]{formatted}[/yellow]"
    return f"[red]{formatted}[/red]"


def run_cost_screen(project: Path) -> None:
    """Launch the Textual cost analytics screen.

    Args:
        project: Path to the project root directory.
    """
    CostApp(project=project).run()
