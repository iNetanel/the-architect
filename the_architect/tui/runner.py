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
"""

from __future__ import annotations

import atexit
import signal
import sys
import threading
import traceback
from collections.abc import Callable
from typing import Any, TypeVar

from loguru import logger
from textual.screen import Screen

from the_architect.tui.app import ArchitectApp

T = TypeVar("T")

_UNEXPECTED_EXIT_WAIT_SECONDS = 30.0


# Module-global reference to the currently active runner, if any.
# Set while ``ArchitectAppRunner.run()`` is in progress. Consumed by
# :func:`active_runner()` from any thread.
_ACTIVE_RUNNER: ArchitectAppRunner | None = None
_ACTIVE_LOCK = threading.Lock()


def active_runner() -> ArchitectAppRunner | None:
    """Return the runner that currently hosts the TUI, if any."""
    with _ACTIVE_LOCK:
        if _ACTIVE_RUNNER is None or not _ACTIVE_RUNNER.app_available:
            return None
        return _ACTIVE_RUNNER


class ArchitectAppRunner:
    """Hosts one :class:`ArchitectApp` while a worker thread drives the flow.

    Usage::

        runner = ArchitectAppRunner(flow_fn, flow_kwargs)
        runner.run()   # blocks until flow completes + app exits

    The worker thread invokes ``flow_fn(**flow_kwargs)``. Any exception
    the flow raises is re-raised from :meth:`run` on the main thread
    after the app has exited, so the CLI's error handling path is
    unchanged.
    """

    def __init__(
        self,
        flow: Callable[..., Any],
        kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._flow = flow
        self._flow_kwargs = kwargs or {}
        self.app = ArchitectApp()
        self._worker: threading.Thread | None = None
        self._flow_exception: BaseException | None = None
        self._flow_return: Any = None
        self._flow_done = threading.Event()
        self._app_available = True

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
        global _ACTIVE_RUNNER
        with _ACTIVE_LOCK:
            _ACTIVE_RUNNER = self

        def _worker() -> None:
            try:
                self._flow_return = self._flow(**self._flow_kwargs)
            except BaseException as exc:  # noqa: BLE001 — re-raised from run()
                self._flow_exception = exc
            finally:
                self._flow_done.set()
                # Schedule app exit on the event loop thread. Safe from
                # worker thread via call_from_thread. Wrapped in try
                # because the app may already be exiting.
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
                daemon=True,
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

        # Also install a SIGINT handler so Ctrl+C arriving *before*
        # Textual has fully taken over the event loop (for example
        # during the brief window between ``atexit.register`` and
        # ``app.run()`` actually calling ``asyncio.run``) still kills
        # any subprocess we've already spawned. After Textual's loop
        # starts, it replaces this handler with its own — which is
        # fine, because its handler routes to ``action_quit`` → our
        # finally block below.
        _prev_sigint: Any = None
        try:
            _prev_sigint = signal.signal(signal.SIGINT, _sigint_kill_handler)
        except (ValueError, OSError):
            # signal.signal() only works on the main thread.
            # ArchitectAppRunner.run() is always called from main in
            # production (from Click's entry point), but tests may
            # invoke it from a worker thread — silently skip.
            _prev_sigint = None

        app_error: BaseException | None = None
        try:
            self.app.run()
        except BaseException as exc:  # noqa: BLE001 — re-raised after cleanup
            app_error = exc
        finally:
            _restore_terminal_input_modes()
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
                # The TUI can disappear if Textual's screen stack is emptied by
                # an overlay transition. That must not cancel the CLI flow: in
                # Infinite Loop it happens after the next planning pass, right
                # before the newly planned tasks should execute. Keep the worker
                # alive and let the run finish rather than returning a dead app
                # from active_runner(). Explicit user shutdown still goes through
                # begin_shutdown()/shutdown_started and is handled below. The wait
                # is bounded so a dead Textual app cannot wedge the CLI forever.
                completed = self._flow_done.wait(timeout=_UNEXPECTED_EXIT_WAIT_SECONDS)
                if not completed:
                    try:
                        from the_architect.core.runner import kill_active_subprocesses

                        kill_active_subprocesses()
                    except Exception:
                        pass
                    if self._flow_exception is None:
                        self._flow_exception = RuntimeError(
                            "Architect TUI exited unexpectedly before the CLI flow completed"
                        )

            # Restore whatever SIGINT handler was in place before we
            # took over (usually Python's default, or a pytest one).
            if _prev_sigint is not None:
                try:
                    signal.signal(signal.SIGINT, _prev_sigint)
                except (ValueError, OSError):
                    pass
            if not unexpected_app_exit:
                # When the app exits (user hit Ctrl+C in the TUI, or the
                # worker completed normally), make sure no provider
                # subprocess is left running in the background. This is
                # the critical half of the Ctrl+C fix — the event loop
                # has already torn down, but a daemon worker thread may
                # still be blocked on ``process.wait()`` for the child
                # opencode / claude invocation. Kill anything tracked by
                # the runner registry before we return.
                try:
                    from the_architect.core.runner import kill_active_subprocesses

                    kill_active_subprocesses()
                except Exception:
                    pass

            # Wait for the worker to finish so flow_exception /
            # flow_return are fully populated.
            if not unexpected_app_exit:
                self._flow_done.wait(timeout=5.0)
            if self._worker is not None and self._worker.is_alive():
                # Worker still stuck; don't block forever.
                self._worker.join(timeout=1.0)
            with _ACTIVE_LOCK:
                if _ACTIVE_RUNNER is self:
                    _ACTIVE_RUNNER = None
            # Unregister the atexit hook now that we've cleaned up
            # synchronously — leaving it registered would fire again
            # at process exit and log "no active subprocesses" noise.
            try:
                atexit.unregister(_atexit_kill_subprocesses)
            except Exception:
                pass

        if self._flow_exception is not None:
            raise self._flow_exception
        if app_error is not None:
            raise app_error
        return self._flow_return


def _restore_terminal_input_modes() -> None:
    """Disable terminal input modes that can leak after abrupt Textual exits."""
    try:
        sys.stdout.write(
            "\033[?1049l"  # leave alternate screen + restore cursor
            "\033[?1000l"  # X10 mouse reporting
            "\033[?1002l"  # button-event mouse reporting
            "\033[?1003l"  # any-event mouse reporting
            "\033[?1004l"  # focus in/out reporting
            "\033[?1006l"  # SGR mouse mode
            "\033[?1015l"  # urxvt mouse mode
            "\033[?2004l"  # bracketed paste
            "\033[?25h"  # cursor visible
        )
        sys.stdout.flush()
    except Exception:
        pass


def _atexit_kill_subprocesses() -> None:
    """atexit hook — kill every registered provider subprocess on exit."""
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
    try:
        from the_architect.core.runner import kill_active_subprocesses

        kill_active_subprocesses()
    except Exception:
        pass
    raise KeyboardInterrupt()
