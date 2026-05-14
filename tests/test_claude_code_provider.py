"""Comprehensive tests for the_architect.core.claude_code_provider module.

Coverage targets:
- ClaudeCodeProvider class methods (get_version, has_any_models, install_hint,
  list_models, list_agents, get_resolved_model, find_user_config, ensure_setup)
- Module-level helper functions (_read_claude_md_agents, _read_claude_md_model,
  _parse_claude_models_output, _parse_claude_agents_output, _parse_claude_default_model,
  _find_claude_binary, _extract_models_from_binary, _pick_default_model)
- Output parsing corner cases (parse_output_line)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Import the module and helper functions directly for testing
from the_architect.core import claude_code_provider as cc


@pytest.fixture
def provider():
    """Fixture for a fresh ClaudeCodeProvider instance."""
    return cc.ClaudeCodeProvider()


@pytest.fixture
def tmp_claude_md(tmp_path):
    """Create a CLAUDE.md file in tmp_path."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# CLAUDE.md\n\nModel: claude-sonnet-4-6\n")
    return claude_md


class TestClaudeCodeProviderGetVersion:
    """Tests for ClaudeCodeProvider.get_version()."""

    def test_version_cache_hit(self, provider):
        """Test that cached version is returned without subprocess call."""
        provider._version_cache = "1.0.0"
        assert provider.get_version() == "1.0.0"

    def test_version_success_subprocess(self, provider):
        """Test successful version call with stdout output."""
        with patch.object(cc.subprocess, "run") as mock_run:
            result_mock = Mock()
            result_mock.returncode = 0
            result_mock.stdout = "claude-code v1.0.0"
            result_mock.stderr = ""
            mock_run.return_value = result_mock
            assert provider.get_version() == "claude-code v1.0.0"
            mock_run.assert_called_once_with(
                ["claude", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )

    def test_version_uses_stderr_as_fallback(self, provider):
        """Test that stderr is used when stdout is empty."""
        with patch.object(cc.subprocess, "run") as mock_run:
            result_mock = Mock()
            result_mock.returncode = 0
            result_mock.stdout = ""
            result_mock.stderr = "claude-code v1.0.0"
            mock_run.return_value = result_mock
            assert provider.get_version() == "claude-code v1.0.0"

    def test_version_unknown_on_returncode_nonzero(self, provider):
        """Test that 'unknown' is returned when returncode is non-zero."""
        with patch.object(cc.subprocess, "run") as mock_run:
            result_mock = Mock()
            result_mock.returncode = 1
            result_mock.stdout = ""
            result_mock.stderr = "error"
            mock_run.return_value = result_mock
            assert provider.get_version() == "unknown"

    def test_version_unknown_on_timeout(self, provider):
        """Test that 'unknown' is returned on subprocess timeout."""
        with patch.object(
            cc.subprocess, "run", side_effect=cc.subprocess.TimeoutExpired("cmd", 10)
        ):
            assert provider.get_version() == "unknown"

    def test_version_unknown_on_subprocess_error(self, provider):
        """Test that 'unknown' is returned on subprocess errors."""
        with patch.object(cc.subprocess, "run", side_effect=cc.subprocess.SubprocessError()):
            assert provider.get_version() == "unknown"

    def test_version_unknown_on_file_not_found(self, provider):
        """Test that 'unknown' is returned when claude binary is not found."""
        with patch.object(cc.subprocess, "run", side_effect=FileNotFoundError()):
            assert provider.get_version() == "unknown"


class TestClaudeCodeProviderHasAnyModels:
    """Tests for ClaudeCodeProvider.has_any_models()."""

    def test_has_models_installed_and_success(self, provider):
        """Test that True is returned when claude is installed and works."""
        with patch.object(shutil, "which", return_value="/usr/bin/claude"):
            with patch.object(cc.subprocess, "run") as mock_run:
                result_mock = Mock()
                result_mock.returncode = 0
                result_mock.stdout = "claude-code v1.0.0"
                mock_run.return_value = result_mock
                assert provider.has_any_models() is True

    def test_has_models_not_installed(self, provider):
        """Test that False is returned when claude is not installed."""
        with patch.object(shutil, "which", return_value=None):
            assert provider.has_any_models() is False

    def test_has_models_subprocess_failure(self, provider):
        """Test that False is returned when subprocess call fails."""
        with patch.object(shutil, "which", return_value="/usr/bin/claude"):
            with patch.object(cc.subprocess, "run", side_effect=cc.subprocess.SubprocessError()):
                assert provider.has_any_models() is False

    def test_has_models_timeout(self, provider):
        """Test that False is returned on subprocess timeout."""
        with patch.object(shutil, "which", return_value="/usr/bin/claude"):
            with patch.object(
                cc.subprocess, "run", side_effect=cc.subprocess.TimeoutExpired("cmd", 10)
            ):
                assert provider.has_any_models() is False


class TestClaudeCodeProviderInstallHint:
    """Tests for ClaudeCodeProvider.install_hint()."""

    def test_install_hint_with_npm(self, provider):
        """Test that npm install command is returned when npm is available."""
        with patch.object(shutil, "which", return_value="/usr/bin/npm"):
            assert provider.install_hint() == "npm install -g @anthropic-ai/claude-code"

    def test_install_hint_without_npm(self, provider):
        """Test that URL hint is returned when npm is not available."""
        with patch.object(shutil, "which", return_value=None):
            assert provider.install_hint() == "see https://docs.anthropic.com/en/docs/claude-code"


class TestClaudeCodeProviderListModels:
    """Tests for ClaudeCodeProvider.list_models()."""

    def test_list_models_binary_cache_hit(self, provider):
        """Test that cached binary models are returned."""
        with patch.object(cc.subprocess, "run", side_effect=FileNotFoundError):
            provider._binary_models_cache = ["claude-sonnet-4-6", "claude-opus-4-7"]
            result = provider.list_models()
            assert result == ["claude-sonnet-4-6", "claude-opus-4-7"]

    def test_list_models_binary_extraction_success(self, provider):
        """Test binary model extraction when cache is empty."""
        with patch.object(cc.subprocess, "run", side_effect=FileNotFoundError):
            with patch.object(cc, "_extract_models_from_binary") as mock_extract:
                mock_extract.return_value = ["claude-sonnet-4-6", "claude-opus-4-7"]
                provider._binary_models_cache = None
                result = provider.list_models()
                assert result == ["claude-sonnet-4-6", "claude-opus-4-7"]
                mock_extract.assert_called_once()

    def test_list_models_subprocess_fallback(self, provider):
        """Test subprocess fallback when binary extraction returns empty."""
        with patch.object(cc, "_extract_models_from_binary", return_value=[]):
            with patch.object(cc.subprocess, "run") as mock_run:
                result_mock = Mock()
                result_mock.returncode = 0
                result_mock.stdout = (
                    "| Model | ID |\n|---|---|\n| **Sonnet** | `claude-sonnet-4` |\n"
                )
                mock_run.return_value = result_mock
                with patch.object(
                    cc, "_parse_claude_models_output", return_value=["claude-sonnet-4"]
                ):
                    result = provider.list_models()
                    assert "claude-sonnet-4" in result

    def test_list_models_fallback_to_static(self, provider):
        """Test static fallback when all methods fail."""
        with patch.object(cc, "_extract_models_from_binary", return_value=[]):
            with patch.object(cc.subprocess, "run", side_effect=cc.subprocess.SubprocessError()):
                result = provider.list_models()
                assert result == list(cc._FALLBACK_CLAUDE_MODELS)


class TestClaudeCodeProviderListAgents:
    """Tests for ClaudeCodeProvider.list_agents()."""

    def test_list_agents_subprocess_success(self, provider, tmp_path):
        """Test successful subprocess call with agents."""
        with patch.object(cc.subprocess, "run") as mock_run:
            result_mock = Mock()
            result_mock.returncode = 0
            result_mock.stdout = "Built-in agents:\n  build · sonnet\n  frontend · sonnet\n"
            mock_run.return_value = result_mock
            with patch.object(cc, "_parse_claude_agents_output") as mock_parse:
                mock_parse.return_value = ["build", "frontend"]
                result = provider.list_agents(tmp_path)
                assert result == ["build", "frontend"]

    def test_list_agents_subprocess_failure_fallback_to_claude_md(self, provider, tmp_path):
        """Test CLAUDE.md fallback when subprocess fails."""
        with patch.object(cc.subprocess, "run", side_effect=cc.subprocess.SubprocessError()):
            with patch.object(cc, "_read_claude_md_agents") as mock_read:
                mock_read.return_value = ["build", "backend"]
                result = provider.list_agents(tmp_path)
                assert result == ["backend", "build"]

    def test_list_agents_fallback_to_build(self, provider, tmp_path):
        """Test default fallback to ['build'] when all methods fail."""
        with patch.object(cc.subprocess, "run", side_effect=cc.subprocess.SubprocessError()):
            with patch.object(cc, "_read_claude_md_agents", return_value=[]):
                result = provider.list_agents(tmp_path)
                assert result == ["build"]

    def test_list_agents_excludes_intelligence_agent(self, provider, tmp_path):
        """Test that 'intelligence' internal agent is excluded from list_agents() results."""
        with patch.object(cc.subprocess, "run", side_effect=cc.subprocess.SubprocessError()):
            with patch.object(
                cc, "_read_claude_md_agents", return_value=["intelligence", "build", "backend"]
            ):
                result = provider.list_agents(tmp_path)
                assert "intelligence" not in result
                assert "build" in result
                assert "backend" in result


class TestClaudeCodeProviderGetResolvedModel:
    """Tests for ClaudeCodeProvider.get_resolved_model()."""

    def test_resolved_model_cache_hit_stream(self, provider):
        """Test that stream cache is returned."""
        provider._resolved_model_cache["_stream"] = "claude-sonnet-4-6"
        result = provider.get_resolved_model(Path("/tmp"))
        assert result == "claude-sonnet-4-6"

    def test_resolved_model_cache_hit_project_dir(self, provider):
        """Test that project directory cache is returned."""
        provider._resolved_model_cache["/tmp"] = "claude-opus-4"
        result = provider.get_resolved_model(Path("/tmp"))
        assert result == "claude-opus-4"

    def test_resolved_model_env_var_anthropic(self, provider, tmp_path, monkeypatch):
        """Test ANTHROPIC_MODEL environment variable resolution."""
        monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-4")
        result = provider.get_resolved_model(tmp_path)
        assert result == "claude-haiku-4"

    def test_resolved_model_env_var_claude(self, provider, tmp_path, monkeypatch):
        """Test CLAUDE_MODEL environment variable resolution."""
        monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-4-6")
        result = provider.get_resolved_model(tmp_path)
        assert result == "claude-sonnet-4-6"

    def test_resolved_model_claude_md(self, provider, tmp_path):
        """Test CLAUDE.md model hint resolution."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("Model: claude-sonnet-4-5\n")
        result = provider.get_resolved_model(tmp_path)
        assert result == "claude-sonnet-4-5"

    def test_resolved_model_binary_extraction(self, provider, tmp_path):
        """Test binary extraction fallback."""
        with patch.object(cc, "_read_claude_md_model", return_value=""):
            with patch.object(cc.subprocess, "run", side_effect=cc.subprocess.SubprocessError()):
                with patch.object(cc, "_extract_models_from_binary") as mock_extract:
                    mock_extract.return_value = ["claude-sonnet-4", "claude-opus-3"]
                    with patch.object(cc, "_pick_default_model", return_value="claude-sonnet-4"):
                        result = provider.get_resolved_model(tmp_path)
                        assert result == "claude-sonnet-4"

    def test_resolved_model_subprocess_fallback(self, provider, tmp_path):
        """Test subprocess fallback when binary extraction fails."""
        with patch.object(cc, "_read_claude_md_model", return_value=""):
            with patch.object(cc, "_extract_models_from_binary", return_value=[]):
                with patch.object(cc.subprocess, "run") as mock_run:
                    result_mock = Mock()
                    result_mock.returncode = 0
                    result_mock.stdout = "Default to **Sonnet 4.6**"
                    mock_run.return_value = result_mock
                    with patch.object(
                        cc, "_parse_claude_default_model", return_value="claude-sonnet-4-6"
                    ):
                        result = provider.get_resolved_model(tmp_path)
                        assert result == "claude-sonnet-4-6"

    def test_resolved_model_empty_fallback(self, provider, tmp_path):
        """Test that empty string is returned when all methods fail."""
        with patch.object(cc, "_read_claude_md_model", return_value=""):
            with patch.object(cc, "_extract_models_from_binary", return_value=[]):
                with patch.object(
                    cc.subprocess, "run", side_effect=cc.subprocess.SubprocessError()
                ):
                    result = provider.get_resolved_model(tmp_path)
                    assert result == ""


class TestClaudeCodeProviderFindUserConfig:
    """Tests for ClaudeCodeProvider.find_user_config()."""

    def test_find_user_config_project_local_exists(self, provider, tmp_path):
        """Test that project-local CLAUDE.md is returned when it exists."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.touch()
        result = provider.find_user_config(tmp_path)
        assert result == claude_md

    def test_find_user_config_global_only(self, provider, tmp_path):
        """Test that global CLAUDE.md is returned when project-local doesn't exist."""
        # Create global CLAUDE.md
        global_claude = Path.home() / ".claude" / "CLAUDE.md"
        global_claude.parent.mkdir(parents=True, exist_ok=True)
        global_claude.touch()
        try:
            result = provider.find_user_config(tmp_path)
            assert result == global_claude
        finally:
            # Cleanup
            global_claude.unlink(missing_ok=True)
            # Only remove the directory if it's empty (may contain other files)
            try:
                global_claude.parent.rmdir()
            except OSError:
                pass

    def test_find_user_config_no_file(self, provider, tmp_path):
        """Test that None is returned when no CLAUDE.md exists."""
        result = provider.find_user_config(tmp_path)
        assert result is None


class TestClaudeCodeProviderEnsureSetup:
    """Tests for ClaudeCodeProvider.ensure_setup()."""

    def test_ensure_setup_converts_string_to_path(self, provider, tmp_path):
        """Test that string project_dir is converted to Path."""
        result = provider.ensure_setup(str(tmp_path), Mock())
        assert isinstance(result, Path)
        assert result.exists()

    def test_write_architect_prompts_includes_intelligence_md(self, provider, tmp_path):
        """Test that intelligence.md is written to .architect/prompts/."""
        provider._write_architect_prompts(tmp_path)
        assert (tmp_path / ".architect" / "prompts" / "intelligence.md").exists()


class TestReadClaudeMdAgents:
    """Tests for _read_claude_md_agents()."""

    def test_read_claude_md_agents_no_file(self, tmp_path):
        """Test that empty list is returned when CLAUDE.md doesn't exist."""
        result = cc._read_claude_md_agents(tmp_path)
        assert result == []

    def test_read_claude_md_agents_with_agents_section(self, tmp_path):
        """Test that agents are extracted from CLAUDE.md."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("## Agents\n- build\n- frontend\n")
        result = cc._read_claude_md_agents(tmp_path)
        assert result == ["build", "frontend"]

    def test_read_claude_md_agents_without_agents_section(self, tmp_path):
        """Test that empty list is returned when no agents section exists."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# No agents here\n")
        result = cc._read_claude_md_agents(tmp_path)
        assert result == []

    def test_read_claude_md_agents_os_error(self, tmp_path):
        """Test that empty list is returned on OSError."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.touch()
        # Make file unreadable
        claude_md.chmod(0o000)
        try:
            result = cc._read_claude_md_agents(tmp_path)
            assert result == []
        finally:
            # Restore permissions
            claude_md.chmod(0o644)


class TestReadClaudeMdModel:
    """Tests for _read_claude_md_model()."""

    def test_read_claude_md_model_no_file(self, tmp_path):
        """Test that empty string is returned when CLAUDE.md doesn't exist."""
        result = cc._read_claude_md_model(tmp_path)
        assert result == ""

    def test_read_claude_md_model_with_model_line(self, tmp_path):
        """Test that model is extracted from CLAUDE.md."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("Model: claude-sonnet-4-6\n")
        result = cc._read_claude_md_model(tmp_path)
        assert result == "claude-sonnet-4-6"

    def test_read_claude_md_model_no_match(self, tmp_path):
        """Test that empty string is returned when no model line matches."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("No model here\n")
        result = cc._read_claude_md_model(tmp_path)
        assert result == ""

    def test_read_claude_md_model_os_error(self, tmp_path):
        """Test that empty string is returned on OSError."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.touch()
        # Make file unreadable
        claude_md.chmod(0o000)
        try:
            result = cc._read_claude_md_model(tmp_path)
            assert result == ""
        finally:
            # Restore permissions
            claude_md.chmod(0o644)


class TestParseClaudeModelsOutput:
    """Tests for _parse_claude_models_output()."""

    def test_parse_claude_models_output_with_table(self):
        """Test that model IDs are extracted from markdown table."""
        stdout = """| Model | ID |
