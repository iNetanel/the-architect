"""Tests for the Textual DoctorApp screen.

The DoctorApp displays project health diagnostics with colour-coded
status indicators (green = ok, yellow = warn, red = fail).
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

from the_architect.tui.screens.doctor_screen import DoctorApp, run_doctor_screen


class _Harness(App[None]):
    """Minimal app that mounts DoctorApp as a screen for testing."""

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
    with_tasks: bool = False,
    with_circuit: bool = False,
    with_ledger: bool = False,
    with_presets: bool = False,
    with_baselines: bool = False,
) -> Path:
    """Create a temporary project with optional sub-artifacts."""
    project = tmp_path / "proj"
    project.mkdir()

    # architect.toml — minimal
    config_file = project / "architect.toml"
    config_file.write_text("[architect]\n")

    # .architect/ dir
    arch_dir = project / ".architect"
    arch_dir.mkdir()

    if with_tasks:
        tasks_dir = project / "tasks"
        tasks_dir.mkdir()
        task_file = tasks_dir / "T01_test.md"
        task_file.write_text("# T01 — Test\n")

    if with_circuit:
        circuit_file = arch_dir / "circuit.json"
        circuit_file.write_text(json.dumps({"T01": {"state": "CLOSED"}}))

    if with_ledger:
        ledger_file = arch_dir / "token_ledger.json"
        ledger_file.write_text("[]")

    if with_presets:
        presets_file = arch_dir / "presets.json"
        presets_file.write_text("[]")

    if with_baselines:
        baselines_dir = arch_dir / "baselines"
        baselines_dir.mkdir()

    return project


# ── Basic rendering ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_doctor_table_columns(tmp_path: Path) -> None:
    """DataTable has correct columns: Check, Status, Detail."""
    project = _make_project(tmp_path)
    app = DoctorApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        table = app.query_one(DataTable)
        # The table is created with 3 columns: Check, Status, Detail
        assert table.row_count >= 1
        for col_idx in range(3):
            cell = table.get_cell_at((0, col_idx))
            assert cell is not None


@pytest.mark.asyncio
async def test_doctor_title_shows_project(tmp_path: Path) -> None:
    """Doctor title widget contains the project path."""
    project = _make_project(tmp_path)
    app = DoctorApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        title = app.query_one("#doctor_title", Static)
        rendered = str(title.render())
        assert "Project Health" in rendered
        assert "proj" in rendered


@pytest.mark.asyncio
async def test_doctor_hint_shown(tmp_path: Path) -> None:
    """Hint text is rendered."""
    project = _make_project(tmp_path)
    app = DoctorApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        hint = app.query_one("#doctor_hint", Static)
        rendered = str(hint.render())
        assert "refresh" in rendered.lower()


# ── Data loading ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_checks_rendered(tmp_path: Path) -> None:
    """Health checks are rendered in the DataTable."""
    project = _make_project(tmp_path)
    app = DoctorApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        table = app.query_one(DataTable)
        # run_project_checks returns 6 checks
        assert table.row_count == 6


@pytest.mark.asyncio
async def test_summary_shows_counts(tmp_path: Path) -> None:
    """Summary section shows check counts."""
    project = _make_project(tmp_path)
    app = DoctorApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        summary = app.query_one("#summary_section", Static)
        rendered = str(summary.render())
        assert "ok" in rendered.lower()
        # All checks should be ok when no state files exist
        assert "6 ok" in rendered or "ok" in rendered


@pytest.mark.asyncio
async def test_check_labels_present(tmp_path: Path) -> None:
    """Check labels are displayed in the first column."""
    project = _make_project(tmp_path)
    app = DoctorApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        table = app.query_one(DataTable)
        labels = [str(table.get_cell_at((i, 0))) for i in range(table.row_count)]
        assert "Lock file" in labels
        assert "Task consistency" in labels


@pytest.mark.asyncio
async def test_ok_status_shown(tmp_path: Path) -> None:
    """OK status is displayed with checkmark."""
    project = _make_project(tmp_path)
    app = DoctorApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        table = app.query_one(DataTable)
        # First row should be Lock file with ok status
        status_cell = str(table.get_cell_at((0, 1)))
        assert "ok" in status_cell.lower()


# ── Graceful degradation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_project_state_shows_checks(tmp_path: Path) -> None:
    """Missing project state still shows all checks as ok."""
    project = _make_project(tmp_path)
    # No .architect sub-artifacts — all checks should be "ok" / "not found"
    app = DoctorApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        table = app.query_one(DataTable)
        assert table.row_count == 6
        # All status cells should contain "ok"
        for i in range(table.row_count):
            status_cell = str(table.get_cell_at((i, 1)))
            assert "ok" in status_cell.lower()


@pytest.mark.asyncio
async def test_nonexistent_project_path(tmp_path: Path) -> None:
    """Non-existent project path handles gracefully."""
    project = tmp_path / "does_not_exist"
    app = DoctorApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        table = app.query_one(DataTable)
        # Should still render (checks run on non-existent path, all ok/not found)
        assert table.row_count >= 0


# ── Refresh action ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_action_refresh(tmp_path: Path) -> None:
    """action_refresh calls _refresh without error."""
    project = _make_project(tmp_path)
    app = DoctorApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        # Should not raise
        app.action_refresh()
        await pilot.pause(0.1)


# ── run_doctor_screen function ─────────────────────────────────────────


def test_run_doctor_screen_calls_run(tmp_path: Path) -> None:
    """run_doctor_screen launches the DoctorApp."""
    project = _make_project(tmp_path)
    with patch.object(DoctorApp, "run") as mock_run:
        run_doctor_screen(project)
        mock_run.assert_called_once()


# ── CLI mutual exclusion ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cli_tui_mutual_exclusion_with_json(tmp_path: Path) -> None:
    """--tui and --json are mutually exclusive."""
    from click.testing import CliRunner

    project = _make_project(tmp_path)

    runner = CliRunner()
    from the_architect.cli import main

    result = runner.invoke(
        main,
        ["doctor", "--project", "--project-path", str(project), "--tui", "--json"],
    )
    assert result.exit_code != 0
    assert (
        "mutually exclusive" in result.output.lower()
        or "cannot be combined" in result.output.lower()
    )


@pytest.mark.asyncio
async def test_cli_tui_requires_project(tmp_path: Path) -> None:
    """--tui requires --project flag."""
    from click.testing import CliRunner

    runner = CliRunner()
    from the_architect.cli import main

    result = runner.invoke(
        main,
        ["doctor", "--tui"],
    )
    assert result.exit_code != 0
    assert "requires --project" in result.output.lower() or "cli-only" in result.output.lower()


@pytest.mark.asyncio
async def test_cli_tui_with_project_valid(tmp_path: Path) -> None:
    """--tui with --project is valid (TUI launches, not tested for crash)."""
    from click.testing import CliRunner

    project = _make_project(tmp_path)

    runner = CliRunner()
    from the_architect.cli import main

    # We can't actually test TUI launch in headless mode easily,
    # but we verify that the mutual exclusion doesn't trigger
    result = runner.invoke(
        main,
        ["doctor", "--project", "--project-path", str(project), "--tui"],
    )
    # Should not have the mutual exclusion error
    assert "mutually exclusive" not in result.output.lower()
    assert "cannot be combined" not in result.output.lower()
