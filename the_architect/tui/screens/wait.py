"""Reusable wait screen for long-running agent work.

Two entry points are exported:

1. :class:`WaitScreen` — a Textual ``Screen`` that can be pushed onto
   an already running app (for example the execution screen during a
   retrospective or reassessment).
2. :class:`WaitApp` — a standalone ``App`` that wraps ``WaitScreen``.
   Used by :func:`the_architect.tui.session.tui_wait_session` when no
   main app is currently running (for example during planning before
   the execution screen has been launched).

Both surfaces show the same three elements:

1. An animated title line (Matrix-rain glyph + label, branded after
   The Architect character from The Matrix)
2. A free-form detail block (e.g. current step, model, tokens)
3. A tail of recent log lines (bounded)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from loguru import logger
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, RichLog, Static

from the_architect.tui.constants import ANIMATION_TICK_INTERVAL
from the_architect.tui.widgets import MatrixRain, next_matrix_frame

if TYPE_CHECKING:
    from textual.timer import Timer


class WaitScreen(Screen[None]):
    """Wait screen that can be pushed onto a running app."""

    DEFAULT_CSS = """
    WaitScreen {
        layout: vertical;
    }

    #wait_body {
        height: 1fr;
        padding: 1 2;
    }

    #wait_title { color: $accent; text-style: bold; }
    #wait_rain_row {
        width: 100%;
        height: __MATRIX_RAIN_ROWS__;
        align-horizontal: center;
        margin: 1 0 0 0;
    }
    #wait_detail { color: $text-muted; padding: 1 0; }

    RichLog { border: round $panel; height: 1fr; }
    """.replace("__MATRIX_RAIN_ROWS__", str(MatrixRain.ROWS))

    # Retained for backward compatibility with tests that reference
    # ``WaitScreen.SPINNER_FRAMES``. No longer used by the live
    # animation — see :func:`the_architect.tui.widgets.next_matrix_frame`.
    SPINNER_FRAMES: ClassVar[tuple[str, ...]] = (
        "⠋",
        "⠙",
        "⠹",
        "⠸",
        "⠼",
        "⠴",
        "⠦",
        "⠧",
        "⠇",
        "⠏",
    )

    BINDINGS = [
        # ESC opens the pause menu (Continue / Detach / Exit). A
        # planning or retrospective run can easily be 5-10 minutes of
        # work, so a stray ESC must not silently tear it down. Ctrl+C
        # remains wired at the app level as the immediate hard stop.
        # priority=True so ESC fires before any focused child (e.g. a
        # spinner or log widget) blurs itself and swallows the key.
        Binding("escape", "pause_menu", "Pause menu", priority=True),
        Binding("ctrl+c", "quit", "Quit"),
    ]

    def __init__(self, title: str) -> None:
        super().__init__()
        self._title = title
        self._detail = ""
        self._frame_index = 0
        self._current_frame = next_matrix_frame(self._frame_index)
        # Buffer early provider lines/details that may arrive before the
        # screen has fully mounted. This is especially important for the
        # standalone planning WaitApp path, where the provider can start
        # streaming immediately after the app thread starts.
        self._pending_detail: str | None = None
        self._pending_log_lines: list[str] = []
        # Timer handle for the animated spinner — stopped on unmount
        # to prevent leaks.
        self._timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="wait_body"):
            yield Static(self._render_title(), id="wait_title")
            with Horizontal(id="wait_rain_row"):
                yield MatrixRain(id="wait_rain")
            yield Static("", id="wait_detail")
            with VerticalScroll():
                yield RichLog(
                    id="wait_log",
                    highlight=False,
                    markup=False,
                    wrap=True,
                    auto_scroll=True,
                    max_lines=500,
                )
        yield Footer()

    def on_mount(self) -> None:
        # 10 FPS spinner; matches the feel of the inline scanner.
        self._timer = self.set_interval(ANIMATION_TICK_INTERVAL, self._tick_spinner)
        # Disable focus on the log so it never shows a blinking cursor.
        try:
            self.query_one("#wait_log", RichLog).can_focus = False
        except Exception as exc:
            logger.debug(f"WaitScreen log can_focus disable failed: {exc!r}")
        self.call_after_refresh(self._flush_pending)

    def on_hide(self) -> None:
        """Pause the spinner animation while the screen is hidden behind an overlay."""
        if self._timer is not None:
            self._timer.pause()

    def on_resume(self) -> None:
        """Restart the spinner animation when the screen becomes visible again."""
        if self._timer is not None:
            self._timer.resume()

    def on_unmount(self) -> None:
        """Stop the spinner timer to prevent leaks after removal."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    # ── Actions ────────────────────────────────────────────────────────

    def action_pause_menu(self) -> None:
        """Open the pause menu when the user hits ESC during a wait.

        Delegates to the hosting :class:`ArchitectApp` so the overlay
        is owned by the app and only one instance can be visible at a
        time. When the wait surface is hosted by a standalone
        :class:`WaitApp` (legacy path without an :class:`ArchitectApp`
        in flight), the app side still exposes ``show_pause_menu``
        via the same contract — see :meth:`WaitApp.show_pause_menu`.
        """
        try:
            self.app.show_pause_menu()  # type: ignore[attr-defined]
        except Exception as exc:
            logger.debug(f"WaitScreen pause menu dispatch failed: {exc!r}")

    # ── Public API (safe from any thread via call_from_thread) ─────────

    def set_title(self, title: str) -> None:
        self._title = title
        try:
            self.query_one("#wait_title", Static).update(self._render_title())
        except Exception as exc:
            logger.debug(f"WaitScreen set_title widget update failed: {exc!r}")

    def set_detail(self, detail: str) -> None:
        self._detail = detail
        try:
            self.query_one("#wait_detail", Static).update(detail)
        except Exception as exc:
            logger.debug(f"WaitScreen set_detail widget update failed: {exc!r}")
            self._pending_detail = detail

    def append_log(self, line: str) -> None:
        try:
            log = self.query_one("#wait_log", RichLog)
            log.write(line)
        except Exception as exc:
            logger.debug(f"WaitScreen append_log widget write failed (buffering): {exc!r}")
            self._pending_log_lines.append(line)

    # ── Internal ──────────────────────────────────────────────────────

    def _tick_spinner(self) -> None:
        # Advance the Matrix-rain frame index and regenerate the glyph.
        # Using a deterministic frame function keeps the animation in
        # sync with other Matrix-rain surfaces (e.g. the splash).
        self._frame_index += 1
        self._current_frame = next_matrix_frame(self._frame_index)
        try:
            self.query_one("#wait_title", Static).update(self._render_title())
        except Exception as exc:
            logger.debug(f"WaitScreen tick spinner update failed: {exc!r}")

    def _render_title(self) -> str:
        return f"{self._current_frame}  {self._title}"

    def _flush_pending(self) -> None:
        if self._pending_detail is not None:
            try:
                self.query_one("#wait_detail", Static).update(self._pending_detail)
            except Exception as exc:
                logger.debug(f"WaitScreen flush pending detail failed: {exc!r}")
            else:
                self._pending_detail = None

        if not self._pending_log_lines:
            return

        try:
            log = self.query_one("#wait_log", RichLog)
        except Exception as exc:
            logger.debug(f"WaitScreen flush pending log query failed: {exc!r}")
            return

        for line in self._pending_log_lines:
            log.write(line)
        self._pending_log_lines.clear()


