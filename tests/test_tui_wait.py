"""Tests for Phase 5 wait screen and wait-session helpers."""

from __future__ import annotations

import pytest
from textual.widgets import RichLog, Static

from the_architect.tui import TuiWaitSession, tui_wait_session
from the_architect.tui.screens.wait import WaitApp
from the_architect.tui.widgets import MatrixRain


class TestWaitApp:
    @pytest.mark.asyncio
    async def test_initial_title_rendered(self) -> None:
        app = WaitApp(title="planning")
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            title = app._screen.query_one("#wait_title", Static)
            assert "planning" in str(title.render())
            rain = app._screen.query_one("#wait_rain", MatrixRain)
            assert rain.region.width == MatrixRain.COLS
            assert rain.region.height == MatrixRain.ROWS
            assert any(ch not in {" ", "\n"} for ch in rain.render().plain)

    @pytest.mark.asyncio
    async def test_set_title_updates_static(self) -> None:
        app = WaitApp(title="initial")
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.set_title("updated")
            await pilot.pause(0.05)
            title = app._screen.query_one("#wait_title", Static)
            assert "updated" in str(title.render())

    @pytest.mark.asyncio
    async def test_set_detail_updates_static(self) -> None:
        app = WaitApp(title="wait")
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.set_detail("Goal: demo\nScope: standard")
            await pilot.pause(0.05)
            detail = app._screen.query_one("#wait_detail", Static)
            rendered = str(detail.render())
            assert "Goal: demo" in rendered
            assert "standard" in rendered

    @pytest.mark.asyncio
    async def test_append_log_writes_line(self) -> None:
        app = WaitApp(title="wait")
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.append_log("first line")
            app.append_log("second line")
            await pilot.pause(0.05)
            log = app._screen.query_one("#wait_log", RichLog)
            assert len(log.lines) >= 2

    @pytest.mark.asyncio
    async def test_early_detail_and_log_are_flushed_after_mount(self) -> None:
        app = WaitApp(title="wait")

        # Simulate planning output arriving before the wait screen is mounted.
        app.set_detail("Goal: demo\nScope: standard")
        app.append_log("first provider line")
        app.append_log("second provider line")

        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            await pilot.pause(0.05)
            detail = app._screen.query_one("#wait_detail", Static)
            rendered = str(detail.render())
            assert "Goal: demo" in rendered

            log = app._screen.query_one("#wait_log", RichLog)
            assert len(log.lines) >= 2

    @pytest.mark.asyncio
    async def test_spinner_advances_frame(self) -> None:
        app = WaitApp(title="spin")
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            first_frame = app._current_frame
            # Force one extra tick directly (avoids depending on interval timing).
            app._tick_spinner()
            await pilot.pause(0.05)
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
            await pilot.pause(0.05)
            app._show_wait_sync("planning", "goal: demo")
            await pilot.pause(0.05)
            assert app._wait_screen is not None
            assert isinstance(app.screen, WaitScreen)

    @pytest.mark.asyncio
    async def test_hide_wait_returns_to_execution_without_emptying_stack(self) -> None:
        from the_architect.tui.app import ArchitectApp
        from the_architect.tui.screens.execution import ExecutionScreen

        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app._show_wait_sync("wait", "")
            await pilot.pause(0.05)
            app._hide_wait_sync()
            await pilot.pause(0.05)
            assert app._wait_screen is None
            # Hide must not pop the final screen off the stack. Infinite Loop
            # uses wait overlays between planning iterations; if the wait
            # screen is the active screen, return to execution instead of
            # letting the TUI app exit before newly planned tasks run.
            assert isinstance(app.screen, ExecutionScreen)

    @pytest.mark.asyncio
    async def test_update_wait_updates_title_and_detail(self) -> None:
        from the_architect.tui.app import ArchitectApp

        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app._show_wait_sync("phase A", "")
            await pilot.pause(0.05)
            app._update_wait_sync("phase B", "new detail")
            await pilot.pause(0.05)
            assert app._wait_screen is not None
            title = app._wait_screen.query_one("#wait_title", Static)
            detail = app._wait_screen.query_one("#wait_detail", Static)
            assert "phase B" in str(title.render())
            assert "new detail" in str(detail.render())

    @pytest.mark.asyncio
    async def test_execution_output_does_not_replace_visible_wait_overlay(self) -> None:
        from the_architect.tui.app import ArchitectApp
        from the_architect.tui.screens.wait import WaitScreen

        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app._show_wait_sync("planning", "goal: demo")
            await pilot.pause(0.05)
            assert isinstance(app.screen, WaitScreen)

            # Provider output may arrive while the wait overlay is visible.
            # That must not switch the visible screen away from the overlay.
            app.push_output_line("provider line")
            await pilot.pause(0.05)

            assert isinstance(app.screen, WaitScreen)
            assert app._wait_screen is not None

    @pytest.mark.asyncio
    async def test_overlay_wait_flushes_early_log_after_show(self) -> None:
        from the_architect.tui.app import ArchitectApp

        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app._show_wait_sync("planning", "")
            assert app._wait_screen is not None

            # Simulate stream lines landing before the wait screen has mounted.
            app._wait_screen.append_log("provider line before mount")
            app._wait_screen.set_detail("Goal: demo")

            await pilot.pause(0.05)
            await pilot.pause(0.05)

            detail = app._wait_screen.query_one("#wait_detail", Static)
            assert "Goal: demo" in str(detail.render())

            log = app._wait_screen.query_one("#wait_log", RichLog)
            assert len(log.lines) >= 1

    @pytest.mark.asyncio
    async def test_hide_wait_does_not_pop_unrelated_top_overlay(self) -> None:
        from textual.screen import Screen

        from the_architect.tui.app import ArchitectApp

        class DummyOverlay(Screen[None]):
            pass

        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app._show_wait_sync("planning", "")
            await pilot.pause(0.05)
            dummy = DummyOverlay()
            app.push_screen(dummy)
            await pilot.pause(0.05)

            assert app.screen is dummy
            app.hide_wait()
            await pilot.pause(0.05)

            # hide_wait should only dismiss the wait overlay when it is
            # actually on top, not pop whatever screen currently happens
            # to be active.
            assert app.screen is dummy
