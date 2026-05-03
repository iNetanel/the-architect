"""Help overlay — shows the current screen's key bindings.

Phase 19: pushed onto the app on the ``?`` key from any screen. The
overlay inspects the screen underneath it and lists every active key
binding in a readable table. Dismisses on ``?`` or ``Escape``.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static


class HelpScreen(ModalScreen[None]):
    """Modal help overlay listing the current screen's bindings."""

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }

    #help_body {
        width: 64;
        height: auto;
        padding: 1 2;
        border: round $panel;
        background: $panel 30%;
    }

    #help_title { color: $accent; text-style: bold; }
    #help_hint { color: $text-muted; padding: 0 0 1 0; }

    DataTable { border: round $panel; }

    #help_instructions { color: $text-muted; padding: 1 0 0 0; }
    """

    BINDINGS = [
        Binding("question_mark", "dismiss_help", "Close"),
        Binding("escape", "dismiss_help", "Close"),
        Binding("ctrl+c", "dismiss_help", "Close"),
    ]

    def __init__(self, bindings: list[tuple[str, str]]) -> None:
        """Build the overlay.

        Args:
            bindings: Ordered list of ``(key, description)`` pairs to
                render in the help table.
        """
        super().__init__()
        self._rows = bindings

    def compose(self) -> ComposeResult:
        with Vertical(id="help_body"):
            yield Static("Keyboard shortcuts", id="help_title")
            yield Static(
                "Bindings active on the screen behind this overlay.",
                id="help_hint",
            )
            with VerticalScroll():
                table: DataTable[str] = DataTable(zebra_stripes=True, show_header=True)
                table.add_columns("Key", "Action")
                for key, description in self._rows:
                    table.add_row(key, description)
                yield table
            yield Static(
                "[dim]? or Esc close[/dim]",
                id="help_instructions",
                markup=True,
            )

    def action_dismiss_help(self) -> None:
        self.dismiss(None)


def collect_screen_bindings(screen: object) -> list[tuple[str, str]]:
    """Return ``(key, description)`` pairs for every binding on ``screen``.

    Phase 19: resolve the bindings defined on the screen class itself.
    App-level bindings (``?`` for help, ``q`` to quit, etc.) are added
    at the end so users always see the global shortcuts.
    """
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()

    bindings = getattr(screen.__class__, "BINDINGS", None) or []
    for binding in bindings:
        key, description = _extract_binding_fields(binding)
        if not key or not description or key in seen:
            continue
        seen.add(key)
        rows.append((_format_key(key), description))

    # Always surface the global bindings last so help is self-contained.
    globals_: list[tuple[str, str]] = [
        ("?", "Show this help"),
        ("q", "Quit"),
        ("ctrl+c", "Quit"),
    ]
    for key, description in globals_:
        if key in seen:
            continue
        seen.add(key)
        rows.append((_format_key(key), description))

    return rows


def _extract_binding_fields(binding: object) -> tuple[str, str]:
    """Normalise a Textual binding entry into ``(key, description)``."""
    if isinstance(binding, Binding):
        return binding.key, binding.description or binding.action
    if isinstance(binding, tuple) and len(binding) >= 2:
        key = str(binding[0])
        description = str(binding[-1]) if len(binding) >= 3 else str(binding[1])
        return key, description
    return "", ""


def _format_key(key: str) -> str:
    """Make keybinding names a bit nicer to read in the table."""
    return key.replace("ctrl+", "Ctrl+").replace("escape", "Esc")
