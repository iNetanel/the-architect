"""Textual monitor screen — live view of runner state."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Footer, Header, Static

from the_architect.core.monitor_state import read_monitor_state


def _as_int(value: object, default: int = 0) -> int:
    """Safely coerce a monitor state value to int for formatting."""
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return default


class MonitorApp(App[None]):
    """Monitor screen — polls `.architect/monitor.json` and renders state."""

    CSS = """
    Screen { background: $surface; }
    #monitor_body { height: 1fr; padding: 1 2; }
    #monitor_title { color: $accent; text-style: bold; }
    #monitor_content { padding: 1 0 0 0; }
    .muted { color: $text-muted; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    POLL_INTERVAL = 1.0

    def __init__(self, project: Path) -> None:
        super().__init__()
        self._project = project

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="monitor_body"):
            yield Static(f"Monitor  —  {self._project}", id="monitor_title")
            with VerticalScroll():
                yield Static("", id="monitor_content", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()
        self.set_interval(self.POLL_INTERVAL, self._refresh)

    def action_refresh(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        state = read_monitor_state(self._project)
        content = self.query_one("#monitor_content", Static)
        if state is None:
            content.update("No active monitor state (.architect/monitor_state.json not found).")
            return
        lines: list[str] = [self._format_section("Run", self._run_fields(state))]
        if state.get("current_task_id"):
            lines.append(self._format_section("Current task", self._task_fields(state)))
        if state.get("cooldown_active"):
            lines.append(self._format_section("Cooldown", self._cooldown_fields(state)))
        lines.append(self._format_section("Tokens", self._token_fields(state)))
        tasks_section = self._task_list_section(state)
        if tasks_section:
            lines.append(tasks_section)
        content.update("\n\n".join(lines))

    @staticmethod
    def _format_section(title: str, rows: list[tuple[str, str]]) -> str:
        header = title
        body = "\n".join(f"  {k:<22}  {v}" for k, v in rows)
        return f"{header}\n{body}" if body else header

    @staticmethod
    def _run_fields(state: dict[str, object]) -> list[tuple[str, str]]:
        session_tokens = _as_int(state.get("session_tokens"))
        return [
            ("status", str(state.get("status", ""))),
            ("total_tasks", str(state.get("total_tasks", ""))),
            ("completed_tasks", str(state.get("completed_tasks", ""))),
            ("session_tokens", f"{session_tokens:,}"),
        ]

    @staticmethod
    def _task_fields(state: dict[str, object]) -> list[tuple[str, str]]:
        return [
            ("task_id", str(state.get("current_task_id", ""))),
            ("title", str(state.get("current_task_title", ""))),
            ("attempt", str(state.get("current_attempt", ""))),
            ("model", str(state.get("current_model", ""))),
        ]

    @staticmethod
    def _cooldown_fields(state: dict[str, object]) -> list[tuple[str, str]]:
        return [
            ("started_at", str(state.get("cooldown_started_at", ""))),
            ("wait_count", str(state.get("cooldown_wait_count", ""))),
        ]

    @staticmethod
    def _token_fields(state: dict[str, object]) -> list[tuple[str, str]]:
        session = _as_int(state.get("session_tokens"))
        last_attempt = _as_int(state.get("last_attempt_tokens"))
        return [
            ("session", f"{session:,}"),
            ("last_attempt", f"{last_attempt:,}"),
        ]

    @staticmethod
    def _task_list_section(state: dict[str, object]) -> str:
        statuses = state.get("task_statuses")
        if not isinstance(statuses, dict) or not statuses:
            return ""
        lines = ["Task statuses"]
        for tid in sorted(statuses.keys()):
            lines.append(f"  {tid:<8}  {statuses[tid]}")
        return "\n".join(lines)


def run_monitor_screen(project: Path) -> None:
    """Launch the Textual monitor screen."""
    MonitorApp(project=project).run()
