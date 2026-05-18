"""Tests for the SuccessScreen TUI component and its formatting helpers.

The SuccessScreen is the final screen shown after a successful Architect run.
It renders task results, token usage, retrospective rounds, and allows the
user to exit with ``q``, ``Enter``, or ``Escape``.
"""

from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import Static

from the_architect.core.runner import TaskResult, TokenUsage
from the_architect.core.success import RetrospectiveRound
from the_architect.tui.screens.success import (
    SuccessScreen,
    _fmt_duration,
    _fmt_model,
    _fmt_tokens,
)
from the_architect.tui.widgets import MatrixRain


# Minimal host app for pushing SuccessScreen in tests.
class _HostApp(App[None]):
    """Plain Textual App used only as a container for SuccessScreen tests."""

    pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_token_usage(
    input_tokens: int = 5000,
    output_tokens: int = 2000,
    cache_read: int = 0,
    cache_write: int = 0,
) -> TokenUsage:
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
    )


def _make_task_result(
    prefix: str = "T01",
    title: str = "Test task",
    status: str = "done",
    duration: float = 45.0,
    attempts: int = 1,
    input_tokens: int = 5000,
    output_tokens: int = 2000,
    model: str = "anthropic/claude-sonnet-4",
    rate_limit_hit: bool = False,
) -> TaskResult:
    return TaskResult(
        prefix=prefix,
        title=title,
        status=status,
        duration_seconds=duration,
        attempts=attempts,
        tokens=_make_token_usage(input_tokens, output_tokens),
        model=model,
        rate_limit_hit=rate_limit_hit,
    )


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


class TestFmtDuration:
    def test_negative_returns_dash(self) -> None:
        assert _fmt_duration(-1) == "—"

    def test_zero_seconds(self) -> None:
        assert _fmt_duration(0) == "0:00"

    def test_under_one_minute(self) -> None:
        assert _fmt_duration(30) == "0:30"

    def test_exact_one_minute(self) -> None:
        assert _fmt_duration(60) == "1:00"

    def test_minutes_and_seconds(self) -> None:
        assert _fmt_duration(125) == "2:05"

    def test_exact_one_hour(self) -> None:
        assert _fmt_duration(3600) == "1:00:00"

    def test_hours_minutes_seconds(self) -> None:
        assert _fmt_duration(3725) == "1:02:05"

    def test_float_seconds_truncated(self) -> None:
        assert _fmt_duration(65.9) == "1:05"


class TestFmtTokens:
    def test_zero_tokens(self) -> None:
        assert _fmt_tokens(0) == "0"

    def test_small_count(self) -> None:
        assert _fmt_tokens(42) == "42"

    def test_exactly_999(self) -> None:
        assert _fmt_tokens(999) == "999"

    def test_exactly_1000(self) -> None:
        assert _fmt_tokens(1000) == "1.0K"

    def test_over_1000(self) -> None:
        assert _fmt_tokens(1234) == "1.2K"

    def test_large_count(self) -> None:
        assert _fmt_tokens(15000) == "15.0K"


class TestFmtModel:
    def test_empty_string(self) -> None:
        assert _fmt_model("") == "—"

    def test_anthropic_prefix_stripped(self) -> None:
        assert _fmt_model("anthropic/claude-sonnet-4") == "claude-sonnet-4"

    def test_openai_prefix_stripped(self) -> None:
        assert _fmt_model("openai/gpt-4o") == "gpt-4o"

    def test_openrouter_anthropic_prefix_stripped(self) -> None:
        assert _fmt_model("openrouter/anthropic/claude-sonnet-4") == "claude-sonnet-4"

    def test_openrouter_prefix_stripped(self) -> None:
        assert _fmt_model("openrouter/google/gemini-pro") == "google/gemini-pro"

    def test_unknown_prefix_unchanged(self) -> None:
        assert _fmt_model("custom/my-model") == "custom/my-model"

    def test_no_prefix_unchanged(self) -> None:
        assert _fmt_model("claude-sonnet-4") == "claude-sonnet-4"


