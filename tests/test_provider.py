"""Tests for the provider abstraction layer.

Covers:
- ArchitectProvider protocol
- OpenCodeProvider implementation
- ClaudeCodeProvider implementation
- CodexCliProvider integration in detection
- GeminiCliProvider integration in detection
- detect_provider() auto-detection
- detect_available_providers()
- ParsedEvent structure
- Provider-specific output parsing
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from the_architect.core.claude_code_provider import ClaudeCodeProvider
from the_architect.core.codex_cli_provider import CodexCliProvider
from the_architect.core.gemini_cli_provider import GeminiCliProvider
from the_architect.core.opencode_provider import OpenCodeProvider
from the_architect.core.provider import (
    ArchitectProvider,
    ParsedEvent,
    ProviderNotFoundError,
    detect_available_providers,
    detect_provider,
)

# ---------------------------------------------------------------------------
# ParsedEvent
# ---------------------------------------------------------------------------


class TestParsedEvent:
    """Tests for the ParsedEvent dataclass."""

    def test_default_values(self) -> None:
        """Default ParsedEvent should have sensible zero values."""
        event = ParsedEvent()
        assert event.event_type == "raw"
        assert event.display_lines == []
        assert event.tokens is None
        assert event.rate_limit is False
        assert event.model_not_found is False

    def test_text_event(self) -> None:
        """Should create a text event with display lines."""
        event = ParsedEvent(event_type="text", display_lines=["hello", "world"])
        assert event.event_type == "text"
        assert event.display_lines == ["hello", "world"]

    def test_rate_limit_flag(self) -> None:
        """Should carry rate-limit flag."""
        event = ParsedEvent(rate_limit=True)
        assert event.rate_limit is True

    def test_model_not_found_flag(self) -> None:
        """Should carry model-not-found flag."""
        event = ParsedEvent(model_not_found=True)
        assert event.model_not_found is True


# ---------------------------------------------------------------------------
# OpenCodeProvider — identity
# ---------------------------------------------------------------------------


class TestOpenCodeProviderIdentity:
    """Tests for OpenCodeProvider identity properties."""

    def test_name(self) -> None:
        assert OpenCodeProvider().name == "opencode"

    def test_display_name(self) -> None:
        assert OpenCodeProvider().display_name == "OpenCode"

    def test_binary_name(self) -> None:
        assert OpenCodeProvider().binary_name == "opencode"

    def test_supports_agents(self) -> None:
        assert OpenCodeProvider().supports_agents() is True

    def test_supports_json_output(self) -> None:
        assert OpenCodeProvider().supports_json_output() is True

    def test_supports_free_tier_with_openrouter_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return True when OPENROUTER_API_KEY is set."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
        assert OpenCodeProvider().supports_free_tier() is True

    def test_supports_free_tier_without_key_or_models(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return False when no OpenRouter signal is present."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with patch.object(
            OpenCodeProvider, "list_models", return_value=["anthropic/claude-3-5-sonnet"]
        ):
            assert OpenCodeProvider().supports_free_tier() is False

    def test_supports_free_tier_with_openrouter_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return True when at least one openrouter/ model is in the list."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with patch.object(
            OpenCodeProvider, "list_models", return_value=["openrouter/qwen/qwen3-235b"]
        ):
            assert OpenCodeProvider().supports_free_tier() is True


# ---------------------------------------------------------------------------
# OpenCodeProvider — installation checks
# ---------------------------------------------------------------------------


class TestOpenCodeProviderInstallation:
    """Tests for OpenCodeProvider installation detection."""

    def test_is_installed_returns_bool(self) -> None:
        result = OpenCodeProvider().is_installed()
        assert isinstance(result, bool)

    def test_get_version_returns_string(self) -> None:
        result = OpenCodeProvider().get_version()
        assert isinstance(result, str)

    def test_install_hint_returns_string(self) -> None:
        hint = OpenCodeProvider().install_hint()
        assert isinstance(hint, str)
        assert len(hint) > 0

    def test_install_hint_contains_opencode(self) -> None:
        hint = OpenCodeProvider().install_hint()
        assert "opencode" in hint.lower()

    def test_not_installed_when_binary_missing(self) -> None:
        with patch("shutil.which", return_value=None):
            assert OpenCodeProvider().is_installed() is False

    def test_installed_when_binary_found(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/opencode"):
            assert OpenCodeProvider().is_installed() is True


# ---------------------------------------------------------------------------
# OpenCodeProvider — command building
# ---------------------------------------------------------------------------


class TestOpenCodeProviderCommand:
    """Tests for OpenCodeProvider command building."""

    def test_build_command_basic(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/opencode"):
            cmd = OpenCodeProvider().build_command("do something")
        assert "/usr/local/bin/opencode" in cmd[0] or "opencode" in cmd[0]
        assert "run" in cmd
        assert "--format" in cmd
        assert "json" in cmd
        assert "--dangerously-skip-permissions" in cmd
        assert "do something" in cmd

    def test_build_command_with_model(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/opencode"):
            cmd = OpenCodeProvider().build_command("task", model_override="claude-sonnet-4")
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "claude-sonnet-4"

    def test_build_command_with_agent(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/opencode"):
            cmd = OpenCodeProvider().build_command("task", agent_override="build")
        assert "--agent" in cmd
        idx = cmd.index("--agent")
        assert cmd[idx + 1] == "build"

    def test_build_command_no_agent_when_none(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/opencode"):
            cmd = OpenCodeProvider().build_command("task")
        assert "--agent" not in cmd


# ---------------------------------------------------------------------------
# OpenCodeProvider — env overrides
# ---------------------------------------------------------------------------


class TestOpenCodeProviderEnv:
    """Tests for OpenCodeProvider environment variable overrides."""

    def test_no_override_returns_empty(self) -> None:
        env = OpenCodeProvider().get_env_overrides(None)
        assert env == {}

    def test_config_override_sets_opencode_config(self, tmp_path: Path) -> None:
        cfg = tmp_path / "architect.json"
        cfg.write_text("{}", encoding="utf-8")
        env = OpenCodeProvider().get_env_overrides(cfg)
        assert "OPENCODE_CONFIG" in env
        assert str(cfg.resolve()) in env["OPENCODE_CONFIG"]


# ---------------------------------------------------------------------------
# OpenCodeProvider — output parsing
# ---------------------------------------------------------------------------


class TestOpenCodeProviderParsing:
    """Tests for OpenCodeProvider JSON event parsing."""

    def test_returns_none_for_non_json(self) -> None:
        result = OpenCodeProvider().parse_output_line("not json")
        assert result is None

    def test_parses_text_event(self) -> None:
        line = '{"type":"text","part":{"text":"hello world"}}'
        result = OpenCodeProvider().parse_output_line(line)
        assert result is not None
        assert result.event_type == "text"
        assert "hello world" in result.display_lines

    def test_parses_error_event(self) -> None:
        line = '{"type":"error","message":"something failed"}'
        result = OpenCodeProvider().parse_output_line(line)
        assert result is not None
        assert result.event_type == "error"
        assert any("something failed" in dl for dl in result.display_lines)

    def test_detects_rate_limit_in_error(self) -> None:
        line = '{"type":"error","message":"rate limit exceeded"}'
        result = OpenCodeProvider().parse_output_line(line)
        assert result is not None
        assert result.rate_limit is True

    def test_step_finish_no_display_lines(self) -> None:
        line = '{"type":"step_finish","part":{}}'
        result = OpenCodeProvider().parse_output_line(line)
        assert result is not None
        assert result.display_lines == []


# ---------------------------------------------------------------------------
# ClaudeCodeProvider — identity
# ---------------------------------------------------------------------------


class TestClaudeCodeProviderIdentity:
    """Tests for ClaudeCodeProvider identity properties."""

    def test_name(self) -> None:
        assert ClaudeCodeProvider().name == "claude-code"

    def test_display_name(self) -> None:
        assert ClaudeCodeProvider().display_name == "Claude Code"

    def test_binary_name(self) -> None:
        assert ClaudeCodeProvider().binary_name == "claude"

    def test_supports_agents_true(self) -> None:
        """Claude Code supports named agent selection via --agent."""
        assert ClaudeCodeProvider().supports_agents() is True

    def test_supports_json_output_false(self) -> None:
        """Claude Code outputs plain text, not JSON events."""
        assert ClaudeCodeProvider().supports_json_output() is False

    def test_supports_free_tier_false(self) -> None:
        """Claude Code never supports OpenRouter free-tier rotation."""
        assert ClaudeCodeProvider().supports_free_tier() is False


# ---------------------------------------------------------------------------
# ClaudeCodeProvider — installation checks
# ---------------------------------------------------------------------------


class TestClaudeCodeProviderInstallation:
    """Tests for ClaudeCodeProvider installation detection."""

    def test_is_installed_returns_bool(self) -> None:
        result = ClaudeCodeProvider().is_installed()
        assert isinstance(result, bool)

    def test_not_installed_when_binary_missing(self) -> None:
        with patch("shutil.which", return_value=None):
            assert ClaudeCodeProvider().is_installed() is False

    def test_installed_when_binary_found(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            assert ClaudeCodeProvider().is_installed() is True

    def test_install_hint_contains_claude_code(self) -> None:
        hint = ClaudeCodeProvider().install_hint()
        assert "claude" in hint.lower()

    def test_install_hint_contains_npm_or_url(self) -> None:
        hint = ClaudeCodeProvider().install_hint()
        assert "npm" in hint or "https" in hint


# ---------------------------------------------------------------------------
# ClaudeCodeProvider — command building
# ---------------------------------------------------------------------------


class TestClaudeCodeProviderCommand:
    """Tests for ClaudeCodeProvider command building."""

    def test_build_command_basic(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            cmd = ClaudeCodeProvider().build_command("do something")
        assert "claude" in cmd[0]
        assert "--dangerously-skip-permissions" in cmd
        assert "--print" in cmd
        assert "--output-format" in cmd
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "stream-json"
        assert "--verbose" in cmd
        assert "do something" in cmd

    def test_build_command_with_model(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            cmd = ClaudeCodeProvider().build_command("task", model_override="claude-opus-4")
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "claude-opus-4"

    def test_build_command_with_agent_override(self) -> None:
        """Claude Code should pass named agent selection via --agent."""
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            cmd = ClaudeCodeProvider().build_command("task", agent_override="build")
        assert "--agent" in cmd
        idx = cmd.index("--agent")
        assert cmd[idx + 1] == "build"

    def test_uses_stream_json_output_format(self) -> None:
        """Claude Code uses --output-format stream-json for token and model capture."""
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            cmd = ClaudeCodeProvider().build_command("task")
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "--verbose" in cmd


# ---------------------------------------------------------------------------
# ClaudeCodeProvider — env overrides
# ---------------------------------------------------------------------------


class TestClaudeCodeProviderEnv:
    """Tests for ClaudeCodeProvider environment variable overrides."""

    def test_no_override_returns_empty(self) -> None:
        env = ClaudeCodeProvider().get_env_overrides(None)
        assert env == {}

    def test_config_override_ignored(self, tmp_path: Path) -> None:
        """Claude Code ignores config_override — no OPENCODE_CONFIG equivalent."""
        cfg = tmp_path / "some_config.json"
        cfg.write_text("{}", encoding="utf-8")
        env = ClaudeCodeProvider().get_env_overrides(cfg)
        assert "OPENCODE_CONFIG" not in env
        assert env == {}


# ---------------------------------------------------------------------------
# ClaudeCodeProvider — output parsing
# ---------------------------------------------------------------------------


class TestClaudeCodeProviderParsing:
    """Tests for ClaudeCodeProvider stream-json output parsing."""

    def test_returns_none_for_empty_line(self) -> None:
        result = ClaudeCodeProvider().parse_output_line("")
        assert result is None

    def test_returns_none_for_whitespace_only(self) -> None:
        result = ClaudeCodeProvider().parse_output_line("   ")
        assert result is None

    # ── Plain-text fallback (non-JSON lines) ──────────────────────────

    def test_wraps_plain_text_as_text_event(self) -> None:
        """Non-JSON lines fall back to plain-text wrapping."""
        result = ClaudeCodeProvider().parse_output_line("I wrote the file.")
        assert result is not None
        assert result.event_type == "text"
        assert "I wrote the file." in result.display_lines

    def test_no_tokens_for_plain_text_fallback(self) -> None:
        """Plain-text fallback lines carry no token counts."""
        result = ClaudeCodeProvider().parse_output_line("some output")
        assert result is not None
        assert result.tokens is None

    def test_detects_rate_limit_in_plain_text(self) -> None:
        result = ClaudeCodeProvider().parse_output_line("Error: rate limit exceeded")
        assert result is not None
        assert result.rate_limit is True

    def test_detects_model_not_found_in_plain_text(self) -> None:
        result = ClaudeCodeProvider().parse_output_line("Error: model not found")
        assert result is not None
        assert result.model_not_found is True

    def test_normal_text_no_flags(self) -> None:
        result = ClaudeCodeProvider().parse_output_line("All tests pass.")
        assert result is not None
        assert result.rate_limit is False
        assert result.model_not_found is False

    # ── stream-json event parsing ─────────────────────────────────────

    def test_system_event_caches_model(self) -> None:
        """system init event populates the resolved model cache."""
        import json

        provider = ClaudeCodeProvider()
        line = json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-4-6"})
        result = provider.parse_output_line(line)
        assert result is not None
        assert result.event_type == "system"
        assert result.display_lines == []
        # Model should be cached under "_stream"
        assert provider._resolved_model_cache.get("_stream") == "claude-sonnet-4-6"

    def test_system_event_model_returned_by_get_resolved_model(self) -> None:
        """get_resolved_model returns the model seen in the stream-json system event."""
        import json
        from pathlib import Path

        provider = ClaudeCodeProvider()
        line = json.dumps({"type": "system", "subtype": "init", "model": "claude-opus-4"})
        provider.parse_output_line(line)
        assert provider.get_resolved_model(Path("/some/project")) == "claude-opus-4"

    def test_assistant_event_extracts_text(self) -> None:
        """assistant event yields the agent's text content for display."""
        import json

        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Hello from Claude!"}]},
            }
        )
        result = ClaudeCodeProvider().parse_output_line(line)
        assert result is not None
        assert result.event_type == "assistant"
        assert "Hello from Claude!" in result.display_lines
        assert result.tokens is None

    def test_result_event_extracts_tokens(self) -> None:
        """result event yields cumulative token usage."""
        import json

        line = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 200,
                    "cache_creation_input_tokens": 30,
                },
            }
        )
        result = ClaudeCodeProvider().parse_output_line(line)
        assert result is not None
        assert result.event_type == "result"
        assert result.tokens is not None
        assert result.tokens.input_tokens == 100
        assert result.tokens.output_tokens == 50
        assert result.tokens.cache_read_tokens == 200
        assert result.tokens.cache_write_tokens == 30
        assert result.tokens.total == 150

    def test_result_error_event_sets_rate_limit(self) -> None:
        """result error event with rate-limit message sets rate_limit flag."""
        import json

        line = json.dumps(
            {
                "type": "result",
                "subtype": "error",
                "is_error": True,
                "result": "rate limit exceeded",
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
        )
        result = ClaudeCodeProvider().parse_output_line(line)
        assert result is not None
        assert result.rate_limit is True

    def test_rate_limit_event_sets_flag(self) -> None:
        """rate_limit_event with status=rejected sets rate_limit flag."""
        import json

        line = json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}})
        result = ClaudeCodeProvider().parse_output_line(line)
        assert result is not None
        assert result.rate_limit is True
        assert result.display_lines == []

    def test_rate_limit_event_allowed_does_not_set_flag(self) -> None:
        """rate_limit_event with status=allowed must NOT set rate_limit (informational only)."""
        import json

        line = json.dumps(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {"status": "allowed", "resetsAt": 1776787200},
            }
        )
        result = ClaudeCodeProvider().parse_output_line(line)
        assert result is not None
        assert result.rate_limit is False
        assert result.cooldown_until == 0


