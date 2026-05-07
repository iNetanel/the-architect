"""Textual read-only task list screen.

Mirrors ``architect list`` output — one row per task with prefix,
title, and status (Done / Failed / Blocked / Pending). Data is
collected once on mount; press ``r`` to refresh.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Static

from the_architect.config import load_config
from the_architect.core.progress import task_status
from the_architect.core.tasks import discover_tasks


class ListApp(App[None]):
    """Task list screen — one row per task with status."""

    CSS = """
    Screen { background: $surface; }
    #list_body { height: 1fr; padding: 1 2; }
    #list_title { color: $accent; text-style: bold; }
    #list_summary { color: $text-muted; }
    DataTable { border: round $panel; }
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

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="list_body"):
            yield Static(f"Tasks  —  {self._project}", id="list_title")
            yield Static("", id="list_summary")
            with VerticalScroll():
                table: DataTable[str] = DataTable(zebra_stripes=True)
                table.add_columns("Task", "Title", "Status")
                yield table
        yield Footer()

    def on_mount(self) -> None:
        from the_architect.tui.app import apply_architect_theme

        apply_architect_theme(self)
        self._refresh_table()

    def action_refresh(self) -> None:
        self._refresh_table()

    def _refresh_table(self) -> None:
        config = load_config(self._project)
        tasks_dir = self._project / config.tasks_dir.name
        progress_file = config.progress_file

        table = self.query_one(DataTable)
        table.clear()

        if not tasks_dir.exists():
            self.query_one("#list_summary", Static).update("No tasks directory found.")
            return

        tasks = discover_tasks(tasks_dir)
        if not tasks:
            self.query_one("#list_summary", Static).update("No tasks found.")
            return

        done = 0
        for task in tasks:
            status = task_status(progress_file, task.prefix) or "Pending"
            if status == "Done":
                done += 1
            table.add_row(task.prefix, task.title or task.name, status)

        self.query_one("#list_summary", Static).update(f"{done}/{len(tasks)} tasks complete")


def run_list_screen(project: Path) -> None:
    """Launch the read-only task list TUI."""
    ListApp(project=project).run()
