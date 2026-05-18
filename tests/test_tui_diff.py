"""Tests for the Textual DiffApp screen."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.widgets import DataTable

from the_architect.tui.screens.diff_screen import DiffApp, run_diff_screen

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_baseline(baselines_dir: Path, task_prefix: str, files: dict[str, str]) -> None:
    """Write a minimal baseline JSON file for testing.

    Args:
        baselines_dir: Directory to write the baseline into.
        task_prefix: Task prefix (e.g. T01).
        files: Mapping of relative path to content hash.
    """
    baseline = {
        "timestamp": "2026-05-17T00:00:00+00:00",
        "task_prefix": task_prefix,
        "files": {
            path: {
                "path": path,
                "sha256": hash,
                "size": len(hash) * 2,
            }
            for path, hash in files.items()
        },
    }
    baselines_dir.mkdir(parents=True, exist_ok=True)
    (baselines_dir / f"{task_prefix}.json").write_text(json.dumps(baseline), encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests — empty states
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_screen_no_baselines_dir(tmp_path: Path) -> None:
    """Screen shows 'No baseline data' when baselines directory is missing."""
    app = DiffApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count == 1
        cell = str(table.get_cell_at((0, 0)))
        assert cell == "—"


@pytest.mark.asyncio
async def test_diff_screen_empty_baselines_dir(tmp_path: Path) -> None:
    """Screen shows 'No baseline data' when baselines directory is empty."""
    baselines_dir = tmp_path / ".architect" / "baselines"
    baselines_dir.mkdir(parents=True)

    app = DiffApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count == 1
        cell = str(table.get_cell_at((0, 0)))
        assert cell == "—"


# ---------------------------------------------------------------------------
# Tests — single task with changes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_screen_single_task_created(tmp_path: Path) -> None:
    """Screen shows created files for a single task."""
    baselines_dir = tmp_path / ".architect" / "baselines"

    # Write baseline with one file that has a different hash than current workspace
    _write_baseline(baselines_dir, "T01", {"new_file.py": "abc123"})

    # Create the actual file on disk with different content
    (tmp_path / "new_file.py").write_text("hello world", encoding="utf-8")

    app = DiffApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count >= 1
        # First row should be T01 with a change type
        task_cell = str(table.get_cell_at((0, 0)))
        assert task_cell == "T01"
        change_cell = str(table.get_cell_at((0, 1)))
        assert change_cell in ("Created", "Modified")


# ---------------------------------------------------------------------------
# Tests — multiple tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_screen_multiple_tasks(tmp_path: Path) -> None:
    """Screen shows data from multiple baseline files."""
    baselines_dir = tmp_path / ".architect" / "baselines"

    _write_baseline(baselines_dir, "T01", {"file_a.py": "hash001"})
    _write_baseline(baselines_dir, "T02", {"file_b.py": "hash002"})

    # Create actual files with different content
    (tmp_path / "file_a.py").write_text("content A", encoding="utf-8")
    (tmp_path / "file_b.py").write_text("content B", encoding="utf-8")

    app = DiffApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        # Should have at least 2 rows (one per task file)
        assert table.row_count >= 2
        # Check that both tasks appear
        task_cells = {str(table.get_cell_at((i, 0))) for i in range(table.row_count)}
        assert "T01" in task_cells
        assert "T02" in task_cells


# ---------------------------------------------------------------------------
# Tests — run_diff_screen function
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_diff_screen_launches_app(tmp_path: Path) -> None:
    """run_diff_screen creates and runs a DiffApp instance."""
    import unittest.mock as mock

    with mock.patch.object(DiffApp, "run") as mock_run:
        run_diff_screen(project=tmp_path)
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — refresh binding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_screen_refresh_binding(tmp_path: Path) -> None:
    """Pressing 'r' triggers a refresh without error."""
    baselines_dir = tmp_path / ".architect" / "baselines"
    baselines_dir.mkdir(parents=True)

    app = DiffApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        await pilot.press("r")
        await pilot.pause(0.05)
        # Should not raise; table should still exist
        table = app.query_one(DataTable)
        assert table.row_count >= 0
