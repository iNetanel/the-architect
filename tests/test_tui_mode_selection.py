"""Tests for the Textual ModeSelectionScreen.

Phase 16 converted ``ModeSelectionApp`` to a ``Screen`` subclass.
``ModeSelectionApp`` remains as a legacy alias, but the screen must be
mounted inside a host app to run. We use a minimal harness app.
"""

from __future__ import annotations

from typing import Any

import pytest
from textual.app import App
from textual.widgets import Checkbox, Input

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
        await pilot.pause()
        screen.action_submit()
        await pilot.pause()
    assert harness.dismissed == {
        "free": False,
        "persistent": False,
        "integrity": True,
        "token_budget_per_hour": 0,
    }


@pytest.mark.asyncio
async def test_submit_with_toggles_and_budget() -> None:
    screen = ModeSelectionScreen(show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause()
        screen.query_one("#chk_free", Checkbox).value = True
        screen.query_one("#chk_persistent", Checkbox).value = True
        screen.query_one("#chk_integrity", Checkbox).value = False
        screen.query_one("#inp_budget", Input).value = "150000"
        await pilot.pause()
        screen.action_submit()
        await pilot.pause()
    assert harness.dismissed == {
        "free": True,
        "persistent": True,
        "integrity": False,
        "token_budget_per_hour": 150000,
    }


@pytest.mark.asyncio
async def test_submit_hides_free_tier_when_disabled() -> None:
    screen = ModeSelectionScreen(show_free=False)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause()
        screen.query_one("#chk_persistent", Checkbox).value = True
        screen.action_submit()
        await pilot.pause()
    assert isinstance(harness.dismissed, dict)
    result = harness.dismissed
    assert result["free"] is False
    assert result["persistent"] is True


@pytest.mark.asyncio
async def test_cancel_returns_none() -> None:
    screen = ModeSelectionScreen(show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause()
        screen.action_cancel()
        await pilot.pause()
    assert harness.dismissed is None


@pytest.mark.asyncio
async def test_invalid_budget_clamps_to_zero() -> None:
    screen = ModeSelectionScreen(show_free=False)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause()
        screen.query_one("#inp_budget", Input).value = "not-a-number"
        screen.action_submit()
        await pilot.pause()
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
        await pilot.pause()
        first_focused = harness.focused.id if harness.focused else None
        assert first_focused is not None, "on_mount should focus the first Checkbox"

        await pilot.press("down")
        await pilot.pause()
        second_focused = harness.focused.id if harness.focused else None
        assert second_focused is not None
        assert second_focused != first_focused, (
            f"Down arrow did not move focus: stayed on {first_focused!r}. "
            "Check that action_focus_next exists on the screen."
        )

        await pilot.press("up")
        await pilot.pause()
        back_focused = harness.focused.id if harness.focused else None
        assert back_focused == first_focused, (
            f"Up arrow did not move focus back: expected {first_focused!r}, got {back_focused!r}."
        )
        screen.action_cancel()
        await pilot.pause()


# ── Phase A: Back navigation ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_back_returns_sentinel() -> None:
    """Phase A: Backspace dismisses with BACK_SENTINEL instead of None."""
    screen = ModeSelectionScreen(show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause()
        screen.action_go_back()
        await pilot.pause()
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
        await pilot.pause()
        assert screen.query_one("#chk_free", Checkbox).value is True
        assert screen.query_one("#chk_persistent", Checkbox).value is True
        assert screen.query_one("#chk_integrity", Checkbox).value is False
        assert screen.query_one("#inp_budget", Input).value == "50000"
        screen.action_cancel()
        await pilot.pause()
