"""Shared fixtures for The Architect tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from the_architect.core.retrospective import RetrospectiveResult


async def _fake_run_retrospective_noop(*args: object, **kwargs: object) -> RetrospectiveResult:
    """Fast no-op stand-in for run_retrospective used in CLI-level tests.

    Prevents real AI provider subprocesses from running during unit tests that
    call _run_main() without an explicit run_retrospective mock.  Tests that
    need to verify retrospective behaviour must override this via their own
    ``with patch("the_architect.cli.run_retrospective", ...)`` block.

    Note: this patches the *cli module's* reference, not the source module.
    Tests in test_retrospective.py that import run_retrospective directly from
    the_architect.core.retrospective are unaffected.
    """
    return RetrospectiveResult()


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """A temporary project directory."""
    return tmp_path


@pytest.fixture(autouse=True)
def clear_opencode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear OPENCODE_CONFIG* and ARCHITECT_TUI env vars for every test.

    Prevents the dev environment's opencode config from leaking into
    tests that create their own project directories and opencode.json files.

    ARCHITECT_TUI is cleared so CLI commands use the non-TUI (plain text)
    code path.  TUI-specific tests must explicitly set this env var or mock
    _tui_mode_enabled() themselves.
    """
    monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
    monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("ARCHITECT_TUI", raising=False)


@pytest.fixture(autouse=True)
def stub_cli_retrospective() -> object:
    """Patch the_architect.cli.run_retrospective for the duration of every test.

    Prevents slow real-provider subprocess calls in tests that exercise _run_main()
    without explicitly mocking retrospective behaviour.  The autouse fixture
    applies globally but only patches the cli module's reference — direct
    imports in test_retrospective.py are unaffected.

    Tests that need to assert on retrospective behaviour (e.g. TestRunMainExecution
    tests that use ``with patch("the_architect.cli.run_retrospective", ...)`` ) will
    have their own context-manager patch take precedence while inside the ``with``
    block.  When that block exits, this fixture's patch is restored.
    """
    with patch("the_architect.cli.run_retrospective", new=_fake_run_retrospective_noop):
        yield


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
