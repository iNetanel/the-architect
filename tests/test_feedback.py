"""Tests for the feedback storage module and CLI command.

Covers:
- FeedbackState Pydantic model validation
- load_feedback() — file exists, missing, corrupted
- save_feedback() — creates file, writes correct content
- clear_feedback() — removes file, handles missing file
- architect feedback --write — stores message, shows confirmation
- architect feedback --view — displays message and timestamp
- architect feedback --clear — removes feedback
- architect feedback --json — valid JSON output
- architect feedback with no flags — shows status
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from the_architect.cli import main
from the_architect.core.feedback import (
    FEEDBACK_FILE,
    FeedbackState,
    clear_feedback,
    load_feedback,
    save_feedback,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def _write_raw_feedback(project: Path, data: dict[str, object]) -> None:
    """Write a raw feedback JSON file directly to disk."""
    feedback_path = project / FEEDBACK_FILE
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    feedback_path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# FeedbackState model
# ---------------------------------------------------------------------------


class TestFeedbackStateModel:
    """Tests for the FeedbackState Pydantic model."""

    def test_create_with_message_only(self) -> None:
        state = FeedbackState(
            message="fix the bug",
            written_at="2026-05-17T10:00:00+00:00",
        )
        assert state.message == "fix the bug"
        assert state.target_task is None
        assert state.written_at == "2026-05-17T10:00:00+00:00"

    def test_create_with_target_task(self) -> None:
        state = FeedbackState(
            message="use sqlite",
            written_at="2026-05-17T10:00:00+00:00",
            target_task="T05",
        )
        assert state.message == "use sqlite"
        assert state.target_task == "T05"

    def test_model_dump_contains_all_fields(self) -> None:
        state = FeedbackState(
            message="test message",
            written_at="2026-05-17T10:00:00+00:00",
            target_task="T03",
        )
        dump = state.model_validate(state.model_dump())
        assert dump.message == "test message"
        assert dump.target_task == "T03"
        assert dump.written_at == "2026-05-17T10:00:00+00:00"


# ---------------------------------------------------------------------------
# load_feedback()
# ---------------------------------------------------------------------------


class TestLoadFeedback:
    """Tests for load_feedback()."""

    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        result = load_feedback(tmp_path)
        assert result is None

    def test_returns_none_when_architect_dir_missing(self, tmp_path: Path) -> None:
        """Even if .architect/ doesn't exist, load_feedback returns None."""
        result = load_feedback(tmp_path)
        assert result is None

    def test_returns_feedback_state_when_file_exists(self, tmp_path: Path) -> None:
        _write_raw_feedback(
            tmp_path,
            {
                "message": "use sqlite for cache",
                "written_at": "2026-05-17T10:00:00+00:00",
                "target_task": "T03",
            },
        )
        result = load_feedback(tmp_path)
        assert result is not None
        assert result.message == "use sqlite for cache"
        assert result.target_task == "T03"
        assert result.written_at == "2026-05-17T10:00:00+00:00"

    def test_returns_feedback_when_target_task_is_null(self, tmp_path: Path) -> None:
        _write_raw_feedback(
            tmp_path,
            {
                "message": "generic feedback",
                "written_at": "2026-05-17T10:00:00+00:00",
                "target_task": None,
            },
        )
        result = load_feedback(tmp_path)
        assert result is not None
        assert result.target_task is None

    def test_returns_none_on_invalid_json(self, tmp_path: Path) -> None:
        feedback_path = tmp_path / FEEDBACK_FILE
        feedback_path.parent.mkdir(parents=True, exist_ok=True)
        feedback_path.write_text("not valid json {{{{", encoding="utf-8")
        result = load_feedback(tmp_path)
        assert result is None

    def test_returns_none_on_corrupted_json(self, tmp_path: Path) -> None:
        feedback_path = tmp_path / FEEDBACK_FILE
        feedback_path.parent.mkdir(parents=True, exist_ok=True)
        feedback_path.write_text("CORRUPTED DATA", encoding="utf-8")
        result = load_feedback(tmp_path)
        assert result is None

    def test_returns_none_on_json_with_missing_fields(self, tmp_path: Path) -> None:
        """JSON that is valid but missing required 'message' field returns None."""
        _write_raw_feedback(
            tmp_path,
            {
                "written_at": "2026-05-17T10:00:00+00:00",
                # missing 'message'
            },
        )
        result = load_feedback(tmp_path)
        assert result is None

    def test_returns_none_on_empty_file(self, tmp_path: Path) -> None:
        feedback_path = tmp_path / FEEDBACK_FILE
        feedback_path.parent.mkdir(parents=True, exist_ok=True)
        feedback_path.write_text("", encoding="utf-8")
        result = load_feedback(tmp_path)
        assert result is None

    def test_returns_none_on_os_error(self, tmp_path: Path) -> None:
        """OSError during file read returns None."""
        # Create a file at the feedback path location to prevent dir creation
        feedback_path = tmp_path / FEEDBACK_FILE
        # Make the path a file, not a directory, so parent mkdir fails
        parent = feedback_path.parent
        parent.mkdir(parents=True, exist_ok=True)
        with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
            result = load_feedback(tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# save_feedback()
# ---------------------------------------------------------------------------


class TestSaveFeedback:
    """Tests for save_feedback()."""

    def test_creates_architect_dir(self, tmp_path: Path) -> None:
        """save_feedback creates the .architect/ directory if it doesn't exist."""
        assert not (tmp_path / ".architect").exists()
        state = save_feedback(tmp_path, "hello world")
        assert (tmp_path / ".architect").exists()
        assert state.message == "hello world"

    def test_writes_valid_json(self, tmp_path: Path) -> None:
        save_feedback(tmp_path, "fix the auth bug")
        feedback_path = tmp_path / FEEDBACK_FILE
        data = json.loads(feedback_path.read_text(encoding="utf-8"))
        assert data["message"] == "fix the auth bug"
        assert "written_at" in data
        assert data["target_task"] is None

    def test_returns_feedback_state(self, tmp_path: Path) -> None:
        state = save_feedback(tmp_path, "test message")
        assert isinstance(state, FeedbackState)
        assert state.message == "test message"

    def test_sets_target_task(self, tmp_path: Path) -> None:
        state = save_feedback(tmp_path, "use sqlite", target_task="T05")
        assert state.target_task == "T05"
        feedback_path = tmp_path / FEEDBACK_FILE
        data = json.loads(feedback_path.read_text(encoding="utf-8"))
        assert data["target_task"] == "T05"

    def test_sets_iso_timestamp(self, tmp_path: Path) -> None:
        state = save_feedback(tmp_path, "test")
        assert state.written_at != ""
        # Verify it is a valid ISO timestamp
        from datetime import datetime

        datetime.fromisoformat(state.written_at)

    def test_overwrites_existing_feedback(self, tmp_path: Path) -> None:
        save_feedback(tmp_path, "first message")
        state = save_feedback(tmp_path, "second message")
        assert state.message == "second message"
        feedback_path = tmp_path / FEEDBACK_FILE
        data = json.loads(feedback_path.read_text(encoding="utf-8"))
        assert data["message"] == "second message"

    def test_no_temp_files_left(self, tmp_path: Path) -> None:
        save_feedback(tmp_path, "cleanup test")
        temp_files = list((tmp_path / ".architect").glob(".feedback_tmp_*"))
        assert temp_files == []


# ---------------------------------------------------------------------------
# clear_feedback()
# ---------------------------------------------------------------------------


class TestClearFeedback:
    """Tests for clear_feedback()."""

    def test_removes_existing_feedback(self, tmp_path: Path) -> None:
        save_feedback(tmp_path, "to be cleared")
        assert load_feedback(tmp_path) is not None
        clear_feedback(tmp_path)
        assert load_feedback(tmp_path) is None

    def test_no_error_when_file_missing(self, tmp_path: Path) -> None:
        """clear_feedback on a project with no feedback file should not raise."""
        clear_feedback(tmp_path)  # should not raise

    def test_no_error_when_architect_dir_missing(self, tmp_path: Path) -> None:
        """clear_feedback when .architect/ doesn't exist should not raise."""
        assert not (tmp_path / ".architect").exists()
        clear_feedback(tmp_path)  # should not raise

    def test_handles_os_error_gracefully(self, tmp_path: Path) -> None:
        """OSError during unlink is swallowed silently."""
        feedback_path = tmp_path / FEEDBACK_FILE
        feedback_path.parent.mkdir(parents=True, exist_ok=True)
        feedback_path.write_text("{}", encoding="utf-8")
        with patch.object(Path, "unlink", side_effect=OSError("permission denied")):
            clear_feedback(tmp_path)  # should not raise


# ---------------------------------------------------------------------------
# CLI — architect feedback command
# ---------------------------------------------------------------------------


class TestFeedbackCommand:
    """Tests for the `architect feedback` CLI command."""

    def test_feedback_in_help(self, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "feedback" in result.output

    # --write tests

    def test_write_stores_feedback(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path), "--write", "fix the auth flow"],
        )
        assert result.exit_code == 0, result.output
        assert "Feedback saved" in result.output
        fb = load_feedback(tmp_path)
        assert fb is not None
        assert fb.message == "fix the auth flow"

    def test_write_with_target_task(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path), "--write", "use sqlite", "--target", "T05"],
        )
        assert result.exit_code == 0, result.output
        fb = load_feedback(tmp_path)
        assert fb is not None
        assert fb.target_task == "T05"

    def test_write_shows_target_in_output(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path), "--write", "test", "--target", "T03"],
        )
        assert result.exit_code == 0, result.output
        assert "T03" in result.output

    def test_write_shows_next_pending_when_no_target(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path), "--write", "test"],
        )
        assert result.exit_code == 0, result.output
        assert "next pending task" in result.output

    # --view tests

    def test_view_displays_feedback(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        save_feedback(tmp_path, "view this message")
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path), "--view"],
        )
        assert result.exit_code == 0, result.output
        assert "Current feedback" in result.output
        assert "view this message" in result.output

    def test_view_shows_no_feedback_when_empty(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path), "--view"],
        )
        assert result.exit_code == 0, result.output
        assert "No feedback stored" in result.output

    # --clear tests

    def test_clear_removes_feedback(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        save_feedback(tmp_path, "to be cleared")
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path), "--clear"],
        )
        assert result.exit_code == 0, result.output
        assert "Feedback cleared" in result.output
        assert load_feedback(tmp_path) is None

    def test_clear_when_no_feedback(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path), "--clear"],
        )
        assert result.exit_code == 0, result.output
        assert "Feedback cleared" in result.output

    # --json tests

    def test_json_write_output(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path), "--write", "json test", "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["project"] == str(tmp_path.resolve())
        assert payload["feedback"]["message"] == "json test"
        assert "written_at" in payload["feedback"]

    def test_json_view_with_feedback(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        save_feedback(tmp_path, "json view test", target_task="T04")
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path), "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["feedback"]["message"] == "json view test"
        assert payload["feedback"]["target_task"] == "T04"

    def test_json_view_without_feedback(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path), "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["feedback"] is None

    def test_json_clear_output(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        save_feedback(tmp_path, "to be cleared")
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path), "--clear", "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["feedback"] is None

    def test_json_output_has_project_key(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path), "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert "project" in payload
        assert isinstance(payload["project"], str)

    # No flags (default view) tests

    def test_no_flags_shows_no_feedback(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "No feedback stored" in result.output

    def test_no_flags_shows_feedback(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        save_feedback(tmp_path, "default view message")
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "Current feedback" in result.output
        assert "default view message" in result.output

    # Edge cases

    def test_write_empty_message(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Writing an empty string should still save feedback."""
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path), "--write", ""],
        )
        assert result.exit_code == 0, result.output
        fb = load_feedback(tmp_path)
        assert fb is not None
        assert fb.message == ""

    def test_roundtrip_save_load_clear(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Full lifecycle: write -> verify -> clear -> verify empty."""
        # Write
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path), "--write", "roundtrip test"],
        )
        assert result.exit_code == 0

        # Verify
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path), "--json"],
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["feedback"]["message"] == "roundtrip test"

        # Clear
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path), "--clear"],
        )
        assert result.exit_code == 0

        # Verify empty
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path), "--json"],
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["feedback"] is None

    def test_project_short_flag_works(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """-p short flag should work identically to --project."""
        result = cli_runner.invoke(
            main,
            ["feedback", "-p", str(tmp_path), "--write", "short flag test"],
        )
        assert result.exit_code == 0, result.output
        fb = load_feedback(tmp_path)
        assert fb is not None
        assert fb.message == "short flag test"