|---|---|
| **Opus 4** | `claude-opus-4` |
| **Sonnet 4** | `claude-sonnet-4` |
"""
        result = cc._parse_claude_models_output(stdout)
        assert "claude-opus-4" in result
        assert "claude-sonnet-4" in result

    def test_parse_claude_models_output_empty(self):
        """Test that empty list is returned for empty output."""
        result = cc._parse_claude_models_output("")
        assert result == []

    def test_parse_claude_models_output_no_claude_models(self):
        """Test that non-claude backtick content is filtered out."""
        stdout = "| Model | ID |\n|---|---|\n| Test | `test-model` |\n"
        result = cc._parse_claude_models_output(stdout)
        assert result == []


class TestParseClaudeAgentsOutput:
    """Tests for _parse_claude_agents_output()."""

    def test_parse_claude_agents_output_with_agents(self):
        """Test that agent names are extracted from output."""
        stdout = """Built-in agents:
  build · sonnet
  frontend · haiku
"""
        result = cc._parse_claude_agents_output(stdout)
        assert "build" in result
        assert "frontend" in result

    def test_parse_claude_agents_output_empty(self):
        """Test that empty list is returned for empty output."""
        result = cc._parse_claude_agents_output("")
        assert result == []

    def test_parse_claude_agents_output_no_matches(self):
        """Test that empty list is returned when no agent lines match."""
        stdout = "No agents here"
        result = cc._parse_claude_agents_output(stdout)
        assert result == []


class TestParseClaudeDefaultModel:
    """Tests for _parse_claude_default_model()."""

    def test_parse_claude_default_model_currently_running_format(self):
        """Test parsing 'currently running on' format."""
        stdout = """| Model | ID |
