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
        """ESC inside the menu closes it rather than cascading into
        another pause menu — the user already saw the options."""
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
        """Ctrl+C inside the menu should still mean hard-stop."""
        app = _HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.push_screen(PauseMenuScreen(), app._on_dismiss)
            await pilot.pause(0.05)
            await pilot.press("ctrl+c")
            await pilot.pause(0.05)
        assert app.decision == "exit"


class TestPauseMenuDetachBehaviour:
    """Detach policy:

    * Outside tmux → inline error, menu stays open.
    * Inside tmux + detach succeeds → dismiss with "detach".
    * Inside tmux + detach command fails → inline error, menu stays open.

    The previous design silently dismissed both failure modes as
    "continue", which left the user thinking their click did nothing.
    """

    @pytest.mark.asyncio
    async def test_detach_outside_tmux_keeps_menu_open(self) -> None:
        with patch("the_architect.tui.screens.pause.is_inside_tmux", return_value=False):
            app = _HarnessApp()
            async with app.run_test() as pilot:
                await pilot.pause(0.05)
                menu = PauseMenuScreen()
                app.push_screen(menu, app._on_dismiss)
                await pilot.pause(0.05)
                await pilot.press("d")
                await pilot.pause(0.05)
        # No dismiss happened — decision is still None.
        assert app.decision is None

    @pytest.mark.asyncio
    async def test_detach_inside_tmux_calls_detach_client(self) -> None:
        with (
            patch("the_architect.tui.screens.pause.is_inside_tmux", return_value=True),
            patch(
                "the_architect.tui.screens.pause._tmux_detach_client",
                return_value=True,
            ) as mock_detach,
        ):
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
        """If tmux detach-client fails, keep the menu open and show an
        inline error rather than silently dismissing — the user needs
        to know their action didn't take effect so they can pick a
        different one.
        """
        with (
            patch("the_architect.tui.screens.pause.is_inside_tmux", return_value=True),
            patch(
                "the_architect.tui.screens.pause._tmux_detach_client",
                return_value=False,
            ),
        ):
            app = _HarnessApp()
            async with app.run_test() as pilot:
                await pilot.pause(0.05)
                app.push_screen(PauseMenuScreen(), app._on_dismiss)
                await pilot.pause(0.05)
                await pilot.press("d")
                await pilot.pause(0.05)
        assert app.decision is None


class TestExecutionScreenEscapeOpensPauseMenu:
    """The whole reason the menu exists: ESC during execution must
    open the overlay, NOT quit the app."""

    @pytest.mark.asyncio
    async def test_escape_on_execution_pushes_pause_menu(self) -> None:
        app = ArchitectApp(initial_screen=ExecutionScreen())
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            await pilot.press("escape")
            await pilot.pause(0.05)
            # The active screen on top of the stack should now be the
            # pause menu overlay.
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
    """Arrow keys must move focus between the pause-menu buttons,
    matching the navigation feel of every other form screen
    (:class:`ModeSelectionScreen`, :class:`ResumeScreen`). Regression
    guard: ``Binding("up", "focus_previous", …)`` silently does
    nothing without an ``action_focus_previous`` shim on the screen.
    """

    @pytest.mark.asyncio
    async def test_down_moves_focus_to_next_button(self) -> None:
        """Down/Up walk the button list in DOM order. Detach is
        always enabled (the inside-tmux check runs at press-time
        now), so the focus ring visits all three rows.
        """
        app = _HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.push_screen(PauseMenuScreen(), app._on_dismiss)
            await pilot.pause(0.05)
            # Focus starts on Continue (wired in on_mount).
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

            # And Up walks back Exit → Detach → Continue.
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
        """Enter presses whichever button has focus. Combined with the
        arrow navigation above, that's the full keyboard-only flow.
        """
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
    async def test_detach_outside_tmux_shows_inline_error_not_dismiss(self) -> None:
        """Pressing Detach outside a tmux session must NOT dismiss the
        menu. Silently falling through to "continue" (the old
        behaviour) was the confusing symptom the user reported —
        they clicked Detach and nothing visible happened. We now
        keep the menu open and surface an inline footer message.
        """
        from unittest.mock import patch

        from the_architect.tui.screens.pause import PauseMenuScreen as _PMS

        with patch("the_architect.tui.screens.pause.is_inside_tmux", return_value=False):
            app = _HarnessApp()
            async with app.run_test() as pilot:
                await pilot.pause(0.05)
                menu = _PMS()
                app.push_screen(menu, app._on_dismiss)
                await pilot.pause(0.05)
                await pilot.press("d")
                await pilot.pause(0.05)
                # Menu is still on top of the stack — decision is None
                # because dismiss was never called.
                assert app.decision is None
                # And the footer was repainted with an error class so
                # the user sees an explanation.
                from textual.widgets import Static

                footer = menu.query_one("#pause_footer", Static)
                assert "tmux" in str(footer.render()).lower()
                assert "-error" in footer.classes


def _tmux_detach_stub_success() -> bool:
    return True


class _HarnessApp(ArchitectApp):
    """Test harness that records the pause-menu dismissal value."""

    def __init__(self) -> None:
        super().__init__()
        self.decision: str | None = None

    def _on_dismiss(self, value: str | None) -> None:
        self.decision = value
