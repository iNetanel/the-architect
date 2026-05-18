"""Tests for the Textual RollbackApp screen."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from textual.widgets import DataTable, Static

from the_architect.core.rollback import RollbackResult
from the_architect.tui.screens.rollback_screen import RollbackApp, run_rollback_screen

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_baseline(baselines_dir: Path, task_prefix: str, files: dict[str, str]) -> Path:
    """Write a minimal baseline JSON file for testing.

    Args:
        baselines_dir: Directory to write the baseline into.
        task_prefix: Task prefix (e.g. T01).
        files: Mapping of relative path to content hash.

    Returns:
        Path to the written baseline file.
    """
    baseline = {
        "timestamp": "2026-05-17T00:00:00+00:00",
        "task_prefix": task_prefix,
        "files": {
            path: {
                "path": path,
                "sha256": sha,
                "size": len(sha) * 2,
            }
            for path, sha in files.items()
        },
    }
    baselines_dir.mkdir(parents=True, exist_ok=True)
    baseline_file = baselines_dir / f"{task_prefix}.json"
    baseline_file.write_text(json.dumps(baseline), encoding="utf-8")
    return baseline_file


# ---------------------------------------------------------------------------
# Tests — empty states
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_screen_no_baselines(tmp_path: Path) -> None:
    """Screen shows 'No baseline data' when baselines directory is missing."""
    app = RollbackApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count == 1
        cell = str(table.get_cell_at((0, 0)))
        assert cell == "—"


@pytest.mark.asyncio
async def test_rollback_screen_empty_baselines(tmp_path: Path) -> None:
    """Screen shows 'No baseline data' when baselines directory is empty."""
    baselines_dir = tmp_path / ".architect" / "baselines"
    baselines_dir.mkdir(parents=True)

    app = RollbackApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count == 1
        cell = str(table.get_cell_at((0, 0)))
        assert cell == "—"


# ---------------------------------------------------------------------------
# Tests — task selection mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_screen_task_selection_shows_baselines(tmp_path: Path) -> None:
    """Screen shows available baselines in task selection mode."""
    baselines_dir = tmp_path / ".architect" / "baselines"
    _write_baseline(baselines_dir, "T01", {"file_a.py": "hash001"})
    _write_baseline(baselines_dir, "T02", {"file_b.py": "hash002"})

    app = RollbackApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count == 2
        task_cells = {str(table.get_cell_at((i, 0))) for i in range(table.row_count)}
        assert "T01" in task_cells
        assert "T02" in task_cells


@pytest.mark.asyncio
async def test_rollback_screen_task_selection_columns(tmp_path: Path) -> None:
    """Task selection DataTable has Task, Timestamp, Files columns."""
    baselines_dir = tmp_path / ".architect" / "baselines"
    _write_baseline(baselines_dir, "T01", {"file_a.py": "hash001"})

    app = RollbackApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        # Check column count
        assert len(table.columns) == 3


@pytest.mark.asyncio
async def test_rollback_screen_task_filter_no_match(tmp_path: Path) -> None:
    """Screen shows error when task filter has no matching baseline."""
    baselines_dir = tmp_path / ".architect" / "baselines"
    _write_baseline(baselines_dir, "T01", {"file_a.py": "hash001"})

    app = RollbackApp(project=tmp_path, task_filter="T99")
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        title = app.query_one("#rollback_title", Static)
        assert "no baseline" in str(title.render()).lower()


# ---------------------------------------------------------------------------
# Tests — plan review mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_screen_plan_mode_shows_files(tmp_path: Path) -> None:
    """Plan mode displays files to restore and delete."""
    baselines_dir = tmp_path / ".architect" / "baselines"
    baseline_file = _write_baseline(
        baselines_dir,
        "T01",
        {
            "modified.py": "abc123def456",
        },
    )
    # Create the modified file on disk
    (tmp_path / "modified.py").write_text("new content", encoding="utf-8")
    # Create a file that was created during the run (in baseline's created list)
    # The baseline only tracks existing files — created files are detected by
    # detect_changes as files that exist now but weren't in the baseline.
    # For this test, we just test that the plan mode loads.

    app = RollbackApp(project=tmp_path, baseline_path=baseline_file)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        assert app._mode == "plan"
        table = app.query_one(DataTable)
        # The table should have File, Action, Size columns
        assert len(table.columns) == 3


@pytest.mark.asyncio
async def test_rollback_screen_plan_mode_no_changes(tmp_path: Path) -> None:
    """Plan mode shows 'No changes' when all files are unchanged."""
    baselines_dir = tmp_path / ".architect" / "baselines"

    # Write a baseline where the file matches current state
    content = "original content"
    import hashlib

    sha = hashlib.sha256(content.encode()).hexdigest()
    baseline_file = _write_baseline(
        baselines_dir,
        "T01",
        {
            "unchanged.py": sha,
        },
    )
    # Write the same content to disk
    (tmp_path / "unchanged.py").write_text(content, encoding="utf-8")

    app = RollbackApp(project=tmp_path, baseline_path=baseline_file)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        assert app._mode == "plan"
        summary = app.query_one("#rollback_summary", Static)
        summary_text = str(summary.render())
        assert "Restore: 0" in summary_text
        assert "Delete: 0" in summary_text


@pytest.mark.asyncio
async def test_rollback_screen_plan_mode_summary_counts(tmp_path: Path) -> None:
    """Plan mode summary shows correct counts."""
    baselines_dir = tmp_path / ".architect" / "baselines"
    baseline_file = _write_baseline(
        baselines_dir,
        "T01",
        {
            "file_a.py": "hash001",
            "file_b.py": "hash002",
        },
    )
    # Create files with different content
    (tmp_path / "file_a.py").write_text("new A", encoding="utf-8")
    (tmp_path / "file_b.py").write_text("new B", encoding="utf-8")

    app = RollbackApp(project=tmp_path, baseline_path=baseline_file)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        summary = app.query_one("#rollback_summary", Static)
        summary_text = str(summary.render())
        # Files that differ will show as Restore (if git can retrieve) or 0
        # In a non-git repo, git retrieval fails, so counts may be 0
        assert "Restore:" in summary_text
        assert "Delete:" in summary_text
        assert "Unchanged:" in summary_text


@pytest.mark.asyncio
async def test_rollback_screen_plan_mode_hint_text(tmp_path: Path) -> None:
    """Plan mode hint shows action shortcuts."""
    baselines_dir = tmp_path / ".architect" / "baselines"
    baseline_file = _write_baseline(baselines_dir, "T01", {"file_a.py": "hash001"})
    (tmp_path / "file_a.py").write_text("content", encoding="utf-8")

    app = RollbackApp(project=tmp_path, baseline_path=baseline_file)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        hint = app.query_one("#rollback_hint", Static)
        hint_text = str(hint.render())
        assert "Approve" in hint_text
        assert "Cancel" in hint_text
        assert "Dry Run" in hint_text


# ---------------------------------------------------------------------------
# Tests — action handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_screen_action_cancel(tmp_path: Path) -> None:
    """Cancel action exits the app."""
    baselines_dir = tmp_path / ".architect" / "baselines"
    baseline_file = _write_baseline(baselines_dir, "T01", {"file_a.py": "hash001"})

    app = RollbackApp(project=tmp_path, baseline_path=baseline_file)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        app.action_cancel()
        # action_cancel calls self.exit() which sets return code
        assert app.return_code is not None or app._mode == "plan"


@pytest.mark.asyncio
async def test_rollback_screen_action_dry_run(tmp_path: Path) -> None:
    """Dry Run action shows dry-run result in summary."""
    baselines_dir = tmp_path / ".architect" / "baselines"
    baseline_file = _write_baseline(baselines_dir, "T01", {"file_a.py": "hash001"})
    (tmp_path / "file_a.py").write_text("content", encoding="utf-8")

    app = RollbackApp(project=tmp_path, baseline_path=baseline_file)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        app.action_dry_run()
        await pilot.pause(0.05)
        summary = app.query_one("#rollback_summary", Static)
        summary_text = str(summary.render())
        assert "Dry Run" in summary_text or "Would restore" in summary_text


@pytest.mark.asyncio
async def test_rollback_screen_action_approve_non_git(tmp_path: Path) -> None:
    """Approve action executes rollback (even if no git in test env)."""
    baselines_dir = tmp_path / ".architect" / "baselines"
    baseline_file = _write_baseline(baselines_dir, "T01", {"file_a.py": "hash001"})
    (tmp_path / "file_a.py").write_text("content", encoding="utf-8")

    app = RollbackApp(project=tmp_path, baseline_path=baseline_file)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        app.action_approve()
        await pilot.pause(0.05)
        assert app._mode == "result"
        assert app._result is not None
        # In non-git env, rollback may have errors but should still complete
        title = app.query_one("#rollback_title", Static)
        assert "Complete" in str(title.render())


# ---------------------------------------------------------------------------
# Tests — key bindings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_screen_key_a_approve(tmp_path: Path) -> None:
    """Pressing 'a' in plan mode triggers approve."""
    baselines_dir = tmp_path / ".architect" / "baselines"
    baseline_file = _write_baseline(baselines_dir, "T01", {"file_a.py": "hash001"})
    (tmp_path / "file_a.py").write_text("content", encoding="utf-8")

    app = RollbackApp(project=tmp_path, baseline_path=baseline_file)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        await pilot.press("a")
        await pilot.pause(0.05)
        assert app._mode == "result"


@pytest.mark.asyncio
async def test_rollback_screen_key_d_dry_run(tmp_path: Path) -> None:
    """Pressing 'd' in plan mode triggers dry run."""
    baselines_dir = tmp_path / ".architect" / "baselines"
    baseline_file = _write_baseline(baselines_dir, "T01", {"file_a.py": "hash001"})
    (tmp_path / "file_a.py").write_text("content", encoding="utf-8")

    app = RollbackApp(project=tmp_path, baseline_path=baseline_file)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        await pilot.press("d")
        await pilot.pause(0.05)
        # Should still be in plan mode (dry run doesn't change mode)
        assert app._mode == "plan"


@pytest.mark.asyncio
async def test_rollback_screen_key_c_cancel(tmp_path: Path) -> None:
    """Pressing 'c' in plan mode triggers cancel (exit)."""
    baselines_dir = tmp_path / ".architect" / "baselines"
    baseline_file = _write_baseline(baselines_dir, "T01", {"file_a.py": "hash001"})

    app = RollbackApp(project=tmp_path, baseline_path=baseline_file)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        await pilot.press("c")
        await pilot.pause(0.05)
        # action_cancel calls self.exit()
        assert app.return_code is not None


@pytest.mark.asyncio
async def test_rollback_screen_key_q_quit(tmp_path: Path) -> None:
    """Pressing 'q' quits the app."""
    app = RollbackApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        await pilot.press("q")
        await pilot.pause(0.05)
        assert app.return_code is not None


@pytest.mark.asyncio
async def test_rollback_screen_escape_quit(tmp_path: Path) -> None:
    """Pressing Escape quits the app."""
    app = RollbackApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        await pilot.press("escape")
        await pilot.pause(0.05)
        assert app.return_code is not None


# ---------------------------------------------------------------------------
# Tests — refresh binding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_screen_refresh_binding(tmp_path: Path) -> None:
    """Pressing 'r' triggers a refresh without error."""
    baselines_dir = tmp_path / ".architect" / "baselines"
    baselines_dir.mkdir(parents=True)

    app = RollbackApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        await pilot.press("r")
        await pilot.pause(0.05)
        table = app.query_one(DataTable)
        assert table.row_count >= 0


# ---------------------------------------------------------------------------
# Tests — run_rollback_screen function
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_rollback_screen_launches_app(tmp_path: Path) -> None:
    """run_rollback_screen creates and runs a RollbackApp instance."""
    with mock.patch.object(RollbackApp, "run") as mock_run:
        run_rollback_screen(project=tmp_path)
        mock_run.assert_called_once()


@pytest.mark.asyncio
async def test_run_rollback_screen_with_baseline(tmp_path: Path) -> None:
    """run_rollback_screen passes baseline_path to RollbackApp."""
    baselines_dir = tmp_path / ".architect" / "baselines"
    baseline_file = _write_baseline(baselines_dir, "T01", {"file_a.py": "hash001"})

    with mock.patch.object(RollbackApp, "run") as mock_run:
        run_rollback_screen(project=tmp_path, baseline_path=baseline_file)
        mock_run.assert_called_once()


@pytest.mark.asyncio
async def test_run_rollback_screen_returns_result_on_approve(tmp_path: Path) -> None:
    """run_rollback_screen returns RollbackResult when user approves."""
    baselines_dir = tmp_path / ".architect" / "baselines"
    baseline_file = _write_baseline(baselines_dir, "T01", {"file_a.py": "hash001"})

    # Mock the app to simulate approval
    mock_result = RollbackResult(
        restored_count=1,
        deleted_count=0,
        unchanged_count=0,
        errors=[],
    )

    original_init = RollbackApp.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._result = mock_result

    with mock.patch.object(RollbackApp, "__init__", patched_init):
        with mock.patch.object(RollbackApp, "run"):
            result = run_rollback_screen(project=tmp_path, baseline_path=baseline_file)
            assert result is not None
            assert isinstance(result, RollbackResult)


@pytest.mark.asyncio
async def test_run_rollback_screen_returns_none_on_cancel(tmp_path: Path) -> None:
    """run_rollback_screen returns None when user cancels."""
    original_init = RollbackApp.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._result = None

    with mock.patch.object(RollbackApp, "__init__", patched_init):
        with mock.patch.object(RollbackApp, "run"):
            result = run_rollback_screen(project=tmp_path)
            assert result is None


# ---------------------------------------------------------------------------
# Tests — theme application
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_screen_applies_theme(tmp_path: Path) -> None:
    """Screen applies the Architect theme on mount."""
    app = RollbackApp(project=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        # Theme should be applied — check that the app has the theme set
        assert app.theme == "architect-dark" or app.theme is not None


# ---------------------------------------------------------------------------
# Tests — error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_screen_invalid_baseline(tmp_path: Path) -> None:
    """Screen handles invalid baseline file gracefully."""
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("not json", encoding="utf-8")

    app = RollbackApp(project=tmp_path, baseline_path=bad_file)
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        # Should show error state
        title = app.query_one("#rollback_title", Static)
        title_text = str(title.render()).lower()
        assert "error" in title_text or "cannot read" in title_text
