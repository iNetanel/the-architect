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

    def test_quota_exhausted_error_event_is_rate_limit_signal(self) -> None:
        line = json.dumps(
            {"type": "error", "message": "RESOURCE_EXHAUSTED: quota exceeded; billing not enabled"}
        )
        result = GeminiCliProvider().parse_output_line(line)
        assert result is not None
        assert result.rate_limit is True
        assert "quota exceeded" in " ".join(result.display_lines).lower()

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


# ---------------------------------------------------------------------------
# Version detection error paths — T01.1
# ---------------------------------------------------------------------------


class TestGeminiCliProviderVersionErrors:
    """Tests for GeminiCliProvider.get_version() error branches."""

    def test_get_version_timeout(self) -> None:
        """get_version returns 'unknown' when subprocess times out."""
        import subprocess

        provider = GeminiCliProvider()
        with patch(
            "the_architect.core.gemini_cli_provider.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="gemini", timeout=10),
        ):
            result = provider.get_version()
        assert result == "unknown"

    def test_get_version_called_process_error(self) -> None:
        """get_version returns 'unknown' when subprocess raises CalledProcessError."""
        import subprocess

        provider = GeminiCliProvider()
        with patch(
            "the_architect.core.gemini_cli_provider.subprocess.run",
            side_effect=subprocess.CalledProcessError(
                returncode=1, cmd="gemini", output="", stderr="crash"
            ),
        ):
            result = provider.get_version()
        assert result == "unknown"

    def test_get_version_file_not_found(self) -> None:
        """get_version returns 'unknown' when gemini binary is missing."""
        provider = GeminiCliProvider()
        with patch(
            "the_architect.core.gemini_cli_provider.subprocess.run",
            side_effect=FileNotFoundError("gemini not found"),
        ):
            result = provider.get_version()
        assert result == "unknown"

    def test_get_version_nonzero_returncode(self) -> None:
        """get_version returns 'unknown' when gemini --version exits nonzero."""
        provider = GeminiCliProvider()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error"
        with patch(
            "the_architect.core.gemini_cli_provider.subprocess.run",
            return_value=mock_result,
        ):
            result = provider.get_version()
        assert result == "unknown"

    def test_get_version_stderr_fallback(self) -> None:
        """get_version uses stderr when stdout is empty and returncode is 0."""
        provider = GeminiCliProvider()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = "1.0.0-beta"
        with patch(
            "the_architect.core.gemini_cli_provider.subprocess.run",
            return_value=mock_result,
        ):
            result = provider.get_version()
        assert result == "1.0.0-beta"


# ---------------------------------------------------------------------------
# Model availability error paths — T01.2
# ---------------------------------------------------------------------------


class TestGeminiCliProviderHasModelsErrors:
    """Tests for GeminiCliProvider.has_any_models() error branches."""

    def test_has_any_models_timeout(self) -> None:
        """has_any_models returns False when version check times out."""
        import subprocess

        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            with patch(
                "the_architect.core.gemini_cli_provider.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="gemini", timeout=10),
            ):
                assert GeminiCliProvider().has_any_models() is False

    def test_has_any_models_called_process_error(self) -> None:
        """has_any_models returns False on CalledProcessError."""
        import subprocess

        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            with patch(
                "the_architect.core.gemini_cli_provider.subprocess.run",
                side_effect=subprocess.CalledProcessError(returncode=1, cmd="gemini", output=""),
            ):
                assert GeminiCliProvider().has_any_models() is False

    def test_has_any_models_file_not_found(self) -> None:
        """has_any_models returns False when binary not found during run."""
        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            with patch(
                "the_architect.core.gemini_cli_provider.subprocess.run",
                side_effect=FileNotFoundError(),
            ):
                assert GeminiCliProvider().has_any_models() is False

    def test_has_any_models_nonzero_returncode(self) -> None:
        """has_any_models returns False when gemini --version exits nonzero."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            with patch(
                "the_architect.core.gemini_cli_provider.subprocess.run",
                return_value=mock_result,
            ):
                assert GeminiCliProvider().has_any_models() is False

    def test_has_any_models_success(self) -> None:
        """has_any_models returns True when gemini --version exits cleanly."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            with patch(
                "the_architect.core.gemini_cli_provider.subprocess.run",
                return_value=mock_result,
            ):
                assert GeminiCliProvider().has_any_models() is True


