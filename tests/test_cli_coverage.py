"""CLI prompt function coverage improvement — Cycle 36 T03.

Tests the _prompt_* helper functions in cli.py using the established mocking
pattern: patch _tui_mode_enabled, Application.run, and _get_prompt_toolkit_output.
No live Textual imports — those cause event loop conflicts under pytest.

Target: improve cli.py coverage from 51% to 60%+.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from the_architect.config import ArchitectConfig
from the_architect.core.provider import ArchitectProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_provider(
    display_name: str = "OpenCode",
    supports_free: bool = True,
    supports_agents: bool = True,
) -> MagicMock:
    """Create a mock ArchitectProvider with standard defaults."""
    p = MagicMock(spec=ArchitectProvider)
    p.display_name = display_name
    p.supports_free_tier.return_value = supports_free
    p.supports_agents.return_value = supports_agents
    p.get_version.return_value = "1.0.0"
    p.list_models.return_value = ["model-a", "model-b"]
    p.get_resolved_model.return_value = "model-a"
    p.list_agents.return_value = ["agent-a"]
    p.check_update_available.return_value = ""
    p.install_hint.return_value = "pip install -U the-architect"
    return p


# ---------------------------------------------------------------------------
# _prompt_mode_selection
# ---------------------------------------------------------------------------


class TestPromptModeSelection:
    """Coverage for _prompt_mode_selection (lines 1499-1732)."""

    def test_tui_fast_path_returns_result(self) -> None:
        """TUI mode returns the dict from run_mode_selection."""
        from the_architect.cli import _prompt_mode_selection

        expected = {
            "free": True,
            "persistent": False,
            "integrity": True,
            "token_budget_per_hour": 0,
        }
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch("the_architect.tui.screens.run_mode_selection", return_value=expected),
        ):
            result = _prompt_mode_selection()
        assert result == expected

    def test_tui_fast_path_back_sentinel_returns_none(self) -> None:
        """TUI mode returns None when user presses Back."""
        from the_architect.cli import _prompt_mode_selection
        from the_architect.tui.screens.pre_run import BACK_SENTINEL

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch("the_architect.tui.screens.run_mode_selection", return_value=BACK_SENTINEL),
        ):
            result = _prompt_mode_selection()
        assert result is None

    def test_tui_fast_path_system_exit_propagates(self) -> None:
        """SystemExit in TUI fast-path propagates unchanged."""
        from the_architect.cli import _prompt_mode_selection

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch("the_architect.tui.screens.run_mode_selection", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _prompt_mode_selection()
        assert exc_info.value.code == 0

    def test_tui_fast_path_exception_falls_back(self) -> None:
        """Exception in TUI fast-path falls back to prompt_toolkit."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_mode_selection

        expected = {
            "free": False,
            "persistent": False,
            "integrity": True,
            "token_budget_per_hour": 0,
        }
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch("the_architect.tui.screens.run_mode_selection", side_effect=RuntimeError("boom")),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_mode_selection()
        assert result == expected

    def test_prompt_toolkit_path_no_provider(self) -> None:
        """prompt_toolkit path with no provider shows all options."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_mode_selection

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_mode_selection()
        assert result is not None
        assert "free" in result
        assert "persistent" in result
        assert "integrity" in result

    def test_prompt_toolkit_path_provider_no_free(self) -> None:
        """prompt_toolkit path with provider that doesn't support free tier."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_mode_selection

        fake_provider = _make_fake_provider(supports_free=False)
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_mode_selection(provider=fake_provider)
        assert result is not None
        assert result["free"] is False

    def test_prompt_toolkit_path_provider_with_free(self) -> None:
        """prompt_toolkit path with provider that supports free tier."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_mode_selection

        fake_provider = _make_fake_provider(supports_free=True)
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_mode_selection(provider=fake_provider)
        assert result is not None
        assert result["free"] is False  # default unchecked

    def test_prompt_toolkit_budget_parsing_zero(self) -> None:
        """Budget text empty parses to 0."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_mode_selection

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_mode_selection()
        assert result["token_budget_per_hour"] == 0

    def test_prompt_toolkit_mode_defaults(self) -> None:
        """Default values: free=False, persistent=False, integrity=True."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_mode_selection

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_mode_selection()
        assert result["free"] is False
        assert result["persistent"] is False
        assert result["integrity"] is True

    def test_tui_fast_path_no_provider_defaults_show_free(self) -> None:
        """TUI with no provider defaults to showing free tier."""
        from the_architect.cli import _prompt_mode_selection

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch("the_architect.tui.screens.run_mode_selection") as mock_run,
        ):
            _prompt_mode_selection()
        mock_run.assert_called_once_with(show_free=True)

    def test_tui_fast_path_provider_no_free_hides_free(self) -> None:
        """TUI with provider that doesn't support free hides it."""
        from the_architect.cli import _prompt_mode_selection

        fake_provider = _make_fake_provider(supports_free=False)
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch("the_architect.tui.screens.run_mode_selection") as mock_run,
        ):
            _prompt_mode_selection(provider=fake_provider)
        mock_run.assert_called_once_with(show_free=False)