class WaitApp(App[None]):
    """Standalone wrapper around :class:`WaitScreen`.

    Used when no main app is running — for example during early
    planning before :class:`ArchitectApp` is launched. The methods
    proxy straight through to the underlying screen.
    """

    CSS = """
    Screen { background: $surface; }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
    ]

    def __init__(self, title: str) -> None:
        super().__init__()
        self._screen = WaitScreen(title=title)

    def on_mount(self) -> None:
        from the_architect.tui.app import apply_architect_theme

        apply_architect_theme(self)
        self.push_screen(self._screen)

    def on_unmount(self) -> None:
        """Restore terminal modes whenever the standalone wait app exits."""
        try:
            from the_architect.tui.terminal import restore_terminal_input_modes

            restore_terminal_input_modes()
        except Exception as exc:
            logger.debug(f"WaitApp terminal restore on unmount failed: {exc!r}")

    # Proxy convenience methods so existing callers keep working.
    def set_title(self, title: str) -> None:
        self._screen.set_title(title)

    def set_detail(self, detail: str) -> None:
        self._screen.set_detail(detail)

    def append_log(self, line: str) -> None:
        self._screen.append_log(line)

    def show_pause_menu(self) -> None:
        """Push the pause-menu overlay on top of the wait screen.

        Mirrors :meth:`ArchitectApp.show_pause_menu` so the wait
        surface's ESC binding works identically whether it's hosted
        by the main app (overlay path) or by a dedicated
        :class:`WaitApp` (standalone path). "Exit" routes through
        :meth:`App.exit`, which the caller's ``finally`` block uses
        to stop any registered provider subprocess.
        """
        if getattr(self, "_pause_menu_visible", False):
            return
        from the_architect.tui.screens.pause import PauseMenuScreen

        self._pause_menu_visible = True

        def _on_dismiss(decision: str | None) -> None:
            self._pause_menu_visible = False
            if decision == "exit":
                try:
                    self.exit()
                except Exception as exc:
                    logger.debug(f"WaitApp pause menu exit failed: {exc!r}")

        try:
            self.push_screen(PauseMenuScreen(), _on_dismiss)
        except Exception as exc:
            logger.debug(f"WaitApp push_pause_menu failed: {exc!r}")
            self._pause_menu_visible = False

    # Back-compat accessors used in existing tests.
    @property
    def _current_frame(self) -> str:
        return self._screen._current_frame

    def _tick_spinner(self) -> None:
        self._screen._tick_spinner()
