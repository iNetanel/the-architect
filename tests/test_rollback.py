"""Tests for the_architect.core.rollback — run rollback core module and CLI command.

Covers:
- RollbackError, RollbackPlan, RollbackResult, BaselineInfo models
- compute_rollback_plan() — modified, created, deleted, unchanged, empty, corrupted
- execute_rollback() — restore, delete, dry-run, error handling, partial failures
- list_run_baselines() — multiple, none, corrupted, sorting
- Git helpers — _find_commit_before_timestamp, _get_file_content_at_commit
- CLI command — --task, --all, --dry-run, --json, --yes, mutual exclusion, errors
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from the_architect.cli import main
from the_architect.core.baseline import FileRecord, WorkspaceBaseline
from the_architect.core.rollback import (
    BaselineInfo,
    RollbackError,
    RollbackPlan,
    RollbackResult,
    _find_commit_before_timestamp,
    _get_file_content_at_commit,
    compute_rollback_plan,
    execute_rollback,
    list_run_baselines,
)

# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestRollbackError:
    """Tests for the RollbackError Pydantic model."""

    def test_creation(self) -> None:
        """RollbackError should accept path and message."""
        err = RollbackError(path="foo.py", message="cannot restore")
        assert err.path == "foo.py"
        assert err.message == "cannot restore"

    def test_model_dump(self) -> None:
        """model_dump() should produce a plain dict."""
        err = RollbackError(path="bar.md", message="git failed")
        dump = err.model_dump()
        assert dump == {"path": "bar.md", "message": "git failed"}


class TestRollbackPlan:
    """Tests for the RollbackPlan Pydantic model."""

    def test_creation_with_all_fields(self) -> None:
        """RollbackPlan should accept all three fields."""
        plan = RollbackPlan(
            files_to_restore={"a.py": "content"},
            files_to_delete=["b.py"],
            files_unchanged=["c.py"],
        )
        assert len(plan.files_to_restore) == 1
        assert plan.files_to_delete == ["b.py"]
        assert plan.files_unchanged == ["c.py"]

    def test_defaults_are_empty(self) -> None:
        """All fields should default to empty collections."""
        plan = RollbackPlan()
        assert plan.files_to_restore == {}
        assert plan.files_to_delete == []
        assert plan.files_unchanged == []

    def test_model_dump(self) -> None:
        """model_dump() should serialize all fields."""
        plan = RollbackPlan(
            files_to_restore={"x.py": "code"},
            files_to_delete=["y.py"],
            files_unchanged=["z.py"],
        )
        dump = plan.model_dump()
        assert dump["files_to_restore"] == {"x.py": "code"}
        assert dump["files_to_delete"] == ["y.py"]
        assert dump["files_unchanged"] == ["z.py"]


class TestRollbackResult:
    """Tests for the RollbackResult Pydantic model."""

    def test_creation_with_all_fields(self) -> None:
        """RollbackResult should accept all four fields."""
        result = RollbackResult(
            restored_count=3,
            deleted_count=1,
            unchanged_count=5,
            errors=[RollbackError(path="fail.py", message="error")],
        )
        assert result.restored_count == 3
        assert result.deleted_count == 1
        assert result.unchanged_count == 5
        assert len(result.errors) == 1

    def test_defaults(self) -> None:
        """All counts default to 0, errors to empty list."""
        result = RollbackResult()
        assert result.restored_count == 0
        assert result.deleted_count == 0
        assert result.unchanged_count == 0
        assert result.errors == []

    def test_model_dump(self) -> None:
        """model_dump() should serialize all fields."""
        result = RollbackResult(
            restored_count=2,
            deleted_count=1,
            unchanged_count=3,
            errors=[],
        )
        dump = result.model_dump()
        assert dump["restored_count"] == 2
        assert dump["deleted_count"] == 1
        assert dump["unchanged_count"] == 3
        assert dump["errors"] == []


class TestBaselineInfo:
    """Tests for the BaselineInfo Pydantic model."""

    def test_creation(self) -> None:
        """BaselineInfo should accept all four fields."""
        info = BaselineInfo(
            task_prefix="T01",
            timestamp="2026-05-18T10:00:00",
            file_count=5,
            file_path="/abs/path/baseline.json",
        )
        assert info.task_prefix == "T01"
        assert info.timestamp == "2026-05-18T10:00:00"
        assert info.file_count == 5
        assert info.file_path == "/abs/path/baseline.json"

    def test_model_dump(self) -> None:
        """model_dump() should serialize all fields."""
        info = BaselineInfo(
            task_prefix="T02",
            timestamp="2026-05-18T11:00:00",
            file_count=3,
            file_path="/other/baseline.json",
        )
        dump = info.model_dump()
        assert dump["task_prefix"] == "T02"
        assert dump["file_count"] == 3


# ---------------------------------------------------------------------------
# Git helper tests
# ---------------------------------------------------------------------------


class TestFindCommitBeforeTimestamp:
    """Tests for _find_commit_before_timestamp()."""

    def test_returns_sha_on_success(self, tmp_path: Path) -> None:
        """Should return a 40-char SHA when git succeeds."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "abcdef1234567890abcdef1234567890abcdef12\n"

        with patch("subprocess.run", return_value=mock_result):
            sha = _find_commit_before_timestamp(tmp_path, datetime.now(UTC))
        assert sha == "abcdef1234567890abcdef1234567890abcdef12"

    def test_returns_none_on_git_error(self, tmp_path: Path) -> None:
        """Should return None when git returns non-zero."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            sha = _find_commit_before_timestamp(tmp_path, datetime.now(UTC))
        assert sha is None

    def test_returns_none_on_empty_output(self, tmp_path: Path) -> None:
        """Should return None when git succeeds but output is empty."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "   \n"

        with patch("subprocess.run", return_value=mock_result):
            sha = _find_commit_before_timestamp(tmp_path, datetime.now(UTC))
        assert sha is None

    def test_returns_none_on_timeout(self, tmp_path: Path) -> None:
        """Should return None on subprocess.TimeoutExpired."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 10)):
            sha = _find_commit_before_timestamp(tmp_path, datetime.now(UTC))
        assert sha is None

    def test_returns_none_on_file_not_found(self, tmp_path: Path) -> None:
        """Should return None when git binary is not found."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            sha = _find_commit_before_timestamp(tmp_path, datetime.now(UTC))
        assert sha is None

    def test_returns_none_on_os_error(self, tmp_path: Path) -> None:
        """Should return None on OSError."""
        with patch("subprocess.run", side_effect=OSError("permission denied")):
            sha = _find_commit_before_timestamp(tmp_path, datetime.now(UTC))
        assert sha is None

    def test_passes_correct_args(self, tmp_path: Path) -> None:
        """Should call git log with correct arguments."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "deadbeef" + "0" * 32 + "\n"

        ts = datetime(2026, 5, 18, 10, 0, 0, tzinfo=UTC)

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            _find_commit_before_timestamp(tmp_path, ts)

        mock_run.assert_called_once()
        args = mock_run.call_args
        assert "git" in args[0][0]
        assert "--format=%H" in args[0][0]
        assert "-1" in args[0][0]
        assert f"--before={ts.isoformat()}" in args[0][0]


class TestGetFileContentAtCommit:
    """Tests for _get_file_content_at_commit()."""

    def test_returns_content_on_success(self, tmp_path: Path) -> None:
        """Should return file content as UTF-8 string."""
        expected = "def hello():\n    print('world')\n"
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = expected.encode("utf-8")

        with patch("subprocess.run", return_value=mock_result):
            content = _get_file_content_at_commit(tmp_path, "abc123", "main.py")
        assert content == expected

    def test_returns_none_on_git_error(self, tmp_path: Path) -> None:
        """Should return None when git show fails."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = b""

        with patch("subprocess.run", return_value=mock_result):
            content = _get_file_content_at_commit(tmp_path, "abc123", "missing.py")
        assert content is None

    def test_returns_none_on_timeout(self, tmp_path: Path) -> None:
        """Should return None on subprocess.TimeoutExpired."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 10)):
            content = _get_file_content_at_commit(tmp_path, "abc123", "main.py")
        assert content is None

    def test_returns_none_on_file_not_found(self, tmp_path: Path) -> None:
        """Should return None when git binary is missing."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            content = _get_file_content_at_commit(tmp_path, "abc123", "main.py")
        assert content is None

    def test_returns_none_on_os_error(self, tmp_path: Path) -> None:
        """Should return None on OSError."""
        with patch("subprocess.run", side_effect=OSError("broken pipe")):
            content = _get_file_content_at_commit(tmp_path, "abc123", "main.py")
        assert content is None

    def test_returns_none_on_unicode_decode_error(self, tmp_path: Path) -> None:
        """Should return None when content is not valid UTF-8."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = b"\xff\xfe\x00\x01"

        with patch("subprocess.run", return_value=mock_result):
            content = _get_file_content_at_commit(tmp_path, "abc123", "binary.bin")
        assert content is None

    def test_passes_correct_args(self, tmp_path: Path) -> None:
        """Should call git show with commit:file_path."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = b"content"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            _get_file_content_at_commit(tmp_path, "deadbeef", "src/app.py")

        args = mock_run.call_args
        assert "git" in args[0][0]
        assert "show" in args[0][0]
        assert "deadbeef:src/app.py" in args[0][0]


