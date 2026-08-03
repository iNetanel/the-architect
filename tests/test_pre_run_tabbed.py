"""Tests for the tabbed PreRunScreen (Phase B).

Exercises the Pydantic data model :class:`PreRunValues` and the
tabbed :class:`PreRunScreen` using the same harness-app pattern as
the Phase A pre-run screen tests in :mod:`test_tui_pre_run`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from textual.widgets import (
    Checkbox,
    Footer,
    Label,
    ListView,
    RadioSet,
    Static,
    TabbedContent,
    TextArea,
)

from the_architect.core.provider import ArchitectProvider
from the_architect.tui.screens.pre_run_tabbed import (
    ArchitectModelConfirmScreen,
    GoalTextArea,
    InfiniteLoopConfirmScreen,
    PreRunScreen,
    PreRunValues,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _mock_provider(name: str = "opencode", display: str = "OpenCode") -> MagicMock:
    """Return a mock ArchitectProvider for tests.

    Args:
        name: Provider identifier (e.g. ``"opencode"``).
        display: Human-readable display name.

    Returns:
        A MagicMock satisfying the :class:`ArchitectProvider` protocol.
    """
    prov = MagicMock(spec=ArchitectProvider)
    prov.name = name
    prov.display_name = display
    prov.get_version = MagicMock(return_value="1.0.0")
    prov.list_models = MagicMock(return_value=["model-a", "model-b"])
    prov.list_agents = MagicMock(return_value=["build", "backend"])
    prov.supports_free_tier = MagicMock(return_value=True)
    prov.supports_agents = MagicMock(return_value=True)
    prov.has_any_models = MagicMock(return_value=True)
    prov.find_user_config = MagicMock(return_value=Path("/fake"))
    return prov


def _mock_config() -> Any:
    """Return a minimal ArchitectConfig for passing to PreRunScreen."""
    from the_architect.config import ArchitectConfig

    return ArchitectConfig()


# ── PreRunValues model tests ───────────────────────────────────────────


class TestPreRunValues:
    """PreRunValues Pydantic model — defaults, customisation, serialization."""

    def test_default_values(self) -> None:
        """Default PreRunValues has sensible defaults."""
        v = PreRunValues()
        assert v.goal == ""
        assert v.scope == "standard"
        assert v.context_paths == ()
        assert v.provider_name == ""
        assert v.architect_model is None
        assert v.execution_agent is None
        assert v.free is False
        assert v.persistent is False
        assert v.integrity is True
        assert v.force_reassessment is True
        assert v.infinite_loop is False
        assert v.token_budget_per_hour == 0
        assert v.notify_on_complete is True
        assert v.notify_on_fail is True
        assert v.action == "plan"

    def test_custom_values(self) -> None:
        """PreRunValues accepts custom values and stores them correctly."""
        v = PreRunValues(
            goal="Build auth",
            scope="complex",
            context_paths=(Path("docs/auth.md"),),
            provider_name="claude-code",
            architect_model="claude-sonnet-4-20250514",
            execution_agent="build",
            free=True,
            persistent=True,
            integrity=False,
            force_reassessment=False,
            infinite_loop=True,
            token_budget_per_hour=5000,
            notify_on_complete=False,
            notify_on_fail=False,
            action="replan",
        )
        assert v.goal == "Build auth"
        assert v.scope == "complex"
        assert v.context_paths == (Path("docs/auth.md"),)
        assert v.provider_name == "claude-code"
        assert v.architect_model == "claude-sonnet-4-20250514"
        assert v.execution_agent == "build"
        assert v.free is True
        assert v.persistent is True
        assert v.integrity is False
        assert v.force_reassessment is False
        assert v.infinite_loop is True
        assert v.token_budget_per_hour == 5000
        assert v.notify_on_complete is False
        assert v.notify_on_fail is False
        assert v.action == "replan"

    def test_serialization_round_trip(self) -> None:
        """model_dump + model_validate preserves all values."""
        original = PreRunValues(
            goal="Implement login flow",
            scope="simple",
            context_paths=(Path("README.md"), Path("arch.md")),
            provider_name="opencode",
            architect_model="model-x",
            execution_agent="backend",
            free=True,
            persistent=False,
            integrity=True,
            force_reassessment=False,
            infinite_loop=True,
            token_budget_per_hour=1234,
        )
        dump = original.model_dump()
        restored = PreRunValues.model_validate(dump)
        assert restored == original


# ── Screen harness ─────────────────────────────────────────────────────


class _Harness:
    """Minimal harness that mounts a PreRunScreen and captures dismiss value.

    Mirrors the pattern in :mod:`test_tui_pre_run` so the same assertions
    work across Phase A and Phase B screen tests.
    """

    def __init__(self, screen: Any) -> None:
        self._screen = screen
        self.dismissed: Any = "<not-dismissed>"

    def _on_dismiss(self, value: Any) -> None:
        """Callback invoked when the pushed screen dismisses itself."""
        self.dismissed = value

    async def run(self) -> Any:
        """Mount the screen in a textual test app and return the dismiss value.

        Returns:
            Whatever the screen passed to ``dismiss()``, or the sentinel
            string if the screen was never dismissed.
        """
        from textual.app import App

        class TestApp(App[None]):
            """Minimal test app for mounting a single screen."""

            def on_mount(self) -> None:
                self.push_screen(self._screen, self._on_dismiss_cb)

            def _on_dismiss_cb(self, value: Any) -> None:
                self._dismissed = value

        # Attach instance-level state to the class
        TestApp._screen = self._screen
        TestApp._on_dismiss_cb = lambda self, v: setattr(self, "_dismissed", v)
        TestApp._dismissed = "<not-dismissed>"

        async with TestApp().run_test() as pilot:
            await pilot.pause(0.05)
        return TestApp._dismissed


# ── PreRunScreen interaction tests ─────────────────────────────────────


class TestPreRunScreen:
    """Async interaction tests for the tabbed PreRunScreen."""

    @pytest.mark.asyncio
    async def test_cancel_returns_none(self) -> None:
        """Calling action_cancel dismisses the screen with None."""
        providers = [_mock_provider("opencode")]
        config = _mock_config()
        screen = PreRunScreen(
            providers=providers,
            config=config,
            project_dir=Path("/tmp/test_project"),
        )

        result: Any = "<not-dismissed>"

        async def _run_cancel() -> Any:
            from textual.app import App

            nonlocal result

            class CApp(App[None]):
                def on_mount(self) -> None:
                    self.push_screen(screen, self._cb)

                def _cb(self, value: Any) -> None:
                    nonlocal result
                    result = value

            async with CApp().run_test() as pilot:
                await pilot.pause(0.05)
                await pilot.pause(0.05)
                screen.action_cancel()
                await pilot.pause(0.05)
                await pilot.pause(0.05)

        await _run_cancel()
        assert result is None

    @pytest.mark.asyncio
    async def test_submit_with_incomplete_goal_stays(self) -> None:
        """Submitting with an empty (or short) goal keeps the screen open."""
        providers = [_mock_provider("opencode")]
        config = _mock_config()
        screen = PreRunScreen(
            providers=providers,
            config=config,
            project_dir=Path("/tmp/test_project"),
        )

        dismissed: Any = "<not-dismissed>"
        goal_area: Any = None

        async def _run_short() -> None:
            from textual.app import App

            nonlocal dismissed, goal_area

            class SApp(App[None]):
                def on_mount(self) -> None:
                    self.push_screen(screen, self._cb)

                def _cb(self, value: Any) -> None:
                    nonlocal dismissed
                    dismissed = value

            async with SApp().run_test() as pilot:
                await pilot.pause(0.05)
                screen.action_submit()
                await pilot.pause(0.05)
                goal_area = screen.query_one("#goal_text", TextArea)

        await _run_short()
        assert dismissed == "<not-dismissed>"

    @pytest.mark.asyncio
    async def test_submit_with_complete_goal(self) -> None:
        """Submitting with a goal >= 10 chars dismisses with PreRunValues."""
        providers = [_mock_provider("opencode")]
        config = _mock_config()
        screen = PreRunScreen(
            providers=providers,
            config=config,
            project_dir=Path("/tmp/test_project"),
            architect_model="model-a",
        )

        result: Any = None

        async def _run_complete() -> None:
            from textual.app import App

            nonlocal result

            class CApp(App[None]):
                def on_mount(self) -> None:
                    self.push_screen(screen, self._cb)

                def _cb(self, value: Any) -> None:
                    nonlocal result
                    result = value

            async with CApp().run_test() as pilot:
                await pilot.pause(0.05)
                area = screen.query_one("#goal_text", TextArea)
                area.text = "Build the feature"
                await pilot.pause(0.05)
                screen.action_submit()
                await pilot.pause(0.05)

        await _run_complete()
        assert isinstance(result, PreRunValues)
        assert result.goal == "Build the feature"
        assert result.infinite_loop is False

    @pytest.mark.asyncio
    async def test_infinite_loop_checkbox_defaults_off_for_new_goal(self) -> None:
        """New goals never inherit Infinite Loop as enabled."""
        from textual.app import App

        screen = PreRunScreen(
            providers=[_mock_provider("opencode")],
            config=_mock_config(),
            project_dir=Path("/tmp/test_project"),
        )

        class LoopDefaultApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda value: None)

        async with LoopDefaultApp().run_test() as pilot:
            await pilot.pause(0.05)
            assert screen.query_one("#chk_infinite_loop", Checkbox).value is False
            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_infinite_loop_requires_confirmation_and_cancel_disables_it(self) -> None:
        """Submitting with Infinite Loop opens the warning and cancel keeps the form open."""
        from textual.app import App

        screen = PreRunScreen(
            providers=[_mock_provider("opencode")],
            config=_mock_config(),
            project_dir=Path("/tmp/test_project"),
            architect_model="model-a",
        )
        result: Any = "<not-dismissed>"

        class LoopCancelApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, self._cb)

            def _cb(self, value: Any) -> None:
                nonlocal result
                result = value

        async with LoopCancelApp().run_test() as pilot:
            await pilot.pause(0.05)
            screen.query_one("#goal_text", TextArea).text = "Build the feature"
            screen.query_one("#chk_infinite_loop", Checkbox).value = True
            screen.action_submit()
            await pilot.pause(0.05)
            assert isinstance(screen.app.screen, InfiniteLoopConfirmScreen)
            screen.app.screen.action_choose(False)
            await pilot.pause(0.05)
            assert result == "<not-dismissed>"
            assert screen.query_one("#chk_infinite_loop", Checkbox).value is False
            screen.query_one("#chk_infinite_loop", Checkbox).value = True
            await pilot.pause(0.05)
            assert isinstance(screen.app.screen, InfiniteLoopConfirmScreen)
            screen.app.screen.action_choose(False)
            await pilot.pause(0.05)
            assert screen.query_one("#chk_infinite_loop", Checkbox).value is False
            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_infinite_loop_confirmation_submits_enabled_value(self) -> None:
        """Confirming the warning submits with Infinite Loop preserved for the chain."""
        from textual.app import App

        screen = PreRunScreen(
            providers=[_mock_provider("opencode")],
            config=_mock_config(),
            project_dir=Path("/tmp/test_project"),
            architect_model="model-a",
        )
        result: Any = "<not-dismissed>"

        class LoopConfirmApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, self._cb)

            def _cb(self, value: Any) -> None:
                nonlocal result
                result = value

        async with LoopConfirmApp().run_test() as pilot:
            await pilot.pause(0.05)
            screen.query_one("#goal_text", TextArea).text = "Build the feature"
            screen.query_one("#chk_infinite_loop", Checkbox).value = True
            screen.action_submit()
            await pilot.pause(0.05)
            assert isinstance(screen.app.screen, InfiniteLoopConfirmScreen)
            screen.app.screen.action_choose(True)
            await pilot.pause(0.05)

        assert isinstance(result, PreRunValues)
        assert result.infinite_loop is True

    @pytest.mark.asyncio
    async def test_existing_tasks_execute_can_submit_without_goal(self) -> None:
        """Existing-task execute path uses the main tabbed screen without requiring a goal."""
        from textual.app import App

        from the_architect.core.tasks import Task, TaskStatus

        providers = [_mock_provider("opencode")]
        config = _mock_config()
        task = Task(
            name="T01_existing",
            prefix="T01",
            number=1,
            path=Path("/tmp/T01_existing.md"),
            status=TaskStatus.PENDING,
            title="Existing task",
        )
        screen = PreRunScreen(
            providers=providers,
            config=config,
            project_dir=Path("/tmp/test_project"),
            pending_tasks=[task],
            action="execute",
            architect_model="model-a",
        )
        result: Any = None

        class CApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, self._cb)

            def _cb(self, value: Any) -> None:
                nonlocal result
                result = value

        async with CApp().run_test() as pilot:
            await pilot.pause(0.05)
            assert screen.query_one("#goal_text", TextArea).display is False
            assert screen.query_one("#scope_set").display is False
            screen.action_submit()
            await pilot.pause(0.05)

        assert isinstance(result, PreRunValues)
        assert result.action == "execute"
        assert result.goal == ""

    @pytest.mark.asyncio
    async def test_existing_tasks_replan_requires_goal(self) -> None:
        """Replan path still requires the Goal tab before submitting."""
        from textual.app import App

        from the_architect.core.tasks import Task, TaskStatus

        providers = [_mock_provider("opencode")]
        config = _mock_config()
        task = Task(
            name="T01_existing",
            prefix="T01",
            number=1,
            path=Path("/tmp/T01_existing.md"),
            status=TaskStatus.PENDING,
            title="Existing task",
        )
        screen = PreRunScreen(
            providers=providers,
            config=config,
            project_dir=Path("/tmp/test_project"),
            pending_tasks=[task],
            action="replan",
            architect_model="model-a",
        )
        result: Any = "<not-dismissed>"

        class CApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, self._cb)

            def _cb(self, value: Any) -> None:
                nonlocal result
                result = value

        async with CApp().run_test() as pilot:
            await pilot.pause(0.05)
            assert screen.query_one("#goal_text", TextArea).display is True
            assert screen.query_one("#scope_set").display is True
            assert screen.focused is screen.query_one("#goal_text", TextArea)
            screen.action_submit()
            await pilot.pause(0.05)
            assert result == "<not-dismissed>"
            area = screen.query_one("#goal_text", TextArea)
            area.text = "Build a replacement plan"
            screen.action_submit()
            await pilot.pause(0.05)

        assert isinstance(result, PreRunValues)
        assert result.action == "replan"
        assert result.goal == "Build a replacement plan"

    @pytest.mark.asyncio
    async def test_existing_tasks_replan_selection_reveals_goal_fields(self) -> None:
        """Replan hides pending summary, reveals goal fields, and focuses goal input."""
        from textual.app import App

        from the_architect.core.tasks import Task, TaskStatus

        task = Task(
            name="T01_existing",
            prefix="T01",
            number=1,
            path=Path("/tmp/T01_existing.md"),
            status=TaskStatus.PENDING,
            title="Existing task",
        )
        screen = PreRunScreen(
            providers=[_mock_provider("opencode")],
            config=_mock_config(),
            project_dir=Path("/tmp/test_project"),
            pending_tasks=[task],
            action="execute",
        )

        class CApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda value: None)

        async with CApp().run_test() as pilot:
            await pilot.pause(0.05)
            assert screen.query_one("#goal_text", TextArea).display is False
            assert screen.query_one("#pending_tasks_summary", Static).display is True
            rb_replan = screen.query_one("#rb_action_replan")
            rb_replan.value = True
            screen._update_replan_controls_visibility()
            await pilot.pause(0.05)
            await pilot.pause(0.05)
            assert screen.query_one("#goal_text", TextArea).display is True
            assert screen.query_one("#scope_set").display is True
            assert screen.query_one("#pending_tasks_summary", Static).display is False
            assert screen.focused is screen.query_one("#goal_text", TextArea)

    @pytest.mark.asyncio
    async def test_required_bindings_exist(self) -> None:
        """Required key bindings are registered on the screen."""
        providers = [_mock_provider("opencode")]
        config = _mock_config()
        screen = PreRunScreen(
            providers=providers,
            config=config,
            project_dir=Path("/tmp/test_project"),
        )

        binding_keys = {b.key for b in screen.BINDINGS}

        # Tab navigation
        assert "tab" in binding_keys
        assert "shift+tab" in binding_keys
        assert "ctrl+tab" in binding_keys
        assert "ctrl+shift+tab" in binding_keys

        # Pause menu via Escape
        assert "escape" in binding_keys

        # Submit via Enter
        assert "enter" in binding_keys

    @pytest.mark.asyncio
    async def test_project_subtitle_shows_path(self) -> None:
        """The subtitle shows the project directory path."""
        providers = [_mock_provider("opencode")]
        config = _mock_config()
        project_dir = Path("/tmp/my_awesome_project")
        screen = PreRunScreen(
            providers=providers,
            config=config,
            project_dir=project_dir,
        )

        from textual.app import App

        class SubApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with SubApp().run_test() as pilot:
            await pilot.pause(0.05)
            subtitle = screen.query_one("#prerun_subtitle")
            # The rendered content should contain the project path
            rendered = subtitle.render()
            assert str(project_dir) in str(rendered)
            assert list(screen.query(Footer)) == []
            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_model_fetch_error_shows_warning(self) -> None:
        """When list_models fails, the models tab shows an error message."""
        prov = _mock_provider("opencode")
        prov.list_models = MagicMock(side_effect=RuntimeError("no models"))
        screen = PreRunScreen(
            providers=[prov],
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
        )

        from textual.app import App

        class ErrApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with ErrApp().run_test() as pilot:
            await pilot.pause(0.05)
            await pilot.pause(0.05)
            # After mount, model_fetch_error should be True
            assert screen._model_fetch_error is True
            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_persisted_model_coerced_when_missing(self) -> None:
        """A persisted model not in the current list is coerced to None."""
        prov = _mock_provider("opencode")
        prov.list_models = MagicMock(return_value=["model-x", "model-y"])
        # architect_model="model-z" doesn't exist in the list
        screen = PreRunScreen(
            providers=[prov],
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
            architect_model="model-z",
        )

        from textual.app import App

        class CoerceApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with CoerceApp().run_test() as pilot:
            await pilot.pause(0.05)
            await pilot.pause(0.05)
            # model-z was coerced to None
            assert screen._values.architect_model is None
            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_provider_tab_hidden_for_single_provider(self) -> None:
        """When only one provider exists, the Provider tab is omitted."""
        providers = [_mock_provider("opencode")]
        screen = PreRunScreen(
            providers=providers,
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
        )
        assert screen._show_provider_tab is False

    @pytest.mark.asyncio
    async def test_provider_tab_shown_for_multiple_providers(self) -> None:
        """When multiple providers exist, the Provider tab is shown."""
        providers = [
            _mock_provider("opencode", "OpenCode"),
            _mock_provider("claude-code", "Claude Code"),
        ]
        screen = PreRunScreen(
            providers=providers,
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
        )
        assert screen._show_provider_tab is True

    @pytest.mark.asyncio
    async def test_scope_radio_lives_in_goal_tab(self) -> None:
        """Scope RadioSet is composed inside the Goal tab with Standard default.

        Regression guard: the user explicitly wants the Scope RadioSet
        ABOVE the Goal TextArea inside the Goal tab (not on a separate
        Scope tab). If someone re-splits them later, this test catches it.
        """
        from textual.app import App
        from textual.widgets import RadioSet, TabbedContent, TextArea

        providers = [_mock_provider("opencode")]
        screen = PreRunScreen(
            providers=providers,
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
        )

        class ScopeApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with ScopeApp().run_test() as pilot:
            await pilot.pause(0.05)
            tabs = screen.query_one("#prerun_tabs", TabbedContent)
            # Goal tab is active by default
            assert tabs.active == "tab_goal"
            # There must NOT be a separate Scope tab
            from textual.widgets import Tabs

            tab_ids = [t.id for t in tabs.query_one(Tabs).children if hasattr(t, "id") and t.id]
            assert "tab_scope" not in tab_ids
            # The scope_set RadioSet must live inside the Goal tab pane
            rs = screen.query_one("#scope_set", RadioSet)
            assert rs is not None
            # Standard must be pre-selected by default
            pressed = rs.pressed_button
            assert pressed is not None
            assert pressed.id == "rb_standard"
            # The goal_text TextArea must also be in the same tab
            area = screen.query_one("#goal_text", TextArea)
            assert area is not None
            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_switching_provider_does_not_crash(self) -> None:
        """Changing the selected provider refreshes lists without crashing.

        Regression guard: when the user picks a non-OpenCode provider,
        the Models tab must refresh without raising, regardless of
        whether that provider supports agents or has different models.
        """
        from textual.app import App

        # Two providers — first supports agents + free, second does neither
        prov_a = _mock_provider("opencode", "OpenCode")
        prov_a.list_models = MagicMock(return_value=["model-a1", "model-a2"])
        prov_a.list_agents = MagicMock(return_value=["build", "backend"])
        prov_a.supports_agents = MagicMock(return_value=True)
        prov_a.supports_free_tier = MagicMock(return_value=True)

        prov_b = _mock_provider("codex", "Codex CLI")
        prov_b.list_models = MagicMock(return_value=["gpt-5", "gpt-4.1"])
        prov_b.list_agents = MagicMock(return_value=[])
        prov_b.supports_agents = MagicMock(return_value=False)
        prov_b.supports_free_tier = MagicMock(return_value=False)

        screen = PreRunScreen(
            providers=[prov_a, prov_b],
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
        )

        class SwitchApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with SwitchApp().run_test() as pilot:
            await pilot.pause(0.05)
            await pilot.pause(0.05)
            # After mount, OpenCode is active; agent_list should have items
            assert screen._models == ["model-a1", "model-a2"]
            assert screen._agents == ["build", "backend"]

            # Switch to the Codex provider — this is the crash case
            rb_codex = screen.query_one("#rb_prov_1")
            rb_codex.value = True
            # Trigger the change handler
            screen._on_provider_changed()
            await pilot.pause(0.05)
            # No crash — models refreshed, agents empty
            assert screen._models == ["gpt-5", "gpt-4.1"]
            assert screen._agents == []
            screen.action_cancel()
            await pilot.pause(0.05)

    def test_stale_provider_fetch_results_are_ignored(self) -> None:
        """Late model results from a previous provider must not overwrite current state."""
        prov_a = _mock_provider("opencode", "OpenCode")
        prov_b = _mock_provider("codex", "Codex CLI")
        screen = PreRunScreen(
            providers=[prov_a, prov_b],
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
        )
        screen._values.provider_name = "codex"
        screen._provider_fetch_generation = {"opencode": 1, "codex": 2}
        screen._provider_loading = {"opencode", "codex"}
        screen._models = []
        screen._agents = []
        screen._models_loading = True

        screen._apply_provider_data("opencode", 1, ["wrong-opencode-model"], ["backend"], True)

        assert screen._models == []
        assert screen._agents == []
        assert screen._models_loading is True
        assert "opencode" not in screen._provider_loading

        screen._apply_provider_data("codex", 2, ["right-codex-model"], [], True)

        assert screen._models == ["right-codex-model"]
        assert screen._agents == []
        assert screen._models_loading is False
        assert screen._provider_data_cache["codex"] == (["right-codex-model"], [], True)

    def test_models_tab_stays_complete_while_provider_models_loading(self) -> None:
        """Provider defaults remain valid while optional model choices load."""
        screen = PreRunScreen(
            providers=[_mock_provider("opencode")],
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
            goal_text="Build something useful",
        )

        screen._models_loading = True

        assert screen._models_complete is True

    @pytest.mark.asyncio
    async def test_only_one_provider_radio_selected_at_a_time(self) -> None:
        """Provider selection handler responds correctly to each radio button change.

        Regression guard: users reported seeing multiple providers appear
        selected. This test verifies that our provider-change handler correctly
        tracks the selected provider when radio buttons are switched.
        """
        from textual.app import App
        from textual.widgets import RadioSet

        prov_a = _mock_provider("opencode", "OpenCode")
        prov_b = _mock_provider("claude-code", "Claude Code")
        prov_c = _mock_provider("codex", "Codex")
        screen = PreRunScreen(
            providers=[prov_a, prov_b, prov_c],
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
        )

        class MultiApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with MultiApp().run_test() as pilot:
            await pilot.pause(0.05)
            rs = screen.query_one("#provider_set", RadioSet)

            # Exactly one radio button should be pressed initially
            pressed_count = sum(1 for btn in rs.query("RadioButton") if btn.value)
            assert pressed_count == 1

            # Switch to the second provider via direct value + handler (reliable in headless)
            rb2 = screen.query_one("#rb_prov_1")
            rb2.value = True
            screen._on_provider_changed()
            await pilot.pause(0.05)
            assert screen._values.provider_name == "claude-code"

            # Switch to the third provider
            rb3 = screen.query_one("#rb_prov_2")
            rb3.value = True
            screen._on_provider_changed()
            await pilot.pause(0.05)
            assert screen._values.provider_name == "codex"

            pressed_count = sum(1 for btn in rs.query("RadioButton") if btn.value)
            assert pressed_count == 1
            assert rs.pressed_button is not None
            assert rs.pressed_button.id == "rb_prov_2"

            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_provider_change_warns_when_architect_model_set(
        self,
    ) -> None:
        """Footer warning appears when provider change resets a model selection."""
        from textual.app import App

        prov_a = _mock_provider("opencode", "OpenCode")
        prov_b = _mock_provider("claude-code", "Claude Code")
        screen = PreRunScreen(
            providers=[prov_a, prov_b],
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
        )

        class WarnApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with WarnApp().run_test() as pilot:
            await pilot.pause(0.05)
            # Simulate user having selected an architect model
            screen._values.architect_model = "gpt-4o"
            # Switch provider — should trigger warning
            rb2 = screen.query_one("#rb_prov_1")
            rb2.value = True
            screen._on_provider_changed()
            await pilot.pause(0.05)
            # Warning should mention architect model reset
            assert "architect model" in screen._warning_text
            assert "reset" in screen._warning_text
            # Values should be cleared
            assert screen._values.architect_model is None
            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_provider_change_warns_when_execution_agent_set(
        self,
    ) -> None:
        """Footer warning appears when provider change resets an agent selection."""
        from textual.app import App

        prov_a = _mock_provider("opencode", "OpenCode")
        prov_b = _mock_provider("claude-code", "Claude Code")
        screen = PreRunScreen(
            providers=[prov_a, prov_b],
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
        )

        class WarnApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with WarnApp().run_test() as pilot:
            await pilot.pause(0.05)
            # Simulate user having selected an execution agent
            screen._values.execution_agent = "backend"
            # Switch provider — should trigger warning
            rb2 = screen.query_one("#rb_prov_1")
            rb2.value = True
            screen._on_provider_changed()
            await pilot.pause(0.05)
            # Warning should mention execution agent reset
            assert "execution agent" in screen._warning_text
            assert "reset" in screen._warning_text
            # Values should be cleared
            assert screen._values.execution_agent is None
            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_provider_change_warns_when_both_model_and_agent_set(
        self,
    ) -> None:
        """Footer warning mentions both when both model and agent are reset."""
        from textual.app import App

        prov_a = _mock_provider("opencode", "OpenCode")
        prov_b = _mock_provider("claude-code", "Claude Code")
        screen = PreRunScreen(
            providers=[prov_a, prov_b],
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
        )

        class WarnApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with WarnApp().run_test() as pilot:
            await pilot.pause(0.05)
            # Simulate user having selected both
            screen._values.architect_model = "gpt-4o"
            screen._values.execution_agent = "backend"
            # Switch provider — should trigger warning mentioning both
            rb2 = screen.query_one("#rb_prov_1")
            rb2.value = True
            screen._on_provider_changed()
            await pilot.pause(0.05)
            assert "architect model" in screen._warning_text
            assert "agent" in screen._warning_text
            assert "reset" in screen._warning_text
            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_provider_change_no_warning_when_nothing_to_reset(
        self,
    ) -> None:
        """No footer warning when both model and agent were already None."""
        from textual.app import App

        prov_a = _mock_provider("opencode", "OpenCode")
        prov_b = _mock_provider("claude-code", "Claude Code")
        screen = PreRunScreen(
            providers=[prov_a, prov_b],
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
        )

        class WarnApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with WarnApp().run_test() as pilot:
            await pilot.pause(0.05)
            # Both are None by default — no selection to lose
            assert screen._values.architect_model is None
            assert screen._values.execution_agent is None
            # Record footer state before switch
            footer_before = screen._warning_text
            # Switch provider — should NOT trigger a reset warning
            rb2 = screen.query_one("#rb_prov_1")
            rb2.value = True
            screen._on_provider_changed()
            await pilot.pause(0.05)
            # Footer should not contain a reset warning
            assert "reset" not in screen._warning_text.lower() or (
                screen._warning_text == footer_before
            )
            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_enter_submits_from_goal_textarea(self) -> None:
        """Pressing Enter in the Goal TextArea submits the form.

        Regression guard: users expect Enter to submit (chat-app
        convention). The screen has a priority Enter binding, so this
        verifies the wiring end-to-end with a valid goal text.
        """
        from textual.app import App

        providers = [_mock_provider("opencode")]
        screen = PreRunScreen(
            providers=providers,
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
            architect_model="model-a",
        )
        result: Any = "<not-dismissed>"

        class EnterApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, self._cb)

            def _cb(self, value: Any) -> None:
                nonlocal result
                result = value

        async with EnterApp().run_test() as pilot:
            await pilot.pause(0.05)
            area = screen.query_one("#goal_text", GoalTextArea)
            area.focus()
            area.text = "Build something reasonable"
            await pilot.pause(0.05)
            await pilot.press("enter")
            await pilot.pause(0.05)
            await pilot.pause(0.05)

        assert isinstance(result, PreRunValues)
        assert result.goal == "Build something reasonable"

    @pytest.mark.asyncio
    async def test_shift_enter_inserts_newline_in_goal_textarea(self) -> None:
        """Shift+Enter adds a newline instead of submitting.

        Regression guard: users composing multi-line goals should still
        be able to do so; we only move Enter to 'submit', not remove the
        ability to insert newlines.
        """
        from textual.app import App

        providers = [_mock_provider("opencode")]
        screen = PreRunScreen(
            providers=providers,
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
        )

        class ShiftApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with ShiftApp().run_test() as pilot:
            await pilot.pause(0.05)
            area = screen.query_one("#goal_text", GoalTextArea)
            area.focus()
            area.text = "line 1"
            # Move cursor to end so the newline lands after "line 1"
            area.move_cursor(area.document.end)
            await pilot.pause(0.05)
            await pilot.press("shift+enter")
            # Continue typing
            for ch in "line 2":
                await pilot.press(ch)
            await pilot.pause(0.05)

            assert "\n" in area.text
            assert area.text.startswith("line 1")
            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_arrow_keys_not_intercepted_by_screen(self) -> None:
        """Arrow keys are not handled by the screen — they belong to widgets.

        All four arrow keys (up/down/left/right) are absent from BINDINGS
        and _on_key does not intercept them.  Widgets own cursor movement
        and item navigation.  Tab / Shift+Tab move focus between fields.
        """
        providers = [_mock_provider("opencode")]
        screen = PreRunScreen(
            providers=providers,
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
        )
        bindings = {b.key: b.action for b in screen.BINDINGS}
        assert bindings.get("up") is None
        assert bindings.get("down") is None
        assert bindings.get("left") is None
        assert bindings.get("right") is None
        # Tab navigation bindings still present
        assert bindings.get("tab") == "next_tab"
        assert bindings.get("shift+tab") == "prev_tab"

    @pytest.mark.asyncio
    async def test_up_down_move_focus_between_prerun_sections(self) -> None:
        from textual.app import App

        providers = [_mock_provider("opencode")]
        screen = PreRunScreen(
            providers=providers,
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
        )

        class FocusApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with FocusApp().run_test() as pilot:
            await pilot.pause(0.05)
            area = screen.query_one("#goal_text", GoalTextArea)
            area.focus()
            await pilot.pause(0.05)

            # up from goal_text → scope_set RadioSet (RadioSet has
            # can_focus_children=False, so we focus the container itself)
            screen.action_focus_previous()
            await pilot.pause(0.05)
            assert getattr(screen.focused, "id", None) == "scope_set"

            # down from scope_set first moves the radio cursor only; it must not
            # change the selected scope until the user presses Space/clicks.
            rs = screen.query_one("#scope_set", RadioSet)
            screen.action_focus_next()
            await pilot.pause(0.05)
            assert rs.pressed_button is not None
            assert rs.pressed_button.id == "rb_standard"
            assert getattr(screen.focused, "id", None) == "scope_set"

            # Continue down to the end of the radio cursor range, then leave
            # the Scope section for goal_text without changing the selection.
            screen.action_focus_next()
            await pilot.pause(0.05)
            assert rs.pressed_button is not None
            assert rs.pressed_button.id == "rb_standard"
            assert getattr(screen.focused, "id", None) == "scope_set"

            screen.action_focus_next()
            await pilot.pause(0.05)
            assert getattr(screen.focused, "id", None) == "goal_text"

            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_up_down_move_model_list_selection_before_next_section(self) -> None:
        """Up/down should select ListView rows before leaving the list section."""
        from textual.app import App

        providers = [_mock_provider("opencode")]
        screen = PreRunScreen(
            providers=providers,
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
        )

        class ModelApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda value: None)

        async with ModelApp().run_test() as pilot:
            await pilot.pause(0.05)
            screen.action_jump_tab_2()
            await pilot.pause(0.05)
            model_list = screen.query_one("#model_list", ListView)
            model_list.focus()
            await pilot.pause(0.05)
            assert model_list.index == 0

            screen.action_focus_next()
            await pilot.pause(0.05)
            assert model_list.index == 1
            assert screen._collect_values().architect_model is None

            screen.action_focus_next()
            await pilot.pause(0.05)
            assert model_list.index == 2
            assert screen._collect_values().architect_model is None

            await pilot.press("space")
            await pilot.pause(0.05)
            assert screen._collect_values().architect_model == "model-b"
            selected_label = model_list.children[2].query_one(Label).render()
            default_label = model_list.children[0].query_one(Label).render()
            assert str(selected_label).startswith("● ")
            assert str(default_label).startswith("○ ")

            # At the last model row, down leaves the model list for the agent list.
            screen.action_focus_next()
            await pilot.pause(0.05)
            assert getattr(screen.focused, "id", None) == "agent_list"

            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_existing_run_action_arrows_hover_replan_until_space_selects(self) -> None:
        """Existing-task action arrows hover choices; Space commits Replan."""
        from textual.app import App
        from textual.widgets import RadioSet

        from the_architect.core.tasks import Task, TaskStatus

        task = Task(
            name="T01_existing",
            prefix="T01",
            number=1,
            path=Path("/tmp/T01_existing.md"),
            status=TaskStatus.PENDING,
            title="Existing task",
        )
        screen = PreRunScreen(
            providers=[_mock_provider("opencode")],
            config=_mock_config(),
            project_dir=Path("/tmp/test_project"),
            pending_tasks=[task],
            action="execute",
        )

        class RunApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda value: None)

        async with RunApp().run_test() as pilot:
            await pilot.pause(0.05)
            action_set = screen.query_one("#action_set", RadioSet)
            action_set.focus()
            assert action_set.pressed_button is not None
            assert action_set.pressed_button.id == "rb_action_execute"

            screen.action_focus_next()
            await pilot.pause(0.05)
            assert action_set.pressed_button is not None
            assert action_set.pressed_button.id == "rb_action_execute"
            assert screen.query_one("#goal_text", TextArea).display is False

            await pilot.press("space")
            await pilot.pause(0.05)
            assert action_set.pressed_button is not None
            assert action_set.pressed_button.id == "rb_action_replan"
            assert screen.query_one("#goal_text", TextArea).display is True

            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_provider_tab_focus_next_handles_content_tabs_focus(self) -> None:
        """Provider tab down/next must not crash when Textual focus is on ContentTabs."""
        from textual.app import App
        from textual.widgets import RadioSet, TabbedContent
        from textual.widgets._tabbed_content import ContentTabs

        screen = PreRunScreen(
            providers=[_mock_provider("opencode"), _mock_provider("claude-code", "Claude Code")],
            config=_mock_config(),
            project_dir=Path("/tmp/test_project"),
        )

        class RunApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda value: None)

        async with RunApp().run_test() as pilot:
            await pilot.pause(0.05)
            screen._try_activate_tab("tab_provider")
            await pilot.pause(0.05)
            tabs = screen.query_one("#prerun_tabs", TabbedContent)
            assert tabs.active == "tab_provider"
            screen.query_one(ContentTabs).focus()

            screen.action_focus_next()
            await pilot.pause(0.05)

            assert getattr(screen.focused, "id", None) == "provider_set"
            assert screen.query_one("#provider_set", RadioSet).pressed_button is not None

            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_options_tab_left_right_switches_tabs(self) -> None:
        """Tab navigation works correctly across all tabs.

        Tests action_next_tab and action_focus_next are callable without error,
        and that focus moves between widgets within the Options tab using the
        internal focus-next/previous actions. Arrow keys belong to widgets.
        """
        from textual.app import App
        from textual.widgets import TabbedContent

        providers = [_mock_provider("opencode")]
        screen = PreRunScreen(
            providers=providers,
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
        )

        class ModeApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with ModeApp().run_test() as pilot:
            await pilot.pause(0.05)

            # action_next_tab / action_prev_tab must not raise
            screen.action_next_tab()
            await pilot.pause(0.05)
            screen.action_prev_tab()
            await pilot.pause(0.05)

            # Navigate to the Options tab by number key
            screen.action_jump_tab_4()
            await pilot.pause(0.1)
            await pilot.pause(0.1)
            tabs = screen.query_one("#prerun_tabs", TabbedContent)
            assert tabs.active == "tab_mode"

            # Focus chk_free — first focusable widget in Options tab
            chk_free = screen.query_one("#chk_free")
            chk_free.focus()
            await pilot.pause(0.05)
            assert getattr(screen.focused, "id", None) == "chk_free"

            # down key → moves focus within Options tab
            # New order: Free, Persistent, Infinite Loop, Budget, Budget Run, Task Timeout,
            #   Integrity, Force Reassessment, Notify Complete, Notify Fail
            screen.action_focus_next()
            await pilot.pause(0.05)
            assert getattr(screen.focused, "id", None) == "chk_persistent"

            screen.action_focus_next()
            await pilot.pause(0.05)
            assert getattr(screen.focused, "id", None) == "chk_infinite_loop"

            screen.action_focus_next()
            await pilot.pause(0.05)
            assert getattr(screen.focused, "id", None) == "inp_budget"

            screen.action_focus_next()
            await pilot.pause(0.05)
            assert getattr(screen.focused, "id", None) == "inp_budget_run"

            screen.action_focus_next()
            await pilot.pause(0.05)
            assert getattr(screen.focused, "id", None) == "inp_task_timeout"

            screen.action_focus_next()
            await pilot.pause(0.05)
            assert getattr(screen.focused, "id", None) == "chk_integrity"

            screen.action_focus_next()
            await pilot.pause(0.05)
            assert getattr(screen.focused, "id", None) == "chk_force_reassessment"

            screen.action_focus_next()
            await pilot.pause(0.05)
            assert getattr(screen.focused, "id", None) == "chk_notify_complete"

            screen.action_focus_next()
            await pilot.pause(0.05)
            assert getattr(screen.focused, "id", None) == "chk_notify_fail"

            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_arrow_defers_to_textarea_for_cursor_movement(self) -> None:
        """Arrow keys belong to widgets — not intercepted by the screen.

        Arrow keys are not in BINDINGS and _on_key does not intercept them.
        They always reach the focused widget for cursor movement or item
        navigation.  This test verifies:
        1. ``action_next_tab`` / ``action_prev_tab`` are callable from any state.
        2. Arrow keys are NOT in BINDINGS (widgets own them entirely).
        3. Ctrl+Left / Ctrl+Right still switch tabs even inside a TextArea.
        """
        from textual.app import App

        providers = [_mock_provider("opencode")]
        screen = PreRunScreen(
            providers=providers,
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
        )

        class EditorApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with EditorApp().run_test() as pilot:
            await pilot.pause(0.05)
            area = screen.query_one("#goal_text", GoalTextArea)
            area.focus()
            area.text = "hello world"
            await pilot.pause(0.05)

            # Arrow keys are NOT in BINDINGS — handled conditionally in _on_key
            bindings = {b.key: b.action for b in screen.BINDINGS}
            assert bindings.get("right") is None
            assert bindings.get("left") is None

            # action_next_tab callable without raising, even with goal focused
            tabs = screen.query_one("#prerun_tabs", TabbedContent)
            assert tabs.active == "tab_goal"
            screen.action_next_tab()
            await pilot.pause(0.05)
            await pilot.pause(0.05)
            assert tabs.active == "tab_models"

            # action_prev_tab callable too
            screen.action_prev_tab()
            await pilot.pause(0.05)
            await pilot.pause(0.05)
            assert tabs.active == "tab_goal"

            # Ctrl+Left / Ctrl+Right still in BINDINGS for tab switching from TextArea
            assert bindings.get("ctrl+left") == "prev_tab"
            assert bindings.get("ctrl+right") == "next_tab"

            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_tab_switches_tab_from_radioset(self) -> None:
        """Tab key actually changes the active tab when RadioSet has focus.

        Regression guard: the previous implementation called
        ``_auto_focus_active_tab()`` synchronously inside ``action_next_tab``,
        which focused a widget in the new tab before the tab-switch event had
        settled. Textual's ``TabbedContent._on_tab_pane_focused`` then fired
        for the OLD focused widget and reset ``active`` back — making the first
        tab press a no-op. The fix defers ``_auto_focus_active_tab``
        with ``call_after_refresh`` so the tab switch lands before focus moves.
        """
        from textual.app import App
        from textual.widgets import RadioSet, TabbedContent

        providers = [_mock_provider("opencode")]
        screen = PreRunScreen(
            providers=providers,
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
        )

        class TabApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with TabApp().run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.05)
            await pilot.pause(0.05)

            tabs = screen.query_one("#prerun_tabs", TabbedContent)
            assert tabs.active == "tab_goal"

            # Focus the RadioSet (scope_set) on the Goal tab
            rs = screen.query_one("#scope_set", RadioSet)
            rs.focus()
            await pilot.pause(0.05)

            # Press Tab — must switch to the next tab (tab_models, no Provider tab)
            await pilot.press("tab")
            await pilot.pause(0.05)
            assert tabs.active == "tab_models", (
                f"Tab from RadioSet did not switch tab: still on {tabs.active!r}"
            )

            # Press Tab again — should reach Options tab
            await pilot.press("tab")
            await pilot.pause(0.05)
            assert tabs.active == "tab_mode"

            # Press Shift+Tab — back to Models
            await pilot.press("shift+tab")
            await pilot.pause(0.05)
            assert tabs.active == "tab_models"

            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_tab_and_shift_tab_switch_tabs(self) -> None:
        """Tab/Shift+Tab match the pre-run footer's tab navigation hint."""
        from textual.app import App
        from textual.widgets import TabbedContent

        screen = PreRunScreen(
            providers=[_mock_provider("opencode")],
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
        )

        class TabApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda value: None)

        async with TabApp().run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.05)
            tabs = screen.query_one("#prerun_tabs", TabbedContent)
            assert tabs.active == "tab_goal"

            await pilot.press("tab")
            await pilot.pause(0.05)
            assert tabs.active == "tab_models"

            await pilot.press("shift+tab")
            await pilot.pause(0.05)
            assert tabs.active == "tab_goal"

            screen.action_cancel()
            await pilot.pause(0.05)

    # ── Model/agent label tests ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_model_label_shows_committed_model(self) -> None:
        """model_label shows the committed architect model after selection."""
        from textual.app import App

        prov = _mock_provider("opencode")
        prov.list_models = MagicMock(return_value=["model-a", "model-b"])

        screen = PreRunScreen(
            providers=[prov],
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
            architect_model="model-a",
        )

        class ModelLabelApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with ModelLabelApp().run_test() as pilot:
            await pilot.pause(0.05)
            await pilot.pause(0.05)
            label = screen.query_one("#model_label", Label)
            assert "Selected: model-a" in label.render_str(label.render()).plain
            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_model_label_shows_provider_default_when_none(self) -> None:
        """model_label shows (provider default) when architect_model is None."""
        from textual.app import App

        prov = _mock_provider("opencode")
        prov.list_models = MagicMock(return_value=["model-a", "model-b"])

        screen = PreRunScreen(
            providers=[prov],
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
            architect_model="",
        )

        class DefaultModelApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with DefaultModelApp().run_test() as pilot:
            await pilot.pause(0.05)
            await pilot.pause(0.05)
            label = screen.query_one("#model_label", Label)
            assert "Selected: (provider default)" in label.render_str(label.render()).plain
            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_agent_label_shows_committed_agent(self) -> None:
        """agent_label shows the committed execution agent when supported."""
        from textual.app import App

        prov = _mock_provider("opencode")
        prov.list_models = MagicMock(return_value=["model-a", "model-b"])
        prov.list_agents = MagicMock(return_value=["build", "backend"])
        prov.supports_agents = MagicMock(return_value=True)

        screen = PreRunScreen(
            providers=[prov],
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
            architect_model="model-a",
            execution_model="build",
        )

        class AgentLabelApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with AgentLabelApp().run_test() as pilot:
            await pilot.pause(0.05)
            await pilot.pause(0.05)
            agent_label = screen.query_one("#agent_label", Label)
            assert agent_label.display is True
            assert "Selected: build" in agent_label.render_str(agent_label.render()).plain
            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_agent_label_shows_provider_default_when_none(self) -> None:
        """agent_label shows (provider default) when execution_agent is None."""
        from textual.app import App

        prov = _mock_provider("opencode")
        prov.list_models = MagicMock(return_value=["model-a", "model-b"])
        prov.list_agents = MagicMock(return_value=["build", "backend"])
        prov.supports_agents = MagicMock(return_value=True)

        screen = PreRunScreen(
            providers=[prov],
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
            execution_model="",
        )

        class DefaultAgentApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with DefaultAgentApp().run_test() as pilot:
            await pilot.pause(0.05)
            await pilot.pause(0.05)
            agent_label = screen.query_one("#agent_label", Label)
            assert agent_label.display is True
            assert (
                "Selected: (provider default)" in agent_label.render_str(agent_label.render()).plain
            )
            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_agent_label_hidden_when_provider_no_agents(self) -> None:
        """agent_label is hidden when the provider does not support agents."""
        from textual.app import App

        prov = _mock_provider("opencode")
        prov.list_models = MagicMock(return_value=["model-a"])
        prov.list_agents = MagicMock(return_value=[])
        prov.supports_agents = MagicMock(return_value=False)

        screen = PreRunScreen(
            providers=[prov],
            config=_mock_config(),
            project_dir=Path("/tmp/test"),
        )

        class NoAgentsApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with NoAgentsApp().run_test() as pilot:
            await pilot.pause(0.05)
            await pilot.pause(0.05)
            agent_label = screen.query_one("#agent_label", Label)
            assert agent_label.display is False
            screen.action_cancel()
            await pilot.pause(0.05)


