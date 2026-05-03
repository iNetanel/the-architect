"""Textual circuit breaker inspector screen."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Static

from the_architect.config import load_config
from the_architect.core.circuit import CircuitState, load_circuit_state
from the_architect.core.tasks import discover_tasks


class CircuitApp(App[None]):
    """Circuit breaker screen — per-task state with counters and recovery."""

    CSS = """
    Screen { background: $surface; }
    #circuit_body { height: 1fr; padding: 1 2; }
    #circuit_title { color: $accent; text-style: bold; }
    DataTable { border: round $panel; }
    #circuit_hint { color: $text-muted; padding: 1 0 0 0; }
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
        with Vertical(id="circuit_body"):
            yield Static(
                f"Circuit breaker  —  {self._project}",
                id="circuit_title",
            )
            with VerticalScroll():
                table: DataTable[str] = DataTable(zebra_stripes=True)
                table.add_columns("Task", "State", "No-prog", "Same-err", "Recovery", "Opened")
                yield table
            yield Static(
                "Use 'architect circuit --reset TASK_ID' from the shell to reset a task.",
                id="circuit_hint",
            )
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()

    def action_refresh(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        config = load_config(self._project)
        cb = load_circuit_state(self._project, config)
        states = cb.all_states()

        tasks_dir = self._project / config.tasks_dir.name
        all_tasks = discover_tasks(tasks_dir) if tasks_dir.exists() else []
        for t in all_tasks:
            if t.prefix not in states:
                states[t.prefix] = None  # type: ignore[assignment]

        table = self.query_one(DataTable)
        table.clear()
        for task_id in sorted(states.keys()):
            state = states[task_id]
            if state is None:
                table.add_row(task_id, "CLOSED", "0", "0", "—", "—")
                continue
            if state.state == CircuitState.CLOSED:
                state_str = "CLOSED"
            elif state.state == CircuitState.OPEN:
                state_str = "OPEN"
            else:
                state_str = "HALF_OPEN"
            recovery = str(state.recovery_action.value) if state.recovery_action else "—"
            opened_str = "—"
            if state.opened_at:
                try:
                    then = datetime.fromisoformat(state.opened_at)
                    now = datetime.now(tz=UTC)
                    if then.tzinfo is None:
                        then = then.replace(tzinfo=UTC)
                    elapsed = int((now - then).total_seconds())
                    if elapsed < 60:
                        opened_str = f"{elapsed}s ago"
                    elif elapsed < 3600:
                        opened_str = f"{elapsed // 60}m ago"
                    else:
                        opened_str = f"{elapsed // 3600}h {(elapsed % 3600) // 60}m ago"
                except (ValueError, TypeError):
                    opened_str = state.opened_at[:16]
            table.add_row(
                task_id,
                state_str,
                str(state.consecutive_no_progress),
                str(state.consecutive_same_error),
                recovery,
                opened_str,
            )


def run_circuit_screen(project: Path) -> None:
    """Launch the Textual circuit inspector."""
    CircuitApp(project=project).run()
