"""Textual-backed implementation of :class:`StreamRenderer`.

This keeps the runner's streaming contract unchanged: the runner pushes
provider output lines via ``write_line(...)`` and footer/status text via
``set_footer(...)``, and the renderer forwards those into whichever
Textual app is currently active.

When no Textual app is bound, calls fall back to plain streaming output
so non-TTY/CI environments still behave exactly as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from the_architect.core.runner import PlainStreamRenderer, StreamRenderer

if TYPE_CHECKING:
    from the_architect.tui.app import ArchitectApp
    from the_architect.tui.session import TuiWaitSession


class TextualStreamRenderer(StreamRenderer):
    """Stream renderer that pushes lines and footer text into a Textual app.

    The renderer is intentionally thin: it holds a reference to a Textual
    ``App`` and forwards events. The app owns all layout, styling, and
    repainting. If no app is bound yet, calls degrade to the existing
    plain streaming behavior so tests and headless invocations keep
    working.
    """

    def __init__(self, app: ArchitectApp | None = None) -> None:
        self._app = app
        self._fallback = PlainStreamRenderer()

    def bind(self, app: ArchitectApp) -> None:
        """Attach the renderer to a running Textual app."""
        self._app = app

    def write_line(self, line: str) -> None:
        if self._app is None:
            self._fallback.write_line(line)
            return
        try:
            self._app.push_output_line(line)
        except Exception:
            self._fallback.write_line(line)

    def set_footer(self, text: str) -> None:
        if self._app is None:
            return
        try:
            self._app.update_footer(text)
        except Exception:
            pass

    def clear_footer(self) -> None:
        if self._app is None:
            return
        try:
            self._app.update_footer("")
        except Exception:
            pass

    def close(self) -> None:
        return


class WaitLogRenderer(StreamRenderer):
    """Stream renderer that appends provider lines into a wait-screen log tail.

    During planning, retrospective review, and per-task reassessment
    the visible surface is the :class:`WaitScreen` overlay rather than
    the execution tabs. Writing provider output to
    :meth:`TuiWaitSession.append_log` means the user can actually see
    what the model is doing (tool calls, thinking text, progress
    signals) instead of staring at an empty spinner for five minutes
    while the Textual alt-screen swallows every ``stdout`` write.

    Footer updates and set-detail lines are ignored — wait screens
    already render title + detail via dedicated setters, not through
    the StreamRenderer contract. ``write_line`` is the only signal
    that needs to be forwarded.

    When no wait session is attached, calls become a no-op rather
    than falling back to plain stdout — writing to stdout while the
    Textual alt-screen is active is exactly the bug this class was
    introduced to fix.
    """

    def __init__(self, session: TuiWaitSession | None = None) -> None:
        self._session = session

    def bind(self, session: TuiWaitSession) -> None:
        """Attach the renderer to a running wait session."""
        self._session = session

    def write_line(self, line: str) -> None:
        if self._session is None:
            return
        # ``append_log`` is always thread-safe: the overlay path uses
        # ``app.call_from_thread``, the standalone path uses
        # ``WaitApp.call_from_thread``. Either is fine from a worker.
        try:
            self._session.append_log(line)
        except Exception:
            # Never let a UI failure crash the planner / reviewer.
            pass

    def set_footer(self, text: str) -> None:
        return

    def clear_footer(self) -> None:
        return

    def close(self) -> None:
        return
