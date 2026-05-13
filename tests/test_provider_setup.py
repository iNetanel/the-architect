"""Tests for shared provider setup fallback behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from the_architect.config import ArchitectConfig
from the_architect.core.provider_setup import ensure_provider_setup


def _write_existing_setup(project: Path) -> None:
    architect_dir = project / ".architect"
    prompts_dir = architect_dir / "prompts"
    prompts_dir.mkdir(parents=True)
    for filename in (
        "architect.md",
        "intelligence.md",
        "reviewer.md",
        "execution-protocol.md",
    ):
        (prompts_dir / filename).write_text(f"{filename} prompt\n", encoding="utf-8")
    (architect_dir / "architect.json").write_text(
        '{"agent":{"architect":{"prompt":"architect.md"},"reviewer":{"prompt":"reviewer.md"}}}\n',
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
