"""Pre-run Textual screens.

Each screen below is a :class:`~textual.screen.Screen` subclass that
can be pushed onto a running :class:`ArchitectApp`. Dismiss values
match what the legacy per-stage apps used to return from ``App.run()``,
so the orchestration code in ``cli.py`` keeps working without any
signature changes.

Convenience ``run_*`` wrappers boot a temporary :class:`ArchitectApp`,
push the screen, wait for the dismiss value, and exit. They exist only
so the current sequential CLI flow can call each prompt as a function
one stage at a time without needing to restructure ``cli.py`` in the
same commit. The architectural target — one persistent app for the
entire run — is available directly via
:meth:`ArchitectApp.push_and_wait` for any caller that orchestrates
multiple stages in a single session.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import (
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Static,
    TextArea,
)

if TYPE_CHECKING:
    from the_architect.core.provider import ArchitectProvider


T = TypeVar("T")


# ══════════════════════════════════════════════════════════════════════
# Provider selection
# ══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ProviderOption:
    """Minimal dataclass carrying enough to render a provider row."""

    display_name: str
    version: str
    provider: ArchitectProvider


class ProviderSelectionScreen(Screen[int]):
    """Textual screen for picking a provider when multiple are installed."""

    DEFAULT_CSS = """
    ProviderSelectionScreen {
        align: center middle;
    }

    #provider_body {
        width: 72;
        height: auto;
        padding: 1 2;
        border: round $panel;
        background: $panel 20%;
    }

    #provider_title { color: $accent; text-style: bold; }
    #provider_hint { color: $text-muted; padding: 0 0 1 0; }

    ListView { border: round $panel; height: auto; }
    ListItem { padding: 0 1; }

    #provider_instructions { color: $text-muted; padding: 1 0 0 0; }
    """

    BINDINGS = [
        Binding("enter", "confirm", "Select"),
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+c", "cancel", "Cancel"),
    ]

    def __init__(self, options: list[ProviderOption]) -> None:
        super().__init__()
        self._options = options

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="provider_body"):
            yield Static("Select provider", id="provider_title")
            yield Static(
                "Multiple AI CLI providers are installed. Pick one for this run.",
                id="provider_hint",
            )
            items: list[ListItem] = []
            for opt in self._options:
                suffix = f"  (v{opt.version})" if opt.version and opt.version != "unknown" else ""
                items.append(ListItem(Label(f"{opt.display_name}{suffix}")))
            yield ListView(*items, id="provider_list", initial_index=0)
            yield Static(
                "[dim]↑↓ navigate · Enter confirm · Esc cancel[/dim]",
                id="provider_instructions",
                markup=True,
            )
        yield Footer()

    def action_confirm(self) -> None:
        try:
            list_view = self.query_one("#provider_list", ListView)
            idx = list_view.index if list_view.index is not None else 0
        except Exception:
            idx = 0
        self.dismiss(int(idx))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.action_confirm()


def run_provider_selection(options: list[ProviderOption]) -> int:
    """Boot the Architect app, show the provider screen, return the index.

    Raises ``SystemExit(0)`` on cancel.
    """
    from the_architect.tui.app import run_single_screen

    result = run_single_screen(ProviderSelectionScreen(options=options))
    if result is None:
        raise SystemExit(0)
    return int(result)


# ══════════════════════════════════════════════════════════════════════
# Goal
# ══════════════════════════════════════════════════════════════════════


class GoalScreen(Screen[str]):
    """Textual screen for capturing the user's planning goal."""

    DEFAULT_CSS = """
    GoalScreen {
        align: center middle;
    }

    #goal_body {
        width: 82;
        height: auto;
        padding: 1 2;
        border: round $panel;
        background: $panel 20%;
    }

    #goal_title { color: $accent; text-style: bold; }
    #goal_hint { color: $text-muted; padding: 0 0 1 0; }

    TextArea { height: 10; border: round $panel; }

    #goal_instructions { color: $text-muted; padding: 1 0 0 0; }
    """

    BINDINGS = [
        # TextArea captures Enter for newlines, so submit is Ctrl+Enter
        # or Ctrl+S. priority so the binding runs before the widget.
        Binding("ctrl+enter", "submit", "Submit", priority=True),
        Binding("ctrl+s", "submit", "Submit", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("ctrl+c", "cancel", "Cancel", priority=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="goal_body"):
            yield Static("What do you want to build?", id="goal_title")
            yield Static(
                "Describe the feature, component, or goal in plain English.",
                id="goal_hint",
            )
            yield TextArea(id="goal_text", soft_wrap=True)
            yield Static(
                "[dim]Type goal · Ctrl+Enter / Ctrl+S submit · Esc cancel[/dim]",
                id="goal_instructions",
                markup=True,
            )
        yield Footer()

    def action_submit(self) -> None:
        try:
            text = self.query_one("#goal_text", TextArea).text.strip()
        except Exception:
            text = ""
        if not text:
            return
        self.dismiss(text)

    def action_cancel(self) -> None:
        self.dismiss(None)


def run_goal_screen() -> str:
    """Boot the Architect app, show the goal screen, return the text.

    Raises ``SystemExit(0)`` on cancel or ``SystemExit(1)`` on empty input.
    """
    from the_architect.tui.app import run_single_screen

    result = run_single_screen(GoalScreen())
    if result is None:
        raise SystemExit(0)
    text = str(result).strip()
    if not text:
        raise SystemExit(1)
    return text


# ══════════════════════════════════════════════════════════════════════
# Scope
# ══════════════════════════════════════════════════════════════════════


class ScopeScreen(Screen[str]):
    """Textual screen for picking the planning scope."""

    DEFAULT_CSS = """
    ScopeScreen {
        align: center middle;
    }

    #scope_body {
        width: 82;
        height: auto;
        padding: 1 2;
        border: round $panel;
        background: $panel 20%;
    }

    #scope_title { color: $accent; text-style: bold; }
    #scope_hint { color: $text-muted; padding: 0 0 1 0; }

    ListView { border: round $panel; height: auto; }
    ListItem { padding: 0 1; }

    #scope_instructions { color: $text-muted; padding: 1 0 0 0; }
    """

    BINDINGS = [
        Binding("enter", "confirm", "Select"),
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+c", "cancel", "Cancel"),
    ]

    _CHOICES = (
        (
            "standard",
            "Standard — one feature area per task, balanced context (recommended)",
        ),
        (
            "simple",
            "Simple — one thing per task, smaller context per run (weak/local models)",
        ),
        (
            "complex",
            "Complex — one subsystem per task, larger context (frontier models only)",
        ),
    )

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="scope_body"):
            yield Static("Task scope", id="scope_title")
            yield Static(
                "Pick the task granularity. Standard is recommended "
                "unless you know you need otherwise.",
                id="scope_hint",
            )
            items: list[ListItem] = []
            for _, label in self._CHOICES:
                items.append(ListItem(Label(label)))
            yield ListView(*items, id="scope_list", initial_index=0)
            yield Static(
                "[dim]↑↓ navigate · Enter confirm · Esc cancel[/dim]",
                id="scope_instructions",
                markup=True,
            )
        yield Footer()

    def action_confirm(self) -> None:
        try:
            idx = self.query_one("#scope_list", ListView).index
            if idx is None:
                idx = 0
        except Exception:
            idx = 0
        self.dismiss(self._CHOICES[idx][0])

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.action_confirm()


def run_scope_screen() -> str:
    """Boot the Architect app, show the scope screen, return the scope."""
    from the_architect.tui.app import run_single_screen

    result = run_single_screen(ScopeScreen())
    if result is None:
        raise SystemExit(0)
    return str(result)


# ══════════════════════════════════════════════════════════════════════
# Generic string-list picker (model / agent)
# ══════════════════════════════════════════════════════════════════════


class StringListPickerScreen(Screen[str]):
    """Shared screen for picking one string from a labelled list."""

    DEFAULT_CSS = """
    StringListPickerScreen {
        align: center middle;
    }

    #picker_body {
        width: 82;
        height: auto;
        padding: 1 2;
        border: round $panel;
        background: $panel 20%;
    }

    #picker_title { color: $accent; text-style: bold; }
    #picker_hint { color: $text-muted; padding: 0 0 1 0; }

    ListView { border: round $panel; height: auto; }
    ListItem { padding: 0 1; }

    #picker_instructions { color: $text-muted; padding: 1 0 0 0; }
    """

    BINDINGS = [
        Binding("enter", "confirm", "Select"),
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+c", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        *,
        title: str,
        hint: str,
        choices: list[tuple[str, str]],
        initial_index: int = 0,
    ) -> None:
        super().__init__()
        self._title = title
        self._hint = hint
        self._choices = choices
        self._initial_index = max(0, min(initial_index, len(choices) - 1)) if choices else 0

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="picker_body"):
            yield Static(self._title, id="picker_title")
            yield Static(self._hint, id="picker_hint")
            items: list[ListItem] = [ListItem(Label(label)) for _, label in self._choices]
            yield ListView(*items, id="picker_list", initial_index=self._initial_index)
            yield Static(
                "[dim]↑↓ navigate · Enter confirm · Esc cancel[/dim]",
                id="picker_instructions",
                markup=True,
            )
        yield Footer()

    def action_confirm(self) -> None:
        try:
            idx = self.query_one("#picker_list", ListView).index
            if idx is None:
                idx = 0
        except Exception:
            idx = 0
        self.dismiss(self._choices[idx][0])

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.action_confirm()


