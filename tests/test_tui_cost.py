"""Tests for the Textual CostApp screen.

The CostApp displays cross-run cost analytics with multiple DataTables:
summary, model breakdown, top expensive tasks, and daily spending.
Tests mount the app inside a small harness.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from textual.app import App
from textual.widgets import DataTable, Static

from the_architect.tui.screens.cost_screen import CostApp, run_cost_screen


class _Harness(App[None]):
    """Minimal app that mounts CostApp as a screen for testing."""

    def __init__(self, screen: Any) -> None:
        super().__init__()
        self._screen = screen
        self.dismissed: Any = "<not-dismissed>"

    def on_mount(self) -> None:
        self.push_screen(self._screen, self._on_dismiss)

    def _on_dismiss(self, value: Any) -> None:
        self.dismissed = value


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_project(
    tmp_path: Path,
    *,
    with_ledger: bool = False,
    ledger_data: list[dict] | None = None,
) -> Path:
    """Create a temporary project with optional token ledger."""
    project = tmp_path / "proj"
    project.mkdir()

    # architect.toml — minimal
    config_file = project / "architect.toml"
    config_file.write_text("[architect]\n")

    # .architect/ dir
    arch_dir = project / ".architect"
    arch_dir.mkdir()

    if with_ledger or ledger_data is not None:
        ledger_file = arch_dir / "token_ledger.json"
        data = ledger_data if ledger_data is not None else []
        ledger_file.write_text(json.dumps(data))

    return project


def _sample_ledger_data() -> list[dict]:
    """Return sample ledger data with two runs across different models."""
    return [
        {
            "run_id": "run001",
            "timestamp": "2026-05-14T10:00:00+00:00",
            "goal_summary": "Build auth module",
            "total_tokens": 50000,
            "total_cost_estimate": 0.25,
            "model_breakdown": [
                {
                    "model": "gpt-4o",
                    "input_tokens": 30000,
                    "output_tokens": 20000,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "cost_estimate": 0.15,
                },
                {
                    "model": "claude-sonnet-4",
                    "input_tokens": 10000,
                    "output_tokens": 10000,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "cost_estimate": 0.10,
                },
            ],
            "task_breakdown": [
                {
                    "task_id": "T01",
                    "title": "Design auth schema",
                    "status": "done",
                    "input_tokens": 25000,
                    "output_tokens": 15000,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "model": "gpt-4o",
                    "cost_estimate": 0.12,
                    "duration_seconds": 120.0,
                },
                {
                    "task_id": "T02",
                    "title": "Implement login flow",
                    "status": "done",
                    "input_tokens": 25000,
                    "output_tokens": 15000,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "model": "claude-sonnet-4",
                    "cost_estimate": 0.13,
                    "duration_seconds": 95.0,
                },
            ],
            "task_count": 2,
            "outcome": "success",
            "duration_seconds": 215.0,
        },
        {
            "run_id": "run002",
            "timestamp": "2026-05-15T14:00:00+00:00",
            "goal_summary": "Add payment integration",
            "total_tokens": 80000,
            "total_cost_estimate": 0.50,
            "model_breakdown": [
                {
                    "model": "gpt-4o",
                    "input_tokens": 50000,
                    "output_tokens": 30000,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "cost_estimate": 0.35,
                },
            ],
            "task_breakdown": [
                {
                    "task_id": "T01",
                    "title": "Research payment APIs",
                    "status": "done",
                    "input_tokens": 20000,
                    "output_tokens": 15000,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "model": "gpt-4o",
                    "cost_estimate": 0.15,
                    "duration_seconds": 60.0,
                },
                {
                    "task_id": "T02",
                    "title": "Implement Stripe integration",
                    "status": "done",
                    "input_tokens": 30000,
                    "output_tokens": 15000,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "model": "gpt-4o",
                    "cost_estimate": 0.20,
                    "duration_seconds": 180.0,
                },
            ],
            "task_count": 2,
            "outcome": "success",
            "duration_seconds": 240.0,
        },
    ]


# ── Basic rendering ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cost_screen_has_widgets(tmp_path: Path) -> None:
    """CostApp has Header, Footer, and multiple DataTables."""
    project = _make_project(tmp_path)
    app = CostApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        assert len(app.query(Static)) >= 1
        # Should have exactly 3 DataTables (model, tasks, daily)
        tables = list(app.query(DataTable))
        assert len(tables) == 3


@pytest.mark.asyncio
async def test_cost_title_shows_project(tmp_path: Path) -> None:
    """Cost title widget contains the project path."""
    project = _make_project(tmp_path)
    app = CostApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        title = app.query_one("#cost_title", Static)
        rendered = str(title.render())
        assert "Cost Analytics" in rendered
        assert "proj" in rendered


@pytest.mark.asyncio
async def test_cost_hint_shown(tmp_path: Path) -> None:
    """Hint text is rendered."""
    project = _make_project(tmp_path)
    app = CostApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        hint = app.query_one("#cost_hint", Static)
        rendered = str(hint.render())
        assert "refresh" in rendered.lower()


# ── Data loading ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_model_breakdown_table_has_rows(tmp_path: Path) -> None:
    """Model breakdown table has rows when ledger data exists."""
    project = _make_project(tmp_path, ledger_data=_sample_ledger_data())
    app = CostApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        # First DataTable is the model breakdown table
        model_table = app.query_one("#model_table", DataTable)
        assert model_table.row_count >= 1
        # Should have columns: Model, Tokens, Cost, Runs, Avg Cost/Run
        for col_idx in range(5):
            cell = model_table.get_cell_at((0, col_idx))
            assert cell is not None


@pytest.mark.asyncio
async def test_top_tasks_table_has_rows(tmp_path: Path) -> None:
    """Top expensive tasks table has rows when ledger data exists."""
    project = _make_project(tmp_path, ledger_data=_sample_ledger_data())
    app = CostApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        tasks_table = app.query_one("#tasks_table", DataTable)
        assert tasks_table.row_count >= 1


@pytest.mark.asyncio
async def test_daily_spending_table_has_rows(tmp_path: Path) -> None:
    """Daily spending table has rows when ledger data exists."""
    project = _make_project(tmp_path, ledger_data=_sample_ledger_data())
    app = CostApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        daily_table = app.query_one("#daily_table", DataTable)
        assert daily_table.row_count >= 1


@pytest.mark.asyncio
async def test_summary_shows_totals(tmp_path: Path) -> None:
    """Summary section shows total cost and tokens when data exists."""
    project = _make_project(tmp_path, ledger_data=_sample_ledger_data())
    app = CostApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        summary = app.query_one("#summary_section", Static)
        rendered = str(summary.render())
        assert "Total cost" in rendered or "$" in rendered
        assert "Total tokens" in rendered or "Tokens" in rendered


# ── Graceful degradation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_ledger_shows_message(tmp_path: Path) -> None:
    """Missing ledger file shows 'No cost data available' message."""
    project = _make_project(tmp_path)
    # No ledger file created
    app = CostApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        summary = app.query_one("#summary_section", Static)
        rendered = str(summary.render())
        assert "No cost data available" in rendered


@pytest.mark.asyncio
async def test_empty_ledger_shows_message(tmp_path: Path) -> None:
    """Empty ledger file shows 'No cost data available' message."""
    project = _make_project(tmp_path, ledger_data=[])
    app = CostApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        summary = app.query_one("#summary_section", Static)
        rendered = str(summary.render())
        assert "No cost data available" in rendered


@pytest.mark.asyncio
async def test_no_ledger_tables_empty(tmp_path: Path) -> None:
    """All DataTables are empty when no ledger data exists."""
    project = _make_project(tmp_path)
    app = CostApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        for selector in ("#model_table", "#tasks_table", "#daily_table"):
            table = app.query_one(selector, DataTable)
            assert table.row_count == 0


# ── Refresh action ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_action_refresh(tmp_path: Path) -> None:
    """action_refresh calls _refresh without error."""
    project = _make_project(tmp_path, ledger_data=_sample_ledger_data())
    app = CostApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        # Should not raise
        app.action_refresh()
        await pilot.pause(0.1)


# ── run_cost_screen function ───────────────────────────────────────────


def test_run_cost_screen_calls_run(tmp_path: Path) -> None:
    """run_cost_screen launches the CostApp."""
    project = _make_project(tmp_path)
    with patch.object(CostApp, "run") as mock_run:
        run_cost_screen(project)
        mock_run.assert_called_once()


# ── CLI mutual exclusion ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cli_tui_mutual_exclusion_with_json(tmp_path: Path) -> None:
    """--tui and --json are mutually exclusive."""
    from click.testing import CliRunner

    # Create a project with an architect.toml so the path validator passes
    project = _make_project(tmp_path, ledger_data=_sample_ledger_data())

    runner = CliRunner()
    from the_architect.cli import main

    result = runner.invoke(
        main,
        ["cost", "-p", str(project), "--tui", "--json"],
    )
    assert result.exit_code != 0
    assert (
        "mutually exclusive" in result.output.lower()
        or "cannot be combined" in result.output.lower()
    )


@pytest.mark.asyncio
async def test_cli_tui_mutual_exclusion_with_since(tmp_path: Path) -> None:
    """--tui and --since are mutually exclusive."""
    from click.testing import CliRunner

    project = _make_project(tmp_path, ledger_data=_sample_ledger_data())

    runner = CliRunner()
    from the_architect.cli import main

    result = runner.invoke(
        main,
        ["cost", "-p", str(project), "--tui", "--since", "2026-01-01"],
    )
    assert result.exit_code != 0
    assert (
        "mutually exclusive" in result.output.lower()
        or "cannot be combined" in result.output.lower()
    )


@pytest.mark.asyncio
async def test_cli_tui_mutual_exclusion_with_model(tmp_path: Path) -> None:
    """--tui and --model are mutually exclusive."""
    from click.testing import CliRunner

    project = _make_project(tmp_path, ledger_data=_sample_ledger_data())

    runner = CliRunner()
    from the_architect.cli import main

    result = runner.invoke(
        main,
        ["cost", "-p", str(project), "--tui", "--model", "gpt-4o"],
    )
    assert result.exit_code != 0
    assert (
        "mutually exclusive" in result.output.lower()
        or "cannot be combined" in result.output.lower()
    )
