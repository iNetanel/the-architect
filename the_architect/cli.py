"""The Architect CLI — pure terminal interface.

Planning uses interactive prompts (questionary arrow-key menus).
Execution streams opencode output raw to the terminal — no TUI overlay.
Results are written to SUCCESS.md and printed as a terminal summary.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prompt_toolkit.styles import Style as PromptStyle

from datetime import UTC

import click
from loguru import logger
from prompt_toolkit.layout.dimension import D

from the_architect import __version__
from the_architect.config import ArchitectConfig, load_config, write_config
from the_architect.core.monitor_state import MonitorStateWriter
from the_architect.core.planner import (
    PlanningFailedError,
    PlanningRequest,
    check_pending_tasks,
    run_planner,
)
from the_architect.core.progress import (
    reconcile_task_status,
    replace_task_status,
    task_is_done,
    task_is_resolved,
    task_status,
)
from the_architect.core.provider import (
    ArchitectProvider,
    ProviderNotFoundError,
    detect_available_providers,
    detect_provider,
)
from the_architect.core.retrospective import (
    RetrospectiveRequest,
    run_retrospective,
    run_task_reassessment,
)
from the_architect.core.runner import TaskResult, TokenUsage, run_all, run_task, setup_logging
from the_architect.core.success import RetrospectiveRound, print_success_summary, write_success_md
from the_architect.core.tasks import Task, TaskPlan, TaskScope, TaskStatus, discover_tasks
from the_architect.core.tmux import PaddedConsole

console = PaddedConsole()


# ---------------------------------------------------------------------------
# Alternate screen buffer — clean terminal like opencode, vim, htop
# ---------------------------------------------------------------------------


@contextmanager
def alternate_screen() -> Generator[None, None, None]:
    """Enter the terminal alternate screen buffer, restoring the original on exit.

    When the architect starts, it switches to a fresh, empty alternate screen
    — just like ``vim``, ``htop``, ``lazygit``, and ``opencode`` do.  When it
    exits (normally, on error, or Ctrl+C), the original terminal content is
    restored exactly as it was.

    This is a no-op when stdout is not a real TTY (e.g. piped output, Click
    test runner) so that automated tools and tests are not affected.

    Uses ANSI escape sequence ``CSI ? 1049 h`` (enter alternate screen +
    save cursor) and ``CSI ? 1049 l`` (exit alternate screen + restore cursor).
    """
    if not sys.stdout.isatty():
        yield
        return

    # Enter alternate screen buffer + save cursor
    sys.stdout.write("\033[?1049h")
    sys.stdout.flush()

    try:
        yield
    finally:
        # Exit alternate screen buffer + restore cursor
        sys.stdout.write("\033[?1049l")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _setup_loguru() -> None:
    """Configure loguru — INFO to stderr, no timestamps in normal mode."""
    logger.remove()
    logger.add(
        sys.stderr,
        format="<level>{level: <8}</level> | {message}",
        level="WARNING",
        colorize=sys.stderr.isatty() and not bool(os.environ.get("NO_COLOR")),
    )


# ---------------------------------------------------------------------------
# Task helpers
# ---------------------------------------------------------------------------


def _provider_has_any_models() -> bool:
    """Check whether the active provider can list at least one model.

    Uses auto-detection to find the installed provider.

    Returns:
        True if the provider has at least one model available.
    """
    try:
        from the_architect.core.provider import detect_provider

        return detect_provider("auto").has_any_models()
    except Exception:
        return False


# Backward-compatible alias — tests import this name
_opencode_has_any_models = _provider_has_any_models


def _filter_and_set_status(tasks: list[Task], progress_file: Path) -> list[Task]:
    """Return tasks with their status mirrored from PROGRESS.md (read-only, no mutation).

    Mapping:
      - ``Done``   → :class:`TaskStatus.DONE`
      - ``Failed`` → :class:`TaskStatus.FAILED`
      - ``Blocked`` and ``Pending`` (and anything else) → the task's original
        status, which is :class:`TaskStatus.PENDING` coming from
        :func:`discover_tasks`.

    Callers use the resulting statuses to drive the resume screen, the
    status tables, and skip decisions.  Anything that should be skipped
    silently by the main execution loop is now also filtered by
    :func:`the_architect.core.progress.task_is_resolved` inside
    ``_run_all_inner`` — this helper exists mainly for UI layers.
    """
    result: list[Task] = []
    for task in tasks:
        status = task_status(progress_file, task.prefix)
        if status == "Done":
            new_status = TaskStatus.DONE
        elif status == "Failed":
            new_status = TaskStatus.FAILED
        else:
            # Pending, Blocked, missing row — carry the task's existing
            # status (PENDING by default) so the UI doesn't misrepresent it.
            result.append(task)
            continue
        result.append(
            Task(
                name=task.name,
                prefix=task.prefix,
                number=task.number,
                path=task.path,
                title=task.title,
                status=new_status,
            )
        )
    return result


def _task_is_terminal(task: Task) -> bool:
    """Return True if a task's in-memory status is terminal (no more work).

    Terminal statuses are ``DONE`` and ``FAILED``.  ``BLOCKED`` is not yet
    represented in :class:`TaskStatus` — blocked PROGRESS.md rows round-trip
    through :func:`_filter_and_set_status` as ``PENDING``, which is
    deliberate: a blocked task will be retried on the next loop when the
    resource constraint (rate-limit, budget) lifts, so treating it as
    non-terminal at the CLI layer is correct.

    Prefer this helper over raw ``t.status == TaskStatus.DONE`` wherever the
    intent is "there is no outstanding work on this task" — a ``Failed``
    task is resolved just as firmly as a ``Done`` one, and silently lumping
    it into "pending" causes the retrospective-loop regression.
    """
    return task.status in (TaskStatus.DONE, TaskStatus.FAILED)


def _task_needs_work(task: Task) -> bool:
    """Inverse of :func:`_task_is_terminal` — ``True`` when the task is pending.

    Provided as a readable complement at call sites that filter by "still
    needs to run".  Equivalent to ``not _task_is_terminal(task)``.
    """
    return not _task_is_terminal(task)


# ---------------------------------------------------------------------------
# The Architect brand colour constant
# ---------------------------------------------------------------------------

ARCHITECT_GREEN = "#7cc800"

# Shared questionary style — The Architect green for user input and selections
_QUESTIONARY_STYLE = None
_PROMPT_LEFT_PAD = 2
_PROMPT_RIGHT_PAD = 2


def _padded_window(content: Any) -> Any:
    """Wrap prompt_toolkit content in a horizontally padded layout."""
    from prompt_toolkit.layout import Layout, VSplit, Window

    return Layout(
        VSplit(
            [
                Window(width=D.exact(_PROMPT_LEFT_PAD), dont_extend_width=True),
                Window(content=content),
                Window(width=D.exact(_PROMPT_RIGHT_PAD), dont_extend_width=True),
            ]
        )
    )


def _prompt_text_input(
    title: str,
    instruction: str,
    default: str = "",
) -> str | None:
    """Prompt for free text with right-side padding in the main pane."""
    from prompt_toolkit import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
    from prompt_toolkit.layout.controls import BufferControl
    from prompt_toolkit.styles import Style as PtStyle

    cancelled = False
    buffer = Buffer(multiline=True)
    if default:
        buffer.text = default

    kb = KeyBindings()

    @kb.add("c-c")
    def _(event: KeyPressEvent) -> None:
        nonlocal cancelled
        cancelled = True
        event.app.exit()

    @kb.add("c-m")
    def _(event: KeyPressEvent) -> None:
        event.app.exit()

    console.print(
        f"[bold {ARCHITECT_GREEN}]{title}[/bold {ARCHITECT_GREEN}]  [grey62]{instruction}[/grey62]"
    )
    from prompt_toolkit.layout import Layout, VSplit, Window

    layout = Layout(
        VSplit(
            [
                Window(content=BufferControl(buffer=buffer), wrap_lines=True),
                Window(width=D.exact(_PROMPT_RIGHT_PAD), dont_extend_width=True),
            ]
        )
    )

    pt_style = PtStyle(
        [
            ("", f"fg:{ARCHITECT_GREEN} bold"),
            ("header", f"fg:{ARCHITECT_GREEN} bold"),
            ("instruction", "fg:#666666"),
        ]
    )

    app: Application[object] = Application(
        layout=layout,
        key_bindings=kb,
        style=pt_style,
        full_screen=False,
    )
    app.run()

    if cancelled:
        return None
    return buffer.text


def _questionary_style() -> PromptStyle:
    """Return the shared questionary Style with The Architect green theming.

    Lazy-imported so we don't pay the import cost at module level.

    Visual rules:
    - Cursor row (highlighted): ``›`` arrow + green bold text — NO background.
      ``noinherit`` kills the prompt_toolkit default ``reverse`` (green bg /
      dark text) that would otherwise appear on the focused row.
    - Checkbox checked item (selected): green ``●`` dot, row text normal —
      no background colour on the row itself.
    - Checkbox unchecked item: dim ``○`` dot, row text normal.
    - pointer token: green ``›`` character drawn by questionary before the row.

    Returns:
        A prompt_toolkit Style instance.
    """
    global _QUESTIONARY_STYLE
    if _QUESTIONARY_STYLE is not None:
        return _QUESTIONARY_STYLE

    from prompt_toolkit.styles import Style

    _QUESTIONARY_STYLE = Style(
        [
            ("qmark", f"fg:{ARCHITECT_GREEN} bold"),
            ("question", "bold"),
            ("answer", f"fg:{ARCHITECT_GREEN}"),
            # Cursor row: green arrow pointer drawn via pointer="›".
            # noinherit removes the default `reverse` so there is NO background
            # highlight — only the › arrow + green bold text shows the active row.
            ("highlighted", f"noinherit fg:{ARCHITECT_GREEN} bold"),
            # questionary uses class:selected for the default item in select()
            # AND for checked items in checkbox(). In both cases the item should
            # be green bold with no background — noinherit kills the reverse.
            ("selected", f"noinherit fg:{ARCHITECT_GREEN} bold"),
            # The › pointer character itself is green.
            ("pointer", f"fg:{ARCHITECT_GREEN} bold"),
            # Checkbox indicator tokens: ● green when checked, ○ dim when not.
            ("checkbox-selected", f"fg:{ARCHITECT_GREEN}"),
            ("checkbox", "fg:#666666"),
            ("instruction", "fg:#666666"),
        ]
    )
    return _QUESTIONARY_STYLE


# ---------------------------------------------------------------------------
# Interactive mode selection screen
# ---------------------------------------------------------------------------


def _prompt_provider_selection(available: list[ArchitectProvider]) -> ArchitectProvider:
    """Show an interactive provider selection screen when multiple providers are installed.

    Presented when multiple providers are detected and the
    user has not specified a preference in ``architect.toml``.


    Args:
        available: List of installed providers (at least 2).

    Returns:
        The selected :class:`~the_architect.core.provider.ArchitectProvider`.
    """
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
    from prompt_toolkit.layout import FormattedTextControl
    from prompt_toolkit.styles import Style as PtStyle

    selected_idx = 0
    cancelled = False
    n = len(available)

    # Resolve each provider's version string ONCE, up front.  The render
    # callback below runs on every keystroke; calling ``get_version()``
    # from there would spawn ``opencode --version`` / ``claude --version``
    # (each ~hundreds of ms) on every arrow-key press and make the screen
    # feel sluggish.  The provider's own cache protects future callers too,
    # but we resolve here as well so the behaviour does not depend on
    # cache internals.
    versions: list[str] = [p.get_version() for p in available]

    kb = KeyBindings()

    @kb.add("up")
    def _(event: KeyPressEvent) -> None:
        nonlocal selected_idx
        selected_idx = (selected_idx - 1) % n

    @kb.add("down")
    def _(event: KeyPressEvent) -> None:
        nonlocal selected_idx
        selected_idx = (selected_idx + 1) % n

    @kb.add("enter")
    def _(event: KeyPressEvent) -> None:
        event.app.exit()

    @kb.add("c-c")
    def _(event: KeyPressEvent) -> None:
        nonlocal cancelled
        cancelled = True
        event.app.exit()

    def _render() -> list[tuple[str, str]]:
        lines: list[tuple[str, str]] = []
        lines.append(("class:header", "\n The Architect  "))
        lines.append(("class:dim", "select provider\n\n"))
        lines.append(("class:dim", "  Multiple AI CLI providers are installed.\n"))
        lines.append(("class:dim", "  Select which provider to use for this run.\n\n"))

        for i, p in enumerate(available):
            if i == selected_idx:
                lines.append(("class:pointer", "  › "))
                lines.append(("class:focused", f"{p.display_name}"))
            else:
                lines.append(("", "    "))
                lines.append(("", f"{p.display_name}"))

            # Show version if available.  Use the pre-resolved list so
            # the render callback stays pure in-memory — never fork a
            # subprocess per keystroke.
            ver = versions[i]
            if ver and ver != "unknown":
                lines.append(("class:dim", f"  (v{ver})"))
            lines.append(("", "\n"))

        lines.append(("", "\n"))
        lines.append(("class:dim", "  ↑↓ navigate   Enter confirm"))
        return lines

    content = FormattedTextControl(_render)
    layout = _padded_window(content)

    pt_style = PtStyle(
        [
            ("header", f"bold {ARCHITECT_GREEN}"),
            ("pointer", "bold"),
            ("focused", f"bold {ARCHITECT_GREEN}"),
            ("dim", "#888888"),
        ]
    )

    app: Application[object] = Application(
        layout=layout,
        key_bindings=kb,
        style=pt_style,
        full_screen=False,
    )
    app.run()

    if cancelled:
        raise SystemExit(0)

    return available[selected_idx]


def _prompt_mode_selection(
    provider: ArchitectProvider | None = None,
) -> dict[str, bool | int]:
    """Show a single-screen interactive configuration for The Architect run.

    Presented when the user runs ``architect`` without any mode flags
    (``--free``, ``--persistent``).  Power users who pass flags on the
    command line skip this screen entirely.

    The screen is provider-aware:
    - Free Tier and Token Budget are only shown when the provider supports
      OpenRouter free-tier rotation (OpenCode + OpenRouter configured).
    - When only Persistent is available, the screen is simplified to a
      single checkbox.

    Args:
        provider: The active AI CLI provider.  Used to determine which
            options are available.  Defaults to showing all options when
            not specified (backward-compatible behaviour).

    Returns:
        Dictionary with mode names mapped to their values.
        Example: ``{"free": True, "persistent": False, "token_budget_per_hour": 0}``
        ``token_budget_per_hour`` is 0 when the user doesn't type a budget (unlimited).
    """
    from collections.abc import Callable

    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
    from prompt_toolkit.layout import FormattedTextControl
    from prompt_toolkit.styles import Style as PtStyle

    # Determine which options are available for this provider.
    # Free Tier (OpenRouter rotation) is provider-specific.
    # Persistent and Token Budget are universal — always shown.
    show_free = provider.supports_free_tier() if provider is not None else True

    # Index mapping:
    #   show_free:  0=free  1=persistent  2=budget
    #   hide_free:  0=persistent  1=budget
    IDX_FREE = 0 if show_free else -1
    IDX_PERSISTENT = 1 if show_free else 0
    IDX_BUDGET = 2 if show_free else 1
    ITEM_COUNT = 3 if show_free else 2

    # ── Mutable state ────────────────────────────────────────────────────
    free = False
    persistent = False
    budget_text = ""
    focused = 0
    cancelled = False
    BUDGET_MAX_LEN = 10

    # ── Key bindings ─────────────────────────────────────────────────────
    kb = KeyBindings()

    @kb.add("up")
    def _(event: KeyPressEvent) -> None:
        nonlocal focused
        focused = (focused - 1) % ITEM_COUNT

    @kb.add("down")
    def _(event: KeyPressEvent) -> None:
        nonlocal focused
        focused = (focused + 1) % ITEM_COUNT

    @kb.add("tab")
    def _(event: KeyPressEvent) -> None:
        nonlocal focused
        focused = (focused + 1) % ITEM_COUNT

    @kb.add("space")
    def _(event: KeyPressEvent) -> None:
        nonlocal free, persistent
        if focused == IDX_FREE:
            free = not free
        elif focused == IDX_PERSISTENT:
            persistent = not persistent

    @kb.add("enter")
    def _(event: KeyPressEvent) -> None:
        event.app.exit()

    @kb.add("c-c")
    def _(event: KeyPressEvent) -> None:
        nonlocal cancelled
        cancelled = True
        event.app.exit()

    # Digit keys for budget input (always available — budget is universal)
    for _digit in "0123456789":

        def _make_handler(d: str) -> Callable[[KeyPressEvent], None]:
            def handler(event: KeyPressEvent) -> None:
                nonlocal budget_text
                if focused == IDX_BUDGET and len(budget_text) < BUDGET_MAX_LEN:
                    budget_text += d

            return handler

        kb.add(_digit)(_make_handler(_digit))

    @kb.add("backspace")
    def _(event: KeyPressEvent) -> None:
        nonlocal budget_text
        if focused == IDX_BUDGET:
            budget_text = budget_text[:-1]

    @kb.add("delete")
    def _(event: KeyPressEvent) -> None:
        nonlocal budget_text
        if focused == IDX_BUDGET:
            budget_text = ""

    # ── Layout ───────────────────────────────────────────────────────────
    def _render() -> list[tuple[str, str]]:
        lines: list[tuple[str, str]] = []

        # Header
        lines.append(("class:header", "\n The Architect  "))
        lines.append(("class:dim", "configure run\n\n"))

        if show_free:
            # Free Tier (OpenCode + OpenRouter only)
            ck = "x" if free else " "
            if focused == IDX_FREE:
                lines.append(("class:pointer", "  › "))
                lines.append(("class:focused", f"[{ck}] "))
            else:
                lines.append(("", "    "))
                lines.append(("", f"[{ck}] "))
            lines.append(("", "Free Tier"))
            lines.append(("class:dim", "        (OpenRouter free models, rotate on rate limit)\n"))

        # Persistent (universal)
        ck = "x" if persistent else " "
        if focused == IDX_PERSISTENT:
            lines.append(("class:pointer", "  › "))
            lines.append(("class:focused", f"[{ck}] "))
        else:
            lines.append(("", "    "))
            lines.append(("", f"[{ck}] "))
        lines.append(("", "Persistent"))
        lines.append(("class:dim", "       (30 retries, deeper retrospective)\n"))

        # Token Budget (universal)
        bd = budget_text or "0"
        if focused == IDX_BUDGET:
            lines.append(("class:pointer", "  › "))
            lines.append(("class:focused", "Token budget/hr: "))
            lines.append(("class:focused", bd))
            lines.append(("class:cursor", "█"))
        else:
            lines.append(("", "    "))
            lines.append(("", "Token budget/hr: "))
            lines.append(("", bd))
        lines.append(("class:dim", "  (0 = unlimited)\n"))

        # Instructions
        lines.append(("", "\n"))
        if focused == IDX_BUDGET:
            lines.append(
                ("class:dim", "  ↑↓ navigate   Type amount   Backspace delete   Enter confirm")
            )
        else:
            lines.append(("class:dim", "  ↑↓ navigate   Space toggle   Enter confirm"))

        return lines

    content = FormattedTextControl(_render)
    layout = _padded_window(content)

    pt_style = PtStyle(
        [
            ("header", f"bold {ARCHITECT_GREEN}"),
            ("pointer", "bold"),
            ("focused", "bold"),
            ("cursor", "bold"),
            ("dim", "#888888"),
        ]
    )

    app: Application[object] = Application(
        layout=layout,
        key_bindings=kb,
        style=pt_style,
        full_screen=False,
    )
    app.run()

    if cancelled:
        raise SystemExit(0)

    # ── Parse budget ─────────────────────────────────────────────────────
    try:
        token_budget = int(budget_text.strip() or "0")
    except ValueError:
        token_budget = 0

    return {
        "free": free if show_free else False,
        "persistent": persistent,
        "token_budget_per_hour": max(token_budget, 0),
    }


def _prompt_resume_screen(
    pending_tasks: list[Task],
    config: ArchitectConfig,
    provider: ArchitectProvider | None = None,
) -> dict[str, bool | int | str]:
    """Show a resume screen when pending tasks exist from a previous run.

    Displays the pending task count and names, pre-fills settings from
    the current config, and lets the user confirm execution, adjust
    settings, or switch to replan mode.

    The screen is provider-aware: Free Tier and Token Budget are only
    shown when the provider supports OpenRouter free-tier rotation.

    Args:
        pending_tasks: List of pending Task objects.
        config: Current ArchitectConfig (used for pre-filling settings).
        provider: The active AI CLI provider.  Used to determine which
            options are available.

    Returns:
        Dictionary with keys:
        - ``free``: bool
        - ``persistent``: bool
        - ``token_budget_per_hour``: int
        - ``action``: ``"execute"`` or ``"replan"``
    """
    from collections.abc import Callable

    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
    from prompt_toolkit.layout import FormattedTextControl
    from prompt_toolkit.styles import Style as PtStyle

    # Determine which options are available for this provider.
    # Free Tier (OpenRouter rotation) is provider-specific.
    # Persistent and Token Budget are universal — always shown.
    show_free = provider.supports_free_tier() if provider is not None else True

    # Index mapping:
    #   show_free:  0=free  1=persistent  2=budget  3=replan  4=execute
    #   hide_free:  0=persistent  1=budget  2=replan  3=execute
    IDX_FREE = 0 if show_free else -1
    IDX_PERSISTENT = 1 if show_free else 0
    IDX_BUDGET = 2 if show_free else 1
    IDX_REPLAN = 3 if show_free else 2
    IDX_EXECUTE = 4 if show_free else 3
    ITEM_COUNT = 5 if show_free else 4

    # ── Mutable state ────────────────────────────────────────────────────
    free = config.free_mode if show_free else False
    persistent = config.persistent
    budget_text = str(config.token_budget_per_hour) if config.token_budget_per_hour > 0 else ""
    focused = 0
    cancelled = False
    action = "execute"
    BUDGET_MAX_LEN = 10

    # ── Key bindings ─────────────────────────────────────────────────────
    kb = KeyBindings()

    @kb.add("up")
    def _(event: KeyPressEvent) -> None:
        nonlocal focused
        focused = (focused - 1) % ITEM_COUNT

    @kb.add("down")
    def _(event: KeyPressEvent) -> None:
        nonlocal focused
        focused = (focused + 1) % ITEM_COUNT

    @kb.add("tab")
    def _(event: KeyPressEvent) -> None:
        nonlocal focused
        focused = (focused + 1) % ITEM_COUNT

    @kb.add("space")
    def _(event: KeyPressEvent) -> None:
        nonlocal free, persistent, action
        if focused == IDX_FREE:
            free = not free
        elif focused == IDX_PERSISTENT:
            persistent = not persistent
        elif focused == IDX_REPLAN:
            action = "replan"
            event.app.exit()
        elif focused == IDX_EXECUTE:
            action = "execute"
            event.app.exit()

    @kb.add("enter")
    def _(event: KeyPressEvent) -> None:
        nonlocal action
        if focused == IDX_REPLAN:
            action = "replan"
        else:
            action = "execute"
        event.app.exit()

    @kb.add("c-c")
    def _(event: KeyPressEvent) -> None:
        nonlocal cancelled
        cancelled = True
        event.app.exit()

    # Digit keys for budget input (universal — always registered)
    for _digit in "0123456789":

        def _make_handler(d: str) -> Callable[[KeyPressEvent], None]:
            def handler(event: KeyPressEvent) -> None:
                nonlocal budget_text
                if focused == IDX_BUDGET and len(budget_text) < BUDGET_MAX_LEN:
                    budget_text += d

            return handler

        kb.add(_digit)(_make_handler(_digit))

    @kb.add("backspace")
    def _(event: KeyPressEvent) -> None:
        nonlocal budget_text
        if focused == IDX_BUDGET:
            budget_text = budget_text[:-1]

    @kb.add("delete")
    def _(event: KeyPressEvent) -> None:
        nonlocal budget_text
        if focused == IDX_BUDGET:
            budget_text = ""

    # ── Layout ───────────────────────────────────────────────────────────
    def _render() -> list[tuple[str, str]]:
        lines: list[tuple[str, str]] = []

        # Header
        lines.append(("class:header", "\n The Architect  "))
        lines.append(("class:dim", "resume run\n\n"))

        # Task summary
        n = len(pending_tasks)
        lines.append(("class:label", f"  {n} pending task{'s' if n != 1 else ''} to execute\n"))
        for t in pending_tasks[:5]:
            name = getattr(t, "name", str(t))
            prefix = getattr(t, "prefix", "")
            title = getattr(t, "title", name)
            lines.append(("class:dim", f"    {prefix}  {title}\n"))
        if n > 5:
            lines.append(("class:dim", f"    ... and {n - 5} more\n"))
        lines.append(("", "\n"))

        # Settings section
        lines.append(("class:label", "  Settings\n"))

        if show_free:
            # Free Tier (OpenCode + OpenRouter only)
            ck = "x" if free else " "
            if focused == IDX_FREE:
                lines.append(("class:pointer", "  › "))
                lines.append(("class:focused", f"[{ck}] "))
            else:
                lines.append(("", "    "))
                lines.append(("", f"[{ck}] "))
            lines.append(("", "Free Tier"))
            lines.append(("class:dim", "        (OpenRouter free models)\n"))

        # Persistent (universal)
        ck = "x" if persistent else " "
        if focused == IDX_PERSISTENT:
            lines.append(("class:pointer", "  › "))
            lines.append(("class:focused", f"[{ck}] "))
        else:
            lines.append(("", "    "))
            lines.append(("", f"[{ck}] "))
        lines.append(("", "Persistent"))
        lines.append(("class:dim", "       (30 retries, deeper retrospective)\n"))

        # Token Budget (universal)
        bd = budget_text or "0"
        if focused == IDX_BUDGET:
            lines.append(("class:pointer", "  › "))
            lines.append(("class:focused", "Token budget/hr: "))
            lines.append(("class:focused", bd))
            lines.append(("class:cursor", "█"))
        else:
            lines.append(("", "    "))
            lines.append(("", "Token budget/hr: "))
            lines.append(("", bd))
        lines.append(("class:dim", "  (0 = unlimited)\n"))

        # Separator
        lines.append(("", "\n"))

        # Actions
        if focused == IDX_REPLAN:
            lines.append(("class:pointer", "  › "))
            lines.append(("class:action_focused", "Replan"))
        else:
            lines.append(("", "    "))
            lines.append(("class:action", "Replan"))
        lines.append(("class:dim", "               (start fresh with a new goal)\n"))

        if focused == IDX_EXECUTE:
            lines.append(("class:pointer", "  › "))
            lines.append(("class:action_focused", "Execute"))
        else:
            lines.append(("", "    "))
            lines.append(("class:action", "Execute"))
        lines.append(("class:dim", "             (continue running pending tasks)\n"))

        # Instructions
        lines.append(("", "\n"))
        if focused == IDX_BUDGET:
            lines.append(
                ("class:dim", "  ↑↓ navigate   Type amount   Backspace delete   Enter execute")
            )
        elif focused == IDX_REPLAN:
            lines.append(("class:dim", "  ↑↓ navigate   Space/Enter to replan"))
        elif focused == IDX_EXECUTE:
            lines.append(("class:dim", "  ↑↓ navigate   Enter to execute"))
        else:
            lines.append(("class:dim", "  ↑↓ navigate   Space toggle   Enter execute"))

        return lines

    content = FormattedTextControl(_render)
    layout = _padded_window(content)

    pt_style = PtStyle(
        [
            ("header", f"bold {ARCHITECT_GREEN}"),
            ("pointer", "bold"),
            ("focused", "bold"),
            ("cursor", "bold"),
            ("dim", "#888888"),
            ("label", "bold"),
            ("action", ""),
            ("action_focused", "bold #7cc800"),
        ]
    )

    app: Application[object] = Application(
        layout=layout,
        key_bindings=kb,
        style=pt_style,
        full_screen=False,
    )
    app.run()

    if cancelled:
        raise SystemExit(0)

    # ── Parse budget ─────────────────────────────────────────────────────
    try:
        token_budget = int(budget_text.strip() or "0")
    except ValueError:
        token_budget = 0

    return {
        "free": free if show_free else False,
        "persistent": persistent,
        "token_budget_per_hour": max(token_budget, 0),
        "action": action,
    }


# ---------------------------------------------------------------------------
# Interactive planning prompts (opencode-style arrow-key menus)
# ---------------------------------------------------------------------------


def _prompt_goal() -> str:
    """Prompt the user for their development goal.

    Returns:
        The goal string (non-empty).
    """
    console.print()
    console.print(
        f"[bold {ARCHITECT_GREEN}]The Architect[/bold {ARCHITECT_GREEN}]  "
        f"[grey62]fire-and-forget autonomous development[/grey62]"
    )
    console.print()

    goal = _prompt_text_input(
        "What do you want to build?",
        "(describe the feature, component, or goal)",
    )

    if goal is None:
        console.print("[dim]Cancelled.[/dim]")
        raise SystemExit(0)
    if not goal.strip():
        console.print("[red]No goal provided. Exiting.[/red]")
        raise SystemExit(1)

    return str(goal.strip())


def _prompt_scope() -> TaskScope:
    """Prompt the user to select task scope.

    Returns:
        The selected TaskScope.
    """
    import questionary

    choice = questionary.select(
        "Task scope",
        choices=[
            questionary.Choice(
                "Standard — one feature area per task, balanced context  (recommended)",
                value="standard",
            ),
            questionary.Choice(
                "Simple   — one thing per task, tends toward smaller context per run  "
                "(weak/local models)",
                value="simple",
            ),
            questionary.Choice(
                "Complex  — one subsystem per task, tends toward larger context per run  "
                "(frontier models only)",
                value="complex",
            ),
        ],
        pointer="›",
        style=_questionary_style(),
    ).ask()

    if choice is None:
        console.print("[dim]Cancelled.[/dim]")
        raise SystemExit(0)

    return TaskScope(choice)


def _prompt_architect_model(
    project_dir: Path,
    provider: ArchitectProvider | None = None,
) -> str | None:
    """Prompt the user to select the architect model from available provider models.

    Fetches the model list and the currently configured model concurrently,
    then presents an arrow-key selection.

    Args:
        project_dir: The project root directory.
        provider: The active provider.  Defaults to auto-detection when not specified.

    Returns:
        Selected model string, or None to use provider default.
    """
    import questionary

    if provider is None:
        from the_architect.core.provider import detect_provider

        provider = detect_provider("auto")

    provider_name = provider.display_name
    console.print(f"[dim]Loading models from {provider_name}...[/dim]", end="\r")

    # Fetch models and current config concurrently
    async def _fetch() -> tuple[list[str], str]:
        loop = asyncio.get_event_loop()
        models, current = await asyncio.gather(
            loop.run_in_executor(None, provider.list_models),
            loop.run_in_executor(None, provider.get_resolved_model, project_dir, "architect"),
        )
        return models, current

    models, current = asyncio.run(_fetch())

    # Clear the loading line
    console.print(" " * 40, end="\r")

    if not models and not current:
        # Can't list models — fall back to free text
        typed = _prompt_text_input(
            "Architect model",
            f"({provider.display_name} model string, or leave blank for default)",
        )
        return typed.strip() if typed and typed.strip() else None

    # Build choices list — current model first so the cursor starts on it.
    # We intentionally avoid passing default= to questionary.select()
    # because that adds the item to selected_options, which makes it
    # permanently green bold (class:selected) even when the cursor
    # moves away.  Instead, we rely on the list order to set the
    # initial cursor position (first item = cursor start).
    choices: list[questionary.Choice] = []

    if models:
        # Put current model at top so the cursor starts there
        ordered = list(models)
        if current and current in ordered:
            ordered.remove(current)
            ordered.insert(0, current)
        elif current:
            ordered.insert(0, current)

        for m in ordered:
            label = f"  {m}"
            if m == current:
                label += "  [current]"
            choices.append(questionary.Choice(label, value=m))
    elif current:
        choices.append(questionary.Choice(f"  {current}  [current]", value=current))

    # Blank / provider-default option goes last — it's the fallback
    default_label = f"  (use {provider_name} default)"
    choices.append(questionary.Choice(default_label, value=""))

    selected = questionary.select(
        "Architect model",
        choices=choices,
        pointer="›",
        style=_questionary_style(),
    ).ask()

    if selected is None:
        console.print("[dim]Cancelled.[/dim]")
        raise SystemExit(0)

    # When the user picks "use provider default", resolve their actual default
    # model rather than returning None.
    if not selected:
        return current if current else None

    return str(selected)


def _prompt_exec_agent(
    project_dir: Path,
    provider: ArchitectProvider | None = None,
) -> str:
    """Prompt the user to select the execution agent.

    For providers that don't support named agents (Claude Code), this
    prompt is skipped and an empty string is returned.

    Args:
        project_dir: The project root directory.
        provider: The active provider.  Defaults to auto-detection when not specified.

    Returns:
        Selected agent string, or empty string for provider default.
    """
    import questionary

    if provider is None:
        from the_architect.core.provider import detect_provider

        provider = detect_provider("auto")

    # Providers without named-agent support — skip the prompt
    if not provider.supports_agents():
        return ""

    console.print(f"[dim]Loading agents from {provider.display_name}...[/dim]", end="\r")
    agents = provider.list_agents(project_dir)
    console.print(" " * 40, end="\r")

    if not agents:
        return ""

    choices = [questionary.Choice(f"  (use {provider.display_name} default)", value="")]
    for a in agents:
        choices.append(questionary.Choice(f"  {a}", value=a))

    selected = questionary.select(
        "Execution agent",
        choices=choices,
        pointer="›",
        style=_questionary_style(),
    ).ask()

    if selected is None:
        console.print("[dim]Cancelled.[/dim]")
        raise SystemExit(0)

    return str(selected)


def run_planning_mode(
    project: Path,
    config: ArchitectConfig,
    headless: bool = False,
    goal_text: str = "",
    scope_text: str = "",
    context_paths: tuple[Path, ...] = (),
    architect_model_override: str = "",
    execution_model_override: str | None = None,
    _skip_pending_guard: bool = False,
    provider: ArchitectProvider | None = None,
) -> None:
    """Interactive planning mode — prompts then runs the architect agent.

    After all prompts are answered, the terminal is cleared and a clean
    header is shown — matching the execution mode layout.

    In headless mode, all interactive prompts are skipped. Required values
    must come from flags or environment variables.

    Args:
        project: The project root directory.
        config: The The Architect configuration.
        headless: If True, skip all interactive prompts.
        goal_text: Pre-supplied goal (from --goal flag or env var).
        scope_text: Pre-supplied scope (from --scope flag or env var).
        context_paths: Pre-supplied context paths (from --context flag or env var).
        architect_model_override: Pre-supplied architect model (from --architect-model flag).
        execution_model_override: Pre-supplied execution model (from --execution-model flag or
            interactively collected by ``_collect_planning_prompts``).  ``None`` means the
            prompt has not been shown yet and should be shown now.  An empty string ``""``
            means the user explicitly chose the provider default (no override).
        _skip_pending_guard: If True, skip the pending-task guard (already ran
            in ``_collect_planning_prompts`` before the alternate screen).
        provider: The AI CLI provider to use.  Defaults to auto-detection when not specified.
    """
    if provider is None:
        from the_architect.core.provider import detect_provider

        provider = detect_provider("auto")
    # ── Pending task guard ─────────────────────────────────────────────
    # Check for unfinished tasks before asking for a new goal.
    # This prevents users from accidentally starting a new goal on top of
    # incomplete work they may have forgotten about.
    # Skipped when prompts were already collected before the alternate screen.
    if not _skip_pending_guard:
        tasks_dir = project / config.tasks_dir.name
        progress_file = project / config.progress_file.name
        pending = check_pending_tasks(tasks_dir, progress_file)
        if pending:
            if headless:
                # In headless mode, log the warning and continue automatically
                logger.warning(
                    f"Found {len(pending)} unfinished task(s) — "
                    "archiving and continuing in headless mode"
                )
            else:
                console.print()
                console.print(f"[yellow]⚠  You have {len(pending)} unfinished task(s):[/yellow]")
                for name in pending:
                    console.print(f"[dim]   • {name}[/dim]")
                console.print()
                console.print(
                    "[dim]Run [bold]architect[/bold] (without --plan) to finish them first.[/dim]"
                )
                console.print(
                    "[dim]Or continue below to start a new goal — "
                    "previous tasks will be archived.[/dim]"
                )
                console.print()
                import questionary as _q

                confirmed = _q.confirm(
                    "Start a new goal anyway? (previous tasks will be archived)",
                    default=False,
                    style=_questionary_style(),
                ).ask()
                if confirmed is not True:
                    console.print(
                        "[dim]Aborted. Run [bold]architect[/bold] to finish existing tasks.[/dim]"
                    )
                    raise SystemExit(0)

    # ── Goal resolution ────────────────────────────────────────────────
    # Load context files first so we can extract goal from them if needed
    context_content = ""
    context_labelled: list[tuple[str, str]] = []
    if context_paths:
        from the_architect.core.context import format_context_for_prompt, load_context_paths

        try:
            context_labelled = load_context_paths(list(context_paths))
            context_content = format_context_for_prompt(context_labelled)
        except FileNotFoundError as exc:
            console.print(f"[red]Error: {exc}[/red]")
            raise SystemExit(1)

    if goal_text:
        goal = goal_text
    elif headless:
        # Try to extract goal from context files
        if context_content:
            from the_architect.core.context import extract_goal_from_context

            extracted = extract_goal_from_context(context_content)
            if extracted:
                goal = extracted
                logger.info(f"Extracted goal from context: {goal[:80]}...")
            else:
                console.print(
                    "[red]Headless mode requires --goal or --context with a goal section.[/red]"
                )
                raise SystemExit(1)
        else:
            console.print("[red]Headless mode requires --goal or --context.[/red]")
            console.print("[dim]Provide a goal with --goal or context files with --context.[/dim]")
            raise SystemExit(1)
    else:
        goal = _prompt_goal()

    # ── Scope resolution ───────────────────────────────────────────────
    if scope_text:
        scope = TaskScope(scope_text)
    elif headless:
        scope = TaskScope.STANDARD  # Default in headless mode
    else:
        scope = _prompt_scope()

    # ── Model / agent resolution ────────────────────────────────────────
    architect_model: str | None
    if architect_model_override:
        architect_model = architect_model_override
    elif headless:
        # In headless mode, resolve the provider's default model explicitly.
        resolved = provider.get_resolved_model(project, "architect")
        architect_model = resolved if resolved else None
    else:
        architect_model = _prompt_architect_model(project, provider=provider)

    if execution_model_override is not None:
        # Already resolved (either from --execution-model flag or from
        # _collect_planning_prompts).  An empty string means the user chose
        # the provider default — still skip the prompt.
        config.execution_agent = execution_model_override
    elif not headless:
        exec_agent = _prompt_exec_agent(project, provider=provider)
        config.execution_agent = exec_agent

    # Clear the terminal and show a clean header — same style as execution
    if not headless:
        console.clear()
    console.print(
        f"[bold {ARCHITECT_GREEN}]The Architect[/bold {ARCHITECT_GREEN}]  "
        f"[grey62]v{__version__}[/grey62]  [grey62]planning[/grey62]"
    )
    console.print()
    console.print(f"[grey62]Goal:[/grey62] [{ARCHITECT_GREEN}]{goal}[/{ARCHITECT_GREEN}]")
    console.print(f"[grey62]Scope:[/grey62] [{ARCHITECT_GREEN}]{scope.value}[/{ARCHITECT_GREEN}]")
    # Resolve the model from the provider when no explicit override was set,
    # so the planning header always shows which model will be used.
    display_model = architect_model
    if not display_model:
        try:
            display_model = provider.get_resolved_model(project, "architect")
        except Exception:
            pass
    if display_model:
        console.print(
            f"[grey62]Model:[/grey62] [{ARCHITECT_GREEN}]{display_model}[/{ARCHITECT_GREEN}]"
        )
    if context_labelled:
        console.print(
            f"[grey62]Context:[/grey62] "
            f"[{ARCHITECT_GREEN}]{len(context_labelled)} file(s)[/{ARCHITECT_GREEN}]"
        )
    console.print()
    console.print(f"[grey62]Starting architect via {provider.display_name}...[/grey62]")
    console.print()

    # ── Project structure detection ─────────────────────────────────────
    from the_architect.core.structure import detect_structure, format_structure_for_prompt

    structure_report = detect_structure(project)
    structure_prompt = format_structure_for_prompt(structure_report)

    # ── ARCHITECT.md management ────────────────────────────────────────
    from the_architect.core.architect_md import (
        read_architect_md,
        write_or_update_architect_md,
    )

    # Write/update the structure section in ARCHITECT.md
    write_or_update_architect_md(project, structure_report)

    # Read full ARCHITECT.md content for prompt injection
    architect_md_content = read_architect_md(project) or ""

    provider.ensure_setup(project, config)

    # Proactive update check before planning starts
    update_msg = provider.check_update_available()
    if update_msg:
        console.print(f"\n[bold yellow]⚠  {update_msg}[/bold yellow]")
        if not headless:
            import questionary as _q

            confirmed = _q.confirm(
                "Continue with outdated provider anyway?",
                default=False,
                style=_questionary_style(),
            ).ask()
            if confirmed is not True:
                console.print("[dim]Aborted by user.[/dim]")
                raise SystemExit(0)
        console.print()

    request = PlanningRequest(
        goal=goal,
        scope=scope,
        project_dir=project,
        model_override=architect_model,
        context_content=context_content,
        structure_report=structure_prompt,
        architect_md_content=architect_md_content,
    )

    # Log file for the planning session
    log_dir = config.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    planning_log = log_dir / "architect.log"

    try:
        result = asyncio.run(run_planner(request, config, log_path=planning_log, provider=provider))
        console.print()
        console.print(f"[#7cc800]✓ Created {len(result.tasks_created)} tasks[/#7cc800]")
        console.print()

        # ── Auto-append planning history to ARCHITECT.md ──────────────
        try:
            from the_architect.core.architect_md import append_planning_history

            tasks_desc = ", ".join(result.tasks_created) if result.tasks_created else "none"
            append_planning_history(
                project,
                goal=goal,
                tasks_created=tasks_desc,
                notes=f"Scope: {scope.value}",
            )
        except Exception as arch_exc:
            logger.debug(f"Failed to append planning history to ARCHITECT.md: {arch_exc!r}")
    except PlanningFailedError as e:
        console.print(f"[red]Planning failed: {e}[/red]")
        raise SystemExit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Pre-flight prompt collection (runs BEFORE alternate screen)
# ---------------------------------------------------------------------------


def _collect_planning_prompts(
    project: Path,
    config: ArchitectConfig,
    headless: bool = False,
    goal_text: str = "",
    scope_text: str = "",
    context_paths: tuple[Path, ...] = (),
    architect_model_override: str = "",
    execution_model_override: str = "",
    provider: ArchitectProvider | None = None,
) -> tuple[str, str, str, str | None]:
    """Collect all planning prompts in the normal scroll buffer.

    This runs the interactive portion of planning (pending-task guard, goal,
    scope, model, agent selection) **before** the alternate screen is entered,
    so that questionary / prompt_toolkit renders at the top of the visible
    area rather than at the bottom of the alternate screen buffer.

    The actual planner execution (opencode) is **not** triggered here — that
    happens inside the alternate screen via ``run_planning_mode``.

    In headless mode all prompts are skipped and the original values are
    returned unchanged.

    Args:
        project: The project root directory.
        config: The pre-loaded Architect configuration (mutated in place for
            ``execution_agent`` when an agent is selected).
        headless: If True, skip all interactive prompts.
        goal_text: Pre-supplied goal (from --goal flag or env var).
        scope_text: Pre-supplied scope (from --scope flag or env var).
        context_paths: Pre-supplied context paths.
        architect_model_override: Pre-supplied architect model.
        execution_model_override: Pre-supplied execution model.  An empty
            string means the user has not yet been asked; ``None`` is never
            passed in but may appear in the return value when headless mode
            skips the prompt entirely.

    Returns:
        Tuple of ``(goal_text, scope_text, architect_model, execution_model)``
        with any interactively collected values filled in.  ``execution_model``
        is ``None`` when headless mode is active and no override was supplied
        (signals ``run_planning_mode`` to skip the prompt without overriding
        ``config.execution_agent``).
    """
    if headless:
        # Return None for execution_model when no override was given so that
        # run_planning_mode skips the prompt without clobbering the config.
        exec_override: str | None = execution_model_override if execution_model_override else None
        return goal_text, scope_text, architect_model_override, exec_override

    # ── Pending task guard ─────────────────────────────────────────────
    tasks_dir = project / config.tasks_dir.name
    progress_file = project / config.progress_file.name
    pending = check_pending_tasks(tasks_dir, progress_file)
    if pending:
        console.print()
        console.print(f"[yellow]⚠  You have {len(pending)} unfinished task(s):[/yellow]")
        for name in pending:
            console.print(f"[dim]   • {name}[/dim]")
        console.print()
        console.print(
            "[dim]Run [bold]architect[/bold] (without --plan) to finish them first.[/dim]"
        )
        console.print(
            "[dim]Or continue below to start a new goal — previous tasks will be archived.[/dim]"
        )
        console.print()
        import questionary as _q

        confirmed = _q.confirm(
            "Start a new goal anyway? (previous tasks will be archived)",
            default=False,
            style=_questionary_style(),
        ).ask()
        if confirmed is not True:
            console.print(
                "[dim]Aborted. Run [bold]architect[/bold] to finish existing tasks.[/dim]"
            )
            raise SystemExit(0)

    # ── Goal ────────────────────────────────────────────────────────────
    if not goal_text:
        goal_text = _prompt_goal()

    # ── Scope ───────────────────────────────────────────────────────────
    if not scope_text:
        scope = _prompt_scope()
        scope_text = scope.value

    # ── Architect model ─────────────────────────────────────────────────
    if not architect_model_override:
        resolved_model = _prompt_architect_model(project, provider=provider)
        architect_model_override = resolved_model or ""

    # ── Execution agent ─────────────────────────────────────────────────
    # Always prompt here (once) if no override was supplied via CLI flag.
    # The result — even an empty string meaning "use provider default" — is
    # returned so run_planning_mode can skip the prompt entirely via the
    # `is not None` sentinel check.
    if not execution_model_override:
        exec_agent = _prompt_exec_agent(project, provider=provider)
        execution_model_override = exec_agent
        config.execution_agent = exec_agent

    return goal_text, scope_text, architect_model_override, execution_model_override


# ---------------------------------------------------------------------------
# Execution — raw terminal passthrough
# ---------------------------------------------------------------------------


def _ansi_supported() -> bool:
    """Return True if the current terminal supports ANSI escape codes and colour.

    Returns False when:
    - stdout is not a TTY
    - NO_COLOR env var is set (https://no-color.org/)
    - TERM is 'dumb'

    Returns:
        True if ANSI output is appropriate.
    """
    if not sys.stdout.isatty():
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    return True


def _spin(label: str, duration: float = 1.2) -> None:
    """Show a brief spinner for ``duration`` seconds then erase the line.

    Runs in the calling thread (blocks for ``duration`` seconds).
    Only used in the ~1s gap between architect printing the task header
    and opencode producing its first line of output.

    Args:
        label: Short text shown next to the spinner.
        duration: How long to spin in seconds.
    """
    import itertools
    import sys

    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    spinner = itertools.cycle(frames)
    interval = 0.08
    elapsed = 0.0
    # Only animate if we have a real TTY with ANSI support
    if not _ansi_supported():
        return
    try:
        while elapsed < duration:
            frame = next(spinner)
            sys.stdout.write(f"\r\033[38;2;124;200;0m{frame}\033[0m\033[2m {label}\033[0m")
            sys.stdout.flush()
            time.sleep(interval)
            elapsed += interval
    finally:
        # Erase the spinner line completely
        sys.stdout.write("\r\033[2K")
        sys.stdout.flush()


def _countdown(seconds: int) -> None:
    """Print a live countdown during the pause between tasks.

    Overwrites itself on each tick so it leaves no trace once done.

    Args:
        seconds: Number of seconds to count down.
    """
    import sys

    if seconds <= 0:
        return
    if not _ansi_supported():
        time.sleep(seconds)
        return

    for remaining in range(seconds, 0, -1):
        sys.stdout.write(f"\r\033[38;2;124;200;0m  ⏸ next task in {remaining}s…\033[0m")
        sys.stdout.flush()
        time.sleep(1)
    sys.stdout.write("\r\033[2K")
    sys.stdout.flush()


async def _run_tasks_raw(
    project: Path,
    config: ArchitectConfig,
    tasks: list[Task],
    free_rotator: object | None = None,
    monitor_writer: MonitorStateWriter | None = None,
    provider: ArchitectProvider | None = None,
) -> tuple[bool, list[TaskResult], float]:
    """Run all tasks with provider output going directly to the terminal.

    The provider renders natively — no piping, no JSON parsing.  Minimal
    architect headers are printed before and after each task so the user
    knows what's running; everything in between is pure provider output.

    Args:
        project: The project root directory.
        config: The The Architect configuration.
        tasks: The tasks to run.
        free_rotator: Optional FreeModelRotator for --free mode.
        monitor_writer: Optional MonitorStateWriter for dashboard state updates.
        provider: The AI CLI provider to use.  Defaults to auto-detection.

    Returns:
        Tuple of (all_succeeded, results, total_duration).
    """
    plan = TaskPlan(tasks=tasks)
    results: list[TaskResult] = []
    run_start = time.time()

    pending_count = sum(1 for t in tasks if _task_needs_work(t))

    def on_task_start(task: Task) -> None:
        remaining = sum(1 for t in plan.tasks if _task_needs_work(t))
        title = task.title or task.name
        suffix = (
            f"  [dim]({remaining}/{pending_count} remaining)[/dim]" if pending_count > 1 else ""
        )
        console.print()
        console.print(f"[bold #7cc800]══ {task.prefix}[/bold #7cc800]  [dim]{title}[/dim]{suffix}")
        _spin(f"  starting {task.prefix}…", duration=1.2)
        console.print()
        if monitor_writer is not None:
            try:
                monitor_writer.on_task_start(task)
            except Exception:
                pass

    def on_task_done(result: TaskResult) -> None:
        results.append(result)
        _record_task_outcome(config.progress_file, result)
        console.print()
        console.print(
            f"[#7cc800]✓ {result.prefix} done[/#7cc800]  "
            f"[dim]{_fmt_duration(result.duration_seconds)}[/dim]"
        )
        if monitor_writer is not None:
            try:
                monitor_writer.on_task_done(result.prefix, tokens=result.tokens.total)
            except Exception:
                pass

    def on_task_failed(result: TaskResult) -> None:
        results.append(result)
        console.print()
        console.print(f"[red]✗ {result.prefix} failed after {config.max_retries} attempts[/red]")
        if monitor_writer is not None:
            try:
                monitor_writer.on_task_failed(result.prefix, tokens=result.tokens.total)
            except Exception:
                pass

    def on_attempt_start(attempt_num: int, model: str | None) -> None:
        if attempt_num > 1:
            model_note = f" → {model}" if model else ""
            console.print()
            console.print(
                f"[yellow]↻ Retry {attempt_num}/{config.max_retries}{model_note}[/yellow]"
            )
            console.print()
        if monitor_writer is not None:
            try:
                monitor_writer.on_attempt_start(attempt_num, model)
            except Exception:
                pass

    def on_model_switched(old_model: str, new_model: str | None) -> None:
        """Called when --free mode rotates to a new model due to rate limit."""
        if new_model:
            console.print()
            console.print(
                f"[yellow]⚡ Rate limit hit on {old_model}[/yellow]  "
                f"[dim]→ switching to {new_model}[/dim]"
            )
            console.print()
        else:
            console.print()
            console.print(
                f"[yellow]⚡ Rate limit hit on {old_model}[/yellow]  "
                f"[dim]→ all free models exhausted, falling back to default[/dim]"
            )
            console.print()
        if monitor_writer is not None and new_model:
            try:
                monitor_writer.on_model_rotated(new_model)
            except Exception:
                pass

    def on_attempt_done(attempt_num: int, success: bool) -> None:
        if not success and attempt_num < config.max_retries:
            next_model = (
                config.retry_model_2
                if attempt_num + 1 == 2 and config.retry_model_2
                else config.retry_model_3
                if attempt_num + 1 == 3 and config.retry_model_3
                else None
            )
            model_note = f" switching to {next_model}" if next_model else ""
            console.print(
                f"\n[dim]Attempt {attempt_num} did not mark task Done —"
                f" retrying ({attempt_num + 1}/{config.max_retries}){model_note}...[/dim]"
            )
        if monitor_writer is not None:
            try:
                monitor_writer.on_attempt_done(attempt_num, success)
            except Exception:
                pass

    def on_circuit_event(event_name: str, data: dict[str, Any]) -> None:
        """Forward circuit/cooldown/replan events to the monitor writer."""
        if monitor_writer is None:
            return
        try:
            if event_name == "circuit_state_change":
                monitor_writer.on_circuit_state_change(
                    state=data.get("state", "CLOSED"),
                    no_progress=data.get("no_progress", 0),
                    same_error=data.get("same_error", 0),
                    no_progress_threshold=data.get("no_progress_threshold", 3),
                    same_error_threshold=data.get("same_error_threshold", 3),
                )
            elif event_name == "cooldown_start":
                monitor_writer.on_cooldown_start(
                    task_id=data.get("task_id", ""),
                    wait_count=data.get("wait_count", 0),
                )
            elif event_name == "cooldown_end":
                monitor_writer.on_cooldown_end()
            elif event_name == "replan_start":
                monitor_writer.on_replan(task_id=data.get("task_id", ""))
            elif event_name == "replan_end":
                monitor_writer.on_replan_done()
        except Exception:
            pass  # Monitor write failure must not stop the run

    # Pre-populate results for already-resolved tasks so the SUCCESS.md
    # summary reflects their real state.  Done → skipped (they were
    # already complete before this run).  Failed → we surface them as
    # failed in the summary so the reviewer has full visibility and the
    # user is not misled into thinking every task succeeded.
    for t in plan.tasks:
        if t.status == TaskStatus.DONE:
            results.append(TaskResult(prefix=t.prefix, title=t.title or t.name, status="skipped"))
        elif t.status == TaskStatus.FAILED:
            results.append(TaskResult(prefix=t.prefix, title=t.title or t.name, status="failed"))

    success = await run_all(
        plan=plan,
        config=config,
        on_task_start=on_task_start,
        on_task_done=on_task_done,
        on_task_failed=on_task_failed,
        on_attempt_start=on_attempt_start,
        on_attempt_done=on_attempt_done,
        on_task_pause=_countdown,
        free_rotator=free_rotator,
        on_model_switched=on_model_switched,
        on_circuit_event=on_circuit_event,
        provider=provider,
    )

    total_duration = time.time() - run_start
    return success, results, total_duration


def _fmt_duration(seconds: float) -> str:
    """Format seconds as M:SS."""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _read_goal_from_instructions(tasks_dir: Path) -> str:
    """Try to read the original goal from tasks/INSTRUCTIONS.md.

    Falls back to empty string if the file doesn't exist or can't be parsed.

    Args:
        tasks_dir: Path to the tasks/ directory.

    Returns:
        The goal string, or empty string if not found.
    """
    instructions_md = tasks_dir / "INSTRUCTIONS.md"
    if not instructions_md.exists():
        return ""
    try:
        content = instructions_md.read_text(encoding="utf-8")
        # Look for "## Goal" section
        import re

        match = re.search(r"## Goal\s*\n\s*(.+?)(?:\n\s*##|\n\s*$)", content, re.DOTALL)
        if match:
            return match.group(1).strip()
    except OSError:
        pass
    return ""


def _record_task_outcome(progress_file: Path, result: TaskResult) -> None:
    """Persist a concise task outcome row and refresh Last Task Summary."""
    try:
        content = progress_file.read_text(encoding="utf-8")
    except OSError:
        return

    summary = (result.outcome_summary or "Downstream impact: none").strip()
    summary_single = summary.replace("\n", " ; ")
    impact = "Possible" if "Downstream impact: possible" in summary else "None"
    row = (
        f"| {result.prefix} | {summary_single} | Captured in summary | "
        f"Captured in summary | {impact} |\n"
    )
    marker = (
        "## Task Outcomes\n\n"
        "| Task | Outcome | Files | Verification | Impact on Next Tasks |\n"
        "|------|---------|-------|--------------|----------------------|\n"
    )
    if marker in content and row not in content:
        content = content.replace(marker, marker + row)

    import re

    content = re.sub(
        r"## Last Task Summary\s*\n\s*.*?(?=\n---)",
        f"## Last Task Summary\n\n{summary}\n",
        content,
        flags=re.DOTALL,
    )

    try:
        progress_file.write_text(content, encoding="utf-8")
    except OSError:
        return


def _task_results_needing_reassessment(results: list[TaskResult]) -> list[TaskResult]:
    """Return done task results that explicitly indicate downstream impact."""
    return [
        result
        for result in results
        if result.status == "done" and "Downstream impact: possible" in result.outcome_summary
    ]


# ---------------------------------------------------------------------------
# Tmux session teardown helper
# ---------------------------------------------------------------------------


def _maybe_kill_own_tmux_session(project_dir: Path) -> None:
    """Kill the tmux session that The Architect created, if we are inside it.

    Called from the ``finally`` block after the runner exits so the user
    lands back in their original terminal cleanly.  Without this, the user
    would be left inside a dead tmux session — requiring a second Ctrl+C
    to exit.

    Only kills sessions matching The Architect's naming convention
    (``architect-<project-name>``) so unrelated sessions are never touched.

    This is a no-op when:
    - We are not inside a tmux session (``TMUX`` env var not set)
    - The current session name does not match our convention
    - The kill command fails for any reason

    Args:
        project_dir: The project root directory (used to derive session name).
    """
    import os as _os

    # Only act when we are inside tmux
    if not _os.environ.get("TMUX"):
        return

    try:
        import subprocess as _sp

        from the_architect.core.tmux import get_session_name, kill_session, session_exists

        expected_session = get_session_name(project_dir)

        # Find the name of the current tmux session
        result = _sp.run(
            ["tmux", "display-message", "-p", "#S"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return

        current_session = result.stdout.strip()

        # Only kill if it's our session
        if current_session != expected_session:
            return

        if session_exists(current_session):
            kill_session(current_session)

    except Exception:
        # Never crash the process — teardown is best-effort
        pass


def _provider_install_hint() -> str:
    """Return the install command for the detected provider.

    Returns:
        Human-readable install command string.
    """
    from the_architect.core.provider import detect_provider

    try:
        return detect_provider("auto").install_hint()
    except Exception:
        from the_architect.core.opencode_provider import OpenCodeProvider

        return OpenCodeProvider().install_hint()


# Backward-compatible alias — tests import this name
_opencode_install_hint = _provider_install_hint


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.version_option(
    __version__,
    "-V",
    "--version",
    message="architect v%(version)s",
)
@click.pass_context
@click.option(
    "--project",
    "-p",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Project directory (default: current working directory)",
)
@click.option("--plan", is_flag=True, help="Force planning mode")
@click.option(
    "--standalone",
    default="",
    metavar="MODEL",
    help="Use this model directly (bypasses provider config)",
)
@click.option(
    "--from",
    "from_task",
    default="",
    metavar="PREFIX",
    help="Resume from a specific task prefix, e.g. --from T03",
)
@click.option(
    "--only",
    "only_task",
    default="",
    metavar="PREFIX",
    help="Run a single task only, e.g. --only T05",
)
@click.option(
    "--persistent",
    is_flag=True,
    help="Persistent mode: retry up to 50 times with 2 retrospective rounds",
)
@click.option(
    "--free",
    "free_mode",
    is_flag=True,
    help="Use free-tier OpenRouter models, rotating on rate limits (OpenCode + OpenRouter only)",
)
@click.option(
    "--no-monitor",
    "no_monitor",
    is_flag=True,
    help="Skip tmux monitoring (useful for CI or piped output)",
)
@click.option(
    "--headless",
    is_flag=True,
    help="Headless mode: no interactive prompts, all input via flags or env vars",
)
@click.option(
    "--goal",
    "goal_text",
    default="",
    metavar="TEXT",
    help="Planning goal (replaces interactive prompt, required in headless mode without --context)",
)
@click.option(
    "--scope",
    "scope_text",
    default=None,
    metavar="SCOPE",
    type=click.Choice(["simple", "standard", "complex"], case_sensitive=False),
    help="Task scope: simple, standard, or complex (default: standard)",
)
@click.option(
    "--context",
    "context_paths",
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    metavar="FILE_OR_DIR",
    help="Add file or directory to planning context (repeatable)",
)
@click.option(
    "--architect-model",
    "architect_model",
    default="",
    metavar="MODEL",
    help="Model for the architect agent (overrides opencode default)",
)
@click.option(
    "--execution-model",
    "execution_model",
    default="",
    metavar="MODEL",
    help="Model for the execution agent (overrides opencode default)",
)
def main(
    ctx: click.Context,
    project: Path | None,
    plan: bool,
    standalone: str,
    from_task: str,
    only_task: str,
    persistent: bool,
    free_mode: bool,
    no_monitor: bool,
    headless: bool,
    goal_text: str,
    scope_text: str,
    context_paths: tuple[Path, ...],
    architect_model: str,
    execution_model: str,
) -> None:
    """The Architect — fire-and-forget autonomous development."""
    _setup_loguru()

    if ctx.invoked_subcommand is not None:
        return

    resolved_project = (project or Path.cwd()).resolve()

    # Resolve headless from env var if flag not set
    if not headless:
        headless = os.environ.get("ARCHITECT_HEADLESS", "").lower() in ("true", "1", "yes")

    # Resolve goal from env var if flag not set
    if not goal_text:
        goal_text = os.environ.get("ARCHITECT_GOAL", "")

    # Resolve scope from env var if flag not set
    if not scope_text:
        scope_text = os.environ.get("ARCHITECT_SCOPE", "")

    # Resolve context from env var if flag not set
    if not context_paths:
        env_context = os.environ.get("ARCHITECT_CONTEXT", "")
        if env_context:
            context_paths = tuple(Path(p) for p in env_context.split(os.pathsep) if p.strip())

    # Resolve architect-model from env var if flag not set
    if not architect_model:
        architect_model = os.environ.get("ARCHITECT_ARCHITECT_MODEL", "")

    # Resolve execution-model from env var if flag not set
    if not execution_model:
        execution_model = os.environ.get("ARCHITECT_EXECUTION_MODEL", "")

    # ── Early config + task discovery ───────────────────────────────────
    # Done BEFORE launching tmux so we can:
    #   1. Write the monitor state file before the dashboard pane starts
    #   2. Show the right dashboard screen immediately (PLANNING vs RUNNING)
    # This is cheap — no prompts, no opencode calls.
    config = load_config(resolved_project)

    if standalone:
        config.standalone_mode = standalone

    # ── Guard: standalone_mode must be compatible with the active provider ─
    # Runs here (before tmux / prompts) so the bad value is cleared before
    # it can be forwarded into _run_main via _pre_loaded_config.
    # Identical logic lives in _run_main for the non-pre-loaded path.
    if config.standalone_mode:
        _provider_hint = os.environ.get("ARCHITECT_PROVIDER", "").strip() or config.provider
        _is_claude_code_hint = _provider_hint in ("claude-code", "claude")
        if not _is_claude_code_hint:
            # Try to detect from installed binaries
            try:
                from the_architect.core.claude_code_provider import ClaudeCodeProvider as _CCP

                _det = detect_provider(_provider_hint if _provider_hint != "auto" else "auto")
                _is_claude_code_hint = isinstance(_det, _CCP)
            except Exception:
                pass
        if _is_claude_code_hint:
            sm = config.standalone_mode
            is_openrouter = sm.startswith("openrouter/") or (
                "/" in sm and not sm.startswith("claude")
            )
            if is_openrouter:
                logger.warning(
                    f"standalone_mode '{sm}' is incompatible with Claude Code "
                    "(OpenRouter model ID) — clearing"
                )
                config.standalone_mode = ""
                from the_architect.config import write_config as _write_cfg

                _write_cfg(resolved_project, {"standalone_mode": ""})

    # ── Phase 1 — Provider binary check (BEFORE tmux, no interactive UI) ──
    # Only check that at least one provider is installed.  If multiple are
    # installed the interactive selection is deferred until AFTER tmux
    # launches (Phase 2 below), so the prompt appears inside the left pane.
    #
    # If the user already chose a provider via architect.toml or the
    # ARCHITECT_PROVIDER env var (set by a previous Phase 2 selection and
    # forwarded into tmux), skip the check entirely.
    _provider_env = os.environ.get("ARCHITECT_PROVIDER", "").strip()
    _active_provider: ArchitectProvider | None = None

    try:
        if config.provider != "auto":
            # Explicit preference in architect.toml — validate immediately
            _active_provider = detect_provider(config.provider)
        elif _provider_env:
            # Already selected in a previous Phase 2 run (forwarded via env var)
            _active_provider = detect_provider(_provider_env)
        else:
            # Auto mode — just check that something is installed
            _available_pre = detect_available_providers()
            if not _available_pre:
                from the_architect.core.claude_code_provider import ClaudeCodeProvider
                from the_architect.core.codex_cli_provider import CodexCliProvider
                from the_architect.core.opencode_provider import OpenCodeProvider

                oc = OpenCodeProvider()
                codex = CodexCliProvider()
                cc = ClaudeCodeProvider()
                console.print("[red]Error: No supported AI CLI found.[/red]")
                console.print()
                console.print("[dim]Install one of:[/dim]")
                console.print(f"[dim]  OpenCode:    {oc.install_hint()}[/dim]")
                console.print(f"[dim]  Codex CLI:   {codex.install_hint()}[/dim]")
                console.print(f"[dim]  Claude Code: {cc.install_hint()}[/dim]")
                raise SystemExit(1)
            elif len(_available_pre) == 1:
                # Only one provider — resolve now, no prompt needed
                _active_provider = _available_pre[0]
            # else: multiple installed → defer selection to Phase 2 (post-tmux)
    except ProviderNotFoundError as _pnfe:
        console.print(f"[red]Error: {_pnfe}[/red]")
        raise SystemExit(1)

    # ── Provider usability check (non-blocking, only when already resolved) ─
    # Only warn when the provider appears to have no models/API key configured.
    # Skip when _active_provider is None (both installed, selection deferred).
    if _active_provider is not None and not _active_provider.has_any_models():
        user_cfg = _active_provider.find_user_config(resolved_project)
        if user_cfg is None:
            console.print()
            console.print(
                f"[yellow]{_active_provider.display_name} may not be configured yet.[/yellow]"
            )
            console.print()
            if _active_provider.name == "opencode":
                console.print(
                    "The Architect uses OpenCode to run AI agents. "
                    "OpenCode needs at least one provider."
                )
                console.print(
                    "Run [bold]opencode[/bold] once to set up a provider, then come back."
                )
                console.print()
                console.print("[dim]OpenCode looks for config in:[/dim]")
                console.print("[dim]  • OPENCODE_CONFIG env var (explicit config file path)[/dim]")
                console.print("[dim]  • OPENCODE_CONFIG_DIR env var (config directory)[/dim]")
                console.print("[dim]  • opencode.json / opencode.jsonc in the project root[/dim]")
                console.print("[dim]  • ~/.config/opencode/opencode.json (global)[/dim]")
                console.print("[dim]  • Built-in free models (no config needed)[/dim]")
            elif _active_provider.name == "codex":
                console.print(
                    "The Architect uses Codex CLI to run AI agents. "
                    "Set CODEX_API_KEY or run [bold]codex[/bold] to configure."
                )
                console.print()
                console.print("[dim]Codex CLI looks for config in:[/dim]")
                console.print("[dim]  • CODEX_API_KEY env var[/dim]")
                console.print("[dim]  • ~/.codex/config.toml (global)[/dim]")
            else:
                console.print(
                    f"The Architect uses {_active_provider.display_name} to run AI agents. "
                    "Set ANTHROPIC_API_KEY or run [bold]claude[/bold] to configure."
                )
                console.print()
                console.print("[dim]Claude Code looks for config in:[/dim]")
                console.print("[dim]  • ANTHROPIC_API_KEY env var[/dim]")
                console.print("[dim]  • CLAUDE.md in the project root[/dim]")
                console.print("[dim]  • ~/.claude/CLAUDE.md (global)[/dim]")
            raise SystemExit(1)

    # ── Discover tasks BEFORE asking mode selection ─────────────────────
    # Mode selection (Free / Persistent) only affects execution — it is
    # meaningless when there are no tasks yet.  We check for tasks first,
    # then only ask about modes when we know execution will happen.
    tasks_dir = resolved_project / config.tasks_dir.name
    _tasks_pre = discover_tasks(tasks_dir)
    progress_file = resolved_project / config.progress_file.name
    _tasks_pre = _filter_and_set_status(_tasks_pre, progress_file)

    # Apply --only / --from for the pre-check
    if only_task:
        _matched_pre = [t for t in _tasks_pre if t.prefix.upper() == only_task.upper()]
        if not _matched_pre:
            console.print(f"[red]No task found with prefix '{only_task}'.[/red]")
            console.print("[dim]Run 'architect list' to see available tasks.[/dim]")
            raise SystemExit(1)
        _tasks_pre = _matched_pre
    elif from_task:
        _prefixes_pre = [t.prefix.upper() for t in _tasks_pre]
        _start_pre = from_task.upper()
        if _start_pre not in _prefixes_pre:
            console.print(f"[red]No task found with prefix '{from_task}'.[/red]")
            console.print("[dim]Run 'architect list' to see available tasks.[/dim]")
            raise SystemExit(1)
        _idx_pre = _prefixes_pre.index(_start_pre)
        _tasks_pre = _tasks_pre[_idx_pre:]

    _all_done_pre = bool(_tasks_pre) and all(_task_is_terminal(t) for t in _tasks_pre)
    _no_tasks_pre = not _tasks_pre
    _needs_planning = plan or _no_tasks_pre or (_all_done_pre and not only_task and not from_task)

    # ── Write monitor state BEFORE launching tmux ───────────────────────
    # The dashboard pane starts alongside the left pane. Writing the state
    # here ensures the dashboard shows the right screen immediately.
    if _needs_planning:
        if _all_done_pre and not plan and not only_task and not from_task and headless:
            console.print(
                "[#7cc800]✓ All tasks complete.[/#7cc800]  "
                "[dim]Use --plan to start a new goal.[/dim]"
            )
            raise SystemExit(0)
        try:
            from the_architect.core.monitor_state import write_planning_state

            write_planning_state(resolved_project, goal=goal_text or "")
        except Exception:
            pass
    else:
        # Execution path: tasks exist and are not all done.
        # The monitor state file may still contain a terminal status (DONE/
        # FAILED) from a previous run.  The dashboard reads it the moment
        # its pane starts, sees a terminal status, and exits after 2 seconds —
        # causing the "right panel shows for 1-2 seconds then disappears" bug.
        # Writing a fresh RUNNING state here clears the stale status so the
        # dashboard stays alive throughout the resume-screen interaction and
        # into execution (where MonitorStateWriter takes over).
        try:
            from the_architect.core.monitor_state import (
                RUN_STATUS_RUNNING,
                TASK_STATUS_DONE,
                TASK_STATUS_FAILED,
                TASK_STATUS_PENDING,
                write_monitor_state,
            )

            def _monitor_status_for(task: Task) -> str:
                """Map a Task in-memory status to the monitor's string vocabulary.

                Mirrors the terminal-vs-pending distinction the execution
                loop uses: DONE and FAILED are terminal and must appear as
                such on the dashboard; everything else is Pending from the
                monitor's perspective.
                """
                if task.status == TaskStatus.DONE:
                    return TASK_STATUS_DONE
                if task.status == TaskStatus.FAILED:
                    return TASK_STATUS_FAILED
                return TASK_STATUS_PENDING

            write_monitor_state(
                resolved_project,
                {
                    "status": RUN_STATUS_RUNNING,
                    "project_name": resolved_project.name,
                    "tasks": [
                        {
                            "id": t.prefix,
                            "title": t.title or t.name,
                            "status": _monitor_status_for(t),
                            "replanned": False,
                        }
                        for t in _tasks_pre
                    ],
                    "current_task_id": None,
                    "current_task_title": None,
                    "current_attempt": 0,
                    "total_tasks": len(_tasks_pre),
                    "tasks_completed": sum(1 for t in _tasks_pre if t.status == TaskStatus.DONE),
                },
            )
        except Exception:
            pass

    # ── Auto-launch in tmux ──────────────────────────────────────────────
    # Launch tmux immediately — all interaction (welcome screen, prompts,
    # execution) happens inside the left pane.  The original process just
    # creates the session and exits (replaced by tmux attach).
    from the_architect.core.tmux import maybe_launch_tmux

    launched = maybe_launch_tmux(resolved_project, sys.argv, no_monitor=no_monitor)
    if launched:
        # tmux attach replaced the process — this line is never reached
        return  # pragma: no cover

    # ── Everything from here runs inside the tmux left pane (or plain
    # terminal when tmux is unavailable).  Wrap in try/finally so that
    # _maybe_kill_own_tmux_session fires on ANY exit path.

    # ── Phase 2 — Provider selection (scroll buffer, after tmux) ─────────
    # If _active_provider is still None here, multiple providers are
    # installed and the user hasn't chosen yet.  Show the selection
    # prompt now — inside the tmux left pane scroll buffer — exactly where
    # all other pre-execution prompts (welcome, goal, scope, model) live.
    # Also persist the selection via env var so sub-processes inherit it.
    if _active_provider is None:
        _available_post = detect_available_providers()
        if not _available_post:
            console.print("[red]Error: No supported AI CLI found.[/red]")
            raise SystemExit(1)
        elif len(_available_post) == 1:
            _active_provider = _available_post[0]
        elif headless:
            _active_provider = _available_post[0]
        else:
            _active_provider = _prompt_provider_selection(_available_post)
            # Clear the provider-selection UI before the next screen renders.
            # _prompt_provider_selection uses full_screen=False so its rendered
            # text stays in the scroll buffer.  Without this clear the next
            # screen (welcome, planning prompts, mode selection) starts below
            # the leftover provider-selection text instead of at the top.
            console.clear()
        os.environ["ARCHITECT_PROVIDER"] = _active_provider.name

        # Usability check for the newly selected provider
        if not _active_provider.has_any_models():
            user_cfg = _active_provider.find_user_config(resolved_project)
            if user_cfg is None:
                console.print()
                console.print(
                    f"[yellow]{_active_provider.display_name} may not be configured yet.[/yellow]"
                )
                console.print()
                if _active_provider.name == "opencode":
                    console.print(
                        "Run [bold]opencode[/bold] once to set up a provider, then come back."
                    )
                elif _active_provider.name == "codex":
                    console.print("Set CODEX_API_KEY or run [bold]codex[/bold] to configure.")
                else:
                    console.print("Set ANTHROPIC_API_KEY or run [bold]claude[/bold] to configure.")
                raise SystemExit(1)

    assert _active_provider is not None

    # Widen to str | None now so the type stays consistent whether or not
    # _collect_planning_prompts runs (it can return None for the model).
    execution_model_resolved: str | None = execution_model or None

    try:
        # ── Planning flow ─────────────────────────────────────────────────
        if _needs_planning:
            # ── Welcome screen ────────────────────────────────────────────
            # Shown here — inside the tmux left pane — so the user sees it
            # alongside the dashboard.  Never shown in the original terminal.
            if not headless:
                if _all_done_pre and not plan and not only_task and not from_task:
                    console.print()
                    console.print(
                        f"[bold {ARCHITECT_GREEN}]The Architect[/bold {ARCHITECT_GREEN}]"
                        f"  [grey62]v{__version__}[/grey62]"
                    )
                    console.print()
                    console.print("[#7cc800]✓ All tasks complete.[/#7cc800]")
                    console.print()
                    import questionary as _q

                    choice = _q.select(
                        "What would you like to do?",
                        choices=[
                            _q.Choice("Start a new goal — plan something new", value="plan"),
                            _q.Choice("Exit", value="exit"),
                        ],
                        pointer="›",
                        style=_questionary_style(),
                    ).ask()
                    if choice is None:
                        console.print("[dim]Cancelled.[/dim]")
                        raise SystemExit(0)
                    if choice != "plan":
                        console.print()
                        raise SystemExit(0)
                    console.print()
                    plan = True

                elif _no_tasks_pre and not plan:
                    console.print()
                    console.print(
                        f"[bold {ARCHITECT_GREEN}]The Architect[/bold {ARCHITECT_GREEN}]"
                        f"  [grey62]v{__version__}[/grey62]"
                    )
                    console.print()
                    console.print("[dim]No tasks found in this project.[/dim]")
                    console.print()
                    console.print(
                        "  The Architect works by planning your goal into tasks,\n"
                        "  then executing them autonomously.\n"
                    )
                    import questionary as _q

                    choice = _q.select(
                        "What would you like to do?",
                        choices=[
                            _q.Choice("Start planning — describe what to build", value="plan"),
                            _q.Choice("Exit", value="exit"),
                        ],
                        pointer="›",
                        style=_questionary_style(),
                    ).ask()
                    if choice is None:
                        console.print("[dim]Cancelled.[/dim]")
                        raise SystemExit(0)
                    if choice != "plan":
                        console.print()
                        console.print(
                            "[dim]Exiting. Run [bold]architect --plan[/bold] "
                            "when you're ready.[/dim]"
                        )
                        console.print()
                        raise SystemExit(0)
                    console.print()
                    plan = True

            # Run planning prompts in the scroll buffer (matches original
            # behaviour). _collect_planning_prompts returns the resolved values
            # so they can be forwarded into run_planning_mode (which will then
            # skip the prompts).
            goal_text, scope_text, architect_model, execution_model_resolved = (
                _collect_planning_prompts(
                    resolved_project,
                    config,
                    headless=headless,
                    goal_text=goal_text or "",
                    scope_text=scope_text or "",
                    context_paths=context_paths,
                    architect_model_override=architect_model,
                    execution_model_override=execution_model,
                    provider=_active_provider,
                )
            )

            # Update the planning state with the resolved goal once prompts are
            # done — the dashboard will show the actual goal the user typed.
            try:
                from the_architect.core.monitor_state import write_planning_state

                write_planning_state(resolved_project, goal=goal_text or "")
            except Exception:
                pass

        # ── Mode selection (only when execution will happen) ──────────────
        # Ask about Free / Persistent modes only now — after we know tasks
        # exist and execution will actually run.  If the user just planned,
        # execution follows immediately so this is the right moment.
        #
        # However, if pending tasks exist from a previous run (no planning
        # happened), skip this screen — the resume screen inside _run_main
        # handles mode selection for resume runs.  Showing both screens
        # would be redundant and confusing.
        _pending_pre = [t for t in _tasks_pre if _task_needs_work(t)] if _tasks_pre else []
        _needs_mode_prompt = not (persistent or free_mode) and not headless and not _pending_pre
        if _needs_mode_prompt:
            modes = _prompt_mode_selection(provider=_active_provider)
            # Clear the mode-selection UI before the alternate screen starts.
            # _prompt_mode_selection uses full_screen=False so its rendered
            # text stays in the scroll buffer.  The alternate screen that
            # follows would otherwise start below the leftover mode-selection
            # text rather than at the top of the visible area.
            console.clear()
            if modes.get("persistent"):
                persistent = True
            if modes.get("free"):
                free_mode = True
            if modes.get("token_budget_per_hour"):
                config.token_budget_per_hour = modes["token_budget_per_hour"]

        if persistent:
            config.persistent = True
            config.max_retries = 30
            config.retrospective_rounds = 2

        if free_mode:
            config.free_mode = True

        # Persist interactive settings to architect.toml for next run
        if _needs_mode_prompt:
            from the_architect.config import write_config

            write_config(
                resolved_project,
                {
                    "free_mode": config.free_mode,
                    "persistent": config.persistent,
                    "token_budget_per_hour": config.token_budget_per_hour,
                },
            )

        # ── Enter alternate screen for execution ──────────────────────────
        with alternate_screen():
            _run_main(
                resolved_project,
                plan=plan,
                standalone=standalone,
                from_task=from_task,
                only_task=only_task,
                persistent=persistent,
                free_mode=free_mode,
                no_monitor=no_monitor,
                headless=headless,
                goal_text=goal_text or "",
                scope_text=scope_text or "",
                context_paths=context_paths,
                architect_model=architect_model,
                execution_model=execution_model_resolved,
                _pre_loaded_config=config,
                provider=_active_provider,
            )

    finally:
        # ── Monitor state cleanup ──────────────────────────────────────────
        # If the process exits while the state file still says PLANNING or
        # RUNNING (e.g. Ctrl+C during prompts, crash before execution),
        # write KILLED so the dashboard pane doesn't stay stuck forever.
        # Only overwrite if the current status is a non-terminal state —
        # never clobber DONE or FAILED from a successful run.
        try:
            from the_architect.core.monitor_state import (
                RUN_STATUS_DONE,
                RUN_STATUS_FAILED,
                RUN_STATUS_KILLED,
                read_monitor_state,
                write_monitor_state,
            )

            current_state = read_monitor_state(resolved_project)
            current_status = current_state.get("status", "") if current_state else ""
            if current_status not in (RUN_STATUS_DONE, RUN_STATUS_FAILED, RUN_STATUS_KILLED):
                write_monitor_state(
                    resolved_project,
                    {
                        "status": RUN_STATUS_KILLED,
                        "project_name": resolved_project.name,
                        "tasks": current_state.get("tasks", []) if current_state else [],
                        "current_task_id": None,
                    },
                )
        except Exception:
            pass

        # ── Tmux session teardown ─────────────────────────────────────────
        # When the runner was launched inside a tmux session by The Architect
        # (i.e. we are in the left pane and --no-monitor was injected), kill
        # the entire session when the run ends so the user lands back in their
        # original terminal cleanly.  Without this, the user would be left
        # inside the tmux session staring at a dead left pane and a live
        # dashboard pane — requiring a second Ctrl+C to fully exit.
        #
        # Fires on ALL exit paths: normal completion, Ctrl+C during prompts,
        # Ctrl+C during execution, or any SystemExit.
        # Does NOT fire on detach (Ctrl+B D) — detach doesn't exit the process.
        # Only kills sessions matching our naming convention
        # ("architect-<project-name>") so unrelated sessions are never touched.
        _maybe_kill_own_tmux_session(resolved_project)


def _run_main(
    project: Path,
    plan: bool = False,
    standalone: str = "",
    from_task: str = "",
    only_task: str = "",
    persistent: bool = False,
    free_mode: bool = False,
    no_monitor: bool = False,
    headless: bool = False,
    goal_text: str = "",
    scope_text: str = "",
    context_paths: tuple[Path, ...] = (),
    architect_model: str = "",
    execution_model: str | None = "",
    _pre_loaded_config: ArchitectConfig | None = None,
    provider: ArchitectProvider | None = None,
) -> None:
    """Main flow: planning, execution, and retrospective review.

    Flow:
        Planning → Execution → Retrospective 1 → Execution → Retrospective 2 → Execution → Done

    Retrospective rounds are skipped when:
    - ``config.retrospective_rounds`` is 0
    - ``--only`` is used (targeted single-task run)
    - The reviewer finds no issues (no new tasks created)

    With ``--persistent``:
    - ``max_retries`` is raised to 50 (persistence wins)
    - ``retrospective_rounds`` is set to 2 (deeper review)

    With ``--free``:
    - ``free_mode`` is set to True on the config
    - The Architect fetches free-tier OpenRouter models and rotates through them
    - On rate limit, switches to the next free model
    - When all free models are exhausted, falls back to default

    With ``--headless``:
    - All interactive prompts are skipped
    - Required values must be provided via flags or env vars
    - Missing required values cause a clear error and non-zero exit

    Args:
        project: The project root directory.
        plan: Force planning mode.
        standalone: Bypass opencode.json and use this model directly.
        from_task: Resume from a specific task prefix.
        only_task: Run a single task only.
        persistent: Persistent mode (30 retries, 2 retrospective rounds).
        free_mode: Use free-tier OpenRouter models.
        no_monitor: Skip tmux monitoring.
        headless: Headless mode (no interactive prompts).
        goal_text: Pre-supplied planning goal.
        scope_text: Pre-supplied task scope.
        context_paths: Pre-supplied context file paths.
        architect_model: Pre-supplied architect model override.
        execution_model: Pre-supplied execution model override.  ``None``
            means the prompt was already shown by ``_collect_planning_prompts``
            and the user chose the opencode default (empty string would also
            work, but ``None`` is the explicit sentinel passed through).
        _pre_loaded_config: Pre-loaded and mutated config from ``main()``.
            When provided, mode-selection prompts and opencode checks are
            skipped because they already ran before the alternate screen.
    """
    # Use the pre-loaded config when available (interactive prompts and
    # opencode checks already ran in main() before the alternate screen).
    if _pre_loaded_config is not None:
        config = _pre_loaded_config
    else:
        config = load_config(project)

        if standalone:
            config.standalone_mode = standalone

        # ── Guard: standalone_mode must be compatible with the active provider ─
        # standalone_mode is persisted to architect.toml.  If it was set during
        # a previous OpenCode/free-mode run (e.g. "openrouter/z-ai/glm-5.1"),
        # it will be silently passed to Claude Code on the next run — which
        # doesn't understand OpenRouter model IDs and will fail.
        # Clear it and warn rather than let it poison the run.
        if config.standalone_mode and provider is not None:
            from the_architect.core.claude_code_provider import ClaudeCodeProvider

            if isinstance(provider, ClaudeCodeProvider):
                sm = config.standalone_mode
                # OpenRouter model IDs always start with "openrouter/"
                # or contain a "/" that indicates a non-Anthropic namespace.
                is_openrouter = sm.startswith("openrouter/") or (
                    "/" in sm and not sm.startswith("claude")
                )
                if is_openrouter:
                    console.print(
                        f"[yellow]⚠  standalone_mode '{sm}' is an OpenRouter model — "
                        f"not compatible with Claude Code. Clearing it.[/yellow]"
                    )
                    logger.warning(
                        f"standalone_mode '{sm}' is incompatible with Claude Code "
                        f"(OpenRouter model ID) — clearing for this run"
                    )
                    config.standalone_mode = ""
                    write_config(project, {"standalone_mode": ""})

        # ── Interactive mode selection (only when no mode flags passed) ─────
        # Power users who pass --free or --persistent skip the prompt entirely.
        # In headless mode, skip all interactive prompts.
        # If pending tasks exist, skip — the resume screen below handles it.
        tasks_dir_pre = project / config.tasks_dir.name
        _tasks_pre_run = discover_tasks(tasks_dir_pre)
        progress_file_pre = project / config.progress_file.name
        _tasks_pre_run = _filter_and_set_status(_tasks_pre_run, progress_file_pre)
        _pending_pre_run = (
            [t for t in _tasks_pre_run if _task_needs_work(t)] if _tasks_pre_run else []
        )

        any_mode_flag = persistent or free_mode
        _needs_mode_prompt = not any_mode_flag and not headless and not _pending_pre_run
        if _needs_mode_prompt:
            modes = _prompt_mode_selection(provider=provider)
            # Clear the mode-selection UI before the execution header renders.
            # _prompt_mode_selection uses full_screen=False so its rendered
            # text stays in the buffer.  Without this clear the execution
            # header starts below the leftover mode-selection text.
            console.clear()
            if modes.get("persistent"):
                persistent = True
            if modes.get("free"):
                free_mode = True
            if modes.get("token_budget_per_hour"):
                config.token_budget_per_hour = modes["token_budget_per_hour"]

        if persistent:
            config.persistent = True
            config.max_retries = 30
            config.retrospective_rounds = 2

        if free_mode:
            config.free_mode = True

        # Persist interactive settings to architect.toml for next run
        if _needs_mode_prompt:
            from the_architect.config import write_config

            write_config(
                project,
                {
                    "free_mode": config.free_mode,
                    "persistent": config.persistent,
                    "token_budget_per_hour": config.token_budget_per_hour,
                },
            )

        # Provider already checked in main() — no need to re-check here
        pass

    tasks_dir = project / config.tasks_dir.name
    tasks = discover_tasks(tasks_dir)
    progress_file = project / config.progress_file.name
    tasks = _filter_and_set_status(tasks, progress_file)

    # Track the original goal for retrospective context
    original_goal = ""

    # Apply --only
    if only_task:
        matched = [t for t in tasks if t.prefix.upper() == only_task.upper()]
        if not matched:
            console.print(f"[red]No task found with prefix '{only_task}'.[/red]")
            console.print("[dim]Run 'architect list' to see available tasks.[/dim]")
            raise SystemExit(1)
        tasks = matched

    # Apply --from
    elif from_task:
        prefixes = [t.prefix.upper() for t in tasks]
        start = from_task.upper()
        if start not in prefixes:
            console.print(f"[red]No task found with prefix '{from_task}'.[/red]")
            console.print("[dim]Run 'architect list' to see available tasks.[/dim]")
            raise SystemExit(1)
        idx = prefixes.index(start)
        tasks = tasks[idx:]

    # Decide mode
    # A task is "done" from the loop's perspective when its status is
    # terminal (DONE or FAILED) — a FAILED task must not be silently re-run
    # as if it were pending.  Only a human-triggered `architect retry` or a
    # reviewer R-task can resurrect a failed task for re-execution.
    all_done = bool(tasks) and all(_task_is_terminal(t) for t in tasks)
    no_tasks = not tasks

    if plan or no_tasks or (all_done and not only_task and not from_task):
        if all_done and not plan and not only_task and not from_task:
            console.print(
                "[#7cc800]✓ All tasks complete.[/#7cc800]  "
                "[dim]Use --plan to start a new goal.[/dim]"
            )
            raise SystemExit(0)
        run_planning_mode(
            project,
            config,
            headless=headless,
            goal_text=goal_text,
            scope_text=scope_text,
            context_paths=context_paths,
            architect_model_override=architect_model,
            execution_model_override=execution_model,
            # Skip pending-task guard when prompts were already collected
            # before the alternate screen in main().
            _skip_pending_guard=(_pre_loaded_config is not None),
            provider=provider,
        )
        # After planning, reload and execute
        tasks = discover_tasks(tasks_dir)
        tasks = _filter_and_set_status(tasks, progress_file)
        if not tasks:
            console.print("[red]Planning did not create any tasks.[/red]")
            raise SystemExit(1)

    # Try to read the original goal for retrospective context
    original_goal = _read_goal_from_instructions(tasks_dir)

    # Filter to pending.  Terminal tasks (Done or Failed) are NOT pending —
    # a failed task requires explicit human/reviewer action before the
    # executor tries it again.
    pending = [t for t in tasks if _task_needs_work(t)]
    if not pending:
        console.print("[#7cc800]✓ All selected tasks are already resolved.[/#7cc800]")
        raise SystemExit(0)

    # ── Resume screen: confirm execution or replan ──────────────────────
    # When pending tasks exist and we're not in headless/plan mode, show
    # a resume screen so the user can confirm, adjust settings, or replan.
    # Skip if mode flags were already set via CLI (--free, --persistent).
    if not headless and not plan and not only_task and not from_task:
        any_mode_flag = persistent or free_mode
        if not any_mode_flag:
            resume = _prompt_resume_screen(pending, config, provider=provider)
            # Clear the resume-screen UI before the next content renders.
            # _prompt_resume_screen uses full_screen=False so its rendered
            # text stays in the scroll buffer.  Without this clear the
            # execution header (or planning prompts) would start below the
            # leftover resume-screen text instead of at the top.
            console.clear()
            if resume.get("action") == "replan":
                run_planning_mode(
                    project,
                    config,
                    headless=headless,
                    goal_text=goal_text,
                    scope_text=scope_text,
                    context_paths=context_paths,
                    architect_model_override=architect_model,
                    execution_model_override=execution_model,
                    provider=provider,
                )
                # After planning, reload and execute
                tasks = discover_tasks(tasks_dir)
                tasks = _filter_and_set_status(tasks, progress_file)
                if not tasks:
                    console.print("[red]Planning did not create any tasks.[/red]")
                    raise SystemExit(1)
                pending = [t for t in tasks if _task_needs_work(t)]
                if not pending:
                    console.print("[#7cc800]✓ All selected tasks are already done.[/#7cc800]")
                    raise SystemExit(0)
            else:
                # Apply settings from resume screen — always assign both on AND
                # off; if-True guards here would silently keep old config values
                # when the user toggles something off.
                free_mode = bool(resume.get("free", False))
                config.free_mode = free_mode

                persistent = bool(resume.get("persistent", False))
                config.persistent = persistent
                if persistent:
                    config.max_retries = 30
                    config.retrospective_rounds = 2

                config.token_budget_per_hour = int(resume.get("token_budget_per_hour") or 0)

                # Persist settings to architect.toml for next run
                from the_architect.config import write_config

                write_config(
                    project,
                    {
                        "free_mode": config.free_mode,
                        "persistent": config.persistent,
                        "token_budget_per_hour": config.token_budget_per_hour,
                    },
                )

    # Warn if tasks/INSTRUCTIONS.md is missing — not fatal, just informational
    instructions_md = tasks_dir / "INSTRUCTIONS.md"
    if not instructions_md.exists():
        console.print(
            "[yellow]⚠  tasks/INSTRUCTIONS.md not found — "
            "agents will have less project context. "
            "Run --plan to regenerate.[/yellow]"
        )

    # ── Free mode: validate + fetch free models from OpenRouter ────────────
    # This runs BEFORE the header print so the "free mode" label is only
    # shown when free mode is actually active.
    free_rotator = None
    if config.free_mode:
        # Guard: free tier only works with OpenCode + OpenRouter.
        # Clear the flag immediately if the provider doesn't support it,
        # so the header never shows "free mode" when it won't be used.
        if provider is not None and not provider.supports_free_tier():
            console.print()
            console.print(
                f"[yellow]⚠  Free Tier is not supported with {provider.display_name} — "
                f"falling back to default model.[/yellow]"
            )
            config.free_mode = False
            from the_architect.config import write_config

            write_config(project, {"free_mode": False})
        else:
            from the_architect.core.free_models import FreeModelRotator

            console.print("[dim]Fetching free-tier models from OpenRouter...[/dim]", end="\r")
            free_rotator = FreeModelRotator()
            asyncio.run(free_rotator.fetch_free_models())
            console.print(" " * 60, end="\r")

            if free_rotator.total_count == 0:
                console.print(
                    "[yellow]⚠  No free models found on OpenRouter — "
                    "falling back to default model.[/yellow]"
                )
                config.free_mode = False
                free_rotator = None
                from the_architect.config import write_config

                write_config(project, {"free_mode": False})
            else:
                console.print(
                    f"[{ARCHITECT_GREEN}]{free_rotator.total_count} free model(s) "
                    f"available[/{ARCHITECT_GREEN}]  "
                    f"[dim]starting with {free_rotator.current_model}[/dim]"
                )

    # Show what we're about to run (after free mode validation so the label is accurate)
    console.print()
    mode_label = "  [grey62]free mode[/grey62]" if config.free_mode else ""
    console.print(
        f"[bold {ARCHITECT_GREEN}]The Architect[/bold {ARCHITECT_GREEN}]  "
        f"[grey62]v{__version__}[/grey62]  "
        f"[{ARCHITECT_GREEN}]{len(pending)} task(s) to run[/{ARCHITECT_GREEN}]"
        f"{mode_label}"
    )
    # Show the execution model — resolve from provider if no explicit override
    exec_model_display = (
        execution_model if isinstance(execution_model, str) else (execution_model or "")
    )
    if not exec_model_display and provider is not None:
        try:
            exec_model_display = provider.get_resolved_model(
                project, config.execution_agent or "build"
            )
        except Exception:
            pass
    if exec_model_display:
        console.print(
            f"[grey62]Model:[/grey62] [{ARCHITECT_GREEN}]{exec_model_display}[/{ARCHITECT_GREEN}]"
        )

    # Ensure provider setup only when a provider has already been selected.
    # Some test and headless control-flow paths intentionally run without a
    # real provider object and should not auto-detect local CLIs here.
    if provider is not None:
        provider.ensure_setup(project, config)
    setup_logging(config.log_dir)

    # ── Monitor state writer (feeds the tmux dashboard) ─────────────────
    # Always create the writer — even when --no-monitor is set (which only
    # controls tmux launching, not state-file writes).  The dashboard pane
    # reads this file to render live status; without it the state stays
    # stuck at PLANNING forever.
    monitor_writer: MonitorStateWriter | None = None
    try:
        monitor_writer = MonitorStateWriter(
            project_dir=project,
            tasks=tasks,
            free_rotator=free_rotator,
            max_retries=config.max_retries,
        )
    except Exception as _mw_exc:
        logger.debug(f"Monitor writer init failed (non-fatal): {_mw_exc!r}")
        monitor_writer = None

    # ── Initial execution ──────────────────────────────────────────────
    all_results: list[TaskResult] = []
    run_start = time.time()

    try:
        success, results, exec_duration = asyncio.run(
            _run_tasks_raw(
                project,
                config,
                tasks,
                free_rotator=free_rotator,
                monitor_writer=monitor_writer,
                provider=provider,
            )
        )
    except RuntimeError as e:
        console.print(f"\n[red]Error: {e}[/red]")
        raise SystemExit(1)
    except Exception as e:
        # Catch-all for fire-and-forget robustness: any unexpected exception
        # (asyncio errors, OS errors, etc.) must not silently crash the process
        # without telling the user what happened.
        console.print(f"\n[red]Unexpected error during task execution: {e!r}[/red]")
        logger.error(f"Unexpected error during task execution: {e!r}")
        raise SystemExit(1)

    all_results.extend(results)

    # ── Retrospective rounds ───────────────────────────────────────────
    # Skip retrospective when using --only (targeted single-task run)
    # or when retrospective_rounds is 0
    should_run_retrospective = config.retrospective_rounds > 0 and not only_task
    collected_retrospective_rounds: list[RetrospectiveRound] = []

    if should_run_retrospective:
        for round_num in range(1, config.retrospective_rounds + 1):
            console.print()
            console.print(
                f"[bold {ARCHITECT_GREEN}]══ Retrospective {round_num}/"
                f"{config.retrospective_rounds}[/bold {ARCHITECT_GREEN}]  "
                f"[dim]reviewing completed work[/dim]"
            )
            console.print()

            retro_request = RetrospectiveRequest(
                round_number=round_num,
                project_dir=project,
                original_goal=original_goal,
                model_override=architect_model or None,
            )

            log_dir = config.log_dir
            log_dir.mkdir(parents=True, exist_ok=True)
            retro_log = log_dir / f"reviewer_round{round_num}.log"

            retro_start = time.time()
            try:
                retro_result = asyncio.run(
                    run_retrospective(retro_request, config, log_path=retro_log, provider=provider)
                )
            except Exception as e:
                console.print(f"[red]Retrospective round {round_num} failed: {e}[/red]")
                break
            retro_duration = time.time() - retro_start

            # Collect retrospective round summary for SUCCESS.md
            collected_retrospective_rounds.append(
                RetrospectiveRound(
                    round_number=round_num,
                    issues_found=retro_result.issues_found,
                    fixes_planned=retro_result.fixes_planned,
                    tasks_created=retro_result.tasks_created,
                    duration_seconds=retro_duration,
                )
            )

            if not retro_result.tasks_created:
                console.print()
                console.print(
                    f"[#7cc800]✓ Retrospective {round_num} — no issues found[/#7cc800]  "
                    f"[dim]build is clean[/dim]"
                )
                # No issues found — skip remaining rounds
                break

            console.print()
            console.print(
                f"[yellow]⚠ Retrospective {round_num} — "
                f"{len(retro_result.tasks_created)} issue(s) found[/yellow]  "
                f"[dim]{retro_result.summary}[/dim]"
            )

            # Discover and execute the new R-prefixed tasks
            retro_tasks = discover_tasks(tasks_dir)
            retro_tasks = _filter_and_set_status(retro_tasks, progress_file)
            retro_pending = [t for t in retro_tasks if _task_needs_work(t)]

            # Add retrospective tasks to the monitor writer so they appear
            # on the dashboard
            if monitor_writer is not None and retro_pending:
                try:
                    monitor_writer.add_tasks(retro_pending)
                except Exception:
                    pass

            if not retro_pending:
                # All fix-up tasks already done (unlikely but possible)
                continue

            console.print()
            console.print(
                f"[bold {ARCHITECT_GREEN}]The Architect[/bold {ARCHITECT_GREEN}]  "
                f"[{ARCHITECT_GREEN}]{len(retro_pending)} fix-up task(s) from "
                f"retrospective {round_num}[/{ARCHITECT_GREEN}]"
            )

            try:
                retro_success, retro_results, retro_duration = asyncio.run(
                    _run_tasks_raw(
                        project,
                        config,
                        retro_tasks,
                        free_rotator=free_rotator,
                        monitor_writer=monitor_writer,
                        provider=provider,
                    )
                )
            except RuntimeError as e:
                console.print(f"\n[red]Error: {e}[/red]")
                break
            except Exception as e:
                console.print(
                    f"\n[red]Unexpected error during retrospective execution: {e!r}[/red]"
                )
                logger.error(f"Unexpected error during retrospective execution: {e!r}")
                break

            all_results.extend(retro_results)

            if not retro_success:
                console.print(f"[red]Fix-up execution failed after retrospective {round_num}[/red]")
                break

    if not only_task and all_results and provider is not None:
        reassessment_log_dir = config.log_dir
        reassessment_log_dir.mkdir(parents=True, exist_ok=True)
        for result in _task_results_needing_reassessment(all_results):
            try:
                reassessment = asyncio.run(
                    run_task_reassessment(
                        project_dir=project,
                        provider=provider,
                        config=config,
                        completed_task=result.prefix,
                        outcome_summary=result.outcome_summary,
                        original_goal=original_goal,
                        model_override=architect_model or None,
                        log_path=reassessment_log_dir / f"{result.prefix.lower()}_reassess.log",
                    )
                )
                if reassessment.tasks_updated:
                    console.print()
                    console.print(
                        f"[yellow]↺ Reassessed after {result.prefix}[/yellow]  "
                        f"[dim]{reassessment.summary}[/dim]"
                    )
            except Exception as exc:
                logger.warning(f"Post-task reassessment failed for {result.prefix}: {exc!r}")

    # ── Final summary ──────────────────────────────────────────────────
    # Write final monitor state before generating the summary
    if monitor_writer is not None:
        try:
            monitor_writer.on_run_done(success)
        except Exception:
            pass

    try:
        total_duration = time.time() - run_start
        total_tokens = TokenUsage()
        for r in all_results:
            total_tokens = total_tokens + r.tokens
        retro_rounds = collected_retrospective_rounds if collected_retrospective_rounds else None
        success_path = write_success_md(
            project, all_results, total_duration, total_tokens, retrospective_rounds=retro_rounds
        )

        # Print terminal summary
        print_success_summary(
            all_results,
            total_duration,
            total_tokens,
            success_path,
            retrospective_rounds=retro_rounds,
        )
    except Exception as exc:
        # Summary generation must not crash the process — log and continue.
        logger.error(f"Error generating final summary: {exc!r}")
        console.print(f"\n[red]Error generating final summary: {exc}[/red]")

    # Pause so the user can read the summary before the screen closes.
    # Skipped in headless mode (no interactive terminal).
    if not headless:
        try:
            console.print("[dim]Press any key to exit…[/dim]")
            import sys as _sys
            import termios as _termios
            import tty as _tty

            fd = _sys.stdin.fileno()
            old = _termios.tcgetattr(fd)
            try:
                _tty.setraw(fd)
                _sys.stdin.read(1)
            finally:
                _termios.tcsetattr(fd, _termios.TCSADRAIN, old)
        except Exception:
            # Non-interactive terminal (pipe, CI, etc.) — skip the pause silently.
            pass

    raise SystemExit(0 if success else 1)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


@main.command(name="list")
@click.option(
    "--project",
    "-p",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
)
def list_cmd(project: Path | None) -> None:
    """Show all tasks and their status."""
    _setup_loguru()
    proj = (project or Path.cwd()).resolve()
    config = load_config(proj)
    tasks_dir = proj / config.tasks_dir.name

    if not tasks_dir.exists():
        console.print("[dim]No tasks directory found.[/dim]")
        return

    tasks = discover_tasks(tasks_dir)
    progress_file = proj / config.progress_file.name

    if not tasks:
        console.print("[dim]No tasks found.[/dim]")
        return

    from rich import box
    from rich.table import Table

    table = Table(box=box.SIMPLE, show_header=True, header_style="dim", padding=(0, 1))
    table.add_column("Task", width=8)
    table.add_column("Title")
    table.add_column("Status", width=10)

    for task in tasks:
        status = task_status(progress_file, task.prefix)
        if status == "Done":
            status_cell = "[#7cc800]✓ Done[/#7cc800]"
        elif status == "Failed":
            status_cell = "[red]✗ Failed[/red]"
        elif status == "Blocked":
            status_cell = "[yellow]⏸ Blocked[/yellow]"
        else:
            status_cell = "[dim]○ Pending[/dim]"
        table.add_row(task.prefix, task.title or task.name, status_cell)

    console.print()
    console.print(table)


@main.command()
@click.option("--task", "-t", required=True, help="Task prefix to retry (e.g. T03)")
@click.option(
    "--project",
    "-p",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
)
def retry(task: str, project: Path | None) -> None:
    """Retry a specific task (reset its terminal status and re-run).

    Handles both Done and Failed rows — flipping either back to Pending
    so the main loop will pick the task up again.  This is the intended
    escape hatch when a task has been marked Failed by the runner's
    retry exhaustion and the user wants to try again after fixing an
    external issue (e.g. restoring network access, fixing credentials).
    """
    _setup_loguru()
    proj = (project or Path.cwd()).resolve()
    config = load_config(proj)
    progress_file = proj / config.progress_file.name

    if not task_is_resolved(progress_file, task):
        console.print(f"[dim]Task {task} is not in a terminal state — running now.[/dim]")

    # Reset in PROGRESS.md if it was Done, Failed, or Blocked.  Use the
    # authoritative reconciler so we don't have to know the old status.
    if progress_file.exists():
        current = task_status(progress_file, task)
        if current in ("Done", "Failed", "Blocked"):
            if reconcile_task_status(progress_file, task, "Pending", completed="—"):
                console.print(f"[dim]Reset {task} ({current} → Pending).[/dim]")

    tasks_dir = proj / config.tasks_dir.name
    all_tasks = discover_tasks(tasks_dir)
    task_obj = next((t for t in all_tasks if t.prefix.upper() == task.upper()), None)

    if not task_obj:
        console.print(f"[red]Task {task} not found in tasks/.[/red]")
        raise SystemExit(1)

    from the_architect.core.provider import ProviderNotFoundError, detect_provider

    try:
        _run_provider = detect_provider("auto")
    except ProviderNotFoundError:
        console.print("[red]Error: No supported AI CLI found.[/red]")
        raise SystemExit(1)
    _run_provider.ensure_setup(proj, config)
    setup_logging(config.log_dir)
    asyncio.run(run_task(task_obj, config))


@main.command()
@click.option("--task", "-t", required=True, help="Task prefix to skip (e.g. T03)")
@click.option(
    "--project",
    "-p",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
)
def skip(task: str, project: Path | None) -> None:
    """Mark a task as Done without running it."""
    _setup_loguru()
    proj = (project or Path.cwd()).resolve()
    config = load_config(proj)
    progress_file = proj / config.progress_file.name

    if not progress_file.exists():
        console.print("[red]PROGRESS.md not found.[/red]")
        console.print("[dim]Run [bold]architect --plan[/bold] first to create tasks.[/dim]")
        raise SystemExit(1)

    content = progress_file.read_text(encoding="utf-8")
    updated = replace_task_status(content, task, "Pending", "Done")
    if updated != content:
        progress_file.write_text(updated, encoding="utf-8")
        console.print(f"[#7cc800]✓ Task {task} marked as Done.[/#7cc800]")
    elif task_is_done(progress_file, task):
        console.print(f"[dim]Task {task} is already Done.[/dim]")
    else:
        console.print(f"[red]Task {task} not found in PROGRESS.md.[/red]")
        raise SystemExit(1)


@main.command()
@click.option(
    "--project",
    "-p",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
)
def reset(project: Path | None) -> None:
    """Reset PROGRESS.md to initial state."""
    _setup_loguru()
    from the_architect.core.progress import PROGRESS_TEMPLATE

    proj = (project or Path.cwd()).resolve()
    config = load_config(proj)
    progress_file = config.progress_file

    if not progress_file.exists():
        console.print("[red]PROGRESS.md not found.[/red]")
        console.print("[dim]Run [bold]architect --plan[/bold] first to create tasks.[/dim]")
        raise SystemExit(1)

    if click.confirm("This will reset PROGRESS.md to initial state. Continue?"):
        content = PROGRESS_TEMPLATE.format(
            tasks_completed=0,
            next_task="T00",
            task_rows="",
            current_state="Reset at user's request.",
            last_summary="",
        )
        progress_file.write_text(content, encoding="utf-8")
        console.print("[#7cc800]✓ PROGRESS.md reset.[/#7cc800]")
    else:
        console.print("[dim]Cancelled.[/dim]")


@main.command()
@click.option(
    "--project",
    "-p",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
)
def cancel(project: Path | None) -> None:
    """Remove a stale lock file so the next run is not blocked.

    The Architect writes a lock file at .architect/runner.lock when it starts.
    If The Architect was killed (Ctrl+C, terminal close, crash) without cleaning
    up, the lock file remains and blocks the next run.

    This command removes the lock file so you can run architect again.
    It does NOT kill any running process — use Ctrl+C or your OS tools
    for that.
    """
    _setup_loguru()
    import os

    proj = (project or Path.cwd()).resolve()
    lock_path = proj / ".architect" / "runner.lock"

    if not lock_path.exists():
        console.print("[dim]No lock file found — nothing to cancel.[/dim]")
        console.print(f"[dim](looked in {lock_path})[/dim]")
        return

    # Read PID from lock file
    try:
        pid_str = lock_path.read_text(encoding="utf-8").strip()
        pid = int(pid_str)
    except (OSError, ValueError):
        pid = None

    # Check if the process is still alive
    process_alive = False
    if pid is not None:
        try:
            # Signal 0 = existence check only — does NOT kill the process.
            # Cross-platform: works on Windows, Linux, and macOS (Python 3.x).
            os.kill(pid, 0)
            process_alive = True
        except (ProcessLookupError, PermissionError, OSError):
            # ProcessLookupError on Linux/macOS, plain OSError on Windows —
            # both indicate the process is gone.
            process_alive = False

    # Show what we found
    if pid is not None and process_alive:
        console.print(f"[yellow]⚠  The Architect is still running (PID {pid}).[/yellow]")
        console.print()
        import signal
        import sys as _sys

        _on_windows = _sys.platform == "win32"
        _term_label = "terminate" if _on_windows else "SIGTERM"
        _term_detail = (
            "  [dim](on Windows this terminates the process immediately)[/dim]"
            if _on_windows
            else ""
        )
        if click.confirm(f"  Send {_term_label} to PID {pid} to stop it?", default=False):
            if _term_detail:
                console.print(_term_detail)
            try:
                if _on_windows:
                    # On Windows, SIGTERM maps to TerminateProcess (immediate kill).
                    # Use it explicitly so the intent is clear.
                    os.kill(pid, signal.SIGTERM)
                    console.print(f"[dim]Terminated PID {pid}.[/dim]")
                else:
                    os.kill(pid, signal.SIGTERM)
                    console.print(f"[dim]Sent SIGTERM to PID {pid}.[/dim]")
            except (ProcessLookupError, PermissionError) as sig_err:
                console.print(f"[yellow]Could not terminate process: {sig_err}[/yellow]")
        else:
            console.print(
                "[dim]Process left running. Use Ctrl+C in the original terminal to stop it.[/dim]"
            )
        console.print()
    elif pid is not None:
        console.print(f"[dim]Stale lock found (PID {pid} is no longer running).[/dim]")
    else:
        console.print("[dim]Lock file found but PID could not be read — removing.[/dim]")

    # Remove the lock
    try:
        lock_path.unlink()
        console.print("[#7cc800]✓ Lock removed.[/#7cc800]")
        console.print("[dim]You can now run [bold]architect[/bold] again.[/dim]")
    except OSError as e:
        console.print(f"[red]Failed to remove lock file: {e}[/red]")
        raise SystemExit(1)


@main.command(name="status")
@click.option(
    "--project",
    "-p",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
)
def status_cmd(project: Path | None) -> None:
    """Show current run state, circuit breaker, and token budget.

    Displays:
    - Active lock file (is a run in progress?)
    - Task list with Done/Pending status
    - Circuit breaker state per task
    - Token budget usage (if configured)
    - Log directory location
    """
    _setup_loguru()
    import json as _json

    proj = (project or Path.cwd()).resolve()
    config = load_config(proj)

    console.print()
    console.print(
        f"[bold {ARCHITECT_GREEN}]The Architect[/bold {ARCHITECT_GREEN}]  [dim]{proj}[/dim]"
    )
    console.print()

    # ── Lock file ────────────────────────────────────────────────────────
    lock_path = proj / ".architect" / "runner.lock"
    if lock_path.exists():
        try:
            pid = int(lock_path.read_text(encoding="utf-8").strip())
            try:
                # Signal 0 = existence check only — does NOT kill the process.
                # Cross-platform: works on Windows, Linux, and macOS (Python 3.x).
                os.kill(pid, 0)
                console.print(f"[yellow]● Running[/yellow]  PID {pid}")
            except (ProcessLookupError, PermissionError, OSError):
                # ProcessLookupError on Linux/macOS, plain OSError on Windows —
                # both indicate the process is gone.
                console.print(
                    "[dim]○ Not running[/dim]  [dim](stale lock — run architect cancel)[/dim]"
                )
        except (OSError, ValueError):
            console.print("[dim]○ Not running[/dim]")
    else:
        console.print("[dim]○ Not running[/dim]")

    console.print()

    # ── Tasks ────────────────────────────────────────────────────────────
    tasks_dir = proj / config.tasks_dir.name
    progress_file = proj / config.progress_file.name

    if not tasks_dir.exists():
        console.print(
            "[dim]No tasks directory found. Run [bold]architect --plan[/bold] to start.[/dim]"
        )
    else:
        tasks = discover_tasks(tasks_dir)
        if not tasks:
            console.print("[dim]No tasks found.[/dim]")
        else:
            from rich import box
            from rich.table import Table

            table = Table(box=box.SIMPLE, show_header=True, header_style="dim", padding=(0, 1))
            table.add_column("Task", width=8)
            table.add_column("Title")
            table.add_column("Status", width=10)

            done_count = 0
            for task in tasks:
                status = task_status(progress_file, task.prefix)
                if status == "Done":
                    done_count += 1
                    status_str = "[#7cc800]✓ Done[/#7cc800]"
                elif status == "Failed":
                    status_str = "[red]✗ Failed[/red]"
                elif status == "Blocked":
                    status_str = "[yellow]⏸ Blocked[/yellow]"
                else:
                    status_str = "[dim]○ Pending[/dim]"
                table.add_row(task.prefix, task.title or task.name, status_str)

            console.print(table)
            console.print(f"[dim]{done_count}/{len(tasks)} tasks complete[/dim]")

    console.print()

    # ── Circuit breaker ──────────────────────────────────────────────────
    circuit_file = proj / ".architect" / "circuit.json"
    if circuit_file.exists():
        try:
            circuit_data = _json.loads(circuit_file.read_text(encoding="utf-8"))
            open_tasks = [
                (tid, s)
                for tid, s in circuit_data.items()
                if s.get("state") in ("OPEN", "HALF_OPEN")
            ]
            if open_tasks:
                console.print("[yellow]Circuit breaker:[/yellow]")
                for tid, s in open_tasks:
                    state = s.get("state", "?")
                    no_prog = s.get("consecutive_no_progress", 0)
                    same_err = s.get("consecutive_same_error", 0)
                    console.print(
                        f"  [yellow]{tid}[/yellow]  {state}  "
                        f"[dim]no_progress={no_prog}  same_error={same_err}[/dim]"
                    )
                console.print()
        except (OSError, _json.JSONDecodeError):
            pass

    # ── Token budget ─────────────────────────────────────────────────────
    if config.token_budget_per_hour > 0:
        console.print(
            f"[dim]Token budget:[/dim]  {config.token_budget_per_hour:,} tokens/hour  "
            "[dim](tracked per run — resets on restart)[/dim]"
        )
        console.print()

    # ── Logs ─────────────────────────────────────────────────────────────
    log_dir = config.log_dir  # already resolved as absolute path by load_config
    if log_dir.exists():
        log_files = sorted(log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
        if log_files:
            console.print(f"[dim]Logs:[/dim]  {log_dir}")
            for lf in log_files[:5]:
                size_kb = lf.stat().st_size // 1024
                console.print(f"  [dim]{lf.name}  ({size_kb} KB)[/dim]")
            if len(log_files) > 5:
                console.print(f"  [dim]… and {len(log_files) - 5} more[/dim]")
            console.print()


@main.command(name="init")
@click.option(
    "--project",
    "-p",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Directory to initialise (default: current directory)",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing files",
)
def init_cmd(project: Path | None, force: bool) -> None:
    """Initialise a project directory for The Architect.

    Creates:
    - AGENTS.md  — project rules for the build agent (edit this!)
    - architect.toml  — configuration with commented defaults

    Safe to run in existing projects — will not overwrite unless --force.
    """
    _setup_loguru()
    proj = (project or Path.cwd()).resolve()
    proj.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    skipped: list[str] = []

    # ── AGENTS.md ────────────────────────────────────────────────────────
    agents_md = proj / "AGENTS.md"
    if agents_md.exists() and not force:
        skipped.append("AGENTS.md")
    else:
        agents_md.write_text(
            "# Project Rules\n\n"
            "> The Architect reads this file before every task.\n"
            "> Add your project's conventions, constraints, and architecture here.\n\n"
            "## Stack\n\n"
            "<!-- e.g. Python 3.12, FastAPI, PostgreSQL -->\n\n"
            "## Conventions\n\n"
            "<!-- e.g. Use loguru for logging. No print(). Type hints on all functions. -->\n\n"
            "## Constraints\n\n"
            "<!-- e.g. Never modify files outside src/. All tests must pass before Done. -->\n\n"
            "## Architecture\n\n"
            "<!-- Key decisions and folder structure -->\n",
            encoding="utf-8",
        )
        created.append("AGENTS.md")

    # ── architect.toml ───────────────────────────────────────────────────
    toml_path = proj / "architect.toml"
    if toml_path.exists() and not force:
        skipped.append("architect.toml")
    else:
        toml_path.write_text(
            "# The Architect configuration\n"
            "# Run 'architect config' to see all options.\n\n"
            "[architect]\n"
            "# max_retries = 3            # retry attempts per task\n"
            "# retry_pause = 30           # seconds between retries\n"
            "# pause_between_tasks = 10   # seconds between tasks\n"
            "# retrospective_rounds = 1   # reviewer rounds after execution\n"
            "# carry_context = true       # inject previous attempt context on retry\n"
            "# token_budget_per_hour = 0  # max tokens/hour, 0 = unlimited\n",
            encoding="utf-8",
        )
        created.append("architect.toml")

    console.print()
    if created:
        for name in created:
            console.print(f"[{ARCHITECT_GREEN}]✓[/{ARCHITECT_GREEN}]  Created  [bold]{name}[/bold]")
    if skipped:
        for name in skipped:
            console.print(
                f"[dim]–  Skipped  {name}  (already exists — use --force to overwrite)[/dim]"
            )

    console.print()
    if created:
        console.print(
            f"[bold {ARCHITECT_GREEN}]Project initialised.[/bold {ARCHITECT_GREEN}]  "
            "Next: edit [bold]AGENTS.md[/bold] with your project rules, then run "
            f"[bold {ARCHITECT_GREEN}]architect --plan[/bold {ARCHITECT_GREEN}]"
        )
    else:
        console.print("[dim]Nothing to do — all files already exist.[/dim]")
    console.print()


@main.command(name="logs")
@click.option(
    "--project",
    "-p",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
)
@click.option(
    "--task",
    "-t",
    default="",
    metavar="PREFIX",
    help="Show logs for a specific task prefix (e.g. T01)",
)
@click.option(
    "--tail",
    "-n",
    default=50,
    show_default=True,
    metavar="N",
    help="Show last N lines of each log file",
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="Show full log content (overrides --tail)",
)
def logs_cmd(
    project: Path | None,
    task: str,
    tail: int,
    show_all: bool,
) -> None:
    """Show execution logs.

    Without --task: lists all available log files.
    With --task T01: shows the log for that task.

    \b
    Examples:
      architect logs               # list all log files
      architect logs --task T01    # show T01 log (last 50 lines)
      architect logs --task T01 --tail 100
      architect logs --task T01 --all
    """
    _setup_loguru()
    proj = (project or Path.cwd()).resolve()
    config = load_config(proj)
    log_dir = config.log_dir  # already resolved as absolute path by load_config

    if not log_dir.exists():
        console.print("[dim]No log directory found. Run architect to generate logs.[/dim]")
        return

    log_files = sorted(log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)

    if not log_files:
        console.print("[dim]No log files found.[/dim]")
        return

    # ── Show specific task log ───────────────────────────────────────────
    if task:
        prefix = task.upper()
        matches = [lf for lf in log_files if lf.name.upper().startswith(prefix)]
        if not matches:
            console.print(f"[red]No log found for task {prefix}.[/red]")
            _total = len(log_files)
            _names = ", ".join(lf.stem for lf in log_files[:10])
            _suffix = f" (+{_total - 10} more)" if _total > 10 else ""
            console.print(f"[dim]Available logs: {_names}{_suffix}[/dim]")
            raise SystemExit(1)

        for log_file in matches:
            console.print()
            console.print(f"[bold dim]── {log_file.name} ──[/bold dim]")
            console.print()
            try:
                raw = log_file.read_text(encoding="utf-8", errors="replace")
                # Strip JSON lines — show only human-readable text events
                lines: list[str] = []
                for line in raw.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    # Try to parse as JSON and extract display text
                    try:
                        import json as _json2

                        event = _json2.loads(line)
                        etype = event.get("type", "")
                        part = event.get("part", {})
                        if etype == "text" and isinstance(part, dict):
                            t = (part.get("text") or "").strip()
                            if t:
                                lines.extend(t.split("\n"))
                        elif etype == "error":
                            msg = str(event.get("message", event.get("error", ""))).strip()
                            if msg:
                                lines.append(f"[ERROR] {msg}")
                    except Exception:
                        # Not JSON — raw log line
                        lines.append(line)

                if not show_all:
                    lines = lines[-tail:]

                for ln in lines:
                    console.print(ln)
            except OSError as e:
                console.print(f"[red]Could not read {log_file.name}: {e}[/red]")
        return

    # ── List all log files ───────────────────────────────────────────────
    from rich import box
    from rich.table import Table

    console.print()
    table = Table(box=box.SIMPLE, show_header=True, header_style="dim", padding=(0, 1))
    table.add_column("Log file")
    table.add_column("Size", width=10)
    table.add_column("Modified", width=20)

    import datetime as _dt

    for lf in log_files:
        stat = lf.stat()
        size = f"{stat.st_size // 1024} KB" if stat.st_size >= 1024 else f"{stat.st_size} B"
        mtime = _dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        table.add_row(lf.name, size, mtime)

    console.print(table)
    console.print("[dim]Use [bold]architect logs --task T01[/bold] to view a specific log.[/dim]")
    console.print()


@main.command(name="config")
@click.option(
    "--project",
    "-p",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
)
@click.option(
    "--set",
    "set_values",
    multiple=True,
    metavar="KEY=VALUE",
    help="Set a config value, e.g. --set max_retries=5 --set carry_context=false",
)
def config_cmd(project: Path | None, set_values: tuple[str, ...]) -> None:
    """Show or update The Architect configuration.

    Without --set: shows the current configuration and its source
    (architect.toml or built-in default).

    With --set KEY=VALUE: updates architect.toml with the given values.
    Multiple --set flags are allowed.

    \b
    Configurable options:
      max_retries                  Maximum retry attempts per task (default: 3)
      retry_pause                  Seconds to wait between retries (default: 30)
      pause_between_tasks          Seconds to wait between tasks (default: 10)
      retrospective_rounds         Retrospective review rounds (default: 1)
      retry_model_2                Fallback model for attempt 2 (default: "")
      retry_model_3                Fallback model for attempt 3 (default: "")
      standalone_mode              Bypass opencode.json, use this model (default: "")
      execution_agent              Agent name for task execution (default: "")
      carry_context                Inject previous attempt context on retry (default: true)
      retry_prompt_mode            Retry prompt style: focused or same (default: focused)
      free_mode                    Use free OpenRouter models, rotate on rate limit (default: false)
      token_budget_per_hour        Max tokens per hour, 0=unlimited (default: 0)

    \b
    Circuit breaker options:
      circuit_no_progress_threshold  Attempts with no file writes before opening (default: 3)
      circuit_same_error_threshold   Attempts with same error before opening (default: 3)
      circuit_token_decline_pct      Token decline % that contributes to opening (default: 60)
      circuit_cooldown_minutes       Minutes before HALF_OPEN retry after opening (default: 30)
      circuit_enable_replan          Replan failing tasks via architect agent (default: true)
      cooldown_detection             Detect provider rate-limits and wait 1h (default: true)

    \b
    Examples:
      architect config
      architect config --set max_retries=5
      architect config --set carry_context=false --set retry_prompt_mode=same
      architect config --set retry_model_2="openrouter/google/gemini-2.5-pro"
      architect config --set token_budget_per_hour=500000
      architect config --set circuit_no_progress_threshold=5
    """
    _setup_loguru()
    proj = (project or Path.cwd()).resolve()
    config = load_config(proj)
    toml_path = proj / "architect.toml"

    if set_values:
        # Parse KEY=VALUE pairs
        updates: dict[str, object] = {}
        for item in set_values:
            if "=" not in item:
                console.print(f"[red]Invalid format '{item}' — use KEY=VALUE[/red]")
                raise SystemExit(1)
            key, _, raw_val = item.partition("=")
            key = key.strip()
            raw_val = raw_val.strip()

            # Type coercion — infer from current field type
            field = ArchitectConfig.model_fields.get(key)
            if field is None:
                console.print(f"[red]Unknown config key: '{key}'[/red]")
                console.print(
                    "[dim]Run [bold]architect config[/bold] to see available options.[/dim]"
                )
                raise SystemExit(1)

            # Coerce to the right Python type
            annotation = field.annotation
            try:
                if isinstance(annotation, type) and annotation is bool:
                    if raw_val.lower() in ("true", "1", "yes"):
                        updates[key] = True
                    elif raw_val.lower() in ("false", "0", "no"):
                        updates[key] = False
                    else:
                        raise ValueError(f"Expected true/false for '{key}'")
                elif isinstance(annotation, type) and annotation is int:
                    updates[key] = int(raw_val)
                else:
                    updates[key] = raw_val
            except ValueError as e:
                console.print(f"[red]Invalid value for '{key}': '{raw_val}' — {e}[/red]")
                raise SystemExit(1)

        try:
            written = write_config(proj, updates)
        except (ValueError, TypeError) as e:
            console.print(f"[red]Config error: {e}[/red]")
            raise SystemExit(1)

        console.print(f"[#7cc800]✓ Saved to {written.relative_to(proj)}[/#7cc800]")
        for key, val in updates.items():
            console.print(f"  [dim]{key}[/dim] = [bold]{val}[/bold]")
        return

    # Show current config
    has_toml = toml_path.exists()
    console.print()
    console.print(
        f"[bold {ARCHITECT_GREEN}]The Architect config[/bold {ARCHITECT_GREEN}]  "
        f"[grey62]{'architect.toml' if has_toml else 'defaults only'}[/grey62]"
    )
    console.print()

    # Fields to display — exclude internal path fields
    display_fields = [
        ("max_retries", config.max_retries),
        ("retry_pause", config.retry_pause),
        ("pause_between_tasks", config.pause_between_tasks),
        ("retrospective_rounds", config.retrospective_rounds),
        ("retry_model_2", config.retry_model_2 or "[dim](default)[/dim]"),
        ("retry_model_3", config.retry_model_3 or "[dim](default)[/dim]"),
        ("standalone_mode", config.standalone_mode or "[dim](not set)[/dim]"),
        ("execution_agent", config.execution_agent or "[dim](not set)[/dim]"),
        ("carry_context", config.carry_context),
        ("retry_prompt_mode", config.retry_prompt_mode),
        ("free_mode", config.free_mode),
    ]

    for key, val in display_fields:
        console.print(f"  [grey62]{key:<22}[/grey62] {val}")

    console.print()
    if has_toml:
        console.print(f"[dim]Config file: {toml_path}[/dim]")
    else:
        console.print("[dim]No architect.toml found — using built-in defaults.[/dim]")
        console.print("[dim]Run [bold]architect config --set KEY=VALUE[/bold] to create one.[/dim]")
    console.print()


@main.command(name="circuit")
@click.option(
    "--project",
    "-p",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
)
@click.option(
    "--reset",
    "reset_task",
    default="",
    metavar="TASK_ID",
    help="Reset a specific task's circuit state to CLOSED (e.g. --reset T04)",
)
def circuit_cmd(project: Path | None, reset_task: str) -> None:
    """Show or reset circuit breaker state for all tasks.

    Without --reset: shows the current circuit state for every tracked task,
    including which thresholds are elevated and the recovery action if OPEN.

    With --reset TASK_ID: manually resets that task's circuit state to CLOSED
    with zeroed counters.  Use this after fixing the underlying problem
    (e.g. installing a missing dependency) so The Architect can retry without
    waiting for the cooldown period.

    \b
    Examples:
      architect circuit
      architect circuit --reset T04
    """
    _setup_loguru()
    from datetime import datetime

    from rich import box
    from rich.table import Table

    from the_architect.core.circuit import CircuitState, load_circuit_state

    proj = (project or Path.cwd()).resolve()
    config = load_config(proj)

    cb = load_circuit_state(proj, config)

    if reset_task:
        # Normalise prefix format (accept "t04" or "T04")
        task_id = reset_task.upper()
        cb.reset_task(task_id)
        console.print(f"[#7cc800]✓ Circuit state for {task_id} reset to CLOSED.[/#7cc800]")
        return

    states = cb.all_states()

    # Also show tasks from the tasks directory that have no circuit state yet
    tasks_dir = proj / config.tasks_dir.name
    all_tasks = discover_tasks(tasks_dir)
    for t in all_tasks:
        if t.prefix not in states:
            states[t.prefix] = None  # type: ignore[assignment]

    if not states:
        console.print()
        console.print("[dim]No circuit state found — all tasks implicitly CLOSED.[/dim]")
        console.print("[dim]Run [bold]architect[/bold] to start task execution.[/dim]")
        return

    table = Table(box=box.SIMPLE, show_header=True, header_style="dim", padding=(0, 1))
    table.add_column("Task", width=8)
    table.add_column("State", width=12)
    table.add_column("No-prog", width=9)
    table.add_column("Same-err", width=10)
    table.add_column("Recovery", width=10)
    table.add_column("Opened", width=20)

    for task_id in sorted(states.keys()):
        state = states[task_id]

        if state is None:
            # Task exists but has no circuit state — implicitly CLOSED
            table.add_row(
                task_id,
                "[dim]CLOSED[/dim]",
                "[dim]0[/dim]",
                "[dim]0[/dim]",
                "[dim]—[/dim]",
                "[dim]—[/dim]",
            )
            continue

        if state.state == CircuitState.CLOSED:
            state_str = "[#7cc800]CLOSED[/#7cc800]"
        elif state.state == CircuitState.OPEN:
            state_str = "[red]OPEN[/red]"
        else:
            state_str = "[yellow]HALF_OPEN[/yellow]"

        no_prog = str(state.consecutive_no_progress)
        same_err = str(state.consecutive_same_error)
        recovery = str(state.recovery_action.value) if state.recovery_action else "—"

        opened_str = "—"
        if state.opened_at:
            try:
                then = datetime.fromisoformat(state.opened_at)
                now = datetime.now(tz=UTC)
                if then.tzinfo is None:
                    then = then.replace(tzinfo=UTC)
                elapsed = int((now - then).total_seconds())
                if elapsed < 60:
                    opened_str = f"{elapsed}s ago"
                elif elapsed < 3600:
                    opened_str = f"{elapsed // 60}m ago"
                else:
                    opened_str = f"{elapsed // 3600}h {(elapsed % 3600) // 60}m ago"
            except (ValueError, TypeError):
                logger.debug(f"Could not parse circuit opened_at timestamp: {state.opened_at!r}")
                opened_str = state.opened_at[:16] if state.opened_at else "—"

        table.add_row(task_id, state_str, no_prog, same_err, recovery, opened_str)

    console.print()
    console.print(
        f"[bold {ARCHITECT_GREEN}]The Architect circuit breaker[/bold {ARCHITECT_GREEN}]  "
        f"[grey62]{proj}[/grey62]"
    )
    console.print()
    console.print(table)
    console.print(
        "[dim]Use [bold]architect circuit --reset TASK_ID[/bold] to manually reset a task.[/dim]"
    )
    console.print()


@main.command()
def version() -> None:
    """Show The Architect version."""
    click.echo(f"architect v{__version__}")


@main.command(name="monitor")
@click.option(
    "--project",
    "-p",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
)
def monitor_cmd(project: Path | None) -> None:
    """Attach to the live monitoring session for this project.

    Looks for a tmux session named ``architect-<project>`` and attaches
    to it.  Works from any terminal — useful for reconnecting after SSH
    reconnection or accidental detach.

    If no active session is found, prints a clear message with the
    command to start a new run.
    """
    _setup_loguru()
    from the_architect.core.tmux import (
        attach_session,
        get_session_name,
        is_tmux_available,
        list_architect_sessions,
        session_exists,
    )

    proj = (project or Path.cwd()).resolve()
    session_name = get_session_name(proj)

    if not is_tmux_available():
        console.print("[red]tmux is not installed — monitoring requires tmux.[/red]")
        console.print("[dim]Install tmux to use live monitoring.[/dim]")
        raise SystemExit(1)

    if session_exists(session_name):
        console.print(f"[dim]Attaching to session: {session_name}[/dim]")
        attach_session(session_name)
        return  # pragma: no cover (attach replaces process)

    # Session not found — check if any architect sessions exist
    all_sessions = list_architect_sessions()
    if len(all_sessions) == 1:
        # Exactly one architect session running — attach to it regardless
        # of project.  This handles the common case where the user
        # reconnected via SSH and runs `architect monitor` from a
        # different directory.
        console.print(f"[dim]Attaching to session: {all_sessions[0]}[/dim]")
        attach_session(all_sessions[0])
        return  # pragma: no cover (attach replaces process)
    elif all_sessions:
        console.print(
            f"[yellow]No active session found for this project ({session_name}).[/yellow]"
        )
        console.print()
        console.print("[dim]Other active sessions:[/dim]")
        for s in all_sessions:
            console.print(f"  [dim]{s}[/dim]")
        console.print()
        console.print("[dim]To attach to a specific session, run:[/dim]")
        console.print("[dim]  tmux attach-session -t <session-name>[/dim]")
    else:
        console.print(
            f"[yellow]No active session found for this project ({session_name}).[/yellow]"
        )
        console.print("[dim]Start a run with: [bold]architect[/bold][/dim]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    main()
