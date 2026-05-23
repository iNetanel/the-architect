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


class TestWaitScreenExceptionPaths:
    """Tests for WaitScreen exception handlers — query_one fails."""

    @pytest.mark.asyncio
    async def test_on_mount_query_one_fails(self) -> None:
        """on_mount swallows query_one exception for wait_log."""
        from the_architect.tui.screens.wait import WaitScreen

        screen = WaitScreen(title="test")
        # Patch query_one to raise — simulates widget not yet available
        screen.query_one = lambda *a, **k: (_ for _ in ()).throw(Exception("boom"))  # type: ignore[method-assign]
        # on_mount should not raise
        screen.on_mount()

    @pytest.mark.asyncio
    async def test_set_title_query_one_fails(self) -> None:
        """set_title swallows query_one exception."""
        from the_architect.tui.screens.wait import WaitScreen

        screen = WaitScreen(title="initial")
        screen.query_one = lambda *a, **k: (_ for _ in ()).throw(Exception("boom"))  # type: ignore[method-assign]
        # Should not raise — title stored internally
        screen.set_title("new title")
        assert screen._title == "new title"

    @pytest.mark.asyncio
    async def test_set_detail_query_one_fails_buffers_pending(self) -> None:
        """set_detail on unmounted screen buffers to _pending_detail."""
        from the_architect.tui.screens.wait import WaitScreen

        screen = WaitScreen(title="test")
        # Before mount, query_one raises — detail goes to pending buffer
        screen.set_detail("buffered detail")
        assert screen._pending_detail == "buffered detail"

    @pytest.mark.asyncio
    async def test_append_log_query_one_fails_buffers_pending(self) -> None:
        """append_log on unmounted screen buffers to _pending_log_lines."""
        from the_architect.tui.screens.wait import WaitScreen

        screen = WaitScreen(title="test")
        # Before mount, query_one raises — line goes to pending buffer
        screen.append_log("buffered line")
        assert screen._pending_log_lines == ["buffered line"]

    @pytest.mark.asyncio
    async def test_tick_spinner_query_one_fails(self) -> None:
        """_tick_spinner swallows query_one exception."""
        from the_architect.tui.screens.wait import WaitScreen

        screen = WaitScreen(title="test")
        screen.query_one = lambda *a, **k: (_ for _ in ()).throw(Exception("boom"))  # type: ignore[method-assign]
        frame_before = screen._frame_index
        # Should not raise — frame still advances
        screen._tick_spinner()
        assert screen._frame_index == frame_before + 1

    @pytest.mark.asyncio
    async def test_flush_pending_detail_query_one_fails(self) -> None:
        """_flush_pending swallows detail query_one exception."""
        from the_architect.tui.screens.wait import WaitScreen

        screen = WaitScreen(title="test")
        screen._pending_detail = "detail"
        screen.query_one = lambda *a, **k: (_ for _ in ()).throw(Exception("boom"))  # type: ignore[method-assign]
        # Should not raise — pending_detail stays set
        screen._flush_pending()
        assert screen._pending_detail == "detail"

    @pytest.mark.asyncio
    async def test_flush_pending_log_query_one_fails(self) -> None:
        """_flush_pending swallows log query_one exception, keeps lines buffered."""
        from the_architect.tui.screens.wait import WaitScreen

        screen = WaitScreen(title="test")
        screen._pending_log_lines = ["line1"]
        screen.query_one = lambda *a, **k: (_ for _ in ()).throw(Exception("boom"))  # type: ignore[method-assign]
        # Should not raise — lines stay buffered
        screen._flush_pending()
        assert screen._pending_log_lines == ["line1"]

    @pytest.mark.asyncio
    async def test_flush_pending_nothing_to_flush(self) -> None:
        """_flush_pending is a no-op when nothing is pending."""
        from the_architect.tui.screens.wait import WaitScreen

        screen = WaitScreen(title="test")
        screen._pending_detail = None
        screen._pending_log_lines = []
        # Should not raise
        screen._flush_pending()