# ---------------------------------------------------------------------------
# _prompt_provider_selection
# ---------------------------------------------------------------------------


class TestPromptProviderSelection:
    """Coverage for _prompt_provider_selection (lines 979-1111)."""

    def test_tui_fast_path_returns_provider(self) -> None:
        """TUI mode returns the selected provider by index."""
        from the_architect.cli import _prompt_provider_selection

        p1 = _make_fake_provider("OpenCode")
        p2 = _make_fake_provider("Claude Code")
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch("the_architect.tui.screens.pre_run.run_provider_selection", return_value=1),
        ):
            result = _prompt_provider_selection([p1, p2])
        assert result is p2

    def test_tui_fast_path_back_sentinel_exits(self) -> None:
        """TUI mode raises SystemExit(0) on BACK_SENTINEL."""
        from the_architect.cli import _prompt_provider_selection
        from the_architect.tui.screens.pre_run import BACK_SENTINEL

        p1 = _make_fake_provider("OpenCode")
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch(
                "the_architect.tui.screens.pre_run.run_provider_selection",
                return_value=BACK_SENTINEL,
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _prompt_provider_selection([p1])
        assert exc_info.value.code == 0

    def test_tui_fast_path_system_exit_propagates(self) -> None:
        """SystemExit in TUI fast-path propagates unchanged."""
        from the_architect.cli import _prompt_provider_selection

        p1 = _make_fake_provider("OpenCode")
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch(
                "the_architect.tui.screens.pre_run.run_provider_selection",
                side_effect=SystemExit(0),
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _prompt_provider_selection([p1])
        assert exc_info.value.code == 0

    def test_tui_fast_path_exception_falls_back(self) -> None:
        """Exception in TUI fast-path falls back to prompt_toolkit."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_provider_selection

        p1 = _make_fake_provider("OpenCode")
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch(
                "the_architect.tui.screens.pre_run.run_provider_selection",
                side_effect=RuntimeError("boom"),
            ),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_provider_selection([p1])
        assert result is p1

    def test_prompt_toolkit_returns_first_provider(self) -> None:
        """prompt_toolkit path returns first provider by default."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_provider_selection

        p1 = _make_fake_provider("OpenCode")
        p2 = _make_fake_provider("Claude Code")
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_provider_selection([p1, p2])
        assert result is p1

    def test_prompt_toolkit_single_provider(self) -> None:
        """prompt_toolkit path with single provider returns it."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_provider_selection

        p1 = _make_fake_provider("OpenCode")
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_provider_selection([p1])
        assert result is p1

    def test_prompt_toolkit_version_display(self) -> None:
        """prompt_toolkit path pre-resolves provider versions."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_provider_selection

        p1 = _make_fake_provider("OpenCode")
        p1.get_version.return_value = "2.0.0"
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_provider_selection([p1])
        assert result is p1
        p1.get_version.assert_called()

    def test_prompt_toolkit_version_unknown_hidden(self) -> None:
        """prompt_toolkit path hides 'unknown' version strings."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_provider_selection

        p1 = _make_fake_provider("OpenCode")
        p1.get_version.return_value = "unknown"
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_provider_selection([p1])
        assert result is p1

    def test_prompt_toolkit_version_empty_hidden(self) -> None:
        """prompt_toolkit path hides empty version strings."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_provider_selection

        p1 = _make_fake_provider("OpenCode")
        p1.get_version.return_value = ""
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_provider_selection([p1])
        assert result is p1


# ---------------------------------------------------------------------------
# _prompt_goal
# ---------------------------------------------------------------------------


class TestPromptGoal:
    """Coverage for _prompt_goal (lines 2047-2072)."""

    def test_goal_returns_stripped_text(self) -> None:
        """Normal goal returns stripped text."""
        from the_architect.cli import _prompt_goal

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("the_architect.cli._prompt_text_input", return_value="  hello world  "),
            patch("the_architect.cli.console.print"),
        ):
            result = _prompt_goal()
        assert result == "hello world"

    def test_goal_none_cancels(self) -> None:
        """Goal returns None when _prompt_text_input returns None."""

        from the_architect.cli import _prompt_goal

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("the_architect.cli._prompt_text_input", return_value=None),
            patch("the_architect.cli.console.print") as mock_print,
        ):
            with pytest.raises(SystemExit) as exc_info:
                _prompt_goal()
        assert exc_info.value.code == 0
        mock_print.assert_called()

    def test_goal_empty_string_exits_with_code_1(self) -> None:
        """Empty goal string raises SystemExit(1)."""

        from the_architect.cli import _prompt_goal

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("the_architect.cli._prompt_text_input", return_value="   "),
            patch("the_architect.cli.console.print") as mock_print,
        ):
            with pytest.raises(SystemExit) as exc_info:
                _prompt_goal()
        assert exc_info.value.code == 1
        calls = [c for c in mock_print.call_args_list]
        assert any("No goal" in str(c) for c in calls)

    def test_goal_strips_whitespace(self) -> None:
        """Goal text is stripped of leading/trailing whitespace."""
        from the_architect.cli import _prompt_goal

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("the_architect.cli._prompt_text_input", return_value="  my goal  "),
            patch("the_architect.cli.console.print"),
        ):
            result = _prompt_goal()
        assert result == "my goal"


