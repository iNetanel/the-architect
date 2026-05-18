"""Tests for Phase 13/14 pre-run screens (pickers, update, pending).

Phase 16 note: converted to test ``*Screen`` classes inside a harness
app instead of standalone ``App`` subclasses.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from textual.app import App
from textual.widgets import ListView

from the_architect.tui.screens.pre_run import (
    BACK_SENTINEL,
    PendingTasksScreen,
    ProviderOption,
    ProviderSelectionScreen,
    SelfUpdateScreen,
    StringListPickerScreen,
    UpdateActionScreen,
    run_agent_picker,
    run_model_picker,
    run_pending_tasks_screen,
    run_provider_selection,
    run_self_update_screen,
    run_update_action_screen,
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
            await pilot.pause(0.05)
            screen.action_confirm()
            await pilot.pause(0.05)
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
            await pilot.pause(0.05)
            lv = screen.query_one("#picker_list", ListView)
            lv.index = 1
            await pilot.pause(0.05)
            screen.action_confirm()
            await pilot.pause(0.05)
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
            await pilot.pause(0.05)
            screen.action_cancel()
            await pilot.pause(0.05)
        assert harness.dismissed is None


class TestUpdateActionScreen:
    @pytest.mark.asyncio
    async def test_continue_returns_continue(self) -> None:
        screen = UpdateActionScreen(update_msg="outdated", install_hint="pip install -U x")
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            screen.action_confirm()
            await pilot.pause(0.05)
        assert harness.dismissed == "continue"

    @pytest.mark.asyncio
    async def test_exit_returns_exit(self) -> None:
        screen = UpdateActionScreen(update_msg="outdated", install_hint="pip install -U x")
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            screen.action_exit()
            await pilot.pause(0.05)
        assert harness.dismissed == "exit"

    @pytest.mark.asyncio
    async def test_update_returns_update(self) -> None:
        screen = UpdateActionScreen(update_msg="outdated", install_hint="pip install -U x")
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            screen.action_update()
            await pilot.pause(0.05)
        assert harness.dismissed == "update"


class TestPendingTasksScreen:
    @pytest.mark.asyncio
    async def test_confirm_returns_true(self) -> None:
        screen = PendingTasksScreen(pending=["T03_api", "T04_tests"])
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            screen.action_confirm()
            await pilot.pause(0.05)
        assert harness.dismissed is True

    @pytest.mark.asyncio
    async def test_abort_returns_false(self) -> None:
        screen = PendingTasksScreen(pending=["T03_api"])
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            screen.action_abort()
            await pilot.pause(0.05)
        assert harness.dismissed is False


class TestSpinnersSilentInTuiMode:
    """Legacy spinner helpers (_start_live_spinner / _start_wait_spinner) were
    deleted in build 10136 along with every other stdout-ANSI animation. The
    TUI is now the sole UI surface, so there is nothing left to check for
    TUI-silence. Placeholder kept so reviewers see the historical intent
    of the now-deleted tests.
    """


# ── run_* wrapper function tests (mock run_single_screen) ─────────────


class TestRunProviderSelection:
    """Tests for run_provider_selection wrapper function."""

    def test_returns_index_on_success(self) -> None:
        with patch("the_architect.tui.app.run_single_screen", return_value=1) as mock_run:
            options = [_fake_provider("OpenCode"), _fake_provider("Claude Code")]
            result = run_provider_selection(options)
            assert result == 1
            mock_run.assert_called_once()

    def test_returns_back_sentinel_on_back(self) -> None:
        with patch("the_architect.tui.app.run_single_screen", return_value=BACK_SENTINEL):
            options = [_fake_provider("A")]
            result = run_provider_selection(options)
            assert result is BACK_SENTINEL

    def test_raises_system_exit_on_cancel(self) -> None:
        with patch("the_architect.tui.app.run_single_screen", return_value=None):
            options = [_fake_provider("A")]
            with pytest.raises(SystemExit) as exc_info:
                run_provider_selection(options)
            assert exc_info.value.code == 0


class TestRunModelPicker:
    """Tests for run_model_picker wrapper function."""

    def test_returns_selected_model(self) -> None:
        with patch("the_architect.tui.app.run_single_screen", return_value="gpt-4") as mock_run:
            result = run_model_picker(
                provider_name="opencode", models=["gpt-4", "gpt-3.5"], current="gpt-3.5"
            )
            assert result == "gpt-4"
            mock_run.assert_called_once()

    def test_returns_back_sentinel_on_back(self) -> None:
        with patch("the_architect.tui.app.run_single_screen", return_value=BACK_SENTINEL):
            result = run_model_picker(provider_name="opencode", models=["gpt-4"], current="")
            assert result is BACK_SENTINEL

    def test_raises_system_exit_on_cancel(self) -> None:
        with patch("the_architect.tui.app.run_single_screen", return_value=None):
            with pytest.raises(SystemExit) as exc_info:
                run_model_picker(provider_name="opencode", models=["gpt-4"], current="")
            assert exc_info.value.code == 0

    def test_returns_current_model_when_default_selected(self) -> None:
        """When user picks the default option (empty string), return current model."""
        with patch("the_architect.tui.app.run_single_screen", return_value=""):
            result = run_model_picker(provider_name="opencode", models=["gpt-4"], current="gpt-3.5")
            assert result == "gpt-3.5"

    def test_returns_none_when_default_selected_no_current(self) -> None:
        """When user picks default and no current model, return None."""
        with patch("the_architect.tui.app.run_single_screen", return_value=""):
            result = run_model_picker(provider_name="opencode", models=["gpt-4"], current="")
            assert result is None

    def test_reorders_current_model_to_top(self) -> None:
        """Current model should be reordered to top of list."""
        with patch("the_architect.tui.app.run_single_screen", return_value="gpt-3.5"):
            run_model_picker(
                provider_name="opencode", models=["gpt-4", "gpt-3.5"], current="gpt-3.5"
            )
            # Verify the screen was constructed — the screen's choices
            # should have gpt-3.5 first
            mock_run = patch(
                "the_architect.tui.app.run_single_screen", return_value="gpt-3.5"
            ).start()
            run_model_picker(
                provider_name="opencode", models=["gpt-4", "gpt-3.5"], current="gpt-3.5"
            )
            call_args = mock_run.call_args
            screen = call_args[0][0]
            # First choice (after current) should be gpt-3.5
            assert screen._choices[0][0] == "gpt-3.5"
            patch.stopall()

    def test_current_not_in_models_added_to_top(self) -> None:
        """If current model is not in models list, it's prepended."""
        with patch("the_architect.tui.app.run_single_screen", return_value="gpt-3.5"):
            run_model_picker(provider_name="opencode", models=["gpt-4"], current="gpt-3.5")
            mock_run = patch(
                "the_architect.tui.app.run_single_screen", return_value="gpt-3.5"
            ).start()
            run_model_picker(provider_name="opencode", models=["gpt-4"], current="gpt-3.5")
            call_args = mock_run.call_args
            screen = call_args[0][0]
            assert screen._choices[0][0] == "gpt-3.5"
            patch.stopall()