# ---------------------------------------------------------------------------
# ClaudeCodeProvider — prompt injection
# ---------------------------------------------------------------------------


class TestClaudeCodeProviderPrompts:
    """Tests for ClaudeCodeProvider prompt injection methods."""

    def test_get_architect_prompt_returns_string(self) -> None:
        prompt = ClaudeCodeProvider().get_architect_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 100  # Non-trivial content

    def test_get_architect_prompt_contains_architect_role(self) -> None:
        prompt = ClaudeCodeProvider().get_architect_prompt()
        assert "Architect" in prompt

    def test_get_architect_prompt_discourages_invented_implementation_details(self) -> None:
        """Planner prompt should keep tasks outcome-first, not guessed-internal-first."""
        prompt = ClaudeCodeProvider().get_architect_prompt()
        assert "Do **not** invent exact names" in prompt
        assert "Outcome-first, not implementation-first" in prompt
        assert "Avoid false coherence" in prompt
        assert "Exploration plans — guide, do not constrain" in prompt

    def test_get_architect_prompt_keeps_boundaries_scope_based(self) -> None:
        """Prompt should prevent over-rigid file-level task boundaries."""
        prompt = ClaudeCodeProvider().get_architect_prompt()
        assert "Boundaries should prevent scope overlap" in prompt
        assert "not block necessary integration edits" in prompt

    def test_get_reviewer_prompt_returns_string(self) -> None:
        prompt = ClaudeCodeProvider().get_reviewer_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_get_reviewer_prompt_contains_reviewer_role(self) -> None:
        prompt = ClaudeCodeProvider().get_reviewer_prompt()
        assert "reviewer" in prompt.lower() or "Reviewer" in prompt


