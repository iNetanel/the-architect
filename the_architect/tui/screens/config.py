"""Textual screen for viewing The Architect configuration.

Read-only view of all runtime config fields. Editing continues to go
through ``architect config --set KEY=VALUE`` so mutations stay
auditable and scriptable; this screen is the nicer lens onto what the
current values are and where they came from.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Static

if TYPE_CHECKING:
    from pathlib import Path

    from the_architect.config import ArchitectConfig


class ConfigApp(App[None]):
    """Textual app that shows the resolved Architect configuration."""

    CSS = """
    Screen { background: $surface; }

    #config_body {
        height: 1fr;
        padding: 1 2;
    }

    #config_title { color: $accent; text-style: bold; }
    #config_source { color: $text-muted; }
    #config_hint { color: $text-muted; padding: 1 0 0 0; }

    DataTable { border: round $panel; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit"),
    ]

    def __init__(self, config: ArchitectConfig, toml_path: Path, has_toml: bool) -> None:
        super().__init__()
        self._config = config
        self._toml_path = toml_path
        self._has_toml = has_toml

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="config_body"):
            yield Static("Configuration", id="config_title")
            source = str(self._toml_path) if self._has_toml else "defaults only"
            yield Static(f"Source: {source}", id="config_source")
            yield Static("")
            with VerticalScroll():
                table: DataTable[str] = DataTable(
                    zebra_stripes=True,
                    cursor_type="row",
                )
                table.add_columns("Field", "Value")
                for key, val in self._rows():
                    table.add_row(key, val)
                yield table
            yield Static(
                "[dim]↑↓ navigate · Esc quit[/dim]",
                id="config_hint",
                markup=True,
            )
        yield Footer()

    def on_mount(self) -> None:
        # Focus the DataTable so its built-in arrow-key row navigation
        # works immediately without needing Tab first.
        try:
            self.query_one(DataTable).focus()
        except Exception:
            pass

    def _rows(self) -> list[tuple[str, str]]:
        c = self._config
        rows: list[tuple[str, str]] = [
            ("max_retries", str(c.max_retries)),
            ("retry_pause", str(c.retry_pause)),
            ("pause_between_tasks", str(c.pause_between_tasks)),
            ("retrospective_rounds", str(c.retrospective_rounds)),
            ("retry_model_2", c.retry_model_2 or "(default)"),
            ("retry_model_3", c.retry_model_3 or "(default)"),
            ("standalone_mode", c.standalone_mode or "(not set)"),
            ("execution_agent", c.execution_agent or "(not set)"),
            ("carry_context", str(c.carry_context)),
            ("retry_prompt_mode", c.retry_prompt_mode),
            ("free_mode", str(c.free_mode)),
            ("persistent", str(c.persistent)),
            ("token_budget_per_hour", str(c.token_budget_per_hour)),
            ("integrity", str(c.integrity)),
            (
                "circuit_no_progress_threshold",
                str(c.circuit_no_progress_threshold),
            ),
            ("circuit_same_error_threshold", str(c.circuit_same_error_threshold)),
            ("circuit_token_decline_pct", str(c.circuit_token_decline_pct)),
            ("circuit_cooldown_minutes", str(c.circuit_cooldown_minutes)),
            ("circuit_enable_replan", str(c.circuit_enable_replan)),
            ("cooldown_detection", str(c.cooldown_detection)),
        ]
        return rows


def run_config_screen(config: ArchitectConfig, toml_path: Path, has_toml: bool) -> None:
    """Launch the read-only TUI config viewer."""
    ConfigApp(config=config, toml_path=toml_path, has_toml=has_toml).run()