|---|---|
| **Sonnet 4.6** | `claude-sonnet-4-6` |

You're currently running on **Sonnet 4.6**.
"""
        result = cc._parse_claude_default_model(stdout)
        assert result == "claude-sonnet-4-6"

    def test_parse_claude_default_model_default_to_format(self):
        """Test parsing 'Default to' format."""
        stdout = """| Model | ID |
|---|---|
| **Sonnet 4** | `claude-sonnet-4` |

Default to Sonnet 4 for most tasks.
"""
        result = cc._parse_claude_default_model(stdout)
        assert result == "claude-sonnet-4"

    def test_parse_claude_default_model_fallback_to_first(self):
        """Test fallback to first model when no prose line matches."""
        stdout = """| Model | ID |
|---|---|
| **Sonnet** | `claude-sonnet-4` |
| **Opus** | `claude-opus-4` |
"""
        result = cc._parse_claude_default_model(stdout)
        assert result == "claude-sonnet-4"

    def test_parse_claude_default_model_empty_output(self):
        """Test that empty string is returned for empty output."""
        result = cc._parse_claude_default_model("")
        assert result == ""


class TestFindClaudeBinary:
    """Tests for _find_claude_binary()."""

    def test_find_claude_binary_found(self):
        """Test that binary path is returned when found."""
        with patch.object(shutil, "which", return_value="/usr/bin/claude"):
            with patch("pathlib.Path.is_file", return_value=True):
                result = cc._find_claude_binary()
                assert result is not None

    def test_find_claude_binary_not_found(self):
        """Test that None is returned when binary is not found."""
        with patch.object(shutil, "which", return_value=None):
            result = cc._find_claude_binary()
            assert result is None

    def test_find_claude_binary_symlink_resolution(self, tmp_path):
        """Test that symlinks are resolved to actual binary."""
        # Create a mock binary file
        mock_bin = tmp_path / "real_claude"
        mock_bin.touch()

        with patch.object(shutil, "which", return_value=str(mock_bin)):
            with patch.object(Path, "resolve", return_value=Path("/opt/claude/bin/claude")):
                resolved_path = Path("/opt/claude/bin/claude")

                # Mock the is_file method
                with patch("pathlib.Path.is_file", return_value=True):
                    result = cc._find_claude_binary()
                    assert result == resolved_path

    def test_find_claude_binary_os_error(self):
        """Test that None is returned on OSError during resolution."""
        with patch.object(shutil, "which", return_value="/usr/bin/claude"):
            with patch("pathlib.Path.resolve", side_effect=OSError("resolution failed")):
                result = cc._find_claude_binary()
                assert result is None


class TestExtractModelsFromBinary:
    """Tests for _extract_models_from_binary()."""

    def test_extract_models_from_binary_not_found(self):
        """Test that empty list is returned when binary is not found."""
        with patch.object(cc, "_find_claude_binary", return_value=None):
            result = cc._extract_models_from_binary()
            assert result == []

    def test_extract_models_from_binary_os_error(self, tmp_path):
        """Test that empty list is returned on OSError reading file."""
        binary_path = tmp_path / "claude"
        binary_path.touch()
        # Make file unreadable
        binary_path.chmod(0o000)
        with patch.object(cc, "_find_claude_binary", return_value=binary_path):
            result = cc._extract_models_from_binary()
            assert result == []
        # Restore permissions
        binary_path.chmod(0o755)


class TestPickDefaultModel:
    """Tests for _pick_default_model()."""

    def test_pick_default_model_empty_list(self):
        """Test that empty string is returned for empty list."""
        result = cc._pick_default_model([])
        assert result == ""

    def test_pick_default_model_short_alias_selection(self):
        """Test that short aliases are preferred."""
        models = ["claude-sonnet-4-6", "claude-sonnet-4-6-20251101"]
        result = cc._pick_default_model(models)
        assert result == "claude-sonnet-4-6"

    def test_pick_default_model_sonnet_priority(self):
        """Test that Sonnet is preferred over Opus and Haiku."""
        models = ["claude-sonnet-4", "claude-opus-4", "claude-haiku-4"]
        result = cc._pick_default_model(models)
        assert result == "claude-sonnet-4"

    def test_pick_default_model_opus_priority(self):
        """Test that Opus is preferred when Sonnet not available."""
        models = ["claude-opus-4", "claude-haiku-4"]
        result = cc._pick_default_model(models)
        assert result == "claude-opus-4"

    def test_pick_default_model_haiku_priority(self):
        """Test that Haiku is preferred when only it's available."""
        models = ["claude-haiku-4"]
        result = cc._pick_default_model(models)
        assert result == "claude-haiku-4"


