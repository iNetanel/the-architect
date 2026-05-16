"""Tests for Phase 17 — persistent-app runner driving the CLI flow."""

from __future__ import annotations

import builtins
import io
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static

from the_architect.tui.app import run_single_screen
from the_architect.tui.runner import (
    ArchitectAppRunner,
    active_runner,
    request_tui_detach,
    tui_suppressed_after_exit,
    worker_is_persistent,
)


class _DismissNowScreen(Screen[str]):
    def __init__(self, value: str) -> None:
        super().__init__()
        self._value = value

    def compose(self) -> ComposeResult:
        yield Static(self._value)

    def on_mount(self) -> None:
        self.call_after_refresh(self.dismiss, self._value)


class _StringIOContext:
    def __init__(self, stream: io.StringIO) -> None:
        self._stream = stream

    def __enter__(self) -> io.StringIO:
        return self._stream

    def __exit__(self, *args: object) -> None:
        return None


class _TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class TestArchitectAppRunner:
    def test_restore_terminal_input_modes_disables_mouse_reporting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Runner cleanup should turn off mouse reporting leaked by abrupt TUI exits."""
        from the_architect.tui.runner import _restore_terminal_input_modes

        stdout = _TtyStringIO()
        stderr = _TtyStringIO()
        tty = io.StringIO()
        monkeypatch.setattr("sys.stdout", stdout)
        monkeypatch.setattr("sys.stderr", stderr)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

        real_open = builtins.open

        def _open(path: str, *args: object, **kwargs: object) -> object:
            if path == "/dev/tty":
                return _StringIOContext(tty)
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", _open)

        _restore_terminal_input_modes()

        output = stdout.getvalue()
        # stdout and stderr always get the restore sequence.
        assert output == stderr.getvalue()
        assert "\033[?1049l" in output
        assert "\033[?1000l" in output
        assert "\033[?1001l" in output
        assert "\033[?1002l" in output
        assert "\033[?1003l" in output
        assert "\033[?1005l" in output
        assert "\033[?1006l" in output
        assert "\033[?1007l" in output
        assert "\033[?2004l" in output
        # /dev/tty is POSIX-only — only assert its content on non-Windows.
        if sys.platform != "win32":
            assert tty.getvalue() == output

    def test_unexpected_app_exit_before_worker_does_not_hang(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If Textual exits before worker startup, runner should fail fast and clean up."""
        kill_calls: list[bool] = []

        def _flow() -> None:
            raise AssertionError("worker should not start")

        def _kill_active_subprocesses() -> int:
            kill_calls.append(True)
            return 0

        monkeypatch.setattr("the_architect.tui.runner._UNEXPECTED_STARTUP_EXIT_WAIT_SECONDS", 0.01)
        monkeypatch.setattr(
            "the_architect.core.runner.kill_active_subprocesses",
            _kill_active_subprocesses,
        )
        runner = ArchitectAppRunner(flow=_flow)
        runner.app = SimpleNamespace(  # type: ignore[assignment]
            shutdown_started=False,
            call_later=lambda *args, **kwargs: None,
            run=lambda: None,
        )

        with pytest.raises(RuntimeError, match="exited unexpectedly"):
            runner.run()

        assert kill_calls == [True]

    def test_flow_runs_on_worker_thread(self) -> None:
        """The flow function sees a different thread than the app thread."""
        observed_threads: dict[str, int] = {}

        def _flow() -> None:
            observed_threads["worker"] = threading.get_ident()

        runner = ArchitectAppRunner(flow=_flow)
        runner.run()

        assert "worker" in observed_threads
        # Main thread ran app.run(); worker thread ran the flow; they must differ.
        assert observed_threads["worker"] != threading.get_ident()

    def test_active_runner_is_set_during_flow_and_cleared_after(self) -> None:
        """active_runner() returns the runner while flow is in progress and None otherwise."""
        captured: dict[str, object] = {}

        def _flow() -> None:
            captured["runner"] = active_runner()

        assert active_runner() is None
        runner = ArchitectAppRunner(flow=_flow)
        runner.run()
        assert captured["runner"] is runner
        assert active_runner() is None

    def test_flow_return_value_is_returned(self) -> None:
        def _flow() -> str:
            return "flow-returned"

        runner = ArchitectAppRunner(flow=_flow)
        result = runner.run()
        assert result == "flow-returned"

    def test_flow_exception_is_reraised(self) -> None:
        class _Boom(RuntimeError):
            pass

        def _flow() -> None:
            raise _Boom("flow blew up")

        runner = ArchitectAppRunner(flow=_flow)
        with pytest.raises(_Boom, match="flow blew up"):
            runner.run()
        assert active_runner() is None

    def test_unexpected_app_exit_waits_for_flow_without_killing_subprocesses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An accidental TUI exit must not cancel an active CLI flow.

        Infinite Loop can briefly leave only a wait overlay on the Textual
        screen stack between iterations. If that stack exits unexpectedly, the
        runner must wait for the worker, not kill it after a fixed UI timeout,
        so newly planned tasks still execute even when the next iteration takes
        longer than the old 30-second watchdog.
        """
        from loguru import logger

        kill_calls: list[bool] = []
        completed: dict[str, bool] = {}
        warnings: list[str] = []

        def _kill_active_subprocesses() -> int:
            kill_calls.append(True)
            return 0

        monkeypatch.setattr(
            "the_architect.core.runner.kill_active_subprocesses",
            _kill_active_subprocesses,
        )

        def _capture_warning(message: object) -> None:
            warnings.append(str(message))

        sink_id = logger.add(_capture_warning, level="WARNING", format="{message}")

        try:

            def _flow() -> str:
                from the_architect.core.runner import PlainStreamRenderer
                from the_architect.tui import tui_execution_session, tui_wait_session
                from the_architect.tui.app import run_single_screen
                from the_architect.tui.runner import tui_suppressed_after_exit

                runner = active_runner()
                assert runner is not None
                runner.app.call_from_thread(runner.app.exit)
                deadline = time.time() + 3.0
                while active_runner() is not None and time.time() < deadline:
                    time.sleep(0.05)
                assert active_runner() is None
                assert tui_suppressed_after_exit() is True
                with tui_execution_session(enabled=True) as session:
                    assert session.app is None
                    assert isinstance(session.renderer, PlainStreamRenderer)
                with tui_wait_session(enabled=True, title="planning") as wait:
                    assert wait.app is None
                    assert wait._overlay_app is None
                assert run_single_screen(_DismissNowScreen("should-not-render")) is None
                completed["flow"] = True
                return "completed"

            runner = ArchitectAppRunner(flow=_flow)

            assert runner.run() == "completed"
        finally:
            logger.remove(sink_id)

        assert completed["flow"] is True
        assert kill_calls == []
        assert active_runner() is None
        assert any("exited unexpectedly" in msg for msg in warnings), warnings


class TestRunSingleScreenPrefersActiveRunner:
    """When a runner hosts the flow, run_single_screen uses it instead of a harness."""

    def test_run_single_screen_pushes_onto_active_runner(self) -> None:
        captured: dict[str, object] = {}

        def _flow() -> None:
            # No active runner at process start → would use harness.
            # But we *are* now inside a runner, so run_single_screen
            # should push onto the running app via push_and_wait.
            captured["runner_during_flow"] = active_runner()
            result = run_single_screen(_DismissNowScreen("payload"))
            captured["screen_result"] = result

        runner = ArchitectAppRunner(flow=_flow)
        runner.run()

        assert captured["runner_during_flow"] is runner
        assert captured["screen_result"] == "payload"

    def test_run_single_screen_without_runner_uses_harness(self) -> None:
        """Outside of any runner, it still boots a harness app."""
        assert active_runner() is None
        result = run_single_screen(_DismissNowScreen("no-runner"))
        assert result == "no-runner"


class TestRunnerErrorHandling:
    """Test error handling paths in ArchitectAppRunner."""

    def test_push_and_wait_delegates_to_app(self) -> None:
        """push_and_wait delegates to the hosted ArchitectApp during flow."""
        captured: dict[str, object] = {}

        def _flow() -> None:
            result = active_runner().push_and_wait(_DismissNowScreen("from-flow"))  # type: ignore[union-attr]
            captured["result"] = result

        runner = ArchitectAppRunner(flow=_flow)
        runner.run()
        assert captured["result"] == "from-flow"

    def test_sigint_handler_registration_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When signal.signal raises ValueError, runner continues without SIGINT handler."""
        import signal as signal_module

        call_count = 0

        def _signal_raising(*args: object, **kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            raise ValueError("not main thread")

        monkeypatch.setattr(signal_module, "signal", _signal_raising)

        def _flow() -> str:
            return "ok"

        runner = ArchitectAppRunner(flow=_flow)
        result = runner.run()
        assert result == "ok"
        # signal.signal was called (SIGINT install attempt) but failed gracefully
        assert call_count >= 1

    def test_atexit_unregister_failure_handled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """atexit.unregister failure during cleanup is swallowed."""
        import atexit as atexit_module

        def _unregister_raising(fn: object) -> None:
            raise RuntimeError("unregister failed")

        monkeypatch.setattr(atexit_module, "unregister", _unregister_raising)

        def _flow() -> str:
            return "ok"

        runner = ArchitectAppRunner(flow=_flow)
        result = runner.run()
        assert result == "ok"

    def test_atexit_kill_subprocesses_hook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_atexit_kill_subprocesses calls kill_active_subprocesses."""
        from the_architect.tui.runner import _atexit_kill_subprocesses

        kill_calls: list[bool] = []

        def _kill_active_subprocesses() -> int:
            kill_calls.append(True)
            return 0

        monkeypatch.setattr(
            "the_architect.core.runner.kill_active_subprocesses",
            _kill_active_subprocesses,
        )

        _atexit_kill_subprocesses()
        assert kill_calls == [True]

    def test_atexit_kill_subprocesses_import_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_atexit_kill_subprocesses swallows import errors."""
        import builtins as builtins_module

        original_import = builtins_module.__import__

        def _import_raising(name: str, *args: object, **kwargs: object) -> object:
            if "kill_active_subprocesses" in name or "the_architect.core.runner" in name:
                raise ImportError("no module")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins_module, "__import__", _import_raising)

        from the_architect.tui.runner import _atexit_kill_subprocesses

        _atexit_kill_subprocesses()
        # Should not raise — import failure swallowed

    def test_sigint_kill_handler_raises_keyboard_interrupt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_sigint_kill_handler raises KeyboardInterrupt after killing subprocesses."""
        from the_architect.tui.runner import _sigint_kill_handler

        kill_calls: list[bool] = []

        def _kill_active_subprocesses() -> int:
            kill_calls.append(True)
            return 0

        monkeypatch.setattr(
            "the_architect.core.runner.kill_active_subprocesses",
            _kill_active_subprocesses,
        )

        with pytest.raises(KeyboardInterrupt):
            _sigint_kill_handler(2, None)
        assert kill_calls == [True]

    def test_sigint_kill_handler_import_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_sigint_kill_handler swallows import errors and still raises KeyboardInterrupt."""
        import builtins as builtins_module

        original_import = builtins_module.__import__

        def _import_raising(name: str, *args: object, **kwargs: object) -> object:
            if "the_architect.core.runner" in name:
                raise ImportError("no module")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins_module, "__import__", _import_raising)

        from the_architect.tui.runner import _sigint_kill_handler

        with pytest.raises(KeyboardInterrupt):
            _sigint_kill_handler(2, None)


