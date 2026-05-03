"""Tests for Phase 5 wait screen and wait-session helpers."""

from __future__ import annotations

import pytest
from textual.widgets import RichLog, Static

from the_architect.tui import TuiWaitSession, tui_wait_session
from the_architect.tui.screens.wait import WaitApp


class TestWaitApp:
    @pytest.mark.asyncio
    async def test_initial_title_rendered(self) -> None:
        app = WaitApp(title="planning")
        async with app.run_test() as pilot:
            await pilot.pause()
            title = app._screen.query_one("#wait_title", Static)
            assert "planning" in str(title.render())

    @pytest.mark.asyncio
    async def test_set_title_updates_static(self) -> None:
        app = WaitApp(title="initial")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.set_title("updated")
            await pilot.pause()
            title = app._screen.query_one("#wait_title", Static)
            assert "updated" in str(title.render())

    @pytest.mark.asyncio
    async def test_set_detail_updates_static(self) -> None:
        app = WaitApp(title="wait")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.set_detail("Goal: demo\nScope: standard")
            await pilot.pause()
            detail = app._screen.query_one("#wait_detail", Static)
            rendered = str(detail.render())
            assert "Goal: demo" in rendered
            assert "standard" in rendered

    @pytest.mark.asyncio
    async def test_append_log_writes_line(self) -> None:
        app = WaitApp(title="wait")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.append_log("first line")
            app.append_log("second line")
            await pilot.pause()
            log = app._screen.query_one("#wait_log", RichLog)
            assert len(log.lines) >= 2

    @pytest.mark.asyncio
    async def test_spinner_advances_frame(self) -> None:
        app = WaitApp(title="spin")
        async with app.run_test() as pilot:
            await pilot.pause()
            first_frame = app._current_frame
            # Force one extra tick directly (avoids depending on interval timing).
            app._tick_spinner()
            await pilot.pause()
            assert app._current_frame != first_frame


class TestTuiWaitSession:
    def test_disabled_yields_noop_session(self) -> None:
        with tui_wait_session(enabled=False, title="anything") as session:
            assert isinstance(session, TuiWaitSession)
            assert session.app is None

    def test_noop_methods_dont_raise_when_disabled(self) -> None:
        with tui_wait_session(enabled=False, title="anything") as session:
            session.set_title("x")
            session.set_detail("y")
            session.append_log("z")


class TestArchitectAppWaitOverlay:
    """Phase 7 — wait screens push onto the running ArchitectApp."""

    @pytest.mark.asyncio
    async def test_show_wait_pushes_overlay(self) -> None:
        from the_architect.tui.app import ArchitectApp
        from the_architect.tui.screens.wait import WaitScreen

        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._show_wait_sync("planning", "goal: demo")
            await pilot.pause()
            assert app._wait_screen is not None
            assert isinstance(app.screen, WaitScreen)

    @pytest.mark.asyncio
    async def test_hide_wait_pops_overlay(self) -> None:
        from the_architect.tui.app import ArchitectApp, SplashScreen

        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._show_wait_sync("wait", "")
            await pilot.pause()
            app._hide_wait_sync()
            await pilot.pause()
            assert app._wait_screen is None
            # After dismissing the wait overlay we return to whatever
            # screen was active before — the splash, in this test.
            assert isinstance(app.screen, SplashScreen)

    @pytest.mark.asyncio
    async def test_update_wait_updates_title_and_detail(self) -> None:
        from the_architect.tui.app import ArchitectApp

        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._show_wait_sync("phase A", "")
            await pilot.pause()
            app._update_wait_sync("phase B", "new detail")
            await pilot.pause()
            assert app._wait_screen is not None
            title = app._wait_screen.query_one("#wait_title", Static)
            detail = app._wait_screen.query_one("#wait_detail", Static)
            assert "phase B" in str(title.render())
            assert "new detail" in str(detail.render())
