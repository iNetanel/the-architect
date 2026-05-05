"""Tests for PROGRESS.md read/write utilities."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from the_architect.core.progress import (
    PROGRESS_TEMPLATE,
    TERMINAL_STATUSES,
    _task_status_pattern,
    get_next_task,
    init_progress,
    read_progress,
    reconcile_task_status,
    replace_task_status,
    task_is_done,
    task_is_resolved,
    task_status,
)


class TestInitProgress:
    """Tests for init_progress function."""

    def test_init_progress_creates_file(self) -> None:
        """Should create PROGRESS.md if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"

            assert not progress_file.exists()
            init_progress(progress_file)
            assert progress_file.exists()

    def test_init_progress_does_not_overwrite(self) -> None:
        """Should not overwrite existing file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            original_content = "Existing content"
            progress_file.write_text(original_content, encoding="utf-8")

            init_progress(progress_file)

            assert progress_file.read_text(encoding="utf-8") == original_content


class TestReadProgress:
    """Tests for read_progress function."""

    def test_read_progress_parses_completed(self) -> None:
        """Should parse tasks_completed correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            progress_file.write_text(
                "**Tasks completed:** 5\n**Next task to run:** T06\n",
                encoding="utf-8",
            )

            state = read_progress(progress_file)

            assert state.tasks_completed == 5
            assert state.next_task == "T06"

    def test_read_progress_safe_defaults_missing_file(self) -> None:
        """Should return safe defaults when file doesn't exist."""
        state = read_progress(Path("/nonexistent/PROGRESS.md"))

        assert state.tasks_completed == 0
        assert state.next_task == "T00"
        assert state.done_tasks == []

    def test_read_progress_safe_defaults_malformed(self) -> None:
        """Should return safe defaults for malformed content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            progress_file.write_text("not a valid progress file", encoding="utf-8")

            state = read_progress(progress_file)

            assert state.tasks_completed == 0
            assert state.next_task == "T00"

    def test_read_progress_parses_done_tasks(self) -> None:
        """Should parse done tasks from table."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            content = (
                "**Tasks completed:** 2\n"
                "**Next task to run:** T03\n"
                "| T01 | First | Done | 2026-04-12 |\n"
                "| T02 | Second | Done | 2026-04-12 |\n"
            )
            progress_file.write_text(content, encoding="utf-8")

            state = read_progress(progress_file)

            assert "T01" in state.done_tasks
            assert "T02" in state.done_tasks
            assert len(state.done_tasks) == 2

    def test_read_progress_pending_tasks_not_in_done(self) -> None:
        """Pending rows must NOT appear in done_tasks — only Done rows should."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            content = (
                "**Tasks completed:** 1\n"
                "**Next task to run:** T02\n"
                "| T01 | First | Done | 2026-04-12 |\n"
                "| T02 | Second | Pending | — |\n"
                "| T03 | Third | Pending | — |\n"
            )
            progress_file.write_text(content, encoding="utf-8")

            state = read_progress(progress_file)

            assert "T01" in state.done_tasks
            assert "T02" not in state.done_tasks
            assert "T03" not in state.done_tasks
            assert len(state.done_tasks) == 1


class TestTaskIsDone:
    """Tests for task_is_done function."""

    def test_task_is_done_true(self) -> None:
        """Should return True when task is marked Done."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            content = "| T01 | First | Done | 2026-04-12 |\n"
            progress_file.write_text(content, encoding="utf-8")

            assert task_is_done(progress_file, "T01") is True

    def test_task_is_done_false(self) -> None:
        """Should return False when task is not marked Done."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            content = "| T01 | First | Pending | — |\n"
            progress_file.write_text(content, encoding="utf-8")

            assert task_is_done(progress_file, "T01") is False

    def test_task_is_done_missing_file(self) -> None:
        """Should return False when file doesn't exist."""
        assert task_is_done(Path("/nonexistent/PROGRESS.md"), "T01") is False

    def test_task_is_done_no_match(self) -> None:
        """Should return False when task not in table."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            content = "| T02 | Second | Done | 2026-04-12 |\n"
            progress_file.write_text(content, encoding="utf-8")

            assert task_is_done(progress_file, "T01") is False


class TestGetNextTask:
    """Tests for get_next_task function."""

    def test_get_next_task_parses_correctly(self) -> None:
        """Should parse next task correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            progress_file.write_text("**Next task to run:** T05\n", encoding="utf-8")

            assert get_next_task(progress_file) == "T05"

    def test_get_next_task_default_missing_file(self) -> None:
        """Should return T00 when file doesn't exist."""
        assert get_next_task(Path("/nonexistent/PROGRESS.md")) == "T00"

    def test_get_next_task_default_malformed(self) -> None:
        """Should return T00 when content is malformed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            progress_file.write_text("no next task here", encoding="utf-8")

            assert get_next_task(progress_file) == "T00"


class TestProgressTemplate:
    """Tests for PROGRESS_TEMPLATE."""

    def test_progress_template_has_placeholders(self) -> None:
        """Should have all required format placeholders."""
        assert "{tasks_completed}" in PROGRESS_TEMPLATE
        assert "{next_task}" in PROGRESS_TEMPLATE
        assert "{task_rows}" in PROGRESS_TEMPLATE
        assert "{current_state}" in PROGRESS_TEMPLATE
        assert "{last_summary}" in PROGRESS_TEMPLATE
        assert "## Task Outcomes" in PROGRESS_TEMPLATE


# ---------------------------------------------------------------------------
# T01 — Canonical regex helper tests
# ---------------------------------------------------------------------------

# The exact row format written by _write_progress_md in planner.py:
#   | T01 | Task Name | Pending | — |
_SAMPLE_DONE_ROW = "| T01 | task_name | Done | 2026-04-13 |"
_SAMPLE_PENDING_ROW = "| T02 | other_task | Pending | — |"
_SAMPLE_CONTENT = (
    "**Tasks completed:** 1\n"
    "**Next task to run:** T02\n"
    "| Task | Title | Status | Completed |\n"
    "|------|-------|--------|----------|\n"
    f"{_SAMPLE_DONE_ROW}\n"
    f"{_SAMPLE_PENDING_ROW}\n"
)


class TestTaskStatusPattern:
    """Tests for _task_status_pattern."""

    def test_matches_done_row(self) -> None:
        """Pattern for Done should match a Done row."""
        pat = _task_status_pattern("T01", "Done")
        assert pat.search(_SAMPLE_CONTENT) is not None

    def test_matches_pending_row(self) -> None:
        """Pattern for Pending should match a Pending row."""
        pat = _task_status_pattern("T02", "Pending")
        assert pat.search(_SAMPLE_CONTENT) is not None

    def test_does_not_match_wrong_prefix(self) -> None:
        """Pattern for T99 should not match anything."""
        pat = _task_status_pattern("T99", "Done")
        assert pat.search(_SAMPLE_CONTENT) is None

    def test_does_not_match_wrong_status(self) -> None:
        """Pattern for T01/Pending should not match — T01 is Done."""
        pat = _task_status_pattern("T01", "Pending")
        assert pat.search(_SAMPLE_CONTENT) is None

    def test_matches_planner_format(self) -> None:
        """Pattern must match the exact format written by _write_progress_md."""
        # _write_progress_md writes: | T01 | Task Name | Pending | — |
        planner_content = "| T01 | Task Name | Pending | — |\n"
        pat = _task_status_pattern("T01", "Pending")
        assert pat.search(planner_content) is not None

    def test_matches_done_planner_format(self) -> None:
        """Pattern must match a Done row in planner format."""
        planner_content = "| T01 | Task Name | Done | 2026-04-13 |\n"
        pat = _task_status_pattern("T01", "Done")
        assert pat.search(planner_content) is not None

    def test_returns_compiled_pattern(self) -> None:
        """Helper must return a compiled re.Pattern."""
        import re

        pat = _task_status_pattern("T01", "Done")
        assert isinstance(pat, re.Pattern)


class TestReplaceTaskStatus:
    """Tests for replace_task_status."""

    def test_done_to_pending(self) -> None:
        """Should replace Done → Pending for the specified prefix."""
        result = replace_task_status(_SAMPLE_CONTENT, "T01", "Done", "Pending")
        # T01 should now be Pending
        assert _task_status_pattern("T01", "Pending").search(result) is not None
        # T01 Done row should be gone
        assert _task_status_pattern("T01", "Done").search(result) is None

    def test_pending_to_done(self) -> None:
        """Should replace Pending → Done for the specified prefix."""
        result = replace_task_status(_SAMPLE_CONTENT, "T02", "Pending", "Done")
        assert _task_status_pattern("T02", "Done").search(result) is not None
        assert _task_status_pattern("T02", "Pending").search(result) is None

    def test_no_change_if_no_match(self) -> None:
        """Should return content unchanged if no matching row found."""
        result = replace_task_status(_SAMPLE_CONTENT, "T99", "Done", "Pending")
        assert result == _SAMPLE_CONTENT

    def test_does_not_affect_other_rows(self) -> None:
        """Replacing T01 status should not disturb T02."""
        result = replace_task_status(_SAMPLE_CONTENT, "T01", "Done", "Pending")
        # T02 should still be Pending
        assert _task_status_pattern("T02", "Pending").search(result) is not None

    def test_works_on_planner_written_format(self) -> None:
        """replace_task_status must work on the exact format _write_progress_md produces."""
        content = "| T01 | Task Name | Pending | — |\n| T02 | Other Task | Pending | — |\n"
        result = replace_task_status(content, "T01", "Pending", "Done")
        # T01 should be Done now
        assert _task_status_pattern("T01", "Done").search(result) is not None
        # T02 unchanged
        assert _task_status_pattern("T02", "Pending").search(result) is not None


class TestCrossConsistency:
    """Verify that read_progress/task_is_done and replace_task_status all agree.

    This is the key correctness test: a row that read_progress/task_is_done
    considers Done must also be detectable by replace_task_status("Done",…),
    and a row considered Pending by task_is_done must be findable for the
    skip operation.
    """

    def test_task_is_done_and_replace_agree_on_done_row(self) -> None:
        """task_is_done sees Done; replace_task_status can find and flip it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            progress_file.write_text(_SAMPLE_CONTENT, encoding="utf-8")

            # task_is_done agrees T01 is Done
            assert task_is_done(progress_file, "T01") is True

            # replace_task_status can flip it to Pending
            content = progress_file.read_text(encoding="utf-8")
            updated = replace_task_status(content, "T01", "Done", "Pending")
            assert updated != content  # A change was made

            progress_file.write_text(updated, encoding="utf-8")
            # Now task_is_done should say False
            assert task_is_done(progress_file, "T01") is False

    def test_read_progress_and_replace_agree_on_pending_row(self) -> None:
        """read_progress does not include T02 in done_tasks; replace can mark Done."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            progress_file.write_text(_SAMPLE_CONTENT, encoding="utf-8")

            state = read_progress(progress_file)
            assert "T02" not in state.done_tasks

            content = progress_file.read_text(encoding="utf-8")
            updated = replace_task_status(content, "T02", "Pending", "Done")
            assert updated != content

            progress_file.write_text(updated, encoding="utf-8")
            assert task_is_done(progress_file, "T02") is True

    def test_planner_format_round_trip(self) -> None:
        """Full round-trip: planner-written row → task_is_done → replace → task_is_done."""
        # Simulate what _write_progress_md writes
        planner_row = "| T03 | my_task_name | Pending | — |"
        content = f"**Tasks completed:** 0\n**Next task to run:** T03\n{planner_row}\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            progress_file.write_text(content, encoding="utf-8")

            assert task_is_done(progress_file, "T03") is False

            # Skip (Pending → Done)
            updated = replace_task_status(content, "T03", "Pending", "Done")
            assert updated != content
            progress_file.write_text(updated, encoding="utf-8")
            assert task_is_done(progress_file, "T03") is True

            # Retry (Done → Pending)
            updated2 = replace_task_status(updated, "T03", "Done", "Pending")
            assert updated2 != updated
            progress_file.write_text(updated2, encoding="utf-8")
            assert task_is_done(progress_file, "T03") is False


class TestStringPathBranches:
    """Tests for str-to-Path conversion in all public functions."""

    def test_init_progress_with_str_path(self) -> None:
        """init_progress should accept a string path and convert to Path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = str(Path(tmpdir) / "PROGRESS.md")
            init_progress(progress_file)
            assert Path(progress_file).exists()

    def test_read_progress_with_str_path(self) -> None:
        """read_progress should accept a string path and convert to Path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            progress_file.write_text(
                "**Tasks completed:** 3\n**Next task to run:** T04\n",
                encoding="utf-8",
            )
            state = read_progress(str(progress_file))
            assert state.tasks_completed == 3
            assert state.next_task == "T04"

    def test_task_is_done_with_str_path(self) -> None:
        """task_is_done should accept a string path and convert to Path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            progress_file.write_text(
                "| T01 | First | Done | 2026-04-13 |\n",
                encoding="utf-8",
            )
            assert task_is_done(str(progress_file), "T01") is True

    def test_get_next_task_with_str_path(self) -> None:
        """get_next_task should accept a string path and convert to Path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            progress_file.write_text(
                "**Next task to run:** R02\n",
                encoding="utf-8",
            )
            assert get_next_task(str(progress_file)) == "R02"


class TestUnreadableFileExceptionPaths:
    """Tests for OSError/UnicodeDecodeError handling in progress readers."""

    def test_read_progress_oserror_returns_defaults(self) -> None:
        """read_progress should return safe defaults when file is unreadable (OSError)."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            progress_file.write_text("some content", encoding="utf-8")

            with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
                state = read_progress(progress_file)

            assert state.tasks_completed == 0
            assert state.next_task == "T00"
            assert state.done_tasks == []
            assert state.raw_content == ""

    def test_read_progress_unicode_decode_error_returns_defaults(self) -> None:
        """read_progress should return safe defaults when file has encoding errors."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            progress_file.write_text("some content", encoding="utf-8")

            with patch.object(
                Path, "read_text", side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "invalid")
            ):
                state = read_progress(progress_file)

            assert state.tasks_completed == 0
            assert state.next_task == "T00"
            assert state.done_tasks == []

    def test_task_is_done_oserror_returns_false(self) -> None:
        """task_is_done should return False when file is unreadable (OSError)."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            progress_file.write_text("| T01 | First | Done | 2026-04-13 |\n", encoding="utf-8")

            with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
                assert task_is_done(progress_file, "T01") is False

    def test_task_is_done_unicode_decode_error_returns_false(self) -> None:
        """task_is_done should return False when file has encoding errors."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            progress_file.write_text("| T01 | First | Done | 2026-04-13 |\n", encoding="utf-8")

            with patch.object(
                Path, "read_text", side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "invalid")
            ):
                assert task_is_done(progress_file, "T01") is False

    def test_get_next_task_oserror_returns_default(self) -> None:
        """get_next_task should return T00 when file is unreadable (OSError)."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            progress_file.write_text("**Next task to run:** T05\n", encoding="utf-8")

            with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
                assert get_next_task(progress_file) == "T00"

    def test_get_next_task_unicode_decode_error_returns_default(self) -> None:
        """get_next_task should return T00 when file has encoding errors."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "PROGRESS.md"
            progress_file.write_text("**Next task to run:** T05\n", encoding="utf-8")

            with patch.object(
                Path, "read_text", side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "invalid")
            ):
                assert get_next_task(progress_file) == "T00"


class TestTerminalStatusVocabulary:
    """Tests for the canonical status vocabulary exposed by progress.py."""

    def test_terminal_statuses_tuple(self) -> None:
        """TERMINAL_STATUSES must contain Done, Failed, and Blocked."""
        assert "Done" in TERMINAL_STATUSES
        assert "Failed" in TERMINAL_STATUSES
        assert "Blocked" in TERMINAL_STATUSES
        assert "Pending" not in TERMINAL_STATUSES

    def test_pattern_matches_failed(self) -> None:
        """_task_status_pattern should match a Failed row."""
        content = "| T01 | Name | Failed | 3 attempts |\n"
        pat = _task_status_pattern("T01", "Failed")
        assert pat.search(content) is not None

    def test_pattern_matches_blocked(self) -> None:
        """_task_status_pattern should match a Blocked row."""
        content = "| T01 | Name | Blocked | rate limit |\n"
        pat = _task_status_pattern("T01", "Blocked")
        assert pat.search(content) is not None

    def test_pattern_tolerates_annotation(self) -> None:
        """Status match must accept annotated cells like 'Failed (3 attempts)'."""
        content = "| T01 | Name | Failed (3 attempts) | — |\n"
        pat = _task_status_pattern("T01", "Failed")
        assert pat.search(content) is not None


class TestReconcileTaskStatus:
    """Tests for the authoritative reconcile_task_status helper."""

    def _sample_progress(self, tmp_path: Path) -> Path:
        """Write a minimal PROGRESS.md and return its path."""
        p = tmp_path / "PROGRESS.md"
        p.write_text(
            "**Tasks completed:** 0\n"
            "**Next task to run:** T01\n"
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|-----------|\n"
            "| T01 | First  | Pending | — |\n"
            "| T02 | Second | Pending | — |\n",
            encoding="utf-8",
        )
        return p

    def test_reconcile_pending_to_done_writes_date(self, tmp_path: Path) -> None:
        """Reconciling Pending → Done should stamp the Completed cell with the date."""
        p = self._sample_progress(tmp_path)
        ok = reconcile_task_status(p, "T01", "Done", completed="2026-04-28")
        assert ok is True
        assert task_is_done(p, "T01") is True
        content = p.read_text(encoding="utf-8")
        assert "| Done | 2026-04-28 |" in content
        # T02 untouched
        assert "| T02 | Second | Pending | — |" in content

    def test_reconcile_pending_to_failed_with_attempts(self, tmp_path: Path) -> None:
        """Reconciling Pending → Failed must persist across reads."""
        p = self._sample_progress(tmp_path)
        ok = reconcile_task_status(p, "T02", "Failed", completed="3 attempts")
        assert ok is True
        assert task_is_resolved(p, "T02") is True
        assert task_is_done(p, "T02") is False
        state = read_progress(p)
        assert state.failed_tasks == ["T02"]
        assert state.done_tasks == []
        assert "T02" in state.resolved_tasks

    def test_reconcile_overwrites_existing_annotation(self, tmp_path: Path) -> None:
        """Reconcile must replace an already-annotated cell cleanly."""
        p = tmp_path / "PROGRESS.md"
        p.write_text(
            "| T01 | Name | Failed (1 attempt) | 2026-04-28 |\n",
            encoding="utf-8",
        )
        ok = reconcile_task_status(p, "T01", "Done", completed="2026-04-29")
        assert ok is True
        content = p.read_text(encoding="utf-8")
        assert "Failed" not in content
        assert "Done" in content
        assert "2026-04-29" in content

    def test_reconcile_missing_row_returns_false(self, tmp_path: Path) -> None:
        """Reconcile for an absent prefix must not write anything."""
        p = self._sample_progress(tmp_path)
        before = p.read_text(encoding="utf-8")
        ok = reconcile_task_status(p, "T99", "Failed")
        assert ok is False
        assert p.read_text(encoding="utf-8") == before

    def test_reconcile_missing_file_returns_false(self) -> None:
        """Reconcile for a missing file must not raise."""
        ok = reconcile_task_status(Path("/nonexistent/PROGRESS.md"), "T01", "Done")
        assert ok is False

    def test_reconcile_preserves_completed_when_none(self, tmp_path: Path) -> None:
        """When completed=None the existing Completed cell must be preserved."""
        p = tmp_path / "PROGRESS.md"
        p.write_text(
            "| T01 | Name | Pending | — |\n",
            encoding="utf-8",
        )
        ok = reconcile_task_status(p, "T01", "Failed")
        assert ok is True
        # Completed cell was "—" and should be untouched
        assert "| Failed | — |" in p.read_text(encoding="utf-8")

    def test_reconcile_idempotent(self, tmp_path: Path) -> None:
        """Running reconcile twice with the same values should not corrupt the row."""
        p = self._sample_progress(tmp_path)
        reconcile_task_status(p, "T01", "Done", completed="2026-04-28")
        first = p.read_text(encoding="utf-8")
        reconcile_task_status(p, "T01", "Done", completed="2026-04-28")
        second = p.read_text(encoding="utf-8")
        assert first == second


class TestTaskIsResolved:
    """Tests for task_is_resolved — the skip-check semantics."""

    def test_done_is_resolved(self, tmp_path: Path) -> None:
        p = tmp_path / "PROGRESS.md"
        p.write_text("| T01 | Name | Done | 2026-04-28 |\n", encoding="utf-8")
        assert task_is_resolved(p, "T01") is True

    def test_failed_is_resolved(self, tmp_path: Path) -> None:
        p = tmp_path / "PROGRESS.md"
        p.write_text("| T01 | Name | Failed | 3 attempts |\n", encoding="utf-8")
        assert task_is_resolved(p, "T01") is True

    def test_blocked_is_resolved(self, tmp_path: Path) -> None:
        p = tmp_path / "PROGRESS.md"
        p.write_text("| T01 | Name | Blocked | rate limit |\n", encoding="utf-8")
        assert task_is_resolved(p, "T01") is True

    def test_pending_is_not_resolved(self, tmp_path: Path) -> None:
        p = tmp_path / "PROGRESS.md"
        p.write_text("| T01 | Name | Pending | — |\n", encoding="utf-8")
        assert task_is_resolved(p, "T01") is False

    def test_missing_file(self) -> None:
        assert task_is_resolved(Path("/nonexistent/PROGRESS.md"), "T01") is False

    def test_missing_row(self, tmp_path: Path) -> None:
        p = tmp_path / "PROGRESS.md"
        p.write_text("| T02 | Other | Done | 2026-04-28 |\n", encoding="utf-8")
        assert task_is_resolved(p, "T01") is False


class TestTaskStatus:
    """Tests for task_status — returns the canonical status string."""

    def test_returns_done(self, tmp_path: Path) -> None:
        p = tmp_path / "PROGRESS.md"
        p.write_text("| T01 | Name | Done | 2026-04-28 |\n", encoding="utf-8")
        assert task_status(p, "T01") == "Done"

    def test_returns_failed(self, tmp_path: Path) -> None:
        p = tmp_path / "PROGRESS.md"
        p.write_text("| T01 | Name | Failed | 3 attempts |\n", encoding="utf-8")
        assert task_status(p, "T01") == "Failed"

    def test_returns_pending(self, tmp_path: Path) -> None:
        p = tmp_path / "PROGRESS.md"
        p.write_text("| T01 | Name | Pending | — |\n", encoding="utf-8")
        assert task_status(p, "T01") == "Pending"

    def test_returns_none_for_missing_row(self, tmp_path: Path) -> None:
        p = tmp_path / "PROGRESS.md"
        p.write_text("| T02 | Name | Done | — |\n", encoding="utf-8")
        assert task_status(p, "T01") is None

    def test_returns_none_for_missing_file(self) -> None:
        assert task_status(Path("/nonexistent/PROGRESS.md"), "T01") is None


class TestProgressStateResolved:
    """Tests for ProgressState.resolved_tasks and per-status buckets."""

    def test_read_progress_buckets_all_terminal_statuses(self, tmp_path: Path) -> None:
        p = tmp_path / "PROGRESS.md"
        p.write_text(
            "**Tasks completed:** 1\n"
            "**Next task to run:** T04\n"
            "| T01 | A | Done    | 2026-04-28 |\n"
            "| T02 | B | Failed  | 3 attempts |\n"
            "| T03 | C | Blocked | rate limit |\n"
            "| T04 | D | Pending | — |\n",
            encoding="utf-8",
        )
        state = read_progress(p)
        assert state.done_tasks == ["T01"]
        assert state.failed_tasks == ["T02"]
        assert state.blocked_tasks == ["T03"]
        assert state.resolved_tasks == ["T01", "T02", "T03"]

    def test_resolved_tasks_deduplicates(self) -> None:
        """resolved_tasks property must not return duplicates even if buckets overlap."""
        from the_architect.core.progress import ProgressState

        state = ProgressState(
            done_tasks=["T01"],
            failed_tasks=["T01", "T02"],
            blocked_tasks=["T02"],
        )
        assert state.resolved_tasks == ["T01", "T02"]


class TestAtomicLock:
    """Tests for acquire_lock atomic behaviour."""

    def test_acquire_lock_creates_file(self, tmp_path: Path) -> None:
        """acquire_lock should create the lock file."""
        from the_architect.core.runner import acquire_lock, release_lock

        result = acquire_lock(tmp_path)
        assert result is True
        assert (tmp_path / ".architect" / "runner.lock").exists()
        release_lock(tmp_path)

    def test_acquire_lock_fails_if_already_locked(self, tmp_path: Path) -> None:
        """Second acquire_lock while first is held should return False."""
        from the_architect.core.runner import acquire_lock, release_lock

        assert acquire_lock(tmp_path) is True
        # Second acquisition should fail
        assert acquire_lock(tmp_path) is False
        release_lock(tmp_path)

    def test_atomic_creation_raises_file_exists_error(self, tmp_path: Path) -> None:
        """Concurrent creation attempt must raise FileExistsError, not silently overwrite."""
        import os

        lock_dir = tmp_path / ".architect"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / "runner.lock"

        # Create the lock file the first time
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)

        # A second attempt with the same flags must raise FileExistsError
        with pytest.raises(FileExistsError):
            fd2 = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd2)

    def test_release_lock_removes_file(self, tmp_path: Path) -> None:
        """release_lock should remove the lock file."""
        from the_architect.core.runner import acquire_lock, release_lock

        acquire_lock(tmp_path)
        release_lock(tmp_path)
        assert not (tmp_path / ".architect" / "runner.lock").exists()

    def test_acquire_lock_after_release(self, tmp_path: Path) -> None:
        """After releasing the lock it should be possible to acquire again."""
        from the_architect.core.runner import acquire_lock, release_lock

        assert acquire_lock(tmp_path) is True
        release_lock(tmp_path)
        assert acquire_lock(tmp_path) is True
        release_lock(tmp_path)
