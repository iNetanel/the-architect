"""Textual Execution screen with live output, progress, and diagnostics tabs.

This screen hosts the live provider stream, an overall task progress view,
and deeper retry/circuit/reassessment diagnostics while a run is active. The
business logic continues to run in :mod:`the_architect.core.runner`; this
screen only renders what the renderer pushes into it.

Layout mirrors :class:`~the_architect.tui.screens.wait.WaitScreen`:

- An animated Matrix-rain title at the top (same spinner, same brand green)
- Four tabs below it:
  - **Live** — raw provider stream
  - **Progress** — overall task list and what is happening now
  - **Diagnostics** — retries, cooldowns, model switches, and circuit events
  - **Settings** — provider, model, agent, and feature flags for this execution
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, cast

from loguru import logger
from rich.markup import escape
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    DataTable,
    Header,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from the_architect.tui.constants import ANIMATION_TICK_INTERVAL
from the_architect.tui.widgets import MatrixRain, next_matrix_frame

if TYPE_CHECKING:
    from textual.timer import Timer


def _fmt_tokens(count: int) -> str:
    """Format a token count as K or raw integer.

    Args:
        count: Token count to format.

    Returns:
        Human-readable string like "12.3K" or "500".
    """
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def _footer_tabs_text() -> str:
    """Tab navigation hint shown at the bottom of each tab page."""
    return (
        "[dim]Tabs:  [l] Live  [p] Progress  [d] Diagnostics"
        "  [g] Settings  [c] Costs  [t] Tasks  ·  Tab ← → switch[/dim]"
    )


def _idle_footer_text() -> str:
    """Initial footer text shown before any run activity.

    ESC opens the pause menu (Continue / Detach / Exit); Ctrl+C is a
    direct hard stop.  Detach is available when running in Infinite
    Loop or persistent mode — the pause menu explains this inline.
    """
    return (
        "(idle)  [l]ive / [p]rogress / [d]iagnostics / settin[g]s"
        " / co[s]ts / tas[k]s  ·  Esc=pause  ·  Ctrl+C=stop"
    )


class ExecutionScreen(Screen[None]):
    """Main execution screen with animated header, tabbed viewport, and status footer."""

    # All bindings use priority=True so they fire regardless of which
    # child widget has focus (RichLog, TabbedContent, etc.).
    # Without this, tab-switch keys (l/p/d/g/c/tab/←→) and ESC need the child
    # widget to bubble them up first — causing missed or double keypresses.
    BINDINGS = [
        Binding("l", "switch_tab('tab_live')", "Live", show=False, priority=True),
        Binding("p", "switch_tab('tab_progress')", "Progress", show=False, priority=True),
        Binding("d", "switch_tab('tab_diagnostics')", "Diagnostics", show=False, priority=True),
        Binding("g", "switch_tab('tab_settings')", "Settings", show=False, priority=True),
        Binding("c", "switch_tab('tab_costs')", "Costs", show=False, priority=True),
        Binding("t", "switch_tab('tab_tasks')", "Tasks", show=False, priority=True),
        Binding("o", "switch_tab('tab_live')", "Live", show=False, priority=True),
        Binding("e", "switch_tab('tab_diagnostics')", "Diagnostics", show=False, priority=True),
        Binding("s", "switch_tab('tab_progress')", "Progress", show=False, priority=True),
        Binding("left", "prev_tab", "Previous tab", show=False, priority=True),
        Binding("right", "next_tab", "Next tab", show=False, priority=True),
        Binding("tab", "next_tab", "Next tab", show=False, priority=True),
        Binding("shift+tab", "prev_tab", "Previous tab", show=False, priority=True),
        Binding("escape", "pause_menu", "Pause menu", priority=True),
    ]

    DEFAULT_CSS = """
    ExecutionScreen {
        layout: vertical;
    }

    #exec_header_row {
        height: 1;
        padding: 0 1;
        margin: 0 0 0 0;
    }

    #exec_anim_title {
        color: $accent;
        text-style: bold;
        width: 1fr;
    }

    #exec_task_badge {
        color: $text-muted;
        text-align: right;
        width: auto;
        padding: 0 1;
    }

    #exec_rain_row {
        width: 100%;
        height: __MATRIX_RAIN_ROWS__;
        align-horizontal: center;
        margin: 1 0 0 0;
    }

    #exec_tabs {
        height: 1fr;
    }

    /* Green tab underline + active/hover tab text — matches The Architect brand. */
    ExecutionScreen Underline > .underline--bar {
        color: $accent;
    }
    ExecutionScreen Tab.-active {
        color: $accent;
        text-style: bold;
    }
    ExecutionScreen Tab:hover {
        color: $accent;
    }

    #exec_output, #exec_diagnostics {
        height: 1fr;
        border: round $panel;
        padding: 0 1;
    }

    #exec_progress, #exec_settings {
        height: 1fr;
        border: round $panel;
        padding: 1 2;
    }

    #exec_costs {
        height: 1fr;
        border: round $panel;
        padding: 1 2;
    }

    #exec_tasks {
        height: 1fr;
        border: round $panel;
        padding: 0 1;
    }

    #exec_footer {
        dock: bottom;
        height: 1;
        padding: 0 1;
        color: $text;
        background: $panel;
    }
    """.replace("__MATRIX_RAIN_ROWS__", str(MatrixRain.ROWS))

    def __init__(self) -> None:
        super().__init__()
        self._details: dict[str, str] = {
            "task": "(waiting)",
            "phase": "idle",
            "attempt": "",
            "model": "",
            "tokens": "",
            "last_activity": "",
            "current_op": "",
        }
        self._settings: dict[str, str] = {}
        self._progress_tasks: list[dict[str, str]] = []
        self._frame_index = 0
        self._current_frame = next_matrix_frame(self._frame_index)
        # Pending updates buffered before first mount so callers who
        # push content while the screen is still being composed don't
        # silently lose their messages. Flushed from on_mount.
        self._pending_output: list[str] = []
        self._pending_diagnostics: list[tuple[str, dict[str, object] | None]] = []
        self._pending_footer: str | None = None
        self._costs: dict[str, object] = {}
        self._pending_costs: dict[str, object] | None = None
        # Track whether any real provider output has been received so the
        # placeholder can be cleared on the first write.
        self._output_received: bool = False
        # Pending feedback message loaded by the runner. Displayed in the
        # footer prefix until the runner clears it after consumption.
        self._feedback_message: str | None = None
        # Upstream artifact count loaded by the runner. Displayed in the
        # footer prefix so the user knows context is being injected.
        self._artifact_count: int = 0
        # Per-task details for the Tasks tab DataTable. Keys are task
        # prefixes; values are dicts with optional "tokens", "model", and
        # "circuit" keys. Populated by update_progress_tasks().
        self._task_details: dict[str, dict[str, str]] = {}
        # Timer handle for the animated spinner — stopped on unmount
        # to prevent leaks.
        self._timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="exec_header_row"):
            yield Static(self._render_anim_title(), id="exec_anim_title")
            yield Static("", id="exec_task_badge")
        with Horizontal(id="exec_rain_row"):
            yield MatrixRain(id="exec_rain")
        with TabbedContent(id="exec_tabs"):
            with TabPane("Live", id="tab_live"):
                yield RichLog(
                    id="exec_output",
                    highlight=False,
                    markup=False,
                    wrap=True,
                    auto_scroll=True,
                )
            with TabPane("Progress", id="tab_progress"):
                with VerticalScroll(id="exec_progress"):
                    yield Static(self._render_progress(), id="exec_progress_text")
            with TabPane("Diagnostics", id="tab_diagnostics"):
                yield RichLog(
                    id="exec_diagnostics",
                    highlight=False,
                    markup=True,
                    wrap=True,
                    auto_scroll=True,
                )
            with TabPane("Settings", id="tab_settings"):
                with VerticalScroll(id="exec_settings"):
                    yield Static(self._render_settings(), id="exec_settings_text")
            with TabPane("Costs", id="tab_costs"):
                with VerticalScroll(id="exec_costs"):
                    yield Static(self._render_costs(), id="exec_costs_text")
            with TabPane("Tasks", id="tab_tasks"):
                with VerticalScroll(id="exec_tasks"):
                    yield DataTable(zebra_stripes=True, id="exec_tasks_table")
        yield Static(
            _idle_footer_text(),
            id="exec_footer",
        )

    def on_mount(self) -> None:
        # Start the spinner at 10 FPS — same cadence as WaitScreen.
        self._timer = self.set_interval(ANIMATION_TICK_INTERVAL, self._tick_spinner)
        # Make every tab body focusable so keyboard scrolling works when a tab
        # has more content than the available terminal height.
        for scroll_id in (
            "#exec_output",
            "#exec_diagnostics",
            "#exec_progress",
            "#exec_settings",
            "#exec_costs",
            "#exec_tasks",
        ):
            try:
                self.query_one(scroll_id).can_focus = True
            except Exception as exc:
                logger.debug(f"ExecutionScreen set can_focus for {scroll_id} failed: {exc!r}")
        self._focus_active_tab_scroller()
        self.call_after_refresh(self._focus_active_tab_scroller)
        # Flush any output that arrived before the DOM was ready first,
        # then write placeholders only for tabs that received nothing.
        # Both callbacks are deferred to the post-refresh tick so every
        # TabPane child is mounted before we query widgets.
        # Order matters: flush → placeholder, so that if real output
        # arrived before mount, _output_received is True by the time
        # _write_default_placeholders runs and the placeholder is skipped.
        self.call_after_refresh(self._flush_pending)
        self.call_after_refresh(self._write_default_placeholders)

    def on_hide(self) -> None:
        """Pause the spinner animation while the screen is hidden behind an overlay."""
        if self._timer is not None:
            self._timer.pause()

    def on_resume(self) -> None:
        """Restart the spinner animation when the screen becomes visible again."""
        if self._timer is not None:
            self._timer.resume()

    def on_unmount(self) -> None:
        """Stop the spinner timer to prevent leaks after removal."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _flush_pending(self) -> None:
        """Apply any output/events/footer updates queued before mount."""
        if self._pending_output:
            try:
                log = self.query_one("#exec_output", RichLog)
                if not self._output_received:
                    self._output_received = True
                for line in self._pending_output:
                    log.write(line)
            except Exception as exc:
                logger.warning(f"ExecutionScreen flush pending output failed: {exc!r}")
            self._pending_output.clear()
        for event, data in self._pending_diagnostics:
            self._write_diagnostic_line(event, data)
        self._pending_diagnostics.clear()
        if self._pending_footer is not None:
            try:
                self.query_one("#exec_footer", Static).update(self._pending_footer)
            except Exception as exc:
                logger.debug(f"ExecutionScreen flush pending footer failed: {exc!r}")
            self._pending_footer = None
        if self._pending_costs is not None:
            self.update_costs(self._pending_costs)
            self._pending_costs = None
        self._refresh_summary_widgets()
        # Flush the Tasks tab DataTable now that widgets are mounted.
        if self._progress_tasks:
            self.update_tasks_table(self._progress_tasks)

    def on_resize(self, event: events.Resize) -> None:
        """Refresh DataTable layout after terminal resize.

        Ensures column widths and row heights recalculate correctly
        when the terminal window is resized during execution.
        """
        try:
            self.refresh(layout=True)
        except Exception as exc:
            logger.debug(f"ExecutionScreen resize refresh failed: {exc!r}")

    def _write_default_placeholders(self) -> None:
        # Only write the output placeholder when no real provider output
        # has arrived yet.  _flush_pending runs before this callback and
        # sets _output_received=True when it drains buffered lines, so
        # fast providers that send output before the DOM is ready won't
        # have their content overwritten by this placeholder.
        if not self._output_received:
            try:
                self.query_one("#exec_output", RichLog).write("Waiting for provider output…")
            except Exception as exc:
                logger.debug(f"ExecutionScreen output placeholder write failed: {exc!r}")
        try:
            self.query_one("#exec_diagnostics", RichLog).write(
                "No diagnostics yet. Retries, cooldowns, model switches, "
                "and circuit events appear here."
            )
        except Exception as exc:
            logger.debug(f"ExecutionScreen diagnostics placeholder write failed: {exc!r}")

    # ── Spinner ────────────────────────────────────────────────────────

    def _tick_spinner(self) -> None:
        """Advance the Matrix-rain frame on the animated title line."""
        self._frame_index += 1
        self._current_frame = next_matrix_frame(self._frame_index)
        try:
            self.query_one("#exec_anim_title", Static).update(self._render_anim_title())
        except Exception as exc:
            logger.debug(f"ExecutionScreen tick spinner update failed: {exc!r}")

    def _render_anim_title(self) -> str:
        phase = self._details.get("phase", "idle")
        task = self._details.get("task", "")
        if task and task != "(waiting)":
            label = f"executing  ·  {task}"
        else:
            label = "execution  ·  waiting for task…"
        return f"{self._current_frame}  {label}  [dim]({phase})[/dim]"

    def action_switch_tab(self, tab_id: str) -> None:
        """Switch execution tabs from execution-scoped key bindings."""
        try:
            self.query_one("#exec_tabs", TabbedContent).active = tab_id
            # Focus synchronously — defer causes a one-frame gap where
            # the next keypress goes to the wrong widget.
            self._focus_active_tab_scroller()
        except Exception as exc:
            logger.debug(f"ExecutionScreen switch_tab({tab_id!r}) failed: {exc!r}")

    def action_next_tab(self) -> None:
        """Switch to the next tab in order."""
        self._switch_tab_direction(1)

    def action_prev_tab(self) -> None:
        """Switch to the previous tab in order."""
        self._switch_tab_direction(-1)

    def _switch_tab_direction(self, direction: int) -> None:
        """Move to the next or previous execution tab.

        Args:
            direction: 1 for next, -1 for previous.
        """
        # Tab order matches compose() yield order
        tab_order = [
            "tab_live",
            "tab_progress",
            "tab_diagnostics",
            "tab_settings",
            "tab_costs",
            "tab_tasks",
        ]
        try:
            tabs = self.query_one("#exec_tabs", TabbedContent)
            current = tabs.active
            if current in tab_order:
                idx = tab_order.index(current)
                target = tab_order[(idx + direction) % len(tab_order)]
                tabs.active = target
                self._focus_active_tab_scroller()
        except Exception as exc:
            logger.debug(f"ExecutionScreen switch_tab({direction}) failed: {exc!r}")

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Refocus the scrollable content after a mouse click on a tab header."""
        self._focus_active_tab_scroller()
        self.call_after_refresh(self._focus_active_tab_scroller)

    def on_key(self, event: events.Key) -> None:
        """Handle arrow-key tab switching as backup when bindings are consumed.

        When a child widget (RichLog, DataTable) intercepts left/right before
        the screen-level BINDINGS fire, this handler catches the bubbled event
        and switches tabs. The bindings (priority=True) are the primary path;
        this is the safety net.
        """
        if event.key == "left":
            event.stop()
            event.prevent_default()
            self.action_prev_tab()
        elif event.key == "right":
            event.stop()
            event.prevent_default()
            self.action_next_tab()

    def _focus_active_tab_scroller(self) -> None:
        """Focus the active tab's scrollable body for keyboard scrolling."""
        try:
            active = self.query_one("#exec_tabs", TabbedContent).active
            target_id = {
                "tab_live": "#exec_output",
                "tab_progress": "#exec_progress",
                "tab_diagnostics": "#exec_diagnostics",
                "tab_settings": "#exec_settings",
                "tab_costs": "#exec_costs",
                "tab_tasks": "#exec_tasks",
            }.get(active)
            if target_id:
                self.query_one(target_id).focus()
        except Exception as exc:
            logger.debug(f"ExecutionScreen focus active tab scroller failed: {exc!r}")

    # ── Renderer hooks ─────────────────────────────────────────────────

    def push_output_line(self, line: str) -> None:
        """Append a provider output line to the Live tab."""
        # Update last-activity timestamp whenever output arrives.
        self._details["last_activity"] = datetime.now().strftime("%H:%M:%S")
        try:
            log = self.query_one("#exec_output", RichLog)
        except Exception as exc:
            logger.debug(f"ExecutionScreen push_output_line query failed (buffering): {exc!r}")
            self._pending_output.append(line)
            return
        # On the first real output line, clear the placeholder text so it
        # doesn't sit above every provider message in the log.
        if not self._output_received:
            self._output_received = True
            try:
                log.clear()
            except Exception as exc:
                logger.debug(f"ExecutionScreen push_output_line clear failed: {exc!r}")
        log.write(line)

    def push_event_line(self, event: str, data: dict[str, object] | None = None) -> None:
        """Append an operational event to the Diagnostics tab."""
        try:
            self.query_one("#exec_diagnostics", RichLog)
        except Exception as exc:
            logger.debug(f"ExecutionScreen push_event_line query failed (buffering): {exc!r}")
            self._pending_diagnostics.append((event, data))
            return
        self._write_diagnostic_line(event, data)

    def _write_diagnostic_line(self, event: str, data: dict[str, object] | None) -> None:
        try:
            log = self.query_one("#exec_diagnostics", RichLog)
            now = datetime.now().strftime("%H:%M:%S")
            payload = " ".join(f"{k}=[cyan]{v}[/cyan]" for k, v in (data or {}).items())
            log.write(f"[dim]{now}[/dim]  [#7cc800]{event}[/#7cc800]  {payload}")
        except Exception as exc:
            logger.debug(f"ExecutionScreen write_diagnostic_line({event!r}) failed: {exc!r}")

    def update_progress_tasks(self, tasks: list[dict[str, str]]) -> None:
        """Replace the Progress tab's task overview and refresh it.

        Also updates the Tasks tab DataTable if it is mounted.
        """
        self._progress_tasks = tasks
        self._refresh_summary_widgets()
        # Update the Tasks tab DataTable — no-op if not mounted yet.
        self.update_tasks_table(tasks)

    def update_footer(self, text: str) -> None:
        """Set the one-line status footer under the tabs.

        If pending feedback is set, it is prepended to the base footer text
        with a visual separator so the user can see that feedback will be
        consumed by the next task.
        """
        rendered = self._render_footer_text(text)
        try:
            footer = self.query_one("#exec_footer", Static)
        except Exception as exc:
            logger.debug(f"ExecutionScreen update_footer query failed (buffering): {exc!r}")
            self._pending_footer = rendered
            return
        footer.update(rendered)

    def update_feedback(self, message: str | None) -> None:
        """Set or clear the pending feedback banner in the footer.

        Args:
            message: Feedback text to display, or ``None`` to clear.
        """
        self._feedback_message = message
        # If the footer widget is already mounted, refresh it with the
        # current base text so the feedback appears or disappears now.
        try:
            current_footer = self.query_one("#exec_footer", Static)
            # Strip any existing feedback prefix before re-rendering,
            # so clearing feedback actually removes the prefix.
            base = self._strip_feedback_prefix(str(current_footer.render_str))
            self.update_footer(base)
            return
        except Exception as exc:
            logger.debug(f"ExecutionScreen update_feedback query failed: {exc!r}")
        # If the widget isn't mounted yet, update the pending footer
        # so the next flush renders with the new feedback state.
        if self._pending_footer is not None:
            # Strip any existing feedback prefix from the pending text
            # and re-render with the new feedback state.
            base = self._strip_feedback_prefix(self._pending_footer)
            self._pending_footer = self._render_footer_text(base)

    def update_artifacts(self, count: int) -> None:
        """Set the upstream artifact count displayed in the footer.

        When *count* is positive, the footer shows an artifact indicator
        (e.g. ``3 upstream artifacts``) so the user knows upstream context
        is being injected.  Zero clears the indicator.

        Args:
            count: Number of upstream artifacts for the current task.
        """
        self._artifact_count = max(count, 0)
        # If the footer widget is already mounted, refresh it.
        try:
            current_footer = self.query_one("#exec_footer", Static)
            base = self._strip_artifacts_suffix(str(current_footer.render_str))
            self.update_footer(base)
            return
        except Exception as exc:
            logger.debug(f"ExecutionScreen update_artifacts query failed: {exc!r}")
        # If the widget isn't mounted yet, update the pending footer.
        if self._pending_footer is not None:
            base = self._strip_artifacts_suffix(self._pending_footer)
            self._pending_footer = self._render_footer_text(base)

    def _strip_feedback_prefix(self, rendered: str) -> str:
        """Remove a previously prepended feedback prefix from rendered text.

        Returns the original base text if no feedback prefix is found.

        Args:
            rendered: Footer text that may contain a feedback prefix.

        Returns:
            Base footer text without the feedback prefix.
        """
        prefix = "[yellow]⚡ Feedback[/yellow]:"
        if rendered.startswith(prefix):
            # Find the "  ·  " separator after the feedback content
            sep = "  ·  "
            after_prefix = rendered[len(prefix) :]
            idx = after_prefix.find(sep)
            if idx != -1:
                return after_prefix[idx + len(sep) :]
        return rendered

    def _strip_artifacts_suffix(self, rendered: str) -> str:
        """Remove a previously appended artifact indicator from rendered text.

        Returns the original base text if no artifact suffix is found.

        Args:
            rendered: Footer text that may contain an artifact indicator.

        Returns:
            Base footer text without the artifact indicator.
        """
        suffix_marker = "  ·  [cyan]📦"
        idx = rendered.find(suffix_marker)
        if idx != -1:
            return rendered[:idx].rstrip()
        return rendered

    def _render_footer_text(self, base_text: str) -> str:
        """Compose the final footer string from base text, feedback, and artifacts.

        When feedback is pending, the footer shows the feedback message
        prefixed with a yellow indicator.  When upstream artifacts are
        being injected, a cyan artifact indicator is appended.  Both can
        be active simultaneously.

        Args:
            base_text: The normal footer text (phase, keys, etc.).

        Returns:
            Final footer string with feedback prepended and/or artifacts
            appended if active.
        """
        parts: list[str] = []

        # Feedback prefix (left side)
        if self._feedback_message is not None:
            msg = self._feedback_message
            max_len = 80
            if len(msg) > max_len:
                msg = msg[: max_len - 3] + "…"
            parts.append(f"[yellow]⚡ Feedback[/yellow]: {escape(msg)}")

        # Base text (middle)
        parts.append(base_text)

        # Artifact count suffix (right side)
        if self._artifact_count > 0:
            n = self._artifact_count
            label = f"{n} upstream artifact{'s' if n != 1 else ''}"
            parts.append(f"[cyan]📦 {label}[/cyan]")

        return "  ·  ".join(parts)

    def update_details(self, **fields: str) -> None:
        """Merge run metadata into the Progress tab and refresh the title."""
        self._details.update({k: v for k, v in fields.items() if v is not None})
        # Also update the task badge in the header row.
        try:
            badge = self.query_one("#exec_task_badge", Static)
            task = self._details.get("task", "")
            if task and task != "(waiting)":
                badge.update(f"[dim]{task}[/dim]")
        except Exception as exc:
            logger.debug(f"ExecutionScreen update_details badge update failed: {exc!r}")
        self._refresh_summary_widgets()
        # Refresh the animated title so it shows the new task label immediately.
        try:
            self.query_one("#exec_anim_title", Static).update(self._render_anim_title())
        except Exception as exc:
            logger.debug(f"ExecutionScreen update_details anim title update failed: {exc!r}")

    def update_settings(self, settings: dict[str, str]) -> None:
        """Replace the Settings tab content with run-scoped execution settings."""
        self._settings = {k: v for k, v in settings.items() if v is not None}
        self._refresh_summary_widgets()

    def update_costs(self, costs: dict[str, object]) -> None:
        """Replace the Costs tab content with live session cost data.

        Args:
            costs: Mapping with keys ``session_cost_usd``, ``last_task_cost_usd``,
                ``session_tokens``, and ``model_costs``.
        """
        self._costs = {k: v for k, v in costs.items() if v is not None}
        try:
            self.query_one("#exec_costs_text", Static).update(self._render_costs())
        except Exception as exc:
            logger.debug(f"ExecutionScreen update_costs widget update failed (buffering): {exc!r}")
            self._pending_costs = costs

    def update_tasks_table(self, tasks: list[dict[str, str]]) -> None:
        """Update the Tasks tab DataTable with current task state.

        Each task dict may contain: prefix, title, status, tokens, model.
        The DataTable is cleared and rebuilt on each call — the table is
        small enough (typically 5-20 rows) that this is efficient.

        Args:
            tasks: List of task dicts from the runner callbacks.
        """
        # Store per-task details for later refreshes
        for task in tasks:
            prefix = task.get("prefix", "")
            if prefix:
                self._task_details[prefix] = {
                    k: v for k, v in task.items() if k not in ("prefix", "title", "status")
                }

        try:
            table = self.query_one("#exec_tasks_table", DataTable)
        except Exception as exc:
            logger.debug(f"ExecutionScreen update_tasks_table DataTable query failed: {exc!r}")
            return

        table.clear(columns=True)
        table.add_columns(
            "Task", "Title", "Status", "Priority", "Tokens", "Model", "Circuit", "Notes"
        )

        for task in tasks:
            prefix = task.get("prefix", "")
            title = task.get("title", "")
            status = task.get("status", "pending")
            priority = task.get("priority", "")
            tokens_raw = task.get("tokens", "")
            model = task.get("model", "")

            # Format tokens
            tokens_str = "—"
            if tokens_raw and tokens_raw not in ("", "—"):
                try:
                    token_count = int(tokens_raw)
                    tokens_str = _fmt_tokens(token_count)
                except (ValueError, TypeError):
                    tokens_str = tokens_raw

            # Priority display — color-coded indicator
            priority_str = {
                "critical": "[red]● Critical[/]",
                "high": "[yellow]● High[/]",
                "medium": "[dim]○ Medium[/]",
                "low": "[dim]○ Low[/]",
            }.get(priority or "medium", "[dim]○ Medium[/]")

            # Shorten model name for display
            model_short = model.split("/")[-1] if "/" in model else model
            if not model_short:
                model_short = "—"

            # Circuit breaker label — maps task status to circuit state
            circuit_str = {
                "running": "CLOSED",
                "done": "CLOSED",
                "failed": "OPEN",
                "skipped": "HALF_OPEN",
                "blocked": "HALF_OPEN",
                "pending": "CLOSED",
            }.get(status, "CLOSED")

            notes = task.get("notes", "")
            if not notes:
                notes = "\u2014"

            table.add_row(
                prefix,
                title,
                status.upper(),
                priority_str,
                tokens_str,
                model_short,
                circuit_str,
                notes,
            )

    def update_validation_gate(self, gate_result: object) -> None:
        """Display validation gate status in the Diagnostics tab.

        Accepts a ValidationGateResult (passed as ``object`` to avoid
        circular imports). The result has ``passed: bool`` and
        ``checks: list[GateCheckResult]`` where each check has
        ``name``, ``status``, ``output``, and ``duration``.

        Args:
            gate_result: A ValidationGateResult instance (typed as object).
        """
        try:
            passed = getattr(gate_result, "passed", None)
            checks = getattr(gate_result, "checks", [])
        except Exception as exc:
            logger.debug(f"ExecutionScreen update_validation_gate getattr failed: {exc!r}")
            return

        if passed is True:
            total = len(checks)
            icon = "[green]✓[/green]"
            status_text = f"[green]passed[/green] ({total}/{total})"
            line = f"Validation Gate: {icon} {status_text}"
        else:
            # Failed — show which checks failed
            failed_checks = []
            for c in checks:
                name = getattr(c, "name", "?")
                status = getattr(c, "status", "?")
                if status != "pass":
                    failed_checks.append(f"{name}: {status}")
            if failed_checks:
                detail = ", ".join(failed_checks)
            else:
                detail = "unknown"
            icon = "[red]✗[/red]"
            line = f"Validation Gate: {icon} [red]failed[/red] ({detail})"

        try:
            self.push_event_line("validation_gate", {"result": line})
        except Exception as exc:
            logger.debug(f"ExecutionScreen validation_gate event push failed: {exc!r}")

    def _refresh_summary_widgets(self) -> None:
        """Refresh cached progress/settings/costs state after mount or updates."""
        try:
            self.query_one("#exec_progress_text", Static).update(self._render_progress())
        except Exception as exc:
            logger.debug(f"ExecutionScreen refresh progress widget failed: {exc!r}")
        try:
            self.query_one("#exec_settings_text", Static).update(self._render_settings())
        except Exception as exc:
            logger.debug(f"ExecutionScreen refresh settings widget failed: {exc!r}")
        try:
            self.query_one("#exec_costs_text", Static).update(self._render_costs())
        except Exception as exc:
            logger.debug(f"ExecutionScreen refresh costs widget failed: {exc!r}")

    # ── Actions ────────────────────────────────────────────────────────

    def action_pause_menu(self) -> None:
        """Show the pause menu (Continue / Detach / Exit).

        Delegates to the hosting :class:`ArchitectApp` so the overlay
        is a proper app-level modal and only one instance can ever be
        on screen at a time.
        """
        try:
            self.app.show_pause_menu()  # type: ignore[attr-defined]
        except Exception as exc:
            logger.debug(f"ExecutionScreen pause menu dispatch failed: {exc!r}")

    # ── Internal ──────────────────────────────────────────────────────

    def _render_progress(self) -> str:
        d = self._details
        task = d.get("task", "(waiting)")
        phase = d.get("phase", "idle")
        attempt = d.get("attempt", "—")
        model = d.get("model", "—")
        tokens = d.get("tokens", "—")
        last_activity = d.get("last_activity", "—")
        current_op = d.get("current_op", "")
        total = len(self._progress_tasks)
        done = sum(1 for item in self._progress_tasks if item.get("status") == "done")
        failed = sum(1 for item in self._progress_tasks if item.get("status") == "failed")
        running = sum(1 for item in self._progress_tasks if item.get("status") == "running")
        pending = max(total - done - failed - running, 0)

        lines = [
            "[#7cc800][bold]Run Progress[/bold][/#7cc800]",
            "",
        ]

        # Phase badge
        phase_colour = {
            "executing": "#7cc800",
            "starting": "yellow",
            "cooldown": "yellow",
            "replanning": "yellow",
            "replan_done": "#7cc800",
            "resumed": "#7cc800",
            "sleeping": "dim",
            "idle": "dim",
        }.get(phase, "dim")
        lines.append(f"  Now          [{phase_colour}]{phase}[/{phase_colour}]")

        lines.append(f"  Current      [bold]{task}[/bold]")
        if attempt:
            lines.append(f"  Attempt      {attempt}")
        if model and model not in ("", "—"):
            lines.append(f"  Model        [dim]{model}[/dim]")
        if tokens and tokens not in ("", "—"):
            lines.append(f"  Tokens       [dim]{tokens}[/dim]")
        if current_op:
            lines.append(f"  Operation    [cyan]{current_op}[/cyan]")
        if last_activity and last_activity != "—":
            lines.append(f"  Last output  [dim]{last_activity}[/dim]")

        if total:
            lines += [
                "",
                f"  Tasks        {done}/{total} done  ·  {running} running  ·  "
                f"{pending} pending  ·  {failed} failed",
                "",
            ]
            for item in self._progress_tasks:
                prefix = item.get("prefix", "")
                title = item.get("title", "")
                status = item.get("status", "pending")
                priority = item.get("priority", "")
                colour = {
                    "running": "yellow",
                    "done": "#7cc800",
                    "failed": "red",
                    "skipped": "dim",
                    "blocked": "yellow",
                    "pending": "dim",
                }.get(status, "dim")
                marker = ">" if status == "running" else " "
                status_label = status.upper()
                # Priority indicator — color-coded
                priority_indicator = {
                    "critical": "[red]●[/] ",
                    "high": "[yellow]●[/] ",
                    "medium": "",
                    "low": "[dim]○[/] ",
                }.get(priority or "medium", "")
                task_line = (
                    f"  {marker} [{colour}]{prefix:<6} {status_label:<8}[/{colour}] "
                    f"{priority_indicator}[dim]{title}[/dim]"
                )
                lines.append(task_line)
        else:
            lines += [
                "",
                "  [dim]Task list will appear when execution starts.[/dim]",
            ]

        lines += [
            "",
            "[dim]─────────────────────────────[/dim]",
            "",
            _footer_tabs_text(),
            "[dim]Keys:  Esc = pause menu  ·  Ctrl+C = stop[/dim]",
        ]
        return "\n".join(lines)

    def _render_settings(self) -> str:
        lines = [
            "[#7cc800][bold]Execution Settings[/bold][/#7cc800]",
            "",
        ]
        if not self._settings:
            lines.append("  [dim]Settings will appear when execution starts.[/dim]")
        else:
            for key, value in self._settings.items():
                lines.append(f"  {escape(key):<24} [dim]{escape(value or '—')}[/dim]")
        lines += [
            "",
            "[dim]─────────────────────────────[/dim]",
            "",
            _footer_tabs_text(),
            "[dim]Keys:  Esc = pause menu  ·  Ctrl+C = stop[/dim]",
        ]
        return "\n".join(lines)

    def _render_costs(self) -> str:
        """Render the Costs tab content with live session cost data.

        Returns:
            Markup string for the Costs tab Static widget.
        """
        lines = [
            "[#7cc800][bold]Session Costs[/bold][/#7cc800]",
            "",
        ]

        session_cost = self._costs.get("session_cost_usd", 0.0)
        last_task_cost = self._costs.get("last_task_cost_usd", 0.0)
        session_tokens = self._costs.get("session_tokens", 0)
        model_costs: dict[str, float] = self._costs.get("model_costs", {})  # type: ignore[assignment]

        if not self._costs:
            lines.append("  [dim]Cost data will appear after the first task completes.[/dim]")
            lines.append("  [dim]Requires models with known pricing (Claude, GPT-4, Gemini).[/dim]")
        else:
            if session_tokens and isinstance(session_tokens, (int, float)):
                lines.append(f"  Session tokens    [dim]{_fmt_tokens(int(session_tokens))}[/dim]")
            if isinstance(session_cost, float) and session_cost > 0:
                lines.append(f"  Session cost      [bold]${session_cost:.4f}[/bold]")
            elif session_tokens and not (isinstance(session_cost, float) and session_cost > 0):
                lines.append("  Session cost      [dim]—  (model not in pricing table)[/dim]")
            if isinstance(last_task_cost, float) and last_task_cost > 0:
                lines.append(f"  Last task cost    [dim]${last_task_cost:.4f}[/dim]")

            if model_costs:
                lines += ["", "  [dim]Per-model breakdown:[/dim]"]
                for model, cost in sorted(model_costs.items(), key=lambda x: -x[1]):
                    model_short = model.split("/")[-1] if "/" in model else model
                    if isinstance(cost, float) and cost > 0:
                        lines.append(f"    [dim]{model_short:<32}[/dim]  ${cost:.4f}")
                    else:
                        lines.append(f"    [dim]{model_short}[/dim]")

            # Budget section — only render when budget keys are present
            budget_limit = self._costs.get("budget_per_run")
            if budget_limit is not None:
                budget_used = self._costs.get("budget_per_run_used", 0)
                budget_remaining = self._costs.get("budget_per_run_remaining", 0)
                budget_pct = self._costs.get("budget_per_run_pct", 0)
                # Ensure numeric types — costs dict is dict[str, object]
                budget_limit_n = int(cast("int", budget_limit))
                budget_used_n = int(cast("int", budget_used)) if budget_used is not None else 0
                budget_remaining_n = (
                    int(cast("int", budget_remaining)) if budget_remaining is not None else 0
                )
                budget_pct_n = float(cast("float", budget_pct)) if budget_pct is not None else 0.0
                # Color based on remaining percentage
                remaining_pct = 100 - budget_pct_n
                if remaining_pct > 50:
                    budget_color = "green"
                elif remaining_pct >= 20:
                    budget_color = "yellow"
                else:
                    budget_color = "red"
                # Visual progress bar
                bar_width = 20
                filled = int(bar_width * budget_pct_n / 100)
                bar = "█" * filled + "░" * (bar_width - filled)
                lines += [
                    "",
                    f"  [{budget_color}][bold]Token Budget[/bold][/{budget_color}]",
                    f"    Limit       {_fmt_tokens(budget_limit_n)} tokens",
                    f"    Used        {_fmt_tokens(budget_used_n)} tokens",
                    f"    [{budget_color}]{bar}[/{budget_color}]  {budget_pct_n:.0f}%",
                    f"    Remaining   {_fmt_tokens(budget_remaining_n)} tokens",
                ]

        lines += [
            "",
            "  [dim]Prices are estimates based on public list rates.[/dim]",
            "  [dim]Run  architect token-report  for historical totals.[/dim]",
            "",
            "[dim]─────────────────────────────[/dim]",
            "",
            _footer_tabs_text(),
            "[dim]Keys:  Esc = pause menu  ·  Ctrl+C = stop[/dim]",
        ]
        return "\n".join(lines)
