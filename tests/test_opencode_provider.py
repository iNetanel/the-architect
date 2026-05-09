"""Tests for the OpenCode provider implementation.

Covers helper functions, provider methods, output parsing,
tool call line building, and backward-compat shims.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from the_architect.config import ArchitectConfig
from the_architect.core.opencode_provider import (
    OpenCodeProvider,
    _build_tool_call_line,
    _extract_agents_from_config_output,
    _extract_model_from_config_output,
    _get_default_agent_from_debug_config,
    check_opencode_installed,
    check_user_opencode_configured,
    ensure_opencode_setup,
    find_user_opencode_config,
    get_opencode_version,
    get_resolved_architect_model,
    list_opencode_agents,
    list_opencode_models,
    opencode_has_any_models,
    read_opencode_info,
    write_architect_config,
    write_architect_prompts,
)
from the_architect.core.runner import TokenUsage

# ---------------------------------------------------------------------------
# TestExtractModelFromConfigOutput
# ---------------------------------------------------------------------------


class TestExtractModelFromConfigOutput:
    """Tests for _extract_model_from_config_output function."""

    def test_extracts_model_when_agent_found(self) -> None:
        """Should extract model when agent is found with model key."""
        raw = '    "master": {\n        "model": "openrouter/qwen"\n    }'
        result = _extract_model_from_config_output(raw, "master")
        assert result == "openrouter/qwen"

    def test_returns_empty_when_agent_not_found(self) -> None:
        """Should return empty string when agent is not found."""
        raw = '    "other": {\n        "model": "openrouter/qwen"\n    }'
        result = _extract_model_from_config_output(raw, "master")
        assert result == ""

    def test_returns_empty_when_no_model_key(self) -> None:
        """Should return empty string when agent found but no model key."""
        raw = '    "master": {\n        "mode": "primary"\n    }'
        result = _extract_model_from_config_output(raw, "master")
        assert result == ""

    def test_returns_empty_when_multiple_agents(self) -> None:
        """Should return empty when specified agent not in multiple agent config."""
        raw = (
            '    "master": {\n'
            '        "model": "openrouter/qwen"\n'
            "    }\n"
            '    "backend": {\n'
            '        "model": "anthropic/claude"\n'
            "    }"
        )
        result = _extract_model_from_config_output(raw, "frontend")
        assert result == ""


# ---------------------------------------------------------------------------
# TestExtractAgentsFromConfigOutput
# --------------------------------------------------------------------


class TestExtractAgentsFromConfigOutput:
    """Tests for _extract_agents_from_config_output function."""

    def test_extracts_multiple_agents_with_modes(self) -> None:
        """Should extract multiple agents with their modes."""
        raw = (
            '    "master": {\n'
            '        "mode": "primary"\n'
            "    }\n"
            '    "backend": {\n'
            '        "mode": "primary"\n'
            "    }\n"
            '    "frontend": {\n'
            '        "mode": "code"\n'
            "    }"
        )
        result = _extract_agents_from_config_output(raw)
        assert result == {"master": "primary", "backend": "primary", "frontend": "code"}

    def test_returns_unknown_for_agent_without_mode(self) -> None:
        """Should mark mode as unknown when agent has no mode line."""
        raw = '    "master": {\n        "model": "openrouter/qwen"\n    }'
        result = _extract_agents_from_config_output(raw)
        assert result == {"master": "unknown"}

    def test_returns_empty_dict_for_empty_string(self) -> None:
        """Should return empty dict for empty string input."""
        result = _extract_agents_from_config_output("")
        assert result == {}

    def test_extracts_single_agent_with_mode(self) -> None:
        """Should extract single agent with its mode."""
        raw = '    "master": {\n        "mode": "primary"\n    }'
        result = _extract_agents_from_config_output(raw)
        assert result == {"master": "primary"}


# ---------------------------------------------------------------------------
# TestGetDefaultAgentFromDebugConfig
# ----------------------------------------------------------------


class TestGetDefaultAgentFromDebugConfig:
    """Tests for _get_default_agent_from_debug_config function."""

    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_agent_from_json_parse(self, mock_run: MagicMock) -> None:
        """Should return default_agent from valid JSON output."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"default_agent": "master"}', stderr=""
        )
        result = _get_default_agent_from_debug_config(Path("/tmp"))
        assert result == "master"

    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_agent_from_regex_when_json_fails(self, mock_run: MagicMock) -> None:
        """Should fall back to regex when JSON parse fails."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout='"default_agent": "backend"', stderr=""
        )
        result = _get_default_agent_from_debug_config(Path("/tmp"))
        assert result == "backend"

    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_empty_when_no_json_and_no_regex_match(self, mock_run: MagicMock) -> None:
        """Should return empty string when neither JSON nor regex matches."""
        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
        result = _get_default_agent_from_debug_config(Path("/tmp"))
        assert result == ""

    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_empty_when_returncode_non_zero(self, mock_run: MagicMock) -> None:
        """Should return empty string when subprocess returns non-zero."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        result = _get_default_agent_from_debug_config(Path("/tmp"))
        assert result == ""

    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_empty_on_timeout(self, mock_run: MagicMock) -> None:
        """Should return empty string on subprocess timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 15)
        result = _get_default_agent_from_debug_config(Path("/tmp"))
        assert result == ""

    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_empty_on_file_not_found(self, mock_run: MagicMock) -> None:
        """Should return empty string when opencode binary not found."""
        mock_run.side_effect = FileNotFoundError("opencode not found")
        result = _get_default_agent_from_debug_config(Path("/tmp"))
        assert result == ""


# ---------------------------------------------------------------------------
# TestOpenCodeProviderGetVersion
# -----------------------------------------------------------------


class TestOpenCodeProviderGetVersion:
    """Tests for OpenCodeProvider.get_version()."""

    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_version_from_stdout(self, mock_run: MagicMock) -> None:
        """Should return version from stdout when returncode is 0."""
        mock_run.return_value = MagicMock(returncode=0, stdout="1.2.3\n", stderr="")
        provider = OpenCodeProvider()
        result = provider.get_version()
        assert result == "1.2.3"

    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_version_from_stderr_fallback(self, mock_run: MagicMock) -> None:
        """Should return version from stderr when stdout is empty."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="1.2.3\n")
        provider = OpenCodeProvider()
        result = provider.get_version()
        assert result == "1.2.3"

    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_unknown_when_no_output(self, mock_run: MagicMock) -> None:
        """Should return 'unknown' when both stdout and stderr are empty."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        provider = OpenCodeProvider()
        result = provider.get_version()
        assert result == "unknown"

    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_unknown_on_non_zero_returncode(self, mock_run: MagicMock) -> None:
        """Should return 'unknown' when subprocess returns non-zero."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        provider = OpenCodeProvider()
        result = provider.get_version()
        assert result == "unknown"

    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_unknown_on_timeout(self, mock_run: MagicMock) -> None:
        """Should return 'unknown' on subprocess timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 10)
        provider = OpenCodeProvider()
        result = provider.get_version()
        assert result == "unknown"

    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_unknown_on_file_not_found(self, mock_run: MagicMock) -> None:
        """Should return 'unknown' when opencode binary not found."""
        mock_run.side_effect = FileNotFoundError("opencode not found")
        provider = OpenCodeProvider()
        result = provider.get_version()
        assert result == "unknown"

    def test_returns_cached_value_on_subsequent_calls(self) -> None:
        """Should return cached value without subprocess call on second invocation."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="1.2.3\n", stderr="")
            provider = OpenCodeProvider()
            first = provider.get_version()
            second = provider.get_version()
            assert first == second == "1.2.3"
            assert mock_run.call_count == 1


# ---------------------------------------------------------------------------
# TestOpenCodeProviderHasAnyModels
# -------------------------------------------------