# ── Architect model confirmation tests ─────────────────────────────────


class TestArchitectModelConfirmation:
    """ArchitectModelConfirmScreen — shown when architect_model is None at submit."""

    @pytest.mark.asyncio
    async def test_submit_blank_model_shows_confirmation(self) -> None:
        """Submit with architect_model=None shows ArchitectModelConfirmScreen."""
        from textual.app import App

        screen = PreRunScreen(
            providers=[_mock_provider("opencode")],
            config=_mock_config(),
            project_dir=Path("/tmp/test_project"),
        )

        class ConfirmApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with ConfirmApp().run_test() as pilot:
            await pilot.pause(0.05)
            # Fill in goal so _all_complete is True
            screen.query_one("#goal_text", TextArea).text = "Build the feature"
            await pilot.pause(0.05)
            # architect_model is None by default
            assert screen._values.architect_model is None
            screen.action_submit()
            await pilot.pause(0.05)
            assert isinstance(screen.app.screen, ArchitectModelConfirmScreen)

    @pytest.mark.asyncio
    async def test_confirm_architect_model_proceeds_to_dismiss(self) -> None:
        """Confirming architect model warning dismisses the screen with values."""
        from textual.app import App

        screen = PreRunScreen(
            providers=[_mock_provider("opencode")],
            config=_mock_config(),
            project_dir=Path("/tmp/test_project"),
        )
        result: Any = "<not-dismissed>"

        class ConfirmApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, self._cb)

            def _cb(self, value: Any) -> None:
                nonlocal result
                result = value

        async with ConfirmApp().run_test() as pilot:
            await pilot.pause(0.05)
            screen.query_one("#goal_text", TextArea).text = "Build the feature"
            await pilot.pause(0.05)
            screen.action_submit()
            await pilot.pause(0.05)
            assert isinstance(screen.app.screen, ArchitectModelConfirmScreen)
            # Confirm the warning
            screen._handle_architect_model_confirmation(True)
            await pilot.pause(0.05)

        assert isinstance(result, PreRunValues)
        assert result.goal == "Build the feature"
        assert result.architect_model is None
        assert result.infinite_loop is False

    @pytest.mark.asyncio
    async def test_cancel_architect_model_stays_on_form(self) -> None:
        """Cancelling architect model warning keeps the form open."""
        from textual.app import App

        screen = PreRunScreen(
            providers=[_mock_provider("opencode")],
            config=_mock_config(),
            project_dir=Path("/tmp/test_project"),
        )
        result: Any = "<not-dismissed>"

        class ConfirmApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, self._cb)

            def _cb(self, value: Any) -> None:
                nonlocal result
                result = value

        async with ConfirmApp().run_test() as pilot:
            await pilot.pause(0.05)
            screen.query_one("#goal_text", TextArea).text = "Build the feature"
            await pilot.pause(0.05)
            screen.action_submit()
            await pilot.pause(0.05)
            assert isinstance(screen.app.screen, ArchitectModelConfirmScreen)
            # Cancel the warning
            screen._handle_architect_model_confirmation(False)
            await pilot.pause(0.05)
            # Screen should NOT be dismissed
            assert result == "<not-dismissed>"
            assert screen._architect_model_confirmed is False
            # Footer should show a hint message
            assert "Models tab" in screen._warning_text or "model" in screen._warning_text.lower()
            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_explicit_architect_model_skips_confirmation(self) -> None:
        """Submit with an explicit architect_model skips the confirmation modal."""
        from textual.app import App

        screen = PreRunScreen(
            providers=[_mock_provider("opencode")],
            config=_mock_config(),
            project_dir=Path("/tmp/test_project"),
            architect_model="model-a",
        )
        result: Any = "<not-dismissed>"

        class SkipApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, self._cb)

            def _cb(self, value: Any) -> None:
                nonlocal result
                result = value

        async with SkipApp().run_test() as pilot:
            await pilot.pause(0.05)
            await pilot.pause(0.05)
            screen.query_one("#goal_text", TextArea).text = "Build the feature"
            await pilot.pause(0.05)
            assert screen._values.architect_model == "model-a"
            screen.action_submit()
            await pilot.pause(0.05)
            # Should have dismissed directly — no modal shown
            assert not isinstance(screen.app.screen, ArchitectModelConfirmScreen)

        assert isinstance(result, PreRunValues)
        assert result.architect_model == "model-a"

    @pytest.mark.asyncio
    async def test_blank_model_then_infinite_loop_shows_model_modal_first(self) -> None:
        """When both architect_model=None and infinite_loop=True, model modal comes first."""
        from textual.app import App

        screen = PreRunScreen(
            providers=[_mock_provider("opencode")],
            config=_mock_config(),
            project_dir=Path("/tmp/test_project"),
        )
        result: Any = "<not-dismissed>"

        class ChainApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, self._cb)

            def _cb(self, value: Any) -> None:
                nonlocal result
                result = value

        async with ChainApp().run_test() as pilot:
            await pilot.pause(0.05)
            screen.query_one("#goal_text", TextArea).text = "Build the feature"
            # Check infinite loop — the on_checkbox_changed handler fires the IL
            # modal which cancels and unchecks the box. Re-check so _collect_values
            # sees True at submit time.
            screen.query_one("#chk_infinite_loop", Checkbox).value = True
            await pilot.pause(0.05)
            screen.query_one("#chk_infinite_loop", Checkbox).value = True
            await pilot.pause(0.05)
            # First submit — architect_model=None, so model modal appears first
            screen.action_submit()
            await pilot.pause(0.05)
            assert isinstance(screen.app.screen, ArchitectModelConfirmScreen)
            # Confirm the model modal — dismiss(True) triggers the callback which
            # sets _architect_model_confirmed and re-runs action_submit().
            screen.app.screen.dismiss(True)
            await pilot.pause(0.05)
            assert isinstance(screen.app.screen, InfiniteLoopConfirmScreen)
            # Cancel infinite loop
            screen.app.screen.action_choose(False)
            await pilot.pause(0.05)
            assert result == "<not-dismissed>"
            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_model_selection_resets_confirmation_flag(self) -> None:
        """Changing the model selection resets _architect_model_confirmed to False."""
        from textual.app import App

        screen = PreRunScreen(
            providers=[_mock_provider("opencode")],
            config=_mock_config(),
            project_dir=Path("/tmp/test_project"),
        )

        class ModelApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with ModelApp().run_test() as pilot:
            await pilot.pause(0.05)
            # Simulate a prior confirmation
            screen._architect_model_confirmed = True
            # Select a model from the list (index 1 = first real model)
            model_list = screen.query_one("#model_list", ListView)
            model_list.focus()
            model_list.index = 1
            await pilot.press("space")
            await pilot.pause(0.05)
            # Flag should be reset
            assert screen._architect_model_confirmed is False
            screen.action_cancel()
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_provider_change_resets_architect_model_confirmation(self) -> None:
        """Switching provider resets _architect_model_confirmed to False."""
        from textual.app import App

        prov_a = _mock_provider("opencode", "OpenCode")
        prov_b = _mock_provider("claude-code", "Claude Code")
        screen = PreRunScreen(
            providers=[prov_a, prov_b],
            config=_mock_config(),
            project_dir=Path("/tmp/test_project"),
        )

        class ProvApp(App[None]):
            def on_mount(self) -> None:
                self.push_screen(screen, lambda v: None)

        async with ProvApp().run_test() as pilot:
            await pilot.pause(0.05)
            # Simulate a prior confirmation
            screen._architect_model_confirmed = True
            # Switch provider
            rb2 = screen.query_one("#rb_prov_1")
            rb2.value = True
            screen._on_provider_changed()
            await pilot.pause(0.05)
            assert screen._architect_model_confirmed is False
            screen.action_cancel()
            await pilot.pause(0.05)
