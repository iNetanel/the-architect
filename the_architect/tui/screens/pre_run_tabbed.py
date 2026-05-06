"""Unified tabbed pre-run screen (Phase B).

A single persistent :class:`PreRunScreen` that owns the Header, tab bar,
 and Footer, and hosts one tab per configuration concern. The user arrives
 on the Goal tab, can move freely with ``Tab`` / arrow keys / number
hotkeys, and submits once when every required tab is complete.

Replaces the linear chain of screens (Provider → Goal → Scope → Model →
Agent → Mode) that Phase A made back-navigable. The linear screens stay
in :mod:`the_architect.tui.screens.pre_run` as the ``--no-tui`` fallback.

Tabs:
  1. Goal — Scope RadioSet + TextArea (required, ≥10 chars)
  2. Provider — RadioSet (hidden when only one provider installed)
  3. Models — ListView for architect model + execution agent
  4. Options — checkboxes + token budget input
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import BaseModel
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import (
    Checkbox,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RadioSet,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from the_architect.tui.widgets import BlankOffCheckbox, BlankOffRadioButton

if TYPE_CHECKING:
    from the_architect.config import ArchitectConfig
    from the_architect.core.provider import ArchitectProvider
    from the_architect.core.tasks import Task


# ══════════════════════════════════════════════════════════════════════
# GoalTextArea — Shift+Enter inserts newline
# ══════════════════════════════════════════════════════════════════════


class GoalTextArea(TextArea):
    """TextArea where Shift+Enter inserts a newline.

    Enter is handled at the :class:`PreRunScreen` level as a priority
    binding that submits the form — that's the chat-app convention users
    expect (Claude, ChatGPT, etc.).  This subclass adds the Shift+Enter
    escape hatch so users can still compose multi-line goals when they
    need to.

    Textual's terminal key handling normalises ``shift+enter`` to its own
    key name separate from ``enter``, so the screen's Enter binding does
    not fire for Shift+Enter and the key falls through to this handler.

    Left/right arrows are intentionally NOT intercepted here — the
    screen-level priority bindings own those keys so they always switch
    tabs, even while text is being edited in this widget.  If you need
    cursor movement within the text, use Ctrl+Left / Ctrl+Right.
    """

    class Submit(Message):
        """Posted when the user presses Enter (without Shift).

        Reserved for future use if we ever move Enter handling off the
        screen-level priority binding.  Currently unused.
        """

    async def _on_key(self, event: Any) -> None:
        """Translate ``shift+enter`` into a literal newline insert.

        Everything else falls through to :meth:`TextArea._on_key`.
        """
        key = getattr(event, "key", "")
        if key == "shift+enter":
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)


# ══════════════════════════════════════════════════════════════════════
# Data model
# ══════════════════════════════════════════════════════════════════════


class PreRunValues(BaseModel):
    """All values collected from the pre-run screen tabs.

    Serialized to ``architect.toml`` on submit (except ``goal`` and
    ``context_paths`` which change every run and must not auto-fill).
    """

    goal: str = ""
    scope: str = "standard"
    context_paths: tuple[Path, ...] = ()
    provider_name: str = ""
    architect_model: str | None = None
    execution_agent: str | None = None
    free: bool = False
    persistent: bool = False
    integrity: bool = True
    force_reassessment: bool = True
    token_budget_per_hour: int = 0
    action: str = "plan"


# ══════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════

_TAB_GOAL = "tab_goal"
_TAB_PROVIDER = "tab_provider"
_TAB_MODELS = "tab_models"
_TAB_MODE = "tab_mode"

_DOT_COMPLETE = "●"
_DOT_INCOMPLETE = "○"


# ══════════════════════════════════════════════════════════════════════
# PreRunScreen
# ══════════════════════════════════════════════════════════════════════


class PreRunScreen(Screen[PreRunValues]):
    """Unified tabbed pre-run screen.

    Replaces the linear chain of individual screens with a single
    persistent screen containing tabs for Goal, Scope, Provider, Models,
    and Options. Tab titles show dot indicators (●/○) reflecting
    completion state.
    """

    BINDINGS = [
        # Left/right arrows ALWAYS switch tabs — they are priority
        # bindings so they fire before any child widget (TextArea,
        # RadioSet, ListView) can consume them for internal cursor
        # movement.  Ctrl+Left / Ctrl+Right remain available inside
        # the Goal TextArea for word-level cursor movement.
        Binding("right", "next_tab", "Next tab", show=False, priority=True),
        Binding("left", "prev_tab", "Previous tab", show=False, priority=True),
        # Vertical arrows move between sections / options on the active
        # page. priority=True keeps RadioSet and ListView from consuming
        # the keys for in-widget navigation.
        Binding("up", "focus_previous", "Previous field", show=False, priority=True),
        Binding("down", "focus_next", "Next field", show=False, priority=True),
        Binding("tab", "next_tab", "Next tab", show=False),
        Binding("shift+tab", "prev_tab", "Previous tab", show=False),
        Binding("ctrl+tab", "next_tab", "Next tab", show=False),
        Binding("ctrl+right", "next_tab", "Next tab", show=False),
        Binding("ctrl+shift+tab", "prev_tab", "Previous tab", show=False),
        Binding("ctrl+left", "prev_tab", "Previous tab", show=False),
        Binding("enter", "submit", "Submit", priority=True),
        Binding("escape", "pause_menu", "Pause menu"),
        # ctrl+c is intentionally NOT bound here — the app-level binding
        # (ArchitectApp.BINDINGS) handles it via action_quit → app.exit(),
        # which terminates cleanly without popping the screen stack and
        # briefly flashing the SplashScreen underneath.
        Binding("1", "jump_tab_1", "Goal", show=False),
        Binding("2", "jump_tab_2", "Provider", show=False),
        Binding("3", "jump_tab_3", "Models", show=False),
        Binding("4", "jump_tab_4", "Options", show=False),
    ]

    DEFAULT_CSS = """
    PreRunScreen {
        layout: vertical;
    }

    PreRunScreen #prerun_body {
        height: 1fr;
        padding: 0 2;
    }

    PreRunScreen #prerun_subtitle {
        color: $text-muted;
        padding: 0 0 1 0;
    }

    PreRunScreen TabbedContent {
        height: 1fr;
    }

    /* Green tab underline + active tab text — matches The Architect brand.
       The architect theme sets primary = $accent = brand green, so
       $block-cursor-background already resolves to green. These overrides
       are belt-and-suspenders to ensure correct colours regardless of
       which fallback theme is active.
       The `Underline` widget exposes its bar colour as a component class
       (.underline--bar), which is reachable via `> .underline--bar`.
       The `Tabs` widget exposes its active-tab styling through the
       `.-active` pseudo class on individual `Tab` static widgets. */
    PreRunScreen Underline > .underline--bar {
        color: $accent;
    }
    PreRunScreen Tab.-active {
        color: $accent;
        text-style: bold;
    }
    PreRunScreen Tab:hover {
        color: $accent;
    }

    PreRunScreen #prerun_footer {
        height: 1;
        padding: 0 1;
        color: $text;
        background: $panel;
    }

    PreRunScreen #goal_text {
        height: 8;
        border: round $panel;
    }

    PreRunScreen .tab_title {
        color: $accent;
        text-style: bold;
        padding: 1 0 0 0;
    }

    PreRunScreen .tab_hint {
        color: $text-muted;
        padding: 0 0 1 2;
    }

    PreRunScreen ListView {
        border: round $panel;
        height: auto;
        max-height: 8;
    }

    PreRunScreen ListItem {
        padding: 0 1;
    }

    PreRunScreen Checkbox {
        padding: 0;
    }

    PreRunScreen Input {
        border: round $panel;
    }
    """

    def __init__(
        self,
        *,
        providers: list[ArchitectProvider],
        config: ArchitectConfig,
        project_dir: Path,
        goal_text: str = "",
        scope_text: str = "",
        architect_model: str = "",
        execution_model: str = "",
        free_mode: bool = False,
        persistent: bool = False,
        pending_tasks: list[Task] | None = None,
        action: str = "plan",
    ) -> None:
        super().__init__()
        self._providers = providers
        self._config = config
        self._project_dir = project_dir
        self._pending_tasks = pending_tasks or []

        # Hydrate values from config + CLI flags
        self._values = PreRunValues(
            goal=goal_text,
            scope=scope_text or config.last_scope or "standard",
            provider_name=(
                config.provider
                if config.provider != "auto"
                else (providers[0].name if providers else "")
            ),
            architect_model=architect_model or config.architect_model or None,
            execution_agent=execution_model or config.execution_agent or None,
            free=free_mode or config.free_mode,
            persistent=persistent or config.persistent,
            integrity=config.integrity,
            force_reassessment=config.force_reassessment,
            token_budget_per_hour=config.token_budget_per_hour,
            action=action,
        )

        # Track which tabs to show
        self._show_provider_tab = len(providers) > 1

        # Model/agent lists (populated on mount from active provider)
        self._models: list[str] = []
        self._agents: list[str] = []
        self._model_fetch_error = False

        # Warning message for footer
        self._warning_text = ""

    # ── Composition ──────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="prerun_body"):
            yield Static(f"Project: {self._project_dir}", id="prerun_subtitle")
            with TabbedContent(id="prerun_tabs"):
                # Tab 1: Goal for fresh runs, Run decision for existing-task runs.
                first_tab_name = "Run" if self._pending_tasks else "Goal"
                with TabPane(self._tab_label(first_tab_name, False), id=_TAB_GOAL):
                    if self._pending_tasks:
                        yield Static(
                            "Existing tasks", id="pending_tasks_title", classes="tab_title"
                        )
                        yield Static(
                            self._format_pending_tasks(),
                            id="pending_tasks_summary",
                            classes="tab_hint",
                            markup=False,
                        )
                        yield Static("Action", classes="tab_title")
                        yield Static(
                            "Execute keeps the current task plan. Replan archives it and creates "
                            "a fresh plan from a new goal.",
                            classes="tab_hint",
                        )
                        with RadioSet(id="action_set"):
                            yield BlankOffRadioButton(
                                "Execute existing tasks",
                                id="rb_action_execute",
                                value=self._values.action != "replan",
                            )
                            yield BlankOffRadioButton(
                                "Replan from a new goal",
                                id="rb_action_replan",
                                value=self._values.action == "replan",
                            )
                    yield Static("Scope", id="scope_title", classes="tab_title")
                    yield Static(
                        "Choose how wide each planned task should be. This is not a target "
                        "task count: smaller scope usually creates more smaller tasks, while "
                        "larger scope usually creates fewer broader tasks.",
                        id="scope_hint",
                        classes="tab_hint",
                    )
                    with RadioSet(id="scope_set"):
                        yield BlankOffRadioButton(
                            "Simple       One atomic thing per task - smaller context per run",
                            id="rb_simple",
                            value=self._values.scope == "simple",
                        )
                        yield BlankOffRadioButton(
                            "Standard     One feature area per task - balanced context"
                            "  (recommended)",
                            id="rb_standard",
                            value=self._values.scope == "standard"
                            or self._values.scope not in ("simple", "complex"),
                        )
                        yield BlankOffRadioButton(
                            "Complex      One subsystem per task - larger context per run",
                            id="rb_complex",
                            value=self._values.scope == "complex",
                        )
                    yield Static("Goal", id="goal_title", classes="tab_title")
                    yield Static(
                        "Describe what you want built. Minimum 10 characters.  "
                        "Enter = submit, Shift+Enter = newline.",
                        id="goal_hint",
                        classes="tab_hint",
                    )
                    yield GoalTextArea(id="goal_text", soft_wrap=True)

                # Tab 2: Provider (only when multiple)
                if self._show_provider_tab:
                    with TabPane(self._tab_label("Provider", True), id=_TAB_PROVIDER):
                        yield Static("Provider", classes="tab_title")
                        yield Static(
                            "Multiple AI CLI providers are installed. Pick one for this run.",
                            classes="tab_hint",
                        )
                        with RadioSet(id="provider_set"):
                            for i, p in enumerate(self._providers):
                                version = p.get_version()
                                suffix = (
                                    f"  (v{version})" if version and version != "unknown" else ""
                                )
                                is_current = p.name == self._values.provider_name
                                yield BlankOffRadioButton(
                                    f"{p.display_name}{suffix}",
                                    id=f"rb_prov_{i}",
                                    value=is_current,
                                )

                # Tab 3: Models
                with TabPane(self._tab_label("Models", True), id=_TAB_MODELS):
                    yield Static("Models", classes="tab_title")
                    yield Static(
                        "Pick the model for planning and/or the execution agent. "
                        "Defaults use the provider's configured values.",
                        classes="tab_hint",
                    )
                    yield Static(
                        "Loading models…",
                        id="model_fetch_status",
                        classes="tab_hint",
                    )
                    yield Label("Architect model", id="model_label")
                    yield ListView(id="model_list")
                    yield Label("Execution agent", id="agent_label")
                    yield ListView(id="agent_list")

                # Tab 4: Options
                with TabPane(self._tab_label("Options", True), id=_TAB_MODE):
                    yield Static("Options", classes="tab_title")
                    yield Static(
                        "Configure how the run behaves.",
                        classes="tab_hint",
                    )
                    yield BlankOffCheckbox(
                        "Free Tier  (OpenRouter rotation)",
                        id="chk_free",
                        value=self._values.free,
                    )
                    yield Static(
                        "rotate to the next free model on rate-limit",
                        classes="tab_hint",
                    )
                    yield BlankOffCheckbox(
                        "Persistent  (30 retries, 2 retrospective rounds)",
                        id="chk_persistent",
                        value=self._values.persistent,
                    )
                    yield Static("deeper retry + review loop", classes="tab_hint")
                    yield BlankOffCheckbox(
                        "Integrity defense  (snapshot before edits)",
                        id="chk_integrity",
                        value=self._values.integrity,
                    )
                    yield Static(
                        "architect_eval snapshots catch truncated/corrupted writes",
                        classes="tab_hint",
                    )
                    yield BlankOffCheckbox(
                        "Force Reassessment  (after every task)",
                        id="chk_force_reassessment",
                        value=self._values.force_reassessment,
                    )
                    yield Static(
                        "when disabled, reassess only failed/downstream-impact tasks",
                        classes="tab_hint",
                    )
                    yield Label("Token budget/hour (0 = unlimited):")
                    budget_str = (
                        str(self._values.token_budget_per_hour)
                        if self._values.token_budget_per_hour > 0
                        else ""
                    )
                    yield Input(placeholder="0", id="inp_budget", value=budget_str)

        yield Static(self._footer_text(), id="prerun_footer")

    # ── Mount — populate model/agent lists ───────────────────────────

    def on_mount(self) -> None:
        """Load model and agent lists from the active provider."""
        # Pre-fill goal text if provided from CLI flag
        if self._values.goal:
            try:
                area = self.query_one("#goal_text", TextArea)
                area.text = self._values.goal
            except Exception as exc:
                logger.debug(f"PreRunScreen: goal pre-fill failed: {exc!r}")

        # Kick off provider data fetch in a worker so subprocess calls
        # to `opencode models` / `claude agents` don't block the
        # Textual event loop (which would freeze the splash animation
        # behind us and make the whole screen feel dead while the
        # CLI subprocesses run).
        self.run_worker(self._fetch_provider_data_async, thread=True, exclusive=True)

        # Apply cross-tab state (free checkbox visibility)
        self._update_free_checkbox_visibility()
        self._update_replan_controls_visibility()

        # Focus the first field
        try:
            area = self.query_one("#goal_text", TextArea)
            if self._pending_tasks and self._selected_action() == "replan":
                area.focus()
            elif self._pending_tasks:
                self.query_one("#action_set", RadioSet).focus()
            else:
                area.focus()
        except Exception as exc:
            logger.debug(f"PreRunScreen: initial focus failed: {exc!r}")

        self._update_tab_labels()

    def _fetch_provider_data_async(self) -> None:
        """Worker-thread body: fetch models/agents then push UI update.

        Runs in a background thread so the synchronous ``subprocess.run``
        calls inside ``provider.list_models()`` / ``list_agents()`` do
        not block the Textual event loop. The UI update is scheduled
        back onto the event loop via ``call_from_thread``.
        """
        provider = self._get_active_provider()
        models: list[str] = []
        agents: list[str] = []
        model_ok = True
        agent_ok = True

        if provider is not None:
            try:
                models = list(provider.list_models())
            except Exception as exc:
                logger.debug(f"PreRunScreen: list_models failed on {provider.name}: {exc!r}")
                model_ok = False

            try:
                if provider.supports_agents():
                    agents = list(provider.list_agents(self._project_dir))
                else:
                    agents = []
            except Exception as exc:
                logger.debug(f"PreRunScreen: list_agents failed on {provider.name}: {exc!r}")
                agent_ok = False
        else:
            model_ok = False

        # Hop back onto the Textual event loop to mutate widgets.
        self.app.call_from_thread(self._apply_provider_data, models, agents, model_ok and agent_ok)

    def _apply_provider_data(
        self,
        models: list[str],
        agents: list[str],
        ok: bool,
    ) -> None:
        """Event-loop-side update after the worker finishes."""
        self._models = models
        self._agents = agents
        self._model_fetch_error = not ok

        # Coerce persisted model if it no longer exists
        if self._values.architect_model and self._values.architect_model not in self._models:
            self._values.architect_model = None
            self._show_footer_warning(
                "Previously selected model not in current list — using provider default."
            )

        self._update_models_tab()

    # ── Provider data loading ────────────────────────────────────────

    def _refresh_provider_data(self) -> None:
        """Kick off provider data fetch in a worker thread.

        Subprocess calls to ``opencode models`` / ``claude agents`` block
        for up to 15s, so running them on the event loop would freeze
        the splash animation and every PreRunScreen redraw. Delegating
        to a Textual worker keeps the UI responsive during the fetch
        and ``_apply_provider_data`` hops back to the event loop when
        it's done.
        """
        # Show "Loading models…" status while the worker runs
        try:
            status = self.query_one("#model_fetch_status", Static)
            status.update("Loading models…")
            status.display = True
        except Exception:
            pass
        self.run_worker(self._fetch_provider_data_async, thread=True, exclusive=True)

    def _update_models_tab(self) -> None:
        """Update Models tab UI in-place — no remove+remount."""
        provider = self._get_active_provider()
        supports_agents = provider is not None and provider.supports_agents()

        # Update fetch-status line
        try:
            status = self.query_one("#model_fetch_status", Static)
            if self._model_fetch_error:
                status.update("Could not load models — using provider default.")
                status.styles.color = "$warning"
                status.display = True
            else:
                status.display = False
        except Exception as exc:
            logger.debug(f"PreRunScreen: status line update failed: {exc!r}")

        # Update Architect model list
        try:
            model_list = self.query_one("#model_list", ListView)
            self._populate_list(model_list, self._models, self._values.architect_model)
        except Exception as exc:
            logger.debug(f"PreRunScreen: model_list update failed: {exc!r}")

        # Update Execution agent list + label (hidden when not supported)
        try:
            agent_label = self.query_one("#agent_label", Label)
            agent_list = self.query_one("#agent_list", ListView)
            if supports_agents:
                agent_label.display = True
                agent_list.display = True
                self._populate_list(agent_list, self._agents, self._values.execution_agent)
            else:
                agent_label.display = False
                agent_list.display = False
        except Exception as exc:
            logger.debug(f"PreRunScreen: agent_list update failed: {exc!r}")

    @staticmethod
    def _populate_list(lv: ListView, items: list[str], current: str | None) -> None:
        """Refill a ListView with a default row + items, selecting current.

        Uses ``clear()`` + ``append()`` instead of ``remove_children()`` to
        avoid Textual mount/unmount races when switching providers.
        """
        lv.clear()
        lv.append(ListItem(Label("  (use provider default)")))
        for item in items:
            label = f"  {item}"
            if item == current:
                label += "  [current]"
            lv.append(ListItem(Label(label)))

        # Select current if present, else the default row
        if current and current in items:
            lv.index = items.index(current) + 1
        else:
            lv.index = 0

    # ── Tab navigation ───────────────────────────────────────────────

    def action_next_tab(self) -> None:
        """Switch to the next tab and auto-focus its first field."""
        try:
            tabs = self.query_one("#prerun_tabs", TabbedContent)
            tab_ids = self._visible_tab_ids()
            if not tab_ids:
                return
            current = tabs.active if tabs.active in tab_ids else tab_ids[0]
            target = tab_ids[(tab_ids.index(current) + 1) % len(tab_ids)]
            self._try_activate_tab(target)
        except Exception as exc:
            logger.debug(f"PreRunScreen: next_tab failed: {exc!r}")

    def action_prev_tab(self) -> None:
        """Switch to the previous tab and auto-focus its first field."""
        try:
            tabs = self.query_one("#prerun_tabs", TabbedContent)
            tab_ids = self._visible_tab_ids()
            if not tab_ids:
                return
            current = tabs.active if tabs.active in tab_ids else tab_ids[0]
            target = tab_ids[(tab_ids.index(current) - 1) % len(tab_ids)]
            self._try_activate_tab(target)
        except Exception as exc:
            logger.debug(f"PreRunScreen: prev_tab failed: {exc!r}")

    def _visible_tab_ids(self) -> list[str]:
        """Return visible pre-run tab IDs in their rendered order."""
        tab_ids = [_TAB_GOAL]
        if self._show_provider_tab:
            tab_ids.append(_TAB_PROVIDER)
        tab_ids.extend([_TAB_MODELS, _TAB_MODE])
        return tab_ids

    def action_focus_previous(self) -> None:
        """Move focus to the previous focusable control on the active tab."""
        if self._move_focus_within_active_tab(forward=False):
            return

    def action_focus_next(self) -> None:
        """Move focus to the next focusable control on the active tab."""
        if self._move_focus_within_active_tab(forward=True):
            return

    def action_jump_tab_1(self) -> None:
        """Jump to Goal tab."""
        self._try_activate_tab(_TAB_GOAL)

    def action_jump_tab_2(self) -> None:
        """Jump to Provider tab (or Models if no Provider tab)."""
        if self._show_provider_tab:
            self._try_activate_tab(_TAB_PROVIDER)
        else:
            self._try_activate_tab(_TAB_MODELS)

    def action_jump_tab_3(self) -> None:
        """Jump to Models tab (or Options if no Provider tab)."""
        if self._show_provider_tab:
            self._try_activate_tab(_TAB_MODELS)
        else:
            self._try_activate_tab(_TAB_MODE)

    def action_jump_tab_4(self) -> None:
        """Jump to Options tab."""
        self._try_activate_tab(_TAB_MODE)

    def _try_activate_tab(self, tab_id: str) -> None:
        """Try to activate a tab by ID and auto-focus its first field."""
        try:
            tabs = self.query_one("#prerun_tabs", TabbedContent)
            tabs.active = tab_id
            self.call_after_refresh(self._activate_and_focus_tab, tab_id)
        except Exception as exc:
            logger.debug(f"PreRunScreen: activate_tab({tab_id}) failed: {exc!r}")

    def _activate_and_focus_tab(self, tab_id: str) -> None:
        """Re-assert tab activation after pending tab messages, then focus it."""
        try:
            self.query_one("#prerun_tabs", TabbedContent).active = tab_id
        except Exception as exc:
            logger.debug(f"PreRunScreen: deferred activate_tab({tab_id}) failed: {exc!r}")
        self._auto_focus_active_tab()

    def _auto_focus_active_tab(self) -> None:
        """Focus the first interactive widget in the active tab."""
        try:
            tabs = self.query_one("#prerun_tabs", TabbedContent)
            active_id = tabs.active
            if not active_id:
                return
            pane = self.query_one(f"#{active_id}", TabPane)
            for widget in pane.children:
                if widget.focusable and widget.display:
                    # RadioSet has can_focus_children=False, so we focus the
                    # container itself — don't try to focus inner RadioButtons.
                    widget.focus()
                    return
        except Exception as exc:
            logger.debug(f"PreRunScreen: auto_focus failed: {exc!r}")

    def _move_focus_within_active_tab(self, *, forward: bool) -> bool:
        """Move focus among logical controls on the active tab only.

        This keeps up/down scoped to the current page while left/right are
        reserved for tab changes. Composite widgets like ``RadioSet`` and
        ``ListView`` are treated as single stops.
        """
        try:
            tabs = self.query_one("#prerun_tabs", TabbedContent)
            active_id = tabs.active
            if not active_id:
                return False
            pane = self.query_one(f"#{active_id}", TabPane)
        except Exception:
            return False

        stops: list[Widget] = []
        for widget in pane.children:
            widget_id = getattr(widget, "id", "")
            if not widget.display:
                continue
            if widget_id in {
                "scope_set",
                "action_set",
                "provider_set",
                "model_list",
                "agent_list",
                "chk_free",
                "chk_persistent",
                "chk_integrity",
                "chk_force_reassessment",
                "inp_budget",
                "goal_text",
            }:
                stops.append(widget)

        if not stops:
            return False

        focused = self.focused
        current_index = -1
        if focused is not None:
            focused_id = getattr(focused, "id", "") or ""
            for i, stop in enumerate(stops):
                stop_id = getattr(stop, "id", "")
                if focused is stop or focused_id == stop_id:
                    current_index = i
                    break
                if stop_id == "scope_set" and focused_id in {
                    "rb_simple",
                    "rb_standard",
                    "rb_complex",
                }:
                    current_index = i
                    break
                if stop_id == "action_set" and focused_id in {
                    "rb_action_execute",
                    "rb_action_replan",
                }:
                    current_index = i
                    break
                if stop_id == "provider_set" and focused_id.startswith("rb_prov_"):
                    current_index = i
                    break

        if 0 <= current_index < len(stops):
            current_stop = stops[current_index]
            if self._move_composite_selection(current_stop, forward=forward):
                return True

        target_index = current_index + (1 if forward else -1)
        if target_index < 0 or target_index >= len(stops):
            return False

        target = stops[target_index]
        try:
            # RadioSet has can_focus_children=False, so focusing an inner
            # RadioButton directly does not work — Textual silently ignores
            # the call.  Focus the RadioSet container itself instead; the
            # set keeps track of which button is selected internally.
            target.focus()
            return True
        except Exception:
            return False

    def _move_composite_selection(self, widget: Widget, *, forward: bool) -> bool:
        """Move selection inside a RadioSet/ListView before leaving the section."""
        if isinstance(widget, RadioSet):
            buttons: list[Any] = [
                button for button in widget.query("RadioButton") if button.display
            ]
            if not buttons:
                return False
            pressed = widget.pressed_button
            try:
                current = buttons.index(pressed) if pressed in buttons else 0
            except ValueError:
                current = 0
            target = current + (1 if forward else -1)
            if target < 0 or target >= len(buttons):
                return False
            try:
                buttons[target].value = True
                widget.focus()
                if widget.id == "action_set":
                    self._update_replan_controls_visibility()
                return True
            except Exception:
                return False

        if isinstance(widget, ListView):
            item_count = len(widget.children)
            if item_count <= 0:
                return False
            current = widget.index if widget.index is not None else 0
            target = current + (1 if forward else -1)
            if target < 0 or target >= item_count:
                return False
            try:
                widget.index = target
                widget.focus()
                return True
            except Exception:
                return False

        return False

    # ── Completion state ─────────────────────────────────────────────

    @property
    def _goal_complete(self) -> bool:
        """Goal tab is complete when required goal text is present."""
        if self._pending_tasks and self._selected_action() == "execute":
            return True
        try:
            text = self.query_one("#goal_text", TextArea).text.strip()
            return len(text) >= 10
        except Exception:
            return False

    @property
    def _provider_complete(self) -> bool:
        """Provider tab is always complete."""
        return True

    @property
    def _models_complete(self) -> bool:
        """Models tab is always complete (defaults valid)."""
        return True

    @property
    def _mode_complete(self) -> bool:
        """Options tab is always complete (defaults valid)."""
        return True

    @property
    def _all_complete(self) -> bool:
        """True when every visible tab is complete."""
        return self._goal_complete  # only Goal has a required-field rule

    def _completion_count(self) -> tuple[int, int]:
        """Return (complete_count, total_count) for the footer."""
        results = [
            self._goal_complete,
            self._models_complete,
            self._mode_complete,
        ]
        if self._show_provider_tab:
            results.insert(1, self._provider_complete)
        return sum(results), len(results)

    # ── Tab labels with dot indicators ───────────────────────────────

    @staticmethod
    def _tab_label(name: str, complete: bool) -> str:
        """Return a tab label with a dot indicator."""
        dot = _DOT_COMPLETE if complete else _DOT_INCOMPLETE
        return f"{dot} {name}"

    def _update_tab_labels(self) -> None:
        """Recompute and update all tab labels with dot indicators."""
        try:
            tabs = self.query_one("#prerun_tabs", TabbedContent)
            first_tab_name = (
                "Goal"
                if self._pending_tasks and self._selected_action() == "replan"
                else "Run"
                if self._pending_tasks
                else "Goal"
            )
            tabs.get_tab(_TAB_GOAL).label = self._tab_label(first_tab_name, self._goal_complete)
            if self._show_provider_tab:
                tabs.get_tab(_TAB_PROVIDER).label = self._tab_label(
                    "Provider", self._provider_complete
                )
            tabs.get_tab(_TAB_MODELS).label = self._tab_label("Models", self._models_complete)
            tabs.get_tab(_TAB_MODE).label = self._tab_label("Options", self._mode_complete)
        except Exception as exc:
            logger.debug(f"PreRunScreen: update_tab_labels failed: {exc!r}")

    # ── Footer ───────────────────────────────────────────────────────

    def _footer_text(self) -> str:
        """Return the footer status text."""
        done, total = self._completion_count()
        return (
            f"{done}/{total} complete   ·   "
            "Tab switch tabs   ·   Shift+Tab back   ·   Enter = submit   ·   "
            "Esc = pause menu"
        )

    def _update_footer(self) -> None:
        """Refresh the footer with current completion state."""
        try:
            footer = self.query_one("#prerun_footer", Static)
            footer.update(self._footer_text())
        except Exception as exc:
            logger.debug(f"PreRunScreen: footer update failed: {exc!r}")

    def _show_footer_warning(self, text: str) -> None:
        """Show a warning message in the footer area."""
        self._warning_text = text
        try:
            footer = self.query_one("#prerun_footer", Static)
            footer.update(text)
        except Exception as exc:
            logger.debug(f"PreRunScreen: footer warning failed: {exc!r}")

    # ── Value collection ─────────────────────────────────────────────

    def _collect_values(self) -> PreRunValues:
        """Read current widget values and return a PreRunValues instance."""
        # Goal
        goal = ""
        try:
            goal = self.query_one("#goal_text", TextArea).text.strip()
        except Exception:
            pass

        # Scope
        scope = "standard"
        try:
            rs = self.query_one("#scope_set", RadioSet)
            pressed = rs.pressed_button
            if pressed is not None:
                if pressed.id == "rb_simple":
                    scope = "simple"
                elif pressed.id == "rb_complex":
                    scope = "complex"
        except Exception:
            pass

        action = self._selected_action()

        # Provider
        provider_name = self._values.provider_name
        if self._show_provider_tab:
            try:
                rs = self.query_one("#provider_set", RadioSet)
                pressed = rs.pressed_button
                if pressed is not None and pressed.id:
                    idx_str = pressed.id.replace("rb_prov_", "")
                    idx = int(idx_str)
                    if 0 <= idx < len(self._providers):
                        provider_name = self._providers[idx].name
            except Exception:
                pass

        # Architect model
        architect_model: str | None = None
        try:
            lv = self.query_one("#model_list", ListView)
            idx = lv.index if lv.index is not None else 0
            if idx > 0 and idx - 1 < len(self._models):
                architect_model = self._models[idx - 1]
        except Exception:
            pass

        # Execution agent
        execution_agent: str | None = None
        provider = self._get_active_provider()
        if provider is not None and provider.supports_agents():
            try:
                lv = self.query_one("#agent_list", ListView)
                idx = lv.index if lv.index is not None else 0
                if idx > 0 and idx - 1 < len(self._agents):
                    execution_agent = self._agents[idx - 1]
            except Exception:
                pass

        # Mode settings
        free = False
        try:
            chk = self.query_one("#chk_free", Checkbox)
            if chk.display:
                free = bool(chk.value)
        except Exception:
            pass

        persistent = False
        try:
            persistent = bool(self.query_one("#chk_persistent", Checkbox).value)
        except Exception:
            pass

        integrity = True
        try:
            integrity = bool(self.query_one("#chk_integrity", Checkbox).value)
        except Exception:
            pass

        force_reassessment = True
        try:
            force_reassessment = bool(self.query_one("#chk_force_reassessment", Checkbox).value)
        except Exception:
            pass

        budget = 0
        try:
            raw = self.query_one("#inp_budget", Input).value or "0"
            budget = max(int(raw.strip() or "0"), 0)
        except (ValueError, Exception):
            budget = 0

        return PreRunValues(
            goal=goal,
            scope=scope,
            context_paths=(),
            provider_name=provider_name,
            architect_model=architect_model,
            execution_agent=execution_agent,
            free=free,
            persistent=persistent,
            integrity=integrity,
            force_reassessment=force_reassessment,
            token_budget_per_hour=budget,
            action=action,
        )

    # ── Actions ──────────────────────────────────────────────────────

    def action_submit(self) -> None:
        """Submit the form if all tabs are complete."""
        self._update_tab_labels()
        self._update_footer()

        if not self._all_complete:
            if not self._goal_complete:
                self._try_activate_tab(_TAB_GOAL)
            self._show_footer_warning("Fill the ○ tabs before submitting")
            return

        values = self._collect_values()
        self.dismiss(values)

    def action_pause_menu(self) -> None:
        """Open the pause menu (same as ExecutionScreen)."""
        try:
            from the_architect.tui.app import ArchitectApp

            if isinstance(self.app, ArchitectApp):
                self.app.show_pause_menu()
        except Exception as exc:
            logger.debug(f"PreRunScreen: pause_menu failed: {exc!r}")

    def action_cancel(self) -> None:
        """Cancel the pre-run screen."""
        self.dismiss(None)

    # ── Event handlers ───────────────────────────────────────────────

    def on_text_area_changed(self) -> None:
        """Update tab labels when the goal text changes."""
        self._update_tab_labels()
        self._update_footer()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        """Handle radio set changes (scope, provider)."""
        self._update_tab_labels()
        self._update_footer()

        if event.radio_set.id == "provider_set":
            self._on_provider_changed()
        elif event.radio_set.id == "action_set":
            self._update_replan_controls_visibility()

    def _selected_action(self) -> str:
        """Return the existing-task action selected on the Goal tab."""
        if not self._pending_tasks:
            return "plan"
        try:
            pressed = self.query_one("#action_set", RadioSet).pressed_button
            if pressed is not None and pressed.id == "rb_action_replan":
                return "replan"
        except Exception:
            pass
        return "execute"

    def on_checkbox_changed(self) -> None:
        """Update footer when checkboxes change."""
        self._update_tab_labels()
        self._update_footer()

    def _on_provider_changed(self) -> None:
        """Handle provider selection change — refresh model/agent lists."""
        values = self._collect_values()
        self._values.provider_name = values.provider_name

        # Refresh Models tab from new provider
        self._refresh_provider_data()

        # Update Options tab — show/hide free checkbox based on new provider
        self._update_free_checkbox_visibility()

    def _update_free_checkbox_visibility(self) -> None:
        """Show or hide the free-tier checkbox based on active provider."""
        provider = self._get_active_provider()
        supports_free = provider is not None and provider.supports_free_tier()

        try:
            chk = self.query_one("#chk_free", Checkbox)
            if supports_free:
                chk.display = True
            else:
                chk.display = False
                if self._values.free:
                    self._values.free = False
                    chk.value = False
                    pname = self._values.provider_name or "this provider"
                    self._show_footer_warning(f"Free mode not supported on {pname} — disabled.")
        except Exception as exc:
            logger.debug(f"PreRunScreen: free checkbox visibility failed: {exc!r}")

    def _update_replan_controls_visibility(self) -> None:
        """Show goal/scope fields only when existing-task runs choose replan."""
        if not self._pending_tasks:
            return
        show_replan_fields = self._selected_action() == "replan"
        for widget_id in ("pending_tasks_title", "pending_tasks_summary"):
            try:
                self.query_one(f"#{widget_id}").display = not show_replan_fields
            except Exception as exc:
                logger.debug(
                    f"PreRunScreen: pending summary visibility failed for {widget_id}: {exc!r}"
                )
        for widget_id in (
            "scope_title",
            "scope_hint",
            "scope_set",
            "goal_title",
            "goal_hint",
            "goal_text",
        ):
            try:
                self.query_one(f"#{widget_id}").display = show_replan_fields
            except Exception as exc:
                logger.debug(
                    f"PreRunScreen: replan field visibility failed for {widget_id}: {exc!r}"
                )
        if show_replan_fields:
            self.call_after_refresh(self._focus_goal_text)
        self._update_tab_labels()
        self._update_footer()

    def _focus_goal_text(self) -> None:
        """Focus the goal text area after switching an existing run to replan."""
        try:
            area = self.query_one("#goal_text", TextArea)
            if area.display:
                area.focus()
        except Exception as exc:
            logger.debug(f"PreRunScreen: goal focus failed: {exc!r}")

    # ── Helpers ──────────────────────────────────────────────────────

    def _get_active_provider(self) -> ArchitectProvider | None:
        """Return the provider matching the current selection."""
        name = self._values.provider_name
        for p in self._providers:
            if p.name == name:
                return p
        return self._providers[0] if self._providers else None

    def _format_pending_tasks(self) -> str:
        """Return a compact pending-task summary for existing-task runs."""
        n = len(self._pending_tasks)
        lines = [f"{n} pending task{'s' if n != 1 else ''} found:"]
        for task in self._pending_tasks[:5]:
            lines.append(f"  {task.prefix}  {task.title or task.name}")
        if n > 5:
            lines.append(f"  ... and {n - 5} more")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# Runner helper
# ══════════════════════════════════════════════════════════════════════


def run_pre_run_tabbed(
    *,
    providers: list[ArchitectProvider],
    config: ArchitectConfig,
    project_dir: Path,
    goal_text: str = "",
    scope_text: str = "",
    architect_model: str = "",
    execution_model: str = "",
    free_mode: bool = False,
    persistent: bool = False,
    pending_tasks: list[Task] | None = None,
    action: str = "plan",
) -> PreRunValues | None:
    """Show the tabbed pre-run screen and return the chosen values.

    Uses the active :class:`ArchitectAppRunner` if one is in flight — no
    fresh app boot, no alt-screen flash. Falls back to a minimal harness
    when no runner is hosting the CLI flow.

    Raises :class:`SystemExit` with code 0 when the user cancels.

    Returns:
        The collected :class:`PreRunValues`, or None on cancel.
    """
    from the_architect.tui.app import run_single_screen

    screen = PreRunScreen(
        providers=providers,
        config=config,
        project_dir=project_dir,
        goal_text=goal_text,
        scope_text=scope_text,
        architect_model=architect_model,
        execution_model=execution_model,
        free_mode=free_mode,
        persistent=persistent,
        pending_tasks=pending_tasks,
        action=action,
    )
    result = run_single_screen(screen)
    if result is None:
        raise SystemExit(0)
    return result


__all__ = [
    "PreRunScreen",
    "PreRunValues",
    "run_pre_run_tabbed",
]
