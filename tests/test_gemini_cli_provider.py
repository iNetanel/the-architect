"""Tests for the Gemini CLI provider implementation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from the_architect.core.gemini_cli_provider import _FALLBACK_GEMINI_MODELS, GeminiCliProvider
from the_architect.core.runner import TokenUsage

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class TestGeminiCliProviderIdentity:
    """Tests for GeminiCliProvider identity properties."""

    def test_name(self) -> None:
        assert GeminiCliProvider().name == "gemini-cli"

    def test_display_name(self) -> None:
        assert GeminiCliProvider().display_name == "Gemini CLI"

    def test_binary_name(self) -> None:
        assert GeminiCliProvider().binary_name == "gemini"

    def test_supports_agents_false(self) -> None:
        assert GeminiCliProvider().supports_agents() is False

    def test_supports_json_output_true(self) -> None:
        assert GeminiCliProvider().supports_json_output() is True

    def test_supports_free_tier_false(self) -> None:
        assert GeminiCliProvider().supports_free_tier() is False


# ---------------------------------------------------------------------------
# Installation checks
# ---------------------------------------------------------------------------


class TestGeminiCliProviderInstallation:
    """Tests for GeminiCliProvider installation detection."""

    def test_is_installed_returns_bool(self) -> None:
        result = GeminiCliProvider().is_installed()
        assert isinstance(result, bool)

    def test_not_installed_when_binary_missing(self) -> None:
        with patch("shutil.which", return_value=None):
            assert GeminiCliProvider().is_installed() is False

    def test_installed_when_binary_found(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            assert GeminiCliProvider().is_installed() is True

    def test_get_version_returns_string(self) -> None:
        result = GeminiCliProvider().get_version()
        assert isinstance(result, str)

    def test_get_version_caches_result(self) -> None:
        provider = GeminiCliProvider()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "gemini/1.2.3"
        with patch(
            "the_architect.core.gemini_cli_provider.subprocess.run",
            return_value=mock_result,
        ) as mock_run:
            provider.get_version()
            provider.get_version()
        assert mock_run.call_count == 1

    def test_has_any_models_false_when_not_installed(self) -> None:
        with patch("shutil.which", return_value=None):
            assert GeminiCliProvider().has_any_models() is False

    def test_install_hint_returns_string(self) -> None:
        hint = GeminiCliProvider().install_hint()
        assert isinstance(hint, str)
        assert len(hint) > 0

    def test_install_hint_contains_gemini(self) -> None:
        hint = GeminiCliProvider().install_hint()
        assert "gemini" in hint.lower()

    def test_install_hint_contains_npm_or_url(self) -> None:
        hint = GeminiCliProvider().install_hint()
        assert "npm" in hint or "https" in hint


# ---------------------------------------------------------------------------
# Command building
# ---------------------------------------------------------------------------


class TestGeminiCliProviderCommandBuilding:
    """Tests for GeminiCliProvider command building."""

    def test_build_command_basic(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            cmd = GeminiCliProvider().build_command("do something")
        assert "-p" in cmd
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "--yolo" in cmd
        assert "do something" in cmd

    def test_build_command_with_model(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            cmd = GeminiCliProvider().build_command("task", model_override="gemini-2.5-pro")
        assert "--model" in cmd
        assert "gemini-2.5-pro" in cmd

    def test_build_command_without_model(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            cmd = GeminiCliProvider().build_command("task")
        assert "--model" not in cmd

    def test_build_command_ignores_agent_override(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            cmd = GeminiCliProvider().build_command("task", agent_override="master")
        assert "--agent" not in cmd


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------


class TestGeminiCliProviderOutputParsing:
    """Tests for GeminiCliProvider JSONL output parsing."""

    def test_returns_none_for_empty_line(self) -> None:
        result = GeminiCliProvider().parse_output_line("")
        assert result is None

    def test_returns_none_for_whitespace_only(self) -> None:
        result = GeminiCliProvider().parse_output_line("   ")
        assert result is None

    def test_init_event_silent_and_caches_model(self) -> None:
        provider = GeminiCliProvider()
        line = json.dumps({"type": "init", "model": "gemini-2.5-pro"})
        result = provider.parse_output_line(line)
        assert result is not None
        assert result.display_lines == []
        assert provider._resolved_model_cache["_stream"] == "gemini-2.5-pro"

    def test_init_event_caches_model_from_session(self) -> None:
        provider = GeminiCliProvider()
        line = json.dumps({"type": "init", "session": {"model": "gemini-2.5-flash"}})
        result = provider.parse_output_line(line)
        assert result is not None
        assert provider._resolved_model_cache["_stream"] == "gemini-2.5-flash"

    def test_init_event_no_model(self) -> None:
        provider = GeminiCliProvider()
        line = json.dumps({"type": "init"})
        result = provider.parse_output_line(line)
        assert result is not None
        assert result.display_lines == []
        assert "_stream" not in provider._resolved_model_cache

    def test_message_event_model_role(self) -> None:
        line = json.dumps({"type": "message", "role": "model", "content": "Hello!"})
        result = GeminiCliProvider().parse_output_line(line)
        assert result is not None
        assert "Hello!" in result.display_lines

    def test_message_event_user_role_silent(self) -> None:
        line = json.dumps({"type": "message", "role": "user", "content": "prompt"})
        result = GeminiCliProvider().parse_output_line(line)
        assert result is not None
        assert result.display_lines == []

    def test_message_event_text_field(self) -> None:
        line = json.dumps({"type": "message", "role": "model", "text": "Response text"})
        result = GeminiCliProvider().parse_output_line(line)
        assert result is not None
        assert "Response text" in result.display_lines

    def test_tool_use_event(self) -> None:
        line = json.dumps({"type": "tool_use", "name": "read_file"})
        result = GeminiCliProvider().parse_output_line(line)
        assert result is not None
        assert "\u2192 read_file" in result.display_lines

    def test_tool_use_event_tool_name_field(self) -> None:
        line = json.dumps({"type": "tool_use", "tool_name": "write_file"})
        result = GeminiCliProvider().parse_output_line(line)
        assert result is not None
        assert "\u2192 write_file" in result.display_lines

    def test_tool_result_event_silent(self) -> None:
        line = json.dumps({"type": "tool_result", "output": "file contents"})
        result = GeminiCliProvider().parse_output_line(line)
        assert result is not None
        assert result.display_lines == []

    def test_result_event_with_usage(self) -> None:
        line = json.dumps(
            {
                "type": "result",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cached_input_tokens": 20,
                },
            }
        )
        result = GeminiCliProvider().parse_output_line(line)
        assert result is not None
        assert result.tokens is not None
        tokens = cast(TokenUsage, result.tokens)
        assert tokens.input_tokens == 100
        assert tokens.output_tokens == 50
        assert tokens.cache_read_tokens == 20
        assert tokens.cache_write_tokens == 0

    def test_error_event_extracts_message(self) -> None:
        line = json.dumps({"type": "error", "message": "API error"})
        result = GeminiCliProvider().parse_output_line(line)
        assert result is not None
        assert "Error: API error" in result.display_lines

    def test_error_event_error_field(self) -> None:
        line = json.dumps({"type": "error", "error": "Something failed"})
        result = GeminiCliProvider().parse_output_line(line)
        assert result is not None
        assert "Error: Something failed" in result.display_lines

    def test_rate_limit_in_error_event(self) -> None:
        line = json.dumps({"type": "error", "message": "rate limit exceeded"})
        result = GeminiCliProvider().parse_output_line(line)
        assert result is not None
        assert result.rate_limit is True

    def test_rate_limit_in_plain_text(self) -> None:
        result = GeminiCliProvider().parse_output_line("Error: rate limit exceeded")
        assert result is not None
        assert result.rate_limit is True
        assert result.event_type == "text"

    def test_model_not_found_in_plain_text(self) -> None:
        result = GeminiCliProvider().parse_output_line("Error: model not found")
        assert result is not None
        assert result.model_not_found is True

    def test_plain_text_fallback(self) -> None:
        result = GeminiCliProvider().parse_output_line("I wrote the file.")
        assert result is not None
        assert result.event_type == "text"
        assert "I wrote the file." in result.display_lines

    def test_normal_text_no_flags(self) -> None:
        result = GeminiCliProvider().parse_output_line("All tests pass.")
        assert result is not None
        assert result.rate_limit is False
        assert result.model_not_found is False

    def test_no_tokens_for_plain_text(self) -> None:
        result = GeminiCliProvider().parse_output_line("some output")
        assert result is not None
        assert result.tokens is None

    def test_unrecognised_event_type(self) -> None:
        line = json.dumps({"type": "custom_event"})
        result = GeminiCliProvider().parse_output_line(line)
        assert result is not None
        assert result.event_type == "custom_event"

    def test_init_model_caches_for_get_resolved_model(self) -> None:
        provider = GeminiCliProvider()
        line = json.dumps({"type": "init", "model": "gemini-2.5-pro"})
        provider.parse_output_line(line)
        assert provider.get_resolved_model(Path("/tmp")) == "gemini-2.5-pro"


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------


class TestGeminiCliProviderModelResolution:
    """Tests for GeminiCliProvider model resolution."""

    def test_list_models_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-pro")
        models = GeminiCliProvider().list_models()
        assert models == ["gemini-2.5-pro"]

    def test_list_models_from_bundle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_models uses the local Gemini CLI bundle as the authoritative source."""
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        with patch(
            "the_architect.core.gemini_cli_provider._extract_models_from_gemini_bundle",
            return_value=["gemini-2.5-flash", "gemini-2.5-pro"],
        ):
            models = GeminiCliProvider().list_models()
        assert models == ["gemini-2.5-flash", "gemini-2.5-pro"]

    def test_list_models_from_settings_json_when_bundle_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Falls back to settings.json model when bundle extraction returns nothing."""
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        with (
            patch(
                "the_architect.core.gemini_cli_provider._extract_models_from_gemini_bundle",
                return_value=[],
            ),
            patch(
                "the_architect.core.gemini_cli_provider._read_gemini_settings_model",
                return_value="gemini-2.5-flash",
            ),
        ):
            models = GeminiCliProvider().list_models()
        assert models == ["gemini-2.5-flash"]

    def test_list_models_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        with (
            patch(
                "the_architect.core.gemini_cli_provider._extract_models_from_gemini_bundle",
                return_value=[],
            ),
            patch(
                "the_architect.core.gemini_cli_provider._read_gemini_settings_model",
                return_value="",
            ),
        ):
            models = GeminiCliProvider().list_models()
        assert len(models) > 0

    def test_get_resolved_model_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-pro")
        result = GeminiCliProvider().get_resolved_model(Path("/tmp"))
        assert result == "gemini-2.5-pro"

    def test_get_resolved_model_from_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        with patch(
            "the_architect.core.gemini_cli_provider._read_gemini_settings_model",
            return_value="gemini-2.5-flash",
        ):
            result = GeminiCliProvider().get_resolved_model(Path("/tmp"))
        assert result == "gemini-2.5-flash"

    def test_get_resolved_model_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_resolved_model falls back to static list when all local sources fail."""
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        with (
            patch(
                "the_architect.core.gemini_cli_provider._extract_models_from_gemini_bundle",
                return_value=[],
            ),
            patch(
                "the_architect.core.gemini_cli_provider._read_gemini_settings_model",
                return_value="",
            ),
        ):
            result = GeminiCliProvider().get_resolved_model(Path("/tmp"))
        assert result in _FALLBACK_GEMINI_MODELS


