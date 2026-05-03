"""Tests for Phase 13/14 pre-run screens (pickers, update, pending).

Phase 16 note: converted to test ``*Screen`` classes inside a harness
app instead of standalone ``App`` subclasses.
"""

from __future__ import annotations

from typing import Any

import pytest
from textual.app import App
from textual.widgets import ListView

from the_architect.tui.screens.pre_run import (
    PendingTasksScreen,
    StringListPickerScreen,
    UpdateActionScreen,
)


class _Harness(App[None]):
    """Minimal app harness that pushes a single screen."""

    def __init__(self, screen: Any) -> None:
        super().__init__()
        self._screen = screen
        self.dismissed: Any = "<not-dismissed>"

    def on_mount(self) -> None:
        self.push_screen(self._screen, self._on_dismiss)

    def _on_dismiss(self, value: Any) -> None:
        self.dismissed = value


class TestStringListPicker:
    @pytest.mark.asyncio
    async def test_default_confirm_returns_first_item(self) -> None:
        screen = StringListPickerScreen(
            title="Pick",
            hint="Hint",
            choices=[("a", "  a"), ("b", "  b")],
        )
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause()
            screen.action_confirm()
            await pilot.pause()
        assert harness.dismissed == "a"

    @pytest.mark.asyncio
    async def test_navigating_and_confirming_returns_second_item(self) -> None:
        screen = StringListPickerScreen(
            title="Pick",
            hint="Hint",
            choices=[("a", "  a"), ("b", "  b")],
        )
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause()
            lv = screen.query_one("#picker_list", ListView)
            lv.index = 1
            await pilot.pause()
            screen.action_confirm()
            await pilot.pause()
        assert harness.dismissed == "b"

    @pytest.mark.asyncio
    async def test_cancel_returns_none(self) -> None:
        screen = StringListPickerScreen(
            title="Pick",
            hint="Hint",
            choices=[("a", "  a"), ("b", "  b")],
        )
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause()
            screen.action_cancel()
            await pilot.pause()
        assert harness.dismissed is None


class TestUpdateActionScreen:
    @pytest.mark.asyncio
    async def test_continue_returns_continue(self) -> None:
        screen = UpdateActionScreen(update_msg="outdated", install_hint="pip install -U x")
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause()
            screen.action_confirm()
            await pilot.pause()
        assert harness.dismissed == "continue"

    @pytest.mark.asyncio
    async def test_exit_returns_exit(self) -> None:
        screen = UpdateActionScreen(update_msg="outdated", install_hint="pip install -U x")
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause()
            screen.action_exit()
            await pilot.pause()
        assert harness.dismissed == "exit"


class TestPendingTasksScreen:
    @pytest.mark.asyncio
    async def test_confirm_returns_true(self) -> None:
        screen = PendingTasksScreen(pending=["T03_api", "T04_tests"])
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause()
            screen.action_confirm()
            await pilot.pause()
        assert harness.dismissed is True

    @pytest.mark.asyncio
    async def test_abort_returns_false(self) -> None:
        screen = PendingTasksScreen(pending=["T03_api"])
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause()
            screen.action_abort()
            await pilot.pause()
        assert harness.dismissed is False


class TestSpinnersSilentInTuiMode:
    def test_live_spinner_is_noop_when_tui_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from the_architect.cli import _start_live_spinner

        monkeypatch.setenv("ARCHITECT_TUI", "1")
        handle = _start_live_spinner("planning…")
        try:
            assert handle._thread is None
        finally:
            handle.stop()

    def test_wait_spinner_is_noop_when_tui_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from the_architect.cli import _start_wait_spinner

        monkeypatch.setenv("ARCHITECT_TUI", "1")
        handle = _start_wait_spinner("loading models…")
        try:
            assert handle._thread is None
        finally:
            handle.stop()
