"""Shared fixtures for The Architect tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """A temporary project directory."""
    return tmp_path


@pytest.fixture(autouse=True)
def clear_opencode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear OPENCODE_CONFIG* env vars for every test.

    Prevents the dev environment's opencode config from leaking into
    tests that create their own project directories and opencode.json files.
    """
    monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
    monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)


@pytest.fixture(autouse=True)
def isolate_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test from a fresh temp directory.

    This is defence-in-depth against stray file I/O. Tests that mock
    config objects can inadvertently trigger production code to write
    files relative to the current working directory (for example, loguru
    file handlers that receive a mock path). Running from ``tmp_path``
    means any such leaks land in a directory pytest deletes after the
    test, not in the repo root.

    Tests that need to operate from a specific directory can still call
    ``monkeypatch.chdir(...)`` themselves — the later chdir wins.
    """
    monkeypatch.chdir(tmp_path)
