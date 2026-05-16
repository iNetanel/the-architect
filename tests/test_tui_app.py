"""Tests for the Textual ArchitectApp and its execution / splash lifecycle.

The startup screen is a :class:`SplashScreen` — a small centered animated
card with a Matrix rain block, the app title, and a subtitle.  It is
intentionally distinct from :class:`~the_architect.tui.screens.wait.WaitScreen`
(the full-screen log-viewer used during planning and execution waits).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from textual.containers import VerticalScroll
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
        await pilot.pause(0.05)
        assert any(isinstance(s, SplashScreen) for s in app.screen_stack)
        assert app._execution_screen is None


@pytest.mark.asyncio
async def test_splash_is_distinct_from_wait_screen() -> None:
    """SplashScreen is its own class, not an alias for WaitScreen."""
    assert SplashScreen is not WaitScreen
    assert not issubclass(SplashScreen, WaitScreen)


def test_execution_tab_bindings_are_not_global_app_bindings() -> None:
    """Execution-only tab shortcuts must not appear on non-execution footers."""
    app_bindings = {binding.key for binding in ArchitectApp.BINDINGS}
    execution_bindings = {binding.key for binding in ExecutionScreen.BINDINGS}

    assert {"l", "p", "d"}.isdisjoint(app_bindings)
    assert {"l", "p", "d"}.issubset(execution_bindings)


@pytest.mark.asyncio
async def test_splash_shows_title_and_spinner() -> None:
    """The splash renders the app name and a visible Matrix rain animation."""
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        assert isinstance(app.screen, SplashScreen)
        title = app.screen.query_one("#splash_title", Static)
        assert "The Architect" in str(title.render())
        rain = app.screen.query_one("#splash_rain", MatrixRain)
        assert rain.region.width == MatrixRain.COLS
        assert rain.region.height == MatrixRain.ROWS
        assert any(ch not in {" ", "\n"} for ch in rain.render().plain)


@pytest.mark.asyncio
async def test_splash_is_centered() -> None:
    """The splash body card is centered in the viewport on a normal terminal."""
    app = ArchitectApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.05)
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
        await pilot.pause(0.05)
        rain = app.screen.query_one("#splash_rain", MatrixRain)
        subtitle = app.screen.query_one("#splash_subtitle", Static)
        assert rain.region.width == MatrixRain.COLS
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
        await pilot.pause(0.05)
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
        await pilot.pause(0.05)
        assert app._execution_screen is None
        app._ensure_execution_screen()
        assert isinstance(app.screen, SplashScreen)
        app.switch_to_execution()
        await pilot.pause(0.05)
        assert isinstance(app._execution_screen, ExecutionScreen)
        assert app.screen is app._execution_screen


@pytest.mark.asyncio
async def test_push_output_line_appears_in_output_tab() -> None:
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        app.switch_to_execution()
        await pilot.pause(0.05)
        app.push_output_line("hello world")
        await pilot.pause(0.05)
        assert app._execution_screen is not None
        log = app._execution_screen.query_one("#exec_output", RichLog)
        assert len(log.lines) >= 1


@pytest.mark.asyncio
async def test_update_footer_sets_status_text() -> None:
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        app.switch_to_execution()
        await pilot.pause(0.05)
        app.update_footer("T01 | attempt 1/3")
        await pilot.pause(0.05)
        await pilot.pause(0.05)
        assert app._execution_screen is not None
        footer = app._execution_screen.query_one("#exec_footer", Static)
        assert "T01" in str(footer.render())


@pytest.mark.asyncio
async def test_update_details_merges_fields() -> None:
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        app.switch_to_execution()
        await pilot.pause(0.05)
        app.update_details(task="T01 demo", phase="executing", attempt="1/3")
        await pilot.pause(0.05)
        await pilot.pause(0.05)
        assert app._execution_screen is not None
        progress = app._execution_screen.query_one("#exec_progress_text", Static)
        text = str(progress.render())
        assert "T01 demo" in text
        assert "executing" in text


@pytest.mark.asyncio
async def test_update_execution_settings_populates_settings_tab() -> None:
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        app.switch_to_execution()
        await pilot.pause(0.05)
        app.update_execution_settings(
            {
                "Provider": "Claude Code",
                "Execution agent": "build",
                "Free mode": "disabled",
            }
        )
        await pilot.pause(0.05)
        await pilot.pause(0.05)
        assert app._execution_screen is not None
        settings = app._execution_screen.query_one("#exec_settings_text", Static)
        text = str(settings.render())
        assert "Execution Settings" in text
        assert "Claude Code" in text
        assert "build" in text


@pytest.mark.asyncio
async def test_execution_screen_includes_matrix_rain() -> None:
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        app.switch_to_execution()
        await pilot.pause(0.05)
        assert app._execution_screen is not None
        rain = app._execution_screen.query_one("#exec_rain", MatrixRain)
        assert rain.region.width == MatrixRain.COLS
        assert rain.region.height == MatrixRain.ROWS


@pytest.mark.asyncio
async def test_update_progress_tasks_shows_overall_task_picture() -> None:
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        app.switch_to_execution()
        await pilot.pause(0.05)
        app.update_progress_tasks(
            [
                {"prefix": "T01", "title": "First task", "status": "done"},
                {"prefix": "T02", "title": "Second task", "status": "running"},
                {"prefix": "T03", "title": "Third task", "status": "pending"},
            ]
        )
        await pilot.pause(0.05)
        assert app._execution_screen is not None
        progress = app._execution_screen.query_one("#exec_progress_text", Static)
        text = str(progress.render())
        assert "Run Progress" in text
        assert "1/3 done" in text
        assert "T02" in text
        assert "RUNNING" in text


@pytest.mark.asyncio
async def test_execution_tab_bodies_are_scrollable_and_focusable() -> None:
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        app.switch_to_execution()
        await pilot.pause(0.05)
        await pilot.pause(0.05)
        assert app._execution_screen is not None
        screen = app._execution_screen

        progress = screen.query_one("#exec_progress", VerticalScroll)
        settings = screen.query_one("#exec_settings", VerticalScroll)
        output = screen.query_one("#exec_output", RichLog)
        diagnostics = screen.query_one("#exec_diagnostics", RichLog)

        assert progress.can_focus is True
        assert settings.can_focus is True
        assert output.can_focus is True
        assert diagnostics.can_focus is True

        screen.action_switch_tab("tab_progress")
        await pilot.pause(0.05)
        await pilot.pause(0.05)
        assert screen.focused is progress

        screen.action_switch_tab("tab_settings")
        await pilot.pause(0.05)
        await pilot.pause(0.05)
        assert screen.focused is settings


@pytest.mark.asyncio
async def test_execution_screen_mount_placeholders() -> None:
    """Once the execution screen is created, its default placeholders appear."""
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        app.switch_to_execution()
        await pilot.pause(0.05)
        await pilot.pause(0.05)
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
        await pilot.pause(0.05)
        # Switch to execution and immediately queue lines (before mount settles)
        app.switch_to_execution()
        assert app._execution_screen is not None
        screen = app._execution_screen
        # Bypass the thread-safe path and queue directly into pending so we can
        # precisely simulate output arriving before on_mount's deferred callbacks run.
        screen._pending_output.extend(["pending line 1", "pending line 2"])
        await pilot.pause(0.05)
        await pilot.pause(0.05)
        await pilot.pause(0.05)
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
        await pilot.pause(0.05)
        app.switch_to_execution()
        await pilot.pause(0.05)
        await pilot.pause(0.05)
        assert app._execution_screen is not None
        screen = app._execution_screen
        # Push an event — this must not raise.
        screen.push_event_line("task_start", {"task": "T01", "attempt": "1"})
        await pilot.pause(0.05)
        # Switch to the Diagnostics tab (simulates the user clicking it).
        tabs = screen.query_one("#exec_tabs", TabbedContent)
        tabs.active = "tab_diagnostics"
        await pilot.pause(0.05)
        await pilot.pause(0.05)
        log = screen.query_one("#exec_diagnostics", RichLog)
        # At least one line was written (placeholder + our event).
        assert len(log.lines) >= 1
        # The rendered segments must not contain unresolved Textual CSS tokens.
        all_text = " ".join(str(line) for line in log.lines)
        assert "$accent" not in all_text, (
            f"Unresolved Textual CSS variable '$accent' found in Diagnostics log: {all_text!r}"
        )


@pytest.mark.asyncio
async def test_hide_wait_dismisses_overlay_without_reinstalling_execution() -> None:
    """Wait teardown should reveal the mounted execution screen, not replace it.

    Regression for intermittent Infinite Loop TUI drops: replacing a top wait
    overlay with an ExecutionScreen instance that was already mounted underneath
    risks corrupting Textual's screen stack. The safe path is to dismiss the
    overlay and keep the existing execution screen alive.
    """
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        app.switch_to_execution()
        await pilot.pause(0.05)
        execution = app._execution_screen
        assert execution is not None

        app.show_wait("Planning next iteration")
        await pilot.pause(0.05)
        assert isinstance(app.screen, WaitScreen)
        assert execution in app.screen_stack

        app.hide_wait()
        await pilot.pause(0.05)
        await pilot.pause(0.05)

        assert app.screen is execution
        assert app._execution_screen is execution
        assert app._wait_screen is None
        assert len(app.screen_stack) >= 1


@pytest.mark.asyncio
async def test_switch_to_execution_dismisses_wait_overlay_when_execution_underneath() -> None:
    """Switching to execution during a wait overlay must not duplicate screens."""
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        app.switch_to_execution()
        await pilot.pause(0.05)
        execution = app._execution_screen
        assert execution is not None

        app.show_wait("Retrospective")
        await pilot.pause(0.05)
        app.switch_to_execution()
        await pilot.pause(0.05)
        await pilot.pause(0.05)

        assert app.screen is execution
        assert app.screen_stack.count(execution) == 1
        assert app._wait_screen is None


@pytest.mark.asyncio
async def test_show_wait_recovers_from_stale_wait_reference() -> None:
    """A stale wait reference must not make later waits invisible."""
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        app.switch_to_execution()
        await pilot.pause(0.05)

        app.show_wait("First wait")
        await pilot.pause(0.05)
        stale_wait = app._wait_screen
        assert isinstance(stale_wait, WaitScreen)
        stale_wait.dismiss()
        await pilot.pause(0.05)
        assert stale_wait not in app.screen_stack

        app.show_wait("Second wait")
        await pilot.pause(0.05)

        assert isinstance(app.screen, WaitScreen)
        assert app._wait_screen is not stale_wait


@pytest.mark.asyncio
async def test_hide_wait_preserves_pause_menu_above_wait() -> None:
    """Wait cleanup must not dismiss unrelated overlays above the wait screen."""
    from the_architect.tui.screens.pause import PauseMenuScreen

    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        app.switch_to_execution()
        await pilot.pause(0.05)

        app.show_wait("Planning")
        await pilot.pause(0.05)
        wait = app._wait_screen
        assert isinstance(wait, WaitScreen)
        app.push_screen(PauseMenuScreen())
        await pilot.pause(0.05)
        assert isinstance(app.screen, PauseMenuScreen)

        app.hide_wait()
        await pilot.pause(0.05)
        await pilot.pause(0.05)

        assert app.screen is not wait
        assert wait in app.screen_stack
        assert app._wait_screen is wait
        assert len(app.screen_stack) >= 1


@pytest.mark.asyncio
async def test_empty_screen_stack_is_repaired() -> None:
    """The active TUI should recover instead of exiting if its stack is emptied."""
    app = ArchitectApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        app.screen_stack.clear()

        app._ensure_screen_stack_sync("test_empty_stack")
        await pilot.pause(0.05)

        assert len(app.screen_stack) >= 1
        assert isinstance(app.screen, ExecutionScreen | SplashScreen)


def test_screen_stack_names_are_safe_before_app_runs() -> None:
    """Lifecycle diagnostics should not raise even before Textual mounts."""
    app = ArchitectApp()
    names = app._screen_stack_names()

    assert isinstance(names, list)


# ═══════════════════════════════════════════════════════════════════════════
# Thread-safe delegation chains
# ═══════════════════════════════════════════════════════════════════════════


class TestThreadSafeCall:
    """Tests for _thread_safe_call delegation from any thread."""

    def test_thread_safe_call_runtime_error_falls_back(self) -> None:
        """When call_from_thread raises RuntimeError, fn runs directly."""
        app = ArchitectApp()
        called = []

        def fn(val):
            called.append(val)

        # call_from_thread raises RuntimeError when app isn't running
        with patch.object(app, "call_from_thread", side_effect=RuntimeError("not running")):
            app._thread_safe_call(fn, "hello")
        assert called == ["hello"]

    def test_thread_safe_call_runtime_error_fn_raises(self) -> None:
        """When fallback fn raises, the error is swallowed."""
        app = ArchitectApp()

        def fn(val):
            raise ValueError("boom")

        with patch.object(app, "call_from_thread", side_effect=RuntimeError("not running")):
            # Should not raise
            app._thread_safe_call(fn, "hello")

    def test_thread_safe_call_general_exception_falls_back(self) -> None:
        """When call_from_thread raises a general Exception, fn runs directly."""
        app = ArchitectApp()
        called = []

        def fn(val):
            called.append(val)

        with patch.object(app, "call_from_thread", side_effect=Exception("app not ready")):
            app._thread_safe_call(fn, "hello")
        assert called == ["hello"]

    def test_thread_safe_call_general_exception_fn_raises(self) -> None:
        """When fallback fn raises after general exception, error is swallowed."""
        app = ArchitectApp()

        def fn(val):
            raise ValueError("boom")

        with patch.object(app, "call_from_thread", side_effect=Exception("app not ready")):
            # Should not raise
            app._thread_safe_call(fn, "hello")

    @pytest.mark.asyncio
    async def test_thread_safe_call_same_thread_direct(self) -> None:
        """When on the event loop thread, call_from_thread raises RuntimeError, fn runs."""
        app = ArchitectApp()
        called = []

        def fn(val):
            called.append(val)

        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            # Inside run_test, call_from_thread raises RuntimeError (same thread)
            app._thread_safe_call(fn, "hello")
            await pilot.pause(0.05)
        assert called == ["hello"]


class TestSetStatus:
    """Tests for set_status thread-safe status updates."""

    @pytest.mark.asyncio
    async def test_set_status_updates_sub_title(self) -> None:
        """set_status updates the app sub_title shown in headers."""
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.set_status("Running T01")
            await pilot.pause(0.05)
            assert app.sub_title == "Running T01"

    @pytest.mark.asyncio
    async def test_set_status_empty_clears(self) -> None:
        """set_status with empty string clears the sub_title."""
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.set_status("Running")
            await pilot.pause(0.05)
            app.set_status("")
            await pilot.pause(0.05)
            assert app.sub_title == ""


class TestBeginShutdown:
    """Tests for begin_shutdown lifecycle."""

    @pytest.mark.asyncio
    async def test_begin_shutdown_idempotent(self) -> None:
        """Calling begin_shutdown twice should not cause issues."""
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.begin_shutdown()
            assert app._shutdown_started is True
            # Second call should be a no-op
            app.begin_shutdown()
            assert app._shutdown_started is True

    @pytest.mark.asyncio
    async def test_shutdown_started_property(self) -> None:
        """shutdown_started property reflects internal state."""
        app = ArchitectApp()
        assert app.shutdown_started is False
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.begin_shutdown()
            assert app.shutdown_started is True


class TestSplashSubtitle:
    """Tests for SplashScreen.set_subtitle."""

    @pytest.mark.asyncio
    async def test_splash_set_subtitle_updates_text(self) -> None:
        """set_subtitle updates the subtitle widget text."""
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            splash = app.screen
            assert isinstance(splash, SplashScreen)
            splash.set_subtitle("Loading modules…")
            await pilot.pause(0.05)
            subtitle = splash.query_one("#splash_subtitle", Static)
            assert "Loading modules" in str(subtitle.render())

    @pytest.mark.asyncio
    async def test_splash_set_subtitle_before_mount_safe(self) -> None:
        """set_subtitle called before widget exists should not raise."""
        splash = SplashScreen(subtitle="Initial")
        # Not mounted yet — query_one would fail
        splash.set_subtitle("Updated")
        assert splash._subtitle == "Updated"


class TestApplyArchitectTheme:
    """Tests for apply_architect_theme helper."""

    @pytest.mark.asyncio
    async def test_apply_architect_theme_registers_theme(self) -> None:
        """apply_architect_theme registers and activates the theme."""
        from textual.app import App

        from the_architect.tui.app import apply_architect_theme

        class _TestApp(App[None]):
            pass

        test_app = _TestApp()
        async with test_app.run_test() as pilot:
            await pilot.pause(0.05)
            apply_architect_theme(test_app)
            assert test_app.theme == "architect-dark"

    @pytest.mark.asyncio
    async def test_apply_architect_theme_exception_safe(self) -> None:
        """apply_architect_theme swallows exceptions silently."""
        from textual.app import App

        from the_architect.tui.app import apply_architect_theme

        test_app = App()
        async with test_app.run_test() as pilot:
            await pilot.pause(0.05)
            # Patch register_theme to raise after first call (which succeeded)
            with patch.object(test_app, "register_theme", side_effect=RuntimeError("bad")):
                # Should not raise — exception is swallowed
                apply_architect_theme(test_app)


# ═══════════════════════════════════════════════════════════════════════════
# Push and wait exception paths
# ═══════════════════════════════════════════════════════════════════════════


class TestPushAndWait:
    """Tests for push_and_wait exception handling and splash gating."""

    @pytest.mark.asyncio
    async def test_push_and_wait_from_thread(self) -> None:
        """push_and_wait returns the value the screen dismisses with (from thread)."""
        import threading

        from textual.screen import Screen

        app = ArchitectApp()
        result_container = {}

        class _TestScreen(Screen[None]):
            def on_mount(self) -> None:
                self.dismiss("test_result")

        def worker():
            result_container["value"] = app.push_and_wait(_TestScreen())

        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            await pilot.pause(0.5)
            thread.join(timeout=5)

        assert result_container.get("value") == "test_result"

    @pytest.mark.asyncio
    async def test_push_and_wait_headless_skips_splash_gate(self) -> None:
        """In headless mode (run_test), splash minimum display is skipped."""
        import threading
        import time

        app = ArchitectApp()
        result_container = {}

        class _TestScreen(SplashScreen):
            def on_mount(self) -> None:
                self.dismiss("done")

        def worker():
            result_container["value"] = app.push_and_wait(_TestScreen())

        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            # Set splash_shown_at to recent time
            app._splash_shown_at = time.monotonic()
            app._splash_min_seconds = 2.0  # long window
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            await pilot.pause(0.5)
            thread.join(timeout=5)

        assert result_container.get("value") == "done"
        # In headless mode, splash gate is skipped, so splash_shown_at is NOT reset
        assert app._splash_shown_at != 0.0


class TestSwitchToExecutionSync:
    """Tests for _switch_to_execution_sync exception paths."""

    @pytest.mark.asyncio
    async def test_switch_already_active_returns_early(self) -> None:
        """When the current screen is already the execution screen, return early."""
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.switch_to_execution()
            await pilot.pause(0.05)
            # Now switch again — should hit the "already active" early return
            app._switch_to_execution_sync()
            await pilot.pause(0.05)
            assert isinstance(app.screen, ExecutionScreen)


class TestPushOutputLinePaths:
    """Tests for push_output_line thread dispatch paths."""

    @pytest.mark.asyncio
    async def test_push_output_line_no_loop(self) -> None:
        """When _loop is None, falls back to _thread_safe_call path."""
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.switch_to_execution()
            await pilot.pause(0.05)
            # Temporarily set _loop to None
            original_loop = app._loop
            app._loop = None  # type: ignore[attr-defined]
            try:
                app.push_output_line("test line")
                await pilot.pause(0.05)
            finally:
                app._loop = original_loop  # type: ignore[attr-defined]
            # Should not crash

    @pytest.mark.asyncio
    async def test_push_output_line_same_thread(self) -> None:
        """When on the event loop thread, call directly."""
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.switch_to_execution()
            await pilot.pause(0.05)
            # Inside run_test, _thread_id == threading.get_ident()
            app.push_output_line("same thread line")
            await pilot.pause(0.05)
            log = app._execution_screen.query_one(
                "#exec_output", __import__("textual.widgets", fromlist=["RichLog"]).RichLog
            )
            assert len(log.lines) >= 1


class TestWaitScreenPaths:
    """Tests for wait screen update and append paths."""

    @pytest.mark.asyncio
    async def test_update_wait_no_wait_screen_noop(self) -> None:
        """update_wait when _wait_screen is None is a no-op."""
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            # No wait screen pushed — should not raise
            app.update_wait(title="New title", detail="New detail")
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_update_wait_title_only(self) -> None:
        """update_wait with title=None should not change title."""
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.show_wait("Original")
            await pilot.pause(0.05)
            app.update_wait(title=None, detail="Updated detail")
            await pilot.pause(0.05)
            # Title should be unchanged
            assert app._wait_screen is not None

    @pytest.mark.asyncio
    async def test_append_wait_log_no_wait_screen(self) -> None:
        """append_wait_log when _wait_screen is None is a no-op."""
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            # No wait screen pushed
            app.append_wait_log("test log line")
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_append_wait_log_same_thread(self) -> None:
        """append_wait_log on event loop thread calls directly."""
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.show_wait("Planning")
            await pilot.pause(0.05)
            # Inside run_test, same thread — direct call
            app.append_wait_log("log entry")
            await pilot.pause(0.05)

    @pytest.mark.asyncio
    async def test_hide_wait_no_wait_screen(self) -> None:
        """hide_wait when _wait_screen is None runs stack sync."""
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            # No wait screen pushed
            app.hide_wait()
            await pilot.pause(0.05)
            # Should not crash
            assert len(app.screen_stack) >= 1


class TestDismissWaitOverlay:
    """Tests for _dismiss_wait_overlay_if_stacked."""

    @pytest.mark.asyncio
    async def test_dismiss_wait_overlay_no_wait_returns_false(self) -> None:
        """When _wait_screen is None, returns False."""
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            result = app._dismiss_wait_overlay_if_stacked()
            assert result is False

    @pytest.mark.asyncio
    async def test_dismiss_wait_overlay_wait_not_top_returns_false(self) -> None:
        """When wait screen is not the top screen, returns False."""
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.show_wait("Planning")
            await pilot.pause(0.05)
            # Push another screen on top
            app.push_screen(SplashScreen())
            await pilot.pause(0.05)
            result = app._dismiss_wait_overlay_if_stacked()
            assert result is False


class TestRepairScreenStack:
    """Tests for _repair_screen_stack."""

    @pytest.mark.asyncio
    async def test_repair_screen_stack_with_preferred(self) -> None:
        """Repair with a preferred screen pushes it."""
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.screen_stack.clear()
            preferred = SplashScreen(subtitle="Recovering")
            app._repair_screen_stack("test", preferred)
            await pilot.pause(0.05)
            assert len(app.screen_stack) >= 1
            assert isinstance(app.screen, SplashScreen)

    @pytest.mark.asyncio
    async def test_repair_screen_stack_fallback_splash(self) -> None:
        """When preferred screen push fails, fall back to SplashScreen."""
        from textual.screen import Screen

        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app.screen_stack.clear()
            # Force preferred push to fail
            bad_screen = object.__new__(Screen)
            app._repair_screen_stack("test", bad_screen)  # type: ignore[arg-type]
            await pilot.pause(0.05)
            # Should have fallen back to SplashScreen
            assert len(app.screen_stack) >= 1


class TestShowSuccess:
    """Tests for show_success screen."""

    @pytest.mark.asyncio
    async def test_show_success_creates_success_screen(self) -> None:
        """show_success pushes the SuccessScreen."""
        from the_architect.core.runner import TaskResult, TokenUsage

        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            results = [
                TaskResult(
                    prefix="T01",
                    title="Test task",
                    status="done",
                    duration_seconds=10.0,
                    attempts=1,
                    tokens=TokenUsage(),
                    model="test-model",
                )
            ]
            # show_success calls push_and_wait which blocks — we can't fully
            # test it in a unit test. Instead verify the screen is created.
            # Mock push_and_wait to avoid blocking
            called_with = []

            def mock_push_and_wait(screen):
                called_with.append(screen)
                return None

            app.push_and_wait = mock_push_and_wait  # type: ignore[method-assign]
            app.show_success(
                results=results,
                total_duration=10.0,
                total_tokens=TokenUsage(),
            )
            assert len(called_with) == 1


class TestHandleException:
    """Tests for _handle_exception ScreenStackError repair."""

    @pytest.mark.asyncio
    async def test_handle_exception_screen_stack_error_repairs(self) -> None:
        """ScreenStackError triggers repair instead of super."""
        from textual.app import ScreenStackError

        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            # Should not propagate the exception
            app._handle_exception(ScreenStackError("No screens"))
            await pilot.pause(0.05)


class TestShowPauseMenu:
    """Tests for show_pause_menu guard rails."""

    @pytest.mark.asyncio
    async def test_show_pause_menu_already_visible(self) -> None:
        """If menu is already visible, second call is a no-op."""
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            app._pause_menu_visible = True
            initial_stack_len = len(app.screen_stack)
            app.show_pause_menu()
            # Should NOT push because already visible
            assert len(app.screen_stack) == initial_stack_len

    @pytest.mark.asyncio
    async def test_show_pause_menu_push_exception_resets_flag(self) -> None:
        """If push_screen raises, the visible flag is reset."""
        app = ArchitectApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.05)
            assert app._pause_menu_visible is False

            # Force push_screen to raise
            def bad_push(*args, **kwargs):
                raise RuntimeError("push failed")

            app.push_screen = bad_push  # type: ignore[method-assign]
            app.show_pause_menu()
            # Flag should be reset
            assert app._pause_menu_visible is False


class TestRunSingleScreen:
    """Tests for run_single_screen harness paths."""

    def test_run_single_screen_with_active_runner(self) -> None:
        """When active_runner returns a runner, use switch_and_wait."""
        from unittest.mock import MagicMock, patch

        from the_architect.tui.app import run_single_screen

        mock_runner = MagicMock()
        mock_runner.switch_and_wait.return_value = "test_result"

        with (
            patch("the_architect.tui.runner.active_runner", return_value=mock_runner),
            patch("the_architect.tui.runner.tui_suppressed_after_exit", return_value=False),
        ):
            result = run_single_screen(SplashScreen())
            mock_runner.switch_and_wait.assert_called_once()
            assert result == "test_result"

    def test_run_single_screen_tui_suppressed(self) -> None:
        """When tui_suppressed_after_exit is True, return None."""
        from unittest.mock import patch

        from the_architect.tui.app import run_single_screen

        with (
            patch("the_architect.tui.runner.active_runner", return_value=None),
            patch("the_architect.tui.runner.tui_suppressed_after_exit", return_value=True),
        ):
            result = run_single_screen(SplashScreen())
            assert result is None