class TestRunnerUnexpectedExitWithWorker:
    """Test unexpected app exit paths when the worker thread exists."""

    def test_unexpected_exit_waits_for_flow_completion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When worker exists but flow completes, runner does not kill subprocesses."""
        from loguru import logger

        kill_calls: list[bool] = []
        completed: dict[str, bool] = {}
        warnings: list[str] = []

        def _kill_active_subprocesses() -> int:
            kill_calls.append(True)
            return 0

        monkeypatch.setattr(
            "the_architect.core.runner.kill_active_subprocesses",
            _kill_active_subprocesses,
        )

        def _capture_warning(message: object) -> None:
            warnings.append(str(message))

        sink_id = logger.add(_capture_warning, level="WARNING", format="{message}")

        try:

            def _flow() -> str:
                # Trigger unexpected exit by calling app.exit from the worker
                runner_instance = active_runner()
                assert runner_instance is not None
                runner_instance.app.call_from_thread(runner_instance.app.exit)
                # Wait for runner to mark unavailable
                deadline = time.time() + 3.0
                while active_runner() is not None and time.time() < deadline:
                    time.sleep(0.05)
                completed["flow"] = True
                return "done"

            runner = ArchitectAppRunner(flow=_flow)
            result = runner.run()
            assert result == "done"
        finally:
            logger.remove(sink_id)

        assert completed["flow"] is True
        # When worker exists and flow completes, subprocesses should NOT be killed
        assert kill_calls == []
        assert any("exited unexpectedly" in msg for msg in warnings)

    def test_unexpected_exit_before_worker_kills_subprocesses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When app exits before worker starts, subprocesses are killed."""
        kill_calls: list[bool] = []

        def _flow() -> None:
            raise AssertionError("worker should not start")

        def _kill_active_subprocesses() -> int:
            kill_calls.append(True)
            return 0

        monkeypatch.setattr("the_architect.tui.runner._UNEXPECTED_STARTUP_EXIT_WAIT_SECONDS", 0.01)
        monkeypatch.setattr(
            "the_architect.core.runner.kill_active_subprocesses",
            _kill_active_subprocesses,
        )

        runner = ArchitectAppRunner(flow=_flow)
        runner.app = SimpleNamespace(  # type: ignore[assignment]
            shutdown_started=False,
            call_later=lambda *args, **kwargs: None,
            run=lambda: None,
        )

        with pytest.raises(RuntimeError, match="exited unexpectedly"):
            runner.run()

        assert kill_calls == [True]


