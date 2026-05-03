"""Tests for Phase 17 — persistent-app runner driving the CLI flow."""

from __future__ import annotations

import threading

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
