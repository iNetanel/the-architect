"""Tests for :class:`WaitLogRenderer` — provider output during planning /
retrospective / reassessment must land in the wait-screen log tail
rather than being silently swallowed by the Textual alt-screen.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from the_architect.tui.renderer import WaitLogRenderer


class TestWaitLogRenderer:
    def test_write_line_forwards_to_wait_session_append_log(self) -> None:
        session = MagicMock()
        renderer = WaitLogRenderer(session=session)
        renderer.write_line("hello from planner")
        session.append_log.assert_called_once_with("hello from planner")

    def test_write_line_without_session_is_noop(self) -> None:
        """The point of this renderer is to avoid writing to stdout
        while the Textual alt-screen is active. With no session
        bound, the write must silently drop — falling back to
        stdout would reintroduce the original bug.
        """
        renderer = WaitLogRenderer()
        renderer.write_line("hello")  # must not raise, must not print

    def test_footer_operations_are_no_ops(self) -> None:
        session = MagicMock()
        renderer = WaitLogRenderer(session=session)
        renderer.set_footer("ignored")
        renderer.clear_footer()
        # Wait screen manages its own title/detail via dedicated
        # setters; the StreamRenderer footer contract has no meaning
        # here and must not leak into append_log.
        session.append_log.assert_not_called()

    def test_append_log_exception_is_swallowed(self) -> None:
        """A UI failure must never crash the planner."""
        session = MagicMock()
        session.append_log.side_effect = RuntimeError("wait overlay dismissed mid-stream")
        renderer = WaitLogRenderer(session=session)
        renderer.write_line("safe")  # must not raise

    def test_bind_attaches_session_after_construction(self) -> None:
        renderer = WaitLogRenderer()
        renderer.write_line("dropped")  # no session → drop
        session = MagicMock()
        renderer.bind(session)
        renderer.write_line("kept")
        session.append_log.assert_called_once_with("kept")