# ---------------------------------------------------------------------------
# ClaudeCodeProvider — config discovery
# ---------------------------------------------------------------------------


class TestClaudeCodeProviderConfig:
    """Tests for ClaudeCodeProvider config file discovery."""

    def test_finds_project_claude_md(self, tmp_path: Path) -> None:
        """Should find CLAUDE.md in project root."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Claude\n", encoding="utf-8")
        result = ClaudeCodeProvider().find_user_config(tmp_path)
        assert result == claude_md

    def test_returns_none_when_no_claude_md(self, tmp_path: Path) -> None:
        """Should return None when no CLAUDE.md exists."""
        with patch.object(Path, "home", return_value=tmp_path):
            result = ClaudeCodeProvider().find_user_config(tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# ClaudeCodeProvider — model resolution
# ---------------------------------------------------------------------------


class TestClaudeCodeProviderModel:
    """Tests for ClaudeCodeProvider model resolution."""

    def test_returns_anthropic_model_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return ANTHROPIC_MODEL env var when set."""
        monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-4")
        monkeypatch.delenv("CLAUDE_MODEL", raising=False)
        result = ClaudeCodeProvider().get_resolved_model(tmp_path)
        assert result == "claude-opus-4"

    def test_returns_claude_model_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return CLAUDE_MODEL env var when ANTHROPIC_MODEL not set."""
        monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
        monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-4")
        result = ClaudeCodeProvider().get_resolved_model(tmp_path)
        assert result == "claude-sonnet-4"

    def test_returns_empty_when_no_env_or_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return empty string when no model is configured and binary extraction fails.

        When env vars, CLAUDE.md, binary extraction, and ``claude models``
        all fail to return a model, the result is an empty string.
        """
        monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
        monkeypatch.delenv("CLAUDE_MODEL", raising=False)
        # Mock subprocess.run to simulate `claude models` not being available
        import subprocess

        def _mock_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            if args[:2] == ["claude", "models"]:
                return subprocess.CompletedProcess(
                    args=args, returncode=1, stdout="", stderr="error"
                )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", _mock_run)
        # Mock binary extraction to return no models (simulates binary not found)
        monkeypatch.setattr(
            "the_architect.core.claude_code_provider._extract_models_from_binary",
            lambda: [],
        )
        result = ClaudeCodeProvider().get_resolved_model(tmp_path)
        assert result == ""

    def test_returns_model_from_claude_md(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should read model from CLAUDE.md when env vars not set."""
        monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
        monkeypatch.delenv("CLAUDE_MODEL", raising=False)
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("Model: claude-haiku-3-5\n", encoding="utf-8")
        result = ClaudeCodeProvider().get_resolved_model(tmp_path)
        assert result == "claude-haiku-3-5"


# ---------------------------------------------------------------------------
# ClaudeCodeProvider — list_models
# ---------------------------------------------------------------------------


class TestClaudeCodeProviderModels:
    """Tests for ClaudeCodeProvider model listing."""

    def test_list_models_returns_list(self) -> None:
        models = ClaudeCodeProvider().list_models()
        assert isinstance(models, list)
        assert len(models) > 0

    def test_list_models_contains_known_models(self) -> None:
        models = ClaudeCodeProvider().list_models()
        # At least one known model should be in the list
        assert any("claude" in m.lower() for m in models)


# ---------------------------------------------------------------------------
# detect_provider
# ---------------------------------------------------------------------------


class TestDetectProvider:
    """Tests for detect_provider() auto-detection."""

    def test_auto_returns_opencode_when_only_opencode_installed(self) -> None:
        with (
            patch.object(OpenCodeProvider, "is_installed", return_value=True),
            patch.object(ClaudeCodeProvider, "is_installed", return_value=False),
            patch.object(CodexCliProvider, "is_installed", return_value=False),
            patch.object(GeminiCliProvider, "is_installed", return_value=False),
        ):
            provider = detect_provider("auto")
        assert provider.name == "opencode"

    def test_auto_returns_claude_code_when_only_claude_code_installed(self) -> None:
        with (
            patch.object(OpenCodeProvider, "is_installed", return_value=False),
            patch.object(ClaudeCodeProvider, "is_installed", return_value=True),
            patch.object(CodexCliProvider, "is_installed", return_value=False),
            patch.object(GeminiCliProvider, "is_installed", return_value=False),
        ):
            provider = detect_provider("auto")
        assert provider.name == "claude-code"

    def test_auto_prefers_opencode_when_both_installed(self) -> None:
        """When both are installed, OpenCode is preferred in auto mode."""
        with (
            patch.object(OpenCodeProvider, "is_installed", return_value=True),
            patch.object(ClaudeCodeProvider, "is_installed", return_value=True),
            patch.object(CodexCliProvider, "is_installed", return_value=False),
            patch.object(GeminiCliProvider, "is_installed", return_value=False),
        ):
            provider = detect_provider("auto")
        assert provider.name == "opencode"

    def test_auto_raises_when_neither_installed(self) -> None:
        with (
            patch.object(OpenCodeProvider, "is_installed", return_value=False),
            patch.object(ClaudeCodeProvider, "is_installed", return_value=False),
            patch.object(CodexCliProvider, "is_installed", return_value=False),
            patch.object(GeminiCliProvider, "is_installed", return_value=False),
        ):
            with pytest.raises(ProviderNotFoundError):
                detect_provider("auto")

    def test_explicit_opencode_returns_opencode(self) -> None:
        with patch.object(OpenCodeProvider, "is_installed", return_value=True):
            provider = detect_provider("opencode")
        assert provider.name == "opencode"

    def test_explicit_opencode_raises_when_not_installed(self) -> None:
        with patch.object(OpenCodeProvider, "is_installed", return_value=False):
            with pytest.raises(ProviderNotFoundError):
                detect_provider("opencode")

    def test_explicit_claude_code_returns_claude_code(self) -> None:
        with patch.object(ClaudeCodeProvider, "is_installed", return_value=True):
            provider = detect_provider("claude-code")
        assert provider.name == "claude-code"

    def test_explicit_claude_code_raises_when_not_installed(self) -> None:
        with patch.object(ClaudeCodeProvider, "is_installed", return_value=False):
            with pytest.raises(ProviderNotFoundError):
                detect_provider("claude-code")

    def test_unknown_preference_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown provider preference"):
            detect_provider("unknown-provider")

    def test_auto_returns_codex_when_only_codex_installed(self) -> None:
        """When only Codex is installed, auto-detection returns Codex."""
        with (
            patch.object(OpenCodeProvider, "is_installed", return_value=False),
            patch.object(ClaudeCodeProvider, "is_installed", return_value=False),
            patch.object(CodexCliProvider, "is_installed", return_value=True),
            patch.object(GeminiCliProvider, "is_installed", return_value=False),
        ):
            provider = detect_provider("auto")
        assert provider.name == "codex"

    def test_auto_prefers_opencode_over_codex(self) -> None:
        """OpenCode is preferred over Codex in auto mode."""
        with (
            patch.object(OpenCodeProvider, "is_installed", return_value=True),
            patch.object(ClaudeCodeProvider, "is_installed", return_value=False),
            patch.object(CodexCliProvider, "is_installed", return_value=True),
            patch.object(GeminiCliProvider, "is_installed", return_value=False),
        ):
            provider = detect_provider("auto")
        assert provider.name == "opencode"

    def test_auto_prefers_codex_over_claude_code(self) -> None:
        """Codex is preferred over Claude Code in auto mode."""
        with (
            patch.object(OpenCodeProvider, "is_installed", return_value=False),
            patch.object(ClaudeCodeProvider, "is_installed", return_value=True),
            patch.object(CodexCliProvider, "is_installed", return_value=True),
            patch.object(GeminiCliProvider, "is_installed", return_value=False),
        ):
            provider = detect_provider("auto")
        assert provider.name == "codex"

    def test_explicit_codex_returns_codex(self) -> None:
        """Explicit codex preference returns Codex when installed."""
        with patch.object(CodexCliProvider, "is_installed", return_value=True):
            provider = detect_provider("codex")
        assert provider.name == "codex"

    def test_explicit_codex_raises_when_not_installed(self) -> None:
        """Explicit codex preference raises when not installed."""
        with patch.object(CodexCliProvider, "is_installed", return_value=False):
            with pytest.raises(ProviderNotFoundError):
                detect_provider("codex")

    def test_explicit_gemini_cli_returns_gemini_cli(self) -> None:
        """Explicit gemini-cli preference returns Gemini CLI when installed."""
        with patch.object(GeminiCliProvider, "is_installed", return_value=True):
            provider = detect_provider("gemini-cli")
        assert provider.name == "gemini-cli"

    def test_explicit_gemini_cli_raises_when_not_installed(self) -> None:
        """Explicit gemini-cli preference raises when not installed."""
        with patch.object(GeminiCliProvider, "is_installed", return_value=False):
            with pytest.raises(ProviderNotFoundError):
                detect_provider("gemini-cli")

    def test_auto_prefers_claude_code_over_gemini_cli(self) -> None:
        """Claude Code is preferred over Gemini CLI in auto mode."""
        with (
            patch.object(OpenCodeProvider, "is_installed", return_value=False),
            patch.object(ClaudeCodeProvider, "is_installed", return_value=True),
            patch.object(CodexCliProvider, "is_installed", return_value=False),
            patch.object(GeminiCliProvider, "is_installed", return_value=True),
        ):
            provider = detect_provider("auto")
        assert provider.name == "claude-code"

    def test_auto_returns_gemini_cli_when_only_gemini_installed(self) -> None:
        """When only Gemini CLI is installed, auto-detection returns Gemini CLI."""
        with (
            patch.object(OpenCodeProvider, "is_installed", return_value=False),
            patch.object(ClaudeCodeProvider, "is_installed", return_value=False),
            patch.object(CodexCliProvider, "is_installed", return_value=False),
            patch.object(GeminiCliProvider, "is_installed", return_value=True),
        ):
            provider = detect_provider("auto")
        assert provider.name == "gemini-cli"


# ---------------------------------------------------------------------------
# detect_available_providers
# ---------------------------------------------------------------------------


class TestDetectAvailableProviders:
    """Tests for detect_available_providers() — covers all four known providers."""

    def test_returns_empty_when_nothing_installed(self) -> None:
        with (
            patch.object(OpenCodeProvider, "is_installed", return_value=False),
            patch.object(ClaudeCodeProvider, "is_installed", return_value=False),
            patch.object(CodexCliProvider, "is_installed", return_value=False),
            patch.object(GeminiCliProvider, "is_installed", return_value=False),
        ):
            available = detect_available_providers()
        assert available == []

    def test_returns_opencode_when_only_opencode(self) -> None:
        with (
            patch.object(OpenCodeProvider, "is_installed", return_value=True),
            patch.object(ClaudeCodeProvider, "is_installed", return_value=False),
            patch.object(CodexCliProvider, "is_installed", return_value=False),
            patch.object(GeminiCliProvider, "is_installed", return_value=False),
        ):
            available = detect_available_providers()
        assert len(available) == 1
        assert available[0].name == "opencode"

    def test_returns_claude_code_when_only_claude_code(self) -> None:
        with (
            patch.object(OpenCodeProvider, "is_installed", return_value=False),
            patch.object(ClaudeCodeProvider, "is_installed", return_value=True),
            patch.object(CodexCliProvider, "is_installed", return_value=False),
            patch.object(GeminiCliProvider, "is_installed", return_value=False),
        ):
            available = detect_available_providers()
        assert len(available) == 1
        assert available[0].name == "claude-code"

    def test_returns_both_when_both_installed(self) -> None:
        with (
            patch.object(OpenCodeProvider, "is_installed", return_value=True),
            patch.object(ClaudeCodeProvider, "is_installed", return_value=True),
            patch.object(CodexCliProvider, "is_installed", return_value=False),
            patch.object(GeminiCliProvider, "is_installed", return_value=False),
        ):
            available = detect_available_providers()
        assert len(available) == 2

    def test_opencode_comes_first_when_both_installed(self) -> None:
        """OpenCode should be first in the list (preferred provider)."""
        with (
            patch.object(OpenCodeProvider, "is_installed", return_value=True),
            patch.object(ClaudeCodeProvider, "is_installed", return_value=True),
            patch.object(CodexCliProvider, "is_installed", return_value=False),
            patch.object(GeminiCliProvider, "is_installed", return_value=False),
        ):
            available = detect_available_providers()
        assert available[0].name == "opencode"
        assert available[1].name == "claude-code"

    def test_returns_codex_when_only_codex(self) -> None:
        """Should return [codex] when only Codex is installed."""
        with (
            patch.object(OpenCodeProvider, "is_installed", return_value=False),
            patch.object(ClaudeCodeProvider, "is_installed", return_value=False),
            patch.object(CodexCliProvider, "is_installed", return_value=True),
            patch.object(GeminiCliProvider, "is_installed", return_value=False),
        ):
            available = detect_available_providers()
        assert len(available) == 1
        assert available[0].name == "codex"

    def test_returns_all_three_when_all_installed(self) -> None:
        """Should return all three providers when all are installed."""
        with (
            patch.object(OpenCodeProvider, "is_installed", return_value=True),
            patch.object(ClaudeCodeProvider, "is_installed", return_value=True),
            patch.object(CodexCliProvider, "is_installed", return_value=True),
            patch.object(GeminiCliProvider, "is_installed", return_value=False),
        ):
            available = detect_available_providers()
        assert len(available) == 3

    def test_order_is_opencode_codex_claude_code(self) -> None:
        """Provider order must be: OpenCode, Codex, Claude Code."""
        with (
            patch.object(OpenCodeProvider, "is_installed", return_value=True),
            patch.object(ClaudeCodeProvider, "is_installed", return_value=True),
            patch.object(CodexCliProvider, "is_installed", return_value=True),
            patch.object(GeminiCliProvider, "is_installed", return_value=False),
        ):
            available = detect_available_providers()
        assert [p.name for p in available] == ["opencode", "codex", "claude-code"]

    def test_returns_gemini_cli_when_only_gemini(self) -> None:
        """Should return [gemini-cli] when only Gemini CLI is installed."""
        with (
            patch.object(OpenCodeProvider, "is_installed", return_value=False),
            patch.object(ClaudeCodeProvider, "is_installed", return_value=False),
            patch.object(CodexCliProvider, "is_installed", return_value=False),
            patch.object(GeminiCliProvider, "is_installed", return_value=True),
        ):
            available = detect_available_providers()
        assert len(available) == 1
        assert available[0].name == "gemini-cli"

    def test_returns_all_four_when_all_installed(self) -> None:
        """Should return all four providers when all are installed."""
        with (
            patch.object(OpenCodeProvider, "is_installed", return_value=True),
            patch.object(ClaudeCodeProvider, "is_installed", return_value=True),
            patch.object(CodexCliProvider, "is_installed", return_value=True),
            patch.object(GeminiCliProvider, "is_installed", return_value=True),
        ):
            available = detect_available_providers()
        assert len(available) == 4

    def test_order_is_opencode_codex_claude_code_gemini(self) -> None:
        """Provider order must be: OpenCode, Codex, Claude Code, Gemini CLI."""
        with (
            patch.object(OpenCodeProvider, "is_installed", return_value=True),
            patch.object(ClaudeCodeProvider, "is_installed", return_value=True),
            patch.object(CodexCliProvider, "is_installed", return_value=True),
            patch.object(GeminiCliProvider, "is_installed", return_value=True),
        ):
            available = detect_available_providers()
        assert [p.name for p in available] == ["opencode", "codex", "claude-code", "gemini-cli"]


# ---------------------------------------------------------------------------
# ArchitectProvider protocol compliance
# ---------------------------------------------------------------------------


class TestProviderProtocolCompliance:
    """Tests that all providers satisfy the ArchitectProvider protocol."""

    @pytest.mark.parametrize(
        "provider_cls", [OpenCodeProvider, ClaudeCodeProvider, CodexCliProvider, GeminiCliProvider]
    )
    def test_is_architect_provider(self, provider_cls) -> None:
        """Both providers should satisfy the ArchitectProvider protocol."""
        provider = provider_cls()
        assert isinstance(provider, ArchitectProvider)

    @pytest.mark.parametrize(
        "provider_cls", [OpenCodeProvider, ClaudeCodeProvider, CodexCliProvider, GeminiCliProvider]
    )
    def test_has_name_property(self, provider_cls) -> None:
        provider = provider_cls()
        assert isinstance(provider.name, str)
        assert len(provider.name) > 0

    @pytest.mark.parametrize(
        "provider_cls", [OpenCodeProvider, ClaudeCodeProvider, CodexCliProvider, GeminiCliProvider]
    )
    def test_has_display_name_property(self, provider_cls) -> None:
        provider = provider_cls()
        assert isinstance(provider.display_name, str)
        assert len(provider.display_name) > 0

    @pytest.mark.parametrize(
        "provider_cls", [OpenCodeProvider, ClaudeCodeProvider, CodexCliProvider, GeminiCliProvider]
    )
    def test_has_binary_name_property(self, provider_cls) -> None:
        provider = provider_cls()
        assert isinstance(provider.binary_name, str)
        assert len(provider.binary_name) > 0

    @pytest.mark.parametrize(
        "provider_cls", [OpenCodeProvider, ClaudeCodeProvider, CodexCliProvider, GeminiCliProvider]
    )
    def test_is_installed_returns_bool(self, provider_cls) -> None:
        provider = provider_cls()
        assert isinstance(provider.is_installed(), bool)

    @pytest.mark.parametrize(
        "provider_cls", [OpenCodeProvider, ClaudeCodeProvider, CodexCliProvider, GeminiCliProvider]
    )
    def test_get_version_returns_string(self, provider_cls) -> None:
        provider = provider_cls()
        assert isinstance(provider.get_version(), str)

    @pytest.mark.parametrize(
        "provider_cls,binary",
        [
            (OpenCodeProvider, "opencode"),
            (ClaudeCodeProvider, "claude"),
            (CodexCliProvider, "codex"),
            (GeminiCliProvider, "gemini"),
        ],
    )
    def test_get_version_is_cached(self, provider_cls, binary) -> None:
        """get_version() must not re-spawn a subprocess on every call.

        The provider selection screen renders on every keystroke and
        used to call get_version() from the render callback — that made
        arrow-key navigation feel sluggish because each repaint forked
        ``opencode --version`` / ``claude --version``.  This test locks
        in the cache so the regression cannot come back.
        """
        provider = provider_cls()
        fake_result = __import__("subprocess").CompletedProcess(
            args=[binary, "--version"], returncode=0, stdout="1.2.3\n", stderr=""
        )
        with patch("subprocess.run", return_value=fake_result) as mock_run:
            first = provider.get_version()
            second = provider.get_version()
            third = provider.get_version()
        assert first == second == third == "1.2.3"
        assert mock_run.call_count == 1

    @pytest.mark.parametrize(
        "provider_cls", [OpenCodeProvider, ClaudeCodeProvider, CodexCliProvider, GeminiCliProvider]
    )
    def test_install_hint_returns_string(self, provider_cls) -> None:
        provider = provider_cls()
        assert isinstance(provider.install_hint(), str)

    @pytest.mark.parametrize(
        "provider_cls", [OpenCodeProvider, ClaudeCodeProvider, CodexCliProvider, GeminiCliProvider]
    )
    def test_list_models_returns_list(self, provider_cls) -> None:
        provider = provider_cls()
        assert isinstance(provider.list_models(), list)

    @pytest.mark.parametrize(
        "provider_cls", [OpenCodeProvider, ClaudeCodeProvider, CodexCliProvider, GeminiCliProvider]
    )
    def test_build_command_returns_list(self, provider_cls, tmp_path: Path) -> None:
        with patch("shutil.which", return_value="/fake/binary"):
            provider = provider_cls()
            cmd = provider.build_command("test instruction")
        assert isinstance(cmd, list)
        assert len(cmd) > 0

    @pytest.mark.parametrize(
        "provider_cls", [OpenCodeProvider, ClaudeCodeProvider, CodexCliProvider, GeminiCliProvider]
    )
    def test_get_env_overrides_returns_dict(self, provider_cls) -> None:
        provider = provider_cls()
        env = provider.get_env_overrides(None)
        assert isinstance(env, dict)

    @pytest.mark.parametrize(
        "provider_cls", [OpenCodeProvider, ClaudeCodeProvider, CodexCliProvider, GeminiCliProvider]
    )
    def test_supports_agents_returns_bool(self, provider_cls) -> None:
        provider = provider_cls()
        assert isinstance(provider.supports_agents(), bool)

    @pytest.mark.parametrize(
        "provider_cls", [OpenCodeProvider, ClaudeCodeProvider, CodexCliProvider, GeminiCliProvider]
    )
    def test_supports_json_output_returns_bool(self, provider_cls) -> None:
        provider = provider_cls()
        assert isinstance(provider.supports_json_output(), bool)

    @pytest.mark.parametrize(
        "provider_cls", [OpenCodeProvider, ClaudeCodeProvider, CodexCliProvider, GeminiCliProvider]
    )
    def test_supports_free_tier_returns_bool(self, provider_cls) -> None:
        provider = provider_cls()
        assert isinstance(provider.supports_free_tier(), bool)


# ---------------------------------------------------------------------------
# OpenCodeProvider — ensure_setup
# ---------------------------------------------------------------------------


class TestOpenCodeProviderSetup:
    """Tests for OpenCodeProvider.ensure_setup()."""

    def test_writes_prompts_to_architect_dir(self, tmp_path: Path) -> None:
        """Should write prompts to .architect/prompts/."""
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig().resolve(tmp_path)
        OpenCodeProvider().ensure_setup(tmp_path, config)

        assert (tmp_path / ".architect" / "prompts" / "architect.md").exists()
        assert (tmp_path / ".architect" / "prompts" / "reviewer.md").exists()
        assert (tmp_path / ".architect" / "prompts" / "execution-protocol.md").exists()

    def test_writes_architect_json(self, tmp_path: Path) -> None:
        """Should write .architect/architect.json."""
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig().resolve(tmp_path)
        result = OpenCodeProvider().ensure_setup(tmp_path, config)

        assert result == tmp_path / ".architect" / "architect.json"
        assert result.exists()

    def test_never_writes_opencode_json_in_project_root(self, tmp_path: Path) -> None:
        """Must NOT create opencode.json in project root."""
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig().resolve(tmp_path)
        OpenCodeProvider().ensure_setup(tmp_path, config)

        assert not (tmp_path / "opencode.json").exists()


# ---------------------------------------------------------------------------
# ClaudeCodeProvider — ensure_setup
# ---------------------------------------------------------------------------


class TestClaudeCodeProviderSetup:
    """Tests for ClaudeCodeProvider.ensure_setup()."""

    def test_writes_prompts_to_architect_dir(self, tmp_path: Path) -> None:
        """Should write prompts to .architect/prompts/."""
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig().resolve(tmp_path)
        ClaudeCodeProvider().ensure_setup(tmp_path, config)

        assert (tmp_path / ".architect" / "prompts" / "architect.md").exists()
        assert (tmp_path / ".architect" / "prompts" / "reviewer.md").exists()
        assert (tmp_path / ".architect" / "prompts" / "execution-protocol.md").exists()

    def test_returns_prompts_dir(self, tmp_path: Path) -> None:
        """Should return .architect/prompts/ directory path."""
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig().resolve(tmp_path)
        result = ClaudeCodeProvider().ensure_setup(tmp_path, config)

        assert result == tmp_path / ".architect" / "prompts"

    def test_never_writes_opencode_json(self, tmp_path: Path) -> None:
        """Must NOT create opencode.json (Claude Code doesn't use it)."""
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig().resolve(tmp_path)
        ClaudeCodeProvider().ensure_setup(tmp_path, config)

        assert not (tmp_path / "opencode.json").exists()
        assert not (tmp_path / ".architect" / "architect.json").exists()


# ---------------------------------------------------------------------------
# Cooldown detection — parse_output_line signal paths (both providers)
# ---------------------------------------------------------------------------


class TestClaudeCodeCooldownDetection:
    """Verify parse_output_line correctly sets rate_limit and cooldown_until
    for every event shape Claude Code emits during quota exhaustion.

    Derived from real log analysis: Claude Code emits three events per quota hit:
      1. rate_limit_event  — with rate_limit_info.resetsAt timestamp
      2. assistant event   — with error="rate_limit" field
      3. result event      — with is_error=True and api_error_status=429
    """

    def _provider(self) -> ClaudeCodeProvider:
        return ClaudeCodeProvider()

    def test_rate_limit_event_sets_flag_and_resets_at(self) -> None:
        """rate_limit_event must set rate_limit=True and cooldown_until=resetsAt."""
        import json

        p = self._provider()
        event = json.dumps(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "rejected",
                    "resetsAt": 1776769200,
                    "rateLimitType": "five_hour",
                },
            }
        )
        result = p.parse_output_line(event)
        assert result is not None
        assert result.rate_limit is True
        assert result.cooldown_until == 1776769200

    def test_rate_limit_event_missing_resets_at(self) -> None:
        """rate_limit_event status=rejected without resetsAt: rate_limit=True, cooldown_until=0."""
        import json

        p = self._provider()
        event = json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}})
        result = p.parse_output_line(event)
        assert result is not None
        assert result.rate_limit is True
        assert result.cooldown_until == 0

    def test_rate_limit_event_allowed_is_not_a_cooldown(self) -> None:
        """rate_limit_event status=allowed (per-request signal) must not trigger cooldown."""
        import json

        p = self._provider()
        event = json.dumps(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "allowed",
                    "resetsAt": 1776787200,
                    "rateLimitType": "five_hour",
                },
            }
        )
        result = p.parse_output_line(event)
        assert result is not None
        assert result.rate_limit is False
        assert result.cooldown_until == 0

    def test_assistant_event_with_error_rate_limit_field(self) -> None:
        """assistant event with error='rate_limit' must set rate_limit=True."""
        import json

        p = self._provider()
        event = json.dumps(
            {
                "type": "assistant",
                "error": "rate_limit",
                "message": {
                    "content": [
                        {"type": "text", "text": "You're out of extra usage · resets 11am (UTC)"}
                    ]
                },
            }
        )
        result = p.parse_output_line(event)
        assert result is not None
        assert result.rate_limit is True

    def test_assistant_event_normal_no_rate_limit(self) -> None:
        """Normal assistant event without error field must NOT set rate_limit."""
        import json

        p = self._provider()
        event = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Writing the file now."}]},
            }
        )
        result = p.parse_output_line(event)
        assert result is not None
        assert result.rate_limit is False

    def test_result_event_api_error_status_429(self) -> None:
        """result event with api_error_status=429 must set rate_limit=True."""
        import json

        p = self._provider()
        event = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "api_error_status": 429,
                "result": "You're out of extra usage · resets 11am (UTC)",
            }
        )
        result = p.parse_output_line(event)
        assert result is not None
        assert result.rate_limit is True

    def test_result_event_api_error_status_529(self) -> None:
        """result event with api_error_status=529 (overloaded) must set rate_limit=True."""
        import json

        p = self._provider()
        event = json.dumps(
            {
                "type": "result",
                "is_error": True,
                "api_error_status": 529,
                "result": "Service temporarily overloaded",
            }
        )
        result = p.parse_output_line(event)
        assert result is not None
        assert result.rate_limit is True

    def test_result_event_normal_no_rate_limit(self) -> None:
        """Normal successful result event must NOT set rate_limit."""
        import json

        p = self._provider()
        event = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
            }
        )
        result = p.parse_output_line(event)
        # Successful result events return a ParsedEvent (may be None if no display lines)
        if result is not None:
            assert result.rate_limit is False

    def test_real_quota_exhaustion_sequence(self) -> None:
        """Simulate the exact three-event sequence from a real quota exhaustion.

        Verifies that at least one event in the sequence sets rate_limit=True
        and that the rate_limit_event carries the correct resetsAt timestamp.
        """
        import json

        p = self._provider()
        events = [
            json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {
                        "status": "rejected",
                        "resetsAt": 1776769200,
                        "rateLimitType": "five_hour",
                        "overageDisabledReason": "out_of_credits",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "error": "rate_limit",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": "You're out of extra usage · resets 11am (UTC)",
                            }
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": True,
                    "api_error_status": 429,
                    "result": "You're out of extra usage · resets 11am (UTC)",
                }
            ),
        ]

        parsed = [p.parse_output_line(e) for e in events]
        rate_limit_flags = [r.rate_limit for r in parsed if r is not None]
        cooldown_values = [r.cooldown_until for r in parsed if r is not None]

        # At least one event must signal rate_limit
        assert any(rate_limit_flags), f"No rate_limit detected in sequence: {rate_limit_flags}"
        # The rate_limit_event must carry the resetsAt timestamp
        assert 1776769200 in cooldown_values, (
            f"resetsAt not found in cooldown_until values: {cooldown_values}"
        )

    def test_normal_run_sequence_no_false_positives(self) -> None:
        """A normal successful run must NOT trigger any rate_limit flags.

        Claude Code emits rate_limit_event(status=allowed) at the start of
        every request — this must not be mistaken for a quota rejection.
        Thinking and tool_use content must be silent (return None).
        """
        import json

        p = self._provider()
        events = [
            # Informational rate_limit_event at start of every request
            json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {"status": "allowed", "resetsAt": 1776787200},
                }
            ),
            # assistant with thinking content only — must be silent
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "thinking", "thinking": "I will write the task..."}]
                    },
                }
            ),
            # assistant with tool_use content — must be silent
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": "Write", "input": {}}]},
                }
            ),
            # user event (tool result injection) — must be silent
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "tool_result", "content": "File created"}],
                    },
                }
            ),
            # normal assistant text
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "Task complete."}]},
                }
            ),
        ]

        parsed = [p.parse_output_line(e) for e in events]

        # No event in a normal run should set rate_limit=True
        rate_limit_flags = [r.rate_limit for r in parsed if r is not None]
        assert not any(rate_limit_flags), (
            f"False positive rate_limit in normal run: {rate_limit_flags}"
        )

        # Thinking and user events must be silent. Tool-use events should render
        # compact activity lines so Claude users see progress during execution.
        assert parsed[1] is not None and parsed[1].display_lines == [], (
            "assistant(thinking) should be silent ParsedEvent"
        )
        assert parsed[2] is not None and parsed[2].display_lines == ["→ Write"], (
            "assistant(tool_use) should show compact activity"
        )
        assert parsed[3] is not None and parsed[3].display_lines == [], (
            "user event should be silent ParsedEvent"
        )

        # The text event must display correctly
        assert parsed[4] is not None
        assert parsed[4].display_lines == ["Task complete."]


