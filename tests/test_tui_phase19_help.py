"""Tests for Phase 19 help overlay."""

from __future__ import annotations

import pytest
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Static

from the_architect.tui.app import ArchitectApp
from the_architect.tui.screens.help import (
    HelpScreen,
    collect_screen_bindings,
)


class _SampleScreen(Screen[None]):
    BINDINGS = [
        Binding("a", "foo", "Foo"),
        Binding("b", "bar", "Bar"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("sample")


class TestCollectScreenBindings:
    def test_returns_screen_bindings_plus_globals(self) -> None:
        rows = collect_screen_bindings(_SampleScreen())
        keys = [key for key, _ in rows]
        assert "a" in keys
        assert "b" in keys
        # Globals appended at the end.
        assert "?" in keys
        assert "q" in keys

    def test_descriptions_are_preserved(self) -> None:
        rows = dict(collect_screen_bindings(_SampleScreen()))
        assert rows["a"] == "Foo"
        assert rows["b"] == "Bar"

    def test_deduplicates_binding_keys(self) -> None:
        class _DupeScreen(Screen[None]):
            BINDINGS = [
                Binding("a", "foo", "Foo"),
                Binding("a", "foo_again", "Foo again"),
            ]

            def compose(self) -> ComposeResult:
                yield Static("dupe")

        rows = collect_screen_bindings(_DupeScreen())
        a_rows = [row for row in rows if row[0] == "a"]
        assert len(a_rows) == 1


class TestHelpScreenRender:
    @pytest.mark.asyncio
    async def test_help_screen_shows_rows(self) -> None:
        bindings = [("a", "Foo"), ("b", "Bar")]
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(HelpScreen(bindings=bindings))
            await pilot.pause()
            help_screen = app.screen
            assert isinstance(help_screen, HelpScreen)
            table = help_screen.query_one(DataTable)
            assert table.row_count == 2


class TestActionHelp:
    """Pressing ? on any screen pushes the help overlay."""

    @pytest.mark.asyncio
    async def test_action_help_pushes_help_screen(self) -> None:
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_help()
            await pilot.pause()
            assert isinstance(app.screen, HelpScreen)
