"""Textual task dependencies inspector screen."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Static

from the_architect.config import load_config
from the_architect.core.progress import task_status
from the_architect.core.tasks import (
    detect_dependency_cycles,
    detect_missing_dependencies,
    discover_tasks,
)


class DepsApp(App[None]):
    """Task dependencies screen — forward and reverse dependency graph with status."""

    CSS = """
    Screen { background: $surface; }
    #deps_body { height: 1fr; padding: 1 2; }
    #deps_title { color: $accent; text-style: bold; }
    DataTable { border: round $panel; }
    #deps_summary { color: $text-muted; padding: 1 0 0 0; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, project: Path) -> None:
        """Initialise the dependencies screen.

        Args:
            project: Path to the project root directory.
        """
        super().__init__()
        self._project = project

    def compose(self) -> ComposeResult:
        """Compose the screen layout."""
        yield Header()
        with Vertical(id="deps_body"):
            yield Static(
                f"Task Dependencies  —  {self._project}",
                id="deps_title",
            )
            with VerticalScroll():
                table: DataTable[str] = DataTable(zebra_stripes=True)
                table.add_columns(
                    "Task", "Title", "Priority", "Depends On", "Depended By", "Status"
                )
                yield table
            yield Static("", id="deps_summary")
        yield Footer()

    def on_mount(self) -> None:
        """Apply theme and load initial data."""
        from the_architect.tui.app import apply_architect_theme

        apply_architect_theme(self)
        self._refresh()

    def action_refresh(self) -> None:
        """Refresh the dependency table from disk."""
        self._refresh()

    def _refresh(self) -> None:
        """Reload task data and repopulate the DataTable and summary."""
        config = load_config(self._project)
        tasks_dir = self._project / config.tasks_dir.name
        progress_file = config.progress_file

        all_tasks = discover_tasks(tasks_dir) if tasks_dir.exists() else []

        table = self.query_one(DataTable)
        summary_widget = self.query_one("#deps_summary", Static)

        if not all_tasks:
            table.clear()
            summary_widget.update("[dim]No tasks found.[/dim]")
            return

        # Build reverse dependency map: prefix -> list of prefixes that depend on it
        depended_by: dict[str, list[str]] = {}
        for task in all_tasks:
            for dep in task.depends_on:
                depended_by.setdefault(dep, []).append(task.prefix)

        table.clear()
        for task in sorted(all_tasks, key=lambda t: t.prefix):
            # Priority cell — same colouring as CLI _format_priority_cell()
            priority_cell = _format_priority_cell(task.priority)

            # Forward deps: tasks this task depends on
            if task.depends_on:
                deps_cell = "\u2192 " + ", ".join(task.depends_on)
            else:
                deps_cell = "\u2014"

            # Reverse deps: tasks that depend on this task
            dependents = depended_by.get(task.prefix, [])
            if dependents:
                dependents_cell = "\u2190 " + ", ".join(dependents)
            else:
                dependents_cell = "\u2014"

            # Status from PROGRESS.md
            status_cell = _format_status_cell(task_status(progress_file, task.prefix))

            table.add_row(
                task.prefix,
                task.title or task.name,
                priority_cell,
                deps_cell,
                dependents_cell,
                status_cell,
            )

        # Build summary
        total = len(all_tasks)
        tasks_with_deps = sum(1 for t in all_tasks if t.depends_on)
        lines: list[str] = [
            f"Total tasks: {total}",
            f"Tasks with dependencies: {tasks_with_deps}",
        ]

        cycles = detect_dependency_cycles(all_tasks)
        if cycles:
            cycle_strs = [" \u2192 ".join(c) for c in cycles]
            lines.append(f"[red]\u26a0 Dependency cycles detected ({len(cycles)}):[/red]")
            for cs in cycle_strs:
                lines.append(f"  [red]{cs}[/red]")

        missing = detect_missing_dependencies(all_tasks)
        if missing:
            lines.append(
                f"[yellow]\u26a0 Missing dependencies detected ({len(missing)} task(s)):[/yellow]"
            )
            for prefix, missed in sorted(missing.items()):
                lines.append(
                    f"  [yellow]{prefix} depends on non-existent: {', '.join(missed)}[/yellow]"
                )

        summary_widget.update("\n".join(lines))


def _format_priority_cell(priority: str | None) -> str:
    """Return a Rich markup string for a task priority value.

    Uses the same colour scheme as the CLI ``_format_priority_cell()``:
    critical is red, high is yellow, medium/low are dim.  A ``None``
    priority defaults to medium.

    Args:
        priority: The priority string or ``None`` (defaults to medium).

    Returns:
        A Rich markup string with colour-coded priority indicator.
    """
    effective = priority if priority else "medium"
    if effective == "critical":
        return "[red]\u25cf Critical[/red]"
    if effective == "high":
        return "[yellow]\u25cf High[/yellow]"
    if effective == "medium":
        return "[dim]\u25cb Medium[/dim]"
    # low
    return "[dim]\u25cb Low[/dim]"


def _format_status_cell(status: str | None) -> str:
    """Return a Rich markup string for a task status value.

    Uses the same colour scheme as the CLI deps command: Done is green,
    Failed is red, Blocked is yellow, Skipped is magenta, and everything
    else (including ``None``) is dim pending.

    Args:
        status: The status string from PROGRESS.md or ``None``.

    Returns:
        A Rich markup string with colour-coded status indicator.
    """
    if status == "Done":
        return "[#7cc800]\u2713 Done[/#7cc800]"
    if status == "Failed":
        return "[red]\u2717 Failed[/red]"
    if status == "Blocked":
        return "[yellow]\u23f8 Blocked[/yellow]"
    if status and status.startswith("Skipped"):
        return "[magenta]\u2298 Skipped[/magenta]"
    # Pending or None
    return "[dim]\u25cb Pending[/dim]"


def run_deps_screen(project: Path) -> None:
    """Launch the Textual task dependencies inspector.

    Args:
        project: Path to the project root directory.
    """
    DepsApp(project=project).run()
