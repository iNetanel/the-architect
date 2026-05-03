"""Textual Execution screen with tabbed output / events / details.

This screen hosts the live provider stream, circuit/retry/reassessment
events, and per-task metadata while a run is active. The business logic
continues to run in :mod:`the_architect.core.runner`; this screen only
renders what the renderer pushes into it.
"""

from __future__ import annotations

import os
from datetime import datetime

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, RichLog, Static, TabbedContent, TabPane


def _idle_footer_text() -> str:
    """Initial footer text shown before any run activity.

    ESC opens the pause menu (Continue / Detach / Exit); Ctrl+C is a
    direct hard stop. When running inside tmux we also advertise the
    tmux detach shortcut so users know the "step away and come back
    later" path is available without even opening the menu.
    """
    base = "(idle)  [o]utput / [e]vents / [d]etails  ·  Esc=pause menu  ·  Ctrl+C=stop"
    if os.environ.get("TMUX"):
        base += "  ·  Ctrl+B D detaches"
    return base


class ExecutionScreen(Screen[None]):
    """Main execution screen with a tabbed viewport and a status footer."""

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

    #exec_tabs {
        height: 1fr;
    }

    #exec_output, #exec_events {
        height: 1fr;
        border: round $panel;
        padding: 0 1;
    }

    #exec_details {
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
        }
        # Pending updates buffered before first mount so callers who
        # push content while the screen is still being composed don't
        # silently lose their messages. Flushed from on_mount.
        self._pending_output: list[str] = []
        self._pending_events: list[tuple[str, dict[str, object] | None]] = []
        self._pending_footer: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="exec_tabs"):
            with TabPane("Output", id="tab_output"):
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
            with TabPane("Details", id="tab_details"):
                with Vertical(id="exec_details"):
                    yield Static(self._render_details(), id="exec_details_text")
        yield Static(
            _idle_footer_text(),
            id="exec_footer",
        )
        yield Footer()

    def on_mount(self) -> None:
        # Defer writes until after the initial refresh so all tabbed
        # children (especially non-active tab contents) are mounted.
        self.call_after_refresh(self._write_default_placeholders)
        self.call_after_refresh(self._flush_pending)

    def _flush_pending(self) -> None:
        """Apply any output/events/footer updates queued before mount."""
        for line in self._pending_output:
            try:
                self.query_one("#exec_output", RichLog).write(line)
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
        try:
            self.query_one("#exec_output", RichLog).write("Waiting for run to start…")
        except Exception:
            pass
        try:
            self.query_one("#exec_events", RichLog).write(
                "No events yet. Events appear here as the run progresses."
            )
        except Exception:
            pass

    # ── Renderer hooks ─────────────────────────────────────────────────

    def push_output_line(self, line: str) -> None:
        """Append a provider output line to the Output tab."""
        try:
            log = self.query_one("#exec_output", RichLog)
        except Exception:
            self._pending_output.append(line)
            return
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
            payload = " ".join(f"{k}={v}" for k, v in (data or {}).items())
            log.write(f"[dim]{now}[/dim]  [bold]{event}[/bold]  {payload}")
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
        """Merge fields into the Details tab."""
        self._details.update({k: v for k, v in fields.items() if v is not None})
        try:
            static = self.query_one("#exec_details_text", Static)
            static.update(self._render_details())
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
        lines = [
            "[bold]Current task[/bold]",
            f"  task:    {d.get('task', '')}",
            f"  phase:   {d.get('phase', '')}",
            f"  attempt: {d.get('attempt', '')}",
            f"  model:   {d.get('model', '')}",
            f"  tokens:  {d.get('tokens', '')}",
        ]
        return "\n".join(lines)
