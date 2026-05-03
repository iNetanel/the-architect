"""Textual-based TUI layer for The Architect.

This package owns all rich terminal UI for The Architect. Business logic
lives in :mod:`the_architect.core` and must not depend on anything here —
the TUI is a presentation layer that plugs into existing seams such as
:class:`the_architect.core.runner.StreamRenderer`.

Phase 5 adds :func:`tui_wait_session` for planning, retrospective
review, and reassessment phases so those waiting moments render inside
Textual instead of inline terminal spinners. Non-TTY/CI environments
keep falling back to plain streaming automatically.
"""

from __future__ import annotations

from the_architect.tui.renderer import TextualStreamRenderer, WaitLogRenderer
from the_architect.tui.session import (
    TuiSession,
    TuiWaitSession,
    tui_execution_session,
    tui_wait_session,
)

__all__ = [
    "TextualStreamRenderer",
    "TuiSession",
    "TuiWaitSession",
    "WaitLogRenderer",
    "tui_execution_session",
    "tui_wait_session",
]
