"""Pre-run mode-selection Textual screen.

Collects four settings:

1. ``free`` — bool (OpenRouter free-tier rotation; hidden when the
   provider does not support it)
2. ``persistent`` — bool
3. ``integrity`` — bool (architect_eval snapshot defense, default on)
4. ``token_budget_per_hour`` — int (0 = unlimited)

The screen also shows saved presets (loaded from ``.architect/presets.json``)
as a selectable list at the top. Selecting a preset pre-fills the form fields.

The screen dismisses with the dict of values on submit, or ``None`` on
cancel. Callers that aren't already hosting an :class:`ArchitectApp`
use :func:`run_mode_selection`, which routes to the active
:class:`ArchitectAppRunner` when one is in flight (no fresh app boot,
no alt-screen flash) or boots a minimal harness otherwise.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import (
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
    TextArea,
)

from the_architect.tui.screens.pre_run import BACK_SENTINEL
from the_architect.tui.widgets import BlankOffCheckbox

if TYPE_CHECKING:
    from the_architect.core.presets import Preset


class ModeSelectionScreen(Screen[dict[str, object]]):
    """Screen that collects run-mode settings and dismisses with them."""

    DEFAULT_CSS = """
    ModeSelectionScreen {
        align: center middle;
    }

    #mode_body {
        width: 100%;
        max-width: 72;
        height: auto;
        padding: 1 2;
        border: round $panel;
        background: $panel 20%;
    }

    #mode_title { color: $accent; text-style: bold; }
    #mode_hint { color: $text-muted; }
    .mode_help { color: $text-muted; padding: 0 0 1 3; }

    /* Preset section */
    #preset_section {
        width: 100%;
    }
    #preset_label { color: $accent; text-style: bold; padding: 0 0 0 0; }
    #preset_list { border: round $panel; height: auto; }
    #preset_list ListItem { padding: 0 1; }
    #preset_no_msg { color: $text-muted; padding: 0 0 1 3; }
    #spending_section {
        width: 100%;
        padding: 1 0 0 0;
    }
    #spending_label { color: $accent; text-style: bold; padding: 0 0 0 0; }
    #spending_detail { color: $text-muted; padding: 0 0 0 3; }
    #artifact_label { color: $accent; text-style: bold; padding: 0 0 0 0; }
    #artifact_detail { color: $text-muted; padding: 0 0 0 3; }
    #model_routing_label { color: $accent; text-style: bold; padding: 0 0 0 0; }
    #model_routing_detail { color: $text-muted; padding: 0 0 0 3; }
    #priority_label { color: $accent; text-style: bold; padding: 0 0 0 0; }
    #priority_detail { color: $text-muted; padding: 0 0 0 3; }
    #hooks_label { color: $accent; text-style: bold; padding: 0 0 0 0; }
    #hooks_detail { color: $text-muted; padding: 0 0 0 3; }
    #vg_custom_label { color: $accent; text-style: bold; padding: 1 0 0 0; }

    Checkbox { padding: 0; }
    /* On-state marker is bold green; off-state is a blank space
       (handled in Python via BlankOffCheckbox._button) so the
       indicator unambiguously reads as "unselected" rather than a
       dim X that could be mistaken for on. */
    Checkbox.-on > .toggle--button {
        color: $success;
        background: $panel;
        text-style: bold;
    }
    Input { border: round $panel; }
    TextArea { border: round $panel; height: 6; }

    #submit_row { color: $text-muted; padding: 1 0 0 0; }
    """

    BINDINGS = [
        # priority=True so Enter is handled here before the focused
        # Checkbox sees it — Space is the accepted key for toggling
        # checkboxes (Textual's default), Enter always submits.
        Binding("enter", "submit", "Submit", priority=True),
        Binding("backspace", "go_back", "Back"),
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("ctrl+c", "cancel", "Cancel", priority=True),
    ]

    def __init__(
        self,
        show_free: bool = True,
        *,
        project: Path | None = None,
        initial_free: bool = False,
        initial_persistent: bool = False,
        initial_integrity: bool = True,
        initial_budget: int = 0,
        initial_budget_run: int = 0,
        initial_task_timeout: int = 0,
        initial_notify_complete: bool = True,
        initial_notify_fail: bool = True,
        initial_validation_gate_enabled: bool = True,
        initial_validation_gate_checks: tuple[str, ...] = (
            "lint",
            "test",
            "typecheck",
        ),
        initial_validation_gate_custom_commands: dict[str, str] | None = None,
        initial_validation_gate_fail_fast: bool = True,
    ) -> None:
        super().__init__()
        self._show_free = show_free
        self._project = project
        self._initial_free = initial_free
        self._initial_persistent = initial_persistent
        self._initial_integrity = initial_integrity
        self._initial_budget = initial_budget
        self._initial_budget_run = initial_budget_run
        self._initial_task_timeout = initial_task_timeout
        self._initial_notify_complete = initial_notify_complete
        self._initial_notify_fail = initial_notify_fail
        self._initial_validation_gate_enabled = initial_validation_gate_enabled
        self._initial_validation_gate_checks = initial_validation_gate_checks
        self._initial_validation_gate_custom_commands = (
            initial_validation_gate_custom_commands or {}
        )
        self._initial_validation_gate_fail_fast = initial_validation_gate_fail_fast
        self._presets: list[Preset] = []
        self._spending_summary: dict[str, object] | None = None
        self._artifact_count: int = 0
        self._model_routing_summary: str | None = None
        self._priority_summary: str | None = None
        self._hooks_summary: str | None = None
        # Load presets and spending summary synchronously so they are available during compose()
        if project is not None:
            self._load_presets()
            self._load_spending_summary()
            self._load_artifact_count()
            self._load_task_model_routing()
            self._load_task_priority_summary()
            self._load_hooks_summary()

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="mode_body"):
            yield Static("Configure run", id="mode_title")
            yield Static(
                "Pick a preset or configure settings manually.",
                id="mode_hint",
            )
            yield Static("")
            # ── Preset selection section ──────────────────────────
            yield Static("Presets:", id="preset_label")
            if self._presets:
                items: list[ListItem] = []
                for preset in self._presets:
                    desc = preset.description or "(no description)"
                    label = f"  [bold]{preset.name}[/bold] — {desc}"
                    items.append(ListItem(Static(label, markup=True)))
                yield ListView(*items, id="preset_list")
            else:
                yield Static(
                    "[dim]No presets saved. Use 'architect preset create' to add one.[/dim]",
                    id="preset_no_msg",
                    markup=True,
                )
            # ── Spending summary section ──────────────────────────
            if self._spending_summary:
                cost = self._spending_summary["total_cost"]
                runs = self._spending_summary["run_count"]
                model = self._spending_summary["top_model"]
                yield Static("")
                yield Static("Recent Spending (7 days):", id="spending_label")
                yield Static(
                    f"  ${cost:.2f} across {runs} run{'s' if runs != 1 else ''}"
                    f" · top model: {model}",
                    id="spending_detail",
                )
            # ── Artifact count section ────────────────────────────
            if self._artifact_count > 0:
                yield Static("")
                yield Static("Artifacts:", id="artifact_label")
                yield Static(
                    f"  {self._artifact_count} artifact{'s' if self._artifact_count != 1 else ''}"
                    " available from completed tasks",
                    id="artifact_detail",
                )
            # ── Model routing summary ─────────────────────────────
            if self._model_routing_summary:
                yield Static("")
                yield Static("Model Routing:", id="model_routing_label")
                yield Static(
                    f"  {self._model_routing_summary}",
                    id="model_routing_detail",
                )
            # ── Priority summary ──────────────────────────────────
            if self._priority_summary:
                yield Static("")
                yield Static("Task Priorities:", id="priority_label")
                yield Static(
                    f"  {self._priority_summary}",
                    id="priority_detail",
                )
            # ── Hooks summary ─────────────────────────────────────
            if self._hooks_summary:
                yield Static("")
                yield Static("Lifecycle Hooks:", id="hooks_label")
                yield Static(
                    f"  {self._hooks_summary}",
                    id="hooks_detail",
                )
            # ── Form fields ───────────────────────────────────────
            yield Static("")
            if self._show_free:
                yield BlankOffCheckbox(
                    "Free Tier  (OpenRouter rotation)",
                    id="chk_free",
                    value=self._initial_free,
                )
                yield Static(
                    "rotate to the next free model on rate-limit",
                    classes="mode_help",
                )
            yield BlankOffCheckbox(
                "Persistent  (30 retries, 3 retrospective rounds)",
                id="chk_persistent",
                value=self._initial_persistent,
            )
            yield Static("deeper retry + review loop", classes="mode_help")
            yield BlankOffCheckbox(
                "Integrity defense  (snapshot before edits)",
                id="chk_integrity",
                value=self._initial_integrity,
            )
            yield Static(
                "architect_eval snapshots catch truncated/corrupted writes",
                classes="mode_help",
            )
            yield Label("Token budget/hour (0 = unlimited):")
            yield Input(
                placeholder="0",
                id="inp_budget",
                value=str(self._initial_budget) if self._initial_budget > 0 else "",
            )
            yield Label("Token budget/run (0 = unlimited):")
            yield Input(
                placeholder="0",
                id="inp_budget_run",
                value=str(self._initial_budget_run) if self._initial_budget_run > 0 else "",
            )
            yield Label("Task timeout (0 = unlimited):")
            yield Input(
                placeholder="0",
                id="inp_task_timeout",
                value=str(self._initial_task_timeout) if self._initial_task_timeout > 0 else "",
            )
            yield BlankOffCheckbox(
                "Notify on complete  (desktop alert)",
                id="chk_notify_complete",
                value=self._initial_notify_complete,
            )
            yield Static(
                "desktop notification when the run finishes successfully",
                classes="mode_help",
            )
            yield BlankOffCheckbox(
                "Notify on fail  (desktop alert)",
                id="chk_notify_fail",
                value=self._initial_notify_fail,
            )
            yield Static(
                "desktop notification when the run fails",
                classes="mode_help",
            )
            yield BlankOffCheckbox(
                "Validation Gate  (CI checks after each task)",
                id="chk_validation_gate",
                value=self._initial_validation_gate_enabled,
            )
            yield Static(
                "run lint, test, typecheck after each task to catch drift",
                classes="mode_help",
            )
            yield BlankOffCheckbox(
                "  Gate: Lint  (ruff check)",
                id="chk_vg_lint",
                value="lint" in self._initial_validation_gate_checks,
            )
            yield BlankOffCheckbox(
                "  Gate: Test  (pytest)",
                id="chk_vg_test",
                value="test" in self._initial_validation_gate_checks,
            )
            yield BlankOffCheckbox(
                "  Gate: Typecheck  (mypy)",
                id="chk_vg_typecheck",
                value="typecheck" in self._initial_validation_gate_checks,
            )
            yield BlankOffCheckbox(
                "  Gate: Fail Fast  (stop on first failure)",
                id="chk_vg_fail_fast",
                value=self._initial_validation_gate_fail_fast,
            )
            yield Static(
                "Custom Commands  (one per line: name=command)",
                id="vg_custom_label",
            )
            yield Static(
                "e.g. build=npm run build · security=npm audit",
                classes="mode_help",
            )
            vg_custom_text = ""
            if self._initial_validation_gate_custom_commands:
                vg_custom_text = "\n".join(
                    f"{k}={v}" for k, v in self._initial_validation_gate_custom_commands.items()
                )
            yield TextArea(
                text=vg_custom_text,
                id="inp_vg_custom",
                soft_wrap=True,
            )
            yield Static("")
            yield Static(
                "[dim]↑↓ navigate · Space toggle · Enter submit · Esc cancel[/dim]",
                id="submit_row",
                markup=True,
            )
        yield Footer()

    def on_mount(self) -> None:
        # Focus the first interactive widget
        try:
            if self._presets:
                # Focus the preset list first so arrow keys navigate presets
                preset_list = self.query_one("#preset_list", ListView)
                preset_list.focus()
            else:
                first_check = self.query(Checkbox).first()
                if first_check is not None:
                    first_check.focus()
        except Exception:
            pass

    async def _on_key(self, event: Any) -> None:
        """Handle up/down arrows for focus navigation between form fields.

        Up/down arrows move focus between form fields. TextArea keeps arrow
        keys for multi-line cursor movement. Input widgets are single-line
        so up/down always moves focus (left/right still work for cursor).
        """
        key_name = getattr(event, "key", "")

        if key_name in ("up", "down"):
            focused = self.focused
            if isinstance(focused, TextArea):
                return  # let TextArea handle multi-line cursor movement
            if key_name == "up":
                self.action_focus_previous()
            else:
                self.action_focus_next()
            event.stop()
            event.prevent_default()
            return

        await super()._on_key(event)

    def _load_presets(self) -> None:
        """Load presets from the project's .architect/presets.json."""
        if self._project is None:
            return
        try:
            from the_architect.core.presets import list_presets

            self._presets = list_presets(self._project)
        except Exception:
            # If presets can't be loaded, silently show empty
            self._presets = []

    def _load_spending_summary(self) -> None:
        """Load recent spending summary from the project's token ledger.

        Computes the last 7 days of cost data: total cost, run count,
        and most-used model. Returns None if the ledger is empty or
        cannot be loaded.
        """
        if self._project is None:
            return
        try:
            from datetime import UTC, datetime, timedelta

            from the_architect.core.cost_analytics import aggregate_costs
            from the_architect.core.token_ledger import load_ledger

            ledger = load_ledger(self._project)
            if not ledger.records:
                self._spending_summary = None
                return

            # Compute since date (7 days ago)
            since = (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            analytics = aggregate_costs(ledger, since=since)
            if analytics.run_count == 0:
                self._spending_summary = None
                return

            # Find top model by cost
            top_model = ""
            if analytics.model_breakdown:
                top_model = max(
                    analytics.model_breakdown,
                    key=lambda m: analytics.model_breakdown[m].total_cost,
                )

            self._spending_summary = {
                "total_cost": analytics.total_cost,
                "total_tokens": analytics.total_tokens,
                "run_count": analytics.run_count,
                "top_model": top_model,
            }
        except Exception:
            # If spending can't be loaded, silently hide it
            self._spending_summary = None

    def _load_artifact_count(self) -> None:
        """Load the total artifact count from the project's artifact store.

        Returns 0 if the store is empty, missing, or cannot be loaded.
        """
        if self._project is None:
            return
        try:
            from the_architect.core.artifacts import list_artifacts

            self._artifact_count = len(list_artifacts(self._project))
        except Exception:
            # If artifacts can't be loaded, silently show zero
            self._artifact_count = 0

    def _load_task_model_routing(self) -> None:
        """Load per-task model routing summary from the current task plan.

        Discovers task files in the project's tasks directory and computes
        a summary of model assignments. If all tasks use the default model,
        the summary is None (hidden). If tasks use different models, the
        summary shows counts per model (e.g., "3 on default, 2 on gemini").
        """
        if self._project is None:
            return
        try:
            from the_architect.core.tasks import discover_tasks

            tasks_dir = self._project / "tasks"
            tasks = discover_tasks(tasks_dir)
            if not tasks:
                self._model_routing_summary = None
                return

            # Count model assignments
            model_counts: dict[str, int] = {}
            for task in tasks:
                model = task.model or "(default)"
                model_counts[model] = model_counts.get(model, 0) + 1

            # If all tasks use the default model, hide the section
            if len(model_counts) == 1 and "(default)" in model_counts:
                self._model_routing_summary = None
                return

            # Build summary string
            parts: list[str] = []
            for model, count in sorted(model_counts.items()):
                label = "default" if model == "(default)" else model
                parts.append(f"{count} on {label}")
            self._model_routing_summary = ", ".join(parts)
        except Exception:
            # If model routing can't be loaded, silently hide it
            self._model_routing_summary = None

    def _load_task_priority_summary(self) -> None:
        """Load task priority summary from the current task plan.

        Discovers task files in the project's tasks directory and computes
        a summary of priority assignments. If all tasks use the default
        priority (medium), the summary is None (hidden). If tasks use
        different priorities, the summary shows counts per priority
        (e.g., "1 critical, 2 high, 3 medium, 1 low").
        """
        if self._project is None:
            return
        try:
            from the_architect.core.tasks import discover_tasks

            tasks_dir = self._project / "tasks"
            tasks = discover_tasks(tasks_dir)
            if not tasks:
                self._priority_summary = None
                return

            # Count priority assignments
            priority_counts: dict[str, int] = {}
            for task in tasks:
                priority = task.priority or "(default)"
                priority_counts[priority] = priority_counts.get(priority, 0) + 1

            # If all tasks use the default priority, hide the section
            if len(priority_counts) == 1 and "(default)" in priority_counts:
                self._priority_summary = None
                return

            # Build summary string with color-coded indicators
            parts: list[str] = []
            # Order: critical, high, medium, low for consistent display
            for prio in ("critical", "high", "medium", "low"):
                count = priority_counts.get(prio, 0)
                if count > 0:
                    parts.append(f"{count} {prio}")
            # Handle default (None) priority
            default_count = priority_counts.get("(default)", 0)
            if default_count > 0:
                parts.append(f"{default_count} default")
            self._priority_summary = ", ".join(parts)
        except Exception:
            # If priority can't be loaded, silently hide it
            self._priority_summary = None

    def _load_hooks_summary(self) -> None:
        """Load lifecycle hooks summary from the project's hooks config.

        Computes a summary of configured hooks showing count and event types.
        If no hooks are configured, the summary is None (hidden).
        """
        if self._project is None:
            return
        try:
            from the_architect.core.hooks import list_hooks

            hooks = list_hooks(self._project)
            if not hooks:
                self._hooks_summary = None
                return

            # Group hooks by event type
            event_counts: dict[str, int] = {}
            for hook in hooks:
                event_counts[hook.event] = event_counts.get(hook.event, 0) + 1

            # Build summary string
            parts: list[str] = []
            for event, count in sorted(event_counts.items()):
                parts.append(f"{count} {event}")
            total = len(hooks)
            label = f"{total} hook{'s' if total != 1 else ''} configured: "
            self._hooks_summary = label + ", ".join(parts)
        except Exception:
            # If hooks can't be loaded, silently hide it
            self._hooks_summary = None

    def _apply_preset(self, preset: Preset) -> None:
        """Pre-fill form fields from a preset's config_overrides."""
        overrides = preset.config_overrides or {}

        # Map preset config keys to form fields
        free_val = overrides.get("free_mode", self._initial_free)
        persistent_val = overrides.get("persistent", self._initial_persistent)
        integrity_val = overrides.get("integrity", self._initial_integrity)
        budget_val = overrides.get("token_budget_per_hour", self._initial_budget)
        budget_run_val = overrides.get("token_budget_per_run", self._initial_budget_run)
        task_timeout_val = overrides.get("task_timeout", self._initial_task_timeout)
        notify_complete_val = overrides.get("notify_on_complete", self._initial_notify_complete)
        notify_fail_val = overrides.get("notify_on_fail", self._initial_notify_fail)

        # Apply to form widgets
        if self._show_free:
            try:
                self.query_one("#chk_free", Checkbox).value = bool(free_val)
            except Exception:
                pass
        try:
            self.query_one("#chk_persistent", Checkbox).value = bool(persistent_val)
        except Exception:
            pass
        try:
            self.query_one("#chk_integrity", Checkbox).value = bool(integrity_val)
        except Exception:
            pass
        try:
            budget_str = str(int(budget_val)) if int(budget_val) > 0 else ""
            self.query_one("#inp_budget", Input).value = budget_str
        except Exception:
            pass
        try:
            budget_run_str = str(int(budget_run_val)) if int(budget_run_val) > 0 else ""
            self.query_one("#inp_budget_run", Input).value = budget_run_str
        except Exception:
            pass
        try:
            timeout_str = str(int(task_timeout_val)) if int(task_timeout_val) > 0 else ""
            self.query_one("#inp_task_timeout", Input).value = timeout_str
        except Exception:
            pass
        try:
            self.query_one("#chk_notify_complete", Checkbox).value = bool(notify_complete_val)
        except Exception:
            pass
        try:
            self.query_one("#chk_notify_fail", Checkbox).value = bool(notify_fail_val)
        except Exception:
            pass
        # ── Validation Gate ──────────────────────────────────────
        vg_raw = overrides.get("validation_gate", {})
        if isinstance(vg_raw, dict):
            vg_enabled_val = vg_raw.get("enabled", self._initial_validation_gate_enabled)
            vg_checks_val = vg_raw.get(
                "checks",
                list(self._initial_validation_gate_checks),
            )
            vg_fail_fast_val = vg_raw.get(
                "fail_fast",
                self._initial_validation_gate_fail_fast,
            )
        else:
            vg_enabled_val = self._initial_validation_gate_enabled
            vg_checks_val = list(self._initial_validation_gate_checks)
            vg_fail_fast_val = self._initial_validation_gate_fail_fast
        try:
            self.query_one("#chk_validation_gate", Checkbox).value = bool(vg_enabled_val)
        except Exception:
            pass
        try:
            self.query_one("#chk_vg_lint", Checkbox).value = "lint" in vg_checks_val
        except Exception:
            pass
        try:
            self.query_one("#chk_vg_test", Checkbox).value = "test" in vg_checks_val
        except Exception:
            pass
        try:
            self.query_one("#chk_vg_typecheck", Checkbox).value = "typecheck" in vg_checks_val
        except Exception:
            pass
        try:
            self.query_one("#chk_vg_fail_fast", Checkbox).value = bool(vg_fail_fast_val)
        except Exception:
            pass
        # ── Custom Commands ─────────────────────────────────────────
        vg_custom_raw = vg_raw.get("custom_commands", {}) if isinstance(vg_raw, dict) else {}
        if isinstance(vg_custom_raw, dict) and vg_custom_raw:
            try:
                vg_custom_text = "\n".join(f"{k}={v}" for k, v in vg_custom_raw.items())
                self.query_one("#inp_vg_custom", TextArea).text = vg_custom_text
            except Exception:
                pass

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle preset selection — pre-fill form fields."""
        # Only one ListView on this screen (#preset_list), so no sender check needed
        idx = event.index if event.index is not None else 0
        if 0 <= idx < len(self._presets):
            self._apply_preset(self._presets[idx])
            # Move focus to the first checkbox so user can edit fields
            try:
                first_check = self.query(Checkbox).first()
                if first_check is not None:
                    first_check.focus()
            except Exception:
                pass

    def action_focus_previous(self) -> None:
        """Move focus to the previous focusable widget on this screen.

        Textual's :class:`Screen` exposes :meth:`focus_previous` as a
        method, not an ``action_*`` handler, so a ``Binding`` that
        references ``"focus_previous"`` silently does nothing without
        this shim. Same applies to :meth:`action_focus_next`.
        """
        self.focus_previous()

    def action_focus_next(self) -> None:
        """Move focus to the next focusable widget on this screen."""
        self.focus_next()

    def action_submit(self) -> None:
        free = False
        if self._show_free:
            try:
                free = bool(self.query_one("#chk_free", Checkbox).value)
            except Exception:
                free = False
        persistent = bool(self.query_one("#chk_persistent", Checkbox).value)
        integrity = bool(self.query_one("#chk_integrity", Checkbox).value)

        raw_budget = self.query_one("#inp_budget", Input).value or "0"
        try:
            budget = int(raw_budget.strip() or "0")
        except ValueError:
            budget = 0
        budget = max(budget, 0)

        raw_budget_run = self.query_one("#inp_budget_run", Input).value or "0"
        try:
            budget_run = int(raw_budget_run.strip() or "0")
        except ValueError:
            budget_run = 0
        budget_run = max(budget_run, 0)

        raw_task_timeout = self.query_one("#inp_task_timeout", Input).value or "0"
        try:
            task_timeout = int(raw_task_timeout.strip() or "0")
        except ValueError:
            task_timeout = 0
        task_timeout = max(task_timeout, 0)

        notify_complete = bool(self.query_one("#chk_notify_complete", Checkbox).value)
        notify_fail = bool(self.query_one("#chk_notify_fail", Checkbox).value)

        # ── Validation Gate ──────────────────────────────────────
        vg_enabled = bool(self.query_one("#chk_validation_gate", Checkbox).value)
        vg_checks: list[str] = []
        for check_id, check_name in [
            ("chk_vg_lint", "lint"),
            ("chk_vg_test", "test"),
            ("chk_vg_typecheck", "typecheck"),
        ]:
            try:
                if bool(self.query_one(f"#{check_id}", Checkbox).value):
                    vg_checks.append(check_name)
            except Exception:
                pass
        if not vg_checks:
            vg_checks = ["lint", "test", "typecheck"]
        vg_fail_fast = bool(self.query_one("#chk_vg_fail_fast", Checkbox).value)

        # Custom commands — parse "name=command" per line
        vg_custom: dict[str, str] = {}
        try:
            raw = self.query_one("#inp_vg_custom", TextArea).text.strip()
            if raw:
                for line in raw.split("\n"):
                    line = line.strip()
                    if "=" in line and line:
                        key, _, cmd = line.partition("=")
                        key = key.strip()
                        cmd = cmd.strip()
                        if key and cmd:
                            vg_custom[key] = cmd
        except Exception:
            pass

        self.dismiss(
            {
                "free": free,
                "persistent": persistent,
                "integrity": integrity,
                "token_budget_per_hour": budget,
                "token_budget_per_run": budget_run,
                "task_timeout": task_timeout,
                "notify_on_complete": notify_complete,
                "notify_on_fail": notify_fail,
                "validation_gate": {
                    "enabled": vg_enabled,
                    "checks": vg_checks,
                    "custom_commands": vg_custom,
                    "fail_fast": vg_fail_fast,
                },
            }
        )

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_go_back(self) -> None:
        """Navigate back to the previous pre-run screen."""
        self.dismiss(BACK_SENTINEL)  # type: ignore[arg-type]


# Legacy alias for tests that still reference the old class name.
ModeSelectionApp = ModeSelectionScreen


def run_mode_selection(
    show_free: bool = True,
    *,
    project: Path | None = None,
    initial_mode: dict[str, object] | None = None,
) -> dict[str, object] | object:
    """Show the mode-selection screen and return the chosen settings.

    Uses the currently active :class:`ArchitectAppRunner` if one is in
    flight — no fresh app boot, no alt-screen flash. Falls back to a
    minimal harness when no runner is hosting the CLI flow.

    Raises :class:`SystemExit` with code 0 when the user cancels.
    Returns ``BACK_SENTINEL`` on back.
    """
    from the_architect.tui.app import run_single_screen

    mode = initial_mode or {
        "free": False,
        "persistent": False,
        "integrity": True,
        "token_budget_per_hour": 0,
        "token_budget_per_run": 0,
        "task_timeout": 0,
        "notify_on_complete": True,
        "notify_on_fail": True,
        "validation_gate": {
            "enabled": True,
            "checks": ["lint", "test", "typecheck"],
            "custom_commands": {},
            "fail_fast": True,
        },
    }
    vg_mode: dict[str, object] = mode.get("validation_gate", {})  # type: ignore[assignment]
    if isinstance(vg_mode, dict):
        vg_enabled = bool(vg_mode.get("enabled", True))
        vg_checks_raw = vg_mode.get("checks", ["lint", "test", "typecheck"])
        if isinstance(vg_checks_raw, list):
            vg_checks: tuple[str, ...] = tuple(vg_checks_raw)
        else:
            vg_checks = ("lint", "test", "typecheck")
        vg_fail_fast = bool(vg_mode.get("fail_fast", True))
        vg_custom_raw = vg_mode.get("custom_commands", {})
        if isinstance(vg_custom_raw, dict):
            vg_custom: dict[str, str] = {k: str(v) for k, v in vg_custom_raw.items()}
        else:
            vg_custom = {}
    else:
        vg_enabled = True
        vg_checks = ("lint", "test", "typecheck")
        vg_fail_fast = True
        vg_custom = {}
    # Cast mode values — mode dict has `object` values from the broader signature
    screen = ModeSelectionScreen(
        show_free=show_free,
        project=project,
        initial_free=cast(bool, mode.get("free", False)),
        initial_persistent=cast(bool, mode.get("persistent", False)),
        initial_integrity=cast(bool, mode.get("integrity", True)),
        initial_budget=cast(int, mode.get("token_budget_per_hour", 0)),
        initial_budget_run=cast(int, mode.get("token_budget_per_run", 0)),
        initial_task_timeout=cast(int, mode.get("task_timeout", 0)),
        initial_notify_complete=cast(bool, mode.get("notify_on_complete", True)),
        initial_notify_fail=cast(bool, mode.get("notify_on_fail", True)),
        initial_validation_gate_enabled=vg_enabled,
        initial_validation_gate_checks=vg_checks,
        initial_validation_gate_custom_commands=vg_custom,
        initial_validation_gate_fail_fast=vg_fail_fast,
    )
    result = run_single_screen(screen)
    if result is BACK_SENTINEL:
        return BACK_SENTINEL
    if result is None:
        raise SystemExit(0)
    return result
