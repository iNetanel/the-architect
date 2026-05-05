"""Textual Execution screen with tabbed output / events / status.

This screen hosts the live provider stream, circuit/retry/reassessment
events, and per-task metadata while a run is active. The business logic
continues to run in :mod:`the_architect.core.runner`; this screen only
renders what the renderer pushes into it.

Layout mirrors :class:`~the_architect.tui.screens.wait.WaitScreen`:

- An animated Matrix-rain title at the top (same spinner, same brand green)
- Three tabs below it:
  - **Live Output** — raw provider stream, token by token
  - **Events** — timestamped circuit / retry / model-switch events
  - **Status** — structured per-task metadata (task, model, attempt, tokens)
"""

from __future__ import annotations

import os
from datetime import datetime

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, RichLog, Static, TabbedContent, TabPane

from the_architect.tui.widgets import next_matrix_frame


def _idle_footer_text() -> str:
    """Initial footer text shown before any run activity.

    ESC opens the pause menu (Continue / Detach / Exit); Ctrl+C is a
    direct hard stop. When running inside tmux we also advertise the
    tmux detach shortcut so users know the "step away and come back
    later" path is available without even opening the menu.
    """
    base = "(idle)  [o]utput / [e]vents / [s]tatus  ·  Esc=pause menu  ·  Ctrl+C=stop"
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

    #exec_output, #exec_events {
        height: 1fr;
        border: round $panel;
        padding: 0 1;
    }

    #exec_status {
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
    """

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
        self._frame_index = 0
        self._current_frame = next_matrix_frame(self._frame_index)
        # Pending updates buffered before first mount so callers who
        # push content while the screen is still being composed don't
        # silently lose their messages. Flushed from on_mount.
        self._pending_output: list[str] = []
        self._pending_events: list[tuple[str, dict[str, object] | None]] = []
        self._pending_footer: str | None = None
        # Track whether any real provider output has been received so the
        # placeholder can be cleared on the first write.
        self._output_received: bool = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="exec_header_row"):
            yield Static(self._render_anim_title(), id="exec_anim_title")
            yield Static("", id="exec_task_badge")
        with TabbedContent(id="exec_tabs"):
            with TabPane("Live Output", id="tab_output"):
                yield RichLog(
                    id="exec_output",
                    highlight=False,
                    markup=False,
                    wrap=True,
                    auto_scroll=True,
                )
            with TabPane("Events", id="tab_events"):
                yield RichLog(
                    id="exec_events",
                    highlight=False,
                    markup=True,
                    wrap=True,
                    auto_scroll=True,
                )
            with TabPane("Status", id="tab_details"):
                with Vertical(id="exec_status"):
                    yield Static(self._render_details(), id="exec_details_text")
        yield Static(
            _idle_footer_text(),
            id="exec_footer",
        )
        yield Footer()

    def on_mount(self) -> None:
        # Start the spinner at 10 FPS — same cadence as WaitScreen.
        self.set_interval(0.1, self._tick_spinner)
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
        for event, data in self._pending_events:
            self._write_event_line(event, data)
        self._pending_events.clear()
        if self._pending_footer is not None:
            try:
                self.query_one("#exec_footer", Static).update(self._pending_footer)
            except Exception:
                pass
            self._pending_footer = None

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
            self.query_one("#exec_events", RichLog).write(
                "No events yet. Circuit / retry / model-switch events appear here."
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

    # ── Renderer hooks ─────────────────────────────────────────────────

    def push_output_line(self, line: str) -> None:
        """Append a provider output line to the Live Output tab."""
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
        """Append an execution event to the Events tab."""
        try:
            self.query_one("#exec_events", RichLog)
        except Exception:
            self._pending_events.append((event, data))
            return
        self._write_event_line(event, data)

    def _write_event_line(self, event: str, data: dict[str, object] | None) -> None:
        try:
            log = self.query_one("#exec_events", RichLog)
            now = datetime.now().strftime("%H:%M:%S")
            payload = " ".join(f"{k}=[cyan]{v}[/cyan]" for k, v in (data or {}).items())
            log.write(f"[dim]{now}[/dim]  [#7cc800]{event}[/#7cc800]  {payload}")
        except Exception:
            pass

    def update_footer(self, text: str) -> None:
        """Set the one-line status footer under the tabs."""
        try:
            footer = self.query_one("#exec_footer", Static)
        except Exception:
            self._pending_footer = text
            return
        footer.update(text)

    def update_details(self, **fields: str) -> None:
        """Merge fields into the Status tab and refresh the animated title."""
        self._details.update({k: v for k, v in fields.items() if v is not None})
        # Also update the task badge in the header row.
        try:
            badge = self.query_one("#exec_task_badge", Static)
            task = self._details.get("task", "")
            if task and task != "(waiting)":
                badge.update(f"[dim]{task}[/dim]")
        except Exception:
            pass
        try:
            static = self.query_one("#exec_details_text", Static)
            static.update(self._render_details())
        except Exception:
            pass
        # Refresh the animated title so it shows the new task label immediately.
        try:
            self.query_one("#exec_anim_title", Static).update(self._render_anim_title())
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

    def _render_details(self) -> str:
        d = self._details
        task = d.get("task", "(waiting)")
        phase = d.get("phase", "idle")
        attempt = d.get("attempt", "—")
        model = d.get("model", "—")
        tokens = d.get("tokens", "—")
        last_activity = d.get("last_activity", "—")
        current_op = d.get("current_op", "")

        lines = [
            "[$accent][bold]Agent Status[/bold][/$accent]",
            "",
        ]

        # Phase badge
        phase_colour = {
            "executing": "$accent",
            "starting": "yellow",
            "cooldown": "yellow",
            "replanning": "yellow",
            "replan_done": "$accent",
            "resumed": "$accent",
            "idle": "dim",
        }.get(phase, "dim")
        lines.append(f"  Phase        [{phase_colour}]{phase}[/{phase_colour}]")

        lines.append(f"  Task         [bold]{task}[/bold]")
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

        lines += [
            "",
            "[dim]─────────────────────────────[/dim]",
            "",
            "[dim]Tabs:  [o] Live Output  [e] Events  [s] Status[/dim]",
            "[dim]Keys:  Esc = pause menu  ·  Ctrl+C = stop[/dim]",
        ]
        return "\n".join(lines)
