"""Tests for the Textual DepsApp (task dependencies inspector screen).

The DepsApp displays forward and reverse dependency information for every
discovered task, along with priority indicators and PROGRESS.md status.
Tests mount the app inside a small harness following the circuit test pattern.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from textual.app import App
from textual.widgets import DataTable, Static

from the_architect.cli import deps_cmd
from the_architect.tui.screens.deps_screen import DepsApp, run_deps_screen


class _Harness(App[None]):
    """Minimal app that mounts DepsApp as a screen for testing."""

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
    tasks: dict[str, str] | None = None,
) -> Path:
    """Create a temporary project with optional task files.

    Args:
        tmp_path: Pytest tmp_path fixture.
        tasks: Mapping of filename (e.g. ``"T01_setup.md"``) to file contents.

    Returns:
        Path to the created project root directory.
    """
    project = tmp_path / "proj"
    project.mkdir()

    # architect.toml — minimal so load_config returns defaults
    config_file = project / "architect.toml"
    config_file.write_text("[architect]\n", encoding="utf-8")

    if tasks:
        tasks_dir = project / "tasks"
        tasks_dir.mkdir()
        for filename, content in tasks.items():
            (tasks_dir / filename).write_text(content, encoding="utf-8")

    return project


# ── Basic rendering ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deps_table_columns(tmp_path: Path) -> None:
    """DataTable has correct columns: Task, Title, Priority, Depends On, Depended By, Status."""
    project = _make_project(tmp_path, tasks={"T01_setup.md": "# T01 — Setup\n"})
    app = DepsApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        # Verify all 6 columns are accessible by reading cells in the first row
        assert table.row_count >= 1
        for col_idx in range(6):
            cell = table.get_cell_at((0, col_idx))
            assert cell is not None


@pytest.mark.asyncio
async def test_deps_title_shows_project(tmp_path: Path) -> None:
    """Title widget contains 'Task Dependencies' and project path."""
    project = _make_project(tmp_path)
    app = DepsApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        title = app.query_one("#deps_title", Static)
        rendered = str(title.render())
        assert "Task Dependencies" in rendered
        assert "proj" in rendered


# ── Empty / no tasks ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_tasks_empty(tmp_path: Path) -> None:
    """No tasks directory shows 'No tasks found' message and empty table."""
    project = _make_project(tmp_path)  # no tasks dir created
    app = DepsApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count == 0
        summary = app.query_one("#deps_summary", Static)
        rendered = str(summary.render())
        assert "No tasks found" in rendered


# ── Forward dependencies ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_without_dependencies(tmp_path: Path) -> None:
    """Task with no depends_on shows '—' in Depends On column."""
    project = _make_project(
        tmp_path,
        tasks={"T01_setup.md": "# T01 — Setup\n"},
    )
    app = DepsApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count >= 1
        # Depends On is column index 3
        deps_cell = str(table.get_cell_at((0, 3)))
        assert "—" in deps_cell


@pytest.mark.asyncio
async def test_task_with_forward_deps(tmp_path: Path) -> None:
    """Task with ## Dependencies section shows '→ T01' in Depends On column."""
    project = _make_project(
        tmp_path,
        tasks={
            "T01_base.md": "# T01 — Base\n",
            "T02_build.md": "# T02 — Build\n## Dependencies\n- T01\n",
        },
    )
    app = DepsApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count >= 2
        # Find T02 row (sorted by prefix)
        for row_idx in range(table.row_count):
            task_cell = str(table.get_cell_at((row_idx, 0)))
            if task_cell == "T02":
                deps_cell = str(table.get_cell_at((row_idx, 3)))
                assert "\u2192" in deps_cell  # → arrow
                assert "T01" in deps_cell
                return
        pytest.fail("T02 row not found in table")


# ── Reverse dependencies ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reverse_dependency_display(tmp_path: Path) -> None:
    """When T02 depends on T01, T01's Depended By column shows '← T02'."""
    project = _make_project(
        tmp_path,
        tasks={
            "T01_base.md": "# T01 — Base\n",
            "T02_build.md": "# T02 — Build\n## Dependencies\n- T01\n",
        },
    )
    app = DepsApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        # Find T01 row
        for row_idx in range(table.row_count):
            task_cell = str(table.get_cell_at((row_idx, 0)))
            if task_cell == "T01":
                # Depended By is column index 4
                dep_by_cell = str(table.get_cell_at((row_idx, 4)))
                assert "\u2190" in dep_by_cell  # ← arrow
                assert "T02" in dep_by_cell
                return
        pytest.fail("T01 row not found in table")