# ---------------------------------------------------------------------------
# compute_rollback_plan tests
# ---------------------------------------------------------------------------


class TestComputeRollbackPlan:
    """Tests for compute_rollback_plan()."""

    def _make_baseline(self, tmp_path: Path, files: dict[str, str]) -> WorkspaceBaseline:
        """Create files on disk and capture a baseline."""
        for rel, content in files.items():
            (tmp_path / rel).write_text(content, encoding="utf-8")
        from the_architect.core.baseline import capture_baseline

        return capture_baseline(tmp_path, task_prefix="T01")

    def test_empty_baseline_no_changes(self, tmp_path: Path) -> None:
        """Empty baseline with no files produces empty plan."""
        bl = WorkspaceBaseline()
        plan = compute_rollback_plan(bl, tmp_path)
        assert plan.files_to_restore == {}
        assert plan.files_to_delete == []
        assert plan.files_unchanged == []

    def test_unchanged_files_only(self, tmp_path: Path) -> None:
        """Files that exist in baseline and unchanged on disk are unchanged."""
        bl = self._make_baseline(tmp_path, {"stable.py": "content"})
        plan = compute_rollback_plan(bl, tmp_path)
        assert plan.files_to_restore == {}
        assert plan.files_to_delete == []
        assert "stable.py" in plan.files_unchanged

    def test_modified_files_restored(self, tmp_path: Path) -> None:
        """Modified files should appear in files_to_restore."""
        bl = self._make_baseline(tmp_path, {"mod.py": "original"})
        # Modify the file after baseline
        (tmp_path / "mod.py").write_text("changed content", encoding="utf-8")

        # Mock git to return original content
        with (
            patch(
                "the_architect.core.rollback._find_commit_before_timestamp",
                return_value="abc123",
            ),
            patch(
                "the_architect.core.rollback._get_file_content_at_commit",
                return_value="original",
            ),
        ):
            plan = compute_rollback_plan(bl, tmp_path)

        assert "mod.py" in plan.files_to_restore
        assert plan.files_to_restore["mod.py"] == "original"

    def test_created_files_marked_for_deletion(self, tmp_path: Path) -> None:
        """New files not in baseline should be in files_to_delete."""
        bl = self._make_baseline(tmp_path, {"old.py": "content"})
        # Create a new file after baseline
        (tmp_path / "new.py").write_text("new content", encoding="utf-8")

        plan = compute_rollback_plan(bl, tmp_path)
        assert "new.py" in plan.files_to_delete

    def test_git_failure_skips_restoration(self, tmp_path: Path) -> None:
        """If git cannot find the commit, modified files are skipped."""
        bl = self._make_baseline(tmp_path, {"mod.py": "original"})
        (tmp_path / "mod.py").write_text("changed", encoding="utf-8")

        # Mock git to fail
        with patch(
            "the_architect.core.rollback._find_commit_before_timestamp",
            return_value=None,
        ):
            plan = compute_rollback_plan(bl, tmp_path)

        # File is modified but cannot be restored (no git)
        assert "mod.py" not in plan.files_to_restore

    def test_git_commit_found_but_file_missing(self, tmp_path: Path) -> None:
        """If git commit exists but file didn't exist at that commit, skip."""
        bl = self._make_baseline(tmp_path, {"mod.py": "original"})
        (tmp_path / "mod.py").write_text("changed", encoding="utf-8")

        with (
            patch(
                "the_architect.core.rollback._find_commit_before_timestamp",
                return_value="abc123",
            ),
            patch(
                "the_architect.core.rollback._get_file_content_at_commit",
                return_value=None,
            ),
        ):
            plan = compute_rollback_plan(bl, tmp_path)

        # File exists but git couldn't retrieve content
        assert "mod.py" not in plan.files_to_restore

    def test_multiple_modified_and_created(self, tmp_path: Path) -> None:
        """Multiple modified and created files are all classified correctly."""
        bl = self._make_baseline(tmp_path, {"a.py": "v1", "b.py": "v2"})
        (tmp_path / "a.py").write_text("v1_changed", encoding="utf-8")
        (tmp_path / "b.py").write_text("v2_changed", encoding="utf-8")
        (tmp_path / "c.py").write_text("new", encoding="utf-8")

        with (
            patch(
                "the_architect.core.rollback._find_commit_before_timestamp",
                return_value="abc123",
            ),
            patch(
                "the_architect.core.rollback._get_file_content_at_commit",
                return_value="original",
            ),
        ):
            plan = compute_rollback_plan(bl, tmp_path)

        assert "a.py" in plan.files_to_restore
        assert "b.py" in plan.files_to_restore
        assert "c.py" in plan.files_to_delete

    def test_files_to_restore_sorted(self, tmp_path: Path) -> None:
        """files_to_restore keys should be sorted alphabetically."""
        bl = self._make_baseline(tmp_path, {"z.py": "v1", "a.py": "v2", "m.py": "v3"})
        (tmp_path / "z.py").write_text("changed", encoding="utf-8")
        (tmp_path / "a.py").write_text("changed", encoding="utf-8")
        (tmp_path / "m.py").write_text("changed", encoding="utf-8")

        with (
            patch(
                "the_architect.core.rollback._find_commit_before_timestamp",
                return_value="abc123",
            ),
            patch(
                "the_architect.core.rollback._get_file_content_at_commit",
                return_value="original",
            ),
        ):
            plan = compute_rollback_plan(bl, tmp_path)

        assert list(plan.files_to_restore.keys()) == ["a.py", "m.py", "z.py"]

    def test_files_to_delete_sorted(self, tmp_path: Path) -> None:
        """files_to_delete should be sorted alphabetically."""
        bl = self._make_baseline(tmp_path, {"a.py": "v1"})
        (tmp_path / "z.py").write_text("new", encoding="utf-8")
        (tmp_path / "b.py").write_text("new", encoding="utf-8")

        plan = compute_rollback_plan(bl, tmp_path)
        assert plan.files_to_delete == ["b.py", "z.py"]

    def test_unchanged_sorted(self, tmp_path: Path) -> None:
        """files_unchanged should be sorted alphabetically."""
        bl = self._make_baseline(tmp_path, {"z.py": "v1", "a.py": "v2", "m.py": "v3"})
        plan = compute_rollback_plan(bl, tmp_path)
        assert plan.files_unchanged == ["a.py", "m.py", "z.py"]


