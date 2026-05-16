"""Tests for terminal-mode cleanup helpers in terminal.py.

Covers:
- _stream_is_tty exception handling when isatty() raises
- _write_restore_sequence non-TTY early return
- _write_restore_sequence write/flush exception swallowing
- restore_terminal_input_modes pytest skip path
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from the_architect.tui.terminal import (
    TERMINAL_RESTORE_SEQUENCE,
    _stream_is_tty,
    _write_restore_sequence,
    restore_terminal_input_modes,
)


class TestStreamIsTty:
    """Tests for _stream_is_tty helper."""

    def test_returns_true_for_tty_stream(self) -> None:
        """A stream with isatty() returning True should be detected as TTY."""
        stream = MagicMock()
        stream.isatty.return_value = True
        assert _stream_is_tty(stream) is True

    def test_returns_false_for_non_tty_stream(self) -> None:
        """A stream with isatty() returning False should not be detected as TTY."""
        stream = MagicMock()
        stream.isatty.return_value = False
        assert _stream_is_tty(stream) is False

    def test_returns_false_when_isatty_raises(self) -> None:
        """When isatty() raises an exception, return False safely."""
        stream = MagicMock()
        stream.isatty.side_effect = OSError("bad file descriptor")
        assert _stream_is_tty(stream) is False

    def test_returns_false_when_isatty_raises_valueerror(self) -> None:
        """When isatty() raises ValueError (e.g. closed pipe), return False."""
        stream = MagicMock()
        stream.isatty.side_effect = ValueError("stream closed")
        assert _stream_is_tty(stream) is False


class TestWriteRestoreSequence:
    """Tests for _write_restore_sequence helper."""

    def test_writes_sequence_to_tty_stream(self) -> None:
        """A TTY stream should receive the restore sequence."""
        stream = MagicMock()
        stream.isatty.return_value = True
        _write_restore_sequence(stream, require_tty=True)
        stream.write.assert_called_once_with(TERMINAL_RESTORE_SEQUENCE)
        stream.flush.assert_called_once()

    def test_skips_non_tty_stream_when_required(self) -> None:
        """When require_tty=True and stream is not a TTY, do nothing."""
        stream = MagicMock()
        stream.isatty.return_value = False
        _write_restore_sequence(stream, require_tty=True)
        stream.write.assert_not_called()
        stream.flush.assert_not_called()

    def test_writes_to_non_tty_stream_when_not_required(self) -> None:
        """When require_tty=False, write even if stream is not a TTY."""
        stream = MagicMock()
        stream.isatty.return_value = False
        _write_restore_sequence(stream, require_tty=False)
        stream.write.assert_called_once_with(TERMINAL_RESTORE_SEQUENCE)
        stream.flush.assert_called_once()

    def test_swallows_write_exception(self) -> None:
        """When write() raises, the exception is swallowed silently."""
        stream = MagicMock()
        stream.isatty.return_value = True
        stream.write.side_effect = OSError("broken pipe")
        # Should not raise
        _write_restore_sequence(stream, require_tty=True)

    def test_swallows_flush_exception(self) -> None:
        """When flush() raises, the exception is swallowed silently."""
        stream = MagicMock()
        stream.isatty.return_value = True
        stream.flush.side_effect = OSError("io error")
        # Should not raise
        _write_restore_sequence(stream, require_tty=True)
        stream.write.assert_called_once()

    def test_skips_when_isatty_raises_and_required(self) -> None:
        """When isatty() raises and require_tty=True, skip writing."""
        stream = MagicMock()
        stream.isatty.side_effect = OSError("bad fd")
        _write_restore_sequence(stream, require_tty=True)
        stream.write.assert_not_called()


class TestRestoreTerminalInputModes:
    """Tests for restore_terminal_input_modes top-level function."""

    def test_skips_when_pytest_current_test_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Function should be a no-op when PYTEST_CURRENT_TEST env var is set."""
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "some_test")
        # Should not raise — the function should skip stdout/stderr writes
        # because PYTEST_CURRENT_TEST is set
        restore_terminal_input_modes()

    def test_no_crash_on_non_tty_stdin(self) -> None:
        """Function should not crash even when streams are not TTYs."""
        # PYTEST_CURRENT_TEST is set by pytest, so stdout/stderr paths are skipped
        # The /dev/tty path should also not crash
        restore_terminal_input_modes()

    def test_writes_to_stdout_when_not_in_pytest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When PYTEST_CURRENT_TEST is unset, stdout should receive restore sequence."""
        import sys

        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        with patch("the_architect.tui.terminal._write_restore_sequence") as mock_write:
            restore_terminal_input_modes()
            # stdout should have been called
            stdout_calls = [c for c in mock_write.call_args_list if c[0][0] is sys.stdout]
            assert len(stdout_calls) == 1

    def test_writes_to_stderr_when_different_from_stdout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When stderr differs from stdout and not in pytest, stderr gets restore sequence."""
        import sys

        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        with patch("the_architect.tui.terminal._write_restore_sequence") as mock_write:
            restore_terminal_input_modes()
            # stderr should have been called (sys.stderr is not sys.stdout on Linux)
            stderr_calls = [c for c in mock_write.call_args_list if c[0][0] is sys.stderr]
            assert len(stderr_calls) == 1

    def test_opens_dev_tty_on_posix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On POSIX, /dev/tty should be opened for restore sequence."""
        import sys

        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        original_platform = sys.platform
        sys.platform = "linux"
        try:
            with patch("builtins.open", MagicMock()) as mock_open:
                mock_open.return_value.__enter__ = MagicMock()
                mock_open.return_value.__exit__ = MagicMock(return_value=False)
                restore_terminal_input_modes()
            # open should have been called with /dev/tty
            tty_calls = [c for c in mock_open.call_args_list if "/dev/tty" in str(c)]
            assert len(tty_calls) == 1
        finally:
            sys.platform = original_platform
