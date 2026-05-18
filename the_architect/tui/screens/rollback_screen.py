"""Textual rollback confirmation screen.

Displays a rollback plan with file paths, actions, and sizes in a DataTable.
Provides Approve, Cancel, and Dry Run actions via key bindings. Shows the
final result after execution.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.events import Key
from textual.widgets import DataTable, Footer, Header, Static

from the_architect.core.baseline import read_baseline
from the_architect.core.rollback import (
    RollbackPlan,
    RollbackResult,
    compute_rollback_plan,
    execute_rollback,
    list_run_baselines,
)


class RollbackApp(App[None]):
    """Rollback confirmation screen — file listing with approve/cancel actions.

    The screen operates in two modes:

    1. **Task selection mode** — when ``baseline_path`` is ``None``, shows a
       list of available baselines. User selects one via up/down + Enter.
    2. **Plan review mode** — when ``baseline_path`` is provided, shows the
       rollback plan with file actions. User can Approve, Cancel, or Dry Run.

    After approval, the screen switches to a result display showing success
    counts and any errors.
    """

    CSS = """
    Screen { background: $surface; }
    #rollback_body { height: 1fr; padding: 1 2; }
    #rollback_title { color: $accent; text-style: bold; }
    DataTable { border: round $panel; }
    #rollback_summary { color: $text; padding: 1 0 0 0; }
    #rollback_hint { color: $text-muted; padding: 1 0 0 0; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(
        self,
        project: Path,
        baseline_path: Path | None = None,
        task_filter: str | None = None,
    ) -> None:
        """Initialise the rollback screen.

        Args:
            project: The project root directory.
            baseline_path: Optional specific baseline file to use. If ``None``,
                the user selects from available baselines.
            task_filter: Optional task prefix filter (e.g. ``"T01"``).
        """
        super().__init__()
        self._project = project
        self._baseline_path = baseline_path
        self._task_filter = task_filter
        self._plan: RollbackPlan | None = None
        self._result: RollbackResult | None = None
        self._mode: str = "task_select"  # "task_select" | "plan" | "result"

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="rollback_body"):
            yield Static(
                "Rollback  —  Select baseline",
                id="rollback_title",
            )
            with VerticalScroll():
                table: DataTable[str] = DataTable(zebra_stripes=True)
                yield table
            yield Static("", id="rollback_summary")
            yield Static(
                "Use ↑↓ to select. Enter to preview. 'q' to quit.",
                id="rollback_hint",
            )
        yield Footer()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        from the_architect.tui.app import apply_architect_theme

        apply_architect_theme(self)
        self._refresh()

    def action_refresh(self) -> None:
        self._refresh()

    # ------------------------------------------------------------------
    # Task selection mode
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        if self._mode == "result":
            return

        if self._baseline_path is not None:
            self._mode = "plan"
            self._load_plan()
            return

        # Task selection mode
        self._mode = "task_select"
        baselines = list_run_baselines(self._project)

        table = self.query_one(DataTable)
        table.clear(columns=True)
        table.add_columns("Task", "Timestamp", "Files")

        title_widget = self.query_one("#rollback_title", Static)
        hint_widget = self.query_one("#rollback_hint", Static)
        summary_widget = self.query_one("#rollback_summary", Static)

        if self._task_filter:
            # Filter to matching baseline
            matched = [b for b in baselines if b.task_prefix.upper() == self._task_filter.upper()]
            if matched:
                baselines = matched
            else:
                available = ", ".join(b.task_prefix for b in baselines)
                title_widget.update(
                    f"Error: no baseline for '{self._task_filter}'. Available: {available}"
                )
                hint_widget.update("Press 'q' to quit.")
                summary_widget.update("")
                return

        if not baselines:
            table.add_row(
                "—",
                "No baseline data",
                "Baselines are captured automatically during task execution.",
            )
            title_widget.update("Rollback  —  No baselines")
            hint_widget.update("Press 'q' to quit.")
            summary_widget.update("")
            return

        title_widget.update(f"Rollback  —  {self._project}")
        hint_widget.update("Use ↑↓ to select. Enter to preview plan. 'q' to quit.")
        summary_widget.update(f"{len(baselines)} baseline(s) available")

        for info in baselines:
            table.add_row(
                info.task_prefix,
                info.timestamp[:16] if info.timestamp else "—",
                str(info.file_count),
            )

    # ------------------------------------------------------------------
    # Plan review mode
    # ------------------------------------------------------------------

    def _load_plan(self) -> None:
        if self._baseline_path is None:
            return

        try:
            baseline = read_baseline(self._baseline_path)
        except (OSError, ValueError) as exc:
            self._show_error(f"Cannot read baseline: {exc}")
            return

        self._plan = compute_rollback_plan(baseline, self._project)
        self._render_plan()

    def _render_plan(self) -> None:
        plan = self._plan
        if plan is None:
            return

        table = self.query_one(DataTable)
        table.clear(columns=True)
        table.add_columns("File", "Action", "Size")

        title_widget = self.query_one("#rollback_title", Static)
        summary_widget = self.query_one("#rollback_summary", Static)
        hint_widget = self.query_one("#rollback_hint", Static)

        restore_count = len(plan.files_to_restore)
        delete_count = len(plan.files_to_delete)
        unchanged_count = len(plan.files_unchanged)

        title_widget.update(f"Rollback Plan  —  {self._project}")

        for rel_path in sorted(plan.files_to_restore.keys()):
            content = plan.files_to_restore[rel_path]
            size = f"{len(content)} bytes"
            table.add_row(rel_path, "Restore", size)

        for rel_path in plan.files_to_delete:
            table.add_row(rel_path, "Delete", "—")

        if restore_count == 0 and delete_count == 0:
            table.add_row(
                "—",
                "No changes",
                "All files are already at baseline state.",
            )

        summary_widget.update(
            f"Restore: {restore_count}  |  Delete: {delete_count}  |  Unchanged: {unchanged_count}"
        )
        hint_widget.update("'a' Approve  |  'd' Dry Run  |  'c' Cancel  |  'q' Quit")

        # Add action bindings for plan mode
        self._add_action_bindings()

    def _add_action_bindings(self) -> None:
        """Add action bindings for plan review mode."""
        # We add bindings by defining action methods — they always exist
        # but only make sense in plan mode.

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_approve(self) -> None:
        """Execute the rollback plan."""
        if self._plan is None or self._mode != "plan":
            return

        self._mode = "result"
        result = execute_rollback(self._plan, self._project, dry_run=False)
        self._result = result
        self._render_result(result)

    def action_dry_run(self) -> None:
        """Run a dry-run of the rollback plan."""
        if self._plan is None or self._mode != "plan":
            return

        result = execute_rollback(self._plan, self._project, dry_run=True)
        self._render_dry_run_result(result)

    def action_cancel(self) -> None:
        """Cancel the rollback and exit."""
        self.exit()

    def _render_result(self, result: RollbackResult) -> None:
        """Render the post-execution result."""
        table = self.query_one(DataTable)
        table.clear(columns=True)
        table.add_columns("Metric", "Value")

        title_widget = self.query_one("#rollback_title", Static)
        summary_widget = self.query_one("#rollback_summary", Static)
        hint_widget = self.query_one("#rollback_hint", Static)

        title_widget.update("Rollback Complete")

        table.add_row("Restored", str(result.restored_count))
        table.add_row("Deleted", str(result.deleted_count))
        table.add_row("Unchanged", str(result.unchanged_count))

        if result.errors:
            for err in result.errors:
                table.add_row(f"Error: {err.path}", err.message)

        if result.errors:
            summary_widget.update(f"Completed with {len(result.errors)} error(s)")
        else:
            summary_widget.update("Rollback completed successfully")

        hint_widget.update("Press 'q' to quit.")

    def _render_dry_run_result(self, result: RollbackResult) -> None:
        """Render the dry-run result as an informational overlay."""
        summary_widget = self.query_one("#rollback_summary", Static)
        hint_widget = self.query_one("#rollback_hint", Static)

        summary_widget.update(
            f"Dry Run — Would restore: {result.restored_count}, "
            f"delete: {result.deleted_count}, unchanged: {result.unchanged_count}"
        )
        hint_widget.update("'a' Approve  |  'd' Dry Run  |  'c' Cancel  |  'q' Quit")

    def _show_error(self, message: str) -> None:
        """Display an error message."""
        table = self.query_one(DataTable)
        table.clear(columns=True)
        table.add_columns("Error")
        table.add_row(message)

        title_widget = self.query_one("#rollback_title", Static)
        title_widget.update("Rollback Error")

        hint_widget = self.query_one("#rollback_hint", Static)
        hint_widget.update("Press 'q' to quit.")

    # ------------------------------------------------------------------
    # Key handling
    # ------------------------------------------------------------------

    def on_key(self, event: Key) -> None:
        """Handle key presses for action shortcuts."""
        if self._mode == "plan":
            if event.key == "a":
                event.prevent_default()
                self.action_approve()
                return
            if event.key == "d":
                event.prevent_default()
                self.action_dry_run()
                return
            if event.key == "c":
                event.prevent_default()
                self.action_cancel()
                return
        elif self._mode == "task_select":
            if event.key == "enter":
                event.prevent_default()
                self._on_task_selected()
                return

    def _on_task_selected(self) -> None:
        """Handle task selection from the baseline list."""
        table = self.query_one(DataTable)
        cursor = table.cursor_coordinate
        if cursor is None:
            return

        row_idx = cursor.row
        baselines = list_run_baselines(self._project)

        if self._task_filter:
            baselines = [b for b in baselines if b.task_prefix.upper() == self._task_filter.upper()]

        if row_idx < 0 or row_idx >= len(baselines):
            return

        selected = baselines[row_idx]
        self._baseline_path = Path(selected.file_path)
        self._refresh()


# ------------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------------


def run_rollback_screen(
    project: Path,
    baseline_path: Path | None = None,
    task_filter: str | None = None,
) -> RollbackResult | None:
    """Launch the Textual rollback confirmation screen.

    Args:
        project: The project root directory.
        baseline_path: Optional specific baseline file to use.
        task_filter: Optional task prefix filter.

    Returns:
        The :class:`RollbackResult` if the user approved and execution
        completed, or ``None`` if the user cancelled or quit.
    """
    app = RollbackApp(
        project=project,
        baseline_path=baseline_path,
        task_filter=task_filter,
    )
    app.run()
    return app._result