# ---------------------------------------------------------------------------
# _prompt_scope
# ---------------------------------------------------------------------------


class TestPromptScope:
    """Coverage for _prompt_scope (lines 2075-2117)."""

    def test_scope_returns_standard(self) -> None:
        """Normal scope selection returns the chosen scope."""
        from the_architect.cli import _prompt_scope

        with patch("questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = "standard"
            result = _prompt_scope()
        assert result == "standard"

    def test_scope_returns_simple(self) -> None:
        """Simple scope selection returns 'simple'."""
        from the_architect.cli import _prompt_scope

        with patch("questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = "simple"
            result = _prompt_scope()
        assert result == "simple"

    def test_scope_returns_complex(self) -> None:
        """Complex scope selection returns 'complex'."""
        from the_architect.cli import _prompt_scope

        with patch("questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = "complex"
            result = _prompt_scope()
        assert result == "complex"

    def test_scope_none_cancels(self) -> None:
        """Scope returns None (cancelled) raises SystemExit(0)."""
        from the_architect.cli import _prompt_scope

        with (
            patch("questionary.select") as mock_select,
            patch("the_architect.cli.console.print") as mock_print,
        ):
            mock_select.return_value.ask.return_value = None
            with pytest.raises(SystemExit) as exc_info:
                _prompt_scope()
        assert exc_info.value.code == 0
        mock_print.assert_called()

    def test_scope_uses_questionary_style(self) -> None:
        """Scope selection uses the Architect questionary style."""
        from the_architect.cli import _prompt_scope

        with patch("questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = "standard"
            _prompt_scope()
        # Verify style was passed
        call_kwargs = mock_select.call_args[1]
        assert "style" in call_kwargs
        assert "pointer" in call_kwargs
        assert call_kwargs["pointer"] == "\u203a"


# ---------------------------------------------------------------------------
# _prompt_architect_model
# ---------------------------------------------------------------------------


class TestPromptArchitectModel:
    """Coverage for _prompt_architect_model (lines 2120-2232)."""

    def test_tui_fast_path_returns_model(self, tmp_path: Path) -> None:
        """TUI mode returns the selected model."""
        from the_architect.cli import _prompt_architect_model

        fake_provider = _make_fake_provider()
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch("the_architect.tui.screens.pre_run.run_model_picker", return_value="model-b"),
        ):
            result = _prompt_architect_model(tmp_path, provider=fake_provider)
        assert result == "model-b"

    def test_tui_fast_path_back_sentinel_returns_none(self, tmp_path: Path) -> None:
        """TUI mode returns None on BACK_SENTINEL."""
        from the_architect.cli import _prompt_architect_model
        from the_architect.tui.screens.pre_run import BACK_SENTINEL

        fake_provider = _make_fake_provider()
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch("the_architect.tui.screens.pre_run.run_model_picker", return_value=BACK_SENTINEL),
        ):
            result = _prompt_architect_model(tmp_path, provider=fake_provider)
        assert result is None

    def test_tui_fast_path_system_exit_propagates(self, tmp_path: Path) -> None:
        """SystemExit in TUI fast-path propagates."""
        from the_architect.cli import _prompt_architect_model

        fake_provider = _make_fake_provider()
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch("the_architect.tui.screens.pre_run.run_model_picker", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _prompt_architect_model(tmp_path, provider=fake_provider)
        assert exc_info.value.code == 0

    def test_tui_fast_path_exception_falls_back(self, tmp_path: Path) -> None:
        """Exception in TUI fast-path falls back to questionary."""
        from the_architect.cli import _prompt_architect_model

        fake_provider = _make_fake_provider()
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch(
                "the_architect.tui.screens.pre_run.run_model_picker",
                side_effect=RuntimeError("boom"),
            ),
            patch("questionary.select") as mock_select,
        ):
            mock_select.return_value.ask.return_value = "model-b"
            result = _prompt_architect_model(tmp_path, provider=fake_provider)
        assert result == "model-b"

    def test_no_models_no_current_falls_back_to_text_input(self, tmp_path: Path) -> None:
        """No models and no current model falls back to free text."""
        from the_architect.cli import _prompt_architect_model

        fake_provider = _make_fake_provider()
        fake_provider.list_models.return_value = []
        fake_provider.get_resolved_model.return_value = ""
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("the_architect.cli._prompt_text_input", return_value="custom-model"),
            patch("the_architect.cli.console.print"),
        ):
            result = _prompt_architect_model(tmp_path, provider=fake_provider)
        assert result == "custom-model"

    def test_no_models_text_input_empty_returns_none(self, tmp_path: Path) -> None:
        """Free text input empty returns None."""
        from the_architect.cli import _prompt_architect_model

        fake_provider = _make_fake_provider()
        fake_provider.list_models.return_value = []
        fake_provider.get_resolved_model.return_value = ""
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("the_architect.cli._prompt_text_input", return_value=""),
            patch("the_architect.cli.console.print"),
        ):
            result = _prompt_architect_model(tmp_path, provider=fake_provider)
        assert result is None

    def test_questionary_returns_provider_default(self, tmp_path: Path) -> None:
        """Questionary selection of empty string returns current model."""
        from the_architect.cli import _prompt_architect_model

        fake_provider = _make_fake_provider()
        fake_provider.get_resolved_model.return_value = "model-a"
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("questionary.select") as mock_select,
            patch("the_architect.cli.console.print"),
        ):
            mock_select.return_value.ask.return_value = ""
            result = _prompt_architect_model(tmp_path, provider=fake_provider)
        assert result == "model-a"

    def test_questionary_returns_selected_model(self, tmp_path: Path) -> None:
        """Questionary selection returns the chosen model."""
        from the_architect.cli import _prompt_architect_model

        fake_provider = _make_fake_provider()
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("questionary.select") as mock_select,
            patch("the_architect.cli.console.print"),
        ):
            mock_select.return_value.ask.return_value = "model-b"
            result = _prompt_architect_model(tmp_path, provider=fake_provider)
        assert result == "model-b"

    def test_questionary_none_cancels(self, tmp_path: Path) -> None:
        """Questionary selection None raises SystemExit(0)."""
        from the_architect.cli import _prompt_architect_model

        fake_provider = _make_fake_provider()
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("questionary.select") as mock_select,
            patch("the_architect.cli.console.print") as mock_print,
        ):
            mock_select.return_value.ask.return_value = None
            with pytest.raises(SystemExit) as exc_info:
                _prompt_architect_model(tmp_path, provider=fake_provider)
        assert exc_info.value.code == 0
        mock_print.assert_called()

    def test_no_provider_auto_detects(self, tmp_path: Path) -> None:
        """When provider is None, detect_provider is called."""
        from the_architect.cli import _prompt_architect_model

        fake_provider = _make_fake_provider()
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("the_architect.cli.detect_provider", return_value=fake_provider),
            patch("questionary.select") as mock_select,
            patch("the_architect.cli.console.print"),
        ):
            mock_select.return_value.ask.return_value = ""
            result = _prompt_architect_model(tmp_path)
        assert result == "model-a"

    def test_current_model_at_top_of_list(self, tmp_path: Path) -> None:
        """Current model is placed at the top of the choices list."""
        from the_architect.cli import _prompt_architect_model

        fake_provider = _make_fake_provider()
        fake_provider.list_models.return_value = ["model-a", "model-b", "model-c"]
        fake_provider.get_resolved_model.return_value = "model-b"
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("questionary.select") as mock_select,
            patch("the_architect.cli.console.print"),
        ):
            mock_select.return_value.ask.return_value = "model-b"
            _prompt_architect_model(tmp_path, provider=fake_provider)
        # Verify the first choice has "model-b" as value (current model first)
        choices = mock_select.call_args[1]["choices"]
        assert choices[0].value == "model-b"

    def test_current_model_not_in_list_added_first(self, tmp_path: Path) -> None:
        """Current model not in list is still placed first."""
        from the_architect.cli import _prompt_architect_model

        fake_provider = _make_fake_provider()
        fake_provider.list_models.return_value = ["model-a", "model-b"]
        fake_provider.get_resolved_model.return_value = "model-c"
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("questionary.select") as mock_select,
            patch("the_architect.cli.console.print"),
        ):
            mock_select.return_value.ask.return_value = "model-c"
            _prompt_architect_model(tmp_path, provider=fake_provider)
        choices = mock_select.call_args[1]["choices"]
        assert choices[0].value == "model-c"

    def test_models_only_no_current(self, tmp_path: Path) -> None:
        """Models without current model still builds choices correctly."""
        from the_architect.cli import _prompt_architect_model

        fake_provider = _make_fake_provider()
        fake_provider.list_models.return_value = ["model-a", "model-b"]
        fake_provider.get_resolved_model.return_value = ""
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("questionary.select") as mock_select,
            patch("the_architect.cli.console.print"),
        ):
            mock_select.return_value.ask.return_value = "model-a"
            result = _prompt_architect_model(tmp_path, provider=fake_provider)
        assert result == "model-a"

    def test_models_and_current_both_empty_falls_back(self, tmp_path: Path) -> None:
        """Both models and current empty falls back to text input."""
        from the_architect.cli import _prompt_architect_model

        fake_provider = _make_fake_provider()
        fake_provider.list_models.return_value = []
        fake_provider.get_resolved_model.return_value = ""
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("the_architect.cli._prompt_text_input", return_value="my-model"),
            patch("the_architect.cli.console.print"),
        ):
            result = _prompt_architect_model(tmp_path, provider=fake_provider)
        assert result == "my-model"


