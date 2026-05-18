"""Textual status screen — run state, tasks, circuit, tokens, logs."""

from __future__ import annotations

import json
import os
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Static

from the_architect.config import load_config
from the_architect.core.progress import task_status
from the_architect.core.tasks import discover_tasks


class StatusApp(App[None]):
    """Status screen: lock file, tasks, circuit breaker, tokens, logs."""

    CSS = """
    Screen { background: $surface; }
    #status_body { height: 1fr; padding: 1 2; }
    #status_title { color: $accent; text-style: bold; }
    .section_label { color: $text-muted; padding: 1 0 0 0; }
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
        with Vertical(id="status_body"):
            yield Static(f"Status  —  {self._project}", id="status_title")
            with VerticalScroll():
                yield Static("", id="lock_line")
                yield Static("Tasks", classes="section_label")
                tasks_table: DataTable[str] = DataTable(id="tasks_table", zebra_stripes=True)
                tasks_table.add_columns("Task", "Title", "Status")
                yield tasks_table
                yield Static("", id="task_summary")
                yield Static("Circuit breaker", classes="section_label")
                circuit_table: DataTable[str] = DataTable(id="circuit_table", zebra_stripes=True)
                circuit_table.add_columns("Task", "State", "No-prog", "Same-err")
                yield circuit_table
                yield Static("Token budget", classes="section_label")
                yield Static("", id="token_line")
                yield Static("Logs", classes="section_label")
                yield Static("", id="logs_line")
        yield Footer()

    def on_mount(self) -> None:
        from the_architect.tui.app import apply_architect_theme

        apply_architect_theme(self)
        self._refresh_all()

    def action_refresh(self) -> None:
        self._refresh_all()

    def _refresh_all(self) -> None:
        config = load_config(self._project)

        # Lock
        lock_path = self._project / ".architect" / "runner.lock"
        lock_text = "Not running"
        if lock_path.exists():
            try:
                pid = int(lock_path.read_text(encoding="utf-8").strip())
                try:
                    os.kill(pid, 0)
                    lock_text = f"Running · PID {pid}"
                except (ProcessLookupError, PermissionError, OSError):
                    lock_text = "Not running (stale lock)"
            except (OSError, ValueError):
                lock_text = "Not running"
        self.query_one("#lock_line", Static).update(lock_text)

        # Tasks
        tasks_dir = self._project / config.tasks_dir.name
        progress_file = config.progress_file
        tasks_table = self.query_one("#tasks_table", DataTable)
        tasks_table.clear()
        if tasks_dir.exists():
            tasks = discover_tasks(tasks_dir)
            done = 0
            for task in tasks:
                status = task_status(progress_file, task.prefix) or "Pending"
                if status == "Done":
                    done += 1
                tasks_table.add_row(task.prefix, task.title or task.name, status)
            self.query_one("#task_summary", Static).update(f"{done}/{len(tasks)} complete")
        else:
            self.query_one("#task_summary", Static).update("No tasks directory.")

        # Circuit
        circuit_table = self.query_one("#circuit_table", DataTable)
        circuit_table.clear()
        circuit_file = self._project / ".architect" / "circuit.json"
        if circuit_file.exists():
            try:
                data = json.loads(circuit_file.read_text(encoding="utf-8"))
                for tid, s in data.items():
                    if s.get("state") in ("OPEN", "HALF_OPEN"):
                        circuit_table.add_row(
                            tid,
                            s.get("state", "?"),
                            str(s.get("consecutive_no_progress", 0)),
                            str(s.get("consecutive_same_error", 0)),
                        )
            except (OSError, json.JSONDecodeError):
                pass

        # Token budget
        budget_parts: list[str] = []
        if config.token_budget_per_hour > 0:
            budget_parts.append(f"{config.token_budget_per_hour:,} tokens/hour")
        if config.token_budget_per_run > 0:
            budget_parts.append(f"{config.token_budget_per_run:,} tokens/run")
        if budget_parts:
            self.query_one("#token_line", Static).update("  ·  ".join(budget_parts))
        else:
            self.query_one("#token_line", Static).update("unlimited")

        # Logs
        log_dir = config.log_dir
        if log_dir.exists():
            files = sorted(log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
            if files:
                names = [f"{f.name} ({f.stat().st_size // 1024} KB)" for f in files[:5]]
                extra = f"\n... and {len(files) - 5} more" if len(files) > 5 else ""
                self.query_one("#logs_line", Static).update(
                    f"{log_dir}\n" + "\n".join(names) + extra
                )
                return
        self.query_one("#logs_line", Static).update("No logs yet.")


def run_status_screen(project: Path) -> None:
    """Launch the Textual status screen."""
    StatusApp(project=project).run()
