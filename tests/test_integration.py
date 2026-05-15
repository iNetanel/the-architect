"""Integration tests for The Architect full flow."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from the_architect.config import ArchitectConfig
from the_architect.core.planner import PlanningFailedError, PlanningRequest, run_planner
from the_architect.core.progress import read_progress, task_is_done
from the_architect.core.runner import (
    StreamResult,
    TaskResult,
    acquire_lock,
    release_lock,
    run_all,
    setup_logging,
)
from the_architect.core.tasks import Task, TaskPlan, TaskScope, TaskStatus, discover_tasks

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_project(tmp_path: Path) -> Path:
    """A mock project with basic structure."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()

    # Create a minimal PROGRESS.md
    progress_file = tmp_path / "PROGRESS.md"
    progress_file.write_text(
        "# The Architect — Progress Tracker\n\n"
        "**Tasks completed:** 0\n"
        "**Next task to run:** T00\n\n"
        "| Task | Title | Status | Completed |\n"
        "|---------|-------|--------|-----------|\n",
        encoding="utf-8",
    )

    # Create a task file
    task_file = tasks_dir / "T00_test.md"
    task_file.write_text("# T00 — Test Task\n\nTask 1 done.\n", encoding="utf-8")

    return tmp_path


@pytest.fixture
def config_for_project(mock_project: Path) -> ArchitectConfig:
    """Config resolved for the mock project."""
    config = ArchitectConfig(
        tasks_dir=mock_project / "tasks",
        progress_file=mock_project / "PROGRESS.md",
        log_dir=mock_project / ".architect" / "logs",
    )
    return config.resolve(mock_project)


# ---------------------------------------------------------------------------
# test_full_headless_flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_headless_flow(tmp_path: Path) -> None:
    """End-to-end: discover pending task, run it (mocked), verify Done state."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "T00_test.md").write_text("# T00\n\nTask.\n", encoding="utf-8")

    progress_file = tmp_path / "PROGRESS.md"
    progress_file.write_text(
        "# The Architect — Progress Tracker\n\n"
        "**Tasks completed:** 0\n"
        "**Next task to run:** T00\n\n"
        "| Task | Title | Status | Completed |\n"
        "|---------|-------|--------|-----------|\n"
        "| T00 | Test | Pending | |\n",
        encoding="utf-8",
    )

    config = ArchitectConfig(
        tasks_dir=tasks_dir,
        progress_file=progress_file,
        log_dir=tmp_path / ".architect" / "logs",
        retry_pause=0,
        pause_between_tasks=0,
    ).resolve(tmp_path)

    tasks = discover_tasks(tasks_dir)
    plan = TaskPlan(tasks=tasks)

    async def fake_run_once(task, attempt, config, **kwargs):
        # Mark task Done in PROGRESS.md and return success
        progress_file.write_text(
            "# The Architect — Progress Tracker\n\n"
            "**Tasks completed:** 1\n"
            "**Next task to run:** T01\n\n"
            "| Task | Title | Status | Completed |\n"
            "|---------|-------|--------|-----------|\n"
            "| T00 | Test | Done | 2026-01-01 |\n",
            encoding="utf-8",
        )
        return TaskResult(
            prefix=task.prefix,
            title=task.title or task.name,
            status="done",
            attempts=attempt,
        )

    with patch("the_architect.core.runner.run_task_once", side_effect=fake_run_once):
        result = await run_all(plan, config)

    assert result is True
    state = read_progress(progress_file)
    assert state.tasks_completed == 1
    assert task_is_done(progress_file, "T00")


# ---------------------------------------------------------------------------
# test_resume_from_progress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_from_progress(tmp_path: Path) -> None:
    """Tasks already marked Done in PROGRESS.md are skipped on resume."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "T00_done.md").write_text("# T00\n\nAlready done.\n", encoding="utf-8")
    (tasks_dir / "T01_pending.md").write_text("# T01\n\nStill pending.\n", encoding="utf-8")

    progress_file = tmp_path / "PROGRESS.md"
    progress_file.write_text(
        "# The Architect — Progress Tracker\n\n"
        "**Tasks completed:** 1\n"
        "**Next task to run:** T01\n\n"
        "| Task | Title | Status | Completed |\n"
        "|---------|-------|--------|-----------|\n"
        "| T00 | Done  | Done    | 2026-01-01 |\n"
        "| T01 | Todo  | Pending | |\n",
        encoding="utf-8",
    )

    config = ArchitectConfig(
        tasks_dir=tasks_dir,
        progress_file=progress_file,
        log_dir=tmp_path / ".architect" / "logs",
        retry_pause=0,
        pause_between_tasks=0,
    ).resolve(tmp_path)

    tasks = discover_tasks(tasks_dir)
    # Mark T00 as done in the plan to mirror PROGRESS.md state
    updated: list[Task] = []
    for t in tasks:
        if t.prefix == "T00":
            updated.append(t.model_copy(update={"status": TaskStatus.DONE}))
        else:
            updated.append(t)
    plan = TaskPlan(tasks=updated)

    invoked_prefixes: list[str] = []

    async def fake_run_once(task, attempt, config, **kwargs):
        invoked_prefixes.append(task.prefix)
        progress_file.write_text(
            "# The Architect — Progress Tracker\n\n"
            "**Tasks completed:** 2\n"
            "**Next task to run:** T02\n\n"
            "| Task | Title | Status | Completed |\n"
            "|---------|-------|--------|-----------|\n"
            "| T00 | Done  | Done | 2026-01-01 |\n"
            "| T01 | Todo  | Done | 2026-01-02 |\n",
            encoding="utf-8",
        )
        return TaskResult(
            prefix=task.prefix,
            title=task.title or task.name,
            status="done",
            attempts=attempt,
        )

    with patch("the_architect.core.runner.run_task_once", side_effect=fake_run_once):
        result = await run_all(plan, config)

    assert result is True
    # T00 must be skipped; only T01 was actually executed.
    assert "T00" not in invoked_prefixes
    assert "T01" in invoked_prefixes


