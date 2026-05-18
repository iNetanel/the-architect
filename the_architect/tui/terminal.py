"""Terminal-mode cleanup helpers for Textual / prompt-driven screens."""

from __future__ import annotations

import os
import sys
from typing import Any

TERMINAL_RESTORE_SEQUENCE = (
    "\033[?1049l"  # leave alternate screen + restore cursor
    "\033[?1000l"  # X10 mouse reporting
    "\033[?1001l"  # highlight mouse tracking
    "\033[?1002l"  # button-event mouse reporting
    "\033[?1003l"  # any-event mouse reporting
    "\033[?1004l"  # focus in/out reporting
    "\033[?1005l"  # UTF-8 extended mouse mode
    "\033[?1006l"  # SGR mouse mode
    "\033[?1007l"  # alternate scroll mode
    "\033[?1015l"  # urxvt mouse mode
    "\033[?2004l"  # bracketed paste
    "\033[?25h"  # cursor visible
)

# Terminal re-setup sequence used after sleep/wake to re-enter the alternate
# screen and re-enable mouse tracking.  The kernel may reset the alternate
# screen buffer and input modes when the system resumes from suspend.
# Textual's LinuxDriver sets up these modes in _setup_terminal(), but after
# sleep the driver's internal state is stale.  Sending this sequence forces
# the terminal emulator to re-establish the alternate screen, re-enable SGR
# mouse tracking (which Textual uses), and re-enable bracketed paste.
TERMINAL_RESETUP_SEQUENCE = (
    "\033[?1049h"  # enter alternate screen buffer
    "\033[?1003h"  # any-event mouse tracking (SGR mode set by Textual driver)
    "\033[?2004h"  # bracketed paste mode
    "\033[?1006h"  # SGR extended mouse coordinates
)

# The controlling-terminal device path on POSIX systems.
# Does not exist on Windows — access is guarded by the ``sys.platform`` check
# in :func:`restore_terminal_input_modes`.
_DEV_TTY = "/dev/tty"


def _stream_is_tty(stream: Any) -> bool:
    """Return True when a stream appears to be attached to a terminal."""
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def _write_restore_sequence(stream: Any, *, require_tty: bool = True) -> None:
    """Best-effort write of the terminal restore sequence to one stream."""
    if require_tty and not _stream_is_tty(stream):
        return
    try:
        stream.write(TERMINAL_RESTORE_SEQUENCE)
        stream.flush()
    except Exception:
        pass


def restore_terminal_input_modes() -> None:
    """Disable terminal input modes that can leak after abrupt TUI exits.

    The raw ``35;...M`` text seen at the shell prompt is mouse-reporting
    input that the terminal sends after a TUI app enabled mouse tracking
    but exited before turning it off.

    Cleanup is intentionally redundant:

    - **stdout** covers the normal CLI path.
    - **stderr** covers terminals where stdout is redirected.
    - **/dev/tty** (POSIX only) targets the controlling terminal directly
      when both stdout and stderr are redirected.  This path is skipped
      entirely on Windows where ``/dev/tty`` does not exist.

    All writes are best-effort — failures are silently swallowed so cleanup
    never crashes the process on exit.
    """
    if "PYTEST_CURRENT_TEST" not in os.environ:
        _write_restore_sequence(sys.stdout)
        if sys.stderr is not sys.stdout:
            _write_restore_sequence(sys.stderr)

    # /dev/tty is a POSIX-only concept — skip on any non-POSIX platform.
    if sys.platform != "win32":
        try:
            with open(_DEV_TTY, "w", encoding="utf-8") as tty:
                _write_restore_sequence(tty, require_tty=False)
        except Exception:
            pass


def resetup_terminal_after_sleep() -> None:
    """Re-establish terminal modes after system resume from sleep.

    When the OS suspends and resumes, the terminal emulator may reset the
    alternate screen buffer, mouse tracking, and bracketed paste modes.
    This function writes the re-setup sequences to restore those modes so
    the TUI can continue functioning (mouse clicks, tab switching, etc.).

    Must be called from the Textual event loop thread so the driver can
    process the sequences without corrupting the input stream.

    All writes are best-effort — failures are silently swallowed.
    """
    if "PYTEST_CURRENT_TEST" in os.environ:
        return

    # Write re-setup sequence to stdout (the controlling terminal)
    if _stream_is_tty(sys.stdout):
        try:
            sys.stdout.write(TERMINAL_RESETUP_SEQUENCE)
            sys.stdout.flush()
        except Exception:
            pass

    # Also target /dev/tty directly for robustness
    if sys.platform != "win32":
        try:
            with open(_DEV_TTY, "w", encoding="utf-8") as tty:
                tty.write(TERMINAL_RESETUP_SEQUENCE)
                tty.flush()
        except Exception:
            pass
