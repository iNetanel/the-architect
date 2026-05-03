"""Resume-run Textual screen.

Shows pending tasks, prefills mode toggles from the current config,
and dismisses with a dict that includes an ``action`` key
(``"execute"`` or ``"replan"``), or ``None`` on cancel.

The Execute vs Replan choice is a :class:`RadioSet` at the top of the
form — same visual family as the Checkbox-based mode toggles below, so
the whole screen reads as a normal options selection. No Buttons, no
custom keyboard shortcut per action: users arrow through the form,
toggle with Space, and press Enter to submit with whatever Execute /
Replan option is currently selected.

Callers use :func:`run_resume_screen`, which routes to the active
:class:`ArchitectAppRunner` when one is in flight so the screen is
pushed on the already-running app (no alt-screen flash).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Checkbox, Footer, Header, Input, Label, RadioSet, Static

from the_architect.tui.widgets import BlankOffCheckbox, BlankOffRadioButton

if TYPE_CHECKING:
    from the_architect.config import ArchitectConfig
    from the_architect.core.tasks import Task


class ResumeScreen(Screen[dict[str, bool | int | str]]):
    """Screen for resuming a plan with pending tasks."""

    DEFAULT_CSS = """
    ResumeScreen {
        align: center middle;
    }

    #resume_body {
        width: 82;
        height: auto;
        padding: 1 2;
        border: round $panel;
        background: $panel 20%;
    }

    #resume_title { color: $accent; text-style: bold; }
    .muted { color: $text-muted; }

    #task_list { padding: 0 0 1 2; color: $text-muted; }

    /* Action RadioSet: compact, no heavy border, so it reads as one
       of the form fields rather than a boxed-off widget. The `-on`
       dot takes the brand accent colour; off-state dots are blank
       (handled by BlankOffRadioButton._button). */
    #action_set {
        border: none;
        padding: 0;
        margin: 0 0 1 0;
        background: transparent;
    }
    #action_set > RadioButton {
        padding: 0;
        background: transparent;
    }
    #action_set > RadioButton.-on > .toggle--button {
        color: $accent;
        background: $panel;
        text-style: bold;
    }

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
    """

    BINDINGS = [
        # Enter submits the whole form using whatever Execute/Replan
        # radio option is currently selected. priority=True so Enter
        # reaches this handler before the focused child widget
        # (Checkbox, RadioButton, or Input).
        Binding("enter", "submit", "Submit", priority=True),
        # Arrow keys move focus between form fields, matching the
        # arrow navigation the other form screens use. Space toggles
        # the focused Checkbox / RadioButton (Textual's default).
        Binding("up", "focus_previous", "Previous field", show=False),
        Binding("down", "focus_next", "Next field", show=False),
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("ctrl+c", "cancel", "Cancel", priority=True),
    ]

    def __init__(
        self,
        pending_tasks: list[Task],
        config: ArchitectConfig,
        show_free: bool = True,
    ) -> None:
        super().__init__()
        self._pending = pending_tasks
        self._config = config
        self._show_free = show_free

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="resume_body"):
            yield Static("Resume run", id="resume_title")
            n = len(self._pending)
            yield Static(
                f"{n} pending task{'s' if n != 1 else ''} to execute",
                classes="muted",
            )
            task_text = self._format_tasks()
            yield Static(task_text, id="task_list", markup=False)

            # Action picker — Execute vs Replan as a radio group. The
            # first button is selected by default (Execute) so hitting
            # Enter immediately does the expected thing.
            yield Static("Action", classes="muted")
            with RadioSet(id="action_set"):
                yield BlankOffRadioButton(
                    "Execute  (run the pending tasks as-is)",
                    id="rb_execute",
                    value=True,
                )
                yield BlankOffRadioButton(
                    "Replan  (discard pending tasks and plan again)",
                    id="rb_replan",
                )

            yield Static("Settings", classes="muted")
            if self._show_free:
                yield BlankOffCheckbox(
                    "Free Tier  (OpenRouter rotation)",
                    id="chk_free",
                    value=bool(self._config.free_mode),
                )
            yield BlankOffCheckbox(
                "Persistent  (30 retries, 2 retrospective rounds)",
                id="chk_persistent",
                value=bool(self._config.persistent),
            )
            yield BlankOffCheckbox(
                "Integrity defense  (snapshot before edits)",
                id="chk_integrity",
                value=bool(self._config.integrity),
            )
            yield Label("Token budget/hour (0 = unlimited):")
            starting_budget = (
                str(self._config.token_budget_per_hour)
                if self._config.token_budget_per_hour > 0
                else ""
            )
            yield Input(placeholder="0", id="inp_budget", value=starting_budget)

            yield Static("")
            yield Static(
                "[dim]↑↓ navigate · Space toggle · Enter submit · Esc cancel[/dim]",
                markup=True,
            )
        yield Footer()

    def on_mount(self) -> None:
        # Focus the RadioSet first so arrow keys immediately move
        # between Execute / Replan — users see the primary decision
        # before the secondary mode toggles.
        try:
            self.query_one("#action_set", RadioSet).focus()
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
        """Submit the form using the currently selected Execute/Replan option."""
        action = "execute"
        try:
            pressed = self.query_one("#action_set", RadioSet).pressed_button
            if pressed is not None and pressed.id == "rb_replan":
                action = "replan"
        except Exception:
            # Defensive fallback — if the RadioSet query ever fails,
            # treat it as execute so we never silently throw away the
            # user's pending-task progress.
            action = "execute"
        self._submit(action)

    def action_execute(self) -> None:
        """Back-compat shim: submit the form as Execute.

        The Execute / Replan choice lives in the RadioSet now, so the
        canonical path is :meth:`action_submit` after the user moves
        the selection. Existing tests (and any external callers that
        may still exist) drive the screen by calling these methods
        directly, so we keep them as tiny submit shortcuts.
        """
        self._submit("execute")

    def action_replan(self) -> None:
        """Back-compat shim: submit the form as Replan. See :meth:`action_execute`."""
        self._submit("replan")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self, action: str) -> None:
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

        self.dismiss(
            {
                "free": free,
                "persistent": persistent,
                "integrity": integrity,
                "token_budget_per_hour": max(budget, 0),
                "action": action,
            }
        )

    def _format_tasks(self) -> str:
        lines: list[str] = []
        for task in self._pending[:5]:
            prefix = getattr(task, "prefix", "")
            title = getattr(task, "title", None) or getattr(task, "name", "")
            lines.append(f"  {prefix}  {title}".rstrip())
        if len(self._pending) > 5:
            lines.append(f"  ... and {len(self._pending) - 5} more")
        return "\n".join(lines) if lines else "  (none)"


# Legacy alias for existing tests referencing ``ResumeApp``.
ResumeApp = ResumeScreen


def run_resume_screen(
    pending_tasks: list[Task],
    config: ArchitectConfig,
    show_free: bool = True,
) -> dict[str, bool | int | str]:
    """Show the resume screen and return the chosen settings + action.

    Uses the active :class:`ArchitectAppRunner` if one is in flight —
    no new app boot, no alt-screen flash. Falls back to a minimal
    harness when the caller is not inside a runner.
    """
    from the_architect.tui.app import run_single_screen

    result = run_single_screen(
        ResumeScreen(pending_tasks=pending_tasks, config=config, show_free=show_free)
    )
    if result is None:
        raise SystemExit(0)
    return result
