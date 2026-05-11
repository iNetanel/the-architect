"""Tests for Phase 17 — persistent-app runner driving the CLI flow."""

from __future__ import annotations

import threading
import time

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


class TestArchitectAppRunner:
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
        runner must keep the worker alive so newly planned tasks still execute.
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
                runner = active_runner()
                assert runner is not None
                runner.app.call_from_thread(runner.app.exit)
                deadline = time.time() + 3.0
                while active_runner() is not None and time.time() < deadline:
                    time.sleep(0.05)
                assert active_runner() is None
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