class TestParseOutputLine:
    """Tests for parse_output_line edge cases."""

    def test_parse_output_line_rate_limit_event_type_error(self, provider):
        """Test that cooldown_until defaults to 0 on TypeError/ValueError."""
        line = json.dumps(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {"status": "rejected", "resetsAt": "not_a_number"},
            }
        )
        result = provider.parse_output_line(line)
        assert result.cooldown_until == 0

    def test_parse_output_line_tool_use_with_name(self, provider):
        """Test that tool_use event with name is handled."""
        line = json.dumps({"type": "tool_use", "name": "bash", "input": {}})
        result = provider.parse_output_line(line)
        assert "bash" in result.display_lines[0]

    def test_parse_output_line_unknown_event_type(self, provider):
        """Test that unknown event type is consumed silently."""
        line = json.dumps({"type": "unknown_event", "data": "test"})
        result = provider.parse_output_line(line)
        assert result.display_lines == []

    def test_parse_output_line_empty_display_lines_branch(self, provider):
        """Test that empty display_lines returns silent event."""
        line = json.dumps({"type": "assistant", "message": {"content": []}})
        result = provider.parse_output_line(line)
        assert result.display_lines == []

    def test_parse_output_line_result_event_rate_limit(self, provider):
        """Test rate limit detection in result event."""
        line = json.dumps({"type": "result", "subtype": "error", "result": "rate limit exceeded"})
        result = provider.parse_output_line(line)
        assert result.rate_limit is True

    def test_parse_output_line_result_event_model_not_found(self, provider):
        """Test model not found detection in result event."""
        line = json.dumps({"type": "result", "subtype": "error", "result": "model not found"})
        result = provider.parse_output_line(line)
        assert result.model_not_found is True

    def test_parse_output_line_assistant_with_thinking_content(self, provider):
        """Test that assistant events with thinking content are silent."""
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "thinking", "text": "thinking..."}]},
            }
        )
        result = provider.parse_output_line(line)
        assert result.display_lines == []

    def test_parse_output_line_assistant_with_tool_use_content(self, provider):
        """Test that assistant events with tool_use content parts display the tool name."""
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "bash", "input": {}}]},
            }
        )
        result = provider.parse_output_line(line)
        assert result.display_lines == ["→ bash"]

    def test_parse_output_line_assistant_tool_use_with_path(self, provider):
        """Test that tool_use content parts include the most informative input field."""
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {"file_path": "/some/file.py"},
                        }
                    ]
                },
            }
        )
        result = provider.parse_output_line(line)
        assert result.display_lines == ["→ Read /some/file.py"]

    def test_parse_output_line_system_event_updates_cache(self, provider):
        """Test that system event updates the model cache."""
        line = json.dumps({"type": "system", "model": "claude-sonnet-4-6", "init": {}})
        result = provider.parse_output_line(line)
        assert result.display_lines == []
        # Check that cache was updated
        assert "_stream" in provider._resolved_model_cache
        assert provider._resolved_model_cache["_stream"] == "claude-sonnet-4-6"

    def test_parse_output_line_assistant_with_rate_limit_error(self, provider):
        """Test that assistant events with rate limit errors are silent but set rate_limit."""
        line = json.dumps({"type": "assistant", "error": "rate limit", "message": {"content": []}})
        result = provider.parse_output_line(line)
        assert result.display_lines == []
        assert result.rate_limit is True


