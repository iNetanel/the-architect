"""Tests for shared provider setup fallback behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from the_architect.config import ArchitectConfig
from the_architect.core.provider_setup import (
    ensure_provider_setup,
    existing_provider_setup_is_usable,
    provider_uses_architect_config,
)


def _write_existing_setup(project: Path) -> None:
    architect_dir = project / ".architect"
    prompts_dir = architect_dir / "prompts"
    prompts_dir.mkdir(parents=True)
    for filename in (
        "architect.md",
        "intelligence.md",
        "reviewer.md",
        "execution.md",
    ):
        (prompts_dir / filename).write_text(f"{filename} prompt\n", encoding="utf-8")
    (architect_dir / "architect.json").write_text(
        '{"agent":{"architect":{"prompt":"architect.md"},'
        '"intelligence":{"prompt":"intelligence.md"},'
        '"reviewer":{"prompt":"reviewer.md"}}}\n',
        encoding="utf-8",
    )


def test_reuses_existing_setup_after_multiplexed_path_error(tmp_path: Path) -> None:
    """A verified existing .architect setup should survive package resource glitches."""
    _write_existing_setup(tmp_path)
    provider = MagicMock()
    provider.name = "opencode"
    provider.ensure_setup.side_effect = NotADirectoryError(
        "MultiplexedPath only supports directories"
    )

    ensure_provider_setup(provider, tmp_path, ArchitectConfig().resolve(tmp_path))

    provider.ensure_setup.assert_called_once()


def test_reraises_multiplexed_path_error_without_existing_setup(tmp_path: Path) -> None:
    """Fallback must not hide setup failures when reusable files are absent."""
    provider = MagicMock()
    provider.name = "opencode"
    provider.ensure_setup.side_effect = NotADirectoryError(
        "MultiplexedPath only supports directories"
    )

    with pytest.raises(NotADirectoryError):
        ensure_provider_setup(provider, tmp_path, ArchitectConfig().resolve(tmp_path))


def test_reraises_non_multiplexed_path_error(tmp_path: Path) -> None:
    """Only the known importlib.resources glitch is eligible for fallback."""
    _write_existing_setup(tmp_path)
    provider = MagicMock()
    provider.name = "opencode"
    provider.ensure_setup.side_effect = NotADirectoryError("real missing directory")

    with pytest.raises(NotADirectoryError):
        ensure_provider_setup(provider, tmp_path, ArchitectConfig().resolve(tmp_path))


# ---------------------------------------------------------------------------
# existing_provider_setup_is_usable — edge cases
# ---------------------------------------------------------------------------


class TestExistingProviderSetupIsUsable:
    """Tests for existing_provider_setup_is_usable() edge cases."""

    def test_prompts_dir_missing(self, tmp_path: Path) -> None:
        """Return False when .architect/prompts/ directory does not exist."""
        provider = MagicMock()
        provider.name = "opencode"
        assert existing_provider_setup_is_usable(provider, tmp_path) is False

    def test_prompt_file_empty(self, tmp_path: Path) -> None:
        """Return False when a prompt file exists but is empty."""
        _write_existing_setup(tmp_path)
        # Make one prompt file empty
        (tmp_path / ".architect" / "prompts" / "architect.md").write_text("   \n", encoding="utf-8")
        provider = MagicMock()
        provider.name = "opencode"
        assert existing_provider_setup_is_usable(provider, tmp_path) is False

    def test_prompt_file_missing(self, tmp_path: Path) -> None:
        """Return False when a required prompt file is missing."""
        _write_existing_setup(tmp_path)
        # Remove one prompt file
        (tmp_path / ".architect" / "prompts" / "reviewer.md").unlink()
        provider = MagicMock()
        provider.name = "opencode"
        assert existing_provider_setup_is_usable(provider, tmp_path) is False

    def test_prompt_file_read_os_error(self, tmp_path: Path) -> None:
        """Return False when reading a prompt file raises OSError."""
        _write_existing_setup(tmp_path)
        provider = MagicMock()
        provider.name = "opencode"

        original_read_text = Path.read_text

        def mock_read_text(self_path, *args, **kwargs):
            if "architect.md" in str(self_path):
                raise OSError("permission denied")
            return original_read_text(self_path, *args, **kwargs)

        with patch.object(Path, "read_text", mock_read_text):
            assert existing_provider_setup_is_usable(provider, tmp_path) is False

    def test_non_opencode_provider_returns_true(self, tmp_path: Path) -> None:
        """Return True early when provider does not use architect.json config."""
        prompts_dir = tmp_path / ".architect" / "prompts"
        prompts_dir.mkdir(parents=True)
        for filename in (
            "architect.md",
            "intelligence.md",
            "reviewer.md",
            "execution.md",
        ):
            (prompts_dir / filename).write_text(f"{filename} prompt\n", encoding="utf-8")
        provider = MagicMock()
        provider.name = "claude-code"  # not opencode
        assert existing_provider_setup_is_usable(provider, tmp_path) is True

    def test_architect_json_invalid_json(self, tmp_path: Path) -> None:
        """Return False when architect.json contains invalid JSON."""
        _write_existing_setup(tmp_path)
        (tmp_path / ".architect" / "architect.json").write_text("{not valid json", encoding="utf-8")
        provider = MagicMock()
        provider.name = "opencode"
        assert existing_provider_setup_is_usable(provider, tmp_path) is False

    def test_architect_json_agent_not_dict(self, tmp_path: Path) -> None:
        """Return False when 'agent' key is not a dict."""
        _write_existing_setup(tmp_path)
        (tmp_path / ".architect" / "architect.json").write_text('{"agent": []}', encoding="utf-8")
        provider = MagicMock()
        provider.name = "opencode"
        assert existing_provider_setup_is_usable(provider, tmp_path) is False

    def test_architect_json_agent_cfg_not_dict(self, tmp_path: Path) -> None:
        """Return False when an agent config is not a dict."""
        _write_existing_setup(tmp_path)
        (tmp_path / ".architect" / "architect.json").write_text(
            '{"agent": {"architect": null}}', encoding="utf-8"
        )
        provider = MagicMock()
        provider.name = "opencode"
        assert existing_provider_setup_is_usable(provider, tmp_path) is False

    def test_architect_json_prompt_empty(self, tmp_path: Path) -> None:
        """Return False when an agent's prompt value is empty."""
        _write_existing_setup(tmp_path)
        (tmp_path / ".architect" / "architect.json").write_text(
            '{"agent": {"architect": {"prompt": "   "}}}', encoding="utf-8"
        )
        provider = MagicMock()
        provider.name = "opencode"
        assert existing_provider_setup_is_usable(provider, tmp_path) is False

    def test_architect_json_prompt_missing(self, tmp_path: Path) -> None:
        """Return False when an agent config has no prompt key."""
        _write_existing_setup(tmp_path)
        (tmp_path / ".architect" / "architect.json").write_text(
            '{"agent": {"architect": {}}}', encoding="utf-8"
        )
        provider = MagicMock()
        provider.name = "opencode"
        assert existing_provider_setup_is_usable(provider, tmp_path) is False


# ---------------------------------------------------------------------------
# provider_uses_architect_config
# ---------------------------------------------------------------------------


class TestProviderUsesArchitectConfig:
    """Tests for provider_uses_architect_config()."""

    def test_opencode_returns_true(self) -> None:
        """OpenCode provider should return True."""
        provider = MagicMock()
        provider.name = "opencode"
        assert provider_uses_architect_config(provider) is True

    def test_claude_code_returns_false(self) -> None:
        """Claude Code provider should return False."""
        provider = MagicMock()
        provider.name = "claude-code"
        assert provider_uses_architect_config(provider) is False

    def test_codex_returns_false(self) -> None:
        """Codex CLI provider should return False."""
        provider = MagicMock()
        provider.name = "codex"
        assert provider_uses_architect_config(provider) is False

    def test_no_name_attr_returns_false(self) -> None:
        """Provider without name attribute should return False."""
        provider = MagicMock(spec=[])
        delattr(provider, "name")
        assert provider_uses_architect_config(provider) is False
