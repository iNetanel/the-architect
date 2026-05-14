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

import os
from datetime import datetime

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Header, RichLog, Static, TabbedContent, TabPane

from the_architect.tui.widgets import MatrixRain, next_matrix_frame


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


def _idle_footer_text() -> str:
    """Initial footer text shown before any run activity.

    ESC opens the pause menu (Continue / Detach / Exit); Ctrl+C is a
    direct hard stop. When running inside tmux we also advertise the
    tmux detach shortcut so users know the "step away and come back
    later" path is available without even opening the menu.
    """
    base = "(idle)  [l]ive / [p]rogress / [d]iagnostics / settin[g]s  ·  Esc=pause  ·  Ctrl+C=stop"
    if os.environ.get("TMUX"):
        base += "  ·  Ctrl+B D detaches"
    return base


class ExecutionScreen(Screen[None]):
    """Main execution screen with animated header, tabbed viewport, and status footer."""

    # ESC intentionally opens the pause menu instead of quitting —
    # a stray Escape after focusing a field must never drop the
    # backend provider mid-run. Ctrl+C remains the direct-hard-stop
    # path and stays wired at the app level.
    BINDINGS = [
        Binding("l", "switch_tab('tab_live')", "Live", show=False),
        Binding("p", "switch_tab('tab_progress')", "Progress", show=False),
        Binding("d", "switch_tab('tab_diagnostics')", "Diagnostics", show=False),
        Binding("g", "switch_tab('tab_settings')", "Settings", show=False),
        Binding("c", "switch_tab('tab_costs')", "Costs", show=False),
        Binding("o", "switch_tab('tab_live')", "Live", show=False),
        Binding("e", "switch_tab('tab_diagnostics')", "Diagnostics", show=False),
        Binding("s", "switch_tab('tab_progress')", "Progress", show=False),
        Binding("escape", "pause_menu", "Pause menu"),
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
        yield Static(
            _idle_footer_text(),
            id="exec_footer",
        )

    def on_mount(self) -> None:
        # Start the spinner at 10 FPS — same cadence as WaitScreen.
        self.set_interval(0.1, self._tick_spinner)
        # Make every tab body focusable so keyboard scrolling works when a tab
        # has more content than the available terminal height.
        for scroll_id in (
            "#exec_output",
            "#exec_diagnostics",
            "#exec_progress",
            "#exec_settings",
            "#exec_costs",
        ):
            try:
                self.query_one(scroll_id).can_focus = True
            except Exception:
                pass
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

    def _flush_pending(self) -> None:
        """Apply any output/events/footer updates queued before mount."""
        if self._pending_output:
            try:
                log = self.query_one("#exec_output", RichLog)
                if not self._output_received:
                    self._output_received = True
                for line in self._pending_output:
                    log.write(line)
            except Exception:
                pass
            self._pending_output.clear()
        for event, data in self._pending_diagnostics:
            self._write_diagnostic_line(event, data)
        self._pending_diagnostics.clear()
        if self._pending_footer is not None:
            try:
                self.query_one("#exec_footer", Static).update(self._pending_footer)
            except Exception:
                pass
            self._pending_footer = None
        if self._pending_costs is not None:
            self.update_costs(self._pending_costs)
            self._pending_costs = None
        self._refresh_summary_widgets()

    def _write_default_placeholders(self) -> None:
        # Only write the output placeholder when no real provider output
        # has arrived yet.  _flush_pending runs before this callback and
        # sets _output_received=True when it drains buffered lines, so
        # fast providers that send output before the DOM is ready won't
        # have their content overwritten by this placeholder.
        if not self._output_received:
            try:
                self.query_one("#exec_output", RichLog).write("Waiting for provider output…")
            except Exception:
                pass
        try:
            self.query_one("#exec_diagnostics", RichLog).write(
                "No diagnostics yet. Retries, cooldowns, model switches, "
                "and circuit events appear here."
            )
        except Exception:
            pass

    # ── Spinner ────────────────────────────────────────────────────────

    def _tick_spinner(self) -> None:
        """Advance the Matrix-rain frame on the animated title line."""
        self._frame_index += 1
        self._current_frame = next_matrix_frame(self._frame_index)
        try:
            self.query_one("#exec_anim_title", Static).update(self._render_anim_title())
        except Exception:
            pass

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
            self.call_after_refresh(self._focus_active_tab_scroller)
        except Exception:
            pass

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
            }.get(active)
            if target_id:
                self.query_one(target_id).focus()
        except Exception:
            pass

    # ── Renderer hooks ─────────────────────────────────────────────────

    def push_output_line(self, line: str) -> None:
        """Append a provider output line to the Live tab."""
        # Update last-activity timestamp whenever output arrives.
        self._details["last_activity"] = datetime.now().strftime("%H:%M:%S")
        try:
            log = self.query_one("#exec_output", RichLog)
        except Exception:
            self._pending_output.append(line)
            return
        # On the first real output line, clear the placeholder text so it
        # doesn't sit above every provider message in the log.
        if not self._output_received:
            self._output_received = True
            try:
                log.clear()
            except Exception:
                pass
        log.write(line)

    def push_event_line(self, event: str, data: dict[str, object] | None = None) -> None:
        """Append an operational event to the Diagnostics tab."""
        try:
            self.query_one("#exec_diagnostics", RichLog)
        except Exception:
            self._pending_diagnostics.append((event, data))
            return
        self._write_diagnostic_line(event, data)

    def _write_diagnostic_line(self, event: str, data: dict[str, object] | None) -> None:
        try:
            log = self.query_one("#exec_diagnostics", RichLog)
            now = datetime.now().strftime("%H:%M:%S")
            payload = " ".join(f"{k}=[cyan]{v}[/cyan]" for k, v in (data or {}).items())
            log.write(f"[dim]{now}[/dim]  [#7cc800]{event}[/#7cc800]  {payload}")
        except Exception:
            pass

    def update_progress_tasks(self, tasks: list[dict[str, str]]) -> None:
        """Replace the Progress tab's task overview and refresh it."""
        self._progress_tasks = tasks
        self._refresh_summary_widgets()

    def update_footer(self, text: str) -> None:
        """Set the one-line status footer under the tabs."""
        try:
            footer = self.query_one("#exec_footer", Static)
        except Exception:
            self._pending_footer = text
            return
        footer.update(text)

    def update_details(self, **fields: str) -> None:
        """Merge run metadata into the Progress tab and refresh the title."""
        self._details.update({k: v for k, v in fields.items() if v is not None})
        # Also update the task badge in the header row.
        try:
            badge = self.query_one("#exec_task_badge", Static)
            task = self._details.get("task", "")
            if task and task != "(waiting)":
                badge.update(f"[dim]{task}[/dim]")
        except Exception:
            pass
        self._refresh_summary_widgets()
        # Refresh the animated title so it shows the new task label immediately.
        try:
            self.query_one("#exec_anim_title", Static).update(self._render_anim_title())
        except Exception:
            pass

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
        except Exception:
            self._pending_costs = costs

    def _refresh_summary_widgets(self) -> None:
        """Refresh cached progress/settings/costs state after mount or updates."""
        try:
            self.query_one("#exec_progress_text", Static).update(self._render_progress())
        except Exception:
            pass
        try:
            self.query_one("#exec_settings_text", Static).update(self._render_settings())
        except Exception:
            pass
        try:
            self.query_one("#exec_costs_text", Static).update(self._render_costs())
        except Exception:
            pass

    # ── Actions ────────────────────────────────────────────────────────

    def action_pause_menu(self) -> None:
        """Show the pause menu (Continue / Detach / Exit).

        Delegates to the hosting :class:`ArchitectApp` so the overlay
        is a proper app-level modal and only one instance can ever be
        on screen at a time.
        """
        try:
            self.app.show_pause_menu()  # type: ignore[attr-defined]
        except Exception:
            # If the app doesn't expose the hook (tests with a bare
            # harness), ignore — ESC simply does nothing.
            pass

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
                colour = {
                    "running": "yellow",
                    "done": "#7cc800",
                    "failed": "red",
                    "skipped": "dim",
                    "pending": "dim",
                }.get(status, "dim")
                marker = ">" if status == "running" else " "
                status_label = status.upper()
                task_line = (
                    f"  {marker} [{colour}]{prefix:<4} {status_label:<8}[/{colour}] "
                    f"[dim]{title}[/dim]"
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
            "[dim]Tabs:  [l] Live  [p] Progress  [d] Diagnostics  [g] Settings  [c] Costs[/dim]",
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
            "[dim]Tabs:  [l] Live  [p] Progress  [d] Diagnostics  [g] Settings  [c] Costs[/dim]",
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

        lines += [
            "",
            "  [dim]Prices are estimates based on public list rates.[/dim]",
            "  [dim]Run  architect token-report  for historical totals.[/dim]",
            "",
            "[dim]─────────────────────────────[/dim]",
            "",
            "[dim]Tabs:  [l] Live  [p] Progress  [d] Diagnostics  [g] Settings  [c] Costs[/dim]",
            "[dim]Keys:  Esc = pause menu  ·  Ctrl+C = stop[/dim]",
        ]
        return "\n".join(lines)
