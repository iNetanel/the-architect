"""Tests for the Textual ArchitectApp and its execution / splash lifecycle.

The startup screen is a :class:`SplashScreen` — a small centered animated
card with a Matrix rain block, the app title, and a subtitle.  It is
intentionally distinct from :class:`~the_architect.tui.screens.wait.WaitScreen`
(the full-screen log-viewer used during planning and execution waits).
"""

from __future__ import annotations

import pytest
from textual.widgets import RichLog, Static

from the_architect.tui.app import ArchitectApp, SplashScreen
from the_architect.tui.screens.execution import ExecutionScreen
from the_architect.tui.screens.wait import WaitScreen
from the_architect.tui.widgets import MatrixRain


@pytest.mark.asyncio
async def test_app_mounts_splash_by_default() -> None:
    """The app opens on the centered SplashScreen, not the execution viewport."""
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert any(isinstance(s, SplashScreen) for s in app.screen_stack)
        assert app._execution_screen is None


@pytest.mark.asyncio
async def test_splash_is_distinct_from_wait_screen() -> None:
    """SplashScreen is its own class, not an alias for WaitScreen."""
    assert SplashScreen is not WaitScreen
    assert not issubclass(SplashScreen, WaitScreen)


@pytest.mark.asyncio
async def test_splash_shows_title_and_spinner() -> None:
    """The splash renders the app name and a visible Matrix rain animation."""
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, SplashScreen)
        title = app.screen.query_one("#splash_title", Static)
        assert "The Architect" in str(title.render())
        rain = app.screen.query_one("#splash_rain", MatrixRain)
        assert rain.region.height == MatrixRain.ROWS
        assert any(ch not in {" ", "\n"} for ch in rain.render().plain)


@pytest.mark.asyncio
async def test_splash_is_centered() -> None:
    """The splash body card is centered in the viewport on a normal terminal."""
    app = ArchitectApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        body = app.screen.query_one("#splash_body")
        # 48-wide card in an 80-wide terminal → x should be (80-48)/2 = 16
        assert body.region.x == 16
        # Card must be vertically centred — not at y=0 or y=1 (top-left)
        assert body.region.y > 1


@pytest.mark.asyncio
async def test_splash_animation_fits_short_startup_panes() -> None:
    """Startup rain renders above the subtitle on a normal terminal."""
    app = ArchitectApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        rain = app.screen.query_one("#splash_rain", MatrixRain)
        subtitle = app.screen.query_one("#splash_subtitle", Static)
        assert rain.region.height == MatrixRain.ROWS
        assert rain.region.y + rain.region.height <= subtitle.region.y
        assert subtitle.region.y + subtitle.region.height <= app.size.height


@pytest.mark.asyncio
async def test_splash_rain_styles_are_rich_parseable() -> None:
    """Every style the live rain emits must parse cleanly in Rich.

    Regression test: the widget used to emit ``"dim $text-muted"`` which
    Rich cannot parse (Textual's ``text-muted`` theme variable is
    ``"auto 60%"``, not a colour). The resulting ``StyleSyntaxError`` was
    silently swallowed during render and the whole Matrix-rain block
    appeared blank — exactly the bug the user reported as
    "the animation is not showing".
    """
    from rich.style import Style

    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        rain = app.screen.query_one("#splash_rain", MatrixRain)
        for _ in range(6):
            rain._tick()
        for raw in ("bold $accent", "$accent", "$accent-muted"):
            resolved = rain._resolve_style(raw)
            assert "$" not in resolved, f"unresolved Textual token in {resolved!r}"
            Style.parse(resolved)
        rendered = rain.render().plain
        assert any(ch not in {" ", "\n"} for ch in rendered)


@pytest.mark.asyncio
async def test_switch_to_execution_creates_and_activates_it() -> None:
    """``switch_to_execution`` creates the execution screen lazily."""
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._execution_screen is None
        app._ensure_execution_screen()
        assert isinstance(app.screen, SplashScreen)
        app.switch_to_execution()
        await pilot.pause()
        assert isinstance(app._execution_screen, ExecutionScreen)
        assert app.screen is app._execution_screen