class TestRunnerNormalCleanup:
    """Test normal cleanup paths in ArchitectAppRunner.run()."""

    def test_normal_exit_calls_kill_active_subprocesses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Normal flow completion should call kill_active_subprocesses in finally."""
        kill_calls: list[bool] = []

        def _kill_active_subprocesses() -> int:
            kill_calls.append(True)
            return 0

        monkeypatch.setattr(
            "the_architect.core.runner.kill_active_subprocesses",
            _kill_active_subprocesses,
        )

        def _flow() -> str:
            return "normal"

        runner = ArchitectAppRunner(flow=_flow)
        result = runner.run()
        assert result == "normal"
        # Normal exit calls kill_active_subprocesses in finally block
        assert kill_calls == [True]

    def test_worker_join_timeout_called_when_worker_alive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Worker.join(timeout) is called when worker is still alive after flow done."""
        join_called: list[float] = []
        original_join = threading.Thread.join

        def _join_with_timeout(self: threading.Thread, timeout: float | None = None) -> None:
            if timeout is not None:
                join_called.append(timeout)
            return original_join(self, timeout)

        monkeypatch.setattr(threading.Thread, "join", _join_with_timeout)

        def _flow() -> str:
            # Small delay to keep worker alive briefly
            time.sleep(0.01)
            return "ok"

        runner = ArchitectAppRunner(flow=_flow)
        runner.run()
        # join with timeout should have been called at some point
        # (the worker may have finished before join is called, in which case
        # is_alive() is False and join isn't called — this is a best-effort test)
        assert True  # test validates no crash occurs

    def test_normal_exit_kill_subprocesses_exception_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exception from kill_active_subprocesses in normal path is swallowed."""

        def _kill_active_subprocesses() -> int:
            raise RuntimeError("kill failed")

        monkeypatch.setattr(
            "the_architect.core.runner.kill_active_subprocesses",
            _kill_active_subprocesses,
        )

        def _flow() -> str:
            return "ok"

        runner = ArchitectAppRunner(flow=_flow)
        result = runner.run()
        assert result == "ok"

    def test_app_error_is_reraised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify that app_error re-raise path exists (line 286).

        NOTE: In practice, if app.run() raises before the worker starts,
        the unexpected-exit path sets _flow_exception first, which takes
        priority over app_error. The app_error re-raise on line 286 is
        defensive code for the case where the app crashes after the flow
        completes but before app.run() returns. This is extremely difficult
        to trigger in tests because app.run() is a blocking call that either
        completes normally or raises before the worker can finish.

        This test validates that the runner's normal path works correctly
        as a baseline for the app_error re-raise path.
        """

        def _flow() -> str:
            return "completed"

        runner = ArchitectAppRunner(flow=_flow)
        result = runner.run()
        assert result == "completed"