class TestRunAgentPicker:
    """Tests for run_agent_picker wrapper function."""

    def test_returns_selected_agent(self) -> None:
        with patch("the_architect.tui.app.run_single_screen", return_value="master") as mock_run:
            result = run_agent_picker(provider_name="opencode", agents=["master", "backend"])
            assert result == "master"
            mock_run.assert_called_once()

    def test_returns_back_sentinel_on_back(self) -> None:
        with patch("the_architect.tui.app.run_single_screen", return_value=BACK_SENTINEL):
            result = run_agent_picker(provider_name="opencode", agents=["master"])
            assert result is BACK_SENTINEL

    def test_raises_system_exit_on_cancel(self) -> None:
        with patch("the_architect.tui.app.run_single_screen", return_value=None):
            with pytest.raises(SystemExit) as exc_info:
                run_agent_picker(provider_name="opencode", agents=["master"])
            assert exc_info.value.code == 0

    def test_returns_empty_string_for_default(self) -> None:
        """When user picks the default option, return empty string."""
        with patch("the_architect.tui.app.run_single_screen", return_value=""):
            result = run_agent_picker(provider_name="opencode", agents=["master"])
            assert result == ""


class TestRunUpdateActionScreen:
    """Tests for run_update_action_screen wrapper function."""

    def test_returns_action_string(self) -> None:
        with patch("the_architect.tui.app.run_single_screen", return_value="update") as mock_run:
            result = run_update_action_screen(
                update_msg="outdated", install_hint="pip install -U x"
            )
            assert result == "update"
            mock_run.assert_called_once()

    def test_defaults_to_exit_on_none(self) -> None:
        with patch("the_architect.tui.app.run_single_screen", return_value=None):
            result = run_update_action_screen(
                update_msg="outdated", install_hint="pip install -U x"
            )
            assert result == "exit"