class TestOpenCodeProviderHasAnyModels:
    """Tests for OpenCodeProvider.has_any_models()."""

    @patch("shutil.which")
    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_false_when_not_installed(
        self, mock_run: MagicMock, mock_which: MagicMock
    ) -> None:
        """Should return False when opencode is not installed."""
        mock_which.return_value = None
        provider = OpenCodeProvider()
        result = provider.has_any_models()
        assert result is False

    @patch("shutil.which")
    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_true_when_models_present(
        self, mock_run: MagicMock, mock_which: MagicMock
    ) -> None:
        """Should return True when models are listed."""
        mock_which.return_value = "/usr/local/bin/opencode"
        mock_run.return_value = MagicMock(returncode=0, stdout="openrouter/qwen\n", stderr="")
        provider = OpenCodeProvider()
        result = provider.has_any_models()
        assert result is True

    @patch("shutil.which")
    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_false_when_empty_output(
        self, mock_run: MagicMock, mock_which: MagicMock
    ) -> None:
        """Should return False when models command returns empty output."""
        mock_which.return_value = "/usr/local/bin/opencode"
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        provider = OpenCodeProvider()
        result = provider.has_any_models()
        assert result is False

    @patch("shutil.which")
    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_false_on_non_zero_returncode(
        self, mock_run: MagicMock, mock_which: MagicMock
    ) -> None:
        """Should return False when subprocess returns non-zero."""
        mock_which.return_value = "/usr/local/bin/opencode"
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        provider = OpenCodeProvider()
        result = provider.has_any_models()
        assert result is False

    @patch("shutil.which")
    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_false_on_subprocess_exception(
        self, mock_run: MagicMock, mock_which: MagicMock
    ) -> None:
        """Should return False when subprocess raises exception."""
        mock_which.return_value = "/usr/local/bin/opencode"
        mock_run.side_effect = subprocess.SubprocessError("error")
        provider = OpenCodeProvider()
        result = provider.has_any_models()
        assert result is False


# ---------------------------------------------------------------------------
# TestOpenCodeProviderInstallHint
# ----------------------------------------------


class TestOpenCodeProviderInstallHint:
    """Tests for OpenCodeProvider.install_hint()."""

    @patch("shutil.which")
    def test_returns_brew_command_when_brew_available(self, mock_which: MagicMock) -> None:
        """Should return brew install command when brew is available."""
        mock_which.return_value = "/usr/local/bin/brew"
        provider = OpenCodeProvider()
        result = provider.install_hint()
        assert result == "brew install opencode"

    @patch("shutil.which")
    def test_returns_npm_command_when_no_brew(self, mock_which: MagicMock) -> None:
        """Should return npm install command when brew is not available."""
        mock_which.side_effect = [None, "/usr/local/bin/npm"]
        provider = OpenCodeProvider()
        result = provider.install_hint()
        assert result == "npm i -g opencode"

    @patch("shutil.which")
    def test_returns_url_when_neither_available(self, mock_which: MagicMock) -> None:
        """Should return URL when neither brew nor npm is available."""
        mock_which.return_value = None
        provider = OpenCodeProvider()
        result = provider.install_hint()
        assert "opencode.ai" in result.lower()


# ---------------------------------------------------------------------------
# TestOpenCodeProviderListModels
# -----------------------------