class TestClaudeCodeProviderCommandBuilding:
    """Tests for ClaudeCodeProvider command building methods."""

    def test_command_building_basic(self, provider):
        """Test basic command building — instruction is now delivered via stdin."""
        cmd = provider.build_command("test instruction")
        assert "claude" in cmd[0]
        assert "--dangerously-skip-permissions" in cmd
        assert "--print" in cmd
        # Instruction is NOT in the command list; it is written to stdin.
        assert "test instruction" not in cmd
        assert provider.instruction_via_stdin is True

    def test_command_building_with_model_override(self, provider):
        """Test command building with model override."""
        cmd = provider.build_command("test", model_override="claude-sonnet-4-6")
        assert "--model" in cmd
        assert "claude-sonnet-4-6" in cmd

    def test_command_building_with_agent_override(self, provider):
        """Test that agent override is passed via --agent flag."""
        cmd = provider.build_command("test", agent_override="backend")
        assert "--agent" in cmd
        assert "backend" in cmd

    def test_supports_agents_returns_true(self, provider):
        """Test that supports_agents returns True (Claude Code supports --agent)."""
        assert provider.supports_agents() is True

    def test_supports_json_output_returns_false(self, provider):
        """Test that supports_json_output returns False."""
        assert provider.supports_json_output() is False

    def test_get_architect_prompt(self, provider):
        """Test that architect prompt is loaded."""
        prompt = provider.get_architect_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_get_reviewer_prompt(self, provider):
        """Test that reviewer prompt is loaded."""
        prompt = provider.get_reviewer_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_get_env_overrides(self, provider):
        """Test that env overrides are empty."""
        env = provider.get_env_overrides()
        assert isinstance(env, dict)
        assert env == {}


