"""Tests for the Textual ModeSelectionScreen.

Phase 16 converted ``ModeSelectionApp`` to a ``Screen`` subclass.
``ModeSelectionApp`` remains as a legacy alias, but the screen must be
mounted inside a host app to run. We use a minimal harness app.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

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
        "notify_on_complete": True,
        "notify_on_fail": True,
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
        "notify_on_complete": True,
        "notify_on_fail": True,
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

    Regression guard: the bindings ``up``/``down`` reference the actions
    ``focus_previous``/``focus_next``. Textual's :class:`Screen` exposes
    those as plain methods, so a Binding pointing at them silently does
    nothing unless the screen defines ``action_focus_previous`` /
    ``action_focus_next`` shims. This test asserts the focused widget's
    id genuinely changes — a weaker "not None" check lets the regression
    slip through.
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
            f"Down arrow did not move focus: stayed on {first_focused!r}. "
            "Check that action_focus_next exists on the screen."
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
        "notify_on_complete": True,
        "notify_on_fail": True,
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
