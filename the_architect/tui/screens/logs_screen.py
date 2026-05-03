"""Textual logs viewer: list log files + paneled content viewer."""

from __future__ import annotations

import datetime as _dt
import json as _json
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Header, RichLog, Static

from the_architect.config import load_config


class LogsApp(App[None]):
    """Logs screen: pick a log file on the left, view content on the right."""

    CSS = """
    Screen { background: $surface; }
    #logs_body { height: 1fr; padding: 1 2; }
    #logs_title { color: $accent; text-style: bold; }
    #logs_split { height: 1fr; }
    #logs_list { width: 36; border: round $panel; padding: 0 1; }
    #logs_view { width: 1fr; border: round $panel; padding: 0 1; }
    DataTable { border: none; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, project: Path, task_prefix: str = "", tail: int = 200) -> None:
        super().__init__()
        self._project = project
        self._task_prefix = task_prefix
        self._tail = tail

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="logs_body"):
            yield Static(f"Logs  —  {self._project}", id="logs_title")
            with Horizontal(id="logs_split"):
                with Vertical(id="logs_list"):
                    yield Static("Log files", classes="section_label")
                    table: DataTable[str] = DataTable(id="logs_table", zebra_stripes=True)
                    table.add_columns("File", "Size", "Modified")
                    yield table
                with VerticalScroll(id="logs_view"):
                    yield RichLog(id="logs_content", highlight=False, markup=False, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_list()

    def action_refresh(self) -> None:
        self._refresh_list()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        file_name = str(event.row_key.value) if event.row_key is not None else ""
        if file_name:
            self._show_file(file_name)

    def _refresh_list(self) -> None:
        config = load_config(self._project)
        log_dir = config.log_dir
        table = self.query_one("#logs_table", DataTable)
        table.clear()
        if not log_dir.exists():
            return
        files = sorted(log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
        for lf in files:
            stat = lf.stat()
            size = f"{stat.st_size // 1024} KB" if stat.st_size >= 1024 else f"{stat.st_size} B"
            mtime = _dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            table.add_row(lf.name, size, mtime, key=lf.name)
        # Auto-open the first match for the task prefix, or the newest log.
        prefix = self._task_prefix.upper()
        preferred = None
        if prefix:
            for lf in files:
                if lf.name.upper().startswith(prefix):
                    preferred = lf.name
                    break
        if preferred is None and files:
            preferred = files[0].name
        if preferred:
            self._show_file(preferred)

    def _show_file(self, name: str) -> None:
        config = load_config(self._project)
        path = config.log_dir / name
        log = self.query_one("#logs_content", RichLog)
        log.clear()
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.write(f"[error] could not read {name}: {exc}")
            return
        lines: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = _json.loads(line)
            except Exception:
                lines.append(line)
                continue
            etype = event.get("type", "")
            part = event.get("part", {})
            if etype == "text" and isinstance(part, dict):
                text = (part.get("text") or "").strip()
                if text:
                    lines.extend(text.split("\n"))
            elif etype == "error":
                msg = str(event.get("message", event.get("error", ""))).strip()
                if msg:
                    lines.append(f"[ERROR] {msg}")
        if self._tail > 0:
            lines = lines[-self._tail :]
        for line in lines:
            log.write(line)


def run_logs_screen(project: Path, task_prefix: str = "", tail: int = 200) -> None:
    """Launch the Textual logs viewer."""
    LogsApp(project=project, task_prefix=task_prefix, tail=tail).run()
