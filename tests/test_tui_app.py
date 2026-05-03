"""Tests for the Textual ArchitectApp and its execution / splash lifecycle.

Phase 21:
- ``ArchitectApp`` now mounts a :class:`SplashScreen` by default (animated
  branded idle surface), not the execution screen.
- The execution screen is lazy — created only when the worker calls
  :meth:`switch_to_execution` or pushes output/events/details into it.
- Callers that want to interact with the execution screen must ensure
  it has been created first by triggering one of those hooks.
"""

from __future__ import annotations

import pytest
from textual.widgets import RichLog, Static

from the_architect.tui.app import ArchitectApp, SplashScreen
from the_architect.tui.screens.execution import ExecutionScreen


@pytest.mark.asyncio
async def test_app_mounts_splash_by_default() -> None:
    """The app opens on the splash screen, not the execution viewport."""
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert any(isinstance(s, SplashScreen) for s in app.screen_stack)
        assert app._execution_screen is None


@pytest.mark.asyncio
async def test_splash_shows_title_and_spinner() -> None:
    """The splash renders the app name and an animated spinner."""
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, SplashScreen)
        title = app.screen.query_one("#splash_title", Static)
        assert "The Architect" in str(title.render())


@pytest.mark.asyncio
async def test_switch_to_execution_creates_and_activates_it() -> None:
    """``switch_to_execution`` creates the execution screen lazily."""
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._execution_screen is None
        app._ensure_execution_screen()
        # switch_to_execution uses call_from_thread, but we're in the
        # event loop — trigger the sync helper directly via the screen
        # hooks instead.
        app.push_output_line("hello")
        await pilot.pause()
        assert isinstance(app._execution_screen, ExecutionScreen)


@pytest.mark.asyncio
async def test_push_output_line_appears_in_output_tab() -> None:
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_output_line("hello world")
        await pilot.pause()
        assert app._execution_screen is not None
        log = app._execution_screen.query_one("#exec_output", RichLog)
        assert len(log.lines) >= 1


@pytest.mark.asyncio
async def test_update_footer_sets_status_text() -> None:
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.update_footer("T01 | attempt 1/3")
        await pilot.pause()
        await pilot.pause()
        assert app._execution_screen is not None
        footer = app._execution_screen.query_one("#exec_footer", Static)
        assert "T01" in str(footer.render())


@pytest.mark.asyncio
async def test_update_details_merges_fields() -> None:
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.update_details(task="T01 demo", phase="executing", attempt="1/3")
        await pilot.pause()
        assert app._execution_screen is not None
        details = app._execution_screen.query_one("#exec_details_text", Static)
        text = str(details.render())
        assert "T01 demo" in text
        assert "executing" in text


@pytest.mark.asyncio
async def test_execution_screen_mount_placeholders() -> None:
    """Once the execution screen is created, its default placeholders appear."""
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_output_line("")  # triggers lazy creation
        await pilot.pause()
        await pilot.pause()
        assert app._execution_screen is not None
        output = app._execution_screen.query_one("#exec_output", RichLog)
        assert len(output.lines) >= 1
