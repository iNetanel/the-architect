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

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from loguru import logger
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import (
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    RadioSet,
    Static,
    TextArea,
)

from the_architect.tui.widgets import BlankOffCheckbox, BlankOffRadioButton

if TYPE_CHECKING:
    from the_architect.config import ArchitectConfig
    from the_architect.core.resume_verification import ResumeVerificationResult
    from the_architect.core.tasks import Task


class ResumeScreen(Screen[dict[str, bool | int | str]]):
    """Screen for resuming a plan with pending tasks."""

    DEFAULT_CSS = """
    ResumeScreen {
        align: center middle;
    }

    #resume_body {
        width: 100%;
        max-width: 82;
        height: auto;
        padding: 1 2;
        border: round $panel;
        background: $panel 20%;
    }

    #resume_title { color: $accent; text-style: bold; }
    .muted { color: $text-muted; }

    #task_list { padding: 0 0 1 2; color: $text-muted; }

    /* Verification summary and indicators */
    .verify-summary { color: $text-muted; }
    .verify-valid { color: $success; text-style: bold; }
    .verify-stale { color: $warning; text-style: bold; }
    .verify-missing { color: $error; text-style: bold; }

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
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("ctrl+c", "cancel", "Cancel", priority=True),
    ]

    def __init__(
        self,
        pending_tasks: list[Task],
        config: ArchitectConfig,
        show_free: bool = True,
        verification_results: Sequence[ResumeVerificationResult] | None = None,
    ) -> None:
        super().__init__()
        self._pending = pending_tasks
        self._config = config
        self._show_free = show_free
        self._verification_results = (
            list(verification_results) if verification_results is not None else None
        )

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="resume_body"):
            yield Static("Resume run", id="resume_title")
            n = len(self._pending)
            yield Static(
                f"{n} pending task{'s' if n != 1 else ''} to execute",
                classes="muted",
            )

            # Verification summary — shown when verification was performed
            verify_summary = self._format_verify_summary()
            if verify_summary:
                yield Static(verify_summary, classes="verify-summary", markup=False)

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
                "Persistent  (30 retries, 3 retrospective rounds)",
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
            yield Label("Token budget/run (0 = unlimited):")
            starting_budget_run = (
                str(self._config.token_budget_per_run)
                if self._config.token_budget_per_run > 0
                else ""
            )
            yield Input(placeholder="0", id="inp_budget_run", value=starting_budget_run)
            yield Label("Task timeout (0 = unlimited):")
            starting_timeout = (
                str(self._config.task_timeout) if self._config.task_timeout > 0 else ""
            )
            yield Input(placeholder="0", id="inp_task_timeout", value=starting_timeout)
            yield BlankOffCheckbox(
                "Notify on complete  (desktop alert)",
                id="chk_notify_complete",
                value=bool(self._config.notify_on_complete),
            )
            yield BlankOffCheckbox(
                "Notify on fail  (desktop alert)",
                id="chk_notify_fail",
                value=bool(self._config.notify_on_fail),
            )

            yield Static("")
            yield Static(
                "[dim]↑↓ navigate · Space toggle · Enter submit · Esc cancel[/dim]",
                markup=True,
            )
        yield Footer()

    def on_mount(self) -> None:
        # Focus the first actual RadioButton child (not the RadioSet
        # container itself) so up/down move through the form instead of
        # getting trapped on the group widget.
        try:
            first_rb = self.query("#action_set RadioButton").first()
            if first_rb is not None:
                first_rb.focus()
        except Exception as exc:
            logger.debug(f"ResumeScreen initial focus failed: {exc!r}")

    async def _on_key(self, event: Any) -> None:
        """Handle up/down arrows for focus navigation between form fields.

        Up/down arrows move focus between form fields. TextArea keeps arrow
        keys for multi-line cursor movement. Input widgets are single-line
        so up/down always moves focus (left/right still work for cursor).
        """
        key_name = getattr(event, "key", "")

        if key_name in ("up", "down"):
            focused = self.focused
            if isinstance(focused, TextArea):
                return  # let TextArea handle multi-line cursor movement
            if key_name == "up":
                self.action_focus_previous()
            else:
                self.action_focus_next()
            event.stop()
            event.prevent_default()
            return

        await super()._on_key(event)

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
        except Exception as exc:
            logger.debug(f"ResumeScreen action_submit RadioSet query failed: {exc!r}")
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
            except Exception as exc:
                logger.debug(f"ResumeScreen free checkbox query failed: {exc!r}")
                free = False
        persistent = bool(self.query_one("#chk_persistent", Checkbox).value)
        integrity = bool(self.query_one("#chk_integrity", Checkbox).value)

        raw_budget = self.query_one("#inp_budget", Input).value or "0"
        try:
            budget = int(raw_budget.strip() or "0")
        except ValueError:
            budget = 0

        raw_budget_run = self.query_one("#inp_budget_run", Input).value or "0"
        try:
            budget_run = int(raw_budget_run.strip() or "0")
        except ValueError:
            budget_run = 0

        raw_task_timeout = self.query_one("#inp_task_timeout", Input).value or "0"
        try:
            task_timeout = int(raw_task_timeout.strip() or "0")
        except ValueError:
            task_timeout = 0

        notify_complete = bool(self.query_one("#chk_notify_complete", Checkbox).value)
        notify_fail = bool(self.query_one("#chk_notify_fail", Checkbox).value)

        self.dismiss(
            {
                "free": free,
                "persistent": persistent,
                "integrity": integrity,
                "token_budget_per_hour": max(budget, 0),
                "token_budget_per_run": max(budget_run, 0),
                "task_timeout": max(task_timeout, 0),
                "notify_on_complete": notify_complete,
                "notify_on_fail": notify_fail,
                "action": action,
            }
        )

    def _format_tasks(self) -> str:
        lines: list[str] = []
        for task in self._pending[:5]:
            prefix = getattr(task, "prefix", "")
            title = getattr(task, "title", None) or getattr(task, "name", "")
            indicator = self._verify_indicator(prefix)
            lines.append(f"  {indicator}{prefix}  {title}".rstrip())
        if len(self._pending) > 5:
            lines.append(f"  ... and {len(self._pending) - 5} more")
        return "\n".join(lines) if lines else "  (none)"

    def _format_verify_summary(self) -> str:
        """Return a verification summary line, or empty string if no verification.

        Returns a string like "Verification: 2 valid, 1 stale, 1 missing" when
        verification results are available. Returns empty string when verification
        was not performed (disabled or no completed tasks).
        """
        if not self._verification_results:
            return ""
        valid = sum(1 for r in self._verification_results if r.status == "valid")
        stale = sum(1 for r in self._verification_results if r.status == "stale")
        missing = sum(1 for r in self._verification_results if r.status == "missing")
        parts: list[str] = []
        if valid:
            parts.append(f"{valid} valid")
        if stale:
            parts.append(f"{stale} stale")
        if missing:
            parts.append(f"{missing} missing")
        if not parts:
            return ""
        return f"  Verification: {', '.join(parts)}"

    def _verify_indicator(self, task_prefix: str) -> str:
        """Return a color-coded verification indicator for a task prefix.

        Args:
            task_prefix: The task prefix (e.g. "T01").

        Returns:
            A Rich markup string with the indicator, e.g.
            "[green]●[/green] " for valid, "[yellow]●[/yellow] " for stale,
            "[red]●[/red] " for missing, or empty string when not verified.
        """
        if not self._verification_results:
            return ""
        for result in self._verification_results:
            if result.task_id == task_prefix:
                if result.status == "valid":
                    return "[green]●[/green] "
                elif result.status == "stale":
                    return "[yellow]●[/yellow] "
                else:
                    return "[red]●[/red] "
        return ""


# Legacy alias for existing tests referencing ``ResumeApp``.
ResumeApp = ResumeScreen


def run_resume_screen(
    pending_tasks: list[Task],
    config: ArchitectConfig,
    show_free: bool = True,
    verification_results: Sequence[ResumeVerificationResult] | None = None,
) -> dict[str, bool | int | str]:
    """Show the resume screen and return the chosen settings + action.

    Uses the active :class:`ArchitectAppRunner` if one is in flight —
    no new app boot, no alt-screen flash. Falls back to a minimal
    harness when the caller is not inside a runner.

    Args:
        pending_tasks: List of pending Task objects.
        config: Current ArchitectConfig (used for pre-filling settings).
        show_free: Whether to show the Free Tier option.
        verification_results: Optional verification results from
            :func:`~the_architect.core.resume_verification.verify_all_completed_tasks`
            showing which completed tasks are still valid.
    """
    from the_architect.tui.app import run_single_screen

    result = run_single_screen(
        ResumeScreen(
            pending_tasks=pending_tasks,
            config=config,
            show_free=show_free,
            verification_results=verification_results,
        )
    )
    if result is None:
        raise SystemExit(0)
    return result