class TestOpenCodeProviderListModels:
    """Tests for OpenCodeProvider.list_models()."""

    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_sorted_list_when_successful(self, mock_run: MagicMock) -> None:
        """Should return sorted list of models."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="openrouter/qwen\nanthropic/claude\n", stderr=""
        )
        provider = OpenCodeProvider()
        result = provider.list_models()
        assert result == ["anthropic/claude", "openrouter/qwen"]

    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_empty_list_on_non_zero_returncode(self, mock_run: MagicMock) -> None:
        """Should return empty list when subprocess returns non-zero."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        provider = OpenCodeProvider()
        result = provider.list_models()
        assert result == []

    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_empty_list_on_subprocess_exception(self, mock_run: MagicMock) -> None:
        """Should return empty list when subprocess raises exception."""
        mock_run.side_effect = subprocess.SubprocessError("error")
        provider = OpenCodeProvider()
        result = provider.list_models()
        assert result == []

    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_filters_blank_lines(self, mock_run: MagicMock) -> None:
        """Should filter out blank lines from output."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="openrouter/qwen\n\nanthropic/claude\n", stderr=""
        )
        provider = OpenCodeProvider()
        result = provider.list_models()
        assert result == ["anthropic/claude", "openrouter/qwen"]


# ---------------------------------------------------------------------------
# TestOpenCodeProviderListAgents
# ----------------------------


class TestOpenCodeProviderListAgents:
    """Tests for OpenCodeProvider.list_agents()."""

    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_primary_path_returns_primary_agents(self, mock_run: MagicMock) -> None:
        """Should return primary agents from opencode agent list."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="master (primary)\nbackend (primary)\n", stderr=""
        )
        provider = OpenCodeProvider()
        result = provider.list_agents(Path("/tmp"))
        assert result == ["backend", "master"]

    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_primary_path_filters_code_agents(self, mock_run: MagicMock) -> None:
        """Should filter out code mode agents."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="master (primary)\nfrontend (code)\n", stderr=""
        )
        provider = OpenCodeProvider()
        result = provider.list_agents(Path("/tmp"))
        assert result == ["master"]

    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_primary_path_filters_internal_agents(self, mock_run: MagicMock) -> None:
        """Should filter out internal The Architect agents."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="master (primary)\narchitect (code)\n", stderr=""
        )
        provider = OpenCodeProvider()
        result = provider.list_agents(Path("/tmp"))
        assert result == ["master"]

    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_list_agents_excludes_intelligence_agent(self, mock_run: MagicMock) -> None:
        """Should exclude 'intelligence' even when it appears as a primary agent."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="master (primary)\nintelligence (primary)\nbackend (primary)\n",
            stderr="",
        )
        provider = OpenCodeProvider()
        result = provider.list_agents(Path("/tmp"))
        assert "intelligence" not in result
        assert "master" in result
        assert "backend" in result

    @patch("the_architect.core.opencode_provider.subprocess.run")
    @patch("the_architect.core.opencode_provider.OpenCodeProvider.find_user_config")
    def test_fallbacks_to_debug_config_on_primary_failure(
        self, mock_find: MagicMock, mock_run: MagicMock
    ) -> None:
        """Should fall back to debug config when primary path fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        mock_find.return_value = None
        provider = OpenCodeProvider()
        result = provider.list_agents(Path("/tmp"))
        assert result == []

    @patch("the_architect.core.opencode_provider.subprocess.run")
    @patch("the_architect.core.opencode_provider.OpenCodeProvider.find_user_config")
    def test_json_parse_in_debug_config(self, mock_find: MagicMock, mock_run: MagicMock) -> None:
        """Should parse JSON from debug config when available."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"agent": {"master": {"mode": "primary"}}}),
            stderr="",
        )
        mock_find.return_value = None
        provider = OpenCodeProvider()
        result = provider.list_agents(Path("/tmp"))
        assert result == ["master"]

    @patch("the_architect.core.opencode_provider.subprocess.run")
    @patch("the_architect.core.opencode_provider.OpenCodeProvider.find_user_config")
    def test_regex_parse_in_debug_config(self, mock_find: MagicMock, mock_run: MagicMock) -> None:
        """Should fall back to regex parsing when JSON parse fails."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='    "master": {\n        "mode": "primary"\n    }',
            stderr="",
        )
        mock_find.return_value = None
        provider = OpenCodeProvider()
        result = provider.list_agents(Path("/tmp"))
        assert result == ["master"]

    @patch("the_architect.core.opencode_provider.subprocess.run")
    @patch("the_architect.core.opencode_provider.OpenCodeProvider.find_user_config")
    def test_reads_opencode_json_as_fallback(
        self, mock_find: MagicMock, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Should read opencode.json when subprocesses fail."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        oc_json = tmp_path / "opencode.json"
        oc_json.write_text(json.dumps({"agent": {"master": {"mode": "primary"}}}), encoding="utf-8")
        mock_find.return_value = oc_json
        provider = OpenCodeProvider()
        result = provider.list_agents(Path("/tmp"))
        assert result == ["master"]

    @patch("the_architect.core.opencode_provider.subprocess.run")
    @patch("the_architect.core.opencode_provider.OpenCodeProvider.find_user_config")
    def test_returns_empty_when_all_fallbacks_fail(
        self, mock_find: MagicMock, mock_run: MagicMock
    ) -> None:
        """Should return empty list when all fallback paths fail."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        mock_find.return_value = None
        provider = OpenCodeProvider()
        result = provider.list_agents(Path("/tmp"))
        assert result == []


# ---------------------------------------------------------------------------
# TestOpenCodeProviderGetResolvedModel
# ---------------------------------------------


class TestOpenCodeProviderGetResolvedModel:
    """Tests for OpenCodeProvider.get_resolved_model()."""

    @patch("the_architect.config.find_opencode_json")
    def test_returns_model_from_config_file(self, mock_find: MagicMock, tmp_path: Path) -> None:
        """Should return model from config file when agent is specified."""
        oc_json = tmp_path / "opencode.json"
        oc_json.write_text(
            json.dumps({"agent": {"master": {"model": "openrouter/qwen"}}}), encoding="utf-8"
        )
        mock_find.return_value = oc_json
        provider = OpenCodeProvider()
        result = provider.get_resolved_model(Path("/tmp"), "master")
        assert result == "openrouter/qwen"

    @patch("the_architect.config.find_opencode_json")
    def test_uses_default_agent_when_empty_string(
        self, mock_find: MagicMock, tmp_path: Path
    ) -> None:
        """Should use default_agent when agent_name is empty string."""
        oc_json = tmp_path / "opencode.json"
        oc_json.write_text(
            json.dumps(
                {"default_agent": "master", "agent": {"master": {"model": "openrouter/qwen"}}}
            ),
            encoding="utf-8",
        )
        mock_find.return_value = oc_json
        provider = OpenCodeProvider()
        result = provider.get_resolved_model(Path("/tmp"), "")
        assert result == "openrouter/qwen"

    @patch("the_architect.config.find_opencode_json")
    def test_falls_to_top_level_model_when_agent_missing(
        self, mock_find: MagicMock, tmp_path: Path
    ) -> None:
        """Should fall back to top-level model when agent not in config."""
        oc_json = tmp_path / "opencode.json"
        oc_json.write_text(json.dumps({"model": "openrouter/qwen"}), encoding="utf-8")
        mock_find.return_value = oc_json
        provider = OpenCodeProvider()
        result = provider.get_resolved_model(Path("/tmp"), "missing")
        assert result == "openrouter/qwen"

    @patch("the_architect.config.find_opencode_json")
    def test_returns_top_level_model_when_only_present(
        self, mock_find: MagicMock, tmp_path: Path
    ) -> None:
        """Should return top-level model when no agents defined."""
        oc_json = tmp_path / "opencode.json"
        oc_json.write_text(json.dumps({"model": "openrouter/qwen"}), encoding="utf-8")
        mock_find.return_value = oc_json
        provider = OpenCodeProvider()
        result = provider.get_resolved_model(Path("/tmp"), "")
        assert result == "openrouter/qwen"

    @patch("the_architect.config.find_opencode_json")
    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_falls_to_debug_config_when_config_read_fails(
        self, mock_run: MagicMock, mock_find: MagicMock, tmp_path: Path
    ) -> None:
        """Should fall back to debug config when config file read fails."""
        # find_opencode_json returns a path but reading it raises OSError
        non_existent = tmp_path / "nonexistent.json"
        mock_find.return_value = non_existent
        mock_run.return_value = MagicMock(
            returncode=0, stdout=json.dumps({"model": "openrouter/qwen"}), stderr=""
        )
        provider = OpenCodeProvider()
        result = provider.get_resolved_model(Path("/tmp"), "")
        assert result == "openrouter/qwen"

    @patch("the_architect.config.find_opencode_json")
    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_json_parse_in_debug_config(self, mock_run: MagicMock, mock_find: MagicMock) -> None:
        """Should parse JSON from debug config when available."""
        mock_find.return_value = None
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {"default_agent": "master", "agent": {"master": {"model": "openrouter/qwen"}}}
            ),
            stderr="",
        )
        provider = OpenCodeProvider()
        result = provider.get_resolved_model(Path("/tmp"), "")
        assert result == "openrouter/qwen"

    @patch("the_architect.config.find_opencode_json")
    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_regex_fallback_in_debug_config(
        self, mock_run: MagicMock, mock_find: MagicMock
    ) -> None:
        """Should fall back to regex parsing when JSON parse fails in debug config."""
        mock_find.return_value = None
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                '    "default_agent": "master"\n'
                '    "master": {\n        "model": "openrouter/qwen"\n    }'
            ),
            stderr="",
        )
        provider = OpenCodeProvider()
        result = provider.get_resolved_model(Path("/tmp"), "")
        assert result == "openrouter/qwen"

    @patch("the_architect.config.find_opencode_json")
    @patch("the_architect.core.opencode_provider.subprocess.run")
    def test_returns_empty_when_all_paths_fail(
        self, mock_run: MagicMock, mock_find: MagicMock
    ) -> None:
        """Should return empty string when all resolution paths fail."""
        mock_find.return_value = None
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        provider = OpenCodeProvider()
        result = provider.get_resolved_model(Path("/tmp"), "")
        assert result == ""


# ---------------------------------------------------------------------------
# TestOpenCodeProviderFindUserConfig
# -----------------------------------------


class TestOpenCodeProviderFindUserConfig:
    """Tests for OpenCodeProvider.find_user_config()."""

    def test_returns_config_via_open_code_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return config when OPENCODE_CONFIG env var is set and file exists."""
        config_file = tmp_path / "custom.json"
        config_file.write_text("{}", encoding="utf-8")
        monkeypatch.setenv("OPENCODE_CONFIG", str(config_file))
        monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
        provider = OpenCodeProvider()
        result = provider.find_user_config(tmp_path)
        assert result == config_file

    def test_continues_search_when_env_config_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should continue search when OPENCODE_CONFIG file doesn't exist."""
        missing_file = tmp_path / "missing.json"
        monkeypatch.setenv("OPENCODE_CONFIG", str(missing_file))
        monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
        oc_json = tmp_path / "opencode.json"
        oc_json.write_text("{}", encoding="utf-8")
        provider = OpenCodeProvider()
        result = provider.find_user_config(tmp_path)
        assert result == oc_json

    def test_returns_config_via_open_code_dir_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return config when OPENCODE_CONFIG_DIR contains config."""
        config_file = tmp_path / "config.json"
        config_file.write_text("{}", encoding="utf-8")
        monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
        monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(tmp_path))
        provider = OpenCodeProvider()
        result = provider.find_user_config(tmp_path)
        assert result == config_file

    def test_returns_project_local_config(self, tmp_path: Path) -> None:
        """Should return project-local opencode.json."""
        oc_json = tmp_path / "opencode.json"
        oc_json.write_text("{}", encoding="utf-8")
        provider = OpenCodeProvider()
        result = provider.find_user_config(tmp_path)
        assert result == oc_json

    def test_returns_project_local_jsonc_config(self, tmp_path: Path) -> None:
        """Should return project-local opencode.jsonc if present."""
        oc_jsonc = tmp_path / "opencode.jsonc"
        oc_jsonc.write_text("{}", encoding="utf-8")
        provider = OpenCodeProvider()
        result = provider.find_user_config(tmp_path)
        assert result == oc_jsonc

    def test_returns_xdg_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return XDG config when set and exists."""
        xdg_dir = tmp_path / "opencode"
        xdg_dir.mkdir(parents=True)
        config_file = xdg_dir / "opencode.json"
        config_file.write_text("{}", encoding="utf-8")
        monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
        monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        provider = OpenCodeProvider()
        result = provider.find_user_config(tmp_path)
        assert result == config_file

    def test_returns_default_xdg_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return default XDG config when XDG_CONFIG_HOME not set."""
        xdg_dir = tmp_path / ".config" / "opencode"
        xdg_dir.mkdir(parents=True)
        config_file = xdg_dir / "opencode.json"
        config_file.write_text("{}", encoding="utf-8")
        monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
        monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        with patch("pathlib.Path.home", return_value=tmp_path):
            provider = OpenCodeProvider()
            result = provider.find_user_config(tmp_path)
            assert result == config_file

    def test_returns_windows_appdata_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return Windows APPDATA config when on Windows."""
        appdata_dir = tmp_path / "AppData"
        oc_dir = appdata_dir / "opencode"
        oc_dir.mkdir(parents=True)
        config_file = oc_dir / "opencode.json"
        config_file.write_text("{}", encoding="utf-8")
        monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
        monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setenv("APPDATA", str(appdata_dir))
        with patch("the_architect.core.opencode_provider.sys.platform", "win32"):
            with patch("pathlib.Path.home", return_value=tmp_path):
                provider = OpenCodeProvider()
                result = provider.find_user_config(tmp_path)
                assert result == config_file

    def test_returns_none_when_no_config_found(self, tmp_path: Path) -> None:
        """Should return None when config not found anywhere."""
        provider = OpenCodeProvider()
        result = provider.find_user_config(tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# TestOpenCodeProviderEnsureSetupStringPath
# --------------------------------===================


class TestOpenCodeProviderEnsureSetupStringPath:
    """Tests for OpenCodeProvider.ensure_setup() with string path."""

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        """Should accept string path and convert to Path."""

        config = ArchitectConfig().resolve(tmp_path)
        provider = OpenCodeProvider()
        result = provider.ensure_setup(str(tmp_path), config)  # type: ignore[arg-type]
        assert result.is_file()


# ---------------------------------------------------------------------------
# TestOpenCodeProviderReadInfo
# ---------------------


class TestOpenCodeProviderReadInfo:
    """Tests for OpenCodeProvider.read_info()."""

    def test_returns_standalone_info_when_standalone_mode_set(self, tmp_path: Path) -> None:
        """Should return standalone info without subprocess calls."""

        config = ArchitectConfig(standalone_mode="some-model").resolve(tmp_path)
        provider = OpenCodeProvider()
        result = provider.read_info(tmp_path, config)
        assert result["model"] == "some-model"
        assert result["mode"] == "standalone"

    @patch("the_architect.core.opencode_provider.OpenCodeProvider.get_resolved_model")
    @patch("the_architect.core.opencode_provider.OpenCodeProvider.find_user_config")
    @patch("the_architect.core.opencode_provider.OpenCodeProvider.get_version")
    def test_includes_model_from_get_resolved_model(
        self,
        mock_version: MagicMock,
        mock_find: MagicMock,
        mock_resolved: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Should include model from get_resolved_model in info."""

        mock_version.return_value = "1.2.3"
        mock_resolved.return_value = "openrouter/qwen"
        mock_find.return_value = None
        config = ArchitectConfig().resolve(tmp_path)
        provider = OpenCodeProvider()
        result = provider.read_info(tmp_path, config)
        assert result["model"] == "openrouter/qwen"

    @patch("the_architect.core.opencode_provider.OpenCodeProvider.get_resolved_model")
    @patch("the_architect.core.opencode_provider.OpenCodeProvider.find_user_config")
    @patch("the_architect.core.opencode_provider.OpenCodeProvider.get_version")
    def test_includes_agent_from_config_file(
        self,
        mock_version: MagicMock,
        mock_find: MagicMock,
        mock_resolved: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Should include agent from config file in info."""

        mock_version.return_value = "1.2.3"
        mock_resolved.return_value = ""
        oc_json = tmp_path / "opencode.json"
        oc_json.write_text(json.dumps({"default_agent": "master"}), encoding="utf-8")
        mock_find.return_value = oc_json
        config = ArchitectConfig().resolve(tmp_path)
        provider = OpenCodeProvider()
        result = provider.read_info(tmp_path, config)
        assert result["agent"] == "master"

    @patch("the_architect.core.opencode_provider.OpenCodeProvider.get_resolved_model")
    @patch("the_architect.core.opencode_provider.OpenCodeProvider.find_user_config")
    @patch("the_architect.core.opencode_provider._get_default_agent_from_debug_config")
    @patch("the_architect.core.opencode_provider.OpenCodeProvider.get_version")
    def test_falls_to_debug_config_when_no_config_found(
        self,
        mock_version: MagicMock,
        mock_agent: MagicMock,
        mock_find: MagicMock,
        mock_resolved: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Should fall back to debug config when no config file found."""

        mock_version.return_value = "1.2.3"
        mock_resolved.return_value = ""
        mock_find.return_value = None
        mock_agent.return_value = "master"
        config = ArchitectConfig().resolve(tmp_path)
        provider = OpenCodeProvider()
        result = provider.read_info(tmp_path, config)
        assert result["agent"] == "master"


# ---------------------------------------------------------------------------
# TestOpenCodeProviderParseOutputLineEdgeCases
# ------------------==============


class TestOpenCodeProviderParseOutputLineEdgeCases:
    """Tests for edge cases in OpenCodeProvider.parse_output_line()."""

    def test_extracts_tokens_from_part_tokens_with_cache(self) -> None:
        """Should extract tokens from part.tokens with cache.read and cache.write."""
        line = json.dumps(
            {
                "type": "text",
                "part": {
                    "tokens": {"input": 100, "output": 50, "cache": {"read": 200, "write": 30}}
                },
            }
        )
        provider = OpenCodeProvider()
        result = provider.parse_output_line(line)
        assert result is not None
        assert isinstance(result.tokens, TokenUsage)
        assert result.tokens.cache_read_tokens == 200
        assert result.tokens.cache_write_tokens == 30

    def test_treats_cache_as_zero_when_not_dict(self) -> None:
        """Should treat cache tokens as 0 when cache is not a dict."""
        line = json.dumps(
            {
                "type": "text",
                "part": {"tokens": {"input": 100, "output": 50, "cache": "invalid"}},
            }
        )
        provider = OpenCodeProvider()
        result = provider.parse_output_line(line)
        assert result is not None
        assert isinstance(result.tokens, TokenUsage)
        assert result.tokens.cache_read_tokens == 0
        assert result.tokens.cache_write_tokens == 0

    def test_extracts_tokens_from_usage_dict(self) -> None:
        """Should extract tokens from usage dict when part.tokens not present."""
        line = json.dumps(
            {
                "type": "text",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 200,
                    "cache_creation_input_tokens": 30,
                },
            }
        )
        provider = OpenCodeProvider()
        result = provider.parse_output_line(line)
        assert result is not None
        assert isinstance(result.tokens, TokenUsage)
        assert result.tokens.cache_read_tokens == 200
        assert result.tokens.cache_write_tokens == 30

    def test_handles_prompt_tokens_in_usage_dict(self) -> None:
        """Should handle prompt_tokens instead of input_tokens in usage dict."""
        line = json.dumps(
            {
                "type": "text",
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "cache_read_input_tokens": 200,
                    "cache_creation_input_tokens": 30,
                },
            }
        )
        provider = OpenCodeProvider()
        result = provider.parse_output_line(line)
        assert result is not None
        assert isinstance(result.tokens, TokenUsage)
        assert result.tokens.input_tokens == 100

    def test_treats_none_token_values_as_zero(self) -> None:
        """Should treat None token values as 0."""
        line = json.dumps({"type": "text", "usage": {"input_tokens": None, "output_tokens": None}})
        provider = OpenCodeProvider()
        result = provider.parse_output_line(line)
        assert result is not None
        assert isinstance(result.tokens, TokenUsage)
        assert result.tokens.input_tokens == 0
        assert result.tokens.output_tokens == 0

    def test_builds_tool_result_line_for_completed_tool_use(self) -> None:
        """Should build tool result line when tool_use is completed."""
        line = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "write",
                    "state": {
                        "status": "completed",
                        "input": {"path": "test.py"},
                        "output": "File written",
                    },
                },
            }
        )
        provider = OpenCodeProvider()
        result = provider.parse_output_line(line)
        assert result is not None
        assert any("←" in line for line in result.display_lines)

    def test_builds_tool_call_line_when_no_result_for_completed_tool_use(self) -> None:
        """Should build tool call line when no result for completed tool_use."""
        line = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "write",
                    "state": {
                        "status": "completed",
                        "input": {"path": "test.py"},
                        "output": "",
                    },
                },
            }
        )
        provider = OpenCodeProvider()
        result = provider.parse_output_line(line)
        assert result is not None
        assert len(result.display_lines) == 1
        assert "→ write" in result.display_lines[0]

    def test_builds_tool_call_line_for_non_completed_tool_use(self) -> None:
        """Should build tool call line for non-completed tool_use."""
        line = json.dumps(
            {"type": "tool_use", "part": {"tool": "write", "state": {"status": "in_progress"}}}
        )
        provider = OpenCodeProvider()
        result = provider.parse_output_line(line)
        assert result is not None
        assert "→ write" in result.display_lines[0]

    def test_returns_empty_display_lines_for_empty_tool_name(self) -> None:
        """Should return empty display lines for tool_use with empty tool name."""
        line = json.dumps({"type": "tool_use", "part": {}})
        provider = OpenCodeProvider()
        result = provider.parse_output_line(line)
        assert result is not None
        assert result.display_lines == []

    def test_extracts_text_from_legacy_assistant_format(self) -> None:
        """Should extract text from legacy assistant message format."""
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Hello World"}]},
            }
        )
        provider = OpenCodeProvider()
        result = provider.parse_output_line(line)
        assert result is not None
        assert "Hello World" in result.display_lines

    def test_returns_empty_display_lines_for_assistant_without_text(self) -> None:
        """Should return empty display lines for assistant event without text content."""
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "thinking", "thinking": "..."}]},
            }
        )
        provider = OpenCodeProvider()
        result = provider.parse_output_line(line)
        assert result is not None
        assert result.display_lines == []

    def test_builds_tool_call_line_from_legacy_tool_format(self) -> None:
        """Should build tool call line from legacy tool event format."""
        line = json.dumps({"type": "tool", "tool": {"name": "write", "input": {"path": "test.py"}}})
        provider = OpenCodeProvider()
        result = provider.parse_output_line(line)
        assert result is not None
        assert "→ write" in result.display_lines[0]

    def test_returns_empty_display_lines_for_tool_with_empty_name(self) -> None:
        """Should return empty display lines for tool event with empty name."""
        line = json.dumps({"type": "tool", "tool": {}})
        provider = OpenCodeProvider()
        result = provider.parse_output_line(line)
        assert result is not None
        assert result.display_lines == []

    def test_extracts_error_from_error_key(self) -> None:
        """Should extract error message from 'error' key when 'message' not present."""
        line = json.dumps({"type": "error", "error": "Something went wrong"})
        provider = OpenCodeProvider()
        result = provider.parse_output_line(line)
        assert result is not None
        assert "Something went wrong" in result.display_lines[0]

    def test_sets_model_not_found_flag(self) -> None:
        """Should set model_not_found flag for model not found events."""
        line = json.dumps({"type": "error", "message": "model not found: error"})
        provider = OpenCodeProvider()
        result = provider.parse_output_line(line)
        assert result is not None
        assert result.model_not_found is True

    def test_returns_none_for_empty_line(self) -> None:
        """Should return None for empty line."""
        provider = OpenCodeProvider()
        result = provider.parse_output_line("")
        assert result is None

    def test_returns_empty_display_lines_for_text_event_with_empty_text(self) -> None:
        """Should return empty display lines for text event with empty text."""
        line = json.dumps({"type": "text", "part": {}})
        provider = OpenCodeProvider()
        result = provider.parse_output_line(line)
        assert result is not None
        assert result.display_lines == []

    def test_returns_empty_display_lines_for_text_part_not_dict(self) -> None:
        """Should return empty display lines when part is not a dict."""
        line = json.dumps({"type": "text", "part": "not_a_dict"})
        provider = OpenCodeProvider()
        result = provider.parse_output_line(line)
        assert result is not None
        assert result.display_lines == []


# ---------------------------------------------------------------------------
# TestBuildToolCallLine
# -----------


class TestBuildToolCallLine:
    """Tests for _build_tool_call_line function."""

    def build_inp(self, inp: dict[str, Any], default: str = "") -> Any:
        """Helper to create a get_inp function."""

        def _get_inp(key: str, alt: str = "") -> str:
            val = inp.get(key, inp.get(alt, default))
            return str(val) if val else ""

        return _get_inp

    def test_builds_write_tool_call_line(self) -> None:
        """Should build write tool call line."""
        inp = {"path": "test.py"}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("write", inp, get_inp)
        assert result == "→ write test.py"

    def test_builds_edit_tool_call_line(self) -> None:
        """Should build edit tool call line."""
        inp = {"path": "test.py"}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("edit", inp, get_inp)
        assert result == "→ edit test.py"

    def test_builds_bash_tool_call_line(self) -> None:
        """Should build bash tool call line with truncation."""
        inp = {"command": "ls /very/long/path/with/many/segments"}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("bash", inp, get_inp)
        assert result.startswith("$ ")
        assert len(result) <= len("$ ls /very/long/path/with/many/segments")

    def test_builds_read_tool_call_line(self) -> None:
        """Should build read tool call line."""
        inp = {"path": "test.py"}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("read", inp, get_inp)
        assert result == "→ read test.py"

    def test_builds_read_tool_call_line_with_offset_limit(self) -> None:
        """Should build read tool call line with offset and limit."""
        inp = {"path": "test.py", "offset": 10, "limit": 5}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("read", inp, get_inp)
        assert "(L10-15)" in result

    def test_builds_view_tool_call_line(self) -> None:
        """Should build view tool call line (same as read)."""
        inp = {"path": "test.py"}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("view", inp, get_inp)
        assert result == "→ view test.py"

    def test_builds_glob_tool_call_line(self) -> None:
        """Should build glob tool call line."""
        inp = {"pattern": "*.py"}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("glob", inp, get_inp)
        assert result == "→ glob *.py"

    def test_builds_glob_tool_call_line_with_path(self) -> None:
        """Should build glob tool call line with path."""
        inp = {"pattern": "*.py", "path": "/src"}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("glob", inp, get_inp)
        assert "in /src" in result

    def test_builds_grep_tool_call_line(self) -> None:
        """Should build grep tool call line."""
        inp = {"pattern": "import.*"}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("grep", inp, get_inp)
        assert '→ grep "import.*"' in result

    def test_builds_grep_tool_call_line_with_include(self) -> None:
        """Should build grep tool call line with include pattern."""
        inp = {"pattern": "import.*", "include": "*.py"}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("grep", inp, get_inp)
        assert "(*.py)" in result

    def test_builds_grep_tool_call_line_with_path(self) -> None:
        """Should build grep tool call line with path."""
        inp = {"pattern": "import.*", "path": "/src"}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("grep", inp, get_inp)
        assert "in /src" in result

    def test_builds_ls_tool_call_line_with_path(self) -> None:
        """Should build ls tool call line with path."""
        inp = {"path": "/tmp"}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("ls", inp, get_inp)
        assert result == "→ ls /tmp"

    def test_builds_ls_tool_call_line_without_path(self) -> None:
        """Should build ls tool call line without path."""
        inp: dict[str, Any] = {}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("ls", inp, get_inp)
        assert result == "→ ls"

    def test_builds_fetch_tool_call_line_with_url(self) -> None:
        """Should build fetch tool call line with URL."""
        inp = {"url": "https://example.com"}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("fetch", inp, get_inp)
        assert "→ fetch https://example.com" in result

    def test_builds_fetch_tool_call_line_without_url(self) -> None:
        """Should build fetch tool call line without URL."""
        inp: dict[str, Any] = {}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("fetch", inp, get_inp)
        assert result == "→ fetch"

    def test_builds_diagnostics_tool_call_line_with_path(self) -> None:
        """Should build diagnostics tool call line with path."""
        inp = {"filePath": "test.py"}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("diagnostics", inp, get_inp)
        assert result == "→ diagnostics test.py"

    def test_builds_diagnostics_tool_call_line_without_path(self) -> None:
        """Should build diagnostics tool call line without path."""
        inp: dict[str, Any] = {}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("diagnostics", inp, get_inp)
        assert result == "→ diagnostics"

    def test_builds_sourcegraph_tool_call_line_with_query(self) -> None:
        """Should build sourcegraph tool call line with query."""
        inp = {"query": "test query"}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("sourcegraph", inp, get_inp)
        assert '→ sourcegraph "test query"' in result

    def test_builds_sourcegraph_tool_call_line_without_query(self) -> None:
        """Should build sourcegraph tool call line without query."""
        inp: dict[str, Any] = {}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("sourcegraph", inp, get_inp)
        assert result == "→ sourcegraph"

    def test_builds_todowrite_tool_call_line(self) -> None:
        """Should build todowrite tool call line."""
        inp: dict[str, Any] = {}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("todowrite", inp, get_inp)
        assert result == "→ todowrite"

    def test_builds_agent_tool_call_line_with_prompt(self) -> None:
        """Should build agent tool call line with prompt preview truncated to 60 chars."""
        inp = {
            "prompt": "This is a very long prompt that should be truncated"
            " because it exceeds the maximum length allowed for display purposes"
        }
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("agent", inp, get_inp)
        assert "→ agent " in result
        # Prompt is truncated to 60 chars: "→ agent " (8 chars) + 60 = 68 max
        assert len(result) <= 68

    def test_builds_agent_tool_call_line_without_prompt(self) -> None:
        """Should build agent tool call line without prompt."""
        inp: dict[str, Any] = {}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("agent", inp, get_inp)
        assert result == "→ agent"

    def test_builds_unknown_tool_call_line_with_value(self) -> None:
        """Should build unknown tool call line with value."""
        inp = {"key": "value"}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("unknown_tool", inp, get_inp)
        assert "→ unknown_tool value" in result

    def test_builds_unknown_tool_call_line_without_value(self) -> None:
        """Should build unknown tool call line without value."""
        inp: dict[str, Any] = {}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("unknown_tool", inp, get_inp)
        assert result == "→ unknown_tool"

    def test_returns_empty_string_for_empty_tool_name(self) -> None:
        """Should return empty string for empty tool name."""
        inp: dict[str, Any] = {}
        get_inp = self.build_inp(inp)
        result = _build_tool_call_line("", inp, get_inp)
        assert result == ""


# ---------------------------------------------------------------------------
# TestBackwardCompatShims
# -----------=========


class TestBackwardCompatShims:
    """Tests for backward-compatibility shim functions."""

    @patch("the_architect.core.opencode_provider._provider.is_installed")
    def test_check_opencode_installed_delegates_to_provider(
        self, mock_is_installed: MagicMock
    ) -> None:
        """Should delegate to provider.is_installed()."""
        mock_is_installed.return_value = True
        result = check_opencode_installed()
        assert result is True
        mock_is_installed.assert_called_once()

    @patch("the_architect.core.opencode_provider._provider.get_version")
    def test_get_opencode_version_delegates_to_provider(self, mock_get_version: MagicMock) -> None:
        """Should delegate to provider.get_version()."""
        mock_get_version.return_value = "1.2.3"
        result = get_opencode_version()
        assert result == "1.2.3"
        mock_get_version.assert_called_once()

    @patch("the_architect.core.opencode_provider._provider.has_any_models")
    def test_opencode_has_any_models_delegates_to_provider(
        self, mock_has_any_models: MagicMock
    ) -> None:
        """Should delegate to provider.has_any_models()."""
        mock_has_any_models.return_value = True
        result = opencode_has_any_models()
        assert result is True
        mock_has_any_models.assert_called_once()

    @patch("the_architect.core.opencode_provider._provider._write_architect_prompts")
    def test_write_architect_prompts_delegates_to_provider(
        self, mock_write: MagicMock, tmp_path: Path
    ) -> None:
        """Should delegate to provider._write_architect_prompts()."""
        write_architect_prompts(tmp_path)
        mock_write.assert_called_once_with(tmp_path)

    @patch("the_architect.core.opencode_provider._provider.find_user_config")
    def test_find_user_opencode_config_delegates_to_provider(
        self, mock_find: MagicMock, tmp_path: Path
    ) -> None:
        """Should delegate to provider.find_user_config()."""
        mock_find.return_value = Path("/tmp/config.json")
        result = find_user_opencode_config(tmp_path)
        assert result == Path("/tmp/config.json")
        mock_find.assert_called_once_with(tmp_path)

    @patch("the_architect.core.opencode_provider._provider._write_architect_config")
    def test_write_architect_config_delegates_to_provider(
        self, mock_write: MagicMock, tmp_path: Path
    ) -> None:
        """Should delegate to provider._write_architect_config()."""
        mock_write.return_value = Path("/tmp/architect.json")
        result = write_architect_config(tmp_path)
        assert result == Path("/tmp/architect.json")
        mock_write.assert_called_once_with(tmp_path)

    @patch("the_architect.core.opencode_provider._provider.list_models")
    def test_list_opencode_models_delegates_to_provider(self, mock_list: MagicMock) -> None:
        """Should delegate to provider.list_models()."""
        mock_list.return_value = ["model1", "model2"]
        result = list_opencode_models()
        assert result == ["model1", "model2"]
        mock_list.assert_called_once()

    @patch("the_architect.core.opencode_provider._provider.get_resolved_model")
    def test_get_resolved_architect_model_delegates_to_provider(
        self, mock_get: MagicMock, tmp_path: Path
    ) -> None:
        """Should delegate to provider.get_resolved_model()."""
        mock_get.return_value = "openrouter/qwen"
        result = get_resolved_architect_model(tmp_path)
        assert result == "openrouter/qwen"
        mock_get.assert_called_once_with(tmp_path, "architect")

    @patch("the_architect.core.opencode_provider._provider.list_agents")
    def test_list_opencode_agents_delegates_to_provider(
        self, mock_list: MagicMock, tmp_path: Path
    ) -> None:
        """Should delegate to provider.list_agents()."""
        mock_list.return_value = ["master", "backend"]
        result = list_opencode_agents(tmp_path)
        assert result == ["master", "backend"]
        mock_list.assert_called_once_with(tmp_path)

    @patch("the_architect.core.opencode_provider._provider.read_info")
    def test_read_opencode_info_delegates_to_provider(
        self, mock_read: MagicMock, tmp_path: Path
    ) -> None:
        """Should delegate to provider.read_info()."""

        config = ArchitectConfig().resolve(tmp_path)
        mock_read.return_value = {"agent": "master", "model": "openrouter/qwen"}
        result = read_opencode_info(tmp_path, config)
        assert result == {"agent": "master", "model": "openrouter/qwen"}
        mock_read.assert_called_once_with(tmp_path, config)

    @patch("the_architect.core.opencode_provider._provider.find_user_config")
    def test_check_user_opencode_configured_delegates_to_provider(
        self, mock_find: MagicMock, tmp_path: Path
    ) -> None:
        """Should delegate to provider.find_user_config()."""
        mock_find.return_value = Path("/tmp/config.json")
        result = check_user_opencode_configured(tmp_path)
        assert result is True
        mock_find.assert_called_once_with(tmp_path)

    @patch("the_architect.core.opencode_provider._provider.ensure_setup")
    def test_ensure_opencode_setup_delegates_to_provider(
        self, mock_ensure: MagicMock, tmp_path: Path
    ) -> None:
        """Should delegate to provider.ensure_setup()."""

        config = ArchitectConfig().resolve(tmp_path)
        mock_ensure.return_value = Path("/tmp/architect.json")
        result = ensure_opencode_setup(tmp_path, config)
        assert result == Path("/tmp/architect.json")
        mock_ensure.assert_called_once_with(tmp_path, config)


# ---------------------------------------------------------------------------
# TestSupportsFreeTierException
# ------------------------------


class TestSupportsFreeTierException:
    """Tests for exception handling in supports_free_tier()."""

    def test_returns_false_when_list_models_raises_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return False when list_models raises an exception."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with patch.object(OpenCodeProvider, "list_models", side_effect=Exception("error")):
            provider = OpenCodeProvider()
            result = provider.supports_free_tier()
            assert result is False


# ---------------------------------------------------------------------------
# TestRemainingCoverageGaps — extra tests for lines still at <100%
# ---------------------------------------------------------------------------


class TestListAgentsEdgeCases:
    """Additional tests for list_agents() to cover remaining uncovered lines."""

    @patch("the_architect.core.opencode_provider.subprocess.run")
    @patch("shutil.which", return_value="/usr/local/bin/opencode")
    def test_primary_path_skips_blank_lines(
        self, mock_which: MagicMock, mock_run: MagicMock
    ) -> None:
        """Should skip blank lines in opencode agent list output."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="\nmaster (primary)\n\nbackend (primary)\n",
            stderr="",
        )
        provider = OpenCodeProvider()
        result = provider.list_agents(Path("/tmp"))
        assert "master" in result
        assert "backend" in result

    @patch("the_architect.core.opencode_provider.subprocess.run")
    @patch("shutil.which", return_value="/usr/local/bin/opencode")
    def test_primary_path_subprocess_exception(
        self, mock_which: MagicMock, mock_run: MagicMock
    ) -> None:
        """Should fall through when opencode agent list raises exception."""
        # First call (agent list) raises, second call (debug config) also raises
        mock_run.side_effect = subprocess.TimeoutExpired("opencode", 15)
        provider = OpenCodeProvider()
        result = provider.list_agents(Path("/tmp"))
        assert result == []

    @patch("the_architect.core.opencode_provider.subprocess.run")
    @patch("shutil.which", return_value="/usr/local/bin/opencode")
    def test_debug_config_subprocess_exception(
        self, mock_which: MagicMock, mock_run: MagicMock
    ) -> None:
        """Should fall through when debug config raises exception."""
        # First call: agent list returns empty (no primary agents)
        # Second call: debug config raises
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            subprocess.TimeoutExpired("opencode", 15),
        ]
        provider = OpenCodeProvider()
        result = provider.list_agents(Path("/tmp"))
        assert result == []

    @patch("the_architect.core.opencode_provider.OpenCodeProvider.find_user_config")
    @patch("the_architect.core.opencode_provider.subprocess.run")
    @patch("shutil.which", return_value="/usr/local/bin/opencode")
    def test_opencode_json_read_error(
        self, mock_which: MagicMock, mock_run: MagicMock, mock_find: MagicMock
    ) -> None:
        """Should return [] when opencode.json read raises OSError."""
        # Both subprocess calls return no agents
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="", stderr=""),
        ]
        # find_user_config returns a path that will fail to read
        mock_find.return_value = Path("/nonexistent/opencode.json")
        provider = OpenCodeProvider()
        result = provider.list_agents(Path("/tmp"))
        assert result == []

    @patch("the_architect.core.opencode_provider.OpenCodeProvider.find_user_config")
    @patch("the_architect.core.opencode_provider.subprocess.run")
    @patch("shutil.which", return_value="/usr/local/bin/opencode")
    def test_debug_config_regex_with_primary_agents(
        self, mock_which: MagicMock, mock_run: MagicMock, mock_find: MagicMock
    ) -> None:
        """Should extract primary agents via regex when JSON parse fails."""
        # agent list returns empty
        # debug config returns truncated JSON that fails to parse
        raw_config = (
            '    "master": {\n'
            '        "mode": "primary"\n'
            "    },\n"
            '    "architect": {\n'
            '        "mode": "code"\n'
            "    }\n"
        )
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout=raw_config, stderr=""),
        ]
        mock_find.return_value = None
        provider = OpenCodeProvider()
        result = provider.list_agents(Path("/tmp"))
        assert "master" in result
        assert "architect" not in result  # architect is internal


