"""Persistent-app runner for the CLI flow.

Phase 17: host one :class:`ArchitectApp` for the entire run lifecycle.
``cli.py`` used to call a sequence of ``App.run()`` invocations, each
booting a separate Textual app for a single prompt. This runner
replaces that pattern:

1. The caller constructs an :class:`ArchitectAppRunner` with the
   synchronous flow function (e.g. ``_run_main``) and its keyword
   arguments.
2. :meth:`ArchitectAppRunner.run` starts the flow on a background
   worker thread and calls ``app.run()`` on the main thread. The app
   stays alive from the moment the worker begins until the flow
   finishes.
3. While the worker runs, every ``run_*_screen()`` helper in
   :mod:`the_architect.tui.screens.pre_run` that goes through
   :func:`active_runner` detects the live runner and forwards the
   screen to :meth:`ArchitectApp.push_and_wait` instead of booting a
   fresh harness app.

Non-TTY / ``--no-tui`` / ``--headless`` paths never create a runner
and keep running the synchronous flow directly on the main thread —
the existing questionary / prompt_toolkit helpers are unchanged.

Session survival (Infinite Loop / persistent mode)
---------------------------------------------------
When Infinite Loop or persistent mode is active the worker thread is
spawned as **non-daemon** so it keeps running even after the Textual
TUI exits.  A SIGHUP handler is installed for the same duration: if
the controlling terminal closes (SSH drop, window close) the signal
closes the TUI cleanly but the worker continues in headless mode,
writing all output to ``.architect/logs/``.  The user can reconnect
with ``architect monitor`` at any time.

Detach without tmux
-------------------
``request_tui_detach()`` is called from the pause menu "Detach"
button.  It exits the Textual app (freeing the terminal) while the
non-daemon worker continues running.  No tmux required.
"""

from __future__ import annotations

import atexit
import signal
import threading
import traceback
from collections.abc import Callable
from typing import Any, TypeVar

from loguru import logger
from textual.screen import Screen

from the_architect.tui.app import ArchitectApp
from the_architect.tui.terminal import restore_terminal_input_modes

T = TypeVar("T")

_UNEXPECTED_STARTUP_EXIT_WAIT_SECONDS = 30.0


# Module-global reference to the currently active runner, if any.
# Set while ``ArchitectAppRunner.run()`` is in progress. Consumed by
# :func:`active_runner()` from any thread.
_ACTIVE_RUNNER: ArchitectAppRunner | None = None
_ACTIVE_LOCK = threading.Lock()
_TUI_SUPPRESSED_AFTER_EXIT = False


def active_runner() -> ArchitectAppRunner | None:
    """Return the runner that currently hosts the TUI, if any."""
    with _ACTIVE_LOCK:
        if _ACTIVE_RUNNER is None or not _ACTIVE_RUNNER.app_available:
            return None
        return _ACTIVE_RUNNER


def tui_suppressed_after_exit() -> bool:
    """Return True when a dead main TUI should not be replaced by new TUI apps."""
    with _ACTIVE_LOCK:
        return _TUI_SUPPRESSED_AFTER_EXIT


def request_tui_detach() -> bool:
    """Close the Textual TUI while keeping the worker running.

    Called from the pause menu "Detach" button.  The worker thread must
    be non-daemon (i.e. the run was started in Infinite Loop or
    persistent mode) for detach to be meaningful — otherwise the
    process exits when the TUI exits.

    Returns:
        True if the TUI was successfully requested to exit.
        False if there is no active runner or it is already shutting
        down.
    """
    with _ACTIVE_LOCK:
        runner = _ACTIVE_RUNNER
    if runner is None or not runner.app_available:
        return False
    try:
        runner.app.call_from_thread(runner.app.exit)
        return True
    except Exception as exc:
        logger.debug(f"request_tui_detach: call_from_thread failed: {exc!r}")
        return False