# ---------------------------------------------------------------------------
# SuccessScreen rendering
# ---------------------------------------------------------------------------


class TestSuccessScreenRendering:
    @pytest.mark.asyncio
    async def test_screen_composes_with_header_footer(self) -> None:
        """The screen includes a Header, body, and Footer."""
        results = [_make_task_result()]
        tokens = _make_token_usage()
        screen = SuccessScreen(
            results=results,
            total_duration=45.0,
            total_tokens=tokens,
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause(0.05)
            assert isinstance(app.screen, SuccessScreen)
            # Header and Footer are composed by the SuccessScreen
            assert screen.query_one("Header")
            assert screen.query_one("Footer")

    @pytest.mark.asyncio
    async def test_screen_shows_success_headline_all_done(self) -> None:
        """Headline shows checkmark when all tasks are done."""
        results = [_make_task_result(prefix="T01"), _make_task_result(prefix="T02")]
        tokens = _make_token_usage()
        screen = SuccessScreen(
            results=results,
            total_duration=90.0,
            total_tokens=tokens,
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause(0.05)
            headline = screen.query_one("#success_headline", Static)
            rendered = str(headline.render())
            assert "2" in rendered
            assert "task" in rendered.lower() or "Task" in rendered

    @pytest.mark.asyncio
    async def test_screen_shows_failure_headline(self) -> None:
        """Headline shows failure indicator when tasks failed."""
        results = [
            _make_task_result(prefix="T01", status="done"),
            _make_task_result(prefix="T02", status="failed"),
        ]
        tokens = _make_token_usage()
        screen = SuccessScreen(
            results=results,
            total_duration=90.0,
            total_tokens=tokens,
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause(0.05)
            headline = screen.query_one("#success_headline", Static)
            rendered = str(headline.render())
            assert "1" in rendered
            assert "failed" in rendered.lower() or "Failed" in rendered

    @pytest.mark.asyncio
    async def test_screen_shows_matrix_rain(self) -> None:
        """The MatrixRain widget is rendered in the title area."""
        results = [_make_task_result()]
        tokens = _make_token_usage()
        screen = SuccessScreen(
            results=results,
            total_duration=30.0,
            total_tokens=tokens,
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause(0.05)
            rain = screen.query_one("#success_rain", MatrixRain)
            assert rain.region.width == MatrixRain.COLS
            assert rain.region.height == MatrixRain.ROWS

    @pytest.mark.asyncio
    async def test_screen_shows_duration_and_tokens(self) -> None:
        """Summary line contains formatted duration and token count."""
        results = [_make_task_result()]
        tokens = _make_token_usage(input_tokens=5000, output_tokens=3000)
        screen = SuccessScreen(
            results=results,
            total_duration=125.0,
            total_tokens=tokens,
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause(0.05)
            summary = screen.query_one("#success_summary_line", Static)
            rendered = str(summary.render())
            assert "2:05" in rendered  # duration
            assert "8.0K" in rendered  # total tokens

    @pytest.mark.asyncio
    async def test_screen_shows_retry_count(self) -> None:
        """Summary line mentions retries when tasks had multiple attempts."""
        results = [_make_task_result(attempts=3)]
        tokens = _make_token_usage()
        screen = SuccessScreen(
            results=results,
            total_duration=120.0,
            total_tokens=tokens,
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause(0.05)
            summary = screen.query_one("#success_summary_line", Static)
            rendered = str(summary.render())
            assert "2 retr" in rendered  # 3 attempts - 1 task = 2 retries

    @pytest.mark.asyncio
    async def test_screen_shows_rate_limit_info(self) -> None:
        """Summary line mentions rate limits when a task hit one."""
        results = [_make_task_result(rate_limit_hit=True)]
        tokens = _make_token_usage()
        screen = SuccessScreen(
            results=results,
            total_duration=60.0,
            total_tokens=tokens,
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause(0.05)
            summary = screen.query_one("#success_summary_line", Static)
            rendered = str(summary.render())
            assert "1 rate-limited" in rendered

    @pytest.mark.asyncio
    async def test_screen_shows_task_table(self) -> None:
        """Task table widget renders with task prefixes."""
        results = [_make_task_result(prefix="T01", title="Setup")]
        tokens = _make_token_usage()
        screen = SuccessScreen(
            results=results,
            total_duration=45.0,
            total_tokens=tokens,
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause(0.05)
            table = screen.query_one("#success_task_table", Static)
            rendered = str(table.render())
            assert "T01" in rendered

    @pytest.mark.asyncio
    async def test_screen_shows_retrospective_rounds(self) -> None:
        """Retrospective section appears when rounds are provided."""
        results = [_make_task_result()]
        tokens = _make_token_usage()
        rounds = [
            RetrospectiveRound(
                round_number=1,
                issues_found=2,
                fixes_planned=1,
                tasks_created=["R01"],
            )
        ]
        screen = SuccessScreen(
            results=results,
            total_duration=45.0,
            total_tokens=tokens,
            retrospective_rounds=rounds,
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause(0.05)
            retro = screen.query_one("#success_retro", Static)
            rendered = str(retro.render())
            assert "Retrospective" in rendered
            assert "2" in rendered  # issues_found
            assert "R01" in rendered

    @pytest.mark.asyncio
    async def test_screen_hides_retrospective_when_empty(self) -> None:
        """No retrospective widget when rounds list is empty or None."""
        results = [_make_task_result()]
        tokens = _make_token_usage()
        screen = SuccessScreen(
            results=results,
            total_duration=45.0,
            total_tokens=tokens,
            retrospective_rounds=[],
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause(0.05)
            # The retro widget should not be composed
            retro_widgets = list(screen.query("#success_retro"))
            assert len(retro_widgets) == 0

    @pytest.mark.asyncio
    async def test_screen_shows_summary_path(self) -> None:
        """Summary file path widget appears when success_md_path is set."""
        results = [_make_task_result()]
        tokens = _make_token_usage()
        screen = SuccessScreen(
            results=results,
            total_duration=45.0,
            total_tokens=tokens,
            success_md_path="tasks/SUMMARY.md",
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause(0.05)
            file_widget = screen.query_one("#success_file", Static)
            rendered = str(file_widget.render())
            assert "SUMMARY.md" in rendered

    @pytest.mark.asyncio
    async def test_screen_hides_summary_path_when_none(self) -> None:
        """No summary file widget when success_md_path is None."""
        results = [_make_task_result()]
        tokens = _make_token_usage()
        screen = SuccessScreen(
            results=results,
            total_duration=45.0,
            total_tokens=tokens,
            success_md_path=None,
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause(0.05)
            file_widgets = list(screen.query("#success_file"))
            assert len(file_widgets) == 0

    @pytest.mark.asyncio
    async def test_screen_shows_exit_hint(self) -> None:
        """Exit hint is always visible."""
        results = [_make_task_result()]
        tokens = _make_token_usage()
        screen = SuccessScreen(
            results=results,
            total_duration=45.0,
            total_tokens=tokens,
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause(0.05)
            hint = screen.query_one("#success_hint", Static)
            rendered = str(hint.render())
            assert "exit" in rendered.lower() or "Exit" in rendered

    @pytest.mark.asyncio
    async def test_screen_totals_line(self) -> None:
        """Totals line shows done count and duration."""
        results = [_make_task_result(prefix="T01"), _make_task_result(prefix="T02")]
        tokens = _make_token_usage(input_tokens=5000, output_tokens=3000)
        screen = SuccessScreen(
            results=results,
            total_duration=125.0,
            total_tokens=tokens,
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause(0.05)
            totals = screen.query_one("#success_totals", Static)
            rendered = str(totals.render())
            assert "2/2" in rendered
            assert "TOTAL" in rendered


# ---------------------------------------------------------------------------
# SuccessScreen exit actions
# ---------------------------------------------------------------------------


class TestSuccessScreenExit:
    @pytest.mark.asyncio
    async def test_action_exit_screen_dismisses_with_true(self) -> None:
        """Pressing exit key dismisses the screen returning True."""
        results = [_make_task_result()]
        tokens = _make_token_usage()
        screen = SuccessScreen(
            results=results,
            total_duration=45.0,
            total_tokens=tokens,
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen, callback=lambda result: setattr(app, "_dismiss_result", result))
            await pilot.pause(0.05)
            screen.action_exit_screen()
            await pilot.pause(0.05)
            # The screen should have been dismissed
            assert not isinstance(app.screen, SuccessScreen)
            # The dismiss callback should have received True
            assert getattr(app, "_dismiss_result", None) is True

    @pytest.mark.asyncio
    async def test_key_q_exits_screen(self) -> None:
        """Pressing 'q' triggers the exit action."""
        results = [_make_task_result()]
        tokens = _make_token_usage()
        screen = SuccessScreen(
            results=results,
            total_duration=45.0,
            total_tokens=tokens,
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause(0.05)
            await pilot.press("q")
            await pilot.pause(0.05)
            assert not isinstance(app.screen, SuccessScreen)

    @pytest.mark.asyncio
    async def test_key_enter_does_not_exit_screen(self) -> None:
        """Pressing Enter does NOT exit — only Q and Esc are exit keys."""
        results = [_make_task_result()]
        tokens = _make_token_usage()
        screen = SuccessScreen(
            results=results,
            total_duration=45.0,
            total_tokens=tokens,
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause(0.05)
            await pilot.press("enter")
            await pilot.pause(0.05)
            assert isinstance(app.screen, SuccessScreen)

    @pytest.mark.asyncio
    async def test_key_escape_exits_screen(self) -> None:
        """Pressing Escape triggers the exit action."""
        results = [_make_task_result()]
        tokens = _make_token_usage()
        screen = SuccessScreen(
            results=results,
            total_duration=45.0,
            total_tokens=tokens,
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause(0.05)
            await pilot.press("escape")
            await pilot.pause(0.05)
            assert not isinstance(app.screen, SuccessScreen)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestSuccessScreenEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_results_no_crash(self) -> None:
        """Screen handles empty results list without crashing."""
        tokens = _make_token_usage()
        screen = SuccessScreen(
            results=[],
            total_duration=0.0,
            total_tokens=tokens,
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause(0.05)
            assert isinstance(app.screen, SuccessScreen)

    @pytest.mark.asyncio
    async def test_zero_tokens_no_token_display(self) -> None:
        """Summary line omits token count when total is 0."""
        results = [_make_task_result(input_tokens=0, output_tokens=0)]
        tokens = _make_token_usage(input_tokens=0, output_tokens=0)
        screen = SuccessScreen(
            results=results,
            total_duration=30.0,
            total_tokens=tokens,
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause(0.05)
            summary = screen.query_one("#success_summary_line", Static)
            rendered = str(summary.render())
            # Should show duration but not "0K tokens"
            assert "0:30" in rendered

    @pytest.mark.asyncio
    async def test_retrospective_no_issues(self) -> None:
        """Retrospective with 0 issues shows 'no issues found'."""
        results = [_make_task_result()]
        tokens = _make_token_usage()
        rounds = [
            RetrospectiveRound(
                round_number=1,
                issues_found=0,
                fixes_planned=0,
                tasks_created=[],
            )
        ]
        screen = SuccessScreen(
            results=results,
            total_duration=45.0,
            total_tokens=tokens,
            retrospective_rounds=rounds,
        )

        app = _HostApp()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause(0.05)
            retro = screen.query_one("#success_retro", Static)
            rendered = str(retro.render())
            assert "no issues found" in rendered
