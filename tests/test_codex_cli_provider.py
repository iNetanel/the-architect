"""Tests for the Codex CLI provider implementation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from the_architect.core.codex_cli_provider import _FALLBACK_CODEX_MODELS, CodexCliProvider

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class TestCodexCliProviderIdentity:
    """Tests for CodexCliProvider identity properties."""

    def test_name(self) -> None:
        assert CodexCliProvider().name == "codex"

    def test_display_name(self) -> None:
        assert CodexCliProvider().display_name == "Codex CLI"

    def test_binary_name(self) -> None:
        assert CodexCliProvider().binary_name == "codex"

    def test_supports_agents_false(self) -> None:
        assert CodexCliProvider().supports_agents() is False

    def test_supports_json_output_true(self) -> None:
        assert CodexCliProvider().supports_json_output() is True

    def test_supports_free_tier_false(self) -> None:
        assert CodexCliProvider().supports_free_tier() is False


# ---------------------------------------------------------------------------
# Installation checks
# ---------------------------------------------------------------------------


class TestCodexCliProviderInstallation:
    """Tests for CodexCliProvider installation detection."""

    def test_is_installed_returns_bool(self) -> None:
        result = CodexCliProvider().is_installed()
        assert isinstance(result, bool)

    def test_not_installed_when_binary_missing(self) -> None:
        with patch("shutil.which", return_value=None):
            assert CodexCliProvider().is_installed() is False

    def test_installed_when_binary_found(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/codex"):
            assert CodexCliProvider().is_installed() is True

    def test_get_version_returns_string(self) -> None:
        result = CodexCliProvider().get_version()
        assert isinstance(result, str)

    def test_install_hint_returns_string(self) -> None:
        hint = CodexCliProvider().install_hint()
        assert isinstance(hint, str)
        assert len(hint) > 0

    def test_install_hint_contains_codex(self) -> None:
        hint = CodexCliProvider().install_hint()
        assert "codex" in hint.lower()

    def test_install_hint_contains_npm_or_url(self) -> None:
        hint = CodexCliProvider().install_hint()
        assert "npm" in hint or "https" in hint


# ---------------------------------------------------------------------------
# Command building
# ---------------------------------------------------------------------------


class TestCodexCliProviderCommand:
    """Tests for CodexCliProvider command building."""

    def test_build_command_basic(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/codex"):
            cmd = CodexCliProvider().build_command("do something")
        assert "exec" in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "--json" in cmd
        assert "do something" in cmd

    def test_build_command_with_model(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/codex"):
            cmd = CodexCliProvider().build_command("task", model_override="gpt-5.4")
        assert "--model" in cmd
        assert "gpt-5.4" in cmd

    def test_build_command_without_model(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/codex"):
            cmd = CodexCliProvider().build_command("task")
        assert "--model" not in cmd

    def test_build_command_ignores_agent_override(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/codex"):
            cmd = CodexCliProvider().build_command("task", agent_override="build")
        assert "--agent" not in cmd


# ---------------------------------------------------------------------------
# Environment overrides
# ---------------------------------------------------------------------------


class TestCodexCliProviderEnv:
    """Tests for CodexCliProvider environment variable overrides."""

    def test_no_override_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        result = CodexCliProvider().get_env_overrides(None)
        assert result == {}

    def test_api_key_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        result = CodexCliProvider().get_env_overrides(None)
        assert "CODEX_API_KEY" in result

    def test_config_override_ignored(self, tmp_path: Path) -> None:
        cfg = tmp_path / "some_config.json"
        cfg.write_text("{}", encoding="utf-8")
        result = CodexCliProvider().get_env_overrides(cfg)
        assert "OPENCODE_CONFIG" not in result


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------


class TestCodexCliProviderParsing:
    """Tests for CodexCliProvider JSONL output parsing."""

    def test_returns_none_for_empty_line(self) -> None:
        result = CodexCliProvider().parse_output_line("")
        assert result is None

    def test_returns_none_for_whitespace_only(self) -> None:
        result = CodexCliProvider().parse_output_line("   ")
        assert result is None

    def test_thread_started_silent(self) -> None:
        line = json.dumps({"type": "thread.started"})
        result = CodexCliProvider().parse_output_line(line)
        assert result is not None
        assert result.display_lines == []

    def test_turn_started_silent(self) -> None:
        line = json.dumps({"type": "turn.started"})
        result = CodexCliProvider().parse_output_line(line)
        assert result is not None
        assert result.display_lines == []

    def test_item_started_silent(self) -> None:
        line = json.dumps({"type": "item.started"})
        result = CodexCliProvider().parse_output_line(line)
        assert result is not None
        assert result.display_lines == []

    def test_item_completed_agent_message_no_prior_delta(self) -> None:
        # item.completed with agent_message MUST produce display lines when no
        # item.delta text events were received in the same turn.  This is the
        # common case with older Codex builds that never emit item.delta events
        # and send the full agent text only in item.completed.
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Hello from Codex!"},
            }
        )
        result = CodexCliProvider().parse_output_line(line)
        assert result is not None
        assert "Hello from Codex!" in result.display_lines

    def test_item_completed_agent_message_suppressed_after_delta(self) -> None:
        # item.completed with agent_message MUST produce NO display lines when
        # item.delta text events were already streamed in the same turn.
        # This prevents double-printing the same content.
        provider = CodexCliProvider()
        # First simulate a turn.started to reset state
        provider.parse_output_line(json.dumps({"type": "turn.started"}))
        # Then simulate an item.delta that streamed text
        provider.parse_output_line(
            json.dumps({"type": "item.delta", "delta": {"type": "text_delta", "text": "Hello"}})
        )
        # Now item.completed should be suppressed
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Hello from Codex!"},
            }
        )
        result = provider.parse_output_line(line)
        assert result is not None
        assert result.display_lines == []

    def test_item_delta_text_delta(self) -> None:
        # item.delta with text_delta shape is the primary streaming path for Codex
        line = json.dumps(
            {
                "type": "item.delta",
                "delta": {"type": "text_delta", "text": "Hello from Codex!"},
            }
        )
        result = CodexCliProvider().parse_output_line(line)
        assert result is not None
        assert "Hello from Codex!" in result.display_lines

    def test_item_delta_content_delta(self) -> None:
        # item.delta with nested content_delta shape (alternative Codex format)
        line = json.dumps(
            {
                "type": "item.delta",
                "delta": {
                    "type": "content_delta",
                    "delta": {"type": "text", "text": "Streaming content"},
                },
            }
        )
        result = CodexCliProvider().parse_output_line(line)
        assert result is not None
        assert "Streaming content" in result.display_lines

    def test_item_delta_empty_text_silent(self) -> None:
        # item.delta with empty text produces no display lines
        line = json.dumps(
            {
                "type": "item.delta",
                "delta": {"type": "text_delta", "text": ""},
            }
        )
        result = CodexCliProvider().parse_output_line(line)
        assert result is not None
        assert result.display_lines == []

    def test_item_completed_command_execution(self) -> None:
        # command_execution items now show the command with $ prefix
        line = json.dumps(
            {"type": "item.completed", "item": {"type": "command_execution", "command": "ls -la"}}
        )
        result = CodexCliProvider().parse_output_line(line)
        assert result is not None
        assert len(result.display_lines) > 0
        assert any("$" in dl for dl in result.display_lines)

    def test_item_completed_command_execution_id_fallback(self) -> None:
        # Falls back to id field when command is absent
        line = json.dumps(
            {"type": "item.completed", "item": {"type": "command_execution", "id": "cmd_123"}}
        )
        result = CodexCliProvider().parse_output_line(line)
        assert result is not None
        assert len(result.display_lines) > 0
        assert any("$" in dl for dl in result.display_lines)

    def test_turn_completed_with_usage(self) -> None:
        line = json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cached_input_tokens": 20,
                },
            }
        )
        result = CodexCliProvider().parse_output_line(line)
        assert result is not None
        assert result.tokens is not None
        assert result.tokens.input_tokens == 100
        assert result.tokens.output_tokens == 50
        assert result.tokens.cache_read_tokens == 20
        assert result.tokens.cache_write_tokens == 0

    def test_turn_failed_extracts_error(self) -> None:
        line = json.dumps({"type": "turn.failed", "error": "Something went wrong"})
        result = CodexCliProvider().parse_output_line(line)
        assert result is not None
        assert "Error: Something went wrong" in result.display_lines

    def test_error_event_extracts_message(self) -> None:
        line = json.dumps({"type": "error", "message": "API error"})
        result = CodexCliProvider().parse_output_line(line)
        assert result is not None
        assert "Error: API error" in result.display_lines

    def test_rate_limit_in_turn_failed(self) -> None:
        line = json.dumps({"type": "turn.failed", "error": "rate limit exceeded"})
        result = CodexCliProvider().parse_output_line(line)
        assert result is not None
        assert result.rate_limit is True

    def test_rate_limit_in_error_event(self) -> None:
        line = json.dumps({"type": "error", "message": "rate limit exceeded"})
        result = CodexCliProvider().parse_output_line(line)
        assert result is not None
        assert result.rate_limit is True

    def test_rate_limit_in_plain_text(self) -> None:
        result = CodexCliProvider().parse_output_line("Error: rate limit exceeded")
        assert result is not None
        assert result.rate_limit is True
        assert result.event_type == "text"

    def test_model_not_found_in_plain_text(self) -> None:
        result = CodexCliProvider().parse_output_line("Error: model not found")
        assert result is not None
        assert result.model_not_found is True

    def test_plain_text_fallback(self) -> None:
        result = CodexCliProvider().parse_output_line("I wrote the file.")
        assert result is not None
        assert result.event_type == "text"
        assert "I wrote the file." in result.display_lines

    def test_normal_text_no_flags(self) -> None:
        result = CodexCliProvider().parse_output_line("All tests pass.")
        assert result is not None
        assert result.rate_limit is False
        assert result.model_not_found is False

    def test_no_tokens_for_plain_text(self) -> None:
        result = CodexCliProvider().parse_output_line("some output")
        assert result is not None
        assert result.tokens is None

    def test_agent_message_caches_model(self) -> None:
        provider = CodexCliProvider()
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "hi", "model": "gpt-5.4"},
            }
        )
        provider.parse_output_line(line)
        assert provider._resolved_model_cache["_stream"] == "gpt-5.4"
        assert provider.get_resolved_model(Path("/tmp")) == "gpt-5.4"


# ---------------------------------------------------------------------------
# Config discovery
# ---------------------------------------------------------------------------


class TestCodexCliProviderConfig:
    """Tests for CodexCliProvider config file discovery."""

    def test_find_user_config_returns_none_when_missing(self, tmp_path: Path) -> None:
        with patch.object(Path, "home", return_value=tmp_path):
            result = CodexCliProvider().find_user_config(tmp_path)
        assert result is None

    def test_find_user_config_returns_path_when_exists(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".codex"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('model = "o3"\n', encoding="utf-8")
        with patch.object(Path, "home", return_value=tmp_path):
            result = CodexCliProvider().find_user_config(tmp_path)
        assert result is not None
        assert str(result).endswith("config.toml")


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------


class TestCodexCliProviderModel:
    """Tests for CodexCliProvider model resolution."""

    def test_list_models_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_MODEL", "gpt-5.4")
        models = CodexCliProvider().list_models()
        assert models == ["gpt-5.4"]

    def test_list_models_from_debug_catalog(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_models uses codex debug models JSON catalog as the live source."""
        monkeypatch.delenv("CODEX_MODEL", raising=False)
        catalog = {
            "models": [
                {"slug": "gpt-5.5", "visibility": "list", "priority": 0},
                {"slug": "gpt-5.4", "visibility": "list", "priority": 2},
                {"slug": "hidden-model", "visibility": "hide", "priority": 1},
            ]
        }
        import json as _json

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _json.dumps(catalog)
        with patch("subprocess.run", return_value=mock_result):
            models = CodexCliProvider().list_models()
        # Hidden model must be excluded; order follows priority (ascending)
        assert models == ["gpt-5.5", "gpt-5.4"]

    def test_list_models_from_config_toml_when_debug_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When codex debug models fails, config.toml model is used."""
        monkeypatch.delenv("CODEX_MODEL", raising=False)
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with (
            patch("subprocess.run", return_value=mock_result),
            patch(
                "the_architect.core.codex_cli_provider._read_codex_config_model",
                return_value="o3",
            ),
        ):
            models = CodexCliProvider().list_models()
        assert models == ["o3"]

    def test_list_models_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CODEX_MODEL", raising=False)
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with (
            patch("subprocess.run", return_value=mock_result),
            patch(
                "the_architect.core.codex_cli_provider._read_codex_config_model",
                return_value="",
            ),
        ):
            models = CodexCliProvider().list_models()
        assert len(models) > 0

    def test_get_resolved_model_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_MODEL", "o3")
        result = CodexCliProvider().get_resolved_model(Path("/tmp"))
        assert result == "o3"

    def test_get_resolved_model_from_debug_catalog(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_resolved_model picks the first visible model from the live catalog."""
        monkeypatch.delenv("CODEX_MODEL", raising=False)
        import json as _json

        catalog = {
            "models": [
                {"slug": "gpt-5.5", "visibility": "list", "priority": 0},
                {"slug": "gpt-5.4", "visibility": "list", "priority": 2},
            ]
        }
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _json.dumps(catalog)
        with (
            patch("subprocess.run", return_value=mock_result),
            patch(
                "the_architect.core.codex_cli_provider._read_codex_config_model",
                return_value="",
            ),
        ):
            result = CodexCliProvider().get_resolved_model(Path("/tmp"))
        assert result == "gpt-5.5"

    def test_get_resolved_model_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_resolved_model falls back to static list when all live calls fail."""
        monkeypatch.delenv("CODEX_MODEL", raising=False)
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with (
            patch("subprocess.run", return_value=mock_result),
            patch(
                "the_architect.core.codex_cli_provider._read_codex_config_model",
                return_value="",
            ),
        ):
            result = CodexCliProvider().get_resolved_model(Path("/tmp"))
        assert result in _FALLBACK_CODEX_MODELS


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


class TestCodexCliProviderSetup:
    """Tests for CodexCliProvider.ensure_setup()."""

    def test_writes_prompts_to_architect_dir(self, tmp_path: Path) -> None:
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig().resolve(tmp_path)
        CodexCliProvider().ensure_setup(tmp_path, config)
        assert (tmp_path / ".architect" / "prompts" / "architect.md").exists()

    def test_returns_prompts_dir(self, tmp_path: Path) -> None:
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig().resolve(tmp_path)
        result = CodexCliProvider().ensure_setup(tmp_path, config)
        assert result == tmp_path / ".architect" / "prompts"

    def test_never_writes_opencode_json(self, tmp_path: Path) -> None:
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig().resolve(tmp_path)
        CodexCliProvider().ensure_setup(tmp_path, config)
        assert not (tmp_path / "opencode.json").exists()

    def test_write_architect_prompts_includes_intelligence_md(self, tmp_path: Path) -> None:
        """Test that intelligence.md is written to .architect/prompts/."""
        CodexCliProvider()._write_architect_prompts(tmp_path)
        assert (tmp_path / ".architect" / "prompts" / "intelligence.md").exists()


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


class TestCodexCliProviderPrompts:
    """Tests for CodexCliProvider prompt retrieval methods."""

    def test_get_architect_prompt_returns_string(self) -> None:
        prompt = CodexCliProvider().get_architect_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 100
        assert "Architect" in prompt

    def test_get_reviewer_prompt_returns_string(self) -> None:
        prompt = CodexCliProvider().get_reviewer_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0
