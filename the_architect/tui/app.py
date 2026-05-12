"""Top-level Textual application for The Architect.

The app is the single entry point for the TUI. Every screen — provider
selection, goal, scope, mode, resume, planning/wait, execution, review
overlays — lives inside one persistent :class:`ArchitectApp` instance.

Two shapes of caller:

1. **Legacy per-screen callers** (pre-Phase-16 architecture). Use
   :func:`run_single_screen` — boots a fresh app, pushes one screen,
   exits when it dismisses. Deprecated but kept as a stepping stone so
   the existing sequential CLI flow in ``cli.py`` still works while the
   full orchestration refactor lands.

2. **Persistent-app callers** (Phase 16 architecture). Create one
   :class:`ArchitectApp`, start it with ``app.run()`` on the main
   thread, and drive the sequence from a worker thread that calls
   :meth:`ArchitectApp.push_and_wait` for each stage. The app stays
   alive from pre-run through execution, with a consistent title,
   header, footer, and no alt-screen flicker between stages.
"""

from __future__ import annotations

import threading
import time
from typing import Any, TypeVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.theme import Theme
from textual.widgets import Footer, Header, Static

from the_architect.tui.screens.execution import ExecutionScreen
from the_architect.tui.screens.wait import WaitScreen
from the_architect.tui.widgets import MatrixRain
from the_architect.version import __full_version__ as _ARCHITECT_FULL_VERSION


def _architect_header_version() -> str:
    """Return the version string shown in the Textual Header."""
    return f"v{_ARCHITECT_FULL_VERSION}"


T = TypeVar("T")

# Brand colour carried forward from the pre-TUI (Rich/questionary) era,
# where it was defined as ``ARCHITECT_GREEN = "#7cc800"``. Used as the
# accent for titles, spinners, header highlights, and any $accent
# reference in screen CSS. Replaces Textual's default orange accent.
ARCHITECT_GREEN = "#7cc800"
# Darker shade for secondary/muted elements and hover gradients.
ARCHITECT_GREEN_DARK = "#4a7600"

ARCHITECT_THEME = Theme(
    name="architect-dark",
    # primary drives block-cursor-background (ListView/RadioSet/DataTable
    # row highlights) and the Tabs underline bar. Setting it to the brand
    # green means every interactive widget hover/selection is green, not
    # the default Textual blue.
    primary=ARCHITECT_GREEN,
    secondary=ARCHITECT_GREEN_DARK,
    # Accent → brand green. Every $accent reference across the app renders
    # in the same vivid green — titles, spinners, header highlights, etc.
    accent=ARCHITECT_GREEN,
    # Keep the default textual-dark warning orange so warning titles
    # still read as warnings and aren't visually conflated with the
    # brand colour.
    warning="#ffa62b",
    error="#ba3c5b",
    # Keep textual-dark's success green for on-state markers (checkbox
    # "X", success banners) — slightly different hue from the brand
    # green on purpose, so "selected" and "branded" read distinctly.
    success="#4EBF71",
    foreground="#e0e0e0",
    dark=True,
)


def apply_architect_theme(app: App[Any]) -> None:
    """Register and activate the Architect branded theme on ``app``.

    Standalone :class:`~textual.app.App` subclasses (ConfigApp, ListApp,
    CircuitApp, etc.) call this from their ``on_mount`` so they share the
    same green colour palette as the main :class:`ArchitectApp`. Never
    raises — any failure falls back to the default Textual theme silently.
    """
    try:
        app.register_theme(ARCHITECT_THEME)
        app.theme = ARCHITECT_THEME.name
    except Exception:
        pass