class TestClaudeCodeProviderProperties:
    """Tests for ClaudeCodeProvider properties."""

    def test_name_property(self, provider):
        """Test the name property."""
        assert provider.name == "claude-code"

    def test_display_name_property(self, provider):
        """Test the display_name property."""
        assert provider.display_name == "Claude Code"

    def test_binary_name_property(self, provider):
        """Test the binary_name property."""
        assert provider.binary_name == "claude"

    def test_parse_output_line_result_event_rate_limit(self, provider):
        """Test rate limit detection in result event."""
        line = json.dumps({"type": "result", "subtype": "error", "result": "rate limit exceeded"})
        result = provider.parse_output_line(line)
        assert result.rate_limit is True

    def test_parse_output_line_result_event_model_not_found(self, provider):
        """Test model not found detection in result event."""
        line = json.dumps({"type": "result", "subtype": "error", "result": "model not found"})
        result = provider.parse_output_line(line)
        assert result.model_not_found is True

    def test_parse_output_line_assistant_with_thinking_content(self, provider):
        """Test that assistant events with thinking content are silent."""
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "thinking", "text": "thinking..."}]},
            }
        )
        result = provider.parse_output_line(line)
        assert result.display_lines == []

    def test_parse_output_line_assistant_with_tool_use_content(self, provider):
        """Test that assistant events with tool_use content parts display the tool name."""
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "bash", "input": {}}]},
            }
        )
        result = provider.parse_output_line(line)
        assert result.display_lines == ["→ bash"]

    def test_parse_output_line_system_event_updates_cache(self, provider):
        """Test that system event updates the model cache."""
        line = json.dumps({"type": "system", "model": "claude-sonnet-4-6", "init": {}})
        result = provider.parse_output_line(line)
        assert result.display_lines == []
        # Check that cache was updated
        assert "_stream" in provider._resolved_model_cache
        assert provider._resolved_model_cache["_stream"] == "claude-sonnet-4-6"

    def test_parse_output_line_assistant_with_rate_limit_error(self, provider):
        """Test that assistant events with rate limit errors are silent but set rate_limit."""
        line = json.dumps({"type": "assistant", "error": "rate limit", "message": {"content": []}})
        result = provider.parse_output_line(line)
        assert result.display_lines == []
        assert result.rate_limit is True


class TestListModelsDebugLog:
    """Test for line 193 - debug log when claude models returns non-zero."""

    def test_list_models_debug_log_on_nonzero_returncode(self, provider):
        """Test debug log when subprocess returns non-zero returncode."""
        with patch.object(cc, "_extract_models_from_binary", return_value=[]):
            with patch.object(cc.subprocess, "run") as mock_run:
                result_mock = Mock()
                result_mock.returncode = 1
                result_mock.stdout = ""
                mock_run.return_value = result_mock
                result = provider.list_models()
                # Should return fallback models
                assert result == list(cc._FALLBACK_CLAUDE_MODELS)


class TestParseOutputLineEmptyDisplay:
    """Test for line 645 - parse_output_line with etype and empty display_lines."""

    def test_parse_output_line_empty_display_lines_tool_use_no_name(self, provider):
        """Test parse_output_line with tool_use event and empty display lines."""
        # Tool use event with no name - should have empty display_lines
        line = json.dumps({"type": "tool_use", "name": "", "input": {}})
        result = provider.parse_output_line(line)
        assert result.display_lines == []
        assert result.event_type == "tool_use"


class TestParseClaudeDefaultModelDoubleDash:
    """Test for line 883 - double dash collapse in display name pattern."""

    def test_parse_claude_default_model_double_dash_collapse(self):
        """Test that double dashes in display name are collapsed."""
        stdout = (
            "| Model | ID |\n"
            "|---|---|\n"
            "| **Claude  Sonnet 4.6** | `claude-sonnet-4-6` |\n"
            "You're currently running on **Claude  Sonnet 4.6**.\n"
        )
        result = cc._parse_claude_default_model(stdout)
        assert result == "claude-sonnet-4-6"


