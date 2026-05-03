"""Dashboard renderer for The Architect's tmux right pane.

This module is invoked as a subprocess in the right pane of the tmux
split.  It reads the monitor state file every 2 seconds and renders a
live dashboard to the terminal.

Usage (invoked by the tmux manager):
    python -m the_architect.core.dashboard <project_dir>

The dashboard exits when:
- The state file shows STATUS: DONE, FAILED, or KILLED
- The tmux session is destroyed (stdout closes)
- It receives SIGTERM or SIGINT

The runner continues unaffected if this process crashes — it is purely
a reader of the state file.
"""

from __future__ import annotations

import signal
import sys
import textwrap
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[38;2;124;200;0m"  # The Architect lime #7cc800
_YELLOW = "\033[33m"
_RED = "\033[31m"
_CYAN = "\033[36m"
_CURSOR_HOME = "\033[H"  # Move cursor to top-left without erasing
_ERASE_BELOW = "\033[J"  # Erase from cursor to end of screen
_RIGHT_PAD = "  "


def _wrap_task_row(
    symbol: str,
    tid: str,
    title: str,
    suffix: str,
    width: int,
) -> list[str]:
    """Wrap a dashboard task row to fit the pane width.

    The first line includes the task symbol and id. Continuation lines are
    indented so long titles push later rows down instead of visually spilling
    into them.
    """
    width = max(width - len(_RIGHT_PAD), 12)
    prefix = f"{symbol} {tid} "
    continuation = " " * len(prefix)
    text = f"{title} {suffix}".strip()
    wrapped = textwrap.wrap(
        text,
        width=max(width - len(prefix), 8),
        break_long_words=True,
        break_on_hyphens=False,
    )
    if not wrapped:
        return [prefix.rstrip()]
    return [prefix + wrapped[0], *[continuation + line for line in wrapped[1:]]]


def _bold(s: str) -> str:
    return f"{_BOLD}{s}{_RESET}"


def _green(s: str) -> str:
    return f"{_GREEN}{s}{_RESET}"


def _dim(s: str) -> str:
    return f"{_DIM}{s}{_RESET}"


def _yellow(s: str) -> str:
    return f"{_YELLOW}{s}{_RESET}"


def _red(s: str) -> str:
    return f"{_RED}{s}{_RESET}"


# ---------------------------------------------------------------------------
# Duration formatting
# ---------------------------------------------------------------------------


def _fmt_duration(seconds: float) -> str:
    """Format seconds as HH:MM:SS.

    Args:
        seconds: Elapsed seconds.

    Returns:
        Formatted duration string.
    """
    total = max(0, int(seconds))
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _fmt_tokens(n: int) -> str:
    """Format a token count with comma separators.

    Args:
        n: Token count.

    Returns:
        Formatted string, e.g. "48,230".
    """
    return f"{n:,}"


def _fmt_cooldown_remaining(seconds: int | None) -> str:
    """Format cooldown remaining time.

    Args:
        seconds: Remaining seconds, or None if unknown.

    Returns:
        Human-readable string.
    """
    if seconds is None:
        return "YES"
    if seconds <= 0:
        return "YES — ending soon"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"YES — {h}h {m:02d}m remaining"
    elif m > 0:
        return f"YES — {m}m {s:02d}s remaining"
    else:
        return f"YES — {s}s remaining"


# ---------------------------------------------------------------------------
# Task symbol mapping
# ---------------------------------------------------------------------------


def _task_symbol(status: str, replanned: bool) -> str:
    """Return the display symbol for a task.

    Args:
        status: Task status string ("done", "running", "pending", "failed").
        replanned: Whether the task was replanned.

    Returns:
        Symbol character.
    """
    if status == "done":
        return "✓"
    elif status == "running":
        return "●"
    elif status == "failed":
        return "✗"
    else:
        return "○"


def _task_suffix(status: str, replanned: bool, cooldown_active: bool = False) -> str:
    """Return any suffix annotation for a task.

    Args:
        status: Task status string.
        replanned: Whether the task was replanned.
        cooldown_active: Whether this task is currently in cooldown wait.

    Returns:
        Suffix string (may be empty).
    """
    parts = []
    if replanned:
        parts.append("[R]")
    if cooldown_active:
        parts.append("[C]")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Dashboard renderer
# ---------------------------------------------------------------------------


def render_dashboard(state: dict[str, Any], width: int = 30) -> str:
    """Render the dashboard to a string.

    Args:
        state: The monitor state dictionary.
        width: Terminal width of the pane.

    Returns:
        Multi-line string ready to print.
    """
    lines: list[str] = []

    def add(s: str = "") -> None:
        lines.append(s)

    # ── Header ──────────────────────────────────────────────────────────
    add(_bold(_green("THE ARCHITECT")))
    project_name = state.get("project_name", "unknown")
    add(f"Project: {project_name}")
    add()

    # ── Task list ───────────────────────────────────────────────────────
    add(_bold("TASKS"))
    tasks = state.get("tasks", [])
    current_task_id = state.get("current_task_id")
    cooldown = state.get("cooldown", {})
    cooldown_active = cooldown.get("active", False)
    for task in tasks:
        tid = task.get("id", "")
        title = task.get("title", "")
        status = task.get("status", "pending")
        replanned = task.get("replanned", False)

        # Show [C] only on the currently running task when cooldown is active
        is_current_cooldown = cooldown_active and tid == current_task_id

        symbol = _task_symbol(status, replanned)
        suffix = _task_suffix(status, replanned, cooldown_active=is_current_cooldown)

        row_lines = _wrap_task_row(symbol, tid, title, suffix, width)

        for row in row_lines:
            if status == "done":
                row = _green(row)
            elif status == "running":
                row = _bold(row)
            elif status == "failed":
                row = _red(row)
            else:
                row = _dim(row)

            add(row)

    add()

    # ── Status ──────────────────────────────────────────────────────────
    add(_bold("STATUS"))
    total_tasks = state.get("total_tasks", 0)
    run_status = state.get("status", "RUNNING")
    current_attempt = state.get("current_attempt", 0)
    max_retries = state.get("max_retries", 3)

    # Elapsed time
    run_started_at = state.get("run_started_at", "")
    elapsed_seconds = 0.0
    if run_started_at:
        try:
            started = datetime.fromisoformat(run_started_at)
            now = datetime.now(tz=UTC)
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            elapsed_seconds = (now - started).total_seconds()
        except (ValueError, TypeError):
            elapsed_seconds = 0.0

    task_display = f"{current_task_id} / {total_tasks}" if current_task_id else f"– / {total_tasks}"
    add(f"Task:    {task_display}")

    status_str = run_status
    if run_status == "RUNNING":
        status_str = _green(run_status)
    elif run_status in ("DONE",):
        status_str = _green(run_status)
    elif run_status in ("FAILED", "KILLED"):
        status_str = _red(run_status)
    elif run_status in ("COOLDOWN WAIT", "STOPPING"):
        status_str = _yellow(run_status)

    add(f"Status:  {status_str}")
    add(f"Time:    {_fmt_duration(elapsed_seconds)}")
    add(f"Attempt: {current_attempt} / {max_retries}")
    add()

    # ── Circuit breaker ─────────────────────────────────────────────────
    add(_bold("CIRCUIT BREAKER"))
    cb = state.get("circuit_breaker", {})
    cb_state = cb.get("state", "CLOSED")
    no_prog = cb.get("no_progress_count", 0)
    same_err = cb.get("same_error_count", 0)
    thresholds = cb.get("thresholds", {})
    no_prog_thresh = thresholds.get("no_progress", 3)
    same_err_thresh = thresholds.get("same_error", 3)

    cb_state_str = cb_state
    if cb_state == "CLOSED":
        cb_state_str = _green(cb_state)
    elif cb_state == "OPEN":
        cb_state_str = _red(cb_state)
    elif cb_state == "HALF_OPEN":
        cb_state_str = _yellow(cb_state)

    add(f"State:   {cb_state_str}")
    add(f"No-prog: {no_prog} / {no_prog_thresh}")
    add(f"Errors:  {same_err} / {same_err_thresh}")
    add()

    # ── Cooldown ────────────────────────────────────────────────────────
    add(_bold("COOLDOWN"))
    cooldown = state.get("cooldown", {})
    cooldown_active = cooldown.get("active", False)
    cooldown_remaining = cooldown.get("remaining_seconds")
    cooldown_wait_count = cooldown.get("wait_count", 0)

    if cooldown_active:
        active_str = _yellow(_fmt_cooldown_remaining(cooldown_remaining))
    else:
        active_str = _dim("NO")

    add(f"Active:  {active_str}")
    add(f"Waits:   {cooldown_wait_count}")
    add()

    # ── Model ────────────────────────────────────────────────────────────
    add(_bold("MODEL"))
    model_info = state.get("model", {})
    model_current = model_info.get("current", "")
    free_mode = model_info.get("free_mode", False)
    free_remaining = model_info.get("free_remaining", 0)
    rotation_count = model_info.get("rotation_count", 0)

    # Truncate model name to fit
    max_model_len = width - len(_RIGHT_PAD) - 10
    if model_current and len(model_current) > max_model_len:
        # Show just the last part (provider/model-name)
        parts = model_current.split("/")
        model_display = parts[-1] if parts else model_current
        if len(model_display) > max_model_len:
            model_display = model_display[: max_model_len - 1] + "…"
    else:
        model_display = model_current or _dim("(default)")

    add(f"Current: {model_display}")

    if free_mode:
        add(f"Free:    {free_remaining} remaining")
    else:
        add(f"Free:    {_dim('free mode off')}")

    add(f"Rotation: {rotation_count} used")
    add()

    # ── Tokens ──────────────────────────────────────────────────────────
    add(_bold("TOKENS"))
    tokens = state.get("tokens", {})
    session_total = tokens.get("session_total", 0)
    last_attempt = tokens.get("last_attempt", 0)

    add(f"Session: {_fmt_tokens(session_total)}")
    add(f"Last:    {_fmt_tokens(last_attempt)}")
    add()

    # ── Bottom bar ───────────────────────────────────────────────────────
    add(_dim("Ctrl+B D  detach (run keeps going)"))
    add(_dim("Ctrl+C    stop gracefully"))

    return "\n".join(lines)


def render_waiting() -> str:
    """Render the waiting screen when no state file exists yet.

    Returns:
        Multi-line string ready to print.
    """
    lines = [
        _bold(_green("THE ARCHITECT")),
        "",
        _dim("Waiting for run to start..."),
        "",
        _dim("The dashboard will update"),
        _dim("when the runner begins."),
        "",
        _dim("Ctrl+B D  detach (run keeps going)"),
        _dim("Ctrl+C    cancel"),
    ]
    return "\n".join(lines)


def render_planning(state: dict[str, Any], width: int = 30) -> str:
    """Render the planning screen while the architect agent is running.

    Shown when the runner is in planning mode — collecting prompts or
    running the architect agent to generate task files.  No task list
    is shown yet because tasks don't exist.

    Args:
        state: The monitor state dictionary (status == PLANNING).
        width: Terminal width of the pane.

    Returns:
        Multi-line string ready to print.
    """
    lines: list[str] = []

    def add(s: str = "") -> None:
        lines.append(s)

    add(_bold(_green("THE ARCHITECT")))
    project_name = state.get("project_name", "unknown")
    add(f"Project: {project_name}")
    add()
    add(_bold("PLANNING"))
    add()

    goal = state.get("goal", "")
    if goal:
        # Wrap goal to fit the pane width
        max_w = max(width - len(_RIGHT_PAD) - 2, 10)
        words = goal.split()
        current_line: list[str] = []
        current_len = 0
        for word in words:
            if current_len + len(word) + (1 if current_line else 0) > max_w:
                add(_dim(" ".join(current_line)))
                current_line = [word]
                current_len = len(word)
            else:
                current_line.append(word)
                current_len += len(word) + (1 if len(current_line) > 1 else 0)
        if current_line:
            add(_dim(" ".join(current_line)))
        add()

    add(_dim("The architect agent is"))
    add(_dim("generating task files..."))
    add()
    add(_dim("Answer the prompts in"))
    add(_dim("the left pane."))
    add()
    add(_dim("Ctrl+B D  detach (run keeps going)"))
    add(_dim("Ctrl+C    cancel planning"))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

_running = True


def _handle_signal(signum: int, frame: object) -> None:
    """Handle SIGTERM/SIGINT by setting the stop flag.

    Args:
        signum: Signal number.
        frame: Current stack frame.
    """
    global _running
    _running = False


def _get_pane_width() -> int:
    """Return the actual width of the current tmux pane (or terminal).

    Tries three sources in order:
    1. ``tmux display-message -p '#{pane_width}'`` — exact pane width even
       when stdout is not a TTY (dashboard subprocess has no controlling TTY).
    2. ``shutil.get_terminal_size()`` — works when stdout is a real TTY.
    3. Hard fallback: 66 columns (30% of a 220-column session).

    Returns:
        Column count as an integer, minimum 20.
    """
    import shutil as _shutil
    import subprocess as _sp

    # 1. Ask tmux directly — most reliable inside a pane
    try:
        result = _sp.run(
            ["tmux", "display-message", "-p", "#{pane_width}"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            w = int(result.stdout.strip())
            if w > 0:
                return max(w, 20)
    except Exception:
        pass

    # 2. shutil fallback (works when stdout is a TTY)
    try:
        w = _shutil.get_terminal_size((66, 50)).columns
        if w > 0:
            return max(w, 20)
    except Exception:
        pass

    # 3. Hard fallback
    return 66


def run_dashboard(project_dir: Path) -> None:
    """Run the dashboard loop until the run completes or we are stopped.

    Reads the state file every 2 seconds and re-renders the dashboard.
    Exits cleanly when the run status is DONE, FAILED, or KILLED.

    Args:
        project_dir: The project root directory.
    """
    from the_architect.core.monitor_state import (
        RUN_STATUS_DONE,
        RUN_STATUS_FAILED,
        RUN_STATUS_KILLED,
        RUN_STATUS_PLANNING,
        read_monitor_state,
    )

    global _running

    # SIGTERM is not available on native Windows (raises ValueError).
    # SIGINT (Ctrl+C) is safe on all platforms.
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    terminal_states = {RUN_STATUS_DONE, RUN_STATUS_FAILED, RUN_STATUS_KILLED}

    while _running:
        # Get the actual pane width on each cycle so we adapt to resizes.
        # Priority:
        #   1. tmux display-message -p '#{pane_width}' — exact pane width
        #      (the dashboard runs in a 30% split so this is ~66 cols, not
        #      the full session width of 220)
        #   2. shutil.get_terminal_size() — works when stdout is a real TTY
        #   3. Hard fallback: 66 columns (30% of a typical 220-col session)
        width = _get_pane_width()
        try:
            state = read_monitor_state(project_dir)

            # Re-render in place: move cursor to top-left WITHOUT clearing,
            # write the new frame, then erase any leftover lines from a
            # previous, longer render.  This eliminates the whole-screen
            # flash caused by "\033[2J" while still keeping the output clean.
            sys.stdout.write(_CURSOR_HOME)
            if state is None:
                content = render_waiting()
            elif state.get("status") == RUN_STATUS_PLANNING:
                content = render_planning(state, width=width)
            else:
                content = render_dashboard(state, width=width)
            sys.stdout.write(content)
            sys.stdout.write(_ERASE_BELOW)
            sys.stdout.write("\n")
            sys.stdout.flush()

            # Exit if run is complete
            if state is not None and state.get("status") in terminal_states:
                time.sleep(2)  # Brief pause so user sees the final state
                break

        except BrokenPipeError:
            # The tmux pane was closed
            break
        except Exception:
            # Any other error — keep trying
            pass

        time.sleep(2)


# ---------------------------------------------------------------------------
# Entry point (invoked as python -m the_architect.core.dashboard <project_dir>)
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point when invoked as a module.

    Reads the project directory from sys.argv[1].
    """
    if len(sys.argv) < 2:
        sys.stderr.write("Usage: python -m the_architect.core.dashboard <project_dir>\n")
        sys.exit(1)

    project_dir = Path(sys.argv[1]).resolve()
    if not project_dir.is_dir():
        sys.stderr.write(f"Project directory does not exist: {project_dir}\n")
        sys.exit(1)

    run_dashboard(project_dir)


if __name__ == "__main__":
    main()