# SplashScreen — the centered startup card shown while the app boots.
#
# This is intentionally a separate class from WaitScreen.  WaitScreen is a
# full-screen log-viewer layout (title at top-left, rain strip, scrolling log
# below) designed for planning/execution waits where live output matters.
# SplashScreen is a small centered card — visually equivalent to the other
# pre-run dialog screens — so the user immediately sees something branded and
# alive even before the worker thread has started.
class SplashScreen(Screen[None]):
    """Centered animated startup card shown while the app is booting.

    Displays the app name, a Matrix digital-rain block, and a short
    subtitle.  The layout mirrors :class:`ModeSelectionScreen` — the
    Screen itself carries ``align: center middle`` so the single body
    card is placed in the centre of the viewport regardless of terminal
    size, with the docked Header and Footer framing it.
    """

    DEFAULT_CSS = """
    SplashScreen {
        align: center middle;
    }

    #splash_body {
        width: 48;
        height: 14;
        padding: 1 2;
        border: round $panel;
        background: $panel 20%;
    }

    #splash_title {
        width: 100%;
        color: $accent;
        text-style: bold;
        text-align: center;
    }

    #splash_rain_row {
        width: 100%;
        height: __MATRIX_RAIN_ROWS__;
        align-horizontal: center;
        margin: 1 0 0 0;
    }

    #splash_subtitle {
        width: 100%;
        color: $text-muted;
        text-align: center;
        margin: 1 0 0 0;
        padding: 0;
    }
    """.replace("__MATRIX_RAIN_ROWS__", str(MatrixRain.ROWS))

    def __init__(self, subtitle: str = "Starting up…") -> None:
        super().__init__()
        self._subtitle = subtitle

    def compose(self) -> ComposeResult:
        """Compose the centered splash card."""
        yield Header()
        with Vertical(id="splash_body"):
            yield Static("The Architect", id="splash_title")
            with Horizontal(id="splash_rain_row"):
                yield MatrixRain(id="splash_rain")
            yield Static(self._subtitle, id="splash_subtitle")
        yield Footer()

    def set_subtitle(self, subtitle: str) -> None:
        """Update the subtitle text. Safe to call from the UI thread."""
        self._subtitle = subtitle
        try:
            self.query_one("#splash_subtitle", Static).update(subtitle)
        except Exception:
            pass


