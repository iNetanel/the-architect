"""Pause menu overlay shown when the user hits ESC during a run.

During execution and wait screens a stray ESC tap used to either do
nothing (execution) or hard-quit the app (wait). Neither matched the
principle that a long-running task must not be killed by a single
keystroke. This modal overlay gives the user three deliberate choices:

* **Continue** — dismiss the menu and return to the run.
* **Detach** — only meaningful inside a tmux session. Issues
  ``tmux detach-client`` on the current session so the user drops
  back to their shell while the backend keeps running; they can
  reattach later with ``tmux attach``. Outside tmux this option is
  disabled with an explanatory note.
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

import os
import subprocess
from typing import Literal

from loguru import logger
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from the_architect.core.tmux import is_inside_tmux
from the_architect.tui.widgets import MatrixButton

PauseDecision = Literal["continue", "detach", "exit"]


class PauseMenuScreen(ModalScreen[PauseDecision]):
    """Modal confirmation overlay for ESC during an active run.

    Dismisses with one of three decisions. The parent screen (execution
    or wait) listens for that value on ``push_screen`` and acts on it —
    "continue" is a no-op, "detach" either succeeded via tmux or we log
    the failure, and "exit" routes into the same shutdown path as
    Ctrl+C (``App.exit()`` + ``kill_active_subprocesses``).
    """

    # Same visual vocabulary as ModeSelectionScreen / ResumeScreen:
    # ``round $panel`` frame, ``$panel 20%`` background, accent colour
    # reserved for the title so the screen reads as part of the same
    # family rather than a red-alert modal. The matrix-button widgets
    # supply the brand-green touch on their own.
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

    /* Stacked buttons so the focused row clearly highlights as a
       terminal-style menu item. Matches the form layout elsewhere
       in the app. */
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

    # Single-key shortcuts match the button labels. ESC dismisses as
    # "continue" (you already saw the menu; ESC again = never mind).
    # Ctrl+C resolves to "exit" so users who escalate mid-decision
    # still get a clean hard stop. Arrow keys navigate the button
    # list the same way ModeSelectionScreen navigates its checkboxes.
    BINDINGS = [
        Binding("escape", "choose('continue')", "Continue", priority=True),
        Binding("c", "choose('continue')", "Continue"),
        Binding("C", "choose('continue')", "Continue"),
        Binding("d", "choose('detach')", "Detach"),
        Binding("D", "choose('detach')", "Detach"),
        Binding("e", "choose('exit')", "Exit"),
        Binding("E", "choose('exit')", "Exit"),
        Binding("ctrl+c", "choose('exit')", "Exit", priority=True),
        # Arrow navigation through the button list. ``focus_previous``
        # and ``focus_next`` are plain Screen methods, so we need the
        # ``action_*`` shims below to wire them up.
        Binding("up", "focus_previous", "Previous", show=False),
        Binding("down", "focus_next", "Next", show=False),
        # Enter activates whichever button currently has focus —
        # MatrixButton's own binding handles it, but we surface it
        # in the footer hint via this no-op shim.
        Binding("enter", "activate_focused", "Select", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._inside_tmux = is_inside_tmux()

    def compose(self) -> ComposeResult:
        with Vertical(id="pause_body"):
            yield Static("Paused", id="pause_title")
            yield Static(
                "The run is still going. Pick an action — "
                "Ctrl+C anywhere is an immediate hard stop.",
                id="pause_hint",
            )
            with Vertical(id="pause_buttons"):
                yield MatrixButton("Continue", key="C", id="btn_continue")
                # Detach is only meaningful inside a tmux session,
                # but the button is enabled in both cases so the
                # focus ring still lands on it and the user gets a
                # clear, inline reason when they press it outside
                # tmux. Silently disabling + skipping focus was more
                # confusing than informative — users thought the
                # button was "broken" rather than "only works in
                # tmux".
                yield MatrixButton("Detach (tmux)", key="D", id="btn_detach")
                yield MatrixButton("Exit", key="E", id="btn_exit")
            footer_note = (
                "Detach keeps the run going; reattach later with `tmux attach`."
                if self._inside_tmux
                else "Detach only works inside a tmux session."
            )
            yield Static(footer_note, id="pause_footer")

    def on_mount(self) -> None:
        # Open with focus on Continue so the safest option is one
        # Enter away. Users who wanted Exit can type 'E', Tab past,
        # or use the arrow keys.
        try:
            self.query_one("#btn_continue", MatrixButton).focus()
        except Exception:
            pass

    # ── Arrow-key focus navigation ────────────────────────────────────

    def action_focus_previous(self) -> None:
        """Move focus to the previous focusable widget on this screen.

        Same shim pattern as :class:`ModeSelectionScreen` —
        :meth:`Screen.focus_previous` is a plain method, so a
        ``Binding("up", "focus_previous", …)`` silently does nothing
        without this wrapper.
        """
        self.focus_previous()
        # Skip over the disabled detach button if we land on it —
        # users shouldn't be able to sit the focus ring on a button
        # they can't press.
        self._skip_disabled("previous")

    def action_focus_next(self) -> None:
        """Move focus to the next focusable widget on this screen."""
        self.focus_next()
        self._skip_disabled("next")

    def _skip_disabled(self, direction: str) -> None:
        """If focus landed on a disabled :class:`MatrixButton`, skip it."""
        focused = self.focused
        if isinstance(focused, MatrixButton) and focused.is_disabled:
            if direction == "previous":
                self.focus_previous()
            else:
                self.focus_next()

    def action_activate_focused(self) -> None:
        """Enter activates the focused button — delegate to its own action."""
        focused = self.focused
        if isinstance(focused, MatrixButton):
            focused.action_press()

    # ── Button routing ─────────────────────────────────────────────────

    def on_matrix_button_pressed(self, event: MatrixButton.Pressed) -> None:
        """Route MatrixButton presses to the same action the keys use."""
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

        When ``decision == 'detach'`` we first try to actually detach
        from the surrounding tmux session. Two failure modes are
        handled inline rather than dismissed:

        * **Not inside tmux** — we update the footer to explain what
          the user needs to do (launch inside a tmux session) and
          leave the menu open. Silently pretending the click did
          nothing was the confusing behaviour the user reported.
        * **tmux detach-client failed** — log the underlying error
          and surface a short inline message. Again, leave the menu
          open so the user can pick a different action.

        On success the tmux client exits and this process keeps
        running in the background — the dismissed value doesn't
        really matter at that point because the terminal is gone.
        """
        if decision == "detach":
            if not self._inside_tmux:
                self._show_inline_error(
                    "Detach only works inside tmux. "
                    "Run `tmux new -s arch 'architect'` to enable it."
                )
                return
            if not _tmux_detach_client():
                self._show_inline_error(
                    "tmux detach-client failed. See logs under .architect/logs for details."
                )
                return
            self.dismiss("detach")
            return
        # continue / exit — pass through verbatim.
        if decision in ("continue", "exit"):
            self.dismiss(decision)  # type: ignore[arg-type]
            return
        self.dismiss("continue")

    def _show_inline_error(self, message: str) -> None:
        """Replace the footer hint with a short error, highlighted.

        The footer is the natural place to surface a "that action
        can't be taken right now" message because it already explains
        what Detach does — replacing it keeps the layout stable and
        doesn't push the buttons around.
        """
        try:
            footer = self.query_one("#pause_footer", Static)
            footer.update(message)
            footer.add_class("-error")
        except Exception:
            pass


def _tmux_detach_client() -> bool:
    """Issue ``tmux detach-client`` for the current session.

    Runs ``tmux detach-client`` with no explicit target — tmux picks
    the current client from the inherited ``$TMUX`` env var. Returns
    True on success, False on any failure (missing tmux, command
    non-zero, no ``TMUX`` env var). Never raises — failure is logged
    and the caller falls back to a no-op "continue".
    """
    tmux_env = os.environ.get("TMUX", "")
    if not tmux_env:
        return False
    try:
        result = subprocess.run(
            ["tmux", "detach-client"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if result.returncode != 0:
            logger.warning(
                f"tmux detach-client failed (exit {result.returncode}): {result.stderr.strip()}"
            )
            return False
        return True
    except FileNotFoundError:
        logger.warning("tmux binary not found on PATH; cannot detach")
        return False
    except subprocess.TimeoutExpired:
        logger.warning("tmux detach-client timed out")
        return False
    except Exception as exc:
        logger.warning(f"tmux detach-client raised: {exc!r}")
        return False
