"""Tests for Phase 12 pre-run screens (provider / goal / scope).

Phase 16 note: the ``*App`` classes were converted to ``*Screen``
classes that live inside one persistent :class:`ArchitectApp`. Tests
use a small harness app that pushes each screen and captures its
dismiss value via ``on_screen_dismiss``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from textual.app import App
from textual.widgets import ListView, TextArea

from the_architect.tui.screens.pre_run import (
    GoalScreen,
    ProviderOption,
    ProviderSelectionScreen,
    ScopeScreen,
)


def _fake_provider(display: str, version: str = "1.2.3") -> ProviderOption:
    prov = MagicMock()
    prov.display_name = display
    prov.get_version = MagicMock(return_value=version)
    return ProviderOption(display_name=display, version=version, provider=prov)


class _Harness(App[None]):
    """Minimal app harness for mounting a single Screen in tests."""

    def __init__(self, screen: Any) -> None:
        super().__init__()
        self._screen = screen
        self.dismissed: Any = "<not-dismissed>"

    def on_mount(self) -> None:
        self.push_screen(self._screen, self._on_dismiss)

    def _on_dismiss(self, value: Any) -> None:
        self.dismissed = value


class TestProviderSelectionScreen:
    @pytest.mark.asyncio
    async def test_confirm_returns_selected_index(self) -> None:
        screen = ProviderSelectionScreen(
            options=[_fake_provider("OpenCode"), _fake_provider("Claude Code")]
        )
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause()
            lv = screen.query_one("#provider_list", ListView)
            lv.index = 1
            await pilot.pause()
            screen.action_confirm()
            await pilot.pause()
        assert harness.dismissed == 1

    @pytest.mark.asyncio
    async def test_cancel_returns_none(self) -> None:
        screen = ProviderSelectionScreen(options=[_fake_provider("A"), _fake_provider("B")])
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause()
            screen.action_cancel()
            await pilot.pause()
        assert harness.dismissed is None


class TestGoalScreen:
    @pytest.mark.asyncio
    async def test_submit_returns_text(self) -> None:
        screen = GoalScreen()
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause()
            area = screen.query_one("#goal_text", TextArea)
            area.text = "Implement auth"
            screen.action_submit()
            await pilot.pause()
        assert harness.dismissed == "Implement auth"

    @pytest.mark.asyncio
    async def test_submit_ignores_empty_text(self) -> None:
        screen = GoalScreen()
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause()
            # No text typed; submit should be a no-op.
            screen.action_submit()
            await pilot.pause()
            assert harness.dismissed == "<not-dismissed>"
            screen.action_cancel()
            await pilot.pause()
        assert harness.dismissed is None

    @pytest.mark.asyncio
    async def test_cancel_returns_none(self) -> None:
        screen = GoalScreen()
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause()
            screen.action_cancel()
            await pilot.pause()
        assert harness.dismissed is None


class TestScopeScreen:
    @pytest.mark.asyncio
    async def test_default_confirm_returns_standard(self) -> None:
        screen = ScopeScreen()
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause()
            screen.action_confirm()
            await pilot.pause()
        assert harness.dismissed == "standard"

    @pytest.mark.asyncio
    async def test_second_item_returns_simple(self) -> None:
        screen = ScopeScreen()
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause()
            lv = screen.query_one("#scope_list", ListView)
            lv.index = 1
            await pilot.pause()
            screen.action_confirm()
            await pilot.pause()
        assert harness.dismissed == "simple"

    @pytest.mark.asyncio
    async def test_third_item_returns_complex(self) -> None:
        screen = ScopeScreen()
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause()
            lv = screen.query_one("#scope_list", ListView)
            lv.index = 2
            await pilot.pause()
            screen.action_confirm()
            await pilot.pause()
        assert harness.dismissed == "complex"

    @pytest.mark.asyncio
    async def test_cancel_returns_none(self) -> None:
        screen = ScopeScreen()
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause()
            screen.action_cancel()
            await pilot.pause()
        assert harness.dismissed is None
