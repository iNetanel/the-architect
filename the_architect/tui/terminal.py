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
