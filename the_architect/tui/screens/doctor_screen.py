"""Textual doctor diagnostics screen.

Standalone TUI screen for project health checks.  Runs ``run_project_checks()``
and renders a DataTable with colour-coded status indicators (green = ok,
yellow = warn, red = fail).
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Static

from the_architect.core.project_health import run_project_checks


class DoctorApp(App[None]):
    """Project health diagnostics screen — colour-coded check results."""

    CSS = """
    Screen { background: $surface; }
    #doctor_body { height: 1fr; padding: 1 2; }
    #doctor_title { color: $accent; text-style: bold; }
    DataTable { border: round $panel; }
    #doctor_hint { color: $text-muted; padding: 1 0 0 0; }
    .section-heading { color: $text-muted; text-style: bold; padding: 1 0; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, project: Path) -> None:
        """Initialise the doctor diagnostics screen.

        Args:
            project: The project root directory to run health checks against.
        """
        super().__init__()
        self._project = project

    # ── Composition ──────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="doctor_body"):
            yield Static(
                f"Project Health  —  {self._project}",
                id="doctor_title",
            )
            yield Static("", id="summary_section", classes="section-heading")
            with VerticalScroll():
                table: DataTable[str] = DataTable(zebra_stripes=True, id="checks_table")
                table.add_columns("Check", "Status", "Detail")
                yield table
            yield Static(
                "Press 'r' to refresh  |  This screen is read-only.",
                id="doctor_hint",
            )
        yield Footer()

    # ── Lifecycle ────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        from the_architect.tui.app import apply_architect_theme

        apply_architect_theme(self)
        self._refresh()

    def action_refresh(self) -> None:
        """Re-run health checks and update the display."""
        self._refresh()

    # ── Data refresh ─────────────────────────────────────────────────────

    def _refresh(self) -> None:
        try:
            checks = run_project_checks(self._project)
        except Exception:
            checks = []

        summary = self.query_one("#summary_section", Static)
        table = self.query_one("#checks_table", DataTable)

        # Count statuses
        ok_count = sum(1 for c in checks if c.status == "ok")
        warn_count = sum(1 for c in checks if c.status == "warn")
        fail_count = sum(1 for c in checks if c.status == "fail")

        if not checks:
            summary.update("No health checks available — project state not found.")
            return

        summary.update(
            f"Checks: {ok_count} ok, {warn_count} warn, {fail_count} fail  (total {len(checks)})"
        )

        table.clear()
        for check in checks:
            if check.status == "ok":
                status_style = "[green]✓ ok[/green]"
            elif check.status == "warn":
                status_style = "[yellow]⚠ warn[/yellow]"
            else:
                status_style = "[red]✗ fail[/red]"
            table.add_row(check.label, status_style, check.detail)


def run_doctor_screen(project: Path) -> None:
    """Launch the Textual doctor diagnostics screen.

    Args:
        project: The project root directory to run health checks against.
    """
    DoctorApp(project=project).run()