def run_model_picker(
    *,
    provider_name: str,
    models: list[str],
    current: str,
) -> str | None:
    """Run the architect-model picker. Returns selected model or None for default."""
    from the_architect.tui.app import run_single_screen

    ordered = list(models)
    if current and current in ordered:
        ordered.remove(current)
        ordered.insert(0, current)
    elif current:
        ordered.insert(0, current)

    choices: list[tuple[str, str]] = []
    for m in ordered:
        label = f"  {m}"
        if m == current:
            label += "  [current]"
        choices.append((m, label))
    choices.append(("", f"  (use {provider_name} default)"))

    screen = StringListPickerScreen(
        title=f"Architect model — {provider_name}",
        hint="Pick the model the planner/reviewer agent should use.",
        choices=choices,
    )
    result = run_single_screen(screen)
    if result is None:
        raise SystemExit(0)
    if not result:
        return current if current else None
    return str(result)


def run_agent_picker(*, provider_name: str, agents: list[str]) -> str:
    """Run the execution-agent picker. Returns agent name or "" for default."""
    from the_architect.tui.app import run_single_screen

    choices: list[tuple[str, str]] = [("", f"  (use {provider_name} default)")]
    for a in agents:
        choices.append((a, f"  {a}"))

    screen = StringListPickerScreen(
        title=f"Execution agent — {provider_name}",
        hint="Pick the agent used to execute each task.",
        choices=choices,
    )
    result = run_single_screen(screen)
    if result is None:
        raise SystemExit(0)
    return str(result)


