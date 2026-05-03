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
from pathlib import Path
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
from the_architect.version import __version__ as _ARCHITECT_SEMVER


def _architect_header_version() -> str:
    """Return the version string shown in the Textual Header.

    Installed builds use :data:`the_architect.version.__version__`
    (SemVer only — the build counter is a dev-churn value not shipped
    in the wheel). When running from the repo, we additionally probe
    the project-root ``version.py`` for ``__build__`` so developers
    see the full ``v1.2.0 (build 10095)`` string on every screen.

    Never raises — any failure collapses to just the SemVer. Called
    once at app mount, so a little filesystem work is fine.
    """
    base = f"v{_ARCHITECT_SEMVER}"
    try:
        # Project root is two parents up from this file
        # (the_architect/tui/app.py → the_architect/tui → the_architect → <root>).
        root = Path(__file__).resolve().parents[2]
        version_py = root / "version.py"
        if not version_py.is_file():
            return base
        # Execute the module in a clean namespace so we pick up
        # __build__ without importing the file as a top-level module
        # (which would pollute sys.modules with a confusingly-named
        # "version" entry in dev mode).
        ns: dict[str, Any] = {}
        exec(compile(version_py.read_text(encoding="utf-8"), str(version_py), "exec"), ns)
        build = ns.get("__build__")
        if isinstance(build, int):
            return f"{base} (build {build})"
    except Exception:
        pass
    return base


T = TypeVar("T")

# Brand colour carried forward from the pre-TUI (Rich/questionary) era,
# where it was defined as ``ARCHITECT_GREEN = "#7cc800"``. Used as the
# accent for titles, spinners, header highlights, and any $accent
# reference in screen CSS. Replaces Textual's default orange accent.
ARCHITECT_GREEN = "#7cc800"

ARCHITECT_THEME = Theme(
    name="architect-dark",
    primary="#0178D4",
    secondary="#004578",
    # Accent → brand green. This is the one swap that turns every
    # orange $accent reference across the app into The Architect's
    # original vivid green.
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


class SplashScreen(Screen[None]):
    """Animated branded screen shown while no prompt or run is active.

    Used as the app's idle background so consecutive pre-run prompts
    and loading moments don't flash through a tabbed execution
    viewport or empty space between transitions. Shows the app name,
    a Matrix-style digital-rain animation (a nod to The Architect
    character's origin), and a short subtitle that callers can update
    via :meth:`set_subtitle` to describe the current wait.
    """

    DEFAULT_CSS = """
    SplashScreen {
        align: center middle;
    }

    #splash_body {
        /* Fixed width so children with `width: 100%` have something
           real to expand against. Auto height collapses vertically to
           fit the title + rain row + subtitle stack. The Screen's
           outer `align: center middle` centres the whole block. */
        width: 48;
        height: auto;
        padding: 2 2;
    }

    #splash_title {
        width: 100%;
        color: $accent;
        text-style: bold;
        text-align: center;
    }

    /* Wrap MatrixRain in a horizontal strip that spans the body so
       its fixed-width grid can be centred inside via
       `align-horizontal: center`. `align-horizontal` is unreliable on
       a `Vertical` container in current Textual, but works on a
       `Horizontal` the way flexbox's `justify-content` would. */
    #splash_rain_row {
        width: 100%;
        height: 7;
        align-horizontal: center;
        margin: 1 0 0 0;
    }

    #splash_subtitle {
        width: 100%;
        color: $text-muted;
        text-align: center;
        padding: 1 0 0 0;
    }
    """

    def __init__(self, subtitle: str = "Starting up…") -> None:
        super().__init__()
        self._subtitle = subtitle

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="splash_body"):
            yield Static("The Architect", id="splash_title")
            # Matrix digital rain replaces the old braille spinner —
            # same 10 FPS cadence, but themed to The Architect (the
            # character from The Matrix). Self-animates; no tick
            # wiring required. The wrapping Horizontal centres the
            # fixed-width grid horizontally under the title.
            with Horizontal(id="splash_rain_row"):
                yield MatrixRain(id="splash_rain")
            yield Static(self._subtitle, id="splash_subtitle")
        yield Footer()

    def set_subtitle(self, subtitle: str) -> None:
        """Update the subtitle under the animation. Safe from the UI thread."""
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
        Binding("o", "switch_tab('tab_output')", "Output"),
        Binding("e", "switch_tab('tab_events')", "Events"),
        Binding("d", "switch_tab('tab_details')", "Details"),
    ]

    def __init__(self, *, initial_screen: Screen[Any] | None = None) -> None:
        """Create the app.

        Args:
            initial_screen: The first screen to push on mount. Defaults
                to a minimal :class:`SplashScreen` so the app has a
                neutral background for pre-run prompts — the execution
                screen is pushed later when the worker actually starts
                running tasks. Pre-run callers pass a specific screen
                when they want the app to open straight into a prompt.
        """
        super().__init__()
        self._execution_screen: ExecutionScreen | None = None
        self._wait_screen: WaitScreen | None = None
        self._pause_menu_visible: bool = False
        self._initial_screen: Screen[Any] = initial_screen or SplashScreen()

    def on_mount(self) -> None:
        # Register and activate The Architect's branded theme before
        # pushing the first screen so every $accent reference is green
        # from frame one — no orange flash during splash boot.
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

    # ── Run-scoped status (Phase 18) ───────────────────────────────────

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

        self.call_from_thread(_push)
        done.wait()
        return result["value"]  # type: ignore[no-any-return]

    def switch_and_wait(self, screen: Screen[T]) -> T | None:
        """Replace the active screen and block until the new one dismisses.

        This is a thin wrapper around :meth:`push_and_wait` — the
        ``switch_screen`` semantics we originally used required a
        fragile monkey-patch of ``Screen.dismiss`` and caused
        ``ScreenStackError: No screens on stack`` when the last
        pre-run prompt dismissed. Because the idle screen under all
        prompts is the animated branded :class:`SplashScreen`, a brief
        revisit between dismiss and next push looks like a loading
        moment rather than a flicker, which is acceptable.
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
        try:
            if self.screen is not self._execution_screen:
                self.switch_screen(self._execution_screen)
        except Exception:
            # App not fully mounted yet; caller will retry on next tick.
            pass
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
        """Forward a streamed provider line to the execution screen."""
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
        """Update the Details tab with merged fields."""
        self._thread_safe_call(self._update_details_sync, **fields)

    def _update_details_sync(self, **fields: str) -> None:
        screen = self._ensure_execution_screen()
        screen.update_details(**fields)

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
        """Append a line to the wait screen's log tail (if visible)."""
        self._thread_safe_call(self._append_wait_log_sync, line)

    def _append_wait_log_sync(self, line: str) -> None:
        if self._wait_screen is None:
            return
        self._wait_screen.append_log(line)

    def hide_wait(self) -> None:
        """Pop the wait screen overlay and return to the execution screen."""
        self._thread_safe_call(self._hide_wait_sync)

    def _hide_wait_sync(self) -> None:
        if self._wait_screen is None:
            return
        try:
            self.pop_screen()
        except Exception:
            pass
        self._wait_screen = None

    # ── Actions ────────────────────────────────────────────────────────

    def action_switch_tab(self, tab_id: str) -> None:
        try:
            from textual.widgets import TabbedContent

            if self._execution_screen is None:
                return
            tabs = self._execution_screen.query_one("#exec_tabs", TabbedContent)
            tabs.active = tab_id
        except Exception:
            pass

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
                    self.exit()
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
            # instead of Textual's default orange.
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
