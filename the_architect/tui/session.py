"""Lifecycle helpers for launching Textual screens alongside async work.

Two helpers are exported:

1. :func:`tui_execution_session` — used during task execution
   (:class:`ArchitectApp` + execution screen).
2. :func:`tui_wait_session` — used during planning, retrospective
   review, and per-task reassessment. Mirrors the same design:
   launches the :class:`WaitApp` in a background thread, exposes
   thread-safe update methods, and tears down cleanly on exit.

When ``enabled=False`` (non-TTY, ``NO_COLOR``, ``TERM=dumb`` explicitly
set, running inside pytest), both helpers yield no-op sessions so CI
and unit tests still behave like the plain fallback.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING

from the_architect.core.runner import PlainStreamRenderer, StreamRenderer
from the_architect.tui.renderer import TextualStreamRenderer

if TYPE_CHECKING:
    from collections.abc import Iterator

    from the_architect.tui.app import ArchitectApp
    from the_architect.tui.screens.wait import WaitApp


class TuiSession:
    """Handle for a running TUI session.

    Exposes the renderer to plug into the runner and the app to push
    events/details directly. When the TUI is disabled, ``app`` is
    ``None`` and ``renderer`` is a :class:`PlainStreamRenderer`.
    """

    def __init__(
        self,
        renderer: StreamRenderer,
        app: ArchitectApp | None,
        thread: threading.Thread | None,
    ) -> None:
        self.renderer = renderer
        self.app = app
        self._thread = thread

    def push_event(self, event: str, data: dict[str, object] | None = None) -> None:
        """Forward an execution event to the TUI Diagnostics tab (no-op when disabled)."""
        if self.app is None:
            return
        try:
            self.app.push_event_line(event, data)
        except Exception:
            pass

    def update_details(self, **fields: str) -> None:
        """Merge fields into the TUI Progress tab (no-op when disabled)."""
        if self.app is None:
            return
        try:
            self.app.update_details(**fields)
        except Exception:
            pass

    def update_progress_tasks(self, tasks: list[dict[str, str]]) -> None:
        """Replace the TUI Progress tab task list (no-op when disabled)."""
        if self.app is None:
            return
        try:
            self.app.update_progress_tasks(tasks)
        except Exception:
            pass

    def update_settings(self, settings: dict[str, str]) -> None:
        """Replace the TUI Settings tab content (no-op when disabled)."""
        if self.app is None:
            return
        try:
            self.app.update_execution_settings(settings)
        except Exception:
            pass

    def update_costs(self, costs: dict[str, object]) -> None:
        """Push live cost data to the Costs tab (no-op when disabled).

        Args:
            costs: Mapping with keys ``session_cost_usd``, ``last_task_cost_usd``,
                ``session_tokens``, and ``model_costs``.
        """
        if self.app is None:
            return
        try:
            self.app.update_costs(costs)
        except Exception:
            pass

    def update_footer(self, text: str) -> None:
        """Update the TUI footer (no-op when disabled)."""
        if self.app is None:
            return
        try:
            self.app.update_footer(text)
        except Exception:
            pass


@contextmanager
def tui_execution_session(enabled: bool) -> Iterator[TuiSession]:
    """Context manager that owns the Textual app lifecycle.

    When ``enabled`` is False, yields a no-op session whose renderer is
    a :class:`PlainStreamRenderer`.

    When True and an :class:`ArchitectAppRunner` is currently hosting
    the CLI flow, **reuse** its running app: the worker thread is
    already inside that runner's app, so we bind the renderer to the
    live app and switch it from the splash screen to the execution
    screen. Spinning up a second app in a background thread would
    leave the original app stuck on ``SplashScreen`` ("Starting up…")
    while provider output went to an invisible off-screen instance —
    which is exactly the symptom this function used to cause.

    When True and no runner is active (legacy callers, some tests),
    falls back to the original behaviour of launching a dedicated
    :class:`ArchitectApp` in a background thread.
    """
    if not enabled:
        yield TuiSession(renderer=PlainStreamRenderer(), app=None, thread=None)
        return

    try:
        from the_architect.tui.runner import tui_suppressed_after_exit
    except ImportError:
        pass
    else:
        if tui_suppressed_after_exit():
            yield TuiSession(renderer=PlainStreamRenderer(), app=None, thread=None)
            return

    # Preferred path: reuse the runner's persistent app so output
    # lands on the one actually on screen.
    try:
        from the_architect.tui.runner import active_runner
    except ImportError:
        runner = None
    else:
        runner = active_runner()

    if runner is not None:
        app = runner.app
        renderer = TextualStreamRenderer(app=app)
        # Swap off the splash so output is visible from the first line.
        try:
            app.switch_to_execution()
        except Exception:
            pass
        session = TuiSession(renderer=renderer, app=app, thread=None)
        try:
            yield session
        finally:
            # Do NOT exit the app here — the runner still owns it and
            # has post-execution screens (success, review) to show.
            return

    # Fallback path: no runner in flight (tests, headless harness,
    # legacy callers). Launch a dedicated app in its own thread.
    # Guard: if we are NOT on the main thread, booting a new Textual app
    # would crash in LinuxDriver (signal.signal requires main thread).
    # Degrade to a plain-text session instead.
    if threading.current_thread() is not threading.main_thread():
        yield TuiSession(renderer=PlainStreamRenderer(), app=None, thread=None)
        return

    from the_architect.tui.app import ArchitectApp

    app = ArchitectApp()
    renderer = TextualStreamRenderer(app=app)
    ready = threading.Event()

    def _run_app() -> None:
        ready.set()
        try:
            import os

            app.run(headless=bool(os.environ.get("PYTEST_CURRENT_TEST")))
        except Exception:
            pass
        finally:
            try:
                from the_architect.tui.runner import _restore_terminal_input_modes

                _restore_terminal_input_modes()
            except Exception:
                pass

    thread = threading.Thread(target=_run_app, name="architect-tui", daemon=True)
    thread.start()
    ready.wait(timeout=2.0)
    session = TuiSession(renderer=renderer, app=app, thread=thread)
    try:
        yield session
    finally:
        try:
            app.exit()
        except Exception:
            pass
        if thread.is_alive():
            thread.join(timeout=2.0)
        try:
            from the_architect.tui.runner import _restore_terminal_input_modes

            _restore_terminal_input_modes()
        except Exception:
            pass


class TuiWaitSession:
    """Handle for a running wait-screen TUI session.

    Use :meth:`set_title`, :meth:`set_detail`, and :meth:`append_log` to
    push updates from any thread. When the TUI is disabled, every
    method is a safe no-op. When an existing :class:`ArchitectApp` is
    running, the session renders as an overlay on top of it instead of
    spinning up a separate :class:`WaitApp`.
    """

    def __init__(
        self,
        app: WaitApp | None,
        thread: threading.Thread | None,
        overlay_app: ArchitectApp | None = None,
    ) -> None:
        self.app = app
        self._thread = thread
        self._overlay_app = overlay_app

    def set_title(self, title: str) -> None:
        if self._overlay_app is not None:
            try:
                self._overlay_app.update_wait(title=title)
            except Exception:
                pass
            return
        if self.app is None:
            return
        try:
            self.app.call_from_thread(self.app.set_title, title)
        except Exception:
            pass

    def set_detail(self, detail: str) -> None:
        if self._overlay_app is not None:
            try:
                self._overlay_app.update_wait(detail=detail)
            except Exception:
                pass
            return
        if self.app is None:
            return
        try:
            self.app.call_from_thread(self.app.set_detail, detail)
        except Exception:
            pass

    def append_log(self, line: str) -> None:
        if self._overlay_app is not None:
            try:
                self._overlay_app.append_wait_log(line)
            except Exception:
                pass
            return
        if self.app is None:
            return
        # Use call_soon_threadsafe (non-blocking) for high-volume output lines
        # so the caller's asyncio event loop is never stalled waiting for the
        # WaitApp's event loop to acknowledge each individual line.
        # The same blocking issue affects append_log as push_output_line —
        # many lines × ~50 ms round-trip each easily exceeds the 5-second
        # reader_task timeout in stream_provider.
        loop = getattr(self.app, "_loop", None)
        thread_id = getattr(self.app, "_thread_id", None)
        if loop is not None and thread_id != threading.get_ident():
            try:
                loop.call_soon_threadsafe(self.app.append_log, line)
                return
            except RuntimeError:
                pass
        # Fallback: loop closed, not started, or same-thread call
        try:
            self.app.call_from_thread(self.app.append_log, line)
        except Exception:
            pass


@contextmanager
def tui_wait_session(
    enabled: bool,
    title: str,
    overlay_app: ArchitectApp | None = None,
) -> Iterator[TuiWaitSession]:
    """Context manager that hosts a wait surface during long-running work.

    Args:
        enabled: When False, yields a no-op session so non-TTY/CI runs
            stay identical to the plain terminal spinner path.
        title: Initial title shown alongside the spinner.
        overlay_app: When a running :class:`ArchitectApp` is available
            (for example during execution-time retrospective or
            reassessment), pass it here and the wait surface will be
            rendered as a screen overlay on top of the execution
            screen instead of launching a separate wait app. The main
            app stays alive the entire time.
    """
    if not enabled:
        yield TuiWaitSession(app=None, thread=None)
        return

    try:
        from the_architect.tui.runner import tui_suppressed_after_exit
    except ImportError:
        pass
    else:
        if tui_suppressed_after_exit():
            yield TuiWaitSession(app=None, thread=None)
            return

    # Preferred path: if the CLI flow is being hosted by an
    # :class:`ArchitectAppRunner`, the worker thread is already
    # inside its persistent app. Reuse it as the overlay surface so
    # the user sees the wait screen on top of the one real app,
    # instead of booting a phantom :class:`WaitApp` in a background
    # thread that the terminal never actually displays.
    if overlay_app is None:
        try:
            from the_architect.tui.runner import active_runner
        except ImportError:
            pass
        else:
            runner = active_runner()
            if runner is not None:
                overlay_app = runner.app

    # Overlay path: reuse the currently running main app.
    if overlay_app is not None:
        try:
            overlay_app.show_wait(title=title)
        except Exception:
            pass
        try:
            yield TuiWaitSession(app=None, thread=None, overlay_app=overlay_app)
        finally:
            try:
                overlay_app.hide_wait()
            except Exception:
                pass
        return

    # Standalone path: launch a dedicated WaitApp in a background thread.
    # Guard: if we are NOT on the main thread, booting a new Textual app
    # would crash when LinuxDriver tries to register SIGTSTP/SIGCONT
    # (signal.signal requires the main thread).  Degrade to a no-op
    # session — the run continues, just without the spinner UI.
    import threading as _threading

    if _threading.current_thread() is not _threading.main_thread():
        yield TuiWaitSession(app=None, thread=None)
        return

    from the_architect.tui.screens.wait import WaitApp

    app = WaitApp(title=title)
    ready = threading.Event()

    def _run_app() -> None:
        ready.set()
        try:
            import os

            app.run(headless=bool(os.environ.get("PYTEST_CURRENT_TEST")))
        except Exception:
            pass
        finally:
            try:
                from the_architect.tui.runner import _restore_terminal_input_modes

                _restore_terminal_input_modes()
            except Exception:
                pass

    thread = threading.Thread(target=_run_app, name="architect-wait", daemon=True)
    thread.start()
    ready.wait(timeout=2.0)
    session = TuiWaitSession(app=app, thread=thread)
    try:
        yield session
    finally:
        try:
            app.exit()
        except Exception:
            pass
        if thread.is_alive():
            thread.join(timeout=2.0)
        try:
            from the_architect.tui.runner import _restore_terminal_input_modes

            _restore_terminal_input_modes()
        except Exception:
            pass
