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
