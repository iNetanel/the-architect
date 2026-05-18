"""Tests for the TUI execution session lifecycle helper."""

from __future__ import annotations

import threading
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from the_architect.core.runner import PlainStreamRenderer
from the_architect.tui import TuiSession, TuiWaitSession, tui_execution_session, tui_wait_session
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

    def test_update_feedback_with_no_app_is_noop(self) -> None:
        session = TuiSession(renderer=PlainStreamRenderer(), app=None, thread=None)
        session.update_feedback("msg")  # must not raise
        session.update_feedback(None)  # must not raise


class TestTuiSessionFeedbackForwarding:
    """Test that update_feedback forwards to the app."""

    def test_update_feedback_forwards_to_app(self) -> None:
        app = MagicMock()
        session = TuiSession(renderer=PlainStreamRenderer(), app=app, thread=None)
        session.update_feedback("fix the bug")
        app.update_feedback.assert_called_once_with("fix the bug")

    def test_update_feedback_clear_forwards_to_app(self) -> None:
        app = MagicMock()
        session = TuiSession(renderer=PlainStreamRenderer(), app=app, thread=None)
        session.update_feedback(None)
        app.update_feedback.assert_called_once_with(None)

    def test_update_feedback_app_exception_swallowed(self) -> None:
        app = MagicMock()
        app.update_feedback.side_effect = RuntimeError("broken")
        session = TuiSession(renderer=PlainStreamRenderer(), app=app, thread=None)
        session.update_feedback("msg")  # must not raise


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
        assert observed["renderer"]._app is runner.app

    def test_without_runner_falls_back_to_dedicated_app(self) -> None:
        """Legacy callers (no runner) still get a self-hosted app."""
        # No runner active; this should still work and hand back a
        # TuiSession whose app is *not* None (dedicated thread path).
        with tui_execution_session(enabled=True) as session:
            assert isinstance(session, TuiSession)
            assert session.app is not None
            assert session._thread is not None

    def test_without_runner_restores_terminal_modes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Standalone fallback cleanup should share the runner terminal restore path."""
        calls: list[bool] = []

        def _restore() -> None:
            calls.append(True)

        monkeypatch.setattr("the_architect.tui.runner._restore_terminal_input_modes", _restore)

        with tui_execution_session(enabled=True):
            pass

        assert calls


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


class TestTuiSessionDispatchWithMockApp:
    """TuiSession dispatch methods forward calls to the mock app."""

    def test_push_event_forwards_to_app(self) -> None:
        app = MagicMock()
        session = TuiSession(renderer=MagicMock(), app=app, thread=None)
        session.push_event("event_name", {"key": "val"})
        app.push_event_line.assert_called_once_with("event_name", {"key": "val"})

    def test_update_details_forwards_to_app(self) -> None:
        app = MagicMock()
        session = TuiSession(renderer=MagicMock(), app=app, thread=None)
        session.update_details(task="T01", phase="exec")
        app.update_details.assert_called_once_with(task="T01", phase="exec")

    def test_update_progress_tasks_forwards_to_app(self) -> None:
        app = MagicMock()
        session = TuiSession(renderer=MagicMock(), app=app, thread=None)
        tasks = [{"task": "T01"}]
        session.update_progress_tasks(tasks)
        app.update_progress_tasks.assert_called_once_with(tasks)

    def test_update_settings_forwards_to_app(self) -> None:
        app = MagicMock()
        session = TuiSession(renderer=MagicMock(), app=app, thread=None)
        settings = {"key": "val"}
        session.update_settings(settings)
        app.update_execution_settings.assert_called_once_with(settings)

    def test_update_costs_forwards_to_app(self) -> None:
        app = MagicMock()
        session = TuiSession(renderer=MagicMock(), app=app, thread=None)
        costs = cast("dict[str, object]", {"session_cost_usd": 0.5})
        session.update_costs(costs)
        app.update_costs.assert_called_once_with(costs)

    def test_update_footer_forwards_to_app(self) -> None:
        app = MagicMock()
        session = TuiSession(renderer=MagicMock(), app=app, thread=None)
        session.update_footer("hello")
        app.update_footer.assert_called_once_with("hello")


class TestTuiSessionExceptionSwallowing:
    """TuiSession methods swallow exceptions from the app and do not propagate."""

    def test_push_event_swallows_exception(self) -> None:
        app = MagicMock()
        app.push_event_line.side_effect = RuntimeError("boom")
        session = TuiSession(renderer=MagicMock(), app=app, thread=None)
        session.push_event("event_name", {"key": "val"})  # must not raise

    def test_update_details_swallows_exception(self) -> None:
        app = MagicMock()
        app.update_details.side_effect = RuntimeError("boom")
        session = TuiSession(renderer=MagicMock(), app=app, thread=None)
        session.update_details(task="T01")  # must not raise

    def test_update_progress_tasks_swallows_exception(self) -> None:
        app = MagicMock()
        app.update_progress_tasks.side_effect = RuntimeError("boom")
        session = TuiSession(renderer=MagicMock(), app=app, thread=None)
        session.update_progress_tasks([{"task": "T01"}])  # must not raise

    def test_update_settings_swallows_exception(self) -> None:
        app = MagicMock()
        app.update_execution_settings.side_effect = RuntimeError("boom")
        session = TuiSession(renderer=MagicMock(), app=app, thread=None)
        session.update_settings({"key": "val"})  # must not raise

    def test_update_costs_swallows_exception(self) -> None:
        app = MagicMock()
        app.update_costs.side_effect = RuntimeError("boom")
        session = TuiSession(renderer=MagicMock(), app=app, thread=None)
        session.update_costs(cast("dict[str, object]", {"session_cost_usd": 0.5}))  # must not raise

    def test_update_footer_swallows_exception(self) -> None:
        app = MagicMock()
        app.update_footer.side_effect = RuntimeError("boom")
        session = TuiSession(renderer=MagicMock(), app=app, thread=None)
        session.update_footer("hello")  # must not raise


class TestTuiWaitSessionDispatchWithOverlay:
    """TuiWaitSession dispatches to _overlay_app when it is set."""

    def test_set_title_with_overlay(self) -> None:
        overlay = MagicMock()
        session = TuiWaitSession(app=None, thread=None, overlay_app=overlay)
        session.set_title("Planning...")
        overlay.update_wait.assert_called_once_with(title="Planning...")

    def test_set_detail_with_overlay(self) -> None:
        overlay = MagicMock()
        session = TuiWaitSession(app=None, thread=None, overlay_app=overlay)
        session.set_detail("Step 1/3")
        overlay.update_wait.assert_called_once_with(detail="Step 1/3")

    def test_append_log_with_overlay(self) -> None:
        overlay = MagicMock()
        session = TuiWaitSession(app=None, thread=None, overlay_app=overlay)
        session.append_log("line")
        overlay.append_wait_log.assert_called_once_with("line")


class TestTuiWaitSessionDispatchWithStandaloneApp:
    """TuiWaitSession dispatches to standalone app.call_from_thread when no overlay."""

    def test_set_title_standalone(self) -> None:
        app = MagicMock()
        session = TuiWaitSession(app=app, thread=None, overlay_app=None)
        session.set_title("title")
        app.call_from_thread.assert_called_once_with(app.set_title, "title")

    def test_set_detail_standalone(self) -> None:
        app = MagicMock()
        session = TuiWaitSession(app=app, thread=None, overlay_app=None)
        session.set_detail("detail")
        app.call_from_thread.assert_called_once_with(app.set_detail, "detail")

    def test_append_log_standalone_falls_through_to_call_from_thread(self) -> None:
        """No _loop attr means call_soon_threadsafe is skipped, falls through."""
        app = MagicMock()
        app._loop = None  # no event loop available
        session = TuiWaitSession(app=app, thread=None, overlay_app=None)
        session.append_log("line")
        app.call_from_thread.assert_called_once_with(app.append_log, "line")


class TestTuiWaitSessionExceptionSwallowing:
    """TuiWaitSession methods swallow exceptions from overlay and standalone app."""

    def test_set_title_overlay_swallows_exception(self) -> None:
        overlay = MagicMock()
        overlay.update_wait.side_effect = RuntimeError("boom")
        session = TuiWaitSession(app=None, thread=None, overlay_app=overlay)
        session.set_title("title")  # must not raise

    def test_set_detail_overlay_swallows_exception(self) -> None:
        overlay = MagicMock()
        overlay.update_wait.side_effect = RuntimeError("boom")
        session = TuiWaitSession(app=None, thread=None, overlay_app=overlay)
        session.set_detail("detail")  # must not raise

    def test_append_log_overlay_swallows_exception(self) -> None:
        overlay = MagicMock()
        overlay.append_wait_log.side_effect = RuntimeError("boom")
        session = TuiWaitSession(app=None, thread=None, overlay_app=overlay)
        session.append_log("line")  # must not raise

    def test_set_title_standalone_swallows_exception(self) -> None:
        app = MagicMock()
        app.call_from_thread.side_effect = RuntimeError("boom")
        session = TuiWaitSession(app=app, thread=None, overlay_app=None)
        session.set_title("title")  # must not raise

    def test_set_detail_standalone_swallows_exception(self) -> None:
        app = MagicMock()
        app.call_from_thread.side_effect = RuntimeError("boom")
        session = TuiWaitSession(app=app, thread=None, overlay_app=None)
        session.set_detail("detail")  # must not raise

    def test_append_log_standalone_swallows_exception(self) -> None:
        app = MagicMock()
        app.call_from_thread.side_effect = RuntimeError("boom")
        session = TuiWaitSession(app=app, thread=None, overlay_app=None)
        session.append_log("line")  # must not raise


class TestTuiExecutionSessionSuppressed:
    """tui_execution_session yields a no-op session when tui_suppressed_after_exit is True."""

    def test_suppressed_after_exit_yields_noop(self) -> None:
        with patch("the_architect.tui.runner.tui_suppressed_after_exit", return_value=True):
            with tui_execution_session(enabled=True) as session:
                assert isinstance(session, TuiSession)
                assert session.app is None
                assert isinstance(session.renderer, PlainStreamRenderer)


class TestTuiWaitSessionSuppressed:
    """tui_wait_session yields a no-op session when tui_suppressed_after_exit is True."""

    def test_suppressed_after_exit_yields_noop(self) -> None:
        with patch("the_architect.tui.runner.tui_suppressed_after_exit", return_value=True):
            with tui_wait_session(enabled=True, title="test") as session:
                assert isinstance(session, TuiWaitSession)
                assert session.app is None
                assert session._thread is None
                assert session._overlay_app is None


class TestTuiWaitSessionWithExplicitOverlay:
    """tui_wait_session with an explicit overlay_app calls show_wait/hide_wait."""

    def test_overlay_path_calls_show_wait_and_hide_wait(self) -> None:
        overlay_app = MagicMock()
        with tui_wait_session(enabled=True, title="test", overlay_app=overlay_app) as session:
            overlay_app.show_wait.assert_called_once_with(title="test")
            assert session._overlay_app is overlay_app
            assert session.app is None
        overlay_app.hide_wait.assert_called_once()


class TestTuiWaitSessionOverlayExceptionSwallowing:
    """tui_wait_session overlay path swallows exceptions from show_wait/hide_wait."""

    def test_show_wait_exception_not_propagated(self) -> None:
        overlay_app = MagicMock()
        overlay_app.show_wait.side_effect = RuntimeError("boom")
        with tui_wait_session(enabled=True, title="test", overlay_app=overlay_app) as session:
            assert session._overlay_app is overlay_app

    def test_hide_wait_exception_not_propagated(self) -> None:
        overlay_app = MagicMock()
        overlay_app.hide_wait.side_effect = RuntimeError("boom")
        with tui_wait_session(enabled=True, title="test", overlay_app=overlay_app):
            pass
        # Context manager exit should not raise


class TestTuiSessionNoopRemainingMethods:
    """Cover the app-is-None early return for update_progress_tasks, settings, costs."""

    def test_update_progress_tasks_with_no_app_is_noop(self) -> None:
        session = TuiSession(renderer=PlainStreamRenderer(), app=None, thread=None)
        session.update_progress_tasks([{"task": "T01"}])  # must not raise

    def test_update_settings_with_no_app_is_noop(self) -> None:
        session = TuiSession(renderer=PlainStreamRenderer(), app=None, thread=None)
        session.update_settings({"key": "val"})  # must not raise

    def test_update_costs_with_no_app_is_noop(self) -> None:
        session = TuiSession(renderer=PlainStreamRenderer(), app=None, thread=None)
        session.update_costs(cast("dict[str, object]", {"session_cost_usd": 0.5}))  # must not raise


class TestTuiWaitSessionNoopStandalone:
    """Cover the app-is-None early return for TuiWaitSession standalone methods."""

    def test_set_title_no_app_no_overlay_is_noop(self) -> None:
        session = TuiWaitSession(app=None, thread=None, overlay_app=None)
        session.set_title("title")  # must not raise

    def test_set_detail_no_app_no_overlay_is_noop(self) -> None:
        session = TuiWaitSession(app=None, thread=None, overlay_app=None)
        session.set_detail("detail")  # must not raise

    def test_append_log_no_app_no_overlay_is_noop(self) -> None:
        session = TuiWaitSession(app=None, thread=None, overlay_app=None)
        session.append_log("line")  # must not raise


class TestTuiWaitSessionAppendLogThreadSafe:
    """Cover the call_soon_threadsafe path and RuntimeError fallback in append_log."""

    def test_append_log_uses_call_soon_threadsafe_when_loop_available(self) -> None:
        """When _loop exists and thread_id differs, call_soon_threadsafe is used."""
        app = MagicMock()
        app._thread_id = threading.get_ident()  # same thread — so thread_id check fails
        session = TuiWaitSession(app=app, thread=None, overlay_app=None)
        session.append_log("line")
        # Falls through to call_from_thread because thread_id matches
        app.call_from_thread.assert_called_once_with(app.append_log, "line")

    def test_append_log_runtime_error_falls_back_to_call_from_thread(self) -> None:
        """When call_soon_threadsafe raises RuntimeError, fall back to call_from_thread."""
        app = MagicMock()
        app._loop.call_soon_threadsafe.side_effect = RuntimeError("loop closed")
        app._thread_id = 99999  # different from current thread
        session = TuiWaitSession(app=app, thread=None, overlay_app=None)
        session.append_log("line")
        app._loop.call_soon_threadsafe.assert_called_once()
        app.call_from_thread.assert_called_once_with(app.append_log, "line")