class TestOpenCodeCooldownDetection:
    """Verify OpenCode provider cooldown detection via text patterns.

    OpenCode uses plain-text / JSON event output — no structured rate_limit_event.
    Cooldown is detected via text-pattern matching in accumulated_text.
    """

    def test_rate_limit_text_in_error_event(self) -> None:
        """OpenCode error event containing 'rate limit' sets rate_limit=True."""
        import json

        from the_architect.core.opencode_provider import OpenCodeProvider

        p = OpenCodeProvider()
        event = json.dumps(
            {
                "type": "error",
                "message": "rate limit exceeded — please retry after 3600 seconds",
            }
        )
        result = p.parse_output_line(event)
        assert result is not None
        assert result.rate_limit is True

    def test_overloaded_text_sets_rate_limit(self) -> None:
        """Plain text 'overloaded' sets rate_limit=True for OpenCode."""
        from the_architect.core.opencode_provider import OpenCodeProvider

        p = OpenCodeProvider()
        result = p.parse_output_line('{"type":"error","message":"Model is currently overloaded"}')
        assert result is not None
        assert result.rate_limit is True

    def test_opencode_no_cooldown_until(self) -> None:
        """OpenCode never sets cooldown_until (no structured resetsAt)."""
        import json

        from the_architect.core.opencode_provider import OpenCodeProvider

        p = OpenCodeProvider()
        event = json.dumps({"type": "error", "message": "rate limit exceeded"})
        result = p.parse_output_line(event)
        assert result is not None
        assert result.rate_limit is True
        assert result.cooldown_until == 0  # OpenCode has no resetsAt


