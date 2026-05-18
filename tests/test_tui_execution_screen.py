"""Tests for the ExecutionScreen — pre-mount buffers, progress rendering, cost rendering.

Covers the uncovered paths in execution.py:
- Pre-mount buffer-flush for update_footer, push_event_line, update_costs
- _render_progress branches for model, tokens, current_op, last_activity
- _render_costs branches for session cost, last task cost, model costs
- Exception swallowing in _flush_pending, _write_default_placeholders, _tick_spinner
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from the_architect.tui.screens.execution import (
    ExecutionScreen,
    _fmt_tokens,
    _idle_footer_text,
)


class TestFmtTokens:
    """Test _fmt_tokens helper."""

    def test_small_count_returns_raw(self) -> None:
        assert _fmt_tokens(500) == "500"

    def test_thousand_boundary(self) -> None:
        assert _fmt_tokens(1_000) == "1.0K"

    def test_large_count(self) -> None:
        assert _fmt_tokens(12_300) == "12.3K"


class TestIdleFooterText:
    """Test _idle_footer_text helper."""

    def test_base_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TMUX", raising=False)
        text = _idle_footer_text()
        assert "(idle)" in text
        assert "Ctrl+C=stop" in text
        assert "Ctrl+B D" not in text


class TestPreMountBuffers:
    """Test that pre-mount calls buffer values instead of crashing.

    ExecutionScreen methods called before the DOM is mounted should store
    values in pending buffers (_pending_output, _pending_diagnostics,
    _pending_footer, _pending_costs) rather than querying widgets.
    """

    def test_update_footer_buffers_before_mount(self) -> None:
        screen = ExecutionScreen()
        # query_one will raise because the screen is not mounted
        screen.update_footer("custom footer text")
        assert screen._pending_footer == "custom footer text"

    def test_push_event_line_buffers_before_mount(self) -> None:
        screen = ExecutionScreen()
        screen.push_event_line("retry", {"attempt": "2"})
        assert len(screen._pending_diagnostics) == 1
        event, data = screen._pending_diagnostics[0]
        assert event == "retry"
        assert data == {"attempt": "2"}

    def test_update_costs_buffers_before_mount(self) -> None:
        screen = ExecutionScreen()
        costs = {"session_cost_usd": 1.23, "session_tokens": 5000}
        screen.update_costs(costs)
        # _costs is always updated, but _pending_costs is set when query fails
        assert screen._pending_costs == costs

    def test_push_output_line_buffers_before_mount(self) -> None:
        screen = ExecutionScreen()
        screen.push_output_line("hello world")
        assert screen._pending_output == ["hello world"]
        # last_activity should be updated even when buffering
        assert screen._details["last_activity"] != ""

    def test_multiple_buffered_footer_updates(self) -> None:
        screen = ExecutionScreen()
        screen.update_footer("first")
        screen.update_footer("second")
        # Only the last one is kept
        assert screen._pending_footer == "second"

    def test_multiple_buffered_event_lines(self) -> None:
        screen = ExecutionScreen()
        screen.push_event_line("event_a", None)
        screen.push_event_line("event_b", {"key": "val"})
        assert len(screen._pending_diagnostics) == 2
        assert screen._pending_diagnostics[0][0] == "event_a"
        assert screen._pending_diagnostics[1][0] == "event_b"


class TestFlushPending:
    """Test _flush_pending drains buffers after mount."""

    def test_flush_pending_clears_output_buffer(self) -> None:
        screen = ExecutionScreen()
        screen.push_output_line("line1")
        screen.push_output_line("line2")
        # Simulate mounted state by providing a mock RichLog
        mock_log = MagicMock()
        with patch.object(screen, "query_one", return_value=mock_log):
            screen._flush_pending()
        mock_log.write.assert_called()
        assert screen._pending_output == []

    def test_flush_pending_clears_diagnostics_buffer(self) -> None:
        screen = ExecutionScreen()
        screen.push_event_line("test_event", {"key": "val"})
        mock_log = MagicMock()
        with patch.object(screen, "query_one", return_value=mock_log):
            screen._flush_pending()
        assert screen._pending_diagnostics == []

    def test_flush_pending_clears_footer_buffer(self) -> None:
        screen = ExecutionScreen()
        screen.update_footer("flushed footer")
        mock_footer = MagicMock()
        with patch.object(screen, "query_one", return_value=mock_footer):
            screen._flush_pending()
        # query_one returns the same mock for all selectors, so update()
        # is called for footer + progress + settings + costs refresh.
        # Verify the footer text was among the calls.
        assert any("flushed footer" in str(c) for c in mock_footer.update.call_args_list)
        assert screen._pending_footer is None

    def test_flush_pending_clears_costs_buffer(self) -> None:
        screen = ExecutionScreen()
        costs = {"session_cost_usd": 2.5}
        screen.update_costs(costs)
        mock_static = MagicMock()
        with patch.object(screen, "query_one", return_value=mock_static):
            screen._flush_pending()
        assert screen._pending_costs is None


class TestFlushPendingExceptions:
    """Test that _flush_pending swallows widget query exceptions gracefully."""

    def test_flush_pending_output_exception_swallowed(self) -> None:
        screen = ExecutionScreen()
        screen.push_output_line("boom")
        call_count = 0

        def _query_one(*args: object, **kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            raise Exception("widget not found")

        with patch.object(screen, "query_one", side_effect=_query_one):
            screen._flush_pending()
        # Exception swallowed, buffer cleared
        assert screen._pending_output == []
        assert call_count >= 1

    def test_flush_pending_footer_exception_swallowed(self) -> None:
        screen = ExecutionScreen()
        screen.update_footer("boom")
        with patch.object(screen, "query_one", side_effect=Exception("no widget")):
            screen._flush_pending()
        assert screen._pending_footer is None

    def test_flush_pending_costs_exception_swallowed(self) -> None:
        screen = ExecutionScreen()
        screen.update_costs({"session_cost_usd": 1.0})
        with patch.object(screen, "query_one", side_effect=Exception("no widget")):
            screen._flush_pending()
        # _flush_pending always sets _pending_costs = None after calling
        # update_costs(self._pending_costs), even if update_costs catches
        # its own exception and re-sets _pending_costs.
        assert screen._pending_costs is None


class TestWriteDefaultPlaceholders:
    """Test _write_default_placeholders exception swallowing."""

    def test_placeholder_output_exception_swallowed(self) -> None:
        screen = ExecutionScreen()
        screen._output_received = False
        with patch.object(screen, "query_one", side_effect=Exception("no widget")):
            screen._write_default_placeholders()
        # Should not raise

    def test_placeholder_diagnostics_exception_swallowed(self) -> None:
        screen = ExecutionScreen()
        # Skip output placeholder by setting received=True
        screen._output_received = True
        with patch.object(screen, "query_one", side_effect=Exception("no widget")):
            screen._write_default_placeholders()
        # Should not raise


class TestRenderProgress:
    """Test _render_progress branches for model, tokens, current_op, last_activity."""

    def test_progress_includes_model(self) -> None:
        screen = ExecutionScreen()
        screen.update_details(model="gpt-4")
        output = screen._render_progress()
        assert "gpt-4" in output

    def test_progress_includes_tokens(self) -> None:
        screen = ExecutionScreen()
        screen.update_details(tokens="12.3K")
        output = screen._render_progress()
        assert "12.3K" in output

    def test_progress_includes_current_op(self) -> None:
        screen = ExecutionScreen()
        screen.update_details(current_op="writing tests")
        output = screen._render_progress()
        assert "writing tests" in output

    def test_progress_includes_last_activity(self) -> None:
        screen = ExecutionScreen()
        screen.update_details(last_activity="14:30:00")
        output = screen._render_progress()
        assert "14:30:00" in output

    def test_progress_skips_empty_model(self) -> None:
        screen = ExecutionScreen()
        screen.update_details(model="")
        output = screen._render_progress()
        # Model line should not appear when empty or "—"
        assert "Model        " not in output

    def test_progress_skips_dash_model(self) -> None:
        screen = ExecutionScreen()
        screen.update_details(model="—")
        output = screen._render_progress()
        assert "Model        " not in output

    def test_progress_skips_empty_tokens(self) -> None:
        screen = ExecutionScreen()
        screen.update_details(tokens="")
        output = screen._render_progress()
        assert "Tokens       " not in output

    def test_progress_skips_empty_current_op(self) -> None:
        screen = ExecutionScreen()
        screen.update_details(current_op="")
        output = screen._render_progress()
        assert "Operation    " not in output

    def test_progress_skips_dash_last_activity(self) -> None:
        screen = ExecutionScreen()
        screen.update_details(last_activity="—")
        output = screen._render_progress()
        assert "Last output  " not in output

    def test_progress_with_tasks_shows_task_list(self) -> None:
        screen = ExecutionScreen()
        screen.update_progress_tasks(
            [
                {"prefix": "T01", "title": "First task", "status": "done"},
                {"prefix": "T02", "title": "Second task", "status": "running"},
            ]
        )
        output = screen._render_progress()
        assert "T01" in output
        assert "T02" in output
        assert "2/2 done" not in output  # one running
        assert "1 running" in output

    def test_progress_empty_tasks_shows_placeholder(self) -> None:
        screen = ExecutionScreen()
        output = screen._render_progress()
        assert "Task list will appear when execution starts" in output


class TestRenderCosts:
    """Test _render_costs branches for cost display."""

    def test_costs_empty_shows_placeholder(self) -> None:
        screen = ExecutionScreen()
        output = screen._render_costs()
        assert "Cost data will appear" in output

    def test_costs_shows_session_cost(self) -> None:
        screen = ExecutionScreen()
        screen.update_costs({"session_cost_usd": 1.2345})
        output = screen._render_costs()
        assert "$1.2345" in output

    def test_costs_shows_session_tokens(self) -> None:
        screen = ExecutionScreen()
        screen.update_costs({"session_tokens": 15_000})
        output = screen._render_costs()
        assert "15.0K" in output

    def test_costs_shows_last_task_cost(self) -> None:
        screen = ExecutionScreen()
        screen.update_costs({"last_task_cost_usd": 0.5678})
        output = screen._render_costs()
        assert "$0.5678" in output

    def test_costs_shows_model_costs(self) -> None:
        screen = ExecutionScreen()
        screen.update_costs(
            {
                "model_costs": {
                    "openai/gpt-4": 1.00,
                    "anthropic/claude-3": 2.50,
                }
            }
        )
        output = screen._render_costs()
        assert "gpt-4" in output
        assert "claude-3" in output

    def test_costs_model_not_in_pricing_table(self) -> None:
        """When session_tokens present but session_cost is 0, show pricing table note."""
        screen = ExecutionScreen()
        screen.update_costs({"session_tokens": 5000, "session_cost_usd": 0.0})
        output = screen._render_costs()
        assert "model not in pricing table" in output

    def test_costs_model_cost_zero_shows_name_only(self) -> None:
        """Model cost of 0.0 shows model name without dollar amount."""
        screen = ExecutionScreen()
        screen.update_costs({"model_costs": {"openai/unknown": 0.0}})
        output = screen._render_costs()
        assert "unknown" in output
        # Should not show $0.0000 for zero costs
        assert "$0.0000" not in output


class TestTickSpinnerException:
    """Test that _tick_spinner swallows widget query exceptions."""

    def test_tick_spinner_exception_swallowed(self) -> None:
        screen = ExecutionScreen()
        with patch.object(screen, "query_one", side_effect=Exception("no widget")):
            screen._tick_spinner()
        # Should not raise; frame advanced
        assert screen._frame_index == 1


class TestActionSwitchTabException:
    """Test that action_switch_tab swallows widget query exceptions."""

    def test_switch_tab_exception_swallowed(self) -> None:
        screen = ExecutionScreen()
        with patch.object(screen, "query_one", side_effect=Exception("no widget")):
            screen.action_switch_tab("tab_live")
        # Should not raise


class TestFocusActiveTabScrollerException:
    """Test that _focus_active_tab_scroller swallows widget query exceptions."""

    def test_focus_scroller_exception_swallowed(self) -> None:
        screen = ExecutionScreen()
        with patch.object(screen, "query_one", side_effect=Exception("no widget")):
            screen._focus_active_tab_scroller()
        # Should not raise


class TestPushOutputLineClearException:
    """Test that push_output_line swallows clear() exceptions."""

    def test_push_output_clear_exception_swallowed(self) -> None:
        screen = ExecutionScreen()
        mock_log = MagicMock()
        mock_log.clear.side_effect = Exception("clear failed")
        with patch.object(screen, "query_one", return_value=mock_log):
            screen.push_output_line("line")
        mock_log.write.assert_called_once_with("line")
        assert screen._output_received is True


class TestPushEventLinePreMount:
    """Test push_event_line buffers when widget not available."""

    def test_event_line_buffers_when_query_fails(self) -> None:
        screen = ExecutionScreen()
        with patch.object(screen, "query_one", side_effect=Exception("no widget")):
            screen.push_event_line("test", {"key": "val"})
        assert len(screen._pending_diagnostics) == 1


class TestWriteDiagnosticLineException:
    """Test _write_diagnostic_line exception swallowing."""

    def test_write_diagnostic_exception_swallowed(self) -> None:
        screen = ExecutionScreen()
        with patch.object(screen, "query_one", side_effect=Exception("no widget")):
            screen._write_diagnostic_line("test", None)
        # Should not raise


class TestUpdateDetailsExceptions:
    """Test update_details exception swallowing."""

    def test_update_details_badge_exception_swallowed(self) -> None:
        screen = ExecutionScreen()
        with patch.object(screen, "query_one", side_effect=Exception("no widget")):
            screen.update_details(task="T01")
        # Should not raise; details still updated
        assert screen._details["task"] == "T01"

    def test_update_details_title_exception_swallowed(self) -> None:
        screen = ExecutionScreen()
        mock_badge = MagicMock()

        def _query_side_effect(selector: str, *args: object):
            if "#exec_task_badge" in selector:
                return mock_badge
            raise Exception("no widget")

        with patch.object(screen, "query_one", side_effect=_query_side_effect):
            screen.update_details(task="T01")
        # Should not raise even if anim_title query fails


class TestRefreshSummaryWidgetsExceptions:
    """Test _refresh_summary_widgets exception swallowing."""

    def test_refresh_progress_exception_swallowed(self) -> None:
        screen = ExecutionScreen()
        with patch.object(screen, "query_one", side_effect=Exception("no widget")):
            screen._refresh_summary_widgets()
        # Should not raise


class TestActionPauseMenuException:
    """Test action_pause_menu exception swallowing."""

    def test_pause_menu_no_app_hook(self) -> None:
        screen = ExecutionScreen()
        # The screen is not mounted to an app, so self.app may not
        # expose show_pause_menu. The method should swallow the error.
        screen.action_pause_menu()
        # Should not raise — exception swallowed gracefully


class TestWriteDiagnosticLineWithData:
    """Test _write_diagnostic_line with data payload."""

    def test_diagnostic_line_includes_data(self) -> None:
        screen = ExecutionScreen()
        mock_log = MagicMock()
        with patch.object(screen, "query_one", return_value=mock_log):
            screen._write_diagnostic_line("retry", {"attempt": "3", "reason": "timeout"})
        call_args = mock_log.write.call_args[0][0]
        assert "retry" in call_args
        assert "attempt" in call_args
        assert "3" in call_args


class TestFeedbackDisplay:
    """Test feedback banner rendering in the execution screen footer."""

    def test_update_feedback_sets_message(self) -> None:
        screen = ExecutionScreen()
        # Before mount, query_one raises — feedback should still be stored
        screen.update_feedback("fix the login bug")
        assert screen._feedback_message == "fix the login bug"

    def test_update_feedback_clears_message(self) -> None:
        screen = ExecutionScreen()
        screen.update_feedback("some message")
        screen.update_feedback(None)
        assert screen._feedback_message is None

    def test_render_footer_text_no_feedback(self) -> None:
        screen = ExecutionScreen()
        screen._feedback_message = None
        result = screen._render_footer_text("(idle)  keys here")
        assert result == "(idle)  keys here"

    def test_render_footer_text_with_feedback(self) -> None:
        screen = ExecutionScreen()
        screen._feedback_message = "fix the login bug"
        result = screen._render_footer_text("(idle)  keys here")
        assert "⚡ Feedback" in result
        assert "fix the login bug" in result
        assert "(idle)  keys here" in result

    def test_render_footer_text_truncates_long_message(self) -> None:
        screen = ExecutionScreen()
        long_msg = "A" * 100
        screen._feedback_message = long_msg
        result = screen._render_footer_text("base")
        # Message should be truncated to ~80 chars
        assert "…" in result
        assert len(result) < 200

    def test_render_footer_text_escapes_rich_markup(self) -> None:
        screen = ExecutionScreen()
        screen._feedback_message = "[bold]test[/bold]"
        result = screen._render_footer_text("base")
        # Rich's escape() converts [ to \[ — the brackets are escaped
        # so the message is displayed literally, not as Rich markup tags
        assert "\\[bold]" in result
        assert "\\[/bold]" in result
        assert "⚡ Feedback" in result

    def test_strip_feedback_prefix_removes_prefix(self) -> None:
        screen = ExecutionScreen()
        rendered = "[yellow]⚡ Feedback[/yellow]: some message  ·  (idle)  keys"
        stripped = screen._strip_feedback_prefix(rendered)
        assert stripped == "(idle)  keys"

    def test_strip_feedback_prefix_no_prefix_returns_as_is(self) -> None:
        screen = ExecutionScreen()
        text = "(idle)  keys here"
        assert screen._strip_feedback_prefix(text) == text

    def test_strip_feedback_prefix_partial_no_separator(self) -> None:
        screen = ExecutionScreen()
        # Has prefix marker but no separator — return as-is
        text = "[yellow]⚡ Feedback[/yellow]: msg"
        assert screen._strip_feedback_prefix(text) == text

    def test_update_feedback_updates_pending_footer(self) -> None:
        screen = ExecutionScreen()
        # Set a pending footer first (simulates pre-mount update_footer)
        screen.update_footer("base text")
        assert screen._pending_footer == "base text"
        # Now set feedback — should re-render pending footer with feedback
        screen.update_feedback("new feedback")
        assert screen._pending_footer is not None
        assert "⚡ Feedback" in screen._pending_footer
        assert "base text" in screen._pending_footer

    def test_update_feedback_clear_removes_from_pending(self) -> None:
        screen = ExecutionScreen()
        screen.update_footer("base text")
        screen.update_feedback("some feedback")
        assert "⚡ Feedback" in (screen._pending_footer or "")
        # Clear feedback
        screen.update_feedback(None)
        assert screen._pending_footer == "base text"


class TestFeedbackMounted:
    """Test feedback display when the footer widget is mounted."""

    def test_update_feedback_refreshes_footer(self) -> None:
        screen = ExecutionScreen()
        mock_footer = MagicMock()
        mock_footer.render_str = "base footer"
        with patch.object(screen, "query_one", return_value=mock_footer):
            screen.update_feedback("hello")
        # update_footer was called, which calls query_one again —
        # our mock returns mock_footer for all selectors
        mock_footer.update.assert_called()
        call_arg = mock_footer.update.call_args[0][0]
        assert "⚡ Feedback" in call_arg
        assert "hello" in call_arg

    def test_update_feedback_clear_refreshes_footer(self) -> None:
        screen = ExecutionScreen()
        mock_footer = MagicMock()
        mock_footer.render_str = "[yellow]⚡ Feedback[/yellow]: old  ·  base"
        with patch.object(screen, "query_one", return_value=mock_footer):
            screen.update_feedback(None)
        mock_footer.update.assert_called()
        call_arg = mock_footer.update.call_args[0][0]
        # After clearing, the feedback prefix should be gone
        assert "⚡ Feedback" not in call_arg