# ---------------------------------------------------------------------------
# Install hint — T01.2
# ---------------------------------------------------------------------------


class TestGeminiCliProviderInstallHint:
    """Tests for GeminiCliProvider.install_hint() npm fallback."""

    def test_install_hint_npm_available(self) -> None:
        """install_hint returns npm command when npm is on PATH."""
        with patch("shutil.which", return_value="/usr/local/bin/npm"):
            hint = GeminiCliProvider().install_hint()
        assert "npm install" in hint
        assert "gemini-cli" in hint

    def test_install_hint_npm_not_available(self) -> None:
        """install_hint returns URL when npm is not on PATH."""
        with patch("shutil.which", return_value=None):
            hint = GeminiCliProvider().install_hint()
        assert "https" in hint
        assert "npm" not in hint


# ---------------------------------------------------------------------------
# Update check flow — T01.3
# ---------------------------------------------------------------------------


class TestGeminiCliProviderUpdateCheck:
    """Tests for GeminiCliProvider.check_update_available()."""

    def _make_provider(self, version: str) -> GeminiCliProvider:
        """Create a provider with a pre-cached version string."""
        p = GeminiCliProvider()
        p._version_cache = version
        return p

    def test_update_check_not_installed(self) -> None:
        """check_update_available returns empty when gemini is not installed."""
        with patch("shutil.which", return_value=None):
            result = GeminiCliProvider().check_update_available()
        assert result == ""

    def test_update_check_version_unknown(self) -> None:
        """check_update_available returns empty when version is 'unknown'."""
        provider = self._make_provider("unknown")
        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            result = provider.check_update_available()
        assert result == ""

    def test_update_check_no_semver_in_version(self) -> None:
        """check_update_available returns empty when version has no semver."""
        provider = self._make_provider("some-weird-string")
        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            result = provider.check_update_available()
        assert result == ""

    def test_update_check_network_error(self) -> None:
        """check_update_available returns empty on network error."""
        import urllib.request

        provider = self._make_provider("1.0.0")
        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            with patch.object(urllib.request, "urlopen", side_effect=Exception("network fail")):
                result = provider.check_update_available()
        assert result == ""

    def test_update_check_no_version_in_response(self) -> None:
        """check_update_available returns empty when npm response has no version."""
        import urllib.request

        provider = self._make_provider("1.0.0")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({}).encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = lambda s, *a: None
        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            with patch.object(urllib.request, "urlopen", return_value=mock_resp):
                result = provider.check_update_available()
        assert result == ""

    def test_update_check_update_available(self) -> None:
        """check_update_available returns message when newer version exists."""
        import urllib.request

        provider = self._make_provider("gemini/1.0.0")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"version": "2.0.0"}).encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = lambda s, *a: None
        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            with patch.object(urllib.request, "urlopen", return_value=mock_resp):
                result = provider.check_update_available()
        assert "1.0.0" in result
        assert "2.0.0" in result
        assert "npm i -g" in result

    def test_update_check_same_version(self) -> None:
        """check_update_available returns empty when versions match."""
        import urllib.request

        provider = self._make_provider("gemini/1.5.0")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"version": "1.5.0"}).encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = lambda s, *a: None
        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            with patch.object(urllib.request, "urlopen", return_value=mock_resp):
                result = provider.check_update_available()
        assert result == ""

    def test_update_check_installed_newer(self) -> None:
        """check_update_available returns empty when installed is newer."""
        import urllib.request

        provider = self._make_provider("gemini/3.0.0")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"version": "2.0.0"}).encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = lambda s, *a: None
        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            with patch.object(urllib.request, "urlopen", return_value=mock_resp):
                result = provider.check_update_available()
        assert result == ""

    def test_update_check_version_parse_error(self) -> None:
        """check_update_available returns empty when version tuple parsing fails."""
        import urllib.request

        provider = self._make_provider("gemini/1.0.0")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"version": "abc.def.ghi"}).encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = lambda s, *a: None
        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            with patch.object(urllib.request, "urlopen", return_value=mock_resp):
                result = provider.check_update_available()
        assert result == ""


