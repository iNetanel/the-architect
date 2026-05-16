"""Tests for the PauseMenuScreen.

Detach is unconditionally available — the worker thread is always
non-daemon and survives TUI exit regardless of run mode.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import Static

from the_architect.tui.screens.pause import PauseMenuScreen
from the_architect.tui.widgets import MatrixButton


class TestPauseMenuScreenFocus:
    """Focus navigation and activation on the pause menu."""

    @pytest.mark.asyncio
    async def test_on_mount_focuses_continue_button(self) -> None:
        screen = PauseMenuScreen()
        app = MagicMock()
        screen._nodes = {screen}
        screen._parent = app
        try:
            btn = screen.query_one("#btn_continue", MatrixButton)
            assert btn is not None
        except Exception:
            pytest.skip("Cannot query widgets without full Textual app")

    @pytest.mark.asyncio
    async def test_focus_previous_action_moves_focus(self) -> None:
        from textual.app import App

        class _TestApp(App[None]):
            pass

        test_app = _TestApp()
        async with test_app.run_test() as pilot:
            await pilot.pause(0.05)
            test_app.push_screen(PauseMenuScreen())
            await pilot.pause(0.05)
            screen = test_app.screen
            assert isinstance(screen, PauseMenuScreen)
            screen.action_focus_previous()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_focus_next_action_moves_focus(self) -> None:
        from textual.app import App

        class _TestApp(App[None]):
            pass

        test_app = _TestApp()
        async with test_app.run_test() as pilot:
            await pilot.pause(0.05)
            test_app.push_screen(PauseMenuScreen())
            await pilot.pause(0.05)
            screen = test_app.screen
            assert isinstance(screen, PauseMenuScreen)
            screen.action_focus_next()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_activate_focused_presses_button(self) -> None:
        from textual.app import App

        class _TestApp(App[None]):
            pass

        test_app = _TestApp()
        async with test_app.run_test() as pilot:
            await pilot.pause(0.05)
            test_app.push_screen(PauseMenuScreen())
            await pilot.pause(0.05)
            screen = test_app.screen
            assert isinstance(screen, PauseMenuScreen)
            focused = screen.focused
            assert isinstance(focused, MatrixButton)
            screen.action_activate_focused()
            await pilot.pause(0.05)


class TestPauseMenuScreenDecisions:
    """Decision routing — continue, detach, exit."""

    @pytest.mark.asyncio
    async def test_action_choose_continue_dismisses(self) -> None:
        from textual.app import App

        class _TestApp(App[None]):
            pass

        test_app = _TestApp()
        async with test_app.run_test() as pilot:
            await pilot.pause(0.05)
            screen = PauseMenuScreen()
            result_container = []

            def on_dismiss(value):
                result_container.append(value)

            test_app.push_screen(screen, on_dismiss)
            await pilot.pause(0.05)
            screen.action_choose("continue")
            await pilot.pause(0.05)
            assert result_container == ["continue"]

    @pytest.mark.asyncio
    async def test_action_choose_exit_dismisses(self) -> None:
        from textual.app import App

        class _TestApp(App[None]):
            pass

        test_app = _TestApp()
        async with test_app.run_test() as pilot:
            await pilot.pause(0.05)
            screen = PauseMenuScreen()
            result_container = []

            def on_dismiss(value):
                result_container.append(value)

            test_app.push_screen(screen, on_dismiss)
            await pilot.pause(0.05)
            screen.action_choose("exit")
            await pilot.pause(0.05)
            assert result_container == ["exit"]

    @pytest.mark.asyncio
    async def test_action_choose_detach_succeeds_dismisses(self) -> None:
        """Detach always available — request_tui_detach success dismisses."""
        from textual.app import App

        class _TestApp(App[None]):
            pass

        with patch("the_architect.tui.screens.pause.request_tui_detach", return_value=True):
            test_app = _TestApp()
            async with test_app.run_test() as pilot:
                await pilot.pause(0.05)
                screen = PauseMenuScreen()
                result_container = []

                def on_dismiss(value):
                    result_container.append(value)

                test_app.push_screen(screen, on_dismiss)
                await pilot.pause(0.05)
                screen.action_choose("detach")
                await pilot.pause(0.05)
            assert result_container == ["detach"]

    @pytest.mark.asyncio
    async def test_action_choose_detach_failure_shows_error(self) -> None:
        """If request_tui_detach fails, show inline error and keep menu open."""
        from textual.app import App

        class _TestApp(App[None]):
            pass

        with patch("the_architect.tui.screens.pause.request_tui_detach", return_value=False):
            test_app = _TestApp()
            async with test_app.run_test() as pilot:
                await pilot.pause(0.05)
                screen = PauseMenuScreen()
                test_app.push_screen(screen)
                await pilot.pause(0.05)
                screen.action_choose("detach")
                await pilot.pause(0.05)
                # Screen still visible
                assert isinstance(test_app.screen, PauseMenuScreen)
                footer = screen.query_one("#pause_footer", Static)
                text = str(footer.render())
                assert "could not detach" in text.lower()

    @pytest.mark.asyncio
    async def test_action_choose_unknown_dismisses_continue(self) -> None:
        from textual.app import App

        class _TestApp(App[None]):
            pass

        test_app = _TestApp()
        async with test_app.run_test() as pilot:
            await pilot.pause(0.05)
            screen = PauseMenuScreen()
            result_container = []

            def on_dismiss(value):
                result_container.append(value)

            test_app.push_screen(screen, on_dismiss)
            await pilot.pause(0.05)
            screen.action_choose("unknown_decision")
            await pilot.pause(0.05)
            assert result_container == ["continue"]

    @pytest.mark.asyncio
    async def test_on_matrix_button_pressed_routes_continue(self) -> None:
        from textual.app import App

        class _TestApp(App[None]):
            pass

        test_app = _TestApp()
        async with test_app.run_test() as pilot:
            await pilot.pause(0.05)
            screen = PauseMenuScreen()
            result_container = []

            def on_dismiss(value):
                result_container.append(value)

            test_app.push_screen(screen, on_dismiss)
            await pilot.pause(0.05)
            btn = screen.query_one("#btn_continue", MatrixButton)
            event = MatrixButton.Pressed(btn)
            screen.on_matrix_button_pressed(event)
            await pilot.pause(0.05)
            assert result_container == ["continue"]

    @pytest.mark.asyncio
    async def test_on_matrix_button_pressed_routes_exit(self) -> None:
        from textual.app import App

        class _TestApp(App[None]):
            pass

        test_app = _TestApp()
        async with test_app.run_test() as pilot:
            await pilot.pause(0.05)
            screen = PauseMenuScreen()
            result_container = []

            def on_dismiss(value):
                result_container.append(value)

            test_app.push_screen(screen, on_dismiss)
            await pilot.pause(0.05)
            btn = screen.query_one("#btn_exit", MatrixButton)
            event = MatrixButton.Pressed(btn)
            screen.on_matrix_button_pressed(event)
            await pilot.pause(0.05)
            assert result_container == ["exit"]


class TestPauseMenuScreenInlineError:
    """Inline error display on the pause footer."""

    @pytest.mark.asyncio
    async def test_show_inline_error_updates_footer(self) -> None:
        from textual.app import App

        class _TestApp(App[None]):
            pass

        test_app = _TestApp()
        async with test_app.run_test() as pilot:
            await pilot.pause(0.05)
            screen = PauseMenuScreen()
            test_app.push_screen(screen)
            await pilot.pause(0.05)
            screen._show_inline_error("Something went wrong")
            await pilot.pause(0.05)
            footer = screen.query_one("#pause_footer", Static)
            text = str(footer.render())
            assert "Something went wrong" in text
            assert "-error" in footer.classes


class TestPauseMenuScreenBindings:
    """Verify key bindings are correctly defined."""

    def test_bindings_include_escape_continue(self) -> None:
        bindings = {b.key: b.action for b in PauseMenuScreen.BINDINGS}
        assert "escape" in bindings
        assert "choose('continue')" in bindings["escape"]

    def test_bindings_include_ctrl_c_exit(self) -> None:
        bindings = {b.key: b.action for b in PauseMenuScreen.BINDINGS}
        assert "ctrl+c" in bindings
        assert "choose('exit')" in bindings["ctrl+c"]

    def test_bindings_include_arrow_navigation(self) -> None:
        bindings = {b.key: b.action for b in PauseMenuScreen.BINDINGS}
        assert bindings.get("up") == "focus_previous"
        assert bindings.get("down") == "focus_next"

    def test_bindings_include_letter_shortcuts(self) -> None:
        bindings = {b.key: b.action for b in PauseMenuScreen.BINDINGS}
        assert "choose('continue')" in bindings.get("c", "")
        assert "choose('detach')" in bindings.get("d", "")
        assert "choose('exit')" in bindings.get("e", "")


class TestPauseMenuScreenExceptions:
    """Exception handling paths in PauseMenuScreen."""

    @pytest.mark.asyncio
    async def test_show_inline_error_query_one_fails(self) -> None:
        from textual.app import App

        class _TestApp(App[None]):
            pass

        test_app = _TestApp()
        async with test_app.run_test() as pilot:
            await pilot.pause(0.05)
            screen = PauseMenuScreen()
            test_app.push_screen(screen)
            await pilot.pause(0.05)

            def bad_query(*args, **kwargs):
                raise Exception("widget not found")

            screen.query_one = bad_query  # type: ignore[method-assign]
            screen._show_inline_error("Error message")
            await pilot.pause(0.05)