@pytest.mark.asyncio
async def test_push_output_line_appears_in_output_tab() -> None:
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.switch_to_execution()
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
        app.switch_to_execution()
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
        app.switch_to_execution()
        await pilot.pause()
        app.update_details(task="T01 demo", phase="executing", attempt="1/3")
        await pilot.pause()
        assert app._execution_screen is not None
        progress = app._execution_screen.query_one("#exec_progress_text", Static)
        text = str(progress.render())
        assert "T01 demo" in text
        assert "executing" in text


@pytest.mark.asyncio
async def test_update_progress_tasks_shows_overall_task_picture() -> None:
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.switch_to_execution()
        await pilot.pause()
        app.update_progress_tasks(
            [
                {"prefix": "T01", "title": "First task", "status": "done"},
                {"prefix": "T02", "title": "Second task", "status": "running"},
                {"prefix": "T03", "title": "Third task", "status": "pending"},
            ]
        )
        await pilot.pause()
        assert app._execution_screen is not None
        progress = app._execution_screen.query_one("#exec_progress_text", Static)
        text = str(progress.render())
        assert "Run Progress" in text
        assert "1/3 done" in text
        assert "T02" in text
        assert "RUNNING" in text


@pytest.mark.asyncio
async def test_execution_screen_mount_placeholders() -> None:
    """Once the execution screen is created, its default placeholders appear."""
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.switch_to_execution()
        await pilot.pause()
        await pilot.pause()
        assert app._execution_screen is not None
        output = app._execution_screen.query_one("#exec_output", RichLog)
        assert len(output.lines) >= 1


@pytest.mark.asyncio
async def test_output_before_mount_does_not_show_placeholder() -> None:
    """Regression: output queued before DOM mount must not be overwritten by placeholder.

    The race: provider lines arrive via push_output_line before the execution
    screen's on_mount has run.  They go into _pending_output.  on_mount
    schedules _flush_pending then _write_default_placeholders via
    call_after_refresh.  If the callbacks ran in the wrong order (placeholder
    first, flush second) the placeholder appeared AFTER the real lines,
    leaving "Waiting for provider output…" at the bottom of the log.
    With the fix _flush_pending runs first and sets _output_received=True so
    _write_default_placeholders skips the output placeholder entirely.
    """
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Switch to execution and immediately queue lines (before mount settles)
        app.switch_to_execution()
        assert app._execution_screen is not None
        screen = app._execution_screen
        # Bypass the thread-safe path and queue directly into pending so we can
        # precisely simulate output arriving before on_mount's deferred callbacks run.
        screen._pending_output.extend(["pending line 1", "pending line 2"])
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
        log = screen.query_one("#exec_output", RichLog)
        rendered = " ".join(str(line) for line in log.lines)
        # Real lines must be present
        assert "pending line 1" in rendered
        assert "pending line 2" in rendered
        # Placeholder must NOT appear — it would mean _write_default_placeholders
        # ran after _flush_pending and clobbered the real output.
        assert "Waiting for provider output" not in rendered


@pytest.mark.asyncio
async def test_push_event_line_does_not_crash_on_diagnostics_tab() -> None:
    """Regression: clicking Diagnostics tab must not crash due to invalid Rich markup.

    ``[$accent]`` is a Textual CSS variable, not a valid Rich color tag.
    ``RichLog`` uses Rich markup, so passing ``[$accent]`` caused a
    ``MarkupError`` / crash whenever the Diagnostics tab was rendered.
    The fix replaces the CSS variable with the literal brand-green hex color.
    """
    from textual.widgets import TabbedContent

    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.switch_to_execution()
        await pilot.pause()
        await pilot.pause()
        assert app._execution_screen is not None
        screen = app._execution_screen
        # Push an event — this must not raise.
        screen.push_event_line("task_start", {"task": "T01", "attempt": "1"})
        await pilot.pause()
        # Switch to the Diagnostics tab (simulates the user clicking it).
        tabs = screen.query_one("#exec_tabs", TabbedContent)
        tabs.active = "tab_diagnostics"
        await pilot.pause()
        await pilot.pause()
        log = screen.query_one("#exec_diagnostics", RichLog)
        # At least one line was written (placeholder + our event).
        assert len(log.lines) >= 1
        # The rendered segments must not contain unresolved Textual CSS tokens.
        all_text = " ".join(str(line) for line in log.lines)
        assert "$accent" not in all_text, (
            f"Unresolved Textual CSS variable '$accent' found in Diagnostics log: {all_text!r}"
        )
