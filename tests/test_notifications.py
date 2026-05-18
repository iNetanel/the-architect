"""Tests for the_architect.core.notifications module."""

from __future__ import annotations

import subprocess
import sys
from subprocess import CalledProcessError
from unittest.mock import MagicMock, patch


class TestSendDesktopNotificationDarwin:
    """Tests for send_desktop_notification() on macOS (darwin)."""

    def test_calls_osascript(self) -> None:
        from the_architect.core.notifications import send_desktop_notification

        with patch("sys.platform", "darwin"):
            with patch("subprocess.run") as mock_run:
                send_desktop_notification("Title", "Body")

        assert mock_run.called
        args = mock_run.call_args[0][0]
        assert args[0] == "osascript"
        assert args[1] == "-e"

    def test_osascript_script_contains_title_and_body(self) -> None:
        from the_architect.core.notifications import send_desktop_notification

        with patch("sys.platform", "darwin"):
            with patch("subprocess.run") as mock_run:
                send_desktop_notification("Run Complete", "3 tasks done")

        script = mock_run.call_args[0][0][2]
        assert "display notification" in script
        assert "Run Complete" in script
        assert "3 tasks done" in script

    def test_file_not_found_suppressed(self) -> None:
        from the_architect.core.notifications import send_desktop_notification

        with patch("sys.platform", "darwin"):
            with patch("subprocess.run", side_effect=FileNotFoundError("no osascript")):
                # Must not raise
                send_desktop_notification("Title", "Body")

    def test_timeout_suppressed(self) -> None:
        from the_architect.core.notifications import send_desktop_notification

        with patch("sys.platform", "darwin"):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("osascript", 10)):
                # Must not raise
                send_desktop_notification("Title", "Body")

    def test_generic_exception_suppressed(self) -> None:
        from the_architect.core.notifications import send_desktop_notification

        with patch("sys.platform", "darwin"):
            with patch("subprocess.run", side_effect=RuntimeError("boom")):
                # Must not raise
                send_desktop_notification("Title", "Body")


class TestSendDesktopNotificationLinux:
    """Tests for send_desktop_notification() on Linux."""

    def test_calls_notify_send(self) -> None:
        from the_architect.core.notifications import send_desktop_notification

        with patch("sys.platform", "linux"):
            with patch("subprocess.run") as mock_run:
                send_desktop_notification("Title", "Body")

        args = mock_run.call_args[0][0]
        assert args[0] == "notify-send"
        assert args[1] == "Title"
        assert args[2] == "Body"

    def test_file_not_found_suppressed(self) -> None:
        from the_architect.core.notifications import send_desktop_notification

        with patch("sys.platform", "linux"):
            with patch("subprocess.run", side_effect=FileNotFoundError("no notify-send")):
                send_desktop_notification("Title", "Body")

    def test_timeout_suppressed(self) -> None:
        from the_architect.core.notifications import send_desktop_notification

        with patch("sys.platform", "linux"):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("notify-send", 10)):
                send_desktop_notification("Title", "Body")

    def test_generic_exception_suppressed(self) -> None:
        from the_architect.core.notifications import send_desktop_notification

        with patch("sys.platform", "linux"):
            with patch("subprocess.run", side_effect=RuntimeError("boom")):
                send_desktop_notification("Title", "Body")


class TestSendDesktopNotificationWin32:
    """Tests for send_desktop_notification() on Windows (win32)."""

    def test_calls_powershell(self) -> None:
        from the_architect.core.notifications import send_desktop_notification

        with patch("sys.platform", "win32"):
            with patch("subprocess.run") as mock_run:
                send_desktop_notification("Title", "Body")

        args = mock_run.call_args[0][0]
        assert args[0] == "powershell"
        assert args[1] == "-Command"

    def test_script_contains_title_and_body(self) -> None:
        from the_architect.core.notifications import send_desktop_notification

        with patch("sys.platform", "win32"):
            with patch("subprocess.run") as mock_run:
                send_desktop_notification("Run Done", "All tasks complete")

        script = mock_run.call_args[0][0][2]
        assert "NotifyIcon" in script
        assert "Run Done" in script
        assert "All tasks complete" in script

    def test_file_not_found_suppressed(self) -> None:
        from the_architect.core.notifications import send_desktop_notification

        with patch("sys.platform", "win32"):
            with patch("subprocess.run", side_effect=FileNotFoundError("no powershell")):
                send_desktop_notification("Title", "Body")

    def test_timeout_suppressed(self) -> None:
        from the_architect.core.notifications import send_desktop_notification

        with patch("sys.platform", "win32"):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("powershell", 10)):
                send_desktop_notification("Title", "Body")

    def test_generic_exception_suppressed(self) -> None:
        from the_architect.core.notifications import send_desktop_notification

        with patch("sys.platform", "win32"):
            with patch("subprocess.run", side_effect=CalledProcessError(1, "powershell")):
                send_desktop_notification("Title", "Body")


class TestRingTerminalBell:
    """Tests for ring_terminal_bell()."""

    def test_writes_bell_to_stderr(self) -> None:
        from the_architect.core.notifications import ring_terminal_bell

        mock_stderr = MagicMock()

        with patch.object(sys, "stderr", mock_stderr):
            ring_terminal_bell()

        mock_stderr.write.assert_called_once_with("\a")
        mock_stderr.flush.assert_called_once()

    def test_stderr_write_error_suppressed(self) -> None:
        from the_architect.core.notifications import ring_terminal_bell

        mock_stderr = MagicMock(side_effect=BrokenPipeError("pipe broken"))

        with patch.object(sys, "stderr", mock_stderr):
            # Must not raise
            ring_terminal_bell()

    def test_stderr_flush_error_suppressed(self) -> None:
        from the_architect.core.notifications import ring_terminal_bell

        mock_stderr = MagicMock()
        mock_stderr.flush.side_effect = OSError("flush failed")

        with patch.object(sys, "stderr", mock_stderr):
            # Must not raise
            ring_terminal_bell()