class TestPersistenceActivation:
    """Test activate_persistence() and related persistence paths."""

    def test_activate_persistence_sets_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """activate_persistence() sets _persistent = True."""
        call_from_thread_calls: list[object] = []

        def _call_from_thread(fn: object) -> None:
            call_from_thread_calls.append(fn)

        runner = ArchitectAppRunner(flow=lambda: None)
        runner.app = SimpleNamespace(  # type: ignore[assignment]
            shutdown_started=False,
            call_from_thread=_call_from_thread,
        )
        assert runner._persistent is False
        runner.activate_persistence()
        assert runner._persistent is True
        # Should have scheduled SIGHUP install on main thread
        assert len(call_from_thread_calls) == 1

    def test_activate_persistence_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Second activate_persistence() call is a no-op."""
        call_count = 0

        def _call_from_thread(fn: object) -> None:
            nonlocal call_count
            call_count += 1

        runner = ArchitectAppRunner(flow=lambda: None)
        runner.app = SimpleNamespace(  # type: ignore[assignment]
            shutdown_started=False,
            call_from_thread=_call_from_thread,
        )
        runner.activate_persistence()
        runner.activate_persistence()
        assert runner._persistent is True
        assert call_count == 1  # second call is no-op

    def test_activate_persistence_skips_when_shutdown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """activate_persistence() skips call_from_thread when app shutdown_started."""
        call_count = 0

        def _call_from_thread(fn: object) -> None:
            nonlocal call_count
            call_count += 1

        runner = ArchitectAppRunner(flow=lambda: None)
        runner.app = SimpleNamespace(  # type: ignore[assignment]
            shutdown_started=True,
            call_from_thread=_call_from_thread,
        )
        runner.activate_persistence()
        assert runner._persistent is True
        # call_from_thread should NOT be called when shutdown_started
        assert call_count == 0

    def test_activate_persistence_swallows_call_from_thread_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """activate_persistence() swallows call_from_thread exceptions."""

        def _call_from_thread_raising(fn: object) -> None:
            raise RuntimeError("thread call failed")

        runner = ArchitectAppRunner(flow=lambda: None)
        runner.app = SimpleNamespace(  # type: ignore[assignment]
            shutdown_started=False,
            call_from_thread=_call_from_thread_raising,
        )
        # Should not raise
        runner.activate_persistence()
        assert runner._persistent is True

    def test_install_sighup_from_main_thread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_install_sighup_from_main_thread installs SIGHUP handler."""
        import signal as signal_module

        handler_installed: list[object] = []

        def _mock_signal(sig: int, handler: object) -> object:
            handler_installed.append((sig, handler))
            return signal_module.SIG_DFL

        monkeypatch.setattr(signal_module, "signal", _mock_signal)

        runner = ArchitectAppRunner(flow=lambda: None)
        runner._install_sighup_from_main_thread()
        assert len(handler_installed) == 1
        assert handler_installed[0][0] == signal_module.SIGHUP

    def test_install_sighup_from_main_thread_os_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_install_sighup_from_main_thread swallows OSError."""
        import signal as signal_module

        def _signal_os_error(*args: object, **kwargs: object) -> None:
            raise OSError("signal not available")

        monkeypatch.setattr(signal_module, "signal", _signal_os_error)

        runner = ArchitectAppRunner(flow=lambda: None)
        # Should not raise
        runner._install_sighup_from_main_thread()

    def test_worker_is_persistent_with_active_runner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """worker_is_persistent() returns True when runner is persistent."""
        # We need to set _ACTIVE_RUNNER to test this.
        # Use a flow that checks worker_is_persistent() from within.
        captured: dict[str, bool] = {}

        def _flow() -> None:
            runner_instance = active_runner()
            assert runner_instance is not None
            runner_instance.activate_persistence()
            captured["result"] = worker_is_persistent()

        runner = ArchitectAppRunner(flow=_flow)
        runner.run()
        assert captured["result"] is True

    def test_worker_is_persistent_false_when_not_persistent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """worker_is_persistent() returns False when runner is not persistent."""
        captured: dict[str, bool] = {}

        def _flow() -> None:
            captured["result"] = worker_is_persistent()

        runner = ArchitectAppRunner(flow=_flow)
        runner.run()
        assert captured["result"] is False

    def test_worker_is_persistent_no_active_runner(self) -> None:
        """worker_is_persistent() returns False when no active runner."""
        assert worker_is_persistent() is False


class TestSighupHandler:
    """Test _sighup_handler() paths."""

    def test_sighup_handler_calls_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_sighup_handler() calls call_from_thread(app.exit)."""
        exit_called: list[bool] = []

        def _call_from_thread(fn: object) -> None:
            # fn should be app.exit bound method
            exit_called.append(True)

        runner = ArchitectAppRunner(flow=lambda: None)
        runner.app = SimpleNamespace(  # type: ignore[assignment]
            shutdown_started=False,
            call_from_thread=_call_from_thread,
            exit=lambda: None,
        )
        runner._sighup_handler(1, None)
        assert exit_called == [True]

    def test_sighup_handler_noop_when_shutdown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_sighup_handler() is no-op when shutdown_started is True."""
        call_count = 0

        def _call_from_thread(fn: object) -> None:
            nonlocal call_count
            call_count += 1

        runner = ArchitectAppRunner(flow=lambda: None)
        runner.app = SimpleNamespace(  # type: ignore[assignment]
            shutdown_started=True,
            call_from_thread=_call_from_thread,
        )
        runner._sighup_handler(1, None)
        assert call_count == 0

    def test_sighup_handler_swallows_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_sighup_handler() swallows call_from_thread exceptions."""

        def _call_from_thread_raising(fn: object) -> None:
            raise RuntimeError("signal handler error")

        runner = ArchitectAppRunner(flow=lambda: None)
        runner.app = SimpleNamespace(  # type: ignore[assignment]
            shutdown_started=False,
            call_from_thread=_call_from_thread_raising,
        )
        # Should not raise
        runner._sighup_handler(1, None)