class ArchitectApp(App[None]):
    """The Architect — single persistent Textual application.

    Owns every TUI screen for the run lifecycle. Screens are pushed
    and dismissed; the app itself never exits until the run finishes.

    Phase 18 adds run-scoped status that's visible on every screen via
    :attr:`App.sub_title`, which Textual's ``Header`` widget renders
    next to the app title. Callers update it through
    :meth:`set_status` from any thread.
    """

    TITLE = "The Architect"

    CSS = """
    Screen { background: $surface; }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("q", "quit", "Quit"),
        Binding("question_mark", "help", "Help"),
    ]

    def __init__(self, *, initial_screen: Screen[Any] | None = None) -> None:
        """Create the app.

        Args:
            initial_screen: The first screen to push on mount. Defaults
                to a :class:`SplashScreen` centered startup card so the
                user sees a branded, animated surface immediately while
                the worker thread is starting up.
        """
        super().__init__()
        self._execution_screen: ExecutionScreen | None = None
        self._wait_screen: WaitScreen | None = None
        self._pause_menu_visible: bool = False
        self._shutdown_started: bool = False
        self._initial_screen: Screen[Any] = initial_screen or SplashScreen()
        # Wall-clock time when the splash was first painted. Set in
        # on_mount so push_and_wait can enforce a minimum display window.
        self._splash_shown_at: float = 0.0
        # Minimum seconds the SplashScreen must stay visible before any
        # other screen is pushed on top of it.
        self._splash_min_seconds: float = 1.5
        # Set when the app exits so the splash minimum-hold sleep in
        # push_and_wait wakes up immediately instead of blocking exit.
        self._quit_event: threading.Event = threading.Event()

    def on_mount(self) -> None:
        # Register and activate The Architect's branded theme before
        # pushing the first screen so every $accent reference is green
        # from frame one — no orange flash on boot.
        try:
            self.register_theme(ARCHITECT_THEME)
            self.theme = ARCHITECT_THEME.name
        except Exception:
            # Fall back silently to the default theme if registration
            # fails (e.g. a future Textual API change) — the app still
            # works, it just looks orange again.
            pass
        # Append the version to the reactive `title` so the Header
        # shows "The Architect v1.2.0 (build 10095)" on every screen.
        # We deliberately do NOT touch the TITLE class attribute — a
        # test pins TITLE == "The Architect" exactly, and `sub_title`
        # is already claimed by set_status() for run-scoped updates.
        try:
            self.title = f"{self.TITLE}  {_architect_header_version()}"
        except Exception:
            pass
        self.push_screen(self._initial_screen)
        # Record when the splash was painted so push_and_wait can
        # enforce the minimum display window from the worker thread.
        if isinstance(self._initial_screen, SplashScreen):
            self._splash_shown_at = time.monotonic()

    # ── Run-scoped status (Phase 18) ───────────────────────────────────

    async def action_quit(self) -> None:
        """Show a shutdown splash, stop providers, then exit cleanly."""
        self.begin_shutdown()

    def begin_shutdown(self) -> None:
        """Show the branded shutdown screen while provider cleanup runs.

        Confirmed exits from the pause menu and Ctrl+C both route here.
        The Textual app stays open on the animated splash while active
        provider subprocesses are killed on a background thread. Only
        after cleanup and a short minimum display window do we close the
        alternate screen, avoiding a blank terminal during teardown.
        """
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self._quit_event.set()
        started_at = time.monotonic()

        try:
            # Walk the stack and pop everything above the SplashScreen.
            # If there is no SplashScreen in the stack (e.g. during execution)
            # just push a fresh one so there's always something to show.
            while len(self.screen_stack) > 1:
                if isinstance(self.screen_stack[-1], SplashScreen):
                    break
                self.screen_stack[-1].dismiss()

            splash: SplashScreen | None = None
            for s in self.screen_stack:
                if isinstance(s, SplashScreen):
                    splash = s
                    break

            if splash is None:
                splash = SplashScreen(subtitle="Shutting down…")
                self.push_screen(splash)
            else:
                splash.set_subtitle("Shutting down…")

        except Exception:
            pass

        def _cleanup_then_exit() -> None:
            try:
                from the_architect.core.runner import kill_active_subprocesses

                kill_active_subprocesses()
            except Exception:
                pass

            remaining = 1.0 - (time.monotonic() - started_at)
            if remaining > 0:
                time.sleep(remaining)
            try:
                loop = getattr(self, "_loop", None)
                if loop is not None and not loop.is_closed():
                    loop.call_soon_threadsafe(self.exit)
            except Exception:
                pass

        threading.Thread(
            target=_cleanup_then_exit,
            name="architect-shutdown-cleanup",
            daemon=True,
        ).start()

    @property
    def shutdown_started(self) -> bool:
        """True once a user-requested shutdown is already in progress."""
        return self._shutdown_started

    def set_status(self, text: str) -> None:
        """Update the app-wide status line shown in every screen header.

        Safe to call from any thread. Setting an empty string clears
        the sub-title so the header only shows the app title.
        """
        self._thread_safe_call(self._set_status_sync, text)

    def _set_status_sync(self, text: str) -> None:
        self.sub_title = text or ""

    def _thread_safe_call(self, fn: Any, *args: Any, **kwargs: Any) -> None:
        """Run ``fn`` on the app's event loop, from any thread.

        Prefers :meth:`App.call_from_thread` when invoked from a
        foreign thread. When already on the event loop thread (e.g.
        inside ``run_test``), calls ``fn`` directly so the helper is
        usable from unit tests. Silently swallows errors when the app
        isn't running yet.
        """
        try:
            self.call_from_thread(fn, *args, **kwargs)
        except RuntimeError:
            # call_from_thread refuses same-thread calls — just run it.
            try:
                fn(*args, **kwargs)
            except Exception:
                pass
        except Exception:
            # App not running yet.
            try:
                fn(*args, **kwargs)
            except Exception:
                pass

    # ── Worker-thread screen orchestration ─────────────────────────────

    def push_and_wait(self, screen: Screen[T]) -> T | None:
        """Push a screen and block until it dismisses. Thread-safe.

        This is the correct way for a worker thread to drive a
        sequence of screens: push → wait → pop → push next. The app
        stays alive the entire time. Returns the value the screen
        passed to ``self.dismiss()``, or ``None`` on cancel.

        Phase 21: consecutive pre-run screens use
        :meth:`switch_and_wait` instead so the previous screen is
        *replaced* rather than popped back to the splash. Use this
        method for overlays that should stack on top of the current
        content (wait overlays during execution).
        """
        result: dict[str, Any] = {"value": None}
        done = threading.Event()

        def _on_dismiss(value: Any) -> None:
            result["value"] = value
            done.set()

        def _push() -> None:
            self.push_screen(screen, _on_dismiss)

        # If the splash is still in its minimum display window, wait
        # on the worker thread until the window expires. Using an Event
        # (rather than time.sleep) means Ctrl+C / app.exit() wakes this
        # immediately so there is no blank-screen hang after quitting.
        driver_name = type(getattr(self, "_driver", None)).__name__
        if self._splash_shown_at > 0 and driver_name != "HeadlessDriver":
            elapsed = time.monotonic() - self._splash_shown_at
            remaining = self._splash_min_seconds - elapsed
            if remaining > 0:
                self._quit_event.wait(timeout=remaining)
            self._splash_shown_at = 0.0  # only gate the first push

        self.call_from_thread(_push)
        done.wait()
        return result["value"]  # type: ignore[no-any-return]

    def switch_and_wait(self, screen: Screen[T]) -> T | None:
        """Replace the active screen and block until the new one dismisses.

        This is a thin wrapper around :meth:`push_and_wait` — the
        ``switch_screen`` semantics we originally used required a
        fragile monkey-patch of ``Screen.dismiss`` and caused
        ``ScreenStackError: No screens on stack`` when the last
        pre-run prompt dismissed. The animated startup WaitScreen sits
        underneath all prompts, so a brief revisit between dismiss and
        next push looks like a loading moment rather than a flicker.
        """
        return self.push_and_wait(screen)

    # ── Execution screen hooks ─────────────────────────────────────────

    def _ensure_execution_screen(self) -> ExecutionScreen:
        """Create and switch to the execution screen if not already active.

        When the execution screen doesn't exist yet, or isn't the
        currently displayed screen, swap it in via ``switch_screen``
        so callers can immediately query its widgets.
        """
        if self._execution_screen is None:
            self._execution_screen = ExecutionScreen()
        return self._execution_screen

    def switch_to_execution(self) -> None:
        """Replace the current screen with the execution (tabbed) screen.

        Safe from any thread. Called when the worker transitions from
        pre-run / planning into task execution.
        """

        def _switch() -> None:
            screen = self._ensure_execution_screen()
            self.switch_screen(screen)

        self._thread_safe_call(_switch)

    def push_output_line(self, line: str) -> None:
        """Forward a streamed provider line to the execution screen.

        Uses a **non-blocking** fire-and-forget dispatch so the caller
        (typically the provider stdout reader coroutine on the worker
        thread) is never stalled waiting for the Textual event loop to
        process each individual line.

        Using the blocking :meth:`call_from_thread` path here caused a
        5-second ``asyncio.wait_for`` timeout in :func:`stream_provider`
        to fire before the reader task finished: every ``write_line``
        call blocked the worker-thread event loop for a Textual
        round-trip, so 100+ output lines × ~50 ms each easily exceeded
        the timeout and the remaining lines were silently discarded.
        """
        if self._loop is None:
            # App not running yet — buffer through the sync path.
            self._thread_safe_call(self._push_output_line_sync, line)
            return
        if self._thread_id == threading.get_ident():
            # Same thread as the event loop — call directly (e.g. tests).
            self._push_output_line_sync(line)
            return
        try:
            self._loop.call_soon_threadsafe(self._push_output_line_sync, line)
        except RuntimeError:
            # Loop is closed or not running; fall back to blocking path.
            self._thread_safe_call(self._push_output_line_sync, line)

    def _push_output_line_sync(self, line: str) -> None:
        screen = self._ensure_execution_screen()
        screen.push_output_line(line)

    def push_event_line(self, event: str, data: dict[str, object] | None = None) -> None:
        """Forward an execution event to the execution screen."""
        self._thread_safe_call(self._push_event_line_sync, event, data)

    def _push_event_line_sync(self, event: str, data: dict[str, object] | None = None) -> None:
        screen = self._ensure_execution_screen()
        screen.push_event_line(event, data)

    def update_footer(self, text: str) -> None:
        """Update the footer status line."""
        self._thread_safe_call(self._update_footer_sync, text)

    def _update_footer_sync(self, text: str) -> None:
        screen = self._ensure_execution_screen()
        screen.update_footer(text)

    def update_details(self, **fields: str) -> None:
        """Update the Progress tab with merged run metadata."""
        self._thread_safe_call(self._update_details_sync, **fields)

    def _update_details_sync(self, **fields: str) -> None:
        screen = self._ensure_execution_screen()
        screen.update_details(**fields)

    def update_progress_tasks(self, tasks: list[dict[str, str]]) -> None:
        """Update the Progress tab's task overview."""
        self._thread_safe_call(self._update_progress_tasks_sync, tasks)

    def _update_progress_tasks_sync(self, tasks: list[dict[str, str]]) -> None:
        screen = self._ensure_execution_screen()
        screen.update_progress_tasks(tasks)

    def update_execution_settings(self, settings: dict[str, str]) -> None:
        """Update the execution Settings tab."""
        self._thread_safe_call(self._update_execution_settings_sync, settings)

    def _update_execution_settings_sync(self, settings: dict[str, str]) -> None:
        screen = self._ensure_execution_screen()
        screen.update_settings(settings)

    # ── Wait screen overlay (planning / retrospective / reassessment) ──

    def show_wait(self, title: str, detail: str = "") -> None:
        """Push a wait-screen overlay onto the app.

        Called from the worker thread. Safe to call multiple times —
        only one wait screen is active at a time.
        """
        self._thread_safe_call(self._show_wait_sync, title, detail)

    def _show_wait_sync(self, title: str, detail: str) -> None:
        if self._wait_screen is not None:
            self._wait_screen.set_title(title)
            if detail:
                self._wait_screen.set_detail(detail)
            return
        self._wait_screen = WaitScreen(title=title)
        self.push_screen(self._wait_screen)
        if detail:
            self.call_after_refresh(self._wait_screen.set_detail, detail)

    def update_wait(self, title: str | None = None, detail: str | None = None) -> None:
        """Update the currently visible wait screen (if any)."""
        self._thread_safe_call(self._update_wait_sync, title, detail)

    def _update_wait_sync(self, title: str | None, detail: str | None) -> None:
        if self._wait_screen is None:
            return
        if title is not None:
            self._wait_screen.set_title(title)
        if detail is not None:
            self._wait_screen.set_detail(detail)

    def append_wait_log(self, line: str) -> None:
        """Append a line to the wait screen's log tail (if visible).

        Uses ``call_soon_threadsafe`` for non-blocking dispatch from the
        worker thread — same reasoning as :meth:`push_output_line`.
        """
        if self._loop is None:
            self._thread_safe_call(self._append_wait_log_sync, line)
            return
        if self._thread_id == threading.get_ident():
            self._append_wait_log_sync(line)
            return
        try:
            self._loop.call_soon_threadsafe(self._append_wait_log_sync, line)
        except RuntimeError:
            self._thread_safe_call(self._append_wait_log_sync, line)

    def _append_wait_log_sync(self, line: str) -> None:
        if self._wait_screen is None:
            return
        self._wait_screen.append_log(line)

    def hide_wait(self) -> None:
        """Hide the wait overlay without allowing the app to exit.

        During Infinite Loop, planning for the next iteration can run after the
        previous execution screen has been replaced by wait overlays. Popping the
        current wait screen may empty Textual's screen stack, which exits the app
        and kills the CLI flow before the newly planned tasks execute. Replacing
        the wait screen with the execution screen keeps the persistent TUI alive.
        """
        self._thread_safe_call(self._hide_wait_sync)

    def _hide_wait_sync(self) -> None:
        if self._wait_screen is None:
            return
        try:
            if self.screen is self._wait_screen:
                self.switch_screen(self._ensure_execution_screen())
        except Exception:
            pass
        self._wait_screen = None

    # ── Success screen ─────────────────────────────────────────────────

    def show_success(
        self,
        results: list[Any],
        total_duration: float,
        total_tokens: Any,
        success_md_path: str | None = None,
        retrospective_rounds: list[Any] | None = None,
    ) -> None:
        """Push the run-complete :class:`~the_architect.tui.screens.success.SuccessScreen`.

        Thread-safe. Blocks the calling thread until the user dismisses
        the screen (presses Enter, Q, or Escape). Called from the worker
        thread after tasks/SUMMARY.md has been written.
        """
        from the_architect.tui.screens.success import SuccessScreen

        screen = SuccessScreen(
            results=results,
            total_duration=total_duration,
            total_tokens=total_tokens,
            success_md_path=success_md_path,
            retrospective_rounds=retrospective_rounds,
        )
        # push_and_wait blocks the worker thread until the user exits the screen.
        self.push_and_wait(screen)

    def _handle_exception(self, error: Exception) -> None:
        """Swallow stack-empty errors from late widget messages.

        Textual sometimes delivers ``InvokeLater`` messages after a
        screen has been dismissed but before the replacement has
        finished mounting. Those messages call ``self.app.screen``
        which raises :class:`ScreenStackError` when the stack is
        momentarily empty. That's a cosmetic race — ignore it instead
        of crashing the whole app.
        """
        from textual.app import ScreenStackError

        if isinstance(error, ScreenStackError):
            return
        super()._handle_exception(error)

    def action_help(self) -> None:
        """Show the help overlay for the currently active screen."""
        from the_architect.tui.screens.help import (
            HelpScreen,
            collect_screen_bindings,
        )

        try:
            current = self.screen
        except Exception:
            current = self._initial_screen
        bindings = collect_screen_bindings(current)
        self.push_screen(HelpScreen(bindings=bindings))

    # ── Pause menu (ESC during a run) ──────────────────────────────────

    def show_pause_menu(self) -> None:
        """Push the pause-menu overlay on top of the current screen.

        Called from execution / wait screens when the user hits ESC.
        Acts as a guard rail against losing a long-running task to a
        stray keystroke. The overlay dismisses with ``"continue"``,
        ``"detach"``, or ``"exit"``; on ``"exit"`` we call
        :meth:`App.exit`, which unwinds through
        :class:`ArchitectAppRunner`'s ``finally`` block and kills any
        tracked provider subprocess.

        Guards against stacking: if the menu is already visible
        (repeated ESC presses), do nothing.
        """
        if self._pause_menu_visible:
            return
        from the_architect.tui.screens.pause import PauseMenuScreen

        self._pause_menu_visible = True

        def _on_dismiss(decision: str | None) -> None:
            self._pause_menu_visible = False
            if decision == "exit":
                # Same shutdown path as Ctrl+C / action_quit.
                try:
                    self.begin_shutdown()
                except Exception:
                    pass
            # "continue" and "detach" need no extra handling here —
            # "continue" just closes the overlay and "detach" already
            # tore down the tmux client (this process keeps running
            # and will be reattached later, or remains headless).

        try:
            self.push_screen(PauseMenuScreen(), _on_dismiss)
        except Exception:
            # If we fail to push (e.g. app not fully mounted), reset
            # the flag so a retry can succeed.
            self._pause_menu_visible = False