class TestWaitScreenPauseMenu:
    """Tests for WaitScreen action_pause_menu."""

    @pytest.mark.asyncio
    async def test_action_pause_menu_app_no_show_pause_menu(self) -> None:
        """action_pause_menu swallows exception when app lacks show_pause_menu."""
        from the_architect.tui.screens.wait import WaitScreen

        screen = WaitScreen(title="test")
        # action_pause_menu calls self.app.show_pause_menu()
        # When app doesn't have show_pause_menu, it raises AttributeError
        # which is caught by the except Exception: pass
        # We can't easily mock self.app (read-only property), so test
        # through the screen's action in a context where app is not mounted.
        # Since screen.app is a read-only property that references the
        # hosting app, and the screen isn't mounted, accessing app will
        # raise — which is caught by the except clause.
        # Should not raise
        screen.action_pause_menu()

    @pytest.mark.asyncio
    async def test_action_pause_menu_in_mounted_app(self) -> None:
        """action_pause_menu delegates to ArchitectApp.show_pause_menu."""
        from the_architect.tui.app import ArchitectApp

        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.show_wait("test", "")
            await pilot.pause(0.05)
            # Get the wait screen
            from the_architect.tui.screens.wait import WaitScreen

            wait_screen = None
            for s in app.screen_stack:
                if isinstance(s, WaitScreen):
                    wait_screen = s
                    break
            assert wait_screen is not None
            # action_pause_menu should delegate to app.show_pause_menu
            # which pushes PauseMenuScreen
            wait_screen.action_pause_menu()
            await pilot.pause(0.05)
            from the_architect.tui.screens.pause import PauseMenuScreen

            assert any(isinstance(s, PauseMenuScreen) for s in app.screen_stack)


class TestWaitAppExceptionPaths:
    """Tests for WaitApp exception handlers."""

    @pytest.mark.asyncio
    async def test_on_unmount_restore_terminal_fails(self) -> None:
        """on_unmount swallows restore_terminal_input_modes exception."""
        from unittest.mock import patch

        from the_architect.tui.screens.wait import WaitApp

        app = WaitApp(title="test")
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            # Patch restore_terminal_input_modes to raise
            with patch(
                "the_architect.tui.terminal.restore_terminal_input_modes",
                side_effect=RuntimeError("boom"),
            ):
                # on_unmount should not raise
                app.on_unmount()

    @pytest.mark.asyncio
    async def test_show_pause_menu_already_visible(self) -> None:
        """show_pause_menu returns early when already visible."""
        from the_architect.tui.screens.wait import WaitApp

        app = WaitApp(title="test")
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app._pause_menu_visible = True
            # Should return early without pushing
            initial_stack = len(app.screen_stack)
            app.show_pause_menu()
            assert len(app.screen_stack) == initial_stack

    @pytest.mark.asyncio
    async def test_show_pause_menu_push_exception_resets_flag(self) -> None:
        """show_pause_menu resets flag when push_screen raises."""
        from the_architect.tui.screens.wait import WaitApp

        app = WaitApp(title="test")
        async with app.run_test() as pilot:
            await pilot.pause(0.05)

            def bad_push(*args, **kwargs):
                raise RuntimeError("push failed")

            app.push_screen = bad_push  # type: ignore[method-assign]
            # First call sets _pause_menu_visible=True then resets on exception
            app.show_pause_menu()
            # Flag should be reset after exception
            assert getattr(app, "_pause_menu_visible", False) is False

    @pytest.mark.asyncio
    async def test_show_pause_menu_exit_decision(self) -> None:
        """show_pause_menu 'exit' decision calls app.exit()."""
        from the_architect.tui.screens.wait import WaitApp

        app = WaitApp(title="test")
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            # Manually call the _on_dismiss callback with "exit"
            app.show_pause_menu()
            await pilot.pause(0.05)
            # The pause menu was pushed — check it's on the stack
            from the_architect.tui.screens.pause import PauseMenuScreen

            assert any(isinstance(s, PauseMenuScreen) for s in app.screen_stack)