class TestExtractModelsFromBinaryCoverage:
    """Tests for lines 959-990 - binary extraction main scanning loop."""

    def test_extract_models_from_binary_success(self, tmp_path):
        """Test that model IDs are extracted from a binary file."""
        binary_path = tmp_path / "claude"
        # Write binary content with embedded model IDs
        binary_path.write_bytes(
            b"some binary data\x00claude-sonnet-4-6\x00more data\x00claude-opus-4-7\x00"
        )
        with patch.object(cc, "_find_claude_binary", return_value=binary_path):
            result = cc._extract_models_from_binary()
            assert "claude-sonnet-4-6" in result
            assert "claude-opus-4-7" in result

    def test_extract_models_from_binary_chunk_overlap(self, tmp_path):
        """Test binary extraction with chunks larger than 1MB."""
        binary_path = tmp_path / "claude"
        # Create a larger binary file
        large_data = b"x" * (2 * 1024 * 1024) + b"claude-haiku-4\x00"
        binary_path.write_bytes(large_data)
        with patch.object(cc, "_find_claude_binary", return_value=binary_path):
            result = cc._extract_models_from_binary()
            assert "claude-haiku-4" in result

    def test_extract_models_from_binary_too_short_ids(self, tmp_path):
        """Test that model IDs that are too short are skipped."""
        binary_path = tmp_path / "claude"
        binary_path.write_bytes(b"claude-haiku-4\x00claude-sh\x00")
        with patch.object(cc, "_find_claude_binary", return_value=binary_path):
            result = cc._extract_models_from_binary()
            assert "claude-haiku-4" in result
            # "claude-sh" is too short (< 13 chars), should not be included
            assert "claude-sh" not in result

    def test_extract_models_from_binary_with_dots(self, tmp_path):
        """Test that model IDs with dots (display names) are skipped."""
        binary_path = tmp_path / "claude"
        binary_path.write_bytes(b"claude-sonnet-4.6\x00claude-sonnet-4-6\x00")
        with patch.object(cc, "_find_claude_binary", return_value=binary_path):
            result = cc._extract_models_from_binary()
            # "claude-sonnet-4.6" has a dot, should be skipped
            assert "claude-sonnet-4.6" not in result
            assert "claude-sonnet-4-6" in result

    def test_extract_models_from_binary_code_models(self, tmp_path):
        """Test that claude-code-* models are skipped."""
        binary_path = tmp_path / "claude"
        binary_path.write_bytes(b"claude-code-4\x00claude-sonnet-4\x00")
        with patch.object(cc, "_find_claude_binary", return_value=binary_path):
            result = cc._extract_models_from_binary()
            # "claude-code-4" should be skipped
            assert "claude-code-4" not in result
            assert "claude-sonnet-4" in result

    def test_extract_models_from_binary_unicode_decode_error(self, tmp_path):
        """Test that unicode decode errors are skipped."""
        binary_path = tmp_path / "claude"
        # Write binary with invalid UTF-8
        binary_path.write_bytes(b"claude-sonnet-4\xff\xfeclaude-haiku-4\x00")
        with patch.object(cc, "_find_claude_binary", return_value=binary_path):
            result = cc._extract_models_from_binary()
            # Should find "claude-haiku-4" after the invalid bytes
            assert "claude-haiku-4" in result

    def test_extract_models_from_binary_no_model_ids(self, tmp_path):
        """Test that empty list is returned when no model IDs are found."""
        binary_path = tmp_path / "claude"
        binary_path.write_bytes(b"some binary data with no models\x00")
        with patch.object(cc, "_find_claude_binary", return_value=binary_path):
            result = cc._extract_models_from_binary()
            assert result == []


class TestPickDefaultModelFallback:
    """Test for line 1040 - fallback to last candidate when no family matches."""

    def test_pick_default_model_fallback_to_last_candidate(self):
        """Test fallback to last candidate when no sonnet/opus/haiku models exist."""
        models = ["claude-instant-4", "claude-code-4", "claude-sonnet-4-1"]
        result = cc._pick_default_model(models)
        # Should return the last candidate
        assert result == "claude-sonnet-4-1"

    def test_pick_default_model_non_standard_models(self):
        """Test with models that don't contain sonnet/opus/haiku families."""
        models = ["claude-instant-4", "claude-code-4"]
        result = cc._pick_default_model(models)
        # Should return the last candidate (fallback path)
        assert result == "claude-code-4"


# ---------------------------------------------------------------------------
# TestParseOutputLineMissingBranches — covers lines 528, 533-537, 568, 571-573,
#   595-598, 629, 677
# ---------------------------------------------------------------------------


