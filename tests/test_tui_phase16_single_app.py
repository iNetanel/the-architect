"""Tests for Phase 16 — single persistent ArchitectApp orchestration."""

from __future__ import annotations

import threading
from typing import Any

import pytest
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Static

from the_architect.tui.app import ArchitectApp, run_single_screen


class _SimpleDismissScreen(Screen[str]):
    """Minimal screen that dismisses with a value on mount."""

    BINDINGS = [Binding("enter", "go", "Go")]

    def __init__(self, value: str) -> None:
        super().__init__()
        self._value = value

    def compose(self) -> ComposeResult:
        yield Static(f"payload: {self._value}")

    def action_go(self) -> None:
        self.dismiss(self._value)


class TestRunSingleScreen:
    """Legacy stepping-stone helper."""

    def test_returns_dismiss_value(self) -> None:
        # Screens can't be pushed twice; build fresh here.
        class _AutoDismissScreen(Screen[str]):
            def compose(self) -> ComposeResult:
                yield Static("auto")

            def on_mount(self) -> None:
                self.call_after_refresh(self.dismiss, "hello")

        result = run_single_screen(_AutoDismissScreen())
        assert result == "hello"

    def test_returns_none_when_app_exits_without_dismiss(self) -> None:
        class _ImmediateExitScreen(Screen[str]):
            def compose(self) -> ComposeResult:
                yield Static("bye")

            def on_mount(self) -> None:
                # Exit the app without calling dismiss.
                self.call_after_refresh(self.app.exit)

        result = run_single_screen(_ImmediateExitScreen())
        assert result is None


class TestPushAndWait:
    """push_and_wait blocks a worker thread until the screen dismisses."""

    @pytest.mark.asyncio
    async def test_push_and_wait_from_worker_thread(self) -> None:
        app = ArchitectApp()
        captured: dict[str, Any] = {}

        async with app.run_test() as pilot:
            await pilot.pause()

            def _worker() -> None:
                screen = _SimpleDismissScreen("from-worker")
                # Push the screen from the worker thread; it will
                # dismiss when we call its action below.
                future_value = app.push_and_wait(screen)
                captured["value"] = future_value

            t = threading.Thread(target=_worker, daemon=True)
            t.start()

            # Let the worker push the screen, then dismiss from the
            # main thread (the event loop thread).
            await pilot.pause()
            await pilot.pause()
            # The screen is now active — trigger its action to dismiss.
            active_screen = app.screen
            assert isinstance(active_screen, _SimpleDismissScreen)
            active_screen.action_go()
            await pilot.pause()

            t.join(timeout=2.0)

        assert captured["value"] == "from-worker"


class TestArchitectAppTitle:
    """Phase 16: The app is titled 'The Architect'."""

    @pytest.mark.asyncio
    async def test_title_is_the_architect(self) -> None:
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.TITLE == "The Architect"


class TestArchitectAppStatus:
    """Phase 18: run-scoped status appears in every screen header via sub_title."""

    @pytest.mark.asyncio
    async def test_set_status_updates_sub_title(self) -> None:
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._set_status_sync("T01 · starting · Implement auth")
            await pilot.pause()
            assert app.sub_title == "T01 · starting · Implement auth"

    @pytest.mark.asyncio
    async def test_set_status_empty_clears_sub_title(self) -> None:
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._set_status_sync("phase X")
            await pilot.pause()
            app._set_status_sync("")
            await pilot.pause()
            assert app.sub_title == ""