def worker_is_persistent() -> bool:
    """Return True when the active runner is in persistent mode.

    Persistent mode means the SIGHUP handler is installed and detach is
    meaningful — the run will survive terminal close / SSH drop and
    continue headless.

    This is True when:
    - The CLI ``--persistent`` flag was passed (set at runner construction), or
    - The user selected Infinite Loop in the TUI pre-run screen and
      :meth:`ArchitectAppRunner.activate_persistence` was called, or
    - :meth:`ArchitectAppRunner.activate_persistence` was called for any
      other reason.

    Note: the worker thread is **always** non-daemon (it outlives the TUI
    regardless), but ``worker_is_persistent()`` specifically reflects
    whether the SIGHUP handler is active — i.e. whether "Detach" in the
    pause menu will actually keep the run going after terminal close.
    """
    with _ACTIVE_LOCK:
        runner = _ACTIVE_RUNNER
    if runner is None:
        return False
    return runner._persistent


class ArchitectAppRunner:
    """Hosts one :class:`ArchitectApp` while a worker thread drives the flow.

    Usage::

        runner = ArchitectAppRunner(flow_fn, flow_kwargs)
        runner.run()   # blocks until flow completes + app exits

    The worker thread invokes ``flow_fn(**flow_kwargs)``. Any exception
    the flow raises is re-raised from :meth:`run` on the main thread
    after the app has exited, so the CLI's error handling path is
    unchanged.

    When ``persistent=True`` the worker is spawned as a non-daemon
    thread so it keeps running after the TUI exits (SIGHUP / detach).
    """

    def __init__(
        self,
        flow: Callable[..., Any],
        kwargs: dict[str, Any] | None = None,
        persistent: bool = False,
    ) -> None:
        """Initialise the runner.

        Args:
            flow: The synchronous flow function to run on the worker.
            kwargs: Keyword arguments forwarded to ``flow``.
            persistent: When True the worker is immediately non-daemon and
                a SIGHUP handler is installed.  Use when ``--persistent``
                was passed on the CLI (before the TUI starts).

                For Infinite Loop selected inside the TUI pre-run screen,
                call :meth:`activate_persistence` from the worker thread
                after the user has confirmed the loop — this installs the
                SIGHUP handler at that point.  The worker thread is always
                non-daemon regardless, so the run always survives TUI
                detach once it has started.
        """
        self._flow = flow
        self._flow_kwargs = kwargs or {}
        self._persistent = persistent
        self.app = ArchitectApp()
        self._worker: threading.Thread | None = None
        self._flow_exception: BaseException | None = None
        self._flow_return: Any = None
        self._flow_done = threading.Event()
        self._app_available = True

    def activate_persistence(self) -> None:
        """Upgrade this runner to persistent mode at runtime.

        Called from the worker thread after the user selects Infinite Loop
        in the TUI pre-run screen (where ``--persistent`` was not passed on
        the CLI).

        Sets ``_persistent = True`` so that:
        - :func:`worker_is_persistent` returns True (pause menu shows Detach)
        - The run is not joined on TUI exit (detach path)
        - The detach hint is printed after the TUI closes

        The SIGHUP handler cannot be installed from a worker thread
        (``signal.signal`` requires the main thread), so it is instead
        installed on the main thread via :meth:`_install_sighup_if_pending`
        which is called from the app's event loop via ``call_from_thread``.

        Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._persistent:
            return
        self._persistent = True
        logger.debug("activate_persistence: marking runner as persistent")
        # Request the main-thread event loop to install the SIGHUP handler.
        if not self.app.shutdown_started:
            try:
                self.app.call_from_thread(self._install_sighup_from_main_thread)
            except Exception as exc:
                logger.debug(f"activate_persistence: call_from_thread failed: {exc!r}")

    def _install_sighup_from_main_thread(self) -> None:
        """Install the SIGHUP handler on the main thread (called via event loop)."""
        try:
            import signal as _signal

            _signal.signal(_signal.SIGHUP, self._sighup_handler)
            logger.debug("activate_persistence: SIGHUP handler installed on main thread")
        except (ValueError, OSError, AttributeError):
            pass

    @property
    def app_available(self) -> bool:
        """Return whether the hosted Textual app is still running and reusable."""
        return self._app_available and not self.app.shutdown_started

    def push_and_wait(self, screen: Screen[T]) -> T | None:
        """Push a screen on the hosted app and block until dismiss.

        Safe to call from the worker thread only. Delegates to
        :meth:`ArchitectApp.push_and_wait` which uses ``call_from_thread``
        internally so the screen push lands on the event loop.
        """
        return self.app.push_and_wait(screen)

    def switch_and_wait(self, screen: Screen[T]) -> T | None:
        """Replace the active screen on the hosted app, block until dismiss.

        Same thread-safety as :meth:`push_and_wait`. Used by the
        sequential pre-run flow so consecutive prompts replace each
        other cleanly — no return-to-splash between steps.
        """
        return self.app.switch_and_wait(screen)

    def run(self) -> Any:
        """Run the hosted app + worker. Returns whatever the flow returned.

        Any exception the flow raised is re-raised from here after the
        app has exited, so exception propagation matches the
        pre-Phase-17 behaviour where the flow ran inline on the main
        thread.
        """
        global _ACTIVE_RUNNER, _TUI_SUPPRESSED_AFTER_EXIT
        restore_terminal_input_modes()
        with _ACTIVE_LOCK:
            _ACTIVE_RUNNER = self
            _TUI_SUPPRESSED_AFTER_EXIT = False

        def _worker() -> None:
            try:
                self._flow_return = self._flow(**self._flow_kwargs)
            except BaseException as exc:  # noqa: BLE001 — re-raised from run()
                self._flow_exception = exc
            finally:
                self._flow_done.set()
                # Clear _TUI_SUPPRESSED_AFTER_EXIT now that the worker is
                # done — future run_single_screen calls (e.g. from a new
                # run in the same process) must not be suppressed.
                with _ACTIVE_LOCK:
                    global _TUI_SUPPRESSED_AFTER_EXIT
                    _TUI_SUPPRESSED_AFTER_EXIT = False
                # Schedule app exit on the event loop thread. Safe from
                # worker thread via call_from_thread. Wrapped in try
                # because the app may already be exiting (detach / SIGHUP).
                if not self.app.shutdown_started:
                    try:
                        self.app.call_from_thread(self.app.exit)
                    except Exception:
                        pass

        # Start the worker after the app's event loop is running —
        # call_later fires on the next event loop iteration, which is
        # after on_mount completes and the SplashScreen is pushed.
        # The worker will block in push_and_wait() while the SplashScreen
        # minimum display window runs out (enforced by ArchitectApp),
        # so the animation plays freely on the event loop during that time.
        def _spawn_worker_when_ready() -> None:
            self._worker = threading.Thread(
                target=_worker,
                name="architect-cli-flow",
                # Always non-daemon: the worker must be allowed to outlive
                # the TUI so detach works from ANY run (not just those that
                # were started with --persistent or --infinite-loop).
                # The SIGHUP handler is installed immediately when
                # persistent=True (CLI flag) or lazily via
                # activate_persistence() when the user selects Infinite
                # Loop inside the TUI pre-run screen.
                daemon=False,
            )
            self._worker.start()

        self.app.call_later(_spawn_worker_when_ready)

        # Install a belt-and-braces cleanup hook that fires even if we
        # exit abnormally (uncaught exception in the main thread,
        # os._exit, interpreter shutdown). Textual's own Ctrl+C
        # handling runs inside the event loop and already triggers
        # ``action_quit``; this hook only matters for the edge case
        # where a second Ctrl+C arrives during shutdown and the
        # finally block below never reaches ``kill_active_subprocesses``.
        atexit.register(_atexit_kill_subprocesses)

        # Install a SIGINT handler so Ctrl+C arriving *before* Textual
        # has fully taken over the event loop still kills any subprocess
        # we've already spawned.  After Textual's loop starts, it
        # replaces this handler with its own — which routes to
        # ``action_quit`` → our finally block below.
        _prev_sigint: Any = None
        try:
            _prev_sigint = signal.signal(signal.SIGINT, _sigint_kill_handler)
        except (ValueError, OSError):
            _prev_sigint = None

        # SIGHUP handler for persistent / Infinite Loop runs.
        # When the controlling terminal closes (SSH drop, window close)
        # the OS sends SIGHUP to the foreground process.  Default
        # disposition is to terminate the process — which would kill
        # the Infinite Loop mid-run.  Instead: close the TUI cleanly
        # (the terminal is gone anyway) and let the non-daemon worker
        # continue running headless, writing output to the log file.
        # The user reconnects with ``architect monitor``.
        _prev_sighup: Any = None
        if self._persistent:
            try:
                _prev_sighup = signal.signal(signal.SIGHUP, self._sighup_handler)
                logger.debug("Installed SIGHUP handler for persistent run")
            except (ValueError, OSError, AttributeError):
                # SIGHUP not available on Windows — safe to ignore.
                _prev_sighup = None

        app_error: BaseException | None = None
        try:
            import os

            _headless = bool(os.environ.get("PYTEST_CURRENT_TEST"))
            self.app.run(headless=_headless)
        except BaseException as exc:  # noqa: BLE001 — re-raised after cleanup
            app_error = exc
        finally:
            _restore_terminal_input_modes()

            # Restore signal handlers before any further work.
            if _prev_sigint is not None:
                try:
                    signal.signal(signal.SIGINT, _prev_sigint)
                except (ValueError, OSError):
                    pass
            if _prev_sighup is not None:
                try:
                    signal.signal(signal.SIGHUP, _prev_sighup)
                except (ValueError, OSError, AttributeError):
                    pass

            unexpected_app_exit = not self._flow_done.is_set() and not self.app.shutdown_started
            if unexpected_app_exit:
                logger.warning(
                    "Architect TUI app exited unexpectedly while the worker flow is still active; "
                    "marking active_runner unavailable and waiting for the flow to complete."
                )
                stack = "".join(traceback.format_stack())
                logger.debug(f"Unexpected TUI exit stack:\n{stack}")
                self._app_available = False
                with _ACTIVE_LOCK:
                    if _ACTIVE_RUNNER is self:
                        _ACTIVE_RUNNER = None
                    _TUI_SUPPRESSED_AFTER_EXIT = True
                # The TUI can disappear if Textual's screen stack is emptied
                # by an overlay transition (between Infinite Loop iterations),
                # or because the user detached.  Neither should cancel the
                # flow — degrade to headless and wait for the worker.
                if self._worker is not None:
                    self._flow_done.wait()
                else:
                    completed = self._flow_done.wait(timeout=_UNEXPECTED_STARTUP_EXIT_WAIT_SECONDS)
                    if completed:
                        unexpected_app_exit = False
                if unexpected_app_exit and not self._flow_done.is_set():
                    try:
                        from the_architect.core.runner import kill_active_subprocesses

                        kill_active_subprocesses()
                    except Exception:
                        pass
                    if self._flow_exception is None:
                        self._flow_exception = RuntimeError(
                            "Architect TUI exited unexpectedly before the CLI flow completed"
                        )

            if not unexpected_app_exit:
                worker_still_running = (
                    self._worker is not None
                    and self._worker.is_alive()
                    and not self._flow_done.is_set()
                )
                if worker_still_running and not self._persistent:
                    # User quit the TUI (Ctrl+C / q) while a task was running.
                    # Kill the active provider subprocess so the worker
                    # unblocks and can finish cleanly. Without this the
                    # non-daemon worker keeps the process alive forever.
                    try:
                        from the_architect.core.runner import kill_active_subprocesses

                        kill_active_subprocesses()
                    except Exception:
                        pass
                elif not worker_still_running:
                    # Worker already done — kill any leftover subprocess.
                    try:
                        from the_architect.core.runner import kill_active_subprocesses

                        kill_active_subprocesses()
                    except Exception:
                        pass

            # Wait for the worker to finish so flow_exception /
            # flow_return are fully populated.
            # For persistent runs where the user detached and the worker
            # is still running, we do NOT join — just return so the
            # terminal is freed immediately.
            if not unexpected_app_exit and not self._persistent:
                self._flow_done.wait(timeout=5.0)
            if self._worker is not None and self._worker.is_alive() and not self._persistent:
                self._worker.join(timeout=1.0)

            # Only clear _TUI_SUPPRESSED_AFTER_EXIT once the worker has
            # actually finished.  If the user quit the TUI (Ctrl+C / q)
            # while the worker is still running (non-daemon), clearing it
            # early causes run_single_screen to fall through to
            # _Harness().run() from the worker thread → LinuxDriver tries
            # signal.signal(SIGTSTP) from a non-main thread → crash.
            worker_finished = self._flow_done.is_set()
            with _ACTIVE_LOCK:
                if _ACTIVE_RUNNER is self:
                    _ACTIVE_RUNNER = None
                if worker_finished:
                    _TUI_SUPPRESSED_AFTER_EXIT = False
                # else: leave _TUI_SUPPRESSED_AFTER_EXIT = True so the
                # still-running worker's run_single_screen calls return
                # None instead of booting a new Textual app.

            try:
                atexit.unregister(_atexit_kill_subprocesses)
            except Exception:
                pass

            # Persistent run detached — print a brief reconnect hint so
            # the user knows the run is still going.
            if self._persistent and not self._flow_done.is_set():
                _print_detach_hint()

        if self._flow_exception is not None:
            raise self._flow_exception
        if app_error is not None:
            raise app_error
        return self._flow_return

    def _sighup_handler(self, signum: int, frame: Any) -> None:
        """Handle SIGHUP by closing the TUI and continuing headless.

        The terminal is gone (SSH drop / window close). Close the
        Textual app so it does not try to write to a dead terminal,
        then let the non-daemon worker thread continue running
        headless.  All output goes to ``.architect/logs/``.
        """
        logger.info("SIGHUP received — closing TUI, worker continues headless")
        if not self.app.shutdown_started:
            try:
                self.app.call_from_thread(self.app.exit)
            except Exception:
                pass


def _print_detach_hint() -> None:
    """Print a brief message after TUI detach so the user knows what to do."""
    import sys

    sys.stdout.write(
        "\n  The Architect is still running in the background.\n"
        "  Reconnect with:  architect monitor\n\n"
    )
    sys.stdout.flush()


def _restore_terminal_input_modes() -> None:
    """Disable terminal input modes that can leak after abrupt Textual exits."""
    restore_terminal_input_modes()


def _atexit_kill_subprocesses() -> None:
    """atexit hook — kill every registered provider subprocess on exit."""
    _restore_terminal_input_modes()
    try:
        from the_architect.core.runner import kill_active_subprocesses

        kill_active_subprocesses()
    except Exception:
        pass


def _sigint_kill_handler(signum: int, frame: Any) -> None:
    """SIGINT handler — kill subprocesses then raise KeyboardInterrupt.

    The raise mirrors Python's default SIGINT handler so whoever is
    running above us (pytest, Click, the user's shell) sees the same
    exit pattern it always has. The one behavioural difference is
    that before the raise we yank every live provider subprocess out
    of the OS so the user's Ctrl+C actually stops the backend.
    """
    _restore_terminal_input_modes()
    try:
        from the_architect.core.runner import kill_active_subprocesses

        kill_active_subprocesses()
    except Exception:
        pass
    raise KeyboardInterrupt()
