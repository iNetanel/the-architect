"""Tests for the Textual ResumeScreen.

Phase 21 converted ``ResumeApp`` to a ``Screen`` subclass;
``ResumeApp`` remains as a legacy alias. Tests mount the screen inside
a small harness app.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from textual.app import App
from textual.widgets import Checkbox, Input

from the_architect.config import ArchitectConfig
from the_architect.core.tasks import Task, TaskStatus
from the_architect.tui.screens.resume import ResumeScreen


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

    Regression guard: the bindings ``up``/``down`` reference the actions
    ``focus_previous``/``focus_next``. Textual's :class:`Screen` exposes
    those as plain methods, so a Binding pointing at them silently does
    nothing unless the screen defines ``action_focus_previous`` /
    ``action_focus_next`` shims.
    """
    config = ArchitectConfig()
    screen = ResumeScreen(pending_tasks=_make_pending_tasks(2), config=config, show_free=True)
    harness = _Harness(screen)
    async with harness.run_test() as pilot:
        await pilot.pause(0.05)
        first_focused = harness.focused.id if harness.focused else None
        assert first_focused is not None, "on_mount should focus the first Checkbox"

        if first_focused == "action_set":
            first_focused = getattr(screen.query("RadioButton").first(), "id", first_focused)

        await pilot.press("down")
        await pilot.pause(0.05)
        second_focused = harness.focused.id if harness.focused else None
        if second_focused == "action_set":
            second_focused = getattr(screen.query("RadioButton").first(), "id", second_focused)
        assert second_focused is not None
        assert second_focused != first_focused, (
            f"Down arrow did not move focus: stayed on {first_focused!r}. "
            "Check that action_focus_next exists on the screen."
        )

        await pilot.press("up")
        await pilot.pause(0.05)
        back_focused = harness.focused.id if harness.focused else None
        if back_focused == "action_set":
            back_focused = getattr(screen.query("RadioButton").first(), "id", back_focused)
        assert back_focused == first_focused, (
            f"Up arrow did not move focus back: expected {first_focused!r}, got {back_focused!r}."
        )
        screen.action_cancel()
        await pilot.pause(0.05)