# ══════════════════════════════════════════════════════════════════════
# Outdated-provider warning
# ══════════════════════════════════════════════════════════════════════


class UpdateActionScreen(Screen[str]):
    """Confirmation screen for outdated-provider warnings."""

    DEFAULT_CSS = """
    UpdateActionScreen {
        align: center middle;
    }

    #update_body {
        width: 82;
        height: auto;
        padding: 1 2;
        border: round $panel;
        background: $panel 20%;
    }

    #update_title { color: $warning; text-style: bold; }
    #update_msg { padding: 1 0; }
    #update_hint { color: $text-muted; padding: 0 0 1 0; }
    #update_instructions { color: $text-muted; padding: 1 0 0 0; }
    """

    BINDINGS = [
        Binding("c", "confirm", "Continue"),
        Binding("C", "confirm", "Continue"),
        Binding("enter", "confirm", "Continue"),
        Binding("q", "exit", "Exit"),
        Binding("Q", "exit", "Exit"),
        Binding("escape", "exit", "Exit"),
        Binding("ctrl+c", "exit", "Exit"),
    ]

    def __init__(self, update_msg: str, install_hint: str) -> None:
        super().__init__()
        self._update_msg = update_msg
        self._install_hint = install_hint

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="update_body"):
            yield Static("Provider update available", id="update_title")
            yield Static(self._update_msg, id="update_msg")
            yield Static(f"Update:  {self._install_hint}", id="update_hint")
            yield Static(
                "[dim]Enter / C continue · Q / Esc exit[/dim]",
                id="update_instructions",
                markup=True,
            )
        yield Footer()

    def action_confirm(self) -> None:
        self.dismiss("continue")

    def action_exit(self) -> None:
        self.dismiss("exit")