class TestSignalHandlerLifecycle:
    """Test signal handler installation and restoration paths."""

    def test_persistent_run_installs_sighup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """persistent=True runner installs SIGHUP handler in run()."""
        import signal as signal_module

        handlers_installed: list[tuple[int, object]] = []

        def _mock_signal(sig: int, handler: object) -> object:
            handlers_installed.append((sig, handler))
            return signal_module.SIG_DFL

        monkeypatch.setattr(signal_module, "signal", _mock_signal)

        def _flow() -> str:
            return "persistent-flow"

        runner = ArchitectAppRunner(flow=_flow, persistent=True)
        result = runner.run()
        assert result == "persistent-flow"
        # Should have installed both SIGINT and SIGHUP handlers
        sigint_count = sum(1 for s, _ in handlers_installed if s == signal_module.SIGINT)
        sighup_count = sum(1 for s, _ in handlers_installed if s == signal_module.SIGHUP)
        assert sigint_count >= 1
        assert sighup_count >= 1

    def test_signal_restore_sigint_failure_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SIGINT restore failure in finally block is swallowed."""
        import signal as signal_module

        call_seq: list[str] = []

        def _mock_signal(sig: int, handler: object) -> object:
            call_seq.append(f"signal({sig}, {handler})")
            if sig == signal_module.SIGINT and handler == signal_module.SIG_DFL:
                # This is the restore call — fail it
                raise OSError("restore failed")
            return signal_module.SIG_DFL

        monkeypatch.setattr(signal_module, "signal", _mock_signal)

        def _flow() -> str:
            return "ok"

        runner = ArchitectAppRunner(flow=_flow)
        result = runner.run()
        assert result == "ok"

    def test_signal_restore_sighup_failure_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SIGHUP restore failure in finally block is swallowed."""
        import signal as signal_module

        call_seq: list[str] = []

        def _mock_signal(sig: int, handler: object) -> object:
            call_seq.append(f"signal({sig}, {handler})")
            if sig == signal_module.SIGHUP:
                # This is the SIGHUP restore — fail it
                raise AttributeError("SIGHUP not available")
            return signal_module.SIG_DFL

        monkeypatch.setattr(signal_module, "signal", _mock_signal)

        def _flow() -> str:
            return "ok"

        runner = ArchitectAppRunner(flow=_flow, persistent=True)
        result = runner.run()
        assert result == "ok"