class TestGetResolvedModelEdgeCases:
    """Additional tests for get_resolved_model() uncovered lines."""

    @patch("the_architect.core.opencode_provider.subprocess.run")
    @patch("the_architect.config.find_opencode_json")
    def test_regex_with_explicit_agent_name(
        self, mock_find: MagicMock, mock_run: MagicMock
    ) -> None:
        """Should use agent_name directly when JSON decode fails and name is given."""
        raw_config = '    "backend": {\n        "model": "openrouter/qwen"\n    }\n'
        mock_find.return_value = None
        mock_run.return_value = MagicMock(returncode=0, stdout=raw_config, stderr="")
        provider = OpenCodeProvider()
        result = provider.get_resolved_model(Path("/tmp"), "backend")
        assert result == "openrouter/qwen"

    @patch("the_architect.core.opencode_provider.subprocess.run")
    @patch("the_architect.config.find_opencode_json")
    def test_top_level_model_regex_fallback(
        self, mock_find: MagicMock, mock_run: MagicMock
    ) -> None:
        """Should find top-level model field via regex when JSON fails."""
        raw_config = '  "model": "openrouter/default-model"\n'
        mock_find.return_value = None
        mock_run.return_value = MagicMock(returncode=0, stdout=raw_config, stderr="")
        provider = OpenCodeProvider()
        result = provider.get_resolved_model(Path("/tmp"), "")
        assert result == "openrouter/default-model"

    @patch("the_architect.core.opencode_provider.subprocess.run")
    @patch("the_architect.config.find_opencode_json")
    def test_debug_config_subprocess_exception(
        self, mock_find: MagicMock, mock_run: MagicMock
    ) -> None:
        """Should return empty when debug config subprocess raises."""
        mock_find.return_value = None
        mock_run.side_effect = subprocess.TimeoutExpired("opencode", 15)
        provider = OpenCodeProvider()
        result = provider.get_resolved_model(Path("/tmp"), "master")
        assert result == ""


