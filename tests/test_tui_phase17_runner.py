"""Tests for Phase 17 — persistent-app runner driving the CLI flow."""

from __future__ import annotations

import builtins
import io
import threading
import time
from types import SimpleNamespace

import pytest
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static

from the_architect.tui.app import run_single_screen
from the_architect.tui.runner import ArchitectAppRunner, active_runner


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
        assert output == stderr.getvalue() == tty.getvalue()
        assert "\033[?1049l" in output
        assert "\033[?1000l" in output
        assert "\033[?1001l" in output
        assert "\033[?1002l" in output
        assert "\033[?1003l" in output
        assert "\033[?1005l" in output
        assert "\033[?1006l" in output
        assert "\033[?1007l" in output
        assert "\033[?2004l" in output

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
