"""Tests for Phase 8 TUI default-resolution logic."""

from __future__ import annotations

import os
from unittest.mock import patch

from the_architect.cli import _resolve_tui_default


class TestResolveTuiDefault:
    def test_explicit_false_wins(self) -> None:
        with patch("sys.stdout.isatty", return_value=True):
            assert _resolve_tui_default(False, headless=False) is False

    def test_explicit_true_wins(self) -> None:
        with patch("sys.stdout.isatty", return_value=False):
            assert _resolve_tui_default(True, headless=False) is True

    def test_headless_forces_off_even_when_explicit_true(self) -> None:
        assert _resolve_tui_default(True, headless=True) is False

    def test_auto_tty_color_yes_returns_true(self) -> None:
        env = {"NO_COLOR": "", "TERM": "xterm-256color"}
        with (
            patch.dict(os.environ, env, clear=False),
            patch("sys.stdout.isatty", return_value=True),
        ):
            # Ensure NO_COLOR is explicitly empty rather than inherited.
            os.environ.pop("NO_COLOR", None)
            assert _resolve_tui_default(None, headless=False) is True

    def test_auto_no_tty_returns_false(self) -> None:
        with patch("sys.stdout.isatty", return_value=False):
            assert _resolve_tui_default(None, headless=False) is False

    def test_auto_no_color_returns_false(self) -> None:
        with (
            patch.dict(os.environ, {"NO_COLOR": "1", "TERM": "xterm-256color"}),
            patch("sys.stdout.isatty", return_value=True),
        ):
            assert _resolve_tui_default(None, headless=False) is False

    def test_auto_dumb_term_returns_false(self) -> None:
        with (
            patch.dict(os.environ, {"TERM": "dumb"}, clear=False),
            patch("sys.stdout.isatty", return_value=True),
        ):
            os.environ.pop("NO_COLOR", None)
            assert _resolve_tui_default(None, headless=False) is False
