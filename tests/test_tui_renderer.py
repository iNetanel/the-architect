"""Tests for the Textual TUI renderer."""

from __future__ import annotations

from unittest.mock import MagicMock

from the_architect.tui.renderer import TextualStreamRenderer


class TestTextualStreamRenderer:
    def test_write_line_without_app_falls_back_to_plain(self) -> None:
        renderer = TextualStreamRenderer()
        # Should not raise even though no app is bound; fallback prints.
        renderer.write_line("hello")

    def test_write_line_forwards_to_app_when_bound(self) -> None:
        app = MagicMock()
        renderer = TextualStreamRenderer(app=app)
        renderer.write_line("line one")
        app.push_output_line.assert_called_once_with("line one")

    def test_set_footer_forwards_to_app(self) -> None:
        app = MagicMock()
        renderer = TextualStreamRenderer(app=app)
        renderer.set_footer("T01 | attempt 1/3")
        app.update_footer.assert_called_once_with("T01 | attempt 1/3")

    def test_clear_footer_sends_empty_string(self) -> None:
        app = MagicMock()
        renderer = TextualStreamRenderer(app=app)
        renderer.clear_footer()
        app.update_footer.assert_called_once_with("")

    def test_set_footer_without_app_is_noop(self) -> None:
        renderer = TextualStreamRenderer()
        renderer.set_footer("anything")  # must not raise

    def test_app_exception_falls_back_to_plain_write(self) -> None:
        app = MagicMock()
        app.push_output_line.side_effect = RuntimeError("broken")
        renderer = TextualStreamRenderer(app=app)
        # Should swallow and fall back without raising.
        renderer.write_line("still works")


class TestTextualStreamRendererFeedback:
    """Test set_feedback forwarding."""

    def test_set_feedback_forwards_to_app(self) -> None:
        app = MagicMock()
        renderer = TextualStreamRenderer(app=app)
        renderer.set_feedback("fix the login bug")
        app.update_feedback.assert_called_once_with("fix the login bug")

    def test_set_feedback_clear_forwards_to_app(self) -> None:
        app = MagicMock()
        renderer = TextualStreamRenderer(app=app)
        renderer.set_feedback(None)
        app.update_feedback.assert_called_once_with(None)

    def test_set_feedback_without_app_is_noop(self) -> None:
        renderer = TextualStreamRenderer()
        renderer.set_feedback("msg")  # must not raise
        renderer.set_feedback(None)  # must not raise

    def test_set_feedback_app_exception_swallowed(self) -> None:
        app = MagicMock()
        app.update_feedback.side_effect = RuntimeError("broken")
        renderer = TextualStreamRenderer(app=app)
        renderer.set_feedback("msg")  # must not raise