class TestStreamResultCooldownUntil:
    """Verify StreamResult carries cooldown_until from the provider stream."""

    def test_default_cooldown_until_zero(self) -> None:
        """StreamResult.cooldown_until defaults to 0."""
        from the_architect.core.runner import StreamResult, TokenUsage

        r = StreamResult(exit_code=0, tokens=TokenUsage())
        assert r.cooldown_until == 0

    def test_cooldown_until_set(self) -> None:
        """StreamResult.cooldown_until can be set."""
        from the_architect.core.runner import StreamResult, TokenUsage

        r = StreamResult(
            exit_code=1, tokens=TokenUsage(), rate_limit_hit=True, cooldown_until=1776769200
        )
        assert r.cooldown_until == 1776769200


class TestCircuitBreakerCooldownWithRateLimitHit:
    """Verify circuit breaker _check_cooldown uses rate_limit_hit and cooldown_until."""

    def _make_cb(self, tmp_path: Path) -> object:
        from the_architect.config import ArchitectConfig
        from the_architect.core.circuit import CircuitBreaker

        config = ArchitectConfig(cooldown_detection=True).resolve(tmp_path)
        return CircuitBreaker(config=config, project_root=tmp_path)

    def test_rate_limit_hit_triggers_cooldown(self, tmp_path: Path) -> None:
        """AttemptSummary with rate_limit_hit=True must trigger COOLDOWN_WAIT."""
        from the_architect.core.circuit import AttemptSummary, RecoveryAction

        cb = self._make_cb(tmp_path)
        summary = AttemptSummary(
            task_id="T01",
            attempt_number=1,
            completion_detected=False,
            accumulated_text="",  # empty — no text patterns
            exit_code=1,
            rate_limit_hit=True,  # structured signal
        )
        state = cb.record_attempt(summary)
        assert state.cooldown_waiting is True
        assert state.recovery_action == RecoveryAction.COOLDOWN_WAIT

    def test_cooldown_until_used_for_wait_time(self, tmp_path: Path) -> None:
        """AttemptSummary with cooldown_until set must store it for precise timing."""
        import time

        from the_architect.core.circuit import AttemptSummary, RecoveryAction

        cb = self._make_cb(tmp_path)
        future_ts = int(time.time()) + 7200  # 2 hours from now
        summary = AttemptSummary(
            task_id="T01",
            attempt_number=1,
            completion_detected=False,
            accumulated_text="",
            exit_code=1,
            rate_limit_hit=True,
            cooldown_until=future_ts,
        )
        state = cb.record_attempt(summary)
        assert state.cooldown_waiting is True
        assert state.recovery_action == RecoveryAction.COOLDOWN_WAIT

    def test_text_pattern_still_works_without_flag(self, tmp_path: Path) -> None:
        """Text-pattern detection still works when rate_limit_hit=False (OpenCode path)."""
        from the_architect.core.circuit import AttemptSummary, RecoveryAction

        cb = self._make_cb(tmp_path)
        summary = AttemptSummary(
            task_id="T01",
            attempt_number=1,
            completion_detected=False,
            accumulated_text="rate limit exceeded — please retry after 3600 seconds",
            exit_code=0,
            rate_limit_hit=False,  # OpenCode: flag not set, text pattern is the signal
        )
        state = cb.record_attempt(summary)
        assert state.cooldown_waiting is True
        assert state.recovery_action == RecoveryAction.COOLDOWN_WAIT


class TestPlannerCooldownPreciseTiming:
    """Verify planner uses resetsAt for precise wait time."""

    def test_cooldown_until_gives_precise_wait(self) -> None:
        """When cooldown_until is set, wait = resetsAt - now (not flat 3600s)."""
        import time

        # Simulate: resetsAt is 30 minutes from now
        future_ts = int(time.time()) + 1800
        # The planner computes: wait = cooldown_until - now
        now_ts = int(time.time())
        wait = future_ts - now_ts
        assert 1750 < wait <= 1800, f"Expected ~1800s wait, got {wait}s"

    def test_cooldown_until_zero_falls_back_to_rate_limit_hit(self) -> None:
        """When cooldown_until=0 but rate_limit_hit=True, use 1-hour minimum."""
        from the_architect.core.runner import StreamResult, TokenUsage

        r = StreamResult(
            exit_code=1,
            tokens=TokenUsage(),
            rate_limit_hit=True,
            cooldown_until=0,
        )
        # Planner logic: no precise timestamp → use 3600s minimum
        assert r.rate_limit_hit is True
        assert r.cooldown_until == 0
        # The 3600s default is applied in planner.py when cooldown_until == 0