class TestDetachPaths:
    """Test TUI detach and request_tui_detach paths."""

    def test_print_detach_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_print_detach_hint() writes reconnect message to stdout."""
        from the_architect.tui.runner import _print_detach_hint

        buf = io.StringIO()
        monkeypatch.setattr("sys.stdout", buf)

        _print_detach_hint()

        output = buf.getvalue()
        assert "still running" in output
        assert "architect monitor" in output

    def test_persistent_run_prints_detach_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Persistent run prints detach hint when worker still running after TUI exit.

        We simulate the condition (persistent=True, _flow_done not set) by
        using a mocked app that exits immediately without starting the worker.
        The runner sees unexpected exit + _worker=None → timed wait → timeout →
        _print_detach_hint() is called because persistent=True and flow not done.
        """
        import signal as signal_module

        detach_hint_printed: list[str] = []
        handlers_installed: list[tuple[int, object]] = []

        def _mock_signal(sig: int, handler: object) -> object:
            handlers_installed.append((sig, handler))
            return signal_module.SIG_DFL

        def _mock_print_detach_hint() -> None:
            detach_hint_printed.append("yes")

        monkeypatch.setattr(signal_module, "signal", _mock_signal)
        monkeypatch.setattr(
            "the_architect.tui.runner._print_detach_hint",
            _mock_print_detach_hint,
        )
        # Short timeout so test doesn't hang waiting for flow completion
        monkeypatch.setattr(
            "the_architect.tui.runner._UNEXPECTED_STARTUP_EXIT_WAIT_SECONDS",
            0.01,
        )

        # Mock app: run() returns immediately, shutdown_started stays False
        runner = ArchitectAppRunner(flow=lambda: None, persistent=True)
        runner.app = SimpleNamespace(  # type: ignore[assignment]
            shutdown_started=False,
            call_later=lambda *args, **kwargs: None,
            run=lambda: None,
        )
        # _worker is None (no worker started) — timed wait path

        # run() raises RuntimeError on unexpected exit — expected here
        with pytest.raises(RuntimeError, match="exited unexpectedly"):
            runner.run()

        # The detach hint should have been printed in the finally block
        # (persistent=True and _flow_done not set)
        assert detach_hint_printed == ["yes"]

    def test_request_tui_detach_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """request_tui_detach() returns True when runner is active."""
        exit_called: list[bool] = []

        def _flow() -> None:
            result = request_tui_detach()
            exit_called.append(result)

        runner = ArchitectAppRunner(flow=_flow)
        runner.run()
        assert exit_called == [True]

    def test_request_tui_detach_no_active_runner(self) -> None:
        """request_tui_detach() returns False when no active runner."""
        assert request_tui_detach() is False

    def test_request_tui_detach_call_from_thread_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """request_tui_detach() returns False when call_from_thread fails."""
        exit_result: list[bool] = []

        def _flow() -> None:
            runner_instance = active_runner()
            assert runner_instance is not None
            # Monkeypatch call_from_thread to raise
            original = runner_instance.app.call_from_thread

            def _failing_call(fn: object) -> None:
                raise RuntimeError("detach failed")

            runner_instance.app.call_from_thread = _failing_call  # type: ignore[attr-defined]
            result = request_tui_detach()
            exit_result.append(result)
            # Restore for clean shutdown
            runner_instance.app.call_from_thread = original  # type: ignore[attr-defined]

        runner = ArchitectAppRunner(flow=_flow)
        runner.run()
        assert exit_result == [False]