# ---------------------------------------------------------------------------
# Environment overrides
# ---------------------------------------------------------------------------


class TestGeminiCliProviderEnvOverrides:
    """Tests for GeminiCliProvider environment variable overrides."""

    def test_no_override_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        result = GeminiCliProvider().get_env_overrides(None)
        assert result == {}

    def test_api_key_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        result = GeminiCliProvider().get_env_overrides(None)
        assert "GEMINI_API_KEY" in result

    def test_config_override_ignored(self, tmp_path: Path) -> None:
        cfg = tmp_path / "some_config.json"
        cfg.write_text("{}", encoding="utf-8")
        result = GeminiCliProvider().get_env_overrides(cfg)
        assert "OPENCODE_CONFIG" not in result


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


class TestGeminiCliProviderAgents:
    """Tests for GeminiCliProvider agent-related methods."""

    def test_list_agents_returns_empty(self) -> None:
        assert GeminiCliProvider().list_agents(Path("/tmp")) == []


# ---------------------------------------------------------------------------
# Config discovery
# ---------------------------------------------------------------------------


class TestGeminiCliProviderConfig:
    """Tests for GeminiCliProvider config file discovery."""

    def test_find_user_config_returns_none_when_missing(self, tmp_path: Path) -> None:
        with patch.object(Path, "home", return_value=tmp_path):
            result = GeminiCliProvider().find_user_config(tmp_path)
        assert result is None

    def test_find_user_config_returns_project_config(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".gemini"
        config_dir.mkdir()
        (config_dir / "settings.json").write_text(
            '{"model": {"name": "gemini-2.5-pro"}}', encoding="utf-8"
        )
        with patch.object(Path, "home", return_value=Path("/nonexistent")):
            result = GeminiCliProvider().find_user_config(tmp_path)
        assert result is not None
        assert str(result).endswith("settings.json")

    def test_find_user_config_returns_global_config(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".gemini"
        config_dir.mkdir()
        (config_dir / "settings.json").write_text(
            '{"model": {"name": "gemini-2.5-pro"}}', encoding="utf-8"
        )
        with patch.object(Path, "home", return_value=tmp_path):
            # Use a different project dir so project-local config is not found
            result = GeminiCliProvider().find_user_config(Path("/other"))
        assert result is not None
        assert str(result).endswith("settings.json")

    def test_find_user_config_prefers_project_over_global(self, tmp_path: Path) -> None:
        # Create project-local config
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        gemini_dir = project_dir / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "settings.json").write_text(
            '{"model": {"name": "gemini-2.5-pro"}}', encoding="utf-8"
        )
        # Create global config under mocked home
        global_gemini = tmp_path / ".gemini"
        global_gemini.mkdir()
        (global_gemini / "settings.json").write_text(
            '{"model": {"name": "gemini-2.5-flash"}}', encoding="utf-8"
        )
        with patch.object(Path, "home", return_value=tmp_path):
            result = GeminiCliProvider().find_user_config(project_dir)
        assert result is not None
        assert "project" in str(result)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


class TestGeminiCliProviderSetup:
    """Tests for GeminiCliProvider.ensure_setup()."""

    def test_writes_prompts_to_architect_dir(self, tmp_path: Path) -> None:
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig().resolve(tmp_path)
        GeminiCliProvider().ensure_setup(tmp_path, config)
        assert (tmp_path / ".architect" / "prompts" / "architect.md").exists()

    def test_returns_prompts_dir(self, tmp_path: Path) -> None:
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig().resolve(tmp_path)
        result = GeminiCliProvider().ensure_setup(tmp_path, config)
        assert result == tmp_path / ".architect" / "prompts"

    def test_never_writes_opencode_json(self, tmp_path: Path) -> None:
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig().resolve(tmp_path)
        GeminiCliProvider().ensure_setup(tmp_path, config)
        assert not (tmp_path / "opencode.json").exists()

    def test_write_architect_prompts_includes_intelligence_md(self, tmp_path: Path) -> None:
        """Test that intelligence.md is written to .architect/prompts/."""
        GeminiCliProvider()._write_architect_prompts(tmp_path)
        assert (tmp_path / ".architect" / "prompts" / "intelligence.md").exists()


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


class TestGeminiCliProviderPrompts:
    """Tests for GeminiCliProvider prompt retrieval methods."""

    def test_get_architect_prompt_returns_string(self) -> None:
        prompt = GeminiCliProvider().get_architect_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_get_reviewer_prompt_returns_string(self) -> None:
        prompt = GeminiCliProvider().get_reviewer_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0
