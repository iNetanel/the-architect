"""Tests for the ESC pause menu overlay."""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest
from textual.widgets import Static

from the_architect.tui.app import ArchitectApp, SplashScreen
from the_architect.tui.screens.execution import ExecutionScreen
from the_architect.tui.screens.pause import PauseMenuScreen


class TestPauseMenuScreenDecision:
    """The overlay must dismiss with one of three decisions."""

    @pytest.mark.asyncio
    async def test_continue_dismisses_with_continue(self) -> None:
        app = _HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.push_screen(PauseMenuScreen(), app._on_dismiss)
            await pilot.pause(0.05)
            await pilot.press("c")
            await pilot.pause(0.05)
        assert app.decision == "continue"

    @pytest.mark.asyncio
    async def test_escape_dismisses_as_continue(self) -> None:
        app = _HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.push_screen(PauseMenuScreen(), app._on_dismiss)
            await pilot.pause(0.05)
            await pilot.press("escape")
            await pilot.pause(0.05)
        assert app.decision == "continue"

    @pytest.mark.asyncio
    async def test_exit_key_dismisses_with_exit(self) -> None:
        app = _HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.push_screen(PauseMenuScreen(), app._on_dismiss)
            await pilot.pause(0.05)
            await pilot.press("e")
            await pilot.pause(0.05)
        assert app.decision == "exit"

    @pytest.mark.asyncio
    async def test_ctrl_c_inside_menu_dismisses_with_exit(self) -> None:
        app = _HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.push_screen(PauseMenuScreen(), app._on_dismiss)
            await pilot.pause(0.05)
            await pilot.press("ctrl+c")
            await pilot.pause(0.05)
        assert app.decision == "exit"


class TestPauseMenuDetachBehaviour:
    """Detach is always available — the worker is always non-daemon.

    The only failure mode is request_tui_detach() returning False
    (no active runner), which shows an inline error.
    """

    @pytest.mark.asyncio
    async def test_detach_success_dismisses_with_detach(self) -> None:
        """Successful detach request dismisses with 'detach'."""
        with patch(
            "the_architect.tui.screens.pause.request_tui_detach", return_value=True
        ) as mock_detach:
            app = _HarnessApp()
            async with app.run_test() as pilot:
                await pilot.pause(0.05)
                app.push_screen(PauseMenuScreen(), app._on_dismiss)
                await pilot.pause(0.05)
                await pilot.press("d")
                await pilot.pause(0.05)
        mock_detach.assert_called_once()
        assert app.decision == "detach"

    @pytest.mark.asyncio
    async def test_detach_failure_keeps_menu_open(self) -> None:
        """If request_tui_detach returns False, keep menu open with error."""
        with patch("the_architect.tui.screens.pause.request_tui_detach", return_value=False):
            app = _HarnessApp()
            async with app.run_test() as pilot:
                await pilot.pause(0.05)
                app.push_screen(PauseMenuScreen(), app._on_dismiss)
                await pilot.pause(0.05)
                await pilot.press("d")
                await pilot.pause(0.05)
        # No dismiss happened
        assert app.decision is None


class TestExecutionScreenEscapeOpensPauseMenu:
    """ESC during execution must open the overlay, NOT quit the app."""

    @pytest.mark.asyncio
    async def test_escape_on_execution_pushes_pause_menu(self) -> None:
        app = ArchitectApp(initial_screen=ExecutionScreen())
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            await pilot.press("escape")
            await pilot.pause(0.05)
            assert isinstance(app.screen, PauseMenuScreen)

    @pytest.mark.asyncio
    async def test_confirmed_exit_shows_shutdown_splash_during_cleanup(self) -> None:
        """Confirmed pause-menu exit must not drop to a blank TUI during cleanup."""
        cleanup_can_finish = threading.Event()

        def _block_cleanup() -> None:
            cleanup_can_finish.wait(timeout=5)

        with patch(
            "the_architect.core.runner.kill_active_subprocesses", side_effect=_block_cleanup
        ):
            app = ArchitectApp(initial_screen=ExecutionScreen())
            async with app.run_test() as pilot:
                await pilot.pause(0.05)
                await pilot.press("escape")
                await pilot.pause(0.05)
                await pilot.press("e")
                await pilot.pause(0.05)

                assert isinstance(app.screen, SplashScreen)
                subtitle = app.screen.query_one("#splash_subtitle", Static)
                assert "Shutting down" in str(subtitle.render())

                cleanup_can_finish.set()


class TestPauseMenuArrowNavigation:
    """Arrow keys must move focus between the pause-menu buttons."""

    @pytest.mark.asyncio
    async def test_down_moves_focus_to_next_button(self) -> None:
        app = _HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.push_screen(PauseMenuScreen(), app._on_dismiss)
            await pilot.pause(0.05)
            first = app.focused
            assert first is not None
            assert first.id == "btn_continue"

            await pilot.press("down")
            await pilot.pause(0.05)
            assert app.focused is not None
            assert app.focused.id == "btn_detach"

            await pilot.press("down")
            await pilot.pause(0.05)
            assert app.focused is not None
            assert app.focused.id == "btn_exit"

            await pilot.press("up")
            await pilot.pause(0.05)
            assert app.focused is not None
            assert app.focused.id == "btn_detach"

            await pilot.press("up")
            await pilot.pause(0.05)
            assert app.focused is not None
            assert app.focused.id == "btn_continue"

    @pytest.mark.asyncio
    async def test_enter_on_focused_exit_button_dismisses_with_exit(self) -> None:
        app = _HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.push_screen(PauseMenuScreen(), app._on_dismiss)
            await pilot.pause(0.05)
            await pilot.press("down")  # Continue → Detach
            await pilot.press("down")  # Detach → Exit
            await pilot.pause(0.05)
            await pilot.press("enter")
            await pilot.pause(0.05)
        assert app.decision == "exit"

    @pytest.mark.asyncio
    async def test_detach_failure_shows_inline_error(self) -> None:
        """Pressing D when request_tui_detach fails must show inline error."""
        with patch("the_architect.tui.screens.pause.request_tui_detach", return_value=False):
            app = _HarnessApp()
            async with app.run_test() as pilot:
                await pilot.pause(0.05)
                menu = PauseMenuScreen()
                app.push_screen(menu, app._on_dismiss)
                await pilot.pause(0.05)
                await pilot.press("d")
                await pilot.pause(0.05)
                assert app.decision is None
                footer = menu.query_one("#pause_footer", Static)
                assert "-error" in footer.classes


class _HarnessApp(ArchitectApp):
    """Test harness that records the pause-menu dismissal value."""

    def __init__(self) -> None:
        super().__init__()
        self.decision: str | None = None

    def _on_dismiss(self, value: str | None) -> None:
        self.decision = value