class TestReadInfoEdgeCases:
    """Additional tests for read_info() uncovered lines."""

    @patch(
        "the_architect.core.opencode_provider._get_default_agent_from_debug_config",
        return_value="master",
    )
    @patch("the_architect.core.opencode_provider.OpenCodeProvider.find_user_config")
    @patch("the_architect.core.opencode_provider.OpenCodeProvider.get_resolved_model")
    @patch("the_architect.core.opencode_provider.OpenCodeProvider.get_version")
    def test_config_read_error_falls_to_debug(
        self,
        mock_version: MagicMock,
        mock_resolved: MagicMock,
        mock_find: MagicMock,
        mock_default: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Should fall to debug config when config file read fails."""

        mock_version.return_value = "1.2.3"
        mock_resolved.return_value = ""
        # Create a file that will fail to parse
        bad_config = tmp_path / "opencode.json"
        bad_config.write_text("{invalid json", encoding="utf-8")
        mock_find.return_value = bad_config
        config = ArchitectConfig().resolve(tmp_path)
        provider = OpenCodeProvider()
        result = provider.read_info(tmp_path, config)
        assert result["agent"] == "master"  # from _get_default_agent_from_debug_config


class TestParseOutputLineAdditional:
    """Additional tests for parse_output_line() uncovered lines."""

    def test_tool_use_completed_with_multi_line_result(self) -> None:
        """Should handle tool_use with completed status and multi-line result."""
        import json

        event = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "bash",
                    "state": {
                        "input": {"command": "ls -la"},
                        "status": "completed",
                        "output": "file1.txt\nfile2.txt\nfile3.txt",
                        "title": "",
                        "metadata": {},
                    },
                },
            }
        )
        provider = OpenCodeProvider()
        result = provider.parse_output_line(event)
        assert result is not None
        # Should have the call line with ← result
        assert any("←" in line for line in result.display_lines)

    def test_legacy_tool_with_alt_key(self) -> None:
        """Should use alt key when primary key is missing in legacy tool format."""
        import json

        event = json.dumps(
            {
                "type": "tool",
                "tool": {
                    "name": "read",
                    "input": {"filePath": "test.py"},
                },
            }
        )
        provider = OpenCodeProvider()
        result = provider.parse_output_line(event)
        assert result is not None
        assert any("test.py" in line for line in result.display_lines)

    def test_error_event_with_error_key(self) -> None:
        """Should use error key when message key is not present."""
        import json

        event = json.dumps({"type": "error", "error": "something went wrong"})
        provider = OpenCodeProvider()
        result = provider.parse_output_line(event)
        assert result is not None
        assert any("something went wrong" in line for line in result.display_lines)

    def test_model_not_found_detection(self) -> None:
        """Should set model_not_found flag for model-not-found errors."""
        import json

        # Use a message that matches is_model_not_found_event
        event = json.dumps({"type": "error", "message": "model not found: invalid-model-name"})
        provider = OpenCodeProvider()
        result = provider.parse_output_line(event)
        assert result is not None
        assert result.model_not_found is True

    def test_usage_with_prompt_tokens_fallback(self) -> None:
        """Should use prompt_tokens when input_tokens is missing."""
        import json

        event = json.dumps(
            {
                "type": "step_finish",
                "usage": {
                    "prompt_tokens": 500,
                    "completion_tokens": 100,
                },
            }
        )
        provider = OpenCodeProvider()
        result = provider.parse_output_line(event)
        assert result is not None
        assert isinstance(result.tokens, TokenUsage)
        assert result.tokens.input_tokens == 500
        assert result.tokens.output_tokens == 100

    def test_usage_with_none_values(self) -> None:
        """Should handle None values in usage dict."""
        import json

        event = json.dumps(
            {
                "type": "step_finish",
                "usage": {
                    "input_tokens": None,
                    "output_tokens": None,
                },
            }
        )
        provider = OpenCodeProvider()
        result = provider.parse_output_line(event)
        assert result is not None
        assert isinstance(result.tokens, TokenUsage)
        assert result.tokens.input_tokens == 0
        assert result.tokens.output_tokens == 0

    def test_part_tokens_with_cache_not_dict(self) -> None:
        """Should handle cache field that is not a dict in part tokens."""
        import json

        event = json.dumps(
            {
                "type": "text",
                "part": {
                    "text": "hello",
                    "tokens": {
                        "input": 100,
                        "output": 50,
                        "cache": "not a dict",
                    },
                },
            }
        )
        provider = OpenCodeProvider()
        result = provider.parse_output_line(event)
        assert result is not None
        assert isinstance(result.tokens, TokenUsage)
        assert result.tokens.cache_read_tokens == 0
        assert result.tokens.cache_write_tokens == 0

    def test_tool_use_completed_no_result_lines(self) -> None:
        """Should show just call_line when completed but no result lines."""
        import json

        event = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "fetch",
                    "state": {
                        "input": {"url": "https://example.com"},
                        "status": "completed",
                        "output": "",
                        "title": "",
                        "metadata": {},
                    },
                },
            }
        )
        provider = OpenCodeProvider()
        result = provider.parse_output_line(event)
        assert result is not None
        assert any("fetch" in line for line in result.display_lines)

    def test_legacy_tool_with_alt_key_fallback(self) -> None:
        """Should use alt key when primary key is empty in legacy tool format."""
        import json

        # write tool with "path" missing but "filePath" present
        event = json.dumps(
            {
                "type": "tool",
                "tool": {
                    "name": "write",
                    "input": {"filePath": "test.py", "content": "hello"},
                },
            }
        )
        provider = OpenCodeProvider()
        result = provider.parse_output_line(event)
        assert result is not None
        assert any("test.py" in line for line in result.display_lines)


# ---------------------------------------------------------------------------
# TestOpenCodeProviderMissingCoverage — covers lines 132, 591-608, 621-624,
#   754, 772, 789, 794, 810-855
# ---------------------------------------------------------------------------


class TestOpenCodeProviderMissingCoverage:
    """Targeted tests for OpenCodeProvider methods not yet exercised."""

    def test_binary_name_property(self) -> None:
        """binary_name returns 'opencode' (line 132)."""
        provider = OpenCodeProvider()
        assert provider.binary_name == "opencode"

    @patch("shutil.which", return_value="/usr/local/bin/opencode")
    def test_build_command_basic(self, mock_which: MagicMock) -> None:
        """build_command returns expected command list (lines 591-598)."""
        provider = OpenCodeProvider()
        cmd = provider.build_command("do the thing")
        assert cmd[0] == "/usr/local/bin/opencode"
        assert "run" in cmd
        assert "--format" in cmd
        assert "json" in cmd
        assert "--dangerously-skip-permissions" in cmd
        assert "do the thing" in cmd

    @patch("shutil.which", return_value="/usr/local/bin/opencode")
    def test_build_command_with_model_and_agent(self, mock_which: MagicMock) -> None:
        """build_command includes --model and --agent when overrides provided (lines 600-606)."""
        provider = OpenCodeProvider()
        cmd = provider.build_command("goal", model_override="gpt-4o", agent_override="backend")
        assert "--model" in cmd
        assert "gpt-4o" in cmd
        assert "--agent" in cmd
        assert "backend" in cmd

    def test_get_env_overrides_with_config(self, tmp_path: Path) -> None:
        """get_env_overrides sets OPENCODE_CONFIG when config_override provided (lines 621-624)."""
        provider = OpenCodeProvider()
        config_file = tmp_path / "opencode.json"
        config_file.write_text("{}")
        env = provider.get_env_overrides(config_override=config_file)
        assert "OPENCODE_CONFIG" in env
        assert str(config_file.resolve()) in env["OPENCODE_CONFIG"]

    def test_error_event_with_rate_limit_signal(self) -> None:
        """Error event triggers rate_limit flag when is_rate_limit_event matches (line 754)."""
        import json as _json

        provider = OpenCodeProvider()
        event = _json.dumps(
            {"type": "error", "message": "rate limit exceeded, retry after 60s"}
        )
        result = provider.parse_output_line(event)
        assert result is not None
        assert result.rate_limit is True

    def test_supports_json_output_returns_true(self) -> None:
        """OpenCode emits JSON events, so supports_json_output is True (line 772)."""
        provider = OpenCodeProvider()
        assert provider.supports_json_output() is True

    def test_supports_free_tier_with_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fast path: OPENROUTER_API_KEY set → supports_free_tier True (line 789)."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-value")
        provider = OpenCodeProvider()
        assert provider.supports_free_tier() is True

    def test_supports_free_tier_slow_path_openrouter_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Slow path: openrouter/ model in list → supports_free_tier True (line 794)."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with patch.object(OpenCodeProvider, "list_models", return_value=["openrouter/gpt-4o"]):
            provider = OpenCodeProvider()
            assert provider.supports_free_tier() is True

    def test_check_update_returns_empty_when_not_installed(self) -> None:
        """Early return when opencode not installed (line 813)."""
        provider = OpenCodeProvider()
        with patch.object(provider, "is_installed", return_value=False):
            assert provider.check_update_available() == ""

    def test_check_update_returns_empty_when_version_unknown(self) -> None:
        """Early return when version is 'unknown' (line 818)."""
        provider = OpenCodeProvider()
        with patch.object(provider, "is_installed", return_value=True):
            with patch.object(provider, "get_version", return_value="unknown"):
                assert provider.check_update_available() == ""

    def test_check_update_returns_empty_when_no_semver(self) -> None:
        """Early return when version string has no semver (line 823)."""
        provider = OpenCodeProvider()
        with patch.object(provider, "is_installed", return_value=True):
            with patch.object(provider, "get_version", return_value="nightly"):
                assert provider.check_update_available() == ""

    def test_check_update_returns_empty_on_network_error(self) -> None:
        """Returns '' on urllib failure (line 838-840)."""
        provider = OpenCodeProvider()
        with patch.object(provider, "is_installed", return_value=True):
            with patch.object(provider, "get_version", return_value="1.14.28"):
                with patch("urllib.request.urlopen", side_effect=OSError("network")):
                    assert provider.check_update_available() == ""

    def test_check_update_returns_message_when_newer_available(self) -> None:
        """Returns update message when installed < latest (lines 849-853)."""
        import json as _json
        from unittest.mock import Mock

        mock_resp = Mock()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_resp.read.return_value = _json.dumps({"version": "2.0.0"}).encode("utf-8")

        provider = OpenCodeProvider()
        with patch.object(provider, "is_installed", return_value=True):
            with patch.object(provider, "get_version", return_value="1.14.28"):
                with patch("urllib.request.urlopen", return_value=mock_resp):
                    result = provider.check_update_available()
                    assert "1.14.28" in result
                    assert "2.0.0" in result
                    assert "opencode upgrade" in result

    def test_check_update_returns_empty_when_already_up_to_date(self) -> None:
        """Returns '' when installed version equals latest (line 855)."""
        import json as _json
        from unittest.mock import Mock

        mock_resp = Mock()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_resp.read.return_value = _json.dumps({"version": "1.14.28"}).encode("utf-8")

        provider = OpenCodeProvider()
        with patch.object(provider, "is_installed", return_value=True):
            with patch.object(provider, "get_version", return_value="1.14.28"):
                with patch("urllib.request.urlopen", return_value=mock_resp):
                    assert provider.check_update_available() == ""

    def test_check_update_returns_empty_when_no_version_in_response(self) -> None:
        """Returns '' when registry response has no version field (line 836-837)."""
        import json as _json
        from unittest.mock import Mock

        mock_resp = Mock()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_resp.read.return_value = _json.dumps({}).encode("utf-8")

        provider = OpenCodeProvider()
        with patch.object(provider, "is_installed", return_value=True):
            with patch.object(provider, "get_version", return_value="1.14.28"):
                with patch("urllib.request.urlopen", return_value=mock_resp):
                    assert provider.check_update_available() == ""