class TestRunSelfUpdateScreen:
    """Tests for run_self_update_screen wrapper function."""

    def test_returns_continue_action(self) -> None:
        with patch("the_architect.tui.app.run_single_screen", return_value="continue") as mock_run:
            result = run_self_update_screen(current_version="1.0.0", latest_version="2.0.0")
            assert result == "continue"
            mock_run.assert_called_once()

    def test_returns_update_action(self) -> None:
        with patch("the_architect.tui.app.run_single_screen", return_value="update"):
            result = run_self_update_screen(current_version="1.0.0", latest_version="2.0.0")
            assert result == "update"

    def test_defaults_to_continue_on_none(self) -> None:
        with patch("the_architect.tui.app.run_single_screen", return_value=None):
            result = run_self_update_screen(current_version="1.0.0", latest_version="2.0.0")
            assert result == "continue"


class TestRunPendingTasksScreen:
    """Tests for run_pending_tasks_screen wrapper function."""

    def test_returns_true_on_confirm(self) -> None:
        with patch("the_architect.tui.app.run_single_screen", return_value=True):
            result = run_pending_tasks_screen(pending=["T01", "T02"])
            assert result is True

    def test_returns_false_on_abort(self) -> None:
        with patch("the_architect.tui.app.run_single_screen", return_value=False):
            result = run_pending_tasks_screen(pending=["T01"])
            assert result is False

    def test_handles_none_result(self) -> None:
        with patch("the_architect.tui.app.run_single_screen", return_value=None):
            result = run_pending_tasks_screen(pending=["T01"])
            assert result is False


# ── SelfUpdateScreen tests ─────────────────────────────────────────────


class TestSelfUpdateScreen:
    """Tests for SelfUpdateScreen class using _Harness pattern."""

    @pytest.mark.asyncio
    async def test_compose_renders_version_info(self) -> None:
        screen = SelfUpdateScreen(current_version="1.0.0", latest_version="2.0.0")
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            # Just verify compose doesn't crash and widgets exist
            assert screen.query_one("#selfupdate_body") is not None
            assert screen.query_one("#selfupdate_title") is not None
            assert screen.query_one("#selfupdate_msg") is not None
            assert screen.query_one("#selfupdate_hint") is not None
            assert screen.query_one("#selfupdate_instructions") is not None

    @pytest.mark.asyncio
    async def test_action_continue_run_dismisses_continue(self) -> None:
        screen = SelfUpdateScreen(current_version="1.0.0", latest_version="2.0.0")
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            screen.action_continue_run()
            await pilot.pause(0.05)
        assert harness.dismissed == "continue"

    @pytest.mark.asyncio
    async def test_action_update_dismisses_update(self) -> None:
        screen = SelfUpdateScreen(current_version="1.0.0", latest_version="2.0.0")
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            screen.action_update()
            await pilot.pause(0.05)
        assert harness.dismissed == "update"


