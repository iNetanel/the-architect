"""Textual diff viewer screen — per-task baseline change display."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Static

from the_architect.core.baseline import detect_changes, read_baseline


class DiffApp(App[None]):
    """Diff screen — per-task baseline changes with a DataTable."""

    CSS = """
    Screen { background: $surface; }
    #diff_body { height: 1fr; padding: 1 2; }
    #diff_title { color: $accent; text-style: bold; }
    DataTable { border: round $panel; }
    #diff_hint { color: $text-muted; padding: 1 0 0 0; }
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
        with Vertical(id="diff_body"):
            yield Static(
                f"Diff  —  {self._project}",
                id="diff_title",
            )
            with VerticalScroll():
                table: DataTable[str] = DataTable(zebra_stripes=True)
                table.add_columns("Task", "Change", "File")
                yield table
            yield Static(
                "Press 'r' to refresh.",
                id="diff_hint",
            )
        yield Footer()

    def on_mount(self) -> None:
        from the_architect.tui.app import apply_architect_theme

        apply_architect_theme(self)
        self._refresh()

    def action_refresh(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        baselines_dir = self._project / ".architect" / "baselines"

        table = self.query_one(DataTable)
        table.clear()

        if not baselines_dir.is_dir():
            table.add_row(
                "—",
                "No baseline data",
                "Baselines are captured automatically during task execution.",
            )
            return

        json_files = sorted(baselines_dir.glob("*.json"))
        if not json_files:
            table.add_row(
                "—",
                "No baseline data",
                "Baselines are captured automatically during task execution.",
            )
            return

        has_data = False
        for json_file in json_files:
            try:
                baseline = read_baseline(json_file)
            except (OSError, ValueError):
                continue

            try:
                changes = detect_changes(baseline, self._project)
            except OSError:
                continue

            task_prefix = baseline.task_prefix or json_file.stem

            created = sorted(changes.get("created", []))
            modified = sorted(changes.get("modified", []))
            deleted = sorted(changes.get("deleted", []))

            for f in created:
                table.add_row(task_prefix, "Created", f)
                has_data = True
            for f in modified:
                table.add_row(task_prefix, "Modified", f)
                has_data = True
            for f in deleted:
                table.add_row(task_prefix, "Deleted", f)
                has_data = True

        if not has_data:
            table.add_row("—", "No changes", "All baselines match the current workspace.")


def run_diff_screen(project: Path) -> None:
    """Launch the Textual diff viewer."""
    DiffApp(project=project).run()
