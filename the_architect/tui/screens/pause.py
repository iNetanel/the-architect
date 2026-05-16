"""Pause menu overlay shown when the user hits ESC during a run.

During execution and wait screens a stray ESC tap used to either do
nothing (execution) or hard-quit the app (wait). Neither matched the
principle that a long-running task must not be killed by a single
keystroke. This modal overlay gives the user three deliberate choices:

* **Continue** — dismiss the menu and return to the run.
* **Detach** — exit the TUI and free the terminal while the worker
  keeps running in the background (headless, writing to
  ``.architect/logs/``).  Always available — the worker thread is
  always non-daemon.  Reconnect with ``architect monitor``.
* **Exit** — hard-kill the run (same as Ctrl+C): tears down the app
  and terminates any child provider subprocess.

Ctrl+C remains a direct hard-kill without going through this menu —
that matches terminal convention and the user's stated preference for
Ctrl+C as an immediate stop.

Visual style: matches the rest of the app's form screens
(:class:`ModeSelectionScreen`, :class:`ResumeScreen`, etc.) — a
centred ``round $panel`` frame on a ``$panel 20%`` background with
``$accent`` only on the title. The buttons themselves are
:class:`~the_architect.tui.widgets.MatrixButton` so they read as
terminal chrome rather than 3D web buttons.
"""

from __future__ import annotations

from typing import Literal

from loguru import logger
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from the_architect.tui.runner import request_tui_detach
from the_architect.tui.widgets import MatrixButton

PauseDecision = Literal["continue", "detach", "exit"]


class PauseMenuScreen(ModalScreen[PauseDecision]):
    """Modal confirmation overlay for ESC during an active run.

    Dismisses with one of three decisions. The parent screen (execution
    or wait) listens for that value on ``push_screen`` and acts on it —
    "continue" is a no-op, "detach" closes the TUI while the worker
    keeps running, and "exit" routes into the same shutdown path as
    Ctrl+C (``App.exit()`` + ``kill_active_subprocesses``).
    """

    DEFAULT_CSS = """
    PauseMenuScreen {
        align: center middle;
    }

    #pause_body {
        width: 56;
        height: auto;
        padding: 1 2;
        border: round $panel;
        background: $panel 20%;
    }

    #pause_title { color: $accent; text-style: bold; }
    #pause_hint { color: $text-muted; padding: 1 0; }

    #pause_buttons {
        width: 100%;
        height: auto;
        padding: 0 0 1 0;
    }

    MatrixButton {
        width: 100%;
        margin: 0;
    }

    #pause_footer { color: $text-muted; }
    #pause_footer.-error { color: $warning; text-style: bold; }
    """

    BINDINGS = [
        Binding("escape", "choose('continue')", "Continue", priority=True),
        Binding("c", "choose('continue')", "Continue"),
        Binding("C", "choose('continue')", "Continue"),
        Binding("d", "choose('detach')", "Detach"),
        Binding("D", "choose('detach')", "Detach"),
        Binding("e", "choose('exit')", "Exit"),
        Binding("E", "choose('exit')", "Exit"),
        Binding("ctrl+c", "choose('exit')", "Exit", priority=True),
        Binding("up", "focus_previous", "Previous", show=False),
        Binding("down", "focus_next", "Next", show=False),
        Binding("enter", "activate_focused", "Select", show=False),
    ]

    def compose(self) -> ComposeResult:
        """Compose the pause menu."""
        with Vertical(id="pause_body"):
            yield Static("Paused", id="pause_title")
            yield Static(
                "The run is still going. Pick an action — "
                "Ctrl+C anywhere is an immediate hard stop.",
                id="pause_hint",
            )
            with Vertical(id="pause_buttons"):
                yield MatrixButton("Continue", key="C", id="btn_continue")
                yield MatrixButton("Detach", key="D", id="btn_detach")
                yield MatrixButton("Exit", key="E", id="btn_exit")
            yield Static(
                "Detach frees your terminal — run continues in background. "
                "Reconnect with: architect monitor",
                id="pause_footer",
            )

    def on_mount(self) -> None:
        """Focus Continue so the safest option is one Enter away."""
        try:
            self.query_one("#btn_continue", MatrixButton).focus()
        except Exception:
            pass

    def action_focus_previous(self) -> None:
        """Move focus to the previous focusable widget."""
        self.focus_previous()

    def action_focus_next(self) -> None:
        """Move focus to the next focusable widget."""
        self.focus_next()

    def action_activate_focused(self) -> None:
        """Enter activates the focused button."""
        focused = self.focused
        if isinstance(focused, MatrixButton):
            focused.action_press()

    def on_matrix_button_pressed(self, event: MatrixButton.Pressed) -> None:
        """Route MatrixButton presses to action_choose."""
        mapping = {
            "btn_continue": "continue",
            "btn_detach": "detach",
            "btn_exit": "exit",
        }
        decision = mapping.get(event.button.id or "")
        if decision is not None:
            self.action_choose(decision)

    def action_choose(self, decision: str) -> None:
        """Dismiss the overlay with the chosen decision.

        When ``decision == 'detach'``:
        Calls :func:`~the_architect.tui.runner.request_tui_detach` to
        close the TUI while leaving the worker running, then dismisses.
        The worker thread is always non-daemon so it always survives TUI
        exit — detach is unconditionally available.
        """
        if decision == "detach":
            ok = request_tui_detach()
            if not ok:
                self._show_inline_error(
                    "Could not detach — no active runner. See logs for details."
                )
                logger.warning("request_tui_detach returned False from pause menu")
                return
            self.dismiss("detach")
            return

        if decision in ("continue", "exit"):
            self.dismiss(decision)  # type: ignore[arg-type]
            return
        self.dismiss("continue")

    def _show_inline_error(self, message: str) -> None:
        """Replace the footer hint with a short error."""
        try:
            footer = self.query_one("#pause_footer", Static)
            footer.update(message)
            footer.add_class("-error")
        except Exception:
            pass