# ── Exception paths in action_confirm ──────────────────────────────────


class TestActionConfirmExceptionPaths:
    """Tests for exception fallback paths in action_confirm."""

    @pytest.mark.asyncio
    async def test_provider_screen_confirm_query_one_fails(self) -> None:
        """When query_one raises, action_confirm falls back to idx=0."""
        screen = ProviderSelectionScreen(
            options=[_fake_provider("OpenCode"), _fake_provider("Claude Code")]
        )
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            # Monkey-patch query_one to raise, triggering the exception handler
            screen.query_one = MagicMock(side_effect=Exception("boom"))  # type: ignore[method-assign]
            screen.action_confirm()
            await pilot.pause(0.05)
        assert harness.dismissed == 0

    @pytest.mark.asyncio
    async def test_string_list_picker_confirm_query_one_fails(self) -> None:
        """When query_one raises, action_confirm falls back to idx=0."""
        screen = StringListPickerScreen(title="Pick", hint="Hint", choices=[("a", "A"), ("b", "B")])
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            # Monkey-patch query_one to raise, triggering the exception handler
            screen.query_one = MagicMock(side_effect=Exception("boom"))  # type: ignore[method-assign]
            screen.action_confirm()
            await pilot.pause(0.05)
        assert harness.dismissed == "a"

    @pytest.mark.asyncio
    async def test_string_list_picker_confirm_index_is_none(self) -> None:
        """When ListView.index is None, action_confirm falls back to idx=0."""
        screen = StringListPickerScreen(title="Pick", hint="Hint", choices=[("a", "A"), ("b", "B")])
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            # Mock query_one to return a ListView with index=None
            mock_lv = MagicMock()
            mock_lv.index = None
            screen.query_one = MagicMock(return_value=mock_lv)  # type: ignore[method-assign]
            screen.action_confirm()
            await pilot.pause(0.05)
        assert harness.dismissed == "a"


# ── on_list_view_selected event handlers ───────────────────────────────


class TestOnListViewSelected:
    """Tests for on_list_view_selected event handlers on screens."""

    @pytest.mark.asyncio
    async def test_provider_screen_on_list_view_selected(self) -> None:
        """on_list_view_selected triggers action_confirm on ProviderSelectionScreen."""
        from textual.widgets import ListItem as ListItemWidget

        screen = ProviderSelectionScreen(
            options=[_fake_provider("OpenCode"), _fake_provider("Claude Code")]
        )
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            lv = screen.query_one("#provider_list", ListView)
            lv.index = 1
            await pilot.pause(0.05)
            # Simulate ListView.Selected event — needs a ListItem
            item = ListItemWidget()
            event = ListView.Selected(lv, item=item, index=1)
            screen.on_list_view_selected(event)
            await pilot.pause(0.05)
        assert harness.dismissed == 1

    @pytest.mark.asyncio
    async def test_string_list_picker_on_list_view_selected(self) -> None:
        """on_list_view_selected triggers action_confirm on StringListPickerScreen."""
        screen = StringListPickerScreen(title="Pick", hint="Hint", choices=[("a", "A"), ("b", "B")])
        harness = _Harness(screen)
        async with harness.run_test() as pilot:
            await pilot.pause(0.05)
            lv = screen.query_one("#picker_list", ListView)
            lv.index = 1
            await pilot.pause(0.05)
            from textual.widgets import ListItem as ListItemWidget

            item = ListItemWidget()
            event = ListView.Selected(lv, item=item, index=1)
            screen.on_list_view_selected(event)
            await pilot.pause(0.05)
        assert harness.dismissed == "b"


def _fake_provider(display: str, version: str = "1.2.3") -> ProviderOption:
    prov = MagicMock()
    prov.display_name = display
    prov.get_version = MagicMock(return_value=version)
    return ProviderOption(display_name=display, version=version, provider=prov)
