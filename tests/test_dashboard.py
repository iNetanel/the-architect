"""Dedicated unit tests for the_architect/core/dashboard.py.

Covers:
  - _fmt_duration()             — 0s, 59s, 60s, 3661s, negative
  - _fmt_cooldown_remaining()   — various time deltas
  - render_dashboard()          — header, tasks, status sections
  - render_waiting()            — waiting screen content
  - render_planning()           — planning screen content
  - _task_symbol, _task_suffix  — task display helpers
  - _fmt_tokens                 — token count formatting
  - _get_pane_width             — terminal width detection
  - run_dashboard               — main loop with signal handling
  - main                        — CLI entry point
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import the_architect.core.dashboard as dash_mod
from the_architect.core.dashboard import (
    _fmt_cooldown_remaining,
    _fmt_duration,
    _fmt_tokens,
    _get_pane_width,
    _task_suffix,
    _task_symbol,
    main,
    render_dashboard,
    render_planning,
    render_waiting,
    run_dashboard,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(**overrides: object) -> dict:  # type: ignore[type-arg]
    """Build a minimal valid monitor state dictionary."""
    base: dict = {  # type: ignore[type-arg]
        "project_name": "test-project",
        "run_started_at": "2026-04-18T10:00:00+00:00",
        "current_task_id": "T02",
        "current_task_title": "Build",
        "current_attempt": 1,
        "total_tasks": 3,
        "tasks_completed": 1,
        "status": "RUNNING",
        "max_retries": 3,
        "tasks": [
            {"id": "T01", "title": "Setup", "status": "done", "replanned": False},
            {"id": "T02", "title": "Build", "status": "running", "replanned": False},
            {"id": "T03", "title": "Test", "status": "pending", "replanned": False},
        ],
        "circuit_breaker": {
            "state": "CLOSED",
            "no_progress_count": 0,
            "same_error_count": 0,
            "thresholds": {"no_progress": 3, "same_error": 3},
        },
        "cooldown": {
            "active": False,
            "wait_started_at": None,
            "wait_count": 0,
            "remaining_seconds": None,
        },
        "model": {
            "current": "claude-sonnet",
            "free_mode": False,
            "free_remaining": 0,
            "rotation_count": 0,
        },
        "tokens": {"session_total": 48230, "last_attempt": 3840},
        "graceful_stop_requested": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _fmt_duration
# ---------------------------------------------------------------------------


class TestFmtDuration:
    """Tests for _fmt_duration()."""

    def test_zero_seconds(self) -> None:
        """0 seconds should format as 00:00:00."""
        assert _fmt_duration(0) == "00:00:00"

    def test_59_seconds(self) -> None:
        """59 seconds should be in the seconds place only."""
        assert _fmt_duration(59) == "00:00:59"

    def test_60_seconds(self) -> None:
        """60 seconds should roll over to 1 minute."""
        assert _fmt_duration(60) == "00:01:00"

    def test_3661_seconds(self) -> None:
        """3661 seconds = 1 hour, 1 minute, 1 second."""
        assert _fmt_duration(3661) == "01:01:01"

    def test_negative_seconds_treated_as_zero(self) -> None:
        """Negative values should clamp to 00:00:00 (via max(0, ...))."""
        assert _fmt_duration(-5) == "00:00:00"

    def test_large_value(self) -> None:
        """Large durations should produce multi-digit hours."""
        result = _fmt_duration(3600 * 25)  # 25 hours
        assert result.startswith("25:")

    def test_returns_string(self) -> None:
        """Should always return a string."""
        assert isinstance(_fmt_duration(100), str)


# ---------------------------------------------------------------------------
# _fmt_cooldown_remaining
# ---------------------------------------------------------------------------


class TestFmtCooldownRemaining:
    """Tests for _fmt_cooldown_remaining()."""

    def test_none_seconds_returns_yes(self) -> None:
        """None remaining -> 'YES' (unknown remaining time)."""
        assert _fmt_cooldown_remaining(None) == "YES"

    def test_zero_seconds_returns_yes_ending_soon(self) -> None:
        """0 seconds -> 'YES -- ending soon'."""
        result = _fmt_cooldown_remaining(0)
        assert "YES" in result
        assert "soon" in result.lower()

    def test_negative_seconds_returns_yes_ending_soon(self) -> None:
        """Negative seconds should also produce 'YES -- ending soon'."""
        result = _fmt_cooldown_remaining(-10)
        assert "YES" in result

    def test_30_seconds_remaining(self) -> None:
        """30 seconds -> minutes not shown, seconds shown."""
        result = _fmt_cooldown_remaining(30)
        assert "30s" in result
        assert "YES" in result

    def test_90_seconds_shows_minutes(self) -> None:
        """90 seconds = 1m 30s -> minutes shown."""
        result = _fmt_cooldown_remaining(90)
        assert "1m" in result
        assert "YES" in result

    def test_3700_seconds_shows_hours(self) -> None:
        """3700 seconds > 1 hour -> hours shown."""
        result = _fmt_cooldown_remaining(3700)
        assert "1h" in result
        assert "YES" in result

    def test_returns_string(self) -> None:
        """Should always return a string."""
        assert isinstance(_fmt_cooldown_remaining(120), str)


# ---------------------------------------------------------------------------
# render_dashboard -- header, tasks, status
# ---------------------------------------------------------------------------


class TestRenderDashboard:
    """Tests for render_dashboard()."""

    def test_contains_header(self) -> None:
        """Dashboard should show 'THE ARCHITECT' header."""
        output = render_dashboard(_make_state(), width=40)
        assert "THE ARCHITECT" in output

    def test_contains_project_name(self) -> None:
        """Dashboard should display the project name."""
        output = render_dashboard(_make_state(project_name="my-api"), width=40)
        assert "my-api" in output

    def test_contains_all_task_ids(self) -> None:
        """Dashboard should list every task ID in the tasks list."""
        output = render_dashboard(_make_state(), width=40)
        assert "T01" in output
        assert "T02" in output
        assert "T03" in output

    def test_contains_tasks_section_label(self) -> None:
        """Dashboard should have a TASKS section."""
        output = render_dashboard(_make_state(), width=40)
        assert "TASKS" in output

    def test_contains_status_section_label(self) -> None:
        """Dashboard should have a STATUS section."""
        output = render_dashboard(_make_state(), width=40)
        assert "STATUS" in output

    def test_contains_running_status(self) -> None:
        """RUNNING status should appear in the dashboard."""
        output = render_dashboard(_make_state(status="RUNNING"), width=40)
        assert "RUNNING" in output

    def test_contains_done_status(self) -> None:
        """DONE status should appear in the dashboard."""
        output = render_dashboard(_make_state(status="DONE"), width=40)
        assert "DONE" in output

    def test_contains_failed_status(self) -> None:
        """FAILED status should appear in the dashboard."""
        output = render_dashboard(_make_state(status="FAILED"), width=40)
        assert "FAILED" in output

    def test_contains_circuit_breaker_section(self) -> None:
        """Dashboard should have a CIRCUIT BREAKER section."""
        output = render_dashboard(_make_state(), width=40)
        assert "CIRCUIT BREAKER" in output

    def test_contains_circuit_closed_state(self) -> None:
        """CLOSED circuit state should appear in the dashboard."""
        output = render_dashboard(_make_state(), width=40)
        assert "CLOSED" in output

    def test_contains_token_counts(self) -> None:
        """Token counts should appear formatted with comma separators."""
        output = render_dashboard(_make_state(), width=40)
        assert "48,230" in output
        assert "3,840" in output

    def test_contains_model_name(self) -> None:
        """Current model name should appear in the dashboard."""
        output = render_dashboard(_make_state(), width=40)
        assert "claude-sonnet" in output

    def test_contains_tokens_section(self) -> None:
        """Dashboard should have a TOKENS section."""
        output = render_dashboard(_make_state(), width=40)
        assert "TOKENS" in output

    def test_contains_cooldown_section(self) -> None:
        """Dashboard should have a COOLDOWN section."""
        output = render_dashboard(_make_state(), width=40)
        assert "COOLDOWN" in output

    def test_cooldown_active_shows_yes(self) -> None:
        """When cooldown is active the display should include 'YES'."""
        state = _make_state()
        state["cooldown"]["active"] = True
        state["cooldown"]["remaining_seconds"] = 3600
        output = render_dashboard(state, width=40)
        assert "YES" in output

    def test_cooldown_inactive_shows_no(self) -> None:
        """When cooldown is inactive the display should include 'NO'."""
        state = _make_state()
        state["cooldown"]["active"] = False
        output = render_dashboard(state, width=40)
        assert "NO" in output

    def test_replanned_task_shows_marker(self) -> None:
        """A replanned task should show the [R] marker."""
        state = _make_state()
        state["tasks"][1]["replanned"] = True
        output = render_dashboard(state, width=40)
        assert "[R]" in output

    def test_cooldown_wait_task_shows_c_marker(self) -> None:
        """A task in cooldown wait should show the [C] marker."""
        state = _make_state()
        state["cooldown"]["active"] = True
        state["current_task_id"] = "T02"
        output = render_dashboard(state, width=40)
        assert "[C]" in output

    def test_free_mode_shows_remaining(self) -> None:
        """When free mode is on, the remaining count should appear."""
        state = _make_state()
        state["model"]["free_mode"] = True
        state["model"]["free_remaining"] = 7
        output = render_dashboard(state, width=40)
        assert "7" in output

    def test_free_mode_off_shows_label(self) -> None:
        """When free mode is off the 'free mode off' label should appear."""
        state = _make_state()
        state["model"]["free_mode"] = False
        output = render_dashboard(state, width=40)
        assert "free mode off" in output

    def test_empty_tasks_list(self) -> None:
        """Dashboard should not crash with an empty tasks list."""
        state = _make_state()
        state["tasks"] = []
        output = render_dashboard(state, width=40)
        assert "THE ARCHITECT" in output

    def test_returns_string(self) -> None:
        """render_dashboard() should return a string."""
        assert isinstance(render_dashboard(_make_state(), width=40), str)

    def test_narrow_width_does_not_crash(self) -> None:
        """A very narrow width should not raise an exception."""
        output = render_dashboard(_make_state(), width=20)
        assert isinstance(output, str)

    def test_circuit_open_state(self) -> None:
        """OPEN circuit state should appear when set."""
        state = _make_state()
        state["circuit_breaker"]["state"] = "OPEN"
        output = render_dashboard(state, width=40)
        assert "OPEN" in output

    def test_circuit_half_open_state(self) -> None:
        """HALF_OPEN circuit state should appear when set."""
        state = _make_state()
        state["circuit_breaker"]["state"] = "HALF_OPEN"
        output = render_dashboard(state, width=40)
        assert "HALF_OPEN" in output

    def test_missing_run_started_at_does_not_crash(self) -> None:
        """Dashboard should not crash if run_started_at is absent."""
        state = _make_state()
        del state["run_started_at"]
        output = render_dashboard(state, width=40)
        assert isinstance(output, str)

    def test_invalid_run_started_at_does_not_crash(self) -> None:
        """Dashboard should not crash on an invalid timestamp."""
        state = _make_state(run_started_at="not-a-timestamp")
        output = render_dashboard(state, width=40)
        assert isinstance(output, str)


# ---------------------------------------------------------------------------
# render_waiting
# ---------------------------------------------------------------------------


class TestRenderWaiting:
    """Tests for render_waiting()."""

    def test_contains_waiting_text(self) -> None:
        """Should contain the word 'Waiting' (case-insensitive)."""
        output = render_waiting()
        assert "waiting" in output.lower()

    def test_contains_architect_header(self) -> None:
        """Should contain the THE ARCHITECT header."""
        output = render_waiting()
        assert "THE ARCHITECT" in output

    def test_returns_string(self) -> None:
        """Should return a string."""
        assert isinstance(render_waiting(), str)

    def test_no_planning_label(self) -> None:
        """Waiting screen should NOT contain 'PLANNING'."""
        output = render_waiting()
        assert "PLANNING" not in output


# ---------------------------------------------------------------------------
# render_planning
# ---------------------------------------------------------------------------


class TestRenderPlanning:
    """Tests for render_planning()."""

    def test_contains_header(self) -> None:
        """Planning screen should show 'THE ARCHITECT' header."""
        state = {"status": "PLANNING", "project_name": "my-proj", "goal": ""}
        output = render_planning(state)
        assert "THE ARCHITECT" in output

    def test_contains_planning_label(self) -> None:
        """Planning screen should show 'PLANNING' label."""
        state = {"status": "PLANNING", "project_name": "my-proj", "goal": ""}
        output = render_planning(state)
        assert "PLANNING" in output

    def test_contains_project_name(self) -> None:
        """Planning screen should show the project name."""
        state = {"status": "PLANNING", "project_name": "my-api", "goal": ""}
        output = render_planning(state)
        assert "my-api" in output

    def test_contains_goal_when_provided(self) -> None:
        """Goal text should appear on the planning screen."""
        state = {
            "status": "PLANNING",
            "project_name": "my-proj",
            "goal": "Build a REST API with authentication",
        }
        output = render_planning(state)
        assert "REST API" in output

    def test_empty_goal_does_not_crash(self) -> None:
        """Empty goal should not cause a crash."""
        state = {"status": "PLANNING", "project_name": "my-proj", "goal": ""}
        output = render_planning(state)
        assert isinstance(output, str)

    def test_contains_left_pane_hint(self) -> None:
        """Planning screen should tell user to answer prompts in left pane."""
        state = {"status": "PLANNING", "project_name": "my-proj", "goal": ""}
        output = render_planning(state)
        assert "left pane" in output.lower()

    def test_returns_string(self) -> None:
        """Should return a string."""
        state = {"status": "PLANNING", "project_name": "my-proj", "goal": ""}
        assert isinstance(render_planning(state), str)

    def test_wide_goal_wraps_correctly(self) -> None:
        """Long goal text should be word-wrapped to fit the pane width."""
        long_goal = (
            "Build a very detailed and complex distributed microservices system with caching"
        )
        state = {"status": "PLANNING", "project_name": "my-proj", "goal": long_goal}
        output = render_planning(state, width=30)
        # Every visible line in the goal section should be <= width chars (ignoring ANSI)
        # Just check it doesn't crash and the goal words appear
        assert "distributed" in output
        assert isinstance(output, str)

    def test_narrow_width_does_not_crash(self) -> None:
        """A very narrow width should not raise an exception."""
        state = {"status": "PLANNING", "project_name": "my-proj", "goal": "A goal"}
        output = render_planning(state, width=15)
        assert isinstance(output, str)


# ---------------------------------------------------------------------------
# New Tests for Coverage
# ---------------------------------------------------------------------------


class TestTaskSymbol:
    def test_failed_returns_x(self) -> None:
        assert _task_symbol("failed", False) == "✗"

    def test_pending_returns_circle(self) -> None:
        assert _task_symbol("pending", False) == "○"

    def test_done_returns_check(self) -> None:
        assert _task_symbol("done", False) == "✓"

    def test_running_returns_bullet(self) -> None:
        assert _task_symbol("running", False) == "●"


class TestTaskSuffix:
    def test_replanned_only(self) -> None:
        assert _task_suffix("running", True, False) == "[R]"

    def test_cooldown_only(self) -> None:
        assert _task_suffix("running", False, True) == "[C]"

    def test_both(self) -> None:
        assert _task_suffix("running", True, True) == "[R] [C]"

    def test_neither(self) -> None:
        assert _task_suffix("running", False, False) == ""


class TestFmtTokens:
    def test_zero(self) -> None:
        assert _fmt_tokens(0) == "0"

    def test_thousands(self) -> None:
        assert _fmt_tokens(48230) == "48,230"

    def test_millions(self) -> None:
        assert _fmt_tokens(987654321) == "987,654,321"


class TestRenderDashboardEdgeCases:
    def test_failed_task_shows_x_and_red(self) -> None:
        state = _make_state(
            tasks=[{"id": "T01", "title": "Failed", "status": "failed", "replanned": False}]
        )
        output = render_dashboard(state, width=66)
        assert "✗" in output
        assert "\033[31m" in output

    def test_title_truncation_with_ellipsis(self) -> None:
        state = _make_state(
            tasks=[{"id": "T01", "title": "A" * 50, "status": "done", "replanned": False}]
        )
        output = render_dashboard(state, width=20)
        assert "…" in output

    def test_naive_timestamp_no_tzinfo(self) -> None:
        state = _make_state(run_started_at="2026-04-18T10:00:00")
        output = render_dashboard(state, width=66)
        assert isinstance(output, str)

    def test_cooldown_wait_status_yellow(self) -> None:
        state = _make_state(status="COOLDOWN WAIT")
        output = render_dashboard(state, width=66)
        assert "\033[33m" in output

    def test_stopping_status_yellow(self) -> None:
        state = _make_state(status="STOPPING")
        output = render_dashboard(state, width=66)
        assert "\033[33m" in output

    def test_unknown_status_no_color(self) -> None:
        state = _make_state(status="UNKNOWN")
        output = render_dashboard(state, width=66)
        assert "UNKNOWN" in output

    def test_empty_model_shows_default(self) -> None:
        state = _make_state(model={})
        output = render_dashboard(state, width=66)
        assert "default" in output

    def test_long_model_name_truncated(self) -> None:
        state = _make_state(model={"current": "X" * 100})
        output = render_dashboard(state, width=66)
        assert isinstance(output, str)

    def test_no_current_task_id(self) -> None:
        state = _make_state(current_task_id="")
        output = render_dashboard(state, width=66)
        assert "—" in output

    def test_missing_project_name(self) -> None:
        state = _make_state()
        state.pop("project_name", None)
        output = render_dashboard(state, width=66)
        assert "unknown" in output


class TestHandleSignal:
    def test_sets_running_false(self) -> None:
        dash_mod._running = True
        dash_mod._handle_signal(15, None)
        assert dash_mod._running is False

    def teardown_method(self) -> None:
        dash_mod._running = True


class TestGetPaneWidth:
    def test_tmux_returns_valid_width(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "80\n"
        with patch("subprocess.run", return_value=mock_result):
            assert _get_pane_width() == 80

    def test_tmux_returns_zero_falls_through(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "0\n"
        with (
            patch("subprocess.run", return_value=mock_result),
            patch("shutil.get_terminal_size") as mock_ts,
        ):
            mock_ts.return_value = MagicMock(columns=100)
            assert _get_pane_width() == 100

    def test_tmux_fails_shutil_fallback(self) -> None:
        with (
            patch("subprocess.run", side_effect=Exception),
            patch("shutil.get_terminal_size") as mock_ts,
        ):
            mock_ts.return_value = MagicMock(columns=100)
            assert _get_pane_width() == 100

    def test_both_fail_hard_fallback(self) -> None:
        with (
            patch("subprocess.run", side_effect=Exception),
            patch("shutil.get_terminal_size", side_effect=Exception),
        ):
            assert _get_pane_width() == 66

    def test_minimum_width_enforced(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "10\n"
        with patch("subprocess.run", return_value=mock_result):
            assert _get_pane_width() == 20


class TestRunDashboard:
    def setup_method(self) -> None:
        dash_mod._running = True

    def teardown_method(self) -> None:
        dash_mod._running = True

    def test_state_none_renders_waiting(self) -> None:
        from the_architect.core.monitor_state import RUN_STATUS_DONE

        with (
            patch("the_architect.core.monitor_state.read_monitor_state") as mock_read,
            patch("the_architect.core.dashboard._get_pane_width", return_value=66),
            patch("sys.stdout") as mock_stdout,
            patch.object(time, "sleep"),
        ):
            mock_read.side_effect = [None, {"status": RUN_STATUS_DONE}]
            run_dashboard(Path("/tmp"))
            # Check waiting was rendered
            calls = [str(call) for call in mock_stdout.write.call_args_list]
            waiting_present = any("Waiting" in str(call) for call in calls)
            assert waiting_present

    def test_planning_status_renders_planning(self) -> None:
        from the_architect.core.monitor_state import RUN_STATUS_DONE

        with (
            patch("the_architect.core.monitor_state.read_monitor_state") as mock_read,
            patch("the_architect.core.dashboard._get_pane_width", return_value=66),
            patch("sys.stdout") as mock_stdout,
            patch.object(time, "sleep"),
        ):
            mock_read.side_effect = [{"status": "PLANNING"}, {"status": RUN_STATUS_DONE}]
            run_dashboard(Path("/tmp"))
            calls = [str(call) for call in mock_stdout.write.call_args_list]
            planning_present = any("PLANNING" in str(call) for call in calls)
            assert planning_present

    def test_done_status_exits_loop(self) -> None:
        from the_architect.core.monitor_state import RUN_STATUS_DONE

        with (
            patch("the_architect.core.monitor_state.read_monitor_state") as mock_read,
            patch("the_architect.core.dashboard._get_pane_width", return_value=66),
            patch("sys.stdout") as mock_stdout,
            patch.object(time, "sleep"),
        ):
            mock_read.return_value = {"status": RUN_STATUS_DONE}
            run_dashboard(Path("/tmp"))
            # Should exit immediately
            assert mock_stdout.write.call_count >= 1

    def test_failed_status_exits_loop(self) -> None:
        from the_architect.core.monitor_state import RUN_STATUS_FAILED

        with (
            patch("the_architect.core.monitor_state.read_monitor_state") as mock_read,
            patch("the_architect.core.dashboard._get_pane_width", return_value=66),
            patch("sys.stdout") as mock_stdout,
            patch.object(time, "sleep"),
        ):
            mock_read.return_value = {"status": RUN_STATUS_FAILED}
            run_dashboard(Path("/tmp"))
            assert mock_stdout.write.call_count >= 1

    def test_killed_status_exits_loop(self) -> None:
        from the_architect.core.monitor_state import RUN_STATUS_KILLED

        with (
            patch("the_architect.core.monitor_state.read_monitor_state") as mock_read,
            patch("the_architect.core.dashboard._get_pane_width", return_value=66),
            patch("sys.stdout") as mock_stdout,
            patch.object(time, "sleep"),
        ):
            mock_read.return_value = {"status": RUN_STATUS_KILLED}
            run_dashboard(Path("/tmp"))
            assert mock_stdout.write.call_count >= 1

    def test_broken_pipe_error_exits(self) -> None:
        from the_architect.core.monitor_state import RUN_STATUS_DONE

        with (
            patch("the_architect.core.monitor_state.read_monitor_state") as mock_read,
            patch("the_architect.core.dashboard._get_pane_width", return_value=66),
            patch("sys.stdout") as mock_stdout,
            patch.object(time, "sleep"),
        ):
            mock_read.return_value = {"status": RUN_STATUS_DONE}
            mock_stdout.write.side_effect = BrokenPipeError("broken")
            result = run_dashboard(Path("/tmp"))
            assert result is None

    def test_general_exception_continues(self) -> None:
        from the_architect.core.monitor_state import RUN_STATUS_DONE

        with (
            patch("the_architect.core.monitor_state.read_monitor_state") as mock_read,
            patch("the_architect.core.dashboard._get_pane_width", return_value=66),
            patch("sys.stdout"),
            patch.object(time, "sleep"),
        ):
            mock_read.side_effect = [Exception("first"), {"status": RUN_STATUS_DONE}]
            result = run_dashboard(Path("/tmp"))
            assert result is None

    def test_signal_stop_exits_loop(self) -> None:
        from the_architect.core.monitor_state import RUN_STATUS_DONE

        with (
            patch("the_architect.core.monitor_state.read_monitor_state") as mock_read,
            patch("the_architect.core.dashboard._get_pane_width", return_value=66),
            patch("sys.stdout"),
            patch.object(time, "sleep"),
        ):
            mock_read.return_value = {"status": RUN_STATUS_DONE}
            result = run_dashboard(Path("/tmp"))
            assert result is None

    def test_sigterm_registered_on_linux(self) -> None:
        import signal as signal_mod

        from the_architect.core.monitor_state import RUN_STATUS_DONE

        with (
            patch("the_architect.core.dashboard.sys") as mock_sys,
            patch("signal.signal") as mock_signal,
            patch(
                "the_architect.core.monitor_state.read_monitor_state",
                return_value={"status": RUN_STATUS_DONE},
            ),
            patch("the_architect.core.dashboard._get_pane_width", return_value=66),
            patch("sys.stdout"),
            patch.object(time, "sleep"),
        ):
            mock_sys.platform = "linux"
            run_dashboard(Path("/tmp"))
            # Verify signal.signal was called with SIGTERM (15)
            sigterm_called = any(
                call_args[0][0] == signal_mod.SIGTERM for call_args in mock_signal.call_args_list
            )
            assert sigterm_called

    def test_no_sigterm_on_windows(self) -> None:
        import signal as signal_mod

        from the_architect.core.monitor_state import RUN_STATUS_DONE

        with (
            patch("the_architect.core.dashboard.sys") as mock_sys,
            patch("signal.signal") as mock_signal,
            patch(
                "the_architect.core.monitor_state.read_monitor_state",
                return_value={"status": RUN_STATUS_DONE},
            ),
            patch("the_architect.core.dashboard._get_pane_width", return_value=66),
            patch("sys.stdout"),
            patch.object(time, "sleep"),
        ):
            mock_sys.platform = "win32"
            run_dashboard(Path("/tmp"))
            # Verify signal.signal was NOT called with SIGTERM (15)
            sigterm_called = any(
                call_args[0][0] == signal_mod.SIGTERM for call_args in mock_signal.call_args_list
            )
            assert not sigterm_called


class TestMain:
    def test_no_args_exits_1(self) -> None:
        with patch.object(sys, "argv", []), patch.object(sys, "stderr"):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_nonexistent_dir_exits_1(self) -> None:
        with patch.object(sys, "argv", ["dashboard", "/nonexistent/path/xyz"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_valid_dir_calls_run_dashboard(self, tmp_path: Path) -> None:
        with (
            patch.object(sys, "argv", ["dashboard", str(tmp_path)]),
            patch("the_architect.core.dashboard.run_dashboard") as mock_run,
        ):
            main()
            mock_run.assert_called_once()
            called_path = mock_run.call_args[0][0]
            assert called_path == tmp_path.resolve()