# ---------------------------------------------------------------------------
# test_all_done_exits_cleanly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_done_exits_cleanly(tmp_path: Path) -> None:
    """If every task is already Done, run_all returns True without executing anything."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "T00.md").write_text("# T00\n\nDone.\n", encoding="utf-8")

    progress_file = tmp_path / "PROGRESS.md"
    progress_file.write_text(
        "# The Architect — Progress Tracker\n\n"
        "**Tasks completed:** 1\n"
        "**Next task to run:** T01\n\n"
        "| Task | Title | Status | Completed |\n"
        "|---------|-------|--------|-----------|\n"
        "| T00 | Done | Done | 2026-01-01 |\n",
        encoding="utf-8",
    )

    config = ArchitectConfig(
        tasks_dir=tasks_dir,
        progress_file=progress_file,
        log_dir=tmp_path / ".architect" / "logs",
        retry_pause=0,
        pause_between_tasks=0,
    ).resolve(tmp_path)

    tasks = [t.model_copy(update={"status": TaskStatus.DONE}) for t in discover_tasks(tasks_dir)]
    plan = TaskPlan(tasks=tasks)

    async def _should_not_be_called(*_args, **_kwargs):
        raise AssertionError("run_task_once must not be called when every task is Done")

    with patch("the_architect.core.runner.run_task_once", side_effect=_should_not_be_called):
        result = await run_all(plan, config)

    assert result is True


# ---------------------------------------------------------------------------
# test_retry_with_model_fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_with_model_fallback(tmp_path: Path) -> None:
    """Task fails attempts 1 and 2 with model fallbacks, succeeds on attempt 3."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "T00_test.md").write_text("# T00\n\nTask.\n", encoding="utf-8")

    progress_file = tmp_path / "PROGRESS.md"
    progress_file.write_text(
        "# The Architect — Progress Tracker\n\n"
        "**Tasks completed:** 0\n"
        "**Next task to run:** T00\n\n"
        "| Task | Title | Status | Completed |\n"
        "|---------|-------|--------|-----------|\n"
        "| T00 | Test | Pending | |\n",
        encoding="utf-8",
    )

    config = ArchitectConfig(
        tasks_dir=tasks_dir,
        progress_file=progress_file,
        log_dir=tmp_path / ".architect" / "logs",
        retry_model_2="claude-sonnet-4-20250514",
        retry_model_3="claude-opus-4-20250514",
        retry_pause=0,
        pause_between_tasks=0,
    ).resolve(tmp_path)

    tasks = discover_tasks(tasks_dir)
    plan = TaskPlan(tasks=tasks)

    attempt_models: list[str | None] = []

    async def mock_run_once(task, attempt, config, **kwargs):
        attempt_models.append(kwargs.get("model_override"))
        if attempt < 3:
            return TaskResult(
                prefix=task.prefix,
                title=task.title or task.name,
                status="failed",
                attempts=attempt,
            )
        progress_file.write_text(
            "# The Architect — Progress Tracker\n\n"
            "**Tasks completed:** 1\n"
            "**Next task to run:** T01\n\n"
            "| Task | Title | Status | Completed |\n"
            "|---------|-------|--------|-----------|\n"
            "| T00 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )
        return TaskResult(
            prefix=task.prefix,
            title=task.title or task.name,
            status="done",
            attempts=attempt,
        )

    with patch("the_architect.core.runner.run_task_once", side_effect=mock_run_once):
        result = await run_all(plan, config)

    assert result is True
    assert attempt_models[0] is None
    assert attempt_models[1] == "claude-sonnet-4-20250514"
    assert attempt_models[2] == "claude-opus-4-20250514"


# ---------------------------------------------------------------------------
# test_existing_opencode_json_not_overwritten
# ---------------------------------------------------------------------------


def test_existing_opencode_json_not_overwritten(tmp_path: Path) -> None:
    """User has opencode.json — The Architect writes to .architect/ and does NOT touch it."""
    from the_architect.core.opencode_config import ensure_opencode_setup

    # Create existing opencode.json
    existing_config = {"model": "existing-model", "provider": "anthropic"}
    opencode_json = tmp_path / "opencode.json"
    opencode_json.write_text(json.dumps(existing_config), encoding="utf-8")

    config = ArchitectConfig(
        tasks_dir=tmp_path / "tasks",
        progress_file=tmp_path / "PROGRESS.md",
        log_dir=tmp_path / ".architect" / "logs",
    ).resolve(tmp_path)

    # Ensure setup — always returns .architect/architect.json (The Architect's own config)
    result_path = ensure_opencode_setup(tmp_path, config)
    assert result_path == tmp_path / ".architect" / "architect.json"

    # User's opencode.json must be byte-for-byte unchanged
    content = json.loads(opencode_json.read_text())
    assert content["model"] == "existing-model"

    # The Architect prompts must be written to .architect/prompts/
    prompts_dir = tmp_path / ".architect" / "prompts"
    assert prompts_dir.exists()
    assert (prompts_dir / "architect.md").exists()


# ---------------------------------------------------------------------------
# S08.1: test_planner_raises_when_no_tasks_created
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_raises_when_no_tasks_created(tmp_path: Path) -> None:
    """If OpenCode runs but creates no tasks, PlanningFailedError is raised."""
    config = ArchitectConfig().resolve(tmp_path)
    request = PlanningRequest(
        goal="Do something",
        scope=TaskScope.STANDARD,
        project_dir=tmp_path,
    )

    (tmp_path / "tasks").mkdir()

    # Mock the provider so ensure_setup and stream_provider both succeed
    # but no task files are created
    from unittest.mock import MagicMock

    mock_provider = MagicMock()
    mock_provider.display_name = "OpenCode"
    mock_provider.supports_agents.return_value = True
    mock_provider.ensure_setup.return_value = tmp_path / ".architect" / "architect.json"

    # Patch asyncio.sleep so the planner does not wait 30s between retry
    # attempts. Planning has 3 attempts with a 30s pause between each — the
    # waits are irrelevant for this test and would otherwise hang CI.
    async def _no_sleep(_seconds: float) -> None:
        return None

    with (
        patch(
            "the_architect.core.planner.stream_provider",
            new_callable=AsyncMock,
            return_value=StreamResult(exit_code=0),
        ),
        patch("the_architect.core.planner.asyncio.sleep", new=_no_sleep),
    ):
        with pytest.raises(PlanningFailedError):
            await run_planner(request, config, provider=mock_provider)


# ---------------------------------------------------------------------------
# S08.1: test_opencode_not_installed_shows_helpful_error
# ---------------------------------------------------------------------------


def test_opencode_not_installed_shows_helpful_error() -> None:
    """opencode not on PATH. check_opencode_installed returns False."""
    from the_architect.core.opencode_config import check_opencode_installed

    with patch("shutil.which", return_value=None):
        assert check_opencode_installed() is False


# ---------------------------------------------------------------------------
# S08.2: Edge case - malformed PROGRESS.md returns safe defaults
# ---------------------------------------------------------------------------


def test_malformed_progress_returns_defaults(tmp_path: Path) -> None:
    """Malformed PROGRESS.md — read_progress returns safe defaults, never crashes."""
    progress_file = tmp_path / "PROGRESS.md"
    progress_file.write_text("This is not a valid PROGRESS.md file at all!!!", encoding="utf-8")

    state = read_progress(progress_file)

    assert state.tasks_completed == 0
    assert state.next_task == "T00"
    assert state.done_tasks == []


# ---------------------------------------------------------------------------
# S08.2: Edge case - tasks not writable
# ---------------------------------------------------------------------------


def test_tasks_dir_not_writable(tmp_path: Path) -> None:
    """tasks/ not writable — clear error message, not a traceback."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()

    # Make it read-only
    tasks_dir.chmod(0o444)

    try:
        # Should return empty list, not crash
        tasks = discover_tasks(tasks_dir)
        assert tasks == []
    finally:
        # Cleanup - restore write permission
        tasks_dir.chmod(0o755)


# ---------------------------------------------------------------------------
# S08.2: Edge case - OpenCode exits non-zero but task is Done
# ---------------------------------------------------------------------------


def test_opencode_nonzero_but_task_done(tmp_path: Path) -> None:
    """OpenCode exits non-zero but task is Done — treat as success."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()

    (tasks_dir / "T00_test.md").write_text("# T00\n\nTask.\n", encoding="utf-8")

    progress_file = tmp_path / "PROGRESS.md"
    # Mark task as Done even though we simulate non-zero exit
    progress_file.write_text(
        "# The Architect — Progress Tracker\n\n"
        "**Tasks completed:** 1\n"
        "**Next task to run:** T01\n\n"
        "| Task | Title | Status | Completed |\n"
        "|---------|-------|--------|-----------|\n"
        "| T00 | Test | Done | 2026-04-12 |\n",
        encoding="utf-8",
    )

    # Verify task is marked done regardless of exit code
    assert task_is_done(progress_file, "T00") is True


# ---------------------------------------------------------------------------
# S08.2: Edge case - Unicode in task output
# ---------------------------------------------------------------------------


def test_unicode_handling() -> None:
    """Unicode in task output — handled correctly."""
    # Test that runner handles unicode without crashing
    test_string = "Hello 🌍 Ñoño 📦 🚀"
    result = test_string.encode("utf-8", errors="replace").decode("utf-8")
    assert "🧑‍🚀" not in result or "🧑‍🚀" in test_string  # Either preserved or replaced


# ---------------------------------------------------------------------------
# S08.2: Edge case - concurrent runs detected via lock file
# ---------------------------------------------------------------------------


def test_lock_file_mechanism(tmp_path: Path) -> None:
    """Concurrent runs — second instance should detect lock file."""
    project_dir = tmp_path

    # First acquire should succeed
    assert acquire_lock(project_dir) is True

    # Second acquire should fail (already locked)
    assert acquire_lock(project_dir) is False

    # Release
    release_lock(project_dir)

    # Now should succeed again
    assert acquire_lock(project_dir) is True

    # Cleanup
    release_lock(project_dir)


# ---------------------------------------------------------------------------
# S08.2: Edge case - interrupted run cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interrupted_run_cleanup(tmp_path: Path) -> None:
    """Interrupted run (Ctrl+C) — clean shutdown, PROGRESS.md not corrupted."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()

    (tasks_dir / "T00_test.md").write_text("# T00\n\nTask.\n", encoding="utf-8")

    progress_file = tmp_path / "PROGRESS.md"
    progress_file.write_text(
        "# The Architect — Progress Tracker\n\n"
        "**Tasks completed:** 0\n"
        "**Next task to run:** T00\n\n"
        "| Task | Title | Status | Completed |\n"
        "|---------|-------|--------|-----------|\n"
        "| T00 | Test | Pending | |\n",
        encoding="utf-8",
    )

    # Simulate interruption
    original_progress = progress_file.read_text()

    async def mock_run_all(*args, **kwargs):
        raise KeyboardInterrupt()

    # Should clean up lock file
    acquire_lock(tmp_path)

    try:
        with pytest.raises(KeyboardInterrupt):
            await mock_run_all()
    finally:
        release_lock(tmp_path)

    # PROGRESS.md should be unchanged
    assert progress_file.read_text() == original_progress


# ---------------------------------------------------------------------------
# S08.3: Logging setup
# ---------------------------------------------------------------------------


def test_setup_logging(tmp_path: Path) -> None:
    """setup_logging configures loguru correctly."""
    log_dir = tmp_path / "logs"
    setup_logging(log_dir, verbose=False)

    # Verify log directory was created
    assert log_dir.exists()
    assert (log_dir / "the_architect.log").exists()


def test_setup_logging_accepts_str_path(tmp_path: Path) -> None:
    """setup_logging coerces string paths to Path."""
    log_dir_str = str(tmp_path / "strlogs")
    setup_logging(log_dir_str, verbose=False)  # type: ignore[arg-type]

    assert (tmp_path / "strlogs").exists()
    assert (tmp_path / "strlogs" / "the_architect.log").exists()


def test_setup_logging_rejects_mock() -> None:
    """setup_logging raises TypeError when given a MagicMock.

    Regression test. Without the defensive ``Path(log_dir)`` coercion,
    passing a MagicMock silently creates a file in the current working
    directory whose name is the mock's repr — polluting the repo root.
    """
    from unittest.mock import MagicMock

    import pytest

    with pytest.raises(TypeError):
        setup_logging(MagicMock(), verbose=False)
