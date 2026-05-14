"""Tests for live cost tracking features (T02-T05).

Covers:
- MonitorStateWriter accumulates costs when model+tokens are provided
- MonitorStateWriter backward compat (old call style still works)
- SuccessScreen renders cost estimate when session_cost_usd > 0
- ExecutionScreen update_costs stores data and _render_costs uses it
- TuiSession.update_costs is a no-op when app is None
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from the_architect.core.tasks import Task, TaskStatus


def _make_task(tmp_path: Path, prefix: str = "T01", title: str = "Test task") -> Task:
    """Create a minimal Task for test fixtures."""
    path = tmp_path / "tasks" / f"{prefix}_test.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {prefix} — {title}\n", encoding="utf-8")
    # Extract number from prefix (T01 → 1, T02 → 2)
    number = int(prefix[1:]) if len(prefix) > 1 and prefix[1:].isdigit() else 1
    return Task(
        name=f"{prefix}_test",
        prefix=prefix,
        number=number,
        path=path,
        title=title,
        status=TaskStatus.PENDING,
    )


# ---------------------------------------------------------------------------
# T02 — MonitorStateWriter cost accumulation
# ---------------------------------------------------------------------------


def test_monitor_state_accumulates_cost(tmp_path: Path) -> None:
    """on_task_done with model+tokens should update _session_cost_usd."""
    from the_architect.core.monitor_state import MonitorStateWriter

    tasks = [_make_task(tmp_path)]
    writer = MonitorStateWriter(project_dir=tmp_path, tasks=tasks)

    writer.on_task_done(
        "T01",
        tokens=1000,
        input_tokens=800,
        output_tokens=200,
        model="claude-sonnet-4-5",
    )
    # claude-sonnet-4-5 is in the pricing table — cost must be > 0
    assert writer._session_cost_usd > 0
    assert "claude-sonnet-4-5" in writer._model_costs

    # Verify it's in the flushed JSON
    state_file = tmp_path / ".architect" / "monitor_state.json"
    state = json.loads(state_file.read_text())
    assert state["tokens"]["session_cost_usd"] > 0
    assert "claude-sonnet-4-5" in state["tokens"]["model_costs"]


def test_monitor_state_accumulates_cost_on_failure(tmp_path: Path) -> None:
    """on_task_failed with model+tokens should also update _session_cost_usd."""
    from the_architect.core.monitor_state import MonitorStateWriter

    tasks = [_make_task(tmp_path)]
    writer = MonitorStateWriter(project_dir=tmp_path, tasks=tasks)

    writer.on_task_failed(
        "T01",
        tokens=500,
        input_tokens=400,
        output_tokens=100,
        model="claude-sonnet-4-5",
    )
    assert writer._session_cost_usd > 0
    assert "claude-sonnet-4-5" in writer._model_costs


def test_monitor_state_old_call_style_still_works(tmp_path: Path) -> None:
    """on_task_done(task_id, tokens=X) must not raise and cost stays 0.0."""
    from the_architect.core.monitor_state import MonitorStateWriter

    tasks = [_make_task(tmp_path)]
    writer = MonitorStateWriter(project_dir=tmp_path, tasks=tasks)
    writer.on_task_done("T01", tokens=500)  # old-style — no error
    assert writer._session_cost_usd == 0.0  # no model → no cost


def test_monitor_state_old_task_failed_style_still_works(tmp_path: Path) -> None:
    """on_task_failed(task_id, tokens=X) must not raise and cost stays 0.0."""
    from the_architect.core.monitor_state import MonitorStateWriter

    tasks = [_make_task(tmp_path)]
    writer = MonitorStateWriter(project_dir=tmp_path, tasks=tasks)
    writer.on_task_failed("T01", tokens=300)  # old-style — no error
    assert writer._session_cost_usd == 0.0  # no model → no cost


def test_monitor_state_last_task_cost_updates(tmp_path: Path) -> None:
    """_last_task_cost_usd should reflect only the most recent task."""
    from the_architect.core.monitor_state import MonitorStateWriter

    tasks = [_make_task(tmp_path, "T01"), _make_task(tmp_path, "T02")]
    writer = MonitorStateWriter(project_dir=tmp_path, tasks=tasks)

    writer.on_task_done(
        "T01", tokens=1000, input_tokens=800, output_tokens=200, model="claude-sonnet-4-5"
    )
    first_last = writer._last_task_cost_usd
    assert first_last > 0

    writer.on_task_done(
        "T02", tokens=200, input_tokens=100, output_tokens=100, model="claude-sonnet-4-5"
    )
    second_last = writer._last_task_cost_usd
    # Session total should be sum; last-task should be only the second task
    assert writer._session_cost_usd == pytest.approx(first_last + second_last)
    assert writer._last_task_cost_usd == second_last


def test_monitor_state_json_includes_cost_fields(tmp_path: Path) -> None:
    """Flushed JSON tokens section must include all new cost keys."""
    from the_architect.core.monitor_state import MonitorStateWriter

    tasks = [_make_task(tmp_path)]
    MonitorStateWriter(project_dir=tmp_path, tasks=tasks)

    state_file = tmp_path / ".architect" / "monitor_state.json"
    state = json.loads(state_file.read_text())
    tokens = state["tokens"]

    # All new keys must be present even before any task runs
    assert "session_cost_usd" in tokens
    assert "last_task_cost_usd" in tokens
    assert "model_costs" in tokens
    assert tokens["session_cost_usd"] == 0.0
    assert tokens["model_costs"] == {}


# ---------------------------------------------------------------------------
# T03 — ExecutionScreen Costs tab
# ---------------------------------------------------------------------------


def test_execution_screen_update_costs_stores_data() -> None:
    """update_costs should store data and _render_costs should use it."""
    from the_architect.tui.screens.execution import ExecutionScreen

    screen = ExecutionScreen()
    screen.update_costs(
        {
            "session_cost_usd": 0.25,
            "last_task_cost_usd": 0.05,
            "session_tokens": 50000,
            "model_costs": {"claude-sonnet-4-5": 0.25},
        }
    )
    rendered = screen._render_costs()
    assert "$0.2500" in rendered
    assert "50.0K" in rendered
    assert "claude-sonnet-4-5" in rendered


def test_execution_screen_render_costs_empty() -> None:
    """_render_costs with no data should show placeholder text."""
    from the_architect.tui.screens.execution import ExecutionScreen

    screen = ExecutionScreen()
    rendered = screen._render_costs()
    assert "Cost data will appear" in rendered


def test_execution_screen_render_costs_last_task_cost() -> None:
    """_render_costs should show last task cost when > 0."""
    from the_architect.tui.screens.execution import ExecutionScreen

    screen = ExecutionScreen()
    screen.update_costs(
        {
            "session_cost_usd": 0.10,
            "last_task_cost_usd": 0.04,
            "session_tokens": 10000,
            "model_costs": {},
        }
    )
    rendered = screen._render_costs()
    assert "$0.0400" in rendered


def test_execution_screen_has_costs_tab_binding() -> None:
    """ExecutionScreen BINDINGS must include a 'c' key for the Costs tab."""
    from the_architect.tui.screens.execution import ExecutionScreen

    binding_keys = {b.key for b in ExecutionScreen.BINDINGS}
    assert "c" in binding_keys


def test_execution_screen_pending_costs_stored_before_mount() -> None:
    """update_costs before mount should store costs in _pending_costs."""
    from the_architect.tui.screens.execution import ExecutionScreen

    screen = ExecutionScreen()
    costs = {"session_cost_usd": 1.0}
    # Before mount, query_one will raise → falls back to _pending_costs
    screen.update_costs(costs)
    # Either stored in _costs (if query worked) or in _pending_costs
    assert screen._costs or screen._pending_costs is not None


# ---------------------------------------------------------------------------
# T04 — TuiSession.update_costs
# ---------------------------------------------------------------------------


def test_tui_session_update_costs_noop_without_app() -> None:
    """update_costs should not raise when app is None."""
    from the_architect.core.runner import PlainStreamRenderer
    from the_architect.tui.session import TuiSession

    session = TuiSession(renderer=PlainStreamRenderer(), app=None, thread=None)
    session.update_costs({"session_cost_usd": 1.0})  # must not raise


def test_tui_session_update_costs_delegates_to_app() -> None:
    """update_costs should call app.update_costs when app is set."""
    from unittest.mock import MagicMock

    from the_architect.core.runner import PlainStreamRenderer
    from the_architect.tui.session import TuiSession

    mock_app = MagicMock()
    session = TuiSession(renderer=PlainStreamRenderer(), app=mock_app, thread=None)
    costs = {"session_cost_usd": 0.5, "model_costs": {}}
    session.update_costs(costs)
    mock_app.update_costs.assert_called_once_with(costs)


# ---------------------------------------------------------------------------
# T05 — SuccessScreen cost display
# ---------------------------------------------------------------------------


def test_success_screen_renders_cost() -> None:
    """SuccessScreen._render_totals should include cost if session_cost_usd > 0."""
    from the_architect.core.runner import TaskResult, TokenUsage
    from the_architect.tui.screens.success import SuccessScreen

    result = TaskResult(
        prefix="T01",
        title="Test",
        status="done",
        duration_seconds=5.0,
        attempts=1,
        tokens=TokenUsage(input_tokens=500, output_tokens=200),
        model="claude-sonnet-4-5",
    )
    screen = SuccessScreen(
        results=[result],
        total_duration=5.0,
        total_tokens=result.tokens,
        session_cost_usd=0.0042,
    )
    rendered = screen._render_totals()
    assert "$0.0042" in rendered


def test_success_screen_no_cost_when_zero() -> None:
    """SuccessScreen._render_totals should not mention cost when session_cost_usd == 0."""
    from the_architect.core.runner import TaskResult, TokenUsage
    from the_architect.tui.screens.success import SuccessScreen

    result = TaskResult(
        prefix="T01",
        title="Test",
        status="done",
        duration_seconds=5.0,
        attempts=1,
        tokens=TokenUsage(input_tokens=0, output_tokens=0),
        model="",
    )
    screen = SuccessScreen(
        results=[result],
        total_duration=5.0,
        total_tokens=result.tokens,
        session_cost_usd=0.0,
    )
    rendered = screen._render_totals()
    assert "$" not in rendered


def test_success_screen_computes_cost_when_not_supplied() -> None:
    """SuccessScreen should auto-compute cost from results when session_cost_usd=0."""
    from the_architect.core.runner import TaskResult, TokenUsage
    from the_architect.tui.screens.success import SuccessScreen

    result = TaskResult(
        prefix="T01",
        title="Test",
        status="done",
        duration_seconds=5.0,
        attempts=1,
        tokens=TokenUsage(input_tokens=10000, output_tokens=5000),
        model="claude-sonnet-4-5",
    )
    # Pass session_cost_usd=0.0 (default) — screen should auto-compute
    screen = SuccessScreen(
        results=[result],
        total_duration=5.0,
        total_tokens=result.tokens,
    )
    # After __init__, _session_cost_usd should be auto-computed from result
    assert screen._session_cost_usd > 0
    rendered = screen._render_totals()
    assert "$" in rendered
