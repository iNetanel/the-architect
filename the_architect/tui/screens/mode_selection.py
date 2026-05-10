"""Pre-run mode-selection Textual screen.

Collects four settings:

1. ``free`` — bool (OpenRouter free-tier rotation; hidden when the
   provider does not support it)
2. ``persistent`` — bool
3. ``integrity`` — bool (architect_eval snapshot defense, default on)
4. ``token_budget_per_hour`` — int (0 = unlimited)

The screen dismisses with the dict of values on submit, or ``None`` on
cancel. Callers that aren't already hosting an :class:`ArchitectApp`
use :func:`run_mode_selection`, which routes to the active
:class:`ArchitectAppRunner` when one is in flight (no fresh app boot,
no alt-screen flash) or boots a minimal harness otherwise.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Checkbox, Footer, Header, Input, Label, Static

from the_architect.tui.screens.pre_run import BACK_SENTINEL
from the_architect.tui.widgets import BlankOffCheckbox


class ModeSelectionScreen(Screen[dict[str, bool | int]]):
    """Screen that collects run-mode settings and dismisses with them."""

    DEFAULT_CSS = """
    ModeSelectionScreen {
        align: center middle;
    }

    #mode_body {
        width: 72;
        height: auto;
        padding: 1 2;
        border: round $panel;
        background: $panel 20%;
    }

    #mode_title { color: $accent; text-style: bold; }
    #mode_hint { color: $text-muted; }
    .mode_help { color: $text-muted; padding: 0 0 1 3; }

    Checkbox { padding: 0; }
    /* On-state marker is bold green; off-state is a blank space
       (handled in Python via BlankOffCheckbox._button) so the
       indicator unambiguously reads as "unselected" rather than a
       dim X that could be mistaken for on. */
    Checkbox.-on > .toggle--button {
        color: $success;
        background: $panel;
        text-style: bold;
    }
    Input { border: round $panel; }

    #submit_row { color: $text-muted; padding: 1 0 0 0; }
    """

    BINDINGS = [
        # priority=True so Enter is handled here before the focused
        # Checkbox sees it — Space is the accepted key for toggling
        # checkboxes (Textual's default), Enter always submits.
        Binding("enter", "submit", "Submit", priority=True),
        Binding("backspace", "go_back", "Back"),
        # Arrow keys move focus between form fields, matching the
        # arrow navigation the ListView-based screens use.
        Binding("up", "focus_previous", "Previous field", show=False),
        Binding("down", "focus_next", "Next field", show=False),
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("ctrl+c", "cancel", "Cancel", priority=True),
    ]

    def __init__(
        self,
        show_free: bool = True,
        *,
        initial_free: bool = False,
        initial_persistent: bool = False,
        initial_integrity: bool = True,
        initial_budget: int = 0,
    ) -> None:
        super().__init__()
        self._show_free = show_free
        self._initial_free = initial_free
        self._initial_persistent = initial_persistent
        self._initial_integrity = initial_integrity
        self._initial_budget = initial_budget

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="mode_body"):
            yield Static("Configure run", id="mode_title")
            yield Static(
                "Pick your mode toggles and a token budget (0 = unlimited).",
                id="mode_hint",
            )
            yield Static("")
            if self._show_free:
                yield BlankOffCheckbox(
                    "Free Tier  (OpenRouter rotation)",
                    id="chk_free",
                    value=self._initial_free,
                )
                yield Static(
                    "rotate to the next free model on rate-limit",
                    classes="mode_help",
                )
            yield BlankOffCheckbox(
                "Persistent  (30 retries, 3 retrospective rounds)",
                id="chk_persistent",
                value=self._initial_persistent,
            )
            yield Static("deeper retry + review loop", classes="mode_help")
            yield BlankOffCheckbox(
                "Integrity defense  (snapshot before edits)",
                id="chk_integrity",
                value=self._initial_integrity,
            )
            yield Static(
                "architect_eval snapshots catch truncated/corrupted writes",
                classes="mode_help",
            )
            yield Label("Token budget/hour (0 = unlimited):")
            yield Input(
                placeholder="0",
                id="inp_budget",
                value=str(self._initial_budget) if self._initial_budget > 0 else "",
            )
            yield Static("")
            yield Static(
                "[dim]↑↓ navigate · Space toggle · Enter submit · Esc cancel[/dim]",
                id="submit_row",
                markup=True,
            )
        yield Footer()

    def on_mount(self) -> None:
        # Focus the first toggle so Space / Enter / arrow keys work
        # immediately, without users needing to press Tab first.
        try:
            first_check = self.query(Checkbox).first()
            if first_check is not None:
                first_check.focus()
        except Exception:
            pass

    def action_focus_previous(self) -> None:
        """Move focus to the previous focusable widget on this screen.

        Textual's :class:`Screen` exposes :meth:`focus_previous` as a
        method, not an ``action_*`` handler, so a ``Binding`` that
        references ``"focus_previous"`` silently does nothing without
        this shim. Same applies to :meth:`action_focus_next`.
        """
        self.focus_previous()

    def action_focus_next(self) -> None:
        """Move focus to the next focusable widget on this screen."""
        self.focus_next()

    def action_submit(self) -> None:
        free = False
        if self._show_free:
            try:
                free = bool(self.query_one("#chk_free", Checkbox).value)
            except Exception:
                free = False
        persistent = bool(self.query_one("#chk_persistent", Checkbox).value)
        integrity = bool(self.query_one("#chk_integrity", Checkbox).value)

        raw_budget = self.query_one("#inp_budget", Input).value or "0"
        try:
            budget = int(raw_budget.strip() or "0")
        except ValueError:
            budget = 0
        budget = max(budget, 0)

        self.dismiss(
            {
                "free": free,
                "persistent": persistent,
                "integrity": integrity,
                "token_budget_per_hour": budget,
            }
        )

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_go_back(self) -> None:
        """Navigate back to the previous pre-run screen."""
        self.dismiss(BACK_SENTINEL)  # type: ignore[arg-type]


# Legacy alias for tests that still reference the old class name.
ModeSelectionApp = ModeSelectionScreen


def run_mode_selection(
    show_free: bool = True,
    *,
    initial_mode: dict[str, bool | int] | None = None,
) -> dict[str, bool | int] | object:
    """Show the mode-selection screen and return the chosen settings.

    Uses the currently active :class:`ArchitectAppRunner` if one is in
    flight — no fresh app boot, no alt-screen flash. Falls back to a
    minimal harness when no runner is hosting the CLI flow.

    Raises :class:`SystemExit` with code 0 when the user cancels.
    Returns ``BACK_SENTINEL`` on back.
    """
    from the_architect.tui.app import run_single_screen

    mode = initial_mode or {
        "free": False,
        "persistent": False,
        "integrity": True,
        "token_budget_per_hour": 0,
    }
    screen = ModeSelectionScreen(
        show_free=show_free,
        initial_free=bool(mode.get("free", False)),
        initial_persistent=bool(mode.get("persistent", False)),
        initial_integrity=bool(mode.get("integrity", True)),
        initial_budget=int(mode.get("token_budget_per_hour", 0)),
    )
    result = run_single_screen(screen)
    if result is BACK_SENTINEL:
        return BACK_SENTINEL
    if result is None:
        raise SystemExit(0)
    return result