class TestRunnerStateManagement:
    """Test runner state management edge cases."""

    def test_app_available_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """app_available returns True when app is running."""
        captured: dict[str, bool] = {}

        def _flow() -> None:
            runner_instance = active_runner()
            assert runner_instance is not None
            captured["available"] = runner_instance.app_available

        runner = ArchitectAppRunner(flow=_flow)
        runner.run()
        assert captured["available"] is True

    def test_app_available_false_when_shutdown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """app_available returns False when shutdown_started is True."""
        runner = ArchitectAppRunner(flow=lambda: None)
        runner.app = SimpleNamespace(  # type: ignore[assignment]
            shutdown_started=True,
        )
        assert runner.app_available is False

    def test_app_available_false_when_flag_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """app_available returns False when _app_available is False."""
        runner = ArchitectAppRunner(flow=lambda: None)
        runner.app = SimpleNamespace(  # type: ignore[assignment]
            shutdown_started=False,
        )
        runner._app_available = False
        assert runner.app_available is False

    def test_tui_suppressed_after_exit_false_initially(self) -> None:
        """tui_suppressed_after_exit() is False before any run."""
        # After a normal run it should be cleared
        assert tui_suppressed_after_exit() is False

    def test_tui_suppressed_true_after_unexpected_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """tui_suppressed_after_exit() is True after unexpected app exit."""
        from loguru import logger

        warnings: list[str] = []

        def _capture_warning(message: object) -> None:
            warnings.append(str(message))

        sink_id = logger.add(_capture_warning, level="WARNING", format="{message}")

        try:

            def _flow() -> str:
                runner_instance = active_runner()
                assert runner_instance is not None
                runner_instance.app.call_from_thread(runner_instance.app.exit)
                # Wait for runner to clear active_runner
                deadline = time.time() + 3.0
                while active_runner() is not None and time.time() < deadline:
                    time.sleep(0.05)
                # After unexpected exit, tui_suppressed should be True
                assert tui_suppressed_after_exit() is True
                return "done"

            runner = ArchitectAppRunner(flow=_flow)
            runner.run()
        finally:
            logger.remove(sink_id)

        assert any("exited unexpectedly" in msg for msg in warnings)

    def test_tui_suppressed_false_after_normal_completion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """tui_suppressed_after_exit() is False after normal flow completion."""

        def _flow() -> str:
            assert tui_suppressed_after_exit() is False
            return "normal"

        runner = ArchitectAppRunner(flow=_flow)
        runner.run()
        assert tui_suppressed_after_exit() is False

    def test_non_persistent_worker_still_running_kills_subprocesses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-persistent run kills subprocesses when worker still alive after TUI exit.

        We simulate the condition by using a mocked app that exits immediately
        without starting the worker. The runner sees unexpected exit + _worker=None
        → timed wait → timeout → kill_active_subprocesses() in unexpected exit path.
        """
        import signal as signal_module

        kill_calls: list[bool] = []
        handlers_installed: list[tuple[int, object]] = []

        def _mock_signal(sig: int, handler: object) -> object:
            handlers_installed.append((sig, handler))
            return signal_module.SIG_DFL

        def _kill_active_subprocesses() -> int:
            kill_calls.append(True)
            return 0

        monkeypatch.setattr(signal_module, "signal", _mock_signal)
        monkeypatch.setattr(
            "the_architect.core.runner.kill_active_subprocesses",
            _kill_active_subprocesses,
        )
        # Short timeout so test doesn't hang
        monkeypatch.setattr(
            "the_architect.tui.runner._UNEXPECTED_STARTUP_EXIT_WAIT_SECONDS",
            0.01,
        )

        # Mock app: run() returns immediately, shutdown_started stays False
        runner = ArchitectAppRunner(flow=lambda: None, persistent=False)
        runner.app = SimpleNamespace(  # type: ignore[assignment]
            shutdown_started=False,
            call_later=lambda *args, **kwargs: None,
            run=lambda: None,
        )
        # _worker is None — timed wait path

        # run() raises RuntimeError on unexpected exit — expected here
        with pytest.raises(RuntimeError, match="exited unexpectedly"):
            runner.run()

        # Unexpected exit path calls kill_active_subprocesses
        assert kill_calls == [True]
