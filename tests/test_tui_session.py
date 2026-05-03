"""Tests for the TUI execution session lifecycle helper."""

from __future__ import annotations

import threading

from the_architect.core.runner import PlainStreamRenderer
from the_architect.tui import TuiSession, tui_execution_session, tui_wait_session
from the_architect.tui.renderer import TextualStreamRenderer
from the_architect.tui.runner import ArchitectAppRunner


class TestTuiExecutionSessionDisabled:
    def test_yields_noop_session_when_disabled(self) -> None:
        with tui_execution_session(enabled=False) as session:
            assert isinstance(session, TuiSession)
            assert session.app is None
            assert isinstance(session.renderer, PlainStreamRenderer)

    def test_noop_session_does_not_raise_on_methods(self) -> None:
        with tui_execution_session(enabled=False) as session:
            session.push_event("task_start", {"task": "T01"})
            session.update_details(task="T01", phase="executing")
            session.update_footer("T01 | attempt 1/3")
            # None of these should do anything, but none of them should raise.


class TestTuiSessionNoopMethods:
    def test_push_event_with_no_app_is_noop(self) -> None:
        session = TuiSession(renderer=PlainStreamRenderer(), app=None, thread=None)
        session.push_event("x")  # must not raise

    def test_update_details_with_no_app_is_noop(self) -> None:
        session = TuiSession(renderer=PlainStreamRenderer(), app=None, thread=None)
        session.update_details(task="T01")  # must not raise

    def test_update_footer_with_no_app_is_noop(self) -> None:
        session = TuiSession(renderer=PlainStreamRenderer(), app=None, thread=None)
        session.update_footer("hello")  # must not raise


class TestTuiExecutionSessionReusesActiveRunner:
    """Regression for issue 1: execution session must reuse the runner's
    live :class:`ArchitectApp` instead of spawning a second one in a
    background thread. The second app was invisible but was where the
    renderer sent output — so the user saw "Starting up…" forever.
    """

    def test_reuses_runner_app_when_runner_is_active(self) -> None:
        observed: dict[str, object] = {}

        def _flow() -> None:
            # Inside the flow, the active runner is set; the session
            # should hand back a renderer bound to the *runner's* app
            # and should not start its own worker thread.
            with tui_execution_session(enabled=True) as session:
                observed["app"] = session.app
                observed["renderer"] = session.renderer
                observed["thread"] = session._thread
                observed["worker_tid"] = threading.get_ident()

        runner = ArchitectAppRunner(flow=_flow)
        runner.run()

        # Session reused the runner's app and did NOT spawn a new thread.
        assert observed["app"] is runner.app
        assert observed["thread"] is None
        # Renderer is bound to the live app so output lands on the
        # visible execution screen.
        assert isinstance(observed["renderer"], TextualStreamRenderer)
        assert observed["renderer"]._app is runner.app  # type: ignore[union-attr]

    def test_without_runner_falls_back_to_dedicated_app(self) -> None:
        """Legacy callers (no runner) still get a self-hosted app."""
        # No runner active; this should still work and hand back a
        # TuiSession whose app is *not* None (dedicated thread path).
        with tui_execution_session(enabled=True) as session:
            assert isinstance(session, TuiSession)
            assert session.app is not None
            assert session._thread is not None


class TestTuiWaitSessionReusesActiveRunner:
    """Regression for issue 1: planning/retrospective wait screens
    must overlay on the runner's running app so the spinner + detail
    text are visible, not push them to a phantom WaitApp that never
    renders.
    """

    def test_reuses_runner_app_as_overlay(self) -> None:
        observed: dict[str, object] = {}

        def _flow() -> None:
            with tui_wait_session(enabled=True, title="planning…") as wait:
                # Overlay app must be the runner's live app.
                observed["overlay"] = wait._overlay_app
                observed["thread"] = wait._thread
                observed["app"] = wait.app

        runner = ArchitectAppRunner(flow=_flow)
        runner.run()

        assert observed["overlay"] is runner.app
        # Overlay path uses no dedicated thread / WaitApp.
        assert observed["thread"] is None
        assert observed["app"] is None