def run_update_action_screen(update_msg: str, install_hint: str) -> str:
    """Run the outdated-provider confirmation screen."""
    from the_architect.tui.app import run_single_screen

    screen = UpdateActionScreen(update_msg=update_msg, install_hint=install_hint)
    result = run_single_screen(screen)
    return str(result) if result else "exit"


# ══════════════════════════════════════════════════════════════════════
# Pending tasks warning
# ══════════════════════════════════════════════════════════════════════


class PendingTasksScreen(Screen[bool]):
    """Warning screen shown when a previous run left unfinished tasks."""

    DEFAULT_CSS = """
    PendingTasksScreen {
        align: center middle;
    }

    #pending_body {
        width: 82;
        height: auto;
        padding: 1 2;
        border: round $panel;
        background: $panel 20%;
    }

    #pending_title { color: $warning; text-style: bold; }
    #pending_list { color: $text-muted; padding: 1 0; }
    #pending_hint { color: $text-muted; padding: 0 0 1 0; }
    #pending_instructions { color: $text-muted; padding: 1 0 0 0; }
    """

    BINDINGS = [
        Binding("y", "confirm", "Continue"),
        Binding("Y", "confirm", "Continue"),
        Binding("enter", "confirm", "Continue"),
        Binding("n", "abort", "Abort"),
        Binding("N", "abort", "Abort"),
        Binding("escape", "abort", "Abort"),
        Binding("ctrl+c", "abort", "Abort"),
    ]

    def __init__(self, pending: list[str]) -> None:
        super().__init__()
        self._pending = pending

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="pending_body"):
            yield Static(
                f"⚠  You have {len(self._pending)} unfinished task(s)",
                id="pending_title",
            )
            yield Static(
                "\n".join(f"  • {name}" for name in self._pending),
                id="pending_list",
            )
            yield Static(
                "Run 'architect' (without --plan) to finish them first, "
                "or continue to archive them and start a new goal.",
                id="pending_hint",
            )
            yield Static(
                "[dim]Enter / Y continue · N / Esc abort[/dim]",
                id="pending_instructions",
                markup=True,
            )
        yield Footer()

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_abort(self) -> None:
        self.dismiss(False)


def run_pending_tasks_screen(pending: list[str]) -> bool:
    """Run the pending-tasks warning screen."""
    from the_architect.tui.app import run_single_screen

    screen = PendingTasksScreen(pending=pending)
    result = run_single_screen(screen)
    return bool(result)


__all__ = [
    "GoalScreen",
    "PendingTasksScreen",
    "ProviderOption",
    "ProviderSelectionScreen",
    "ScopeScreen",
    "StringListPickerScreen",
    "UpdateActionScreen",
    "run_agent_picker",
    "run_goal_screen",
    "run_model_picker",
    "run_pending_tasks_screen",
    "run_provider_selection",
    "run_scope_screen",
    "run_update_action_screen",
]