# ---------------------------------------------------------------------------
# _prompt_exec_agent
# ---------------------------------------------------------------------------


class TestPromptExecAgent:
    """Coverage for _prompt_exec_agent (lines 2235-2299)."""

    def test_no_provider_auto_detects(self, tmp_path: Path) -> None:
        """When provider is None, detect_provider is called."""
        from the_architect.cli import _prompt_exec_agent

        fake_provider = _make_fake_provider()
        with (
            patch("the_architect.cli.detect_provider", return_value=fake_provider),
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("questionary.select") as mock_select,
            patch("the_architect.cli.console.print"),
        ):
            mock_select.return_value.ask.return_value = ""
            result = _prompt_exec_agent(tmp_path)
        assert result == ""

    def test_provider_no_agent_support_returns_empty(self, tmp_path: Path) -> None:
        """Provider without agent support returns empty string."""
        from the_architect.cli import _prompt_exec_agent

        fake_provider = _make_fake_provider(supports_agents=False)
        result = _prompt_exec_agent(tmp_path, provider=fake_provider)
        assert result == ""

    def test_provider_no_agents_returns_empty(self, tmp_path: Path) -> None:
        """Provider with no agents returns empty string."""
        from the_architect.cli import _prompt_exec_agent

        fake_provider = _make_fake_provider()
        fake_provider.list_agents.return_value = []
        result = _prompt_exec_agent(tmp_path, provider=fake_provider)
        assert result == ""

    def test_tui_fast_path_returns_agent(self, tmp_path: Path) -> None:
        """TUI mode returns the selected agent."""
        from the_architect.cli import _prompt_exec_agent

        fake_provider = _make_fake_provider()
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch("the_architect.tui.screens.pre_run.run_agent_picker", return_value="agent-b"),
        ):
            result = _prompt_exec_agent(tmp_path, provider=fake_provider)
        assert result == "agent-b"

    def test_tui_fast_path_back_sentinel_returns_none(self, tmp_path: Path) -> None:
        """TUI mode returns None on BACK_SENTINEL."""
        from the_architect.cli import _prompt_exec_agent
        from the_architect.tui.screens.pre_run import BACK_SENTINEL

        fake_provider = _make_fake_provider()
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch("the_architect.tui.screens.pre_run.run_agent_picker", return_value=BACK_SENTINEL),
        ):
            result = _prompt_exec_agent(tmp_path, provider=fake_provider)
        assert result is None

    def test_tui_fast_path_system_exit_propagates(self, tmp_path: Path) -> None:
        """SystemExit in TUI fast-path propagates."""
        from the_architect.cli import _prompt_exec_agent

        fake_provider = _make_fake_provider()
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch("the_architect.tui.screens.pre_run.run_agent_picker", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _prompt_exec_agent(tmp_path, provider=fake_provider)
        assert exc_info.value.code == 0

    def test_tui_fast_path_exception_falls_back(self, tmp_path: Path) -> None:
        """Exception in TUI fast-path falls back to questionary."""
        from the_architect.cli import _prompt_exec_agent

        fake_provider = _make_fake_provider()
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch(
                "the_architect.tui.screens.pre_run.run_agent_picker",
                side_effect=RuntimeError("boom"),
            ),
            patch("questionary.select") as mock_select,
            patch("the_architect.cli.console.print"),
        ):
            mock_select.return_value.ask.return_value = "agent-a"
            result = _prompt_exec_agent(tmp_path, provider=fake_provider)
        assert result == "agent-a"

    def test_questionary_returns_selected_agent(self, tmp_path: Path) -> None:
        """Questionary returns the selected agent."""
        from the_architect.cli import _prompt_exec_agent

        fake_provider = _make_fake_provider()
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("questionary.select") as mock_select,
            patch("the_architect.cli.console.print"),
        ):
            mock_select.return_value.ask.return_value = "agent-a"
            result = _prompt_exec_agent(tmp_path, provider=fake_provider)
        assert result == "agent-a"

    def test_questionary_none_cancels(self, tmp_path: Path) -> None:
        """Questionary None raises SystemExit(0)."""
        from the_architect.cli import _prompt_exec_agent

        fake_provider = _make_fake_provider()
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("questionary.select") as mock_select,
            patch("the_architect.cli.console.print") as mock_print,
        ):
            mock_select.return_value.ask.return_value = None
            with pytest.raises(SystemExit) as exc_info:
                _prompt_exec_agent(tmp_path, provider=fake_provider)
        assert exc_info.value.code == 0
        mock_print.assert_called()