# ── Priority display ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_priority_display(tmp_path: Path) -> None:
    """Task with ## Priority: critical shows red priority indicator."""
    project = _make_project(
        tmp_path,
        tasks={
            "T01_critical.md": "# T01 — Critical Task\n## Priority\ncritical\n",
        },
    )
    app = DepsApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count >= 1
        # Priority is column index 2
        priority_cell = str(table.get_cell_at((0, 2)))
        assert "Critical" in priority_cell
        # The cell should contain red markup
        assert "[red]" in priority_cell


@pytest.mark.asyncio
async def test_priority_medium_default(tmp_path: Path) -> None:
    """Task with no priority section shows Medium (default)."""
    project = _make_project(
        tmp_path,
        tasks={"T01_default.md": "# T01 — Default Priority\n"},
    )
    app = DepsApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count >= 1
        priority_cell = str(table.get_cell_at((0, 2)))
        assert "Medium" in priority_cell


@pytest.mark.asyncio
async def test_priority_high_display(tmp_path: Path) -> None:
    """Task with ## Priority: high shows yellow priority indicator."""
    project = _make_project(
        tmp_path,
        tasks={
            "T01_urgent.md": "# T01 — Urgent\n## Priority\nhigh\n",
        },
    )
    app = DepsApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count >= 1
        priority_cell = str(table.get_cell_at((0, 2)))
        assert "High" in priority_cell
        assert "[yellow]" in priority_cell


# ── Summary widget ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_shown(tmp_path: Path) -> None:
    """Summary widget renders with total task count."""
    project = _make_project(
        tmp_path,
        tasks={
            "T01_first.md": "# T01 — First\n",
            "T02_second.md": "# T02 — Second\n",
            "T03_third.md": "# T03 — Third\n## Dependencies\n- T01\n",
        },
    )
    app = DepsApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        summary = app.query_one("#deps_summary", Static)
        rendered = str(summary.render())
        assert "Total tasks: 3" in rendered
        # T03 has dependencies
        assert "Tasks with dependencies: 1" in rendered


# ── Refresh action ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_action_refresh(tmp_path: Path) -> None:
    """action_refresh() can be called without error."""
    project = _make_project(
        tmp_path,
        tasks={"T01_setup.md": "# T01 — Setup\n"},
    )
    app = DepsApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        # Should not raise
        app.action_refresh()
        await pilot.pause(0.05)


# ── run_deps_screen function ───────────────────────────────────────────


def test_run_deps_screen_calls_run(tmp_path: Path) -> None:
    """run_deps_screen launches the DepsApp."""
    project = _make_project(tmp_path)
    with patch.object(DepsApp, "run") as mock_run:
        run_deps_screen(project)
        mock_run.assert_called_once()


# ── CLI integration ────────────────────────────────────────────────────


def test_deps_cmd_tui_flag(tmp_path: Path) -> None:
    """deps_cmd --tui launches the TUI deps screen via run_deps_screen."""
    project = _make_project(
        tmp_path,
        tasks={"T01_setup.md": "# T01 — Setup\n"},
    )
    with patch("the_architect.tui.screens.run_deps_screen") as mock_run:
        runner = CliRunner()
        runner.invoke(deps_cmd, ["--project", str(project), "--tui"])
        mock_run.assert_called_once()


# ── Dependency cycle detection in summary ──────────────────────────────


@pytest.mark.asyncio
async def test_summary_shows_cycle_warning(tmp_path: Path) -> None:
    """Summary widget warns about dependency cycles."""
    project = _make_project(
        tmp_path,
        tasks={
            "T01_a.md": "# T01 — A\n## Dependencies\n- T02\n",
            "T02_b.md": "# T02 — B\n## Dependencies\n- T01\n",
        },
    )
    app = DepsApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        summary = app.query_one("#deps_summary", Static)
        rendered = str(summary.render())
        assert "Dependency cycles detected" in rendered


# ── Multiple forward dependencies ──────────────────────────────────────


@pytest.mark.asyncio
async def test_task_with_multiple_forward_deps(tmp_path: Path) -> None:
    """Task depending on multiple tasks shows all in Depends On column."""
    project = _make_project(
        tmp_path,
        tasks={
            "T01_a.md": "# T01 — A\n",
            "T02_b.md": "# T02 — B\n",
            "T03_c.md": "# T03 — C\n## Dependencies\n- T01\n- T02\n",
        },
    )
    app = DepsApp(project=project)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        # Find T03 row
        for row_idx in range(table.row_count):
            task_cell = str(table.get_cell_at((row_idx, 0)))
            if task_cell == "T03":
                deps_cell = str(table.get_cell_at((row_idx, 3)))
                assert "T01" in deps_cell
                assert "T02" in deps_cell
                return
        pytest.fail("T03 row not found in table")
