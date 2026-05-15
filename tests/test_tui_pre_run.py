"""Tests for Phase 12 pre-run screens (provider / scope and related pickers).

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
from textual.widgets import ListView

from the_architect.tui.screens.pre_run import (
    BACK_SENTINEL,
    ProviderOption,
    ProviderSelectionScreen,
    ScopeScreen,
    StringListPickerScreen,
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
            await pilot.pause(0.05)
            lv = screen.query_one("#provider_list", ListView)
            lv.index = 1
            await pilot.pause(0.05)
            screen.action_confirm()
            await pilot.pause(0.05)
        assert harness.dismissed == 1

    @pytest.mark.asyncio
    async def test_cancel_returns_none(self) -> None:
        screen = ProviderSelectionScreen(options=[_fake_provider("A"), _fake_provider("B")])
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            screen.action_cancel()
            await pilot.pause(0.05)
        assert harness.dismissed is None


class TestScopeScreen:
    @pytest.mark.asyncio
    async def test_default_confirm_returns_standard(self) -> None:
        screen = ScopeScreen()
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            screen.action_confirm()
            await pilot.pause(0.05)
        assert harness.dismissed == "standard"

    @pytest.mark.asyncio
    async def test_second_item_returns_simple(self) -> None:
        screen = ScopeScreen()
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            lv = screen.query_one("#scope_list", ListView)
            lv.index = 1
            await pilot.pause(0.05)
            screen.action_confirm()
            await pilot.pause(0.05)
        assert harness.dismissed == "simple"

    @pytest.mark.asyncio
    async def test_third_item_returns_complex(self) -> None:
        screen = ScopeScreen()
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            lv = screen.query_one("#scope_list", ListView)
            lv.index = 2
            await pilot.pause(0.05)
            screen.action_confirm()
            await pilot.pause(0.05)
        assert harness.dismissed == "complex"

    @pytest.mark.asyncio
    async def test_cancel_returns_none(self) -> None:
        screen = ScopeScreen()
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            screen.action_cancel()
            await pilot.pause(0.05)
        assert harness.dismissed is None


# ── Phase A: Back navigation ─────────────────────────────────────────────


class TestBackNavigation:
    """Phase A: each pre-run screen dismisses with BACK_SENTINEL on Back."""

    @pytest.mark.asyncio
    async def test_scope_screen_back_returns_sentinel(self) -> None:
        screen = ScopeScreen()
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            screen.action_go_back()
            await pilot.pause(0.05)
        assert harness.dismissed is BACK_SENTINEL

    @pytest.mark.asyncio
    async def test_provider_screen_back_returns_sentinel(self) -> None:
        screen = ProviderSelectionScreen(
            options=[_fake_provider("OpenCode"), _fake_provider("Claude Code")]
        )
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            screen.action_go_back()
            await pilot.pause(0.05)
        assert harness.dismissed is BACK_SENTINEL

    @pytest.mark.asyncio
    async def test_string_list_picker_back_returns_sentinel(self) -> None:
        screen = StringListPickerScreen(title="Test", hint="Pick", choices=[("a", "A"), ("b", "B")])
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            screen.action_go_back()
            await pilot.pause(0.05)
        assert harness.dismissed is BACK_SENTINEL


# ── Phase A: Pre-fill constructors ──────────────────────────────────────


class TestPreFill:
    """Phase A: screen constructors accept initial values for pre-fill."""

    @pytest.mark.asyncio
    async def test_scope_screen_initial_scope_complex(self) -> None:
        screen = ScopeScreen(initial_scope="complex")
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            lv = screen.query_one("#scope_list", ListView)
            assert lv.index == 2

    @pytest.mark.asyncio
    async def test_scope_screen_initial_scope_simple(self) -> None:
        screen = ScopeScreen(initial_scope="simple")
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            lv = screen.query_one("#scope_list", ListView)
            assert lv.index == 1

    @pytest.mark.asyncio
    async def test_provider_screen_initial_provider(self) -> None:
        opts = [_fake_provider("OpenCode"), _fake_provider("Claude Code")]
        # The second provider's mock .name attribute
        opts[1].provider.name = "claude-code"
        screen = ProviderSelectionScreen(options=opts, initial_provider_name="claude-code")
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            lv = screen.query_one("#provider_list", ListView)
            assert lv.index == 1

    @pytest.mark.asyncio
    async def test_string_list_picker_initial_value(self) -> None:
        screen = StringListPickerScreen(
            title="Test",
            hint="Pick",
            choices=[("a", "A"), ("b", "B"), ("c", "C")],
            initial_value="b",
        )
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            lv = screen.query_one("#picker_list", ListView)
            assert lv.index == 1