# ---------------------------------------------------------------------------
# _prompt_resume_screen
# ---------------------------------------------------------------------------


class TestPromptResumeScreen:
    """Coverage for _prompt_resume_screen (lines 1740-2039)."""

    def test_tui_fast_path_returns_result(self, tmp_path: Path) -> None:
        """TUI mode returns the dict from run_resume_screen."""
        from the_architect.cli import _prompt_resume_screen
        from the_architect.core.tasks import Task, TaskStatus

        config = ArchitectConfig()
        tasks = [
            Task(
                name="T01_test",
                prefix="T01",
                number=1,
                path=tmp_path / "T01_test.md",
                status=TaskStatus.PENDING,
            ),
        ]
        expected = {
            "free": False,
            "persistent": True,
            "integrity": True,
            "token_budget_per_hour": 0,
            "action": "execute",
        }
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch("the_architect.tui.screens.run_resume_screen", return_value=expected),
        ):
            result = _prompt_resume_screen(tasks, config)
        assert result == expected

    def test_tui_fast_path_system_exit_propagates(self, tmp_path: Path) -> None:
        """SystemExit in TUI fast-path propagates."""
        from the_architect.cli import _prompt_resume_screen
        from the_architect.core.tasks import Task, TaskStatus

        config = ArchitectConfig()
        tasks = [
            Task(
                name="T01_test",
                prefix="T01",
                number=1,
                path=tmp_path / "T01_test.md",
                status=TaskStatus.PENDING,
            ),
        ]
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch("the_architect.tui.screens.run_resume_screen", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _prompt_resume_screen(tasks, config)
        assert exc_info.value.code == 0

    def test_tui_fast_path_exception_falls_back(self, tmp_path: Path) -> None:
        """Exception in TUI fast-path falls back to prompt_toolkit."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_resume_screen
        from the_architect.core.tasks import Task, TaskStatus

        config = ArchitectConfig()
        tasks = [
            Task(
                name="T01_test",
                prefix="T01",
                number=1,
                path=tmp_path / "T01_test.md",
                status=TaskStatus.PENDING,
            ),
        ]
        fake_provider = _make_fake_provider()
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch("the_architect.tui.screens.run_resume_screen", side_effect=RuntimeError("boom")),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_resume_screen(tasks, config, provider=fake_provider)
        assert result is not None
        assert "action" in result

    def test_prompt_toolkit_path_no_provider(self, tmp_path: Path) -> None:
        """prompt_toolkit path with no provider shows all options."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_resume_screen
        from the_architect.core.tasks import Task, TaskStatus

        config = ArchitectConfig()
        tasks = [
            Task(
                name="T01_test",
                prefix="T01",
                number=1,
                path=tmp_path / "T01_test.md",
                status=TaskStatus.PENDING,
            ),
        ]
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_resume_screen(tasks, config)
        assert result is not None
        assert "free" in result
        assert "persistent" in result
        assert "integrity" in result
        assert "action" in result

    def test_prompt_toolkit_path_provider_no_free(self, tmp_path: Path) -> None:
        """prompt_toolkit path with provider that doesn't support free tier."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_resume_screen
        from the_architect.core.tasks import Task, TaskStatus

        config = ArchitectConfig()
        tasks = [
            Task(
                name="T01_test",
                prefix="T01",
                number=1,
                path=tmp_path / "T01_test.md",
                status=TaskStatus.PENDING,
            ),
        ]
        fake_provider = _make_fake_provider(supports_free=False)
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_resume_screen(tasks, config, provider=fake_provider)
        assert result["free"] is False

    def test_prompt_toolkit_path_provider_with_free(self, tmp_path: Path) -> None:
        """prompt_toolkit path with provider that supports free tier."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_resume_screen
        from the_architect.core.tasks import Task, TaskStatus

        config = ArchitectConfig()
        tasks = [
            Task(
                name="T01_test",
                prefix="T01",
                number=1,
                path=tmp_path / "T01_test.md",
                status=TaskStatus.PENDING,
            ),
        ]
        fake_provider = _make_fake_provider(supports_free=True)
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_resume_screen(tasks, config, provider=fake_provider)
        assert result is not None

    def test_prompt_toolkit_defaults(self, tmp_path: Path) -> None:
        """prompt_toolkit path defaults: free from config, persistent from config."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_resume_screen
        from the_architect.core.tasks import Task, TaskStatus

        config = ArchitectConfig()
        config.free_mode = True
        config.persistent = True
        config.integrity = False
        config.token_budget_per_hour = 5000
        tasks = [
            Task(
                name="T01_test",
                prefix="T01",
                number=1,
                path=tmp_path / "T01_test.md",
                status=TaskStatus.PENDING,
            ),
        ]
        fake_provider = _make_fake_provider(supports_free=True)
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_resume_screen(tasks, config, provider=fake_provider)
        assert result["free"] is True
        assert result["persistent"] is True
        assert result["integrity"] is False

    def test_prompt_toolkit_action_execute_default(self, tmp_path: Path) -> None:
        """prompt_toolkit path defaults action to 'execute'."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_resume_screen
        from the_architect.core.tasks import Task, TaskStatus

        config = ArchitectConfig()
        tasks = [
            Task(
                name="T01_test",
                prefix="T01",
                number=1,
                path=tmp_path / "T01_test.md",
                status=TaskStatus.PENDING,
            ),
        ]
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_resume_screen(tasks, config)
        assert result["action"] == "execute"

    def test_prompt_toolkit_budget_from_config(self, tmp_path: Path) -> None:
        """prompt_toolkit path pre-fills budget from config."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_resume_screen
        from the_architect.core.tasks import Task, TaskStatus

        config = ArchitectConfig()
        config.token_budget_per_hour = 10000
        tasks = [
            Task(
                name="T01_test",
                prefix="T01",
                number=1,
                path=tmp_path / "T01_test.md",
                status=TaskStatus.PENDING,
            )
        ]
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_resume_screen(tasks, config)
        assert result["token_budget_per_hour"] == 10000  # pre-filled from config


# ---------------------------------------------------------------------------
# _collect_planning_prompts
# ---------------------------------------------------------------------------
# _collect_planning_prompts
# ---------------------------------------------------------------------------


class TestCollectPlanningPrompts:
    """Coverage for _collect_planning_prompts (lines 2661-2941)."""

    def test_headless_returns_values(self, tmp_path: Path) -> None:
        """Headless mode returns provided values unchanged."""
        from the_architect.cli import _collect_planning_prompts

        config = ArchitectConfig()
        result = _collect_planning_prompts(
            project=tmp_path,
            config=config,
            headless=True,
            goal_text="My goal",
            scope_text="simple",
            architect_model_override="gpt-4",
            execution_model_override="build",
        )
        assert result[0] == "My goal"
        assert result[1] == "simple"
        assert result[2] == "gpt-4"
        assert result[3] == "build"

    def test_headless_empty_exec_model_returns_none(self, tmp_path: Path) -> None:
        """Headless mode returns None for empty execution_model_override."""
        from the_architect.cli import _collect_planning_prompts

        config = ArchitectConfig()
        result = _collect_planning_prompts(
            project=tmp_path,
            config=config,
            headless=True,
            goal_text="My goal",
            scope_text="",
            architect_model_override="",
            execution_model_override="",
        )
        assert result[0] == "My goal"
        assert result[3] is None

    def test_headless_clears_infinite_loop(self, tmp_path: Path) -> None:
        """Headless mode clears infinite loop flag."""
        from the_architect.cli import _collect_planning_prompts

        config = ArchitectConfig()
        _collect_planning_prompts(
            project=tmp_path,
            config=config,
            headless=True,
        )
        assert config._infinite_loop_enabled is False

    def test_pending_tasks_tui_cancel(self, tmp_path: Path) -> None:
        """Pending tasks TUI screen returns False → SystemExit(0)."""
        from the_architect.cli import _collect_planning_prompts

        config = ArchitectConfig()
        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch("the_architect.cli.check_pending_tasks", return_value=["T01_test"]),
            patch(
                "the_architect.tui.screens.pre_run.run_pending_tasks_screen",
                return_value=False,
            ),
            patch("the_architect.cli.detect_provider"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _collect_planning_prompts(
                    project=tmp_path,
                    config=config,
                    headless=False,
                )
        assert exc_info.value.code == 0

    def test_tui_tabbed_screen_returns_result(self, tmp_path: Path) -> None:
        """TUI mode with provider shows tabbed pre-run screen."""
        from the_architect.cli import _collect_planning_prompts

        config = ArchitectConfig()
        fake_provider = _make_fake_provider()
        mock_result = MagicMock()
        mock_result.goal = "Tabbed goal"
        mock_result.scope = "complex"
        mock_result.architect_model = "claude-sonnet"
        mock_result.execution_agent = "agent-a"

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch("the_architect.cli.check_pending_tasks", return_value=[]),
            patch("the_architect.cli.detect_available_providers", return_value=[fake_provider]),
            patch(
                "the_architect.tui.screens.pre_run_tabbed.run_pre_run_tabbed",
                return_value=mock_result,
            ),
            patch("the_architect.cli._check_provider_update_before_model_work"),
            patch("the_architect.cli.write_config"),
        ):
            result = _collect_planning_prompts(
                project=tmp_path,
                config=config,
                headless=False,
                provider=fake_provider,
            )
        assert result[0] == "Tabbed goal"
        assert result[1] == "complex"
        assert result[2] == "claude-sonnet"
        assert result[3] == "agent-a"


# ---------------------------------------------------------------------------
# _prompt_update_action
# ---------------------------------------------------------------------------


class TestPromptUpdateAction:
    """Coverage for _prompt_update_action (lines 1114-1206)."""

    def test_tui_fast_path_returns_result(self) -> None:
        """TUI mode returns result from run_update_action_screen."""
        from the_architect.cli import _prompt_update_action

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch(
                "the_architect.tui.screens.pre_run.run_update_action_screen",
                return_value="update",
            ),
        ):
            result = _prompt_update_action("update msg", "pip install -U")
        assert result == "update"

    def test_tui_fast_path_exception_falls_back(self) -> None:
        """Exception in TUI fast-path falls back to prompt_toolkit."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_update_action

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch(
                "the_architect.tui.screens.pre_run.run_update_action_screen",
                side_effect=RuntimeError("boom"),
            ),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_update_action("update msg", "pip install -U")
        assert result == "exit"  # default

    def test_prompt_toolkit_default_exit(self) -> None:
        """prompt_toolkit path defaults to 'exit' result."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_update_action

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_update_action("update msg", "pip install -U")
        assert result == "exit"


# ---------------------------------------------------------------------------
# _prompt_self_update_action
# ---------------------------------------------------------------------------


class TestPromptSelfUpdateAction:
    """Coverage for _prompt_self_update_action (lines 1361-1439)."""

    def test_tui_fast_path_returns_result(self) -> None:
        """TUI mode returns result from run_self_update_screen."""
        from the_architect.cli import _prompt_self_update_action

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch(
                "the_architect.tui.screens.pre_run.run_self_update_screen",
                return_value="update",
            ),
        ):
            result = _prompt_self_update_action("1.0.0", "2.0.0")
        assert result == "update"

    def test_tui_fast_path_exception_falls_back(self) -> None:
        """Exception in TUI fast-path falls back to prompt_toolkit."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_self_update_action

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch(
                "the_architect.tui.screens.pre_run.run_self_update_screen",
                side_effect=RuntimeError("boom"),
            ),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_self_update_action("1.0.0", "2.0.0")
        assert result == "continue"  # default

    def test_prompt_toolkit_default_continue(self) -> None:
        """prompt_toolkit path defaults to 'continue' result."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_self_update_action

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            result = _prompt_self_update_action("1.0.0", "2.0.0")
        assert result == "continue"


# ---------------------------------------------------------------------------
# _prompt_provider_issue_warning
# ---------------------------------------------------------------------------


class TestPromptProviderIssueWarning:
    """Regression coverage for the persistent-run hang (provider health warning).

    See documentation/PRACTICES.md history / bug report: a background worker
    thread calling this function while a live ArchitectAppRunner owns the
    terminal must never block on a raw prompt_toolkit Application.run().
    """

    def test_tui_fast_path_uses_screen(self) -> None:
        """TUI mode routes through run_provider_issue_screen, not console.print."""
        from the_architect.cli import _prompt_provider_issue_warning

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch(
                "the_architect.tui.screens.pre_run.run_provider_issue_screen",
                return_value="continue",
            ) as run_screen,
        ):
            _prompt_provider_issue_warning("boom")
        run_screen.assert_called_once_with("boom")

    def test_tui_fast_path_exception_falls_back(self) -> None:
        """Exception in the TUI screen path falls back, still guarded by active_runner."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_provider_issue_warning

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=True),
            patch(
                "the_architect.tui.screens.pre_run.run_provider_issue_screen",
                side_effect=RuntimeError("boom"),
            ),
            patch("the_architect.tui.runner.active_runner", return_value=None),
            patch("prompt_toolkit.application.application.Application.run", return_value=None),
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            _prompt_provider_issue_warning("boom")  # must not raise / hang

    def test_no_tui_no_runner_uses_raw_prompt_toolkit(self) -> None:
        """No TUI, no live runner: falls through to the raw prompt_toolkit app."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_provider_issue_warning

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("the_architect.tui.runner.active_runner", return_value=None),
            patch(
                "prompt_toolkit.application.application.Application.run", return_value=None
            ) as app_run,
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            _prompt_provider_issue_warning("boom")
        app_run.assert_called_once()

    def test_live_runner_skips_blocking_prompt_toolkit_app(self) -> None:
        """CRITICAL regression: a live runner must never boot a blocking raw app.

        Simulates a persistent/infinite-loop worker thread calling this
        function after ARCHITECT_TUI is no longer set to True, while a
        Textual ArchitectApp still owns the terminal. Must degrade to a
        non-blocking console.print rather than calling Application.run(),
        which previously hung forever waiting for a keypress nobody could
        provide.
        """
        from the_architect.cli import _prompt_provider_issue_warning

        fake_runner = MagicMock()

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch("the_architect.tui.runner.active_runner", return_value=fake_runner),
            patch(
                "prompt_toolkit.application.application.Application.run", return_value=None
            ) as app_run,
        ):
            _prompt_provider_issue_warning("provider timed out")

        app_run.assert_not_called()

    def test_import_error_falls_back_to_raw_prompt_toolkit(self) -> None:
        """If the runner module cannot be imported, fall through safely (main thread only)."""
        from prompt_toolkit.output import DummyOutput

        from the_architect.cli import _prompt_provider_issue_warning

        with (
            patch("the_architect.cli._tui_mode_enabled", return_value=False),
            patch.dict("sys.modules", {"the_architect.tui.runner": None}),
            patch(
                "prompt_toolkit.application.application.Application.run", return_value=None
            ) as app_run,
            patch("the_architect.cli._get_prompt_toolkit_output", return_value=DummyOutput()),
        ):
            _prompt_provider_issue_warning("boom")
        app_run.assert_called_once()