# ---------------------------------------------------------------------------
# execute_rollback tests
# ---------------------------------------------------------------------------


class TestExecuteRollback:
    """Tests for execute_rollback()."""

    def test_dry_run_no_modifications(self, tmp_path: Path) -> None:
        """Dry run should return counts without modifying files."""
        plan = RollbackPlan(
            files_to_restore={"a.py": "original"},
            files_to_delete=["b.py"],
            files_unchanged=["c.py"],
        )
        # Create files on disk
        (tmp_path / "a.py").write_text("modified", encoding="utf-8")
        (tmp_path / "b.py").write_text("new", encoding="utf-8")
        (tmp_path / "c.py").write_text("unchanged", encoding="utf-8")

        result = execute_rollback(plan, tmp_path, dry_run=True)

        assert result.restored_count == 1
        assert result.deleted_count == 1
        assert result.unchanged_count == 1
        assert result.errors == []
        # Files should NOT be modified
        assert (tmp_path / "a.py").read_text(encoding="utf-8") == "modified"
        assert (tmp_path / "b.py").exists()

    def test_restores_files(self, tmp_path: Path) -> None:
        """Should overwrite modified files with original content."""
        (tmp_path / "a.py").write_text("modified content", encoding="utf-8")
        plan = RollbackPlan(
            files_to_restore={"a.py": "original content"},
            files_to_delete=[],
            files_unchanged=[],
        )

        result = execute_rollback(plan, tmp_path, dry_run=False)

        assert result.restored_count == 1
        assert (tmp_path / "a.py").read_text(encoding="utf-8") == "original content"

    def test_deletes_created_files(self, tmp_path: Path) -> None:
        """Should remove files that were created during the run."""
        (tmp_path / "new.py").write_text("new content", encoding="utf-8")
        plan = RollbackPlan(
            files_to_restore={},
            files_to_delete=["new.py"],
            files_unchanged=[],
        )

        result = execute_rollback(plan, tmp_path, dry_run=False)

        assert result.deleted_count == 1
        assert not (tmp_path / "new.py").exists()

    def test_deletion_skips_already_deleted(self, tmp_path: Path) -> None:
        """Should skip files in files_to_delete that no longer exist."""
        plan = RollbackPlan(
            files_to_restore={},
            files_to_delete=["gone.py"],
            files_unchanged=[],
        )

        result = execute_rollback(plan, tmp_path, dry_run=False)

        assert result.deleted_count == 0
        assert result.errors == []

    def test_deletion_errors_on_directory(self, tmp_path: Path) -> None:
        """Should error when a deletion target is a directory, not a file."""
        (tmp_path / "dir_target").mkdir()
        plan = RollbackPlan(
            files_to_restore={},
            files_to_delete=["dir_target"],
            files_unchanged=[],
        )

        result = execute_rollback(plan, tmp_path, dry_run=False)

        assert result.deleted_count == 0
        assert len(result.errors) == 1
        assert result.errors[0].path == "dir_target"

    def test_restore_os_error_recorded(self, tmp_path: Path) -> None:
        """OSError during file restore should be recorded as error."""
        (tmp_path / "a.py").write_text("modified", encoding="utf-8")
        plan = RollbackPlan(
            files_to_restore={"a.py": "original"},
            files_to_delete=[],
            files_unchanged=[],
        )

        with patch(
            "the_architect.core.rollback.atomic_write_text",
            side_effect=OSError("no access"),
        ):
            result = execute_rollback(plan, tmp_path, dry_run=False)

        assert result.restored_count == 0
        assert len(result.errors) == 1
        assert result.errors[0].path == "a.py"
        assert "no access" in result.errors[0].message

    def test_delete_os_error_recorded(self, tmp_path: Path) -> None:
        """OSError during file deletion should be recorded as error."""
        (tmp_path / "new.py").write_text("new", encoding="utf-8")
        plan = RollbackPlan(
            files_to_restore={},
            files_to_delete=["new.py"],
            files_unchanged=[],
        )

        def mock_unlink(*args, **kwargs):
            raise OSError("permission denied")

        with patch.object(Path, "unlink", side_effect=mock_unlink):
            result = execute_rollback(plan, tmp_path, dry_run=False)

        assert result.deleted_count == 0
        assert len(result.errors) == 1
        assert result.errors[0].path == "new.py"

    def test_partial_failure_continues(self, tmp_path: Path) -> None:
        """Partial failure on one file should not stop processing others."""
        (tmp_path / "a.py").write_text("modified", encoding="utf-8")
        (tmp_path / "b.py").write_text("modified", encoding="utf-8")
        (tmp_path / "new.py").write_text("new", encoding="utf-8")
        plan = RollbackPlan(
            files_to_restore={"a.py": "orig_a", "b.py": "orig_b"},
            files_to_delete=["new.py"],
            files_unchanged=[],
        )

        # Make restoring a.py fail
        call_count = 0

        def selective_fail(path, content):
            nonlocal call_count
            call_count += 1
            if path.name == "a.py":
                raise OSError("no access")
            atomic_write_text_orig(path, content)

        # Import the real atomic_write_text to use for b.py
        from the_architect.core.fileutil import atomic_write_text as atomic_write_text_orig

        with patch("the_architect.core.rollback.atomic_write_text", side_effect=selective_fail):
            result = execute_rollback(plan, tmp_path, dry_run=False)

        assert result.restored_count == 1  # b.py succeeded
        assert len(result.errors) == 1
        assert result.errors[0].path == "a.py"

    def test_empty_plan_noop(self, tmp_path: Path) -> None:
        """Empty plan should return zero counts."""
        plan = RollbackPlan()
        result = execute_rollback(plan, tmp_path, dry_run=False)
        assert result.restored_count == 0
        assert result.deleted_count == 0
        assert result.unchanged_count == 0
        assert result.errors == []


