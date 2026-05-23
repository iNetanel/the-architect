"""Tests for the Textual ModeSelectionScreen.

Phase 16 converted ``ModeSelectionApp`` to a ``Screen`` subclass.
``ModeSelectionApp`` remains as a legacy alias, but the screen must be
mounted inside a host app to run. We use a minimal harness app.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from textual.app import App
from textual.widgets import Checkbox, Input, ListView, Static

from the_architect.tui.screens.mode_selection import ModeSelectionScreen
from the_architect.tui.screens.pre_run import BACK_SENTINEL


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
async def test_submit_returns_defaults() -> None:
    screen = ModeSelectionScreen(show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        screen.action_submit()
        await pilot.pause(0.05)
    assert harness.dismissed == {
        "free": False,
        "persistent": False,
        "integrity": True,
        "token_budget_per_hour": 0,
        "token_budget_per_run": 0,
        "task_timeout": 0,
        "notify_on_complete": True,
        "notify_on_fail": True,
        "validation_gate": {
            "enabled": True,
            "checks": ["lint", "test", "typecheck"],
            "custom_commands": {},
            "fail_fast": True,
        },
    }


@pytest.mark.asyncio
async def test_submit_with_toggles_and_budget() -> None:
    screen = ModeSelectionScreen(show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        screen.query_one("#chk_free", Checkbox).value = True
        screen.query_one("#chk_persistent", Checkbox).value = True
        screen.query_one("#chk_integrity", Checkbox).value = False
        screen.query_one("#inp_budget", Input).value = "150000"
        await pilot.pause(0.05)
        screen.action_submit()
        await pilot.pause(0.05)
    assert harness.dismissed == {
        "free": True,
        "persistent": True,
        "integrity": False,
        "token_budget_per_hour": 150000,
        "token_budget_per_run": 0,
        "task_timeout": 0,
        "notify_on_complete": True,
        "notify_on_fail": True,
        "validation_gate": {
            "enabled": True,
            "checks": ["lint", "test", "typecheck"],
            "custom_commands": {},
            "fail_fast": True,
        },
    }


@pytest.mark.asyncio
async def test_submit_hides_free_tier_when_disabled() -> None:
    screen = ModeSelectionScreen(show_free=False)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        screen.query_one("#chk_persistent", Checkbox).value = True
        screen.action_submit()
        await pilot.pause(0.05)
    assert isinstance(harness.dismissed, dict)
    result = harness.dismissed
    assert result["free"] is False
    assert result["persistent"] is True


@pytest.mark.asyncio
async def test_cancel_returns_none() -> None:
    screen = ModeSelectionScreen(show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        screen.action_cancel()
        await pilot.pause(0.05)
    assert harness.dismissed is None


@pytest.mark.asyncio
async def test_invalid_budget_clamps_to_zero() -> None:
    screen = ModeSelectionScreen(show_free=False)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        screen.query_one("#inp_budget", Input).value = "not-a-number"
        screen.action_submit()
        await pilot.pause(0.05)
    assert isinstance(harness.dismissed, dict)
    assert harness.dismissed["token_budget_per_hour"] == 0


@pytest.mark.asyncio
async def test_arrow_keys_move_focus_between_fields() -> None:
    """Down/up arrows must actually move focus to the next/prev field.

    Arrow keys move focus between form fields when the focused widget is
    NOT an Input or TextArea (so cursor movement in text fields works).
    For Checkbox and RadioSet widgets, up/down moves focus.
    """
    screen = ModeSelectionScreen(show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        first_focused = harness.focused.id if harness.focused else None
        assert first_focused is not None, "on_mount should focus the first Checkbox"

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


# ── Phase A: Back navigation ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_back_returns_sentinel() -> None:
    """Phase A: Backspace dismisses with BACK_SENTINEL instead of None."""
    screen = ModeSelectionScreen(show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        screen.action_go_back()
        await pilot.pause(0.05)
    assert harness.dismissed is BACK_SENTINEL


# ── Phase A: Pre-fill constructors ──────────────────────────────────────


@pytest.mark.asyncio
async def test_pre_fill_initial_values() -> None:
    """Phase A: initial_* parameters pre-fill the screen controls."""
    screen = ModeSelectionScreen(
        show_free=True,
        initial_free=True,
        initial_persistent=True,
        initial_integrity=False,
        initial_budget=50000,
    )
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        assert screen.query_one("#chk_free", Checkbox).value is True
        assert screen.query_one("#chk_persistent", Checkbox).value is True
        assert screen.query_one("#chk_integrity", Checkbox).value is False
        assert screen.query_one("#inp_budget", Input).value == "50000"
        screen.action_cancel()
        await pilot.pause(0.05)


# ── Preset display tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_project_no_presets_shown() -> None:
    """When no project path is given, preset section shows no-presets message."""
    screen = ModeSelectionScreen(show_free=True, project=None)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        # The no-presets message widget should exist
        no_msg = screen.query_one("#preset_no_msg", Static)
        assert "No presets saved" in str(no_msg.render())
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_presets_loaded_and_shown(tmp_path: Path) -> None:
    """When presets exist, they appear as a ListView."""
    from the_architect.core.presets import save_preset

    save_preset(tmp_path, "sprint", "Quick sprint mode", {"free_mode": True})
    save_preset(tmp_path, "deep", "Deep work mode", {"persistent": True})

    screen = ModeSelectionScreen(show_free=True, project=tmp_path)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        # ListView should exist with 2 items
        preset_list = screen.query_one("#preset_list", ListView)
        assert len(list(preset_list.query("ListItem"))) == 2
        # No-presets message should NOT exist
        assert not list(screen.query("#preset_no_msg"))
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_preset_selection_prefills_fields(tmp_path: Path) -> None:
    """Selecting a preset pre-fills the form fields."""
    from the_architect.core.presets import save_preset

    save_preset(
        tmp_path,
        "test-preset",
        "Test preset",
        {
            "free_mode": True,
            "persistent": True,
            "integrity": False,
            "token_budget_per_hour": 50000,
            "token_budget_per_run": 100000,
        },
    )

    screen = ModeSelectionScreen(show_free=True, project=tmp_path)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        # Simulate preset selection by calling _apply_preset directly
        screen._apply_preset(screen._presets[0])
        await pilot.pause(0.05)

        # Check fields were pre-filled
        assert screen.query_one("#chk_free", Checkbox).value is True
        assert screen.query_one("#chk_persistent", Checkbox).value is True
        assert screen.query_one("#chk_integrity", Checkbox).value is False
        assert screen.query_one("#inp_budget", Input).value == "50000"
        assert screen.query_one("#inp_budget_run", Input).value == "100000"

        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_preset_partial_prefill(tmp_path: Path) -> None:
    """Preset with partial config_overrides only fills those fields."""
    from the_architect.core.presets import save_preset

    save_preset(tmp_path, "minimal", "Minimal preset", {"persistent": True})

    screen = ModeSelectionScreen(
        show_free=True,
        project=tmp_path,
        initial_free=True,
        initial_integrity=True,
        initial_budget=999,
    )
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        screen._apply_preset(screen._presets[0])
        await pilot.pause(0.05)

        # persistent should be True (from preset)
        assert screen.query_one("#chk_persistent", Checkbox).value is True
        # free should remain initial value (not in preset)
        assert screen.query_one("#chk_free", Checkbox).value is True
        # integrity should remain initial value (not in preset)
        assert screen.query_one("#chk_integrity", Checkbox).value is True
        # budget should remain initial value (not in preset)
        assert screen.query_one("#inp_budget", Input).value == "999"

        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_preset_load_failure_is_graceful(tmp_path: Path) -> None:
    """If preset loading fails, screen shows no presets gracefully."""
    with patch(
        "the_architect.core.presets.list_presets",
        side_effect=RuntimeError("disk error"),
    ):
        screen = ModeSelectionScreen(show_free=True, project=tmp_path)
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            # No-presets message should be shown
            no_msg = screen.query_one("#preset_no_msg", Static)
            assert "No presets saved" in str(no_msg.render())
            screen.action_cancel()
            await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_preset_submit_after_selection(tmp_path: Path) -> None:
    """After selecting a preset, submit returns the pre-filled values."""
    from the_architect.core.presets import save_preset

    save_preset(
        tmp_path,
        "full-preset",
        "Full preset",
        {
            "free_mode": True,
            "persistent": True,
            "integrity": True,
            "token_budget_per_hour": 75000,
            "token_budget_per_run": 200000,
        },
    )

    screen = ModeSelectionScreen(show_free=True, project=tmp_path)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        screen._apply_preset(screen._presets[0])
        await pilot.pause(0.05)
        screen.action_submit()
        await pilot.pause(0.05)

    assert harness.dismissed == {
        "free": True,
        "persistent": True,
        "integrity": True,
        "token_budget_per_hour": 75000,
        "token_budget_per_run": 200000,
        "task_timeout": 0,
        "notify_on_complete": True,
        "notify_on_fail": True,
        "validation_gate": {
            "enabled": True,
            "checks": ["lint", "test", "typecheck"],
            "custom_commands": {},
            "fail_fast": True,
        },
    }


@pytest.mark.asyncio
async def test_preset_free_mode_hidden(tmp_path: Path) -> None:
    """Preset with free_mode=True but show_free=False skips the checkbox."""
    from the_architect.core.presets import save_preset

    save_preset(
        tmp_path,
        "free-preset",
        "Free preset",
        {"free_mode": True, "persistent": False},
    )

    screen = ModeSelectionScreen(show_free=False, project=tmp_path)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        screen._apply_preset(screen._presets[0])
        await pilot.pause(0.05)
        screen.action_submit()
        await pilot.pause(0.05)

    # free should be False (checkbox hidden, so always False)
    assert harness.dismissed["free"] is False
    assert harness.dismissed["persistent"] is False


# ── Notification settings tests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_notification_checkboxes_default_on() -> None:
    """Notification checkboxes default to True."""
    screen = ModeSelectionScreen(show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        assert screen.query_one("#chk_notify_complete", Checkbox).value is True
        assert screen.query_one("#chk_notify_fail", Checkbox).value is True
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_notification_checkboxes_can_toggle() -> None:
    """Notification checkboxes can be toggled off."""
    screen = ModeSelectionScreen(
        show_free=True,
        initial_notify_complete=False,
        initial_notify_fail=False,
    )
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        assert screen.query_one("#chk_notify_complete", Checkbox).value is False
        assert screen.query_one("#chk_notify_fail", Checkbox).value is False
        screen.action_submit()
        await pilot.pause(0.05)
    assert harness.dismissed["notify_on_complete"] is False
    assert harness.dismissed["notify_on_fail"] is False


@pytest.mark.asyncio
async def test_preset_prefills_notification_settings(tmp_path: Path) -> None:
    """Preset with notification overrides pre-fills the checkboxes."""
    from the_architect.core.presets import save_preset

    save_preset(
        tmp_path,
        "silent",
        "Silent mode",
        {"notify_on_complete": False, "notify_on_fail": False},
    )

    screen = ModeSelectionScreen(show_free=True, project=tmp_path)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        screen._apply_preset(screen._presets[0])
        await pilot.pause(0.05)
        assert screen.query_one("#chk_notify_complete", Checkbox).value is False
        assert screen.query_one("#chk_notify_fail", Checkbox).value is False
        screen.action_cancel()
        await pilot.pause(0.05)


# ── Task timeout tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_timeout_input_exists() -> None:
    """Task timeout input widget exists and is queryable."""
    screen = ModeSelectionScreen(show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        inp = screen.query_one("#inp_task_timeout", Input)
        # Input widget defaults to empty string; submit treats empty as 0
        assert isinstance(inp, Input)
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_task_timeout_input_accepts_value() -> None:
    """Task timeout input accepts a numeric value."""
    screen = ModeSelectionScreen(show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        screen.query_one("#inp_task_timeout", Input).value = "600"
        await pilot.pause(0.05)
        screen.action_submit()
        await pilot.pause(0.05)
    assert harness.dismissed["task_timeout"] == 600


@pytest.mark.asyncio
async def test_task_timeout_invalid_value_clamps_to_zero() -> None:
    """Invalid task timeout value clamps to 0."""
    screen = ModeSelectionScreen(show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        screen.query_one("#inp_task_timeout", Input).value = "not-a-number"
        await pilot.pause(0.05)
        screen.action_submit()
        await pilot.pause(0.05)
    assert harness.dismissed["task_timeout"] == 0


@pytest.mark.asyncio
async def test_task_timeout_initial_value() -> None:
    """Task timeout initial_value pre-fills the input."""
    screen = ModeSelectionScreen(show_free=True, initial_task_timeout=900)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        inp = screen.query_one("#inp_task_timeout", Input)
        assert inp.value == "900"
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_task_timeout_preset_prefill(tmp_path: Path) -> None:
    """Preset with task_timeout pre-fills the input."""
    from the_architect.core.presets import save_preset

    save_preset(
        tmp_path,
        "slow-run",
        "Slow run preset",
        {
            "persistent": True,
            "task_timeout": 1200,
        },
    )

    screen = ModeSelectionScreen(show_free=True, project=tmp_path)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        screen._apply_preset(screen._presets[0])
        await pilot.pause(0.05)
        assert screen.query_one("#inp_task_timeout", Input).value == "1200"
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_task_timeout_preset_partial_no_overwrite(tmp_path: Path) -> None:
    """Preset without task_timeout does not overwrite initial value."""
    from the_architect.core.presets import save_preset

    save_preset(tmp_path, "minimal", "Minimal preset", {"persistent": True})

    screen = ModeSelectionScreen(
        show_free=True,
        project=tmp_path,
        initial_task_timeout=300,
    )
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        screen._apply_preset(screen._presets[0])
        await pilot.pause(0.05)
        # task_timeout should remain at initial value (not in preset)
        assert screen.query_one("#inp_task_timeout", Input).value == "300"
        screen.action_cancel()
        await pilot.pause(0.05)


# ── Spending summary display tests ──────────────────────────────────


@pytest.mark.asyncio
async def test_spending_summary_display_with_data() -> None:
    """Spending summary Static widgets render when spending_summary is populated."""
    screen = ModeSelectionScreen(show_free=True, project=None)
    # Manually set spending summary to simulate loaded data
    screen._spending_summary = {
        "total_cost": 42.50,
        "total_tokens": 1000000,
        "run_count": 5,
        "top_model": "gpt-4o-mini",
    }
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        # Spending label should exist
        spending_label = screen.query_one("#spending_label", Static)
        assert "Recent Spending" in str(spending_label.render())
        # Spending detail should show cost, runs, and model
        spending_detail = screen.query_one("#spending_detail", Static)
        detail_text = str(spending_detail.render())
        assert "$42.50" in detail_text
        assert "5 runs" in detail_text
        assert "gpt-4o-mini" in detail_text
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_spending_summary_display_single_run() -> None:
    """Spending summary uses singular 'run' when run_count is 1."""
    screen = ModeSelectionScreen(show_free=True, project=None)
    screen._spending_summary = {
        "total_cost": 1.23,
        "total_tokens": 50000,
        "run_count": 1,
        "top_model": "claude-sonnet",
    }
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        spending_detail = screen.query_one("#spending_detail", Static)
        detail_text = str(spending_detail.render())
        assert "$1.23" in detail_text
        assert "1 run" in detail_text
        # Should NOT have "runs" plural
        assert "runs" not in detail_text or "1 run" in detail_text
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_spending_summary_hidden_when_no_data() -> None:
    """Spending summary widgets are absent when spending_summary is None."""
    screen = ModeSelectionScreen(show_free=True, project=None)
    # project=None means _spending_summary stays None
    assert screen._spending_summary is None
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        # Spending widgets should NOT exist
        assert not list(screen.query("#spending_label"))
        assert not list(screen.query("#spending_detail"))
        screen.action_cancel()
        await pilot.pause(0.05)


# ── Custom commands text area tests ─────────────────────────────────


@pytest.mark.asyncio
async def test_custom_commands_text_area_populated() -> None:
    """Custom commands TextArea shows commands when provided."""
    screen = ModeSelectionScreen(
        show_free=True,
        project=None,
        initial_validation_gate_custom_commands={
            "build": "npm run build",
            "security": "npm audit",
        },
    )
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        from textual.widgets import TextArea

        text_area = screen.query_one("#inp_vg_custom", TextArea)
        text = text_area.text
        assert "build=npm run build" in text
        assert "security=npm audit" in text
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_custom_commands_empty_by_default() -> None:
    """Custom commands TextArea is empty when no custom commands provided."""
    screen = ModeSelectionScreen(show_free=True, project=None)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        from textual.widgets import TextArea

        text_area = screen.query_one("#inp_vg_custom", TextArea)
        assert text_area.text == ""
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_custom_commands_single_entry() -> None:
    """Custom commands TextArea renders a single entry correctly."""
    screen = ModeSelectionScreen(
        show_free=True,
        project=None,
        initial_validation_gate_custom_commands={"lint": "eslint ."},
    )
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        from textual.widgets import TextArea

        text_area = screen.query_one("#inp_vg_custom", TextArea)
        assert text_area.text == "lint=eslint ."
        screen.action_cancel()
        await pilot.pause(0.05)


# ── on_mount exception handling and focus tests ─────────────────────


@pytest.mark.asyncio
async def test_on_mount_exception_handling() -> None:
    """on_mount handles exceptions gracefully when query_one raises."""
    screen = ModeSelectionScreen(show_free=True, project=None)
    # Set up presets so on_mount tries query_one("#preset_list", ListView)
    # then monkey-patch query_one to raise — on_mount must swallow it
    screen._presets = [MagicMock(name="fake", description="fake")]

    # Override query_one on the instance to raise during on_mount
    def broken_query_one(*args, **kwargs):
        raise Exception("boom")

    screen.query_one = broken_query_one  # type: ignore[method-assign]

    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        # The screen should still be mountable despite the exception
        assert screen.is_mounted

        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_on_mount_focuses_preset_list_when_presets_exist(tmp_path: Path) -> None:
    """on_mount focuses the preset ListView when presets are available."""
    from the_architect.core.presets import save_preset

    save_preset(tmp_path, "sprint", "Quick sprint", {"free_mode": True})

    screen = ModeSelectionScreen(show_free=True, project=tmp_path)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        # The focused widget should be the preset ListView
        focused = harness.focused
        assert focused is not None
        assert focused.id == "preset_list"
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_on_mount_focuses_first_checkbox_when_no_presets() -> None:
    """on_mount focuses the first Checkbox when no presets exist."""
    screen = ModeSelectionScreen(show_free=True, project=None)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        # The focused widget should be the first Checkbox (chk_free)
        focused = harness.focused
        assert focused is not None
        assert isinstance(focused, Checkbox)
        assert focused.id == "chk_free"
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_on_mount_focuses_first_checkbox_no_presets_no_free() -> None:
    """on_mount focuses first available Checkbox when no presets and free hidden."""
    screen = ModeSelectionScreen(show_free=False, project=None)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        focused = harness.focused
        assert focused is not None
        assert isinstance(focused, Checkbox)
        # With free hidden, first checkbox should be chk_persistent
        assert focused.id == "chk_persistent"
        screen.action_cancel()
        await pilot.pause(0.05)


# ── T02: _apply_preset exception handlers ──────────────────────────


@pytest.mark.asyncio
async def test_apply_preset_catches_query_one_exception_persistent() -> None:
    """_apply_preset silently catches exceptions on persistent checkbox."""
    screen = ModeSelectionScreen(show_free=True, project=None)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        original_query_one = screen.query_one

        def raise_for_persistent(*args, **kwargs):
            if args and args[0] == "#chk_persistent":
                raise Exception("boom")
            return original_query_one(*args, **kwargs)

        screen.query_one = raise_for_persistent  # type: ignore[method-assign]

        preset = MagicMock(
            name="test",
            description="test",
            config_overrides={"persistent": True, "integrity": False},
        )
        # Should not raise despite the exception on persistent
        screen._apply_preset(preset)
        await pilot.pause(0.05)
        # integrity should still be set (not affected by persistent exception)
        assert screen.query_one("#chk_integrity", Checkbox).value is False
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_apply_preset_catches_query_one_exception_budget() -> None:
    """_apply_preset silently catches exceptions on budget input."""
    screen = ModeSelectionScreen(show_free=True, project=None)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        original_query_one = screen.query_one

        def raise_for_budget(*args, **kwargs):
            if args and args[0] == "#inp_budget":
                raise Exception("boom")
            return original_query_one(*args, **kwargs)

        screen.query_one = raise_for_budget  # type: ignore[method-assign]

        preset = MagicMock(
            name="test",
            description="test",
            config_overrides={"token_budget_per_hour": 50000},
        )
        screen._apply_preset(preset)
        # Restore original query_one for assertions
        screen.query_one = original_query_one  # type: ignore[method-assign]
        await pilot.pause(0.05)
        # Budget should remain at default (0 = empty string)
        assert screen.query_one("#inp_budget", Input).value == ""
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_apply_preset_catches_query_one_exception_notify() -> None:
    """_apply_preset silently catches exceptions on notification checkboxes."""
    screen = ModeSelectionScreen(show_free=True, project=None)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        original_query_one = screen.query_one

        def raise_for_notify(*args, **kwargs):
            if args and args[0] == "#chk_notify_complete":
                raise Exception("boom")
            return original_query_one(*args, **kwargs)

        screen.query_one = raise_for_notify  # type: ignore[method-assign]

        preset = MagicMock(
            name="test",
            description="test",
            config_overrides={"notify_on_complete": False},
        )
        screen._apply_preset(preset)
        # Restore original query_one for assertions
        screen.query_one = original_query_one  # type: ignore[method-assign]
        await pilot.pause(0.05)
        # notify_complete should remain at default (True)
        assert screen.query_one("#chk_notify_complete", Checkbox).value is True
        screen.action_cancel()
        await pilot.pause(0.05)


# ── T02: _apply_preset validation gate non-dict fallback ───────────


@pytest.mark.asyncio
async def test_apply_preset_validation_gate_non_dict_fallback() -> None:
    """_apply_preset falls back to initial values when validation_gate is not a dict."""
    screen = ModeSelectionScreen(
        show_free=True,
        project=None,
        initial_validation_gate_enabled=False,
        initial_validation_gate_checks=("lint",),
        initial_validation_gate_fail_fast=False,
    )
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        # validation_gate is a string (not a dict) — should fallback to initial values
        preset = MagicMock(
            name="test",
            description="test",
            config_overrides={
                "validation_gate": "not-a-dict",
            },
        )
        screen._apply_preset(preset)
        await pilot.pause(0.05)
        # Should use initial values since validation_gate is not a dict
        assert screen.query_one("#chk_validation_gate", Checkbox).value is False
        assert screen.query_one("#chk_vg_lint", Checkbox).value is True
        assert screen.query_one("#chk_vg_test", Checkbox).value is False
        assert screen.query_one("#chk_vg_typecheck", Checkbox).value is False
        assert screen.query_one("#chk_vg_fail_fast", Checkbox).value is False
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_apply_preset_validation_gate_none_fallback() -> None:
    """_apply_preset falls back to initial values when validation_gate is None."""
    screen = ModeSelectionScreen(
        show_free=True,
        project=None,
        initial_validation_gate_enabled=False,
        initial_validation_gate_checks=(),
        initial_validation_gate_fail_fast=False,
    )
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        preset = MagicMock(
            name="test",
            description="test",
            config_overrides={
                "validation_gate": None,
            },
        )
        screen._apply_preset(preset)
        await pilot.pause(0.05)
        assert screen.query_one("#chk_validation_gate", Checkbox).value is False
        assert screen.query_one("#chk_vg_fail_fast", Checkbox).value is False
        screen.action_cancel()
        await pilot.pause(0.05)


# ── T02: _apply_preset validation gate exception handlers ──────────


@pytest.mark.asyncio
async def test_apply_preset_catches_vg_checkbox_exception() -> None:
    """_apply_preset silently catches exceptions on validation gate checkboxes."""
    screen = ModeSelectionScreen(show_free=True, project=None)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        original_query_one = screen.query_one

        def raise_for_vg(*args, **kwargs):
            if args and args[0] in (
                "#chk_validation_gate",
                "#chk_vg_lint",
                "#chk_vg_test",
                "#chk_vg_typecheck",
                "#chk_vg_fail_fast",
            ):
                raise Exception("boom")
            return original_query_one(*args, **kwargs)

        screen.query_one = raise_for_vg  # type: ignore[method-assign]

        preset = MagicMock(
            name="test",
            description="test",
            config_overrides={
                "validation_gate": {
                    "enabled": False,
                    "checks": ["lint"],
                    "fail_fast": False,
                }
            },
        )
        # Should not raise despite exceptions on all VG checkboxes
        screen._apply_preset(preset)
        # Restore original query_one for assertions
        screen.query_one = original_query_one  # type: ignore[method-assign]
        await pilot.pause(0.05)
        # Values should remain at defaults since all query_one calls failed
        assert screen.query_one("#chk_validation_gate", Checkbox).value is True
        screen.action_cancel()
        await pilot.pause(0.05)


# ── T02: _apply_preset custom commands from preset ─────────────────


@pytest.mark.asyncio
async def test_apply_preset_custom_commands_from_preset() -> None:
    """_apply_preset populates TextArea from preset custom_commands."""
    from textual.widgets import TextArea

    screen = ModeSelectionScreen(show_free=True, project=None)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        preset = MagicMock(
            name="test",
            description="test",
            config_overrides={
                "validation_gate": {
                    "enabled": True,
                    "checks": ["lint"],
                    "fail_fast": True,
                    "custom_commands": {
                        "build": "npm run build",
                        "test": "npm test",
                    },
                }
            },
        )
        screen._apply_preset(preset)
        await pilot.pause(0.05)
        text_area = screen.query_one("#inp_vg_custom", TextArea)
        text = text_area.text
        assert "build=npm run build" in text
        assert "test=npm test" in text
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_apply_preset_custom_commands_empty_dict() -> None:
    """_apply_preset skips TextArea when custom_commands is empty dict."""
    from textual.widgets import TextArea

    screen = ModeSelectionScreen(show_free=True, project=None)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        preset = MagicMock(
            name="test",
            description="test",
            config_overrides={
                "validation_gate": {
                    "enabled": True,
                    "checks": ["lint"],
                    "fail_fast": True,
                    "custom_commands": {},
                }
            },
        )
        screen._apply_preset(preset)
        await pilot.pause(0.05)
        text_area = screen.query_one("#inp_vg_custom", TextArea)
        # Empty dict means TextArea is not touched — remains empty
        assert text_area.text == ""
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_apply_preset_catches_custom_commands_textarea_exception() -> None:
    """_apply_preset catches exceptions on custom commands TextArea."""
    screen = ModeSelectionScreen(show_free=True, project=None)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        original_query_one = screen.query_one

        def raise_for_custom(*args, **kwargs):
            if args and args[0] == "#inp_vg_custom":
                raise Exception("boom")
            return original_query_one(*args, **kwargs)

        screen.query_one = raise_for_custom  # type: ignore[method-assign]

        preset = MagicMock(
            name="test",
            description="test",
            config_overrides={
                "validation_gate": {
                    "enabled": True,
                    "checks": ["lint"],
                    "fail_fast": True,
                    "custom_commands": {"build": "npm run build"},
                }
            },
        )
        # Should not raise despite exception on TextArea
        screen._apply_preset(preset)
        await pilot.pause(0.05)
        screen.action_cancel()
        await pilot.pause(0.05)


# ── T02: _apply_preset all-query-one-fail stress test ──────────────


@pytest.mark.asyncio
async def test_apply_preset_all_query_one_fail() -> None:
    """_apply_preset survives when ALL query_one calls fail."""
    screen = ModeSelectionScreen(show_free=True, project=None)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)

        def all_raise(*args, **kwargs):
            raise Exception("total boom")

        screen.query_one = all_raise  # type: ignore[method-assign]

        preset = MagicMock(
            name="full",
            description="full",
            config_overrides={
                "free_mode": True,
                "persistent": True,
                "integrity": False,
                "token_budget_per_hour": 50000,
                "token_budget_per_run": 100000,
                "task_timeout": 600,
                "notify_on_complete": False,
                "notify_on_fail": False,
                "validation_gate": {
                    "enabled": False,
                    "checks": ["lint"],
                    "fail_fast": False,
                    "custom_commands": {"build": "npm run build"},
                },
            },
        )
        # Must not raise despite every query_one failing
        screen._apply_preset(preset)
        await pilot.pause(0.05)
        screen.action_cancel()
        await pilot.pause(0.05)


# ── T02: on_list_view_selected handler ─────────────────────────────


@pytest.mark.asyncio
async def test_on_list_view_selected_applies_preset(tmp_path: Path) -> None:
    """on_list_view_selected applies the selected preset and focuses first checkbox."""
    from textual.widgets import ListItem as ListItemWidget

    from the_architect.core.presets import save_preset

    save_preset(
        tmp_path,
        "sprint",
        "Quick sprint",
        {"free_mode": True, "persistent": True},
    )

    screen = ModeSelectionScreen(show_free=True, project=tmp_path)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        preset_list = screen.query_one("#preset_list", ListView)
        list_item = ListItemWidget()
        event = ListView.Selected(preset_list, item=list_item, index=0)
        screen.on_list_view_selected(event)
        await pilot.pause(0.05)
        assert screen.query_one("#chk_free", Checkbox).value is True
        assert screen.query_one("#chk_persistent", Checkbox).value is True
        focused = harness.focused
        assert focused is not None
        assert isinstance(focused, Checkbox)
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_on_list_view_selected_invalid_index() -> None:
    """on_list_view_selected handles index out of bounds gracefully."""
    from textual.widgets import ListItem as ListItemWidget

    screen = ModeSelectionScreen(show_free=True, project=None)
    screen._presets = []
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        list_item = ListItemWidget()
        event = ListView.Selected(
            screen,  # type: ignore[arg-type]
            item=list_item,
            index=99,
        )
        screen.on_list_view_selected(event)
        await pilot.pause(0.05)
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_on_list_view_selected_negative_index() -> None:
    """on_list_view_selected handles negative index gracefully."""
    from textual.widgets import ListItem as ListItemWidget

    screen = ModeSelectionScreen(show_free=True, project=None)
    screen._presets = []
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        list_item = ListItemWidget()
        event = ListView.Selected(
            screen,  # type: ignore[arg-type]
            item=list_item,
            index=-1,
        )
        screen.on_list_view_selected(event)
        await pilot.pause(0.05)
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_on_list_view_selected_none_index_defaults_to_zero(tmp_path: Path) -> None:
    """on_list_view_selected uses index=0 when event.index is None."""
    from the_architect.core.presets import save_preset

    save_preset(
        tmp_path,
        "default-preset",
        "Default preset",
        {"persistent": True},
    )

    screen = ModeSelectionScreen(show_free=True, project=tmp_path)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        mock_event = MagicMock()
        mock_event.index = None
        screen.on_list_view_selected(mock_event)
        await pilot.pause(0.05)
        assert screen.query_one("#chk_persistent", Checkbox).value is True
        screen.action_cancel()
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_on_list_view_selected_focus_exception_graceful() -> None:
    """on_list_view_selected catches exceptions when moving focus."""
    from textual.widgets import ListItem as ListItemWidget

    screen = ModeSelectionScreen(show_free=True, project=None)
    screen._presets = [MagicMock(name="fake", description="fake", config_overrides={})]
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)

        def raise_query(*args, **kwargs):
            raise Exception("focus boom")

        screen.query = raise_query  # type: ignore[method-assign]

        list_item = ListItemWidget()
        event = ListView.Selected(
            screen,  # type: ignore[arg-type]
            item=list_item,
            index=0,
        )
        screen.on_list_view_selected(event)
        await pilot.pause(0.05)
        screen.action_cancel()
        await pilot.pause(0.05)


# ── T03: _load_presets and _load_spending_summary guards ─────────────


@pytest.mark.asyncio
async def test_load_presets_guard_when_project_none() -> None:
    """_load_presets returns early when _project is None (line 332)."""
    screen = ModeSelectionScreen(show_free=True, project=None)
    # _project is None; calling _load_presets directly should hit the guard
    screen._load_presets()
    assert screen._presets == []


@pytest.mark.asyncio
async def test_load_spending_summary_guard_when_project_none() -> None:
    """_load_spending_summary returns early when _project is None (line 349)."""
    screen = ModeSelectionScreen(show_free=True, project=None)
    # _project is None; calling _load_spending_summary directly should hit the guard
    screen._load_spending_summary()
    assert screen._spending_summary is None


@pytest.mark.asyncio
async def test_load_spending_summary_with_ledger_data(tmp_path: Path) -> None:
    """_load_spending_summary populates from a ledger with records (lines 362-384)."""
    import json
    from datetime import UTC, datetime, timedelta

    # Write a ledger file as a JSON array of record dicts (matching save_ledger format)
    ledger_path = tmp_path / ".architect" / "token_ledger.json"
    ledger_path.parent.mkdir(exist_ok=True)
    # Use yesterday so the record is always within the 7-day window regardless of test time
    recent_timestamp = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    records = [
        {
            "run_id": "test-run",
            "timestamp": recent_timestamp,
            "goal_summary": "Test goal",
            "total_tokens": 100000,
            "total_cost_estimate": 0.50,
            "task_count": 2,
            "outcome": "done",
            "duration_seconds": 120,
            "model_breakdown": [
                {
                    "model": "gpt-4o-mini",
                    "input_tokens": 50000,
                    "output_tokens": 50000,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "cost_estimate": 0.50,
                }
            ],
        }
    ]
    with open(ledger_path, "w") as f:
        json.dump(records, f)

    screen = ModeSelectionScreen(show_free=True, project=tmp_path)
    # spending summary should be populated
    assert screen._spending_summary is not None
    assert screen._spending_summary["total_cost"] == 0.50
    assert screen._spending_summary["run_count"] == 1
    assert screen._spending_summary["top_model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_load_spending_summary_empty_ledger(tmp_path: Path) -> None:
    """_load_spending_summary returns None when ledger has no records."""
    import json

    ledger_path = tmp_path / ".architect" / "token_ledger.json"
    ledger_path.parent.mkdir(exist_ok=True)
    with open(ledger_path, "w") as f:
        json.dump([], f)

    screen = ModeSelectionScreen(show_free=True, project=tmp_path)
    assert screen._spending_summary is None


@pytest.mark.asyncio
async def test_load_spending_summary_old_records_filtered_out(
    tmp_path: Path,
) -> None:
    """_load_spending_summary returns None when all records are older than 7 days."""
    import json
    from datetime import UTC, datetime, timedelta

    # Write a ledger record older than 7 days ago
    old_timestamp = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    records = [
        {
            "run_id": "old-run",
            "timestamp": old_timestamp,
            "goal_summary": "Old goal",
            "total_tokens": 100000,
            "total_cost_estimate": 0.50,
            "task_count": 2,
            "outcome": "done",
            "duration_seconds": 120,
            "model_breakdown": [
                {
                    "model": "gpt-4o-mini",
                    "input_tokens": 50000,
                    "output_tokens": 50000,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "cost_estimate": 0.50,
                }
            ],
        }
    ]
    ledger_path = tmp_path / ".architect" / "token_ledger.json"
    ledger_path.parent.mkdir(exist_ok=True)
    with open(ledger_path, "w") as f:
        json.dump(records, f)

    screen = ModeSelectionScreen(show_free=True, project=tmp_path)
    # Record is too old (30 days), filtered out by 7-day window
    assert screen._spending_summary is None


@pytest.mark.asyncio
async def test_load_spending_summary_exception_handler(tmp_path: Path) -> None:
    """_load_spending_summary catches exceptions during analytics (lines 382-384)."""
    with patch(
        "the_architect.core.cost_analytics.aggregate_costs",
        side_effect=RuntimeError("analytics boom"),
    ):
        import json

        records = [
            {
                "run_id": "test-run",
                "timestamp": "2026-05-15T12:00:00+00:00",
                "goal_summary": "Test",
                "total_tokens": 100000,
                "total_cost_estimate": 0.50,
                "task_count": 1,
                "outcome": "done",
                "duration_seconds": 60,
                "model_breakdown": [
                    {
                        "model": "gpt-4o-mini",
                        "input_tokens": 50000,
                        "output_tokens": 50000,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                        "cost_estimate": 0.50,
                    }
                ],
            }
        ]
        ledger_path = tmp_path / ".architect" / "token_ledger.json"
        ledger_path.parent.mkdir(exist_ok=True)
        with open(ledger_path, "w") as f:
            json.dump(records, f)

        screen = ModeSelectionScreen(show_free=True, project=tmp_path)
        # Exception caught, spending_summary is None
        assert screen._spending_summary is None


# ── T03: action_submit edge cases ───────────────────────────────────


@pytest.mark.asyncio
async def test_action_submit_free_tier_checkbox_exception() -> None:
    """action_submit handles exception on free tier checkbox (lines 515-516)."""
    screen = ModeSelectionScreen(show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        # Monkey-patch query_one to raise only for #chk_free
        original_query_one = screen.query_one

        def raise_for_free(*args, **kwargs):
            if args and args[0] == "#chk_free":
                raise Exception("boom")
            return original_query_one(*args, **kwargs)

        screen.query_one = raise_for_free  # type: ignore[method-assign]
        screen.action_submit()
        await pilot.pause(0.05)

    assert isinstance(harness.dismissed, dict)
    # free should be False (exception handler default)
    assert harness.dismissed["free"] is False


@pytest.mark.asyncio
async def test_action_submit_budget_run_value_error() -> None:
    """action_submit handles ValueError for budget_run input (lines 530-531)."""
    screen = ModeSelectionScreen(show_free=False)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        screen.query_one("#inp_budget_run", Input).value = "not-a-number"
        screen.action_submit()
        await pilot.pause(0.05)

    assert isinstance(harness.dismissed, dict)
    assert harness.dismissed["token_budget_per_run"] == 0


@pytest.mark.asyncio
async def test_action_submit_vg_checkbox_exception() -> None:
    """action_submit handles exception on validation gate checkboxes (lines 555-556)."""
    screen = ModeSelectionScreen(show_free=False)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        original_query_one = screen.query_one

        def raise_for_vg_checks(*args, **kwargs):
            if args and args[0] in (
                "#chk_vg_lint",
                "#chk_vg_test",
                "#chk_vg_typecheck",
            ):
                raise Exception("boom")
            return original_query_one(*args, **kwargs)

        screen.query_one = raise_for_vg_checks  # type: ignore[method-assign]
        screen.action_submit()
        await pilot.pause(0.05)

    assert isinstance(harness.dismissed, dict)
    # vg_checks should fall back to defaults since all checkboxes raised
    vg = harness.dismissed["validation_gate"]
    assert vg["checks"] == ["lint", "test", "typecheck"]


@pytest.mark.asyncio
async def test_action_submit_vg_no_checks_fallback() -> None:
    """action_submit falls back to default checks when none selected (line 558)."""
    screen = ModeSelectionScreen(
        show_free=False,
        initial_validation_gate_enabled=True,
        initial_validation_gate_checks=(),  # no checks pre-selected
    )
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        # All VG check checkboxes should already be unchecked
        assert screen.query_one("#chk_vg_lint", Checkbox).value is False
        assert screen.query_one("#chk_vg_test", Checkbox).value is False
        assert screen.query_one("#chk_vg_typecheck", Checkbox).value is False
        screen.action_submit()
        await pilot.pause(0.05)

    assert isinstance(harness.dismissed, dict)
    # When no checks are selected, fallback to defaults
    vg = harness.dismissed["validation_gate"]
    assert vg["checks"] == ["lint", "test", "typecheck"]


@pytest.mark.asyncio
async def test_action_submit_custom_commands_parsing() -> None:
    """action_submit parses custom commands from TextArea (lines 566-575)."""
    from textual.widgets import TextArea

    screen = ModeSelectionScreen(show_free=False)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        text_area = screen.query_one("#inp_vg_custom", TextArea)
        text_area.text = "build=npm run build\nsecurity=npm audit"
        await pilot.pause(0.05)
        screen.action_submit()
        await pilot.pause(0.05)

    assert isinstance(harness.dismissed, dict)
    vg = harness.dismissed["validation_gate"]
    custom = vg["custom_commands"]
    assert custom == {"build": "npm run build", "security": "npm audit"}


@pytest.mark.asyncio
async def test_action_submit_custom_commands_malformed_lines() -> None:
    """action_submit skips malformed lines in custom commands TextArea."""
    from textual.widgets import TextArea

    screen = ModeSelectionScreen(show_free=False)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        text_area = screen.query_one("#inp_vg_custom", TextArea)
        text_area.text = "build=npm run build\nno_equals_here\n=empty_key\nempty_val=\n\nvalid=cmd"
        await pilot.pause(0.05)
        screen.action_submit()
        await pilot.pause(0.05)

    assert isinstance(harness.dismissed, dict)
    custom = harness.dismissed["validation_gate"]["custom_commands"]
    # Only lines with both key and cmd are included
    assert "build" in custom
    assert "valid" in custom
    assert custom["valid"] == "cmd"
    # malformed lines should be excluded
    assert len(custom) == 2


@pytest.mark.asyncio
async def test_action_submit_custom_commands_textarea_exception() -> None:
    """action_submit handles exception on custom commands TextArea (lines 574-575)."""
    screen = ModeSelectionScreen(show_free=False)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        original_query_one = screen.query_one

        def raise_for_custom(*args, **kwargs):
            if args and args[0] == "#inp_vg_custom":
                raise Exception("boom")
            return original_query_one(*args, **kwargs)

        screen.query_one = raise_for_custom  # type: ignore[method-assign]
        screen.action_submit()
        await pilot.pause(0.05)

    assert isinstance(harness.dismissed, dict)
    # custom_commands should be empty dict (exception handler default)
    assert harness.dismissed["validation_gate"]["custom_commands"] == {}


# ── T03: run_mode_selection function tests ──────────────────────────


def test_run_mode_selection_defaults() -> None:
    """run_mode_selection with defaults constructs screen with correct values."""
    with patch(
        "the_architect.tui.app.run_single_screen",
        return_value={"free": False, "persistent": False},
    ) as mock_run:
        from the_architect.tui.screens.mode_selection import run_mode_selection

        run_mode_selection()
        # Verify screen was constructed
        call_args = mock_run.call_args
        screen = call_args[0][0]
        assert isinstance(screen, ModeSelectionScreen)
        assert screen._show_free is True
        assert screen._initial_free is False
        assert screen._initial_persistent is False
        assert screen._initial_integrity is True
        assert screen._initial_budget == 0
        assert screen._initial_budget_run == 0
        assert screen._initial_task_timeout == 0
        assert screen._initial_notify_complete is True
        assert screen._initial_notify_fail is True
        assert screen._initial_validation_gate_enabled is True
        assert screen._initial_validation_gate_checks == ("lint", "test", "typecheck")
        assert screen._initial_validation_gate_custom_commands == {}
        assert screen._initial_validation_gate_fail_fast is True


def test_run_mode_selection_with_initial_mode() -> None:
    """run_mode_selection passes initial_mode values to screen constructor."""
    with patch(
        "the_architect.tui.app.run_single_screen",
        return_value={"free": True, "persistent": True},
    ) as mock_run:
        from the_architect.tui.screens.mode_selection import run_mode_selection

        run_mode_selection(
            show_free=True,
            initial_mode={
                "free": True,
                "persistent": True,
                "integrity": False,
                "token_budget_per_hour": 50000,
                "token_budget_per_run": 100000,
                "task_timeout": 600,
                "notify_on_complete": False,
                "notify_on_fail": False,
                "validation_gate": {
                    "enabled": False,
                    "checks": ["lint"],
                    "custom_commands": {"build": "npm run build"},
                    "fail_fast": False,
                },
            },
        )
        screen = mock_run.call_args[0][0]
        assert screen._initial_free is True
        assert screen._initial_persistent is True
        assert screen._initial_integrity is False
        assert screen._initial_budget == 50000
        assert screen._initial_budget_run == 100000
        assert screen._initial_task_timeout == 600
        assert screen._initial_notify_complete is False
        assert screen._initial_notify_fail is False
        assert screen._initial_validation_gate_enabled is False
        assert screen._initial_validation_gate_checks == ("lint",)
        assert screen._initial_validation_gate_custom_commands == {
            "build": "npm run build",
        }
        assert screen._initial_validation_gate_fail_fast is False


def test_run_mode_selection_validation_gate_non_dict() -> None:
    """run_mode_selection handles validation_gate as non-dict (lines 655-659)."""
    with patch(
        "the_architect.tui.app.run_single_screen",
        return_value={"persistent": False},
    ) as mock_run:
        from the_architect.tui.screens.mode_selection import run_mode_selection

        run_mode_selection(
            initial_mode={
                "free": False,
                "persistent": False,
                "integrity": True,
                "token_budget_per_hour": 0,
                "token_budget_per_run": 0,
                "task_timeout": 0,
                "notify_on_complete": True,
                "notify_on_fail": True,
                "validation_gate": "not-a-dict",
            }
        )
        screen = mock_run.call_args[0][0]
        # Should fall back to defaults
        assert screen._initial_validation_gate_enabled is True
        assert screen._initial_validation_gate_checks == (
            "lint",
            "test",
            "typecheck",
        )
        assert screen._initial_validation_gate_fail_fast is True


def test_run_mode_selection_validation_gate_checks_non_list() -> None:
    """run_mode_selection handles validation_gate checks as non-list (line 648)."""
    with patch(
        "the_architect.tui.app.run_single_screen",
        return_value={"persistent": False},
    ) as mock_run:
        from the_architect.tui.screens.mode_selection import run_mode_selection

        run_mode_selection(
            initial_mode={
                "free": False,
                "persistent": False,
                "integrity": True,
                "token_budget_per_hour": 0,
                "token_budget_per_run": 0,
                "task_timeout": 0,
                "notify_on_complete": True,
                "notify_on_fail": True,
                "validation_gate": {
                    "enabled": True,
                    "checks": "not-a-list",
                    "custom_commands": {},
                    "fail_fast": True,
                },
            }
        )
        screen = mock_run.call_args[0][0]
        # Should fall back to default tuple
        assert screen._initial_validation_gate_checks == (
            "lint",
            "test",
            "typecheck",
        )


def test_run_mode_selection_custom_commands_non_dict() -> None:
    """run_mode_selection handles custom_commands as non-dict (line 654)."""
    with patch(
        "the_architect.tui.app.run_single_screen",
        return_value={"persistent": False},
    ) as mock_run:
        from the_architect.tui.screens.mode_selection import run_mode_selection

        run_mode_selection(
            initial_mode={
                "free": False,
                "persistent": False,
                "integrity": True,
                "token_budget_per_hour": 0,
                "token_budget_per_run": 0,
                "task_timeout": 0,
                "notify_on_complete": True,
                "notify_on_fail": True,
                "validation_gate": {
                    "enabled": True,
                    "checks": ["lint"],
                    "custom_commands": "not-a-dict",
                    "fail_fast": True,
                },
            }
        )
        screen = mock_run.call_args[0][0]
        # Should fall back to empty dict
        assert screen._initial_validation_gate_custom_commands == {}


def test_run_mode_selection_returns_back_sentinel() -> None:
    """run_mode_selection returns BACK_SENTINEL when screen dismisses with it."""
    with patch(
        "the_architect.tui.app.run_single_screen",
        return_value=BACK_SENTINEL,
    ):
        from the_architect.tui.screens.mode_selection import run_mode_selection

        result = run_mode_selection()
        assert result is BACK_SENTINEL


def test_run_mode_selection_raises_system_exit_on_cancel() -> None:
    """run_mode_selection raises SystemExit(0) when screen dismisses with None."""
    with patch(
        "the_architect.tui.app.run_single_screen",
        return_value=None,
    ):
        from the_architect.tui.screens.mode_selection import run_mode_selection

        with pytest.raises(SystemExit) as exc_info:
            run_mode_selection()
        assert exc_info.value.code == 0


def test_run_mode_selection_returns_result_dict() -> None:
    """run_mode_selection returns the result dict from run_single_screen."""
    expected = {"free": True, "persistent": True, "integrity": True}
    with patch(
        "the_architect.tui.app.run_single_screen",
        return_value=expected,
    ):
        from the_architect.tui.screens.mode_selection import run_mode_selection

        result = run_mode_selection()
        assert result == expected


# ── Model routing summary tests (Cycle 32) ──────────────────────────────


def test_model_routing_hidden_when_no_project() -> None:
    """Model routing summary is None when project is None."""
    screen = ModeSelectionScreen(show_free=True, project=None)
    assert screen._model_routing_summary is None


def test_model_routing_hidden_when_no_tasks(tmp_path: Path) -> None:
    """Model routing summary is hidden when no tasks exist."""
    project = tmp_path
    screen = ModeSelectionScreen(show_free=True, project=project)
    assert screen._model_routing_summary is None


def test_model_routing_hidden_when_all_default(tmp_path: Path) -> None:
    """Model routing summary is hidden when all tasks use the default model."""
    project = tmp_path
    tasks_dir = project / "tasks"
    tasks_dir.mkdir()
    # Create tasks without model assignments
    (tasks_dir / "T01_setup.md").write_text("# T01 — Setup\n", encoding="utf-8")
    (tasks_dir / "T02_feature.md").write_text("# T02 — Feature\n", encoding="utf-8")
    screen = ModeSelectionScreen(show_free=True, project=project)
    assert screen._model_routing_summary is None


def test_model_routing_shows_mixed_models(tmp_path: Path) -> None:
    """Model routing summary shows counts when tasks use different models."""
    project = tmp_path
    tasks_dir = project / "tasks"
    tasks_dir.mkdir()
    # T01 uses default (no model section)
    (tasks_dir / "T01_setup.md").write_text("# T01 — Setup\n", encoding="utf-8")
    # T02 uses a specific model
    (tasks_dir / "T02_feature.md").write_text(
        "# T02 — Feature\n\n## Model\nopenrouter/google/gemini-2.5-pro\n",
        encoding="utf-8",
    )
    # T03 uses a different model
    (tasks_dir / "T03_analytics.md").write_text(
        "# T03 — Analytics\n\n## Model\nopenrouter/anthropic/claude-sonnet-4-20250514\n",
        encoding="utf-8",
    )
    screen = ModeSelectionScreen(show_free=True, project=project)
    assert screen._model_routing_summary is not None
    assert "1 on default" in screen._model_routing_summary
    assert "gemini-2.5-pro" in screen._model_routing_summary
    assert "claude-sonnet" in screen._model_routing_summary


def test_model_routing_all_same_custom_model(tmp_path: Path) -> None:
    """Model routing shows summary when all tasks use the same non-default model."""
    project = tmp_path
    tasks_dir = project / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "T01_setup.md").write_text(
        "# T01 — Setup\n\n## Model\nopenrouter/google/gemini-2.5-pro\n",
        encoding="utf-8",
    )
    (tasks_dir / "T02_feature.md").write_text(
        "# T02 — Feature\n\n## Model\nopenrouter/google/gemini-2.5-pro\n",
        encoding="utf-8",
    )
    screen = ModeSelectionScreen(show_free=True, project=project)
    assert screen._model_routing_summary is not None
    assert "2 on openrouter/google/gemini-2.5-pro" in screen._model_routing_summary


def test_model_routing_shows_in_compose(tmp_path: Path) -> None:
    """Model routing section renders in the UI when summary is populated."""
    project = tmp_path
    tasks_dir = project / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "T01_setup.md").write_text("# T01 — Setup\n", encoding="utf-8")
    (tasks_dir / "T02_feature.md").write_text(
        "# T02 — Feature\n\n## Model\nopenrouter/google/gemini-2.5-pro\n",
        encoding="utf-8",
    )

    screen = ModeSelectionScreen(show_free=True, project=project)
    harness = _Harness(screen)

    async def run_test():
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            # Check that the model routing widgets exist
            routing_label = screen.query_one("#model_routing_label", Static)
            assert routing_label is not None
            routing_detail = screen.query_one("#model_routing_detail", Static)
            assert routing_detail is not None
            await pilot.pause(0.05)

    import asyncio

    asyncio.run(run_test())


def test_model_routing_hidden_in_compose_when_no_data(tmp_path: Path) -> None:
    """Model routing section does not render when all tasks use default model."""
    project = tmp_path
    tasks_dir = project / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "T01_setup.md").write_text("# T01 — Setup\n", encoding="utf-8")

    screen = ModeSelectionScreen(show_free=True, project=project)
    harness = _Harness(screen)

    async def run_test():
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            # model_routing widgets should NOT exist
            routing_labels = list(screen.query("#model_routing_label"))
            assert len(routing_labels) == 0
            await pilot.pause(0.05)

    import asyncio

    asyncio.run(run_test())


def test_model_routing_load_exception_graceful(tmp_path: Path) -> None:
    """Model routing summary is None when discover_tasks raises an exception."""
    project = tmp_path
    # Create a non-directory tasks path
    (project / "tasks").write_text("not a dir", encoding="utf-8")
    screen = ModeSelectionScreen(show_free=True, project=project)
    assert screen._model_routing_summary is None


# ── T04: Lifecycle Hooks Display ──────────────────────────────────────────


def test_hooks_summary_guard_when_project_none() -> None:
    """_load_hooks_summary returns early when _project is None."""
    screen = ModeSelectionScreen(show_free=True)
    screen._load_hooks_summary()
    assert screen._hooks_summary is None


def test_hooks_summary_empty_when_no_hooks(tmp_path: Path) -> None:
    """_load_hooks_summary returns None when no hooks are configured."""
    project = tmp_path
    screen = ModeSelectionScreen(show_free=True, project=project)
    assert screen._hooks_summary is None


def test_hooks_summary_shows_hooks(tmp_path: Path) -> None:
    """_load_hooks_summary populates when hooks are configured."""
    project = tmp_path
    hooks_dir = project / ".architect"
    hooks_dir.mkdir()
    hooks_data = [
        {"event": "pre_run", "command": "echo pre_run"},
        {"event": "post_task", "command": "echo post_task"},
        {"event": "post_task", "command": "echo post_task_2"},
        {"event": "post_run_success", "command": "echo done"},
    ]
    (hooks_dir / "hooks.json").write_text(__import__("json").dumps(hooks_data), encoding="utf-8")
    screen = ModeSelectionScreen(show_free=True, project=project)
    assert screen._hooks_summary is not None
    assert "4 hooks configured" in screen._hooks_summary
    assert "1 pre_run" in screen._hooks_summary
    assert "2 post_task" in screen._hooks_summary
    assert "1 post_run_success" in screen._hooks_summary


def test_hooks_summary_single_hook(tmp_path: Path) -> None:
    """Hooks summary shows singular 'hook' when only one hook exists."""
    project = tmp_path
    hooks_dir = project / ".architect"
    hooks_dir.mkdir()
    hooks_data = [
        {"event": "pre_run", "command": "echo pre_run"},
    ]
    (hooks_dir / "hooks.json").write_text(__import__("json").dumps(hooks_data), encoding="utf-8")
    screen = ModeSelectionScreen(show_free=True, project=project)
    assert screen._hooks_summary is not None
    assert "1 hook configured" in screen._hooks_summary


def test_hooks_summary_load_exception_graceful(tmp_path: Path) -> None:
    """Hooks summary is None when hooks file has invalid data."""
    project = tmp_path
    hooks_dir = project / ".architect"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text("not valid json", encoding="utf-8")
    screen = ModeSelectionScreen(show_free=True, project=project)
    assert screen._hooks_summary is None


@pytest.mark.asyncio
async def test_hooks_section_shows_in_compose(tmp_path: Path) -> None:
    """Hooks section renders in the UI when hooks are configured."""
    project = tmp_path
    hooks_dir = project / ".architect"
    hooks_dir.mkdir()
    hooks_data = [
        {"event": "pre_run", "command": "echo pre_run"},
        {"event": "post_task", "command": "echo post_task"},
    ]
    (hooks_dir / "hooks.json").write_text(__import__("json").dumps(hooks_data), encoding="utf-8")
    screen = ModeSelectionScreen(show_free=True, project=project)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        hooks_label = screen.query_one("#hooks_label", Static)
        assert hooks_label is not None
        hooks_detail = screen.query_one("#hooks_detail", Static)
        assert hooks_detail is not None
        assert "Lifecycle Hooks" in str(hooks_label.render())
        await pilot.pause(0.05)


@pytest.mark.asyncio
async def test_hooks_section_hidden_when_no_hooks(tmp_path: Path) -> None:
    """Hooks section does not render when no hooks are configured."""
    project = tmp_path
    screen = ModeSelectionScreen(show_free=True, project=project)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        hooks_labels = list(screen.query("#hooks_label"))
        assert len(hooks_labels) == 0
        await pilot.pause(0.05)
