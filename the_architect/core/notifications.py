"""Desktop notification and terminal bell utilities for The Architect.

Provides platform-aware desktop notifications (macOS, Linux, Windows)
and a terminal bell function so users receive alerts when autonomous
runs complete or fail. Both functions fail silently — errors are
never propagated to the caller.

Design notes
------------
- No new dependencies — uses `subprocess.run` with platform-native tools:
  macOS ``osascript``, Linux ``notify-send``, Windows ``powershell``.
- All subprocess calls are fire-and-forget: exceptions are caught and
  logged at DEBUG level so the notification never interrupts the run.
- Terminal bell writes ``\a`` (BEL character) to ``sys.stderr``.
  Some terminals ignore it; this is harmless.
"""

from __future__ import annotations

import subprocess
import sys

from loguru import logger


def send_desktop_notification(title: str, body: str) -> None:
    """Send a desktop notification using the platform-native tool.

    Dispatches to the correct command based on ``sys.platform``:
    - **macOS** (``darwin``): ``osascript`` with ``display notification``
    - **Linux**: ``notify-send``
    - **Windows** (``win32``): ``powershell`` with ``[System.Windows.Forms.NotifyIcon]``

    All errors are suppressed — a missing tool, a failed subprocess, or
    an unsupported environment will *not* raise an exception.  The call
    returns silently whether the notification succeeded or not.

    Args:
        title: Short title shown in the notification banner.
        body: Longer message body shown beneath the title.
    """
    platform = sys.platform

    if platform == "darwin":
        _notify_darwin(title, body)
    elif platform == "win32":
        _notify_win32(title, body)
    else:
        # Linux and other POSIX-like systems
        _notify_linux(title, body)


def _notify_darwin(title: str, body: str) -> None:
    """Send a notification on macOS via ``osascript``."""
    try:
        script = f"display notification {body!r} with title {title!r}"
        subprocess.run(
            ["osascript", "-e", script],
            check=True,
            timeout=10,
            capture_output=True,
        )
    except FileNotFoundError:
        logger.debug("osascript not found — desktop notification skipped")
    except subprocess.TimeoutExpired:
        logger.debug("osascript timed out — desktop notification skipped")
    except Exception as exc:
        logger.debug(f"Desktop notification failed (non-fatal): {exc!r}")


def _notify_linux(title: str, body: str) -> None:
    """Send a notification on Linux via ``notify-send``."""
    try:
        subprocess.run(
            ["notify-send", title, body],
            check=True,
            timeout=10,
            capture_output=True,
        )
    except FileNotFoundError:
        logger.debug("notify-send not found — desktop notification skipped")
    except subprocess.TimeoutExpired:
        logger.debug("notify-send timed out — desktop notification skipped")
    except Exception as exc:
        logger.debug(f"Desktop notification failed (non-fatal): {exc!r}")


def _notify_win32(title: str, body: str) -> None:
    """Send a notification on Windows via ``powershell``."""
    try:
        script = (
            f"$null = [System.Windows.Forms.NotifyIcon]::"
            f"new().ShowBalloonTip(5000, {title!r}, {body!r}, "
            f"[System.Windows.Forms.ToolTipIcon]::Info)"
        )
        subprocess.run(
            ["powershell", "-Command", script],
            check=True,
            timeout=10,
            capture_output=True,
        )
    except FileNotFoundError:
        logger.debug("powershell not found — desktop notification skipped")
    except subprocess.TimeoutExpired:
        logger.debug("powershell timed out — desktop notification skipped")
    except Exception as exc:
        logger.debug(f"Desktop notification failed (non-fatal): {exc!r}")


def ring_terminal_bell() -> None:
    """Ring the terminal bell by writing the BEL character to stderr.

    Outputs ``\a`` (ASCII BEL, code 7) to ``sys.stderr``.  Most modern
    terminals interpret this as a visual or audible bell.  Terminals that
    do not support the bell character will silently ignore it.

    Errors (e.g. if stderr is closed) are suppressed silently.
    """
    try:
        sys.stderr.write("\a")
        sys.stderr.flush()
    except Exception as exc:
        logger.debug(f"Terminal bell failed (non-fatal): {exc!r}")
