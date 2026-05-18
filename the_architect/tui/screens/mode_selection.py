"""Pre-run mode-selection Textual screen.

Collects four settings:

1. ``free`` — bool (OpenRouter free-tier rotation; hidden when the
   provider does not support it)
2. ``persistent`` — bool
3. ``integrity`` — bool (architect_eval snapshot defense, default on)
4. ``token_budget_per_hour`` — int (0 = unlimited)

The screen also shows saved presets (loaded from ``.architect/presets.json``)
as a selectable list at the top. Selecting a preset pre-fills the form fields.

The screen dismisses with the dict of values on submit, or ``None`` on
cancel. Callers that aren't already hosting an :class:`ArchitectApp`
use :func:`run_mode_selection`, which routes to the active
:class:`ArchitectAppRunner` when one is in flight (no fresh app boot,
no alt-screen flash) or boots a minimal harness otherwise.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Checkbox, Footer, Header, Input, Label, ListItem, ListView, Static

from the_architect.tui.screens.pre_run import BACK_SENTINEL
from the_architect.tui.widgets import BlankOffCheckbox

if TYPE_CHECKING:
    from the_architect.core.presets import Preset


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

    /* Preset section */
    #preset_section {
        width: 100%;
    }
    #preset_label { color: $accent; text-style: bold; padding: 0 0 0 0; }
    #preset_list { border: round $panel; height: auto; }
    #preset_list ListItem { padding: 0 1; }
    #preset_no_msg { color: $text-muted; padding: 0 0 1 3; }

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
        project: Path | None = None,
        initial_free: bool = False,
        initial_persistent: bool = False,
        initial_integrity: bool = True,
        initial_budget: int = 0,
        initial_budget_run: int = 0,
        initial_notify_complete: bool = True,
        initial_notify_fail: bool = True,
    ) -> None:
        super().__init__()
        self._show_free = show_free
        self._project = project
        self._initial_free = initial_free
        self._initial_persistent = initial_persistent
        self._initial_integrity = initial_integrity
        self._initial_budget = initial_budget
        self._initial_budget_run = initial_budget_run
        self._initial_notify_complete = initial_notify_complete
        self._initial_notify_fail = initial_notify_fail
        self._presets: list[Preset] = []
        # Load presets synchronously so they are available during compose()
        if project is not None:
            self._load_presets()

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="mode_body"):
            yield Static("Configure run", id="mode_title")
            yield Static(
                "Pick a preset or configure settings manually.",
                id="mode_hint",
            )
            yield Static("")
            # ── Preset selection section ──────────────────────────
            yield Static("Presets:", id="preset_label")
            if self._presets:
                items: list[ListItem] = []
                for preset in self._presets:
                    desc = preset.description or "(no description)"
                    label = f"  [bold]{preset.name}[/bold] — {desc}"
                    items.append(ListItem(Static(label, markup=True)))
                yield ListView(*items, id="preset_list")
            else:
                yield Static(
                    "[dim]No presets saved. Use 'architect preset create' to add one.[/dim]",
                    id="preset_no_msg",
                    markup=True,
                )
            # ── Form fields ───────────────────────────────────────
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
            yield Label("Token budget/run (0 = unlimited):")
            yield Input(
                placeholder="0",
                id="inp_budget_run",
                value=str(self._initial_budget_run) if self._initial_budget_run > 0 else "",
            )
            yield BlankOffCheckbox(
                "Notify on complete  (desktop alert)",
                id="chk_notify_complete",
                value=self._initial_notify_complete,
            )
            yield Static(
                "desktop notification when the run finishes successfully",
                classes="mode_help",
            )
            yield BlankOffCheckbox(
                "Notify on fail  (desktop alert)",
                id="chk_notify_fail",
                value=self._initial_notify_fail,
            )
            yield Static(
                "desktop notification when the run fails",
                classes="mode_help",
            )
            yield Static("")
            yield Static(
                "[dim]↑↓ navigate · Space toggle · Enter submit · Esc cancel[/dim]",
                id="submit_row",
                markup=True,
            )
        yield Footer()

    def on_mount(self) -> None:
        # Focus the first interactive widget
        try:
            if self._presets:
                # Focus the preset list first so arrow keys navigate presets
                preset_list = self.query_one("#preset_list", ListView)
                preset_list.focus()
            else:
                first_check = self.query(Checkbox).first()
                if first_check is not None:
                    first_check.focus()
        except Exception:
            pass

    def _load_presets(self) -> None:
        """Load presets from the project's .architect/presets.json."""
        if self._project is None:
            return
        try:
            from the_architect.core.presets import list_presets

            self._presets = list_presets(self._project)
        except Exception:
            # If presets can't be loaded, silently show empty
            self._presets = []

    def _apply_preset(self, preset: Preset) -> None:
        """Pre-fill form fields from a preset's config_overrides."""
        overrides = preset.config_overrides or {}

        # Map preset config keys to form fields
        free_val = overrides.get("free_mode", self._initial_free)
        persistent_val = overrides.get("persistent", self._initial_persistent)
        integrity_val = overrides.get("integrity", self._initial_integrity)
        budget_val = overrides.get("token_budget_per_hour", self._initial_budget)
        budget_run_val = overrides.get("token_budget_per_run", self._initial_budget_run)
        notify_complete_val = overrides.get("notify_on_complete", self._initial_notify_complete)
        notify_fail_val = overrides.get("notify_on_fail", self._initial_notify_fail)

        # Apply to form widgets
        if self._show_free:
            try:
                self.query_one("#chk_free", Checkbox).value = bool(free_val)
            except Exception:
                pass
        try:
            self.query_one("#chk_persistent", Checkbox).value = bool(persistent_val)
        except Exception:
            pass
        try:
            self.query_one("#chk_integrity", Checkbox).value = bool(integrity_val)
        except Exception:
            pass
        try:
            budget_str = str(int(budget_val)) if int(budget_val) > 0 else ""
            self.query_one("#inp_budget", Input).value = budget_str
        except Exception:
            pass
        try:
            budget_run_str = str(int(budget_run_val)) if int(budget_run_val) > 0 else ""
            self.query_one("#inp_budget_run", Input).value = budget_run_str
        except Exception:
            pass
        try:
            self.query_one("#chk_notify_complete", Checkbox).value = bool(notify_complete_val)
        except Exception:
            pass
        try:
            self.query_one("#chk_notify_fail", Checkbox).value = bool(notify_fail_val)
        except Exception:
            pass

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle preset selection — pre-fill form fields."""
        # Only one ListView on this screen (#preset_list), so no sender check needed
        idx = event.index if event.index is not None else 0
        if 0 <= idx < len(self._presets):
            self._apply_preset(self._presets[idx])
            # Move focus to the first checkbox so user can edit fields
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

        raw_budget_run = self.query_one("#inp_budget_run", Input).value or "0"
        try:
            budget_run = int(raw_budget_run.strip() or "0")
        except ValueError:
            budget_run = 0
        budget_run = max(budget_run, 0)

        notify_complete = bool(self.query_one("#chk_notify_complete", Checkbox).value)
        notify_fail = bool(self.query_one("#chk_notify_fail", Checkbox).value)

        self.dismiss(
            {
                "free": free,
                "persistent": persistent,
                "integrity": integrity,
                "token_budget_per_hour": budget,
                "token_budget_per_run": budget_run,
                "notify_on_complete": notify_complete,
                "notify_on_fail": notify_fail,
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
    project: Path | None = None,
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
        "token_budget_per_run": 0,
        "notify_on_complete": True,
        "notify_on_fail": True,
    }
    screen = ModeSelectionScreen(
        show_free=show_free,
        project=project,
        initial_free=bool(mode.get("free", False)),
        initial_persistent=bool(mode.get("persistent", False)),
        initial_integrity=bool(mode.get("integrity", True)),
        initial_budget=int(mode.get("token_budget_per_hour", 0)),
        initial_budget_run=int(mode.get("token_budget_per_run", 0)),
        initial_notify_complete=bool(mode.get("notify_on_complete", True)),
        initial_notify_fail=bool(mode.get("notify_on_fail", True)),
    )
    result = run_single_screen(screen)
    if result is BACK_SENTINEL:
        return BACK_SENTINEL
    if result is None:
        raise SystemExit(0)
    return result