# ---------------------------------------------------------------------------
# Model resolution cache and edge cases — T01.4
# ---------------------------------------------------------------------------


class TestGeminiCliProviderModelResolutionEdgeCases:
    """Tests for GeminiCliProvider.get_resolved_model() cache and edge cases."""

    def test_get_resolved_model_stream_cache_hit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_resolved_model returns cached _stream value immediately."""
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        provider = GeminiCliProvider()
        # Simulate a stream-cached model from parse_output_line
        provider._resolved_model_cache["_stream"] = "gemini-2.5-pro"
        result = provider.get_resolved_model(Path("/project"))
        assert result == "gemini-2.5-pro"
        # The project-dir cache key should NOT have been set — stream cache short-circuits
        assert str(Path("/project")) not in provider._resolved_model_cache

    def test_get_resolved_model_project_cache_hit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_resolved_model returns cached project-dir value."""
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        provider = GeminiCliProvider()
        proj = Path("/myproject")
        provider._resolved_model_cache[str(proj)] = "gemini-2.5-flash"
        result = provider.get_resolved_model(proj)
        assert result == "gemini-2.5-flash"

    def test_get_resolved_model_empty_return(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_resolved_model returns '' when all sources are exhausted and list is empty."""
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        provider = GeminiCliProvider()
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
            # Temporarily replace the fallback list to force empty path
            with patch(
                "the_architect.core.gemini_cli_provider._FALLBACK_GEMINI_MODELS",
                new=[],
            ):
                # list_models will return empty list
                models = provider.list_models()
                assert models == []
                result = provider.get_resolved_model(Path("/empty"))
        assert result == ""


# ---------------------------------------------------------------------------
# instruction_via_stdin property
# ---------------------------------------------------------------------------


class TestGeminiCliProviderInstructionStdin:
    """Tests for the instruction_via_stdin property."""

    def test_instruction_via_stdin_is_false(self) -> None:
        assert GeminiCliProvider().instruction_via_stdin is False


# ---------------------------------------------------------------------------
# ensure_setup string input — T01.4
# ---------------------------------------------------------------------------


class TestGeminiCliProviderEnsureSetupString:
    """Tests for GeminiCliProvider.ensure_setup() with string project_dir."""

    def test_ensure_setup_accepts_string_path(self, tmp_path: Path) -> None:
        """ensure_setup accepts a string project_dir and converts to Path."""
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig().resolve(tmp_path)
        result = GeminiCliProvider().ensure_setup(str(tmp_path), config)
        assert result == tmp_path / ".architect" / "prompts"


# ---------------------------------------------------------------------------
# parse_output_line — list content and tool detail — T01.4
# ---------------------------------------------------------------------------


class TestGeminiCliProviderOutputParsingAdvanced:
    """Tests for advanced GeminiCliProvider output parsing branches."""

    def test_message_event_content_as_list_of_parts(self) -> None:
        """message event with content as list of dicts extracts all text parts."""
        line = json.dumps(
            {
                "type": "message",
                "role": "model",
                "content": [
                    {"text": "First part"},
                    {"text": "Second part"},
                ],
            }
        )
        result = GeminiCliProvider().parse_output_line(line)
        assert result is not None
        assert "First part" in result.display_lines
        assert "Second part" in result.display_lines

    def test_message_event_content_list_empty_text(self) -> None:
        """message event with content list containing empty text is skipped."""
        line = json.dumps(
            {
                "type": "message",
                "role": "model",
                "content": [
                    {"text": ""},
                    {"text": "Real content"},
                ],
            }
        )
        result = GeminiCliProvider().parse_output_line(line)
        assert result is not None
        assert "Real content" in result.display_lines
        assert "" not in result.display_lines

    def test_tool_use_event_with_path_detail(self) -> None:
        """tool_use event with 'path' input key shows detail."""
        line = json.dumps(
            {
                "type": "tool_use",
                "name": "read_file",
                "input": {"path": "/home/user/file.txt"},
            }
        )
        result = GeminiCliProvider().parse_output_line(line)
        assert result is not None
        assert "\u2192 read_file /home/user/file.txt" in result.display_lines

    def test_tool_use_event_with_command_detail(self) -> None:
        """tool_use event with 'command' input key shows detail."""
        line = json.dumps(
            {
                "type": "tool_use",
                "name": "bash",
                "input": {"command": "ls -la", "path": "/tmp"},
            }
        )
        result = GeminiCliProvider().parse_output_line(line)
        assert result is not None
        # 'path' is checked before 'command' in the key order
        assert "\u2192 bash /tmp" in result.display_lines

    def test_tool_use_event_fallback_detail(self) -> None:
        """tool_use event with unknown key falls back to first non-empty value."""
        line = json.dumps(
            {
                "type": "tool_use",
                "name": "custom_tool",
                "input": {"customKey": "customValue"},
            }
        )
        result = GeminiCliProvider().parse_output_line(line)
        assert result is not None
        assert "\u2192 custom_tool customValue" in result.display_lines

    def test_tool_use_event_no_detail(self) -> None:
        """tool_use event with empty input shows tool name only."""
        line = json.dumps(
            {
                "type": "tool_use",
                "name": "bare_tool",
                "input": {},
            }
        )
        result = GeminiCliProvider().parse_output_line(line)
        assert result is not None
        assert "\u2192 bare_tool" in result.display_lines
        assert len(result.display_lines) == 1


# ---------------------------------------------------------------------------
# _read_gemini_settings_model — T01.4
# ---------------------------------------------------------------------------


class TestReadGeminiSettingsModel:
    """Tests for the _read_gemini_settings_model() helper."""

    def test_read_settings_model_valid(self, tmp_path: Path) -> None:
        """Returns model name when settings.json has valid model.name."""
        settings_dir = tmp_path / ".gemini"
        settings_dir.mkdir()
        (settings_dir / "settings.json").write_text(
            json.dumps({"model": {"name": "gemini-2.5-pro"}}),
            encoding="utf-8",
        )
        with patch("pathlib.Path.home", return_value=tmp_path):
            from the_architect.core.gemini_cli_provider import (
                _read_gemini_settings_model,
            )

            result = _read_gemini_settings_model()
        assert result == "gemini-2.5-pro"

    def test_read_settings_model_file_not_found(self, tmp_path: Path) -> None:
        """Returns empty string when settings.json does not exist."""
        with patch("pathlib.Path.home", return_value=tmp_path):
            from the_architect.core.gemini_cli_provider import (
                _read_gemini_settings_model,
            )

            result = _read_gemini_settings_model()
        assert result == ""

    def test_read_settings_model_invalid_json(self, tmp_path: Path) -> None:
        """Returns empty string when settings.json contains invalid JSON."""
        settings_dir = tmp_path / ".gemini"
        settings_dir.mkdir()
        (settings_dir / "settings.json").write_text(
            "this is not json",
            encoding="utf-8",
        )
        with patch("pathlib.Path.home", return_value=tmp_path):
            from the_architect.core.gemini_cli_provider import (
                _read_gemini_settings_model,
            )

            result = _read_gemini_settings_model()
        assert result == ""

    def test_read_settings_model_no_model_field(self, tmp_path: Path) -> None:
        """Returns empty string when settings.json has no model field."""
        settings_dir = tmp_path / ".gemini"
        settings_dir.mkdir()
        (settings_dir / "settings.json").write_text(
            json.dumps({"theme": "dark"}),
            encoding="utf-8",
        )
        with patch("pathlib.Path.home", return_value=tmp_path):
            from the_architect.core.gemini_cli_provider import (
                _read_gemini_settings_model,
            )

            result = _read_gemini_settings_model()
        assert result == ""

    def test_read_settings_model_no_name_in_model(self, tmp_path: Path) -> None:
        """Returns empty string when model exists but has no name."""
        settings_dir = tmp_path / ".gemini"
        settings_dir.mkdir()
        (settings_dir / "settings.json").write_text(
            json.dumps({"model": {"provider": "google"}}),
            encoding="utf-8",
        )
        with patch("pathlib.Path.home", return_value=tmp_path):
            from the_architect.core.gemini_cli_provider import (
                _read_gemini_settings_model,
            )

            result = _read_gemini_settings_model()
        assert result == ""


# ---------------------------------------------------------------------------
# _extract_models_from_gemini_bundle — T01.5
# ---------------------------------------------------------------------------


class TestExtractModelsFromGeminiBundle:
    """Tests for the _extract_models_from_gemini_bundle() helper."""

    def test_bundle_no_binary(self) -> None:
        """Returns empty list when gemini binary is not on PATH."""
        from the_architect.core.gemini_cli_provider import (
            _extract_models_from_gemini_bundle,
        )

        with patch("shutil.which", return_value=None):
            result = _extract_models_from_gemini_bundle()
        assert result == []

    def test_bundle_finds_models_in_js(self, tmp_path: Path) -> None:
        """Extracts gemini-* model names from JS bundle files."""
        # Create a fake binary directory with JS files
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        js_file = bundle_dir / "chunk.js"
        js_file.write_text(
            'var models = ["gemini-2.5-pro", "gemini-2.5-flash"];',
            encoding="utf-8",
        )
        fake_bin = tmp_path / "gemini"
        fake_bin.write_text("#!/bin/sh", encoding="utf-8")

        with patch("shutil.which", return_value=str(fake_bin)):
            from the_architect.core.gemini_cli_provider import (
                _extract_models_from_gemini_bundle,
            )

            result = _extract_models_from_gemini_bundle()
        assert "gemini-2.5-pro" in result
        assert "gemini-2.5-flash" in result

    def test_bundle_filters_internal_names(self, tmp_path: Path) -> None:
        """Filters out internal identifiers like gemini-cli, gemini-api-key."""
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        js_file = bundle_dir / "chunk.js"
        js_file.write_text(
            '"gemini-2.5-pro", "gemini-cli", "gemini-api-key", "gemini-2.5-flash"',
            encoding="utf-8",
        )
        fake_bin = tmp_path / "gemini"
        fake_bin.write_text("#!/bin/sh", encoding="utf-8")

        with patch("shutil.which", return_value=str(fake_bin)):
            from the_architect.core.gemini_cli_provider import (
                _extract_models_from_gemini_bundle,
            )

            result = _extract_models_from_gemini_bundle()
        assert "gemini-2.5-pro" in result
        assert "gemini-2.5-flash" in result
        assert "gemini-cli" not in result
        assert "gemini-api-key" not in result

    def test_bundle_no_js_files(self, tmp_path: Path) -> None:
        """Returns empty list when no JS files found in bundle dir."""
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        fake_bin = tmp_path / "gemini"
        fake_bin.write_text("#!/bin/sh", encoding="utf-8")

        with patch("shutil.which", return_value=str(fake_bin)):
            from the_architect.core.gemini_cli_provider import (
                _extract_models_from_gemini_bundle,
            )

            result = _extract_models_from_gemini_bundle()
        assert result == []

    def test_bundle_oserror_on_read(self, tmp_path: Path) -> None:
        """Continues gracefully when a JS file raises OSError during read."""
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        good_js = bundle_dir / "good.js"
        good_js.write_text('"gemini-2.5-pro"', encoding="utf-8")
        fake_bin = tmp_path / "gemini"
        fake_bin.write_text("#!/bin/sh", encoding="utf-8")

        # Create a directory that looks like a JS file but is actually a
        # directory — glob will match it, but read_text will fail
        bad_js = bundle_dir / "bad.js"
        bad_js.mkdir()

        with patch("shutil.which", return_value=str(fake_bin)):
            from the_architect.core.gemini_cli_provider import (
                _extract_models_from_gemini_bundle,
            )

            result = _extract_models_from_gemini_bundle()
        # Should still find models from the good file, bad.js OSError is caught
        assert "gemini-2.5-pro" in result

    def test_bundle_top_level_exception(self, tmp_path: Path) -> None:
        """Returns empty list when top-level exception occurs."""
        fake_bin = tmp_path / "gemini"
        fake_bin.write_text("#!/bin/sh", encoding="utf-8")

        def failing_resolve(self):
            raise RuntimeError("symlink loop")

        with patch("shutil.which", return_value=str(fake_bin)):
            with patch.object(Path, "resolve", failing_resolve):
                from the_architect.core.gemini_cli_provider import (
                    _extract_models_from_gemini_bundle,
                )

                result = _extract_models_from_gemini_bundle()
        assert result == []
