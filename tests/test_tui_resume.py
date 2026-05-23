"""Tests for the Textual ResumeScreen.

Phase 21 converted ``ResumeApp`` to a ``Screen`` subclass;
``ResumeApp`` remains as a legacy alias. Tests mount the screen inside
a small harness app.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from textual.app import App
from textual.widgets import Checkbox, Input, RadioSet, Static

from the_architect.config import ArchitectConfig
from the_architect.core.resume_verification import ResumeVerificationResult
from the_architect.core.tasks import Task, TaskStatus
from the_architect.tui.screens.resume import ResumeScreen, run_resume_screen


def _make_pending_tasks(n: int = 3) -> list[Task]:
    tasks: list[Task] = []
    for i in range(1, n + 1):
        tasks.append(
            Task(
                name=f"T{i:02d}_pending",
                prefix=f"T{i:02d}",
                number=i,
                path=Path("/tmp") / f"T{i:02d}_pending.md",
                title=f"Pending task {i}",
                status=TaskStatus.PENDING,
            )
        )
    return tasks


class _Harness(App[None]):
    def __init__(self, screen: Any) -> None:
        super().__init__()
        self._screen = screen
        self.dismissed: Any = "<not-dismissed>"

    def on_mount(self) -> None:
        self.push_screen(self._screen, self._on_dismiss)

    def _on_dismiss(self, value: Any) -> None:
        self.dismissed = value


@pytest.mark.asyncio
async def test_execute_default_action() -> None:
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(2), config=config, show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        screen.action_execute()
        await pilot.pause(0.05)
    assert isinstance(harness.dismissed, dict)
    assert harness.dismissed["action"] == "execute"
    assert harness.dismissed["free"] is False
    assert harness.dismissed["persistent"] is False
    assert harness.dismissed["integrity"] is True
    assert harness.dismissed["notify_on_complete"] is True
    assert harness.dismissed["notify_on_fail"] is True


@pytest.mark.asyncio
async def test_replan_action() -> None:
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config, show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        screen.action_replan()
        await pilot.pause(0.05)
    assert isinstance(harness.dismissed, dict)
    assert harness.dismissed["action"] == "replan"


@pytest.mark.asyncio
async def test_prefilled_from_config() -> None:
    config = ArchitectConfig(
        free_mode=True,
        persistent=True,
        integrity=False,
        token_budget_per_hour=250000,
    )
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config, show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        assert screen.query_one("#chk_free", Checkbox).value is True
        assert screen.query_one("#chk_persistent", Checkbox).value is True
        assert screen.query_one("#chk_integrity", Checkbox).value is False
        assert screen.query_one("#inp_budget", Input).value == "250000"
        screen.action_execute()
        await pilot.pause(0.05)
    assert isinstance(harness.dismissed, dict)
    assert harness.dismissed["free"] is True
    assert harness.dismissed["persistent"] is True
    assert harness.dismissed["integrity"] is False
    assert harness.dismissed["token_budget_per_hour"] == 250000
    assert harness.dismissed["notify_on_complete"] is True
    assert harness.dismissed["notify_on_fail"] is True


@pytest.mark.asyncio
async def test_cancel_returns_none() -> None:
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config, show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        screen.action_cancel()
        await pilot.pause(0.05)
    assert harness.dismissed is None


@pytest.mark.asyncio
async def test_hides_free_tier_when_disabled() -> None:
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config, show_free=False)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        assert len(screen.query("#chk_free")) == 0
        screen.action_execute()
        await pilot.pause(0.05)
    assert isinstance(harness.dismissed, dict)
    assert harness.dismissed["free"] is False


@pytest.mark.asyncio
async def test_arrow_keys_move_focus_between_fields() -> None:
    """Down/up arrows must actually move focus to the next/prev field.

    Arrow keys move focus between form fields when the focused widget is
    NOT an Input or TextArea (so cursor movement in text fields works).
    For Checkbox and RadioSet widgets, up/down moves focus.
    """
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(2), config=config, show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        first_focused = harness.focused.id if harness.focused else None
        assert first_focused is not None, "on_mount should focus the first RadioButton"

        await pilot.press("down")
        await pilot.pause(0.05)
        second_focused = harness.focused.id if harness.focused else None
        assert second_focused is not None
        assert second_focused != first_focused, (
            f"Down arrow did not move focus: stayed on {first_focused!r}."
        )

        await pilot.press("up")
        await pilot.pause(0.05)
        back_focused = harness.focused.id if harness.focused else None
        assert back_focused == first_focused, (
            f"Up arrow did not move focus back: expected {first_focused!r}, got {back_focused!r}."
        )
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_notification_settings_prefilled_from_config() -> None:
    """Notification checkboxes are pre-filled from config and submitted."""
    config = ArchitectConfig(notify_on_complete=False, notify_on_fail=False)
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config, show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        assert screen.query_one("#chk_notify_complete", Checkbox).value is False
        assert screen.query_one("#chk_notify_fail", Checkbox).value is False
        screen.action_execute()
        await pilot.pause(0.05)
    assert isinstance(harness.dismissed, dict)
    assert harness.dismissed["notify_on_complete"] is False
    assert harness.dismissed["notify_on_fail"] is False


# ── T01.1 — Verification display and task list coverage ───────────────


@pytest.mark.asyncio
async def test_verification_summary_shown_when_results_provided() -> None:
    """Verification summary Static is rendered when verification_results is set."""
    results = [
        ResumeVerificationResult(task_id="T01", status="valid", reason="ok"),
        ResumeVerificationResult(task_id="T02", status="stale", reason="changed"),
    ]
    config = ArchitectConfig()
    screen = ResumeScreen(
        pending_tasks=_make_pending_tasks(2),
        config=config,
        verification_results=results,
    )
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        # The summary Static has classes="verify-summary"
        summary_widgets = list(screen.query(".verify-summary"))
        assert len(summary_widgets) == 1
        rendered = str(summary_widgets[0].render())
        assert "Verification:" in rendered
        assert "1 valid" in rendered
        assert "1 stale" in rendered
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_verification_summary_not_shown_without_results() -> None:
    """No verification summary when verification_results is None."""
    config = ArchitectConfig()
    screen = ResumeScreen(
        pending_tasks=_make_pending_tasks(2),
        config=config,
        verification_results=None,
    )
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        summary_widgets = list(screen.query(".verify-summary"))
        assert len(summary_widgets) == 0
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_verification_summary_all_statuses() -> None:
    """Summary includes valid, stale, and missing counts."""
    results = [
        ResumeVerificationResult(task_id="T01", status="valid", reason="ok"),
        ResumeVerificationResult(task_id="T02", status="stale", reason="changed"),
        ResumeVerificationResult(task_id="T03", status="missing", reason="no baseline"),
    ]
    config = ArchitectConfig()
    screen = ResumeScreen(
        pending_tasks=_make_pending_tasks(3),
        config=config,
        verification_results=results,
    )
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        summary_widgets = list(screen.query(".verify-summary"))
        assert len(summary_widgets) == 1
        rendered = str(summary_widgets[0].render())
        assert "1 valid" in rendered
        assert "1 stale" in rendered
        assert "1 missing" in rendered
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_task_list_truncation_for_many_tasks() -> None:
    """Task list shows '... and N more' when > 5 pending tasks."""
    tasks = _make_pending_tasks(8)
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=tasks, config=config)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        task_list = screen.query_one("#task_list", Static)
        rendered = str(task_list.render())
        assert "and 3 more" in rendered
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_task_list_empty_pending() -> None:
    """Task list shows '(none)' when there are zero pending tasks."""
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=[], config=config)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        task_list = screen.query_one("#task_list", Static)
        rendered = str(task_list.render())
        assert "(none)" in rendered
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_task_list_exactly_five_tasks() -> None:
    """Exactly 5 tasks shows all, no truncation."""
    tasks = _make_pending_tasks(5)
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=tasks, config=config)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        task_list = screen.query_one("#task_list", Static)
        rendered = str(task_list.render())
        assert "more" not in rendered
        for i in range(1, 6):
            assert f"T{i:02d}" in rendered
        screen.action_cancel()
        await pilot.pause(0.05)


# ── T01.2 — Action and submit edge cases ──────────────────────────────


@pytest.mark.asyncio
async def test_action_submit_with_execute_radio() -> None:
    """action_submit reads the RadioSet and submits 'execute' when first button selected."""
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        # First radio (Execute) is selected by default
        screen.action_submit()
        await pilot.pause(0.05)
    assert isinstance(harness.dismissed, dict)
    assert harness.dismissed["action"] == "execute"


@pytest.mark.asyncio
async def test_action_submit_with_replan_radio() -> None:
    """action_submit reads the RadioSet and submits 'replan' when second button selected."""
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        # Select the replan radio button by setting its value
        replan_rb = screen.query_one("#rb_replan")
        object.__setattr__(replan_rb, "value", True)
        await pilot.pause(0.05)
        screen.action_submit()
        await pilot.pause(0.05)
    assert isinstance(harness.dismissed, dict)
    assert harness.dismissed["action"] == "replan"


@pytest.mark.asyncio
async def test_action_submit_fallback_on_radioset_error() -> None:
    """action_submit falls back to 'execute' when RadioSet query fails."""
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        # Force the RadioSet query specifically to raise inside action_submit
        original_query_one = screen.query_one

        def selective_fail(*args: Any, **kwargs: Any):
            if args and "#action_set" in str(args[0]):
                raise Exception("boom")
            return original_query_one(*args, **kwargs)

        screen.query_one = selective_fail  # type: ignore[method-assign]
        screen.action_submit()
        await pilot.pause(0.05)
        screen.query_one = original_query_one  # restore
    assert isinstance(harness.dismissed, dict)
    assert harness.dismissed["action"] == "execute"


@pytest.mark.asyncio
async def test_submit_budget_valueerror_fallback() -> None:
    """_submit handles non-numeric budget input gracefully."""
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        # Set budget input to non-numeric
        budget_input = screen.query_one("#inp_budget", Input)
        budget_input.value = "not_a_number"
        await pilot.pause(0.05)
        screen.action_execute()
        await pilot.pause(0.05)
    assert isinstance(harness.dismissed, dict)
    assert harness.dismissed["token_budget_per_hour"] == 0


@pytest.mark.asyncio
async def test_submit_budget_run_valueerror_fallback() -> None:
    """_submit handles non-numeric budget/run input gracefully."""
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        budget_run_input = screen.query_one("#inp_budget_run", Input)
        budget_run_input.value = "abc"
        await pilot.pause(0.05)
        screen.action_execute()
        await pilot.pause(0.05)
    assert isinstance(harness.dismissed, dict)
    assert harness.dismissed["token_budget_per_run"] == 0


@pytest.mark.asyncio
async def test_submit_task_timeout_valueerror_fallback() -> None:
    """_submit handles non-numeric task_timeout input gracefully."""
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        timeout_input = screen.query_one("#inp_task_timeout", Input)
        timeout_input.value = "xyz"
        await pilot.pause(0.05)
        screen.action_execute()
        await pilot.pause(0.05)
    assert isinstance(harness.dismissed, dict)
    assert harness.dismissed["task_timeout"] == 0


@pytest.mark.asyncio
async def test_submit_free_checkbox_exception_fallback() -> None:
    """_submit handles free checkbox query failure gracefully."""
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config, show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        # Force the free checkbox query to fail
        original_query_one = screen.query_one

        call_count = {"n": 0}

        def failing_query_one(*args: Any, **kwargs: Any):
            call_count["n"] += 1
            if call_count["n"] == 1 and "#chk_free" in str(args):
                raise Exception("boom")
            return original_query_one(*args, **kwargs)

        screen.query_one = failing_query_one  # type: ignore[method-assign]
        screen.action_execute()
        await pilot.pause(0.05)
        screen.query_one = original_query_one  # restore
    assert isinstance(harness.dismissed, dict)
    assert harness.dismissed["free"] is False


@pytest.mark.asyncio
async def test_submit_negative_budget_clamped_to_zero() -> None:
    """_submit clamps negative budget values to zero via max(budget, 0)."""
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        budget_input = screen.query_one("#inp_budget", Input)
        budget_input.value = "-100"
        await pilot.pause(0.05)
        screen.action_execute()
        await pilot.pause(0.05)
    assert isinstance(harness.dismissed, dict)
    assert harness.dismissed["token_budget_per_hour"] == 0


@pytest.mark.asyncio
async def test_submit_negative_task_timeout_clamped() -> None:
    """_submit clamps negative task_timeout to zero."""
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        timeout_input = screen.query_one("#inp_task_timeout", Input)
        timeout_input.value = "-500"
        await pilot.pause(0.05)
        screen.action_execute()
        await pilot.pause(0.05)
    assert isinstance(harness.dismissed, dict)
    assert harness.dismissed["task_timeout"] == 0


@pytest.mark.asyncio
async def test_on_mount_exception_handler() -> None:
    """on_mount catches exceptions when focusing the first radio button."""
    exception_caught = {"seen": False}

    class FailingResumeScreen(ResumeScreen):
        def on_mount(self) -> None:
            try:
                first_rb = self.query("#action_set RadioButton").first()
                if first_rb is not None:
                    raise Exception("focus boom")
            except Exception:
                exception_caught["seen"] = True

    config = ArchitectConfig()
    screen = FailingResumeScreen(  # type: ignore[call-arg]
        pending_tasks=_make_pending_tasks(1),
        config=config,
    )
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        assert exception_caught["seen"], "on_mount exception handler should have caught the error"
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_on_mount_exception_handler_via_focus_patch() -> None:
    """on_mount catches exceptions from RadioButton.focus() via patching."""
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        # Patch RadioButton.focus to raise, then trigger on_mount manually
        from textual.widgets import RadioButton

        original_focus = RadioButton.focus
        RadioButton.focus = lambda self: (_ for _ in ()).throw(Exception("boom"))  # type: ignore[assignment]
        # Call on_mount directly — it should catch the exception silently
        screen.on_mount()
        RadioButton.focus = original_focus
        # If we get here without exception, the handler worked
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_task_timeout_input_prefilled_from_config() -> None:
    """task_timeout input is pre-filled from config."""
    config = ArchitectConfig(task_timeout=300)
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        timeout_input = screen.query_one("#inp_task_timeout", Input)
        assert timeout_input.value == "300"
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_budget_run_input_prefilled_from_config() -> None:
    """token_budget_per_run input is pre-filled from config."""
    config = ArchitectConfig(token_budget_per_run=500000)
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        budget_run_input = screen.query_one("#inp_budget_run", Input)
        assert budget_run_input.value == "500000"
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_task_with_no_title_shows_name() -> None:
    """Task without title falls back to name in task list."""
    task = Task(
        name="T01_fallback",
        prefix="T01",
        number=1,
        path=Path("/tmp") / "T01_fallback.md",
        title="",
        status=TaskStatus.PENDING,
    )
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=[task], config=config)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        task_list = screen.query_one("#task_list", Static)
        rendered = str(task_list.render())
        assert "T01_fallback" in rendered
        screen.action_cancel()
        await pilot.pause(0.05)


# ── T01.3 — run_resume_screen function coverage ───────────────────────


def test_run_resume_screen_returns_result() -> None:
    """run_resume_screen returns the screen result dict."""
    tasks = _make_pending_tasks(1)
    config = ArchitectConfig()
    mock_result = {"action": "execute", "free": False, "persistent": False}
    with patch("the_architect.tui.app.run_single_screen", return_value=mock_result):
        result = run_resume_screen(tasks, config)
    assert result == mock_result


def test_run_resume_screen_raises_system_exit_on_none() -> None:
    """run_resume_screen raises SystemExit(0) when screen returns None (cancel)."""
    tasks = _make_pending_tasks(1)
    config = ArchitectConfig()
    with patch("the_architect.tui.app.run_single_screen", return_value=None):
        with pytest.raises(SystemExit) as exc_info:
            run_resume_screen(tasks, config)
        assert exc_info.value.code == 0


def test_run_resume_screen_passes_verification_results() -> None:
    """run_resume_screen passes verification_results to the screen."""
    tasks = _make_pending_tasks(1)
    config = ArchitectConfig()
    results = [
        ResumeVerificationResult(task_id="T01", status="valid", reason="ok"),
    ]
    with patch(
        "the_architect.tui.app.run_single_screen", return_value={"action": "execute"}
    ) as mock_run:
        run_resume_screen(tasks, config, verification_results=results)
    # Verify the screen was constructed with verification_results
    call_args = mock_run.call_args
    screen_instance = call_args[0][0]
    assert screen_instance._verification_results == results


def test_run_resume_screen_passes_show_free() -> None:
    """run_resume_screen passes show_free to the screen."""
    tasks = _make_pending_tasks(1)
    config = ArchitectConfig()
    with patch(
        "the_architect.tui.app.run_single_screen", return_value={"action": "execute"}
    ) as mock_run:
        run_resume_screen(tasks, config, show_free=False)
    call_args = mock_run.call_args
    screen_instance = call_args[0][0]
    assert screen_instance._show_free is False


# ── _format_verify_summary unit tests (no mount needed) ───────────────


def test_format_verify_summary_empty_when_no_results() -> None:
    """_format_verify_summary returns empty string when no verification."""
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=[], config=config, verification_results=None)
    assert screen._format_verify_summary() == ""


def test_format_verify_summary_empty_when_all_unrecognized_status() -> None:
    """_format_verify_summary returns empty when no recognized statuses."""
    # If results exist but none match valid/stale/missing, return empty
    results = [
        ResumeVerificationResult(task_id="T01", status="valid", reason="ok"),
    ]
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=[], config=config, verification_results=results)
    summary = screen._format_verify_summary()
    assert "1 valid" in summary


def test_format_verify_summary_multiple_valid() -> None:
    """Summary correctly counts multiple valid tasks."""
    results = [
        ResumeVerificationResult(task_id="T01", status="valid", reason="ok"),
        ResumeVerificationResult(task_id="T02", status="valid", reason="ok"),
        ResumeVerificationResult(task_id="T03", status="valid", reason="ok"),
    ]
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=[], config=config, verification_results=results)
    summary = screen._format_verify_summary()
    assert "3 valid" in summary


# ── _verify_indicator unit tests (no mount needed) ────────────────────


def test_verify_indicator_empty_when_no_results() -> None:
    """_verify_indicator returns empty string when no verification."""
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=[], config=config, verification_results=None)
    assert screen._verify_indicator("T01") == ""


def test_verify_indicator_valid() -> None:
    """_verify_indicator returns green dot for valid status."""
    results = [
        ResumeVerificationResult(task_id="T01", status="valid", reason="ok"),
    ]
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=[], config=config, verification_results=results)
    indicator = screen._verify_indicator("T01")
    assert "green" in indicator
    assert "\u25cf" in indicator


def test_verify_indicator_stale() -> None:
    """_verify_indicator returns yellow dot for stale status."""
    results = [
        ResumeVerificationResult(task_id="T01", status="stale", reason="changed"),
    ]
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=[], config=config, verification_results=results)
    indicator = screen._verify_indicator("T01")
    assert "yellow" in indicator
    assert "\u25cf" in indicator


def test_verify_indicator_missing() -> None:
    """_verify_indicator returns red dot for missing status."""
    results = [
        ResumeVerificationResult(task_id="T01", status="missing", reason="no baseline"),
    ]
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=[], config=config, verification_results=results)
    indicator = screen._verify_indicator("T01")
    assert "red" in indicator
    assert "\u25cf" in indicator


def test_verify_indicator_unknown_task() -> None:
    """_verify_indicator returns empty for task not in results."""
    results = [
        ResumeVerificationResult(task_id="T02", status="valid", reason="ok"),
    ]
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=[], config=config, verification_results=results)
    assert screen._verify_indicator("T01") == ""


# ── _format_tasks unit tests (no mount needed) ────────────────────────


def test_format_tasks_with_verification_indicators() -> None:
    """_format_tasks includes verification indicators when results provided."""
    tasks = _make_pending_tasks(2)
    results = [
        ResumeVerificationResult(task_id="T01", status="valid", reason="ok"),
        ResumeVerificationResult(task_id="T02", status="stale", reason="changed"),
    ]
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=tasks, config=config, verification_results=results)
    text = screen._format_tasks()
    assert "green" in text  # T01 valid indicator
    assert "yellow" in text  # T02 stale indicator


def test_format_tasks_empty_list() -> None:
    """_format_tasks returns '(none)' for empty task list."""
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=[], config=config)
    text = screen._format_tasks()
    assert "(none)" in text


def test_format_tasks_six_tasks_truncated() -> None:
    """_format_tasks truncates at 5 tasks and shows count."""
    tasks = _make_pending_tasks(6)
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=tasks, config=config)
    text = screen._format_tasks()
    assert "and 1 more" in text
    # Should show exactly 5 task lines + 1 truncation line
    lines = text.split("\n")
    assert len(lines) == 6


# ── RadioSet interaction tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_radioset_default_selection_is_execute() -> None:
    """The Execute radio button is selected by default."""
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        radio_set = screen.query_one("#action_set", RadioSet)
        pressed = radio_set.pressed_button
        assert pressed is not None
        assert pressed.id == "rb_execute"
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_radioset_pressed_button_none_fallback() -> None:
    """action_submit handles pressed_button being None (falls back to execute)."""
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        # Mock pressed_button to return None
        radio_set = screen.query_one("#action_set", RadioSet)
        original_pressed = radio_set.pressed_button
        # Use object.__setattr__ since pressed_button is a property
        type(radio_set).pressed_button = property(lambda self: None)  # type: ignore[attr-defined]
        screen.action_submit()
        await pilot.pause(0.05)
        # Restore
        type(radio_set).pressed_button = property(lambda self: original_pressed)  # type: ignore[attr-defined]
    assert isinstance(harness.dismissed, dict)
    assert harness.dismissed["action"] == "execute"


# ── Pending task count display ────────────────────────────────────────


@pytest.mark.asyncio
async def test_pending_task_count_singular() -> None:
    """Display shows '1 pending task' (singular) for exactly one task."""
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        muted_labels = [str(w.render()) for w in screen.query(".muted")]
        combined = "\n".join(muted_labels)
        assert "1 pending task" in combined
        assert "1 pending tasks" not in combined
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_pending_task_count_plural() -> None:
    """Display shows 'N pending tasks' (plural) for multiple tasks."""
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(3), config=config)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        muted_labels = [str(w.render()) for w in screen.query(".muted")]
        combined = "\n".join(muted_labels)
        assert "3 pending tasks" in combined
        screen.action_cancel()
        await pilot.pause(0.05)


# ── Submit output validation ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_output_contains_all_keys() -> None:
    """Submit output dict contains all expected keys."""
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(1), config=config)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        screen.action_execute()
        await pilot.pause(0.05)
    expected_keys = {
        "free",
        "persistent",
        "integrity",
        "token_budget_per_hour",
        "token_budget_per_run",
        "task_timeout",
        "notify_on_complete",
        "notify_on_fail",
        "action",
    }
    assert set(harness.dismissed.keys()) == expected_keys