class TestParseOutputLineMissingBranches:
    """Targeted tests for parse_output_line branches not yet exercised."""

    def test_empty_line_returns_none(self, provider):
        """Whitespace-only line returns None (line 528)."""
        assert provider.parse_output_line("   ") is None
        assert provider.parse_output_line("") is None

    def test_non_json_line_returns_plain_text(self, provider):
        """Non-JSON text falls back to plain-text event (lines 533-537)."""
        result = provider.parse_output_line("plain text output from agent")
        assert result is not None
        assert result.event_type == "text"
        assert "plain text output from agent" in result.display_lines

    def test_non_json_rate_limit_text(self, provider):
        """Non-JSON rate-limit text sets rate_limit flag (lines 533-537)."""
        result = provider.parse_output_line("rate limit exceeded, please wait")
        assert result is not None
        assert result.rate_limit is True

    def test_assistant_non_dict_content_part_skipped(self, provider):
        """Non-dict content part inside assistant message is skipped (line 568)."""
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        "this is a string not a dict",
                        {"type": "text", "text": "hello from agent"},
                    ]
                },
            }
        )
        result = provider.parse_output_line(line)
        assert result is not None
        assert "hello from agent" in result.display_lines

    def test_assistant_text_content_displayed(self, provider):
        """Text content part inside assistant event is displayed (lines 571-573)."""
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "line one\nline two"}]},
            }
        )
        result = provider.parse_output_line(line)
        assert result is not None
        assert "line one" in result.display_lines
        assert "line two" in result.display_lines

    def test_tool_use_content_fallback_key(self, provider):
        """Tool-use content with no standard key falls back to first value (lines 595-598)."""
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "custom_tool",
                            "input": {"nonstandard_key": "nonstandard_value"},
                        }
                    ]
                },
            }
        )
        result = provider.parse_output_line(line)
        assert result is not None
        assert any("nonstandard_value" in ln for ln in result.display_lines)

    def test_result_event_api_error_status_429(self, provider):
        """api_error_status 429 sets rate_limit (line 629)."""
        line = json.dumps({"type": "result", "api_error_status": 429})
        result = provider.parse_output_line(line)
        assert result is not None
        assert result.rate_limit is True

    def test_result_event_api_error_status_529(self, provider):
        """api_error_status 529 also sets rate_limit (line 629)."""
        line = json.dumps({"type": "result", "api_error_status": 529})
        result = provider.parse_output_line(line)
        assert result is not None
        assert result.rate_limit is True

    def test_tool_result_event_is_silent(self, provider):
        """tool_result event returns empty display_lines silently (line 677)."""
        line = json.dumps({"type": "tool_result", "content": "some tool output"})
        result = provider.parse_output_line(line)
        assert result is not None
        assert result.display_lines == []

    def test_user_event_is_silent(self, provider):
        """user event returns empty display_lines silently (line 677)."""
        line = json.dumps({"type": "user", "message": "injected tool results"})
        result = provider.parse_output_line(line)
        assert result is not None
        assert result.display_lines == []


# ---------------------------------------------------------------------------
# TestSupportsFreeTierClaudeCode — line 717
# ---------------------------------------------------------------------------


class TestSupportsFreeTierClaudeCode:
    """ClaudeCodeProvider.supports_free_tier always returns False."""

    def test_supports_free_tier_returns_false(self, provider):
        """Claude Code never supports OpenRouter free tier (line 717)."""
        assert provider.supports_free_tier() is False


# ---------------------------------------------------------------------------
# TestCheckUpdateAvailableClaudeCode — lines 726-767
# ---------------------------------------------------------------------------


class TestCheckUpdateAvailableClaudeCode:
    """Tests for ClaudeCodeProvider.check_update_available()."""

    def test_returns_empty_when_not_installed(self, provider):
        """Early return when claude is not installed (line 730)."""
        with patch.object(provider, "is_installed", return_value=False):
            assert provider.check_update_available() == ""

    def test_returns_empty_when_version_unknown(self, provider):
        """Early return when get_version returns 'unknown' (line 734)."""
        with patch.object(provider, "is_installed", return_value=True):
            with patch.object(provider, "get_version", return_value="unknown"):
                assert provider.check_update_available() == ""

    def test_returns_empty_when_version_empty(self, provider):
        """Early return when get_version returns empty string (line 734)."""
        with patch.object(provider, "is_installed", return_value=True):
            with patch.object(provider, "get_version", return_value=""):
                assert provider.check_update_available() == ""

    def test_returns_empty_when_no_semver_in_version(self, provider):
        """Early return when installed version has no semver match (line 738)."""
        with patch.object(provider, "is_installed", return_value=True):
            with patch.object(provider, "get_version", return_value="nightly"):
                assert provider.check_update_available() == ""

    def test_returns_empty_on_network_error(self, provider):
        """Returns empty string on urllib failure (line 753)."""
        with patch.object(provider, "is_installed", return_value=True):
            with patch.object(provider, "get_version", return_value="1.0.0"):
                with patch("urllib.request.urlopen", side_effect=OSError("network error")):
                    assert provider.check_update_available() == ""

    def test_returns_update_message_when_newer_version_available(self, provider):
        """Returns update message when installed < latest (lines 761-765)."""
        mock_resp = Mock()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_resp.read.return_value = json.dumps({"version": "2.0.0"}).encode("utf-8")

        with patch.object(provider, "is_installed", return_value=True):
            with patch.object(provider, "get_version", return_value="1.0.0"):
                with patch("urllib.request.urlopen", return_value=mock_resp):
                    result = provider.check_update_available()
                    assert "1.0.0" in result
                    assert "2.0.0" in result
                    assert "claude update" in result

    def test_returns_empty_when_already_up_to_date(self, provider):
        """Returns empty string when already on the latest version (line 767)."""
        mock_resp = Mock()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_resp.read.return_value = json.dumps({"version": "1.0.0"}).encode("utf-8")

        with patch.object(provider, "is_installed", return_value=True):
            with patch.object(provider, "get_version", return_value="1.0.0"):
                with patch("urllib.request.urlopen", return_value=mock_resp):
                    assert provider.check_update_available() == ""

    def test_returns_empty_when_latest_version_missing(self, provider):
        """Returns empty string when registry response has no version field (line 751)."""
        mock_resp = Mock()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_resp.read.return_value = json.dumps({}).encode("utf-8")

        with patch.object(provider, "is_installed", return_value=True):
            with patch.object(provider, "get_version", return_value="1.0.0"):
                with patch("urllib.request.urlopen", return_value=mock_resp):
                    assert provider.check_update_available() == ""