def run_single_screen(screen: Screen[T]) -> T | None:
    """Show one screen and return its value.

    Phase 21: when an :class:`ArchitectAppRunner` is hosting the CLI
    flow, the screen is *switched* onto the already-running app via
    :meth:`ArchitectApp.switch_and_wait` — no new app boot, no
    alt-screen flash, and critically the previous screen is replaced
    rather than popped back to the splash in between prompts.

    When no runner is active (tests or one-off direct calls), a
    minimal harness app boots just long enough to show the screen and
    exits when it dismisses.
    """
    try:
        from the_architect.tui.runner import active_runner
    except ImportError:
        runner = None
    else:
        runner = active_runner()

    if runner is not None:
        return runner.switch_and_wait(screen)

    collected: dict[str, Any] = {"value": None}

    class _Harness(App[None]):
        TITLE = "The Architect"

        def on_mount(self) -> None:
            # Apply the branded theme here too so fallback harness
            # flows (tests, one-off direct calls) render in green
            # instead of Textual's default orange/blue.
            try:
                self.register_theme(ARCHITECT_THEME)
                self.theme = ARCHITECT_THEME.name
            except Exception:
                pass
            # Match the main app so standalone screen flows also
            # show the version next to the app name.
            try:
                self.title = f"{self.TITLE}  {_architect_header_version()}"
            except Exception:
                pass
            self.push_screen(screen, self._on_dismiss)

        def _on_dismiss(self, value: Any) -> None:
            collected["value"] = value
            self.exit()

    _Harness().run()
    return collected["value"]  # type: ignore[no-any-return]