# ---------------------------------------------------------------------------
# list_run_baselines tests
# ---------------------------------------------------------------------------


class TestListRunBaselines:
    """Tests for list_run_baselines()."""

    def _write_baseline_json(
        self,
        baselines_dir: Path,
        name: str,
        task_prefix: str,
        timestamp: str,
        file_count: int,
    ) -> None:
        """Write a baseline JSON file."""
        data = {
            "task_prefix": task_prefix,
            "timestamp": timestamp,
            "files": {
                f"file_{i}.py": {"path": f"file_{i}.py", "sha256": "abc", "size": 10}
                for i in range(file_count)
            },
        }
        (baselines_dir / f"{name}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    def test_no_baselines_dir(self, tmp_path: Path) -> None:
        """Returns empty list when baselines directory does not exist."""
        result = list_run_baselines(tmp_path)
        assert result == []

    def test_empty_baselines_dir(self, tmp_path: Path) -> None:
        """Returns empty list when baselines directory is empty."""
        baselines_dir = tmp_path / ".architect" / "baselines"
        baselines_dir.mkdir(parents=True)
        result = list_run_baselines(tmp_path)
        assert result == []

    def test_single_baseline(self, tmp_path: Path) -> None:
        """Returns one BaselineInfo for a single baseline file."""
        baselines_dir = tmp_path / ".architect" / "baselines"
        baselines_dir.mkdir(parents=True)
        self._write_baseline_json(baselines_dir, "T01", "T01", "2026-05-18T10:00:00", 3)

        result = list_run_baselines(tmp_path)
        assert len(result) == 1
        assert result[0].task_prefix == "T01"
        assert result[0].file_count == 3
        assert result[0].timestamp == "2026-05-18T10:00:00"

    def test_multiple_baselines_sorted(self, tmp_path: Path) -> None:
        """Returns baselines sorted by timestamp (oldest first)."""
        baselines_dir = tmp_path / ".architect" / "baselines"
        baselines_dir.mkdir(parents=True)
        # Write in reverse order
        self._write_baseline_json(baselines_dir, "T02", "T02", "2026-05-18T12:00:00", 5)
        self._write_baseline_json(baselines_dir, "T01", "T01", "2026-05-18T10:00:00", 3)
        self._write_baseline_json(baselines_dir, "T03", "T03", "2026-05-18T11:00:00", 4)

        result = list_run_baselines(tmp_path)
        assert len(result) == 3
        assert result[0].task_prefix == "T01"
        assert result[1].task_prefix == "T03"
        assert result[2].task_prefix == "T02"

    def test_corrupted_baseline_skipped(self, tmp_path: Path) -> None:
        """Corrupted JSON files are skipped gracefully."""
        baselines_dir = tmp_path / ".architect" / "baselines"
        baselines_dir.mkdir(parents=True)
        self._write_baseline_json(baselines_dir, "T01", "T01", "2026-05-18T10:00:00", 3)
        (baselines_dir / "T02.json").write_text("not valid json{", encoding="utf-8")
        self._write_baseline_json(baselines_dir, "T03", "T03", "2026-05-18T11:00:00", 4)

        result = list_run_baselines(tmp_path)
        assert len(result) == 2
        assert result[0].task_prefix == "T01"
        assert result[1].task_prefix == "T03"

    def test_fallback_task_prefix_from_filename(self, tmp_path: Path) -> None:
        """Uses filename stem when task_prefix key is missing."""
        baselines_dir = tmp_path / ".architect" / "baselines"
        baselines_dir.mkdir(parents=True)
        data = {"timestamp": "2026-05-18T10:00:00", "files": {}}
        (baselines_dir / "T01.json").write_text(json.dumps(data), encoding="utf-8")

        result = list_run_baselines(tmp_path)
        assert len(result) == 1
        assert result[0].task_prefix == "T01"

    def test_fallback_empty_timestamp(self, tmp_path: Path) -> None:
        """Uses empty string when timestamp key is missing."""
        baselines_dir = tmp_path / ".architect" / "baselines"
        baselines_dir.mkdir(parents=True)
        data = {"task_prefix": "T01", "files": {}}
        (baselines_dir / "T01.json").write_text(json.dumps(data), encoding="utf-8")

        result = list_run_baselines(tmp_path)
        assert len(result) == 1
        assert result[0].timestamp == ""

    def test_file_path_is_absolute(self, tmp_path: Path) -> None:
        """file_path should be the absolute resolved path."""
        baselines_dir = tmp_path / ".architect" / "baselines"
        baselines_dir.mkdir(parents=True)
        self._write_baseline_json(baselines_dir, "T01", "T01", "2026-05-18T10:00:00", 2)

        result = list_run_baselines(tmp_path)
        assert len(result) == 1
        assert Path(result[0].file_path).is_absolute()

    def test_non_json_files_ignored(self, tmp_path: Path) -> None:
        """Non-JSON files in baselines dir are ignored."""
        baselines_dir = tmp_path / ".architect" / "baselines"
        baselines_dir.mkdir(parents=True)
        self._write_baseline_json(baselines_dir, "T01", "T01", "2026-05-18T10:00:00", 1)
        (baselines_dir / "notes.txt").write_text("some notes", encoding="utf-8")

        result = list_run_baselines(tmp_path)
        assert len(result) == 1
        assert result[0].task_prefix == "T01"


# ---------------------------------------------------------------------------
# CLI command tests
# ---------------------------------------------------------------------------


class TestRollbackCLI:
    """Tests for the ``architect rollback`` CLI command."""

    def _write_baseline(
        self,
        tmp_path: Path,
        name: str,
        files: dict[str, str],
    ) -> None:
        """Write a baseline JSON with the given file checksums."""
        baseline = WorkspaceBaseline(
            task_prefix=name.replace(".json", ""),
            files={
                p: FileRecord(
                    path=p,
                    sha256=hashlib.sha256(c.encode()).hexdigest(),
                    size=len(c),
                )
                for p, c in files.items()
            },
        )
        baselines_dir = tmp_path / ".architect" / "baselines"
        baselines_dir.mkdir(parents=True, exist_ok=True)
        (baselines_dir / f"{name}.json").write_text(
            baseline.model_dump_json(indent=2), encoding="utf-8"
        )

    def test_help(self) -> None:
        """--help shows usage information."""
        result = CliRunner().invoke(main, ["rollback", "--help"])
        assert result.exit_code == 0
        assert "rollback" in result.output.lower()
        assert "--task" in result.output
        assert "--all" in result.output

    def test_no_baselines(self, tmp_path: Path) -> None:
        """Exits with error when no baselines exist."""
        result = CliRunner().invoke(main, ["rollback", "-p", str(tmp_path)])
        assert result.exit_code == 1
        assert "No baseline data available" in result.output

    def test_task_and_all_mutual_exclusion(self, tmp_path: Path) -> None:
        """--task and --all cannot be combined."""
        self._write_baseline(tmp_path, "T01", {"a.py": "v1"})
        result = CliRunner().invoke(
            main, ["rollback", "-p", str(tmp_path), "--task", "T01", "--all"]
        )
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output

    def test_task_not_found(self, tmp_path: Path) -> None:
        """--task with non-existent task shows error."""
        self._write_baseline(tmp_path, "T01", {"a.py": "v1"})
        result = CliRunner().invoke(main, ["rollback", "-p", str(tmp_path), "--task", "T99"])
        assert result.exit_code == 1
        assert "T99" in result.output
        assert "Available: T01" in result.output

    def test_dry_run_no_changes(self, tmp_path: Path) -> None:
        """--dry-run with no changes shows unchanged message."""
        (tmp_path / "a.py").write_text("v1", encoding="utf-8")
        self._write_baseline(tmp_path, "T01", {"a.py": "v1"})

        result = CliRunner().invoke(
            main, ["rollback", "-p", str(tmp_path), "--task", "T01", "--dry-run"]
        )
        assert result.exit_code == 0
        assert "No changes to rollback" in result.output

    def test_dry_run_with_changes(self, tmp_path: Path) -> None:
        """--dry-run shows plan without modifying files."""
        (tmp_path / "a.py").write_text("v1", encoding="utf-8")
        self._write_baseline(tmp_path, "T01", {"a.py": "v1"})
        # Create a new file after baseline
        (tmp_path / "new.py").write_text("new", encoding="utf-8")

        result = CliRunner().invoke(
            main, ["rollback", "-p", str(tmp_path), "--task", "T01", "--dry-run", "--yes"]
        )
        assert result.exit_code == 0
        assert "Would delete" in result.output
        # new.py should still exist (dry-run)
        assert (tmp_path / "new.py").exists()

    def test_json_output_basic_structure(self, tmp_path: Path) -> None:
        """--json output contains required top-level keys."""
        (tmp_path / "a.py").write_text("v1", encoding="utf-8")
        self._write_baseline(tmp_path, "T01", {"a.py": "v1"})

        result = CliRunner().invoke(
            main, ["rollback", "-p", str(tmp_path), "--task", "T01", "--json", "--yes"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "project" in data
        assert "plan" in data
        assert "result" in data

    def test_json_output_plan_structure(self, tmp_path: Path) -> None:
        """--json plan contains restore_count, delete_count, unchanged_count."""
        (tmp_path / "a.py").write_text("v1", encoding="utf-8")
        self._write_baseline(tmp_path, "T01", {"a.py": "v1"})

        result = CliRunner().invoke(
            main, ["rollback", "-p", str(tmp_path), "--task", "T01", "--json", "--yes"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        plan = data["plan"]
        assert "restore_count" in plan
        assert "delete_count" in plan
        assert "unchanged_count" in plan
        assert "files_to_restore" in plan
        assert "files_to_delete" in plan

    def test_json_output_result_structure(self, tmp_path: Path) -> None:
        """--json result contains restored_count, deleted_count, errors."""
        (tmp_path / "a.py").write_text("v1", encoding="utf-8")
        self._write_baseline(tmp_path, "T01", {"a.py": "v1"})

        result = CliRunner().invoke(
            main, ["rollback", "-p", str(tmp_path), "--task", "T01", "--json", "--yes"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        result_data = data["result"]
        assert "restored_count" in result_data
        assert "deleted_count" in result_data
        assert "unchanged_count" in result_data
        assert "errors" in result_data

    def test_json_deterministic(self, tmp_path: Path) -> None:
        """--json output uses sorted keys for determinism."""
        (tmp_path / "a.py").write_text("v1", encoding="utf-8")
        self._write_baseline(tmp_path, "T01", {"a.py": "v1"})

        result = CliRunner().invoke(
            main, ["rollback", "-p", str(tmp_path), "--task", "T01", "--json", "--yes"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        expected = json.dumps(data, indent=2, sort_keys=True)
        assert result.output.strip() == expected

    def test_json_no_rich_markup(self, tmp_path: Path) -> None:
        """--json output contains no Rich ANSI escape codes."""
        (tmp_path / "a.py").write_text("v1", encoding="utf-8")
        self._write_baseline(tmp_path, "T01", {"a.py": "v1"})

        result = CliRunner().invoke(
            main, ["rollback", "-p", str(tmp_path), "--task", "T01", "--json", "--yes"]
        )
        assert result.exit_code == 0
        assert "\x1b" not in result.output

    def test_all_flag_uses_earliest_baseline(self, tmp_path: Path) -> None:
        """--all uses the earliest (oldest) baseline."""
        (tmp_path / "a.py").write_text("v1", encoding="utf-8")
        self._write_baseline(tmp_path, "T01", {"a.py": "v1"})
        self._write_baseline(tmp_path, "T02", {"a.py": "v1"})

        result = CliRunner().invoke(
            main, ["rollback", "-p", str(tmp_path), "--all", "--dry-run", "--yes"]
        )
        assert result.exit_code == 0

    def test_force_flag_skips_confirmation(self, tmp_path: Path) -> None:
        """--yes/--force skips the confirmation prompt."""
        (tmp_path / "a.py").write_text("v1", encoding="utf-8")
        self._write_baseline(tmp_path, "T01", {"a.py": "v1"})
        (tmp_path / "new.py").write_text("new", encoding="utf-8")

        result = CliRunner().invoke(
            main, ["rollback", "-p", str(tmp_path), "--task", "T01", "--force"]
        )
        assert result.exit_code == 0
        # new.py should be deleted
        assert not (tmp_path / "new.py").exists()

    def test_project_option(self, tmp_path: Path) -> None:
        """--project/-p specifies the project directory."""
        project = tmp_path / "myproject"
        project.mkdir()
        (project / "a.py").write_text("v1", encoding="utf-8")
        self._write_baseline(project, "T01", {"a.py": "v1"})

        result = CliRunner().invoke(
            main, ["rollback", "-p", str(project), "--task", "T01", "--dry-run"]
        )
        assert result.exit_code == 0
        assert "No changes to rollback" in result.output

    def test_format_rollback_json_helper(self, tmp_path: Path) -> None:
        """_format_rollback_json produces valid JSON."""
        from the_architect.cli import _format_rollback_json

        plan = {
            "restore_count": 1,
            "delete_count": 0,
            "unchanged_count": 2,
            "files_to_restore": ["a.py"],
            "files_to_delete": [],
        }
        result = {
            "restored_count": 1,
            "deleted_count": 0,
            "unchanged_count": 2,
            "errors": [],
        }
        output = _format_rollback_json(tmp_path, plan, result)
        data = json.loads(output)
        assert data["project"] == str(tmp_path)
        assert data["plan"]["restore_count"] == 1
        assert data["result"]["restored_count"] == 1

    def test_format_rollback_json_no_result(self, tmp_path: Path) -> None:
        """_format_rollback_json omits result when None."""
        from the_architect.cli import _format_rollback_json

        plan = {
            "restore_count": 0,
            "delete_count": 0,
            "unchanged_count": 0,
            "files_to_restore": [],
            "files_to_delete": [],
        }
        output = _format_rollback_json(tmp_path, plan, None)
        data = json.loads(output)
        assert "result" not in data
        assert data["project"] == str(tmp_path)

    def test_corrupted_baseline_skipped_in_cli(self, tmp_path: Path) -> None:
        """Corrupted baseline files do not crash the CLI."""
        baselines_dir = tmp_path / ".architect" / "baselines"
        baselines_dir.mkdir(parents=True)
        (baselines_dir / "T01.json").write_text("not valid json{", encoding="utf-8")

        result = CliRunner().invoke(main, ["rollback", "-p", str(tmp_path)])
        # Should not crash — no valid baselines means "No baseline data available"
        assert result.exit_code == 1
        assert "No baseline data available" in result.output

    def test_json_with_dry_run(self, tmp_path: Path) -> None:
        """--json with --dry-run returns dry-run result."""
        (tmp_path / "a.py").write_text("v1", encoding="utf-8")
        self._write_baseline(tmp_path, "T01", {"a.py": "v1"})
        (tmp_path / "new.py").write_text("new", encoding="utf-8")

        result = CliRunner().invoke(
            main, ["rollback", "-p", str(tmp_path), "--task", "T01", "--json", "--dry-run"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        # Dry-run result should show counts but no actual changes
        assert data["result"]["restored_count"] == 0
        assert data["result"]["deleted_count"] == 1
        # File should still exist (dry-run)
        assert (tmp_path / "new.py").exists()

    def test_json_empty_baselines(self, tmp_path: Path) -> None:
        """--json with no baselines exits with error (no JSON output)."""
        result = CliRunner().invoke(main, ["rollback", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 1
