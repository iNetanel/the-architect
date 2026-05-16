"""Tests for the_architect.core.baseline — workspace baseline capture and change detection."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from the_architect.core.baseline import (
    FileRecord,
    WorkspaceBaseline,
    _hash_file,
    _is_hidden,
    capture_baseline,
    detect_changes,
    read_baseline,
    write_baseline,
)

# ---------------------------------------------------------------------------
# FileRecord model
# ---------------------------------------------------------------------------


class TestFileRecord:
    """Tests for the FileRecord Pydantic model."""

    def test_creation_with_all_fields(self) -> None:
        """FileRecord should accept all three required fields."""
        rec = FileRecord(path="tasks/T01.md", sha256="abc123", size=42)
        assert rec.path == "tasks/T01.md"
        assert rec.sha256 == "abc123"
        assert rec.size == 42

    def test_model_dump_serialization(self) -> None:
        """model_dump() should produce a plain dict with all fields."""
        rec = FileRecord(path="src/main.py", sha256="deadbeef", size=100)
        dump = rec.model_dump()
        assert dump == {"path": "src/main.py", "sha256": "deadbeef", "size": 100}

    def test_model_validate_deserialization(self) -> None:
        """model_validate() should reconstruct a FileRecord from a dict."""
        data = {"path": "README.md", "sha256": "cafe", "size": 55}
        rec = FileRecord.model_validate(data)
        assert rec.path == "README.md"
        assert rec.sha256 == "cafe"
        assert rec.size == 55


# ---------------------------------------------------------------------------
# WorkspaceBaseline model
# ---------------------------------------------------------------------------


class TestWorkspaceBaseline:
    """Tests for the WorkspaceBaseline Pydantic model."""

    def test_creation_with_all_fields(self) -> None:
        """WorkspaceBaseline should accept timestamp, task_prefix, and files."""
        ts = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
        bl = WorkspaceBaseline(
            timestamp=ts,
            task_prefix="T01",
            files={"foo.py": FileRecord(path="foo.py", sha256="aa", size=1)},
        )
        assert bl.timestamp == ts
        assert bl.task_prefix == "T01"
        assert len(bl.files) == 1

    def test_default_timestamp_auto_set(self) -> None:
        """timestamp should default to a recent UTC datetime."""
        before = datetime.now(UTC)
        bl = WorkspaceBaseline()
        after = datetime.now(UTC)
        assert before <= bl.timestamp <= after

    def test_default_task_prefix_empty(self) -> None:
        """task_prefix should default to empty string."""
        bl = WorkspaceBaseline()
        assert bl.task_prefix == ""

    def test_default_files_empty(self) -> None:
        """files should default to an empty dict."""
        bl = WorkspaceBaseline()
        assert bl.files == {}

    def test_model_dump_serialization(self) -> None:
        """model_dump() should produce a dict with all fields."""
        bl = WorkspaceBaseline(
            task_prefix="T02",
            files={"a.py": FileRecord(path="a.py", sha256="bb", size=2)},
        )
        dump = bl.model_dump()
        assert dump["task_prefix"] == "T02"
        assert "a.py" in dump["files"]
        assert dump["files"]["a.py"]["sha256"] == "bb"

    def test_model_validate_deserialization(self) -> None:
        """model_validate() should reconstruct a WorkspaceBaseline from a dict."""
        ts = datetime.now(UTC)
        data = {
            "timestamp": ts.isoformat(),
            "task_prefix": "T03",
            "files": {"b.py": {"path": "b.py", "sha256": "cc", "size": 3}},
        }
        bl = WorkspaceBaseline.model_validate(data)
        assert bl.task_prefix == "T03"
        assert "b.py" in bl.files
        assert bl.files["b.py"].sha256 == "cc"


# ---------------------------------------------------------------------------
# _is_hidden helper
# ---------------------------------------------------------------------------


class TestIsHidden:
    """Tests for the _is_hidden() helper."""

    def test_visible_file(self, tmp_path: Path) -> None:
        """A normal file should not be hidden."""
        assert _is_hidden(tmp_path / "main.py", tmp_path) is False

    def test_hidden_file(self, tmp_path: Path) -> None:
        """A dot-file should be hidden."""
        assert _is_hidden(tmp_path / ".env", tmp_path) is True

    def test_file_in_hidden_dir(self, tmp_path: Path) -> None:
        """A file inside a hidden directory should be hidden."""
        assert _is_hidden(tmp_path / ".git" / "config", tmp_path) is True

    def test_nested_visible(self, tmp_path: Path) -> None:
        """A deeply nested visible file should not be hidden."""
        assert _is_hidden(tmp_path / "src" / "lib" / "util.py", tmp_path) is False

    def test_path_outside_base(self) -> None:
        """A path outside the base should be treated as hidden."""
        assert _is_hidden(Path("/etc/passwd"), Path("/tmp")) is True


# ---------------------------------------------------------------------------
# _hash_file helper
# ---------------------------------------------------------------------------


class TestHashFile:
    """Tests for the _hash_file() helper."""

    def test_text_file(self, tmp_path: Path) -> None:
        """Should return (sha256, size) for a valid UTF-8 file."""
        f = tmp_path / "hello.txt"
        content = b"hello world"
        f.write_bytes(content)
        result = _hash_file(f)
        assert result is not None
        assert result[0] == hashlib.sha256(content).hexdigest()
        assert result[1] == len(content)

    def test_binary_file_returns_none(self, tmp_path: Path) -> None:
        """Should return None for non-UTF-8 binary content."""
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x80\x81\x82\xff\xfe")
        result = _hash_file(f)
        assert result is None

    def test_empty_file(self, tmp_path: Path) -> None:
        """Should handle an empty file."""
        f = tmp_path / "empty.py"
        f.write_bytes(b"")
        result = _hash_file(f)
        assert result is not None
        assert result[1] == 0


# ---------------------------------------------------------------------------
# capture_baseline
# ---------------------------------------------------------------------------


class TestCaptureBaseline:
    """Tests for capture_baseline()."""

    def test_basic_capture(self, tmp_path: Path) -> None:
        """Should capture files in a temp directory."""
        (tmp_path / "main.py").write_text("print(1)", encoding="utf-8")
        bl = capture_baseline(tmp_path, task_prefix="T01")
        assert "main.py" in bl.files
        assert bl.task_prefix == "T01"

    def test_captures_tasks_subdirectory(self, tmp_path: Path) -> None:
        """Should capture files inside tasks/ regardless of extension."""
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        (tasks / "T01.md").write_text("# Task 1", encoding="utf-8")
        (tasks / "INSTRUCTIONS.md").write_text("Do stuff", encoding="utf-8")
        bl = capture_baseline(tmp_path)
        assert "tasks/T01.md" in bl.files
        assert "tasks/INSTRUCTIONS.md" in bl.files

    def test_captures_root_level_tracked_extensions(self, tmp_path: Path) -> None:
        """Should capture .py, .toml, .json, .md at project root."""
        (tmp_path / "app.py").write_text("x=1", encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text("[project]", encoding="utf-8")
        (tmp_path / "config.json").write_text("{}", encoding="utf-8")
        (tmp_path / "README.md").write_text("# Hi", encoding="utf-8")
        bl = capture_baseline(tmp_path)
        assert "app.py" in bl.files
        assert "pyproject.toml" in bl.files
        assert "config.json" in bl.files
        assert "README.md" in bl.files

    def test_skips_untracked_root_extensions(self, tmp_path: Path) -> None:
        """Should skip root files without tracked extensions."""
        (tmp_path / "data.csv").write_text("a,b", encoding="utf-8")
        (tmp_path / "image.png").write_bytes(b"\x89PNG")
        bl = capture_baseline(tmp_path)
        assert "data.csv" not in bl.files
        assert "image.png" not in bl.files

    def test_skips_symlinks(self, tmp_path: Path) -> None:
        """Symlinks should be skipped."""
        real = tmp_path / "real.py"
        real.write_text("x=1", encoding="utf-8")
        link = tmp_path / "link.py"
        link.symlink_to(real)
        bl = capture_baseline(tmp_path)
        assert "real.py" in bl.files
        assert "link.py" not in bl.files

    def test_skips_hidden_directories(self, tmp_path: Path) -> None:
        """Hidden directories should not be descended into."""
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "secret.py").write_text("x=1", encoding="utf-8")
        bl = capture_baseline(tmp_path)
        assert ".hidden/secret.py" not in bl.files

    def test_skips_binary_files(self, tmp_path: Path) -> None:
        """Files that fail UTF-8 decode should be silently skipped."""
        binary = tmp_path / "blob.py"
        binary.write_bytes(b"\xff\xfe\x00\x01")
        bl = capture_baseline(tmp_path)
        assert "blob.py" not in bl.files

    def test_handles_missing_tasks_directory(self, tmp_path: Path) -> None:
        """Should not raise when tasks/ does not exist."""
        (tmp_path / "app.py").write_text("x", encoding="utf-8")
        bl = capture_baseline(tmp_path)
        assert "app.py" in bl.files

    def test_paths_are_relative_to_project_dir(self, tmp_path: Path) -> None:
        """Captured paths should be relative, not absolute."""
        (tmp_path / "mod.py").write_text("y=2", encoding="utf-8")
        bl = capture_baseline(tmp_path)
        for path in bl.files:
            assert not Path(path).is_absolute()

    def test_task_prefix_recorded(self, tmp_path: Path) -> None:
        """task_prefix should be stored in the baseline."""
        (tmp_path / "x.py").write_text("1", encoding="utf-8")
        bl = capture_baseline(tmp_path, task_prefix="T05")
        assert bl.task_prefix == "T05"


# ---------------------------------------------------------------------------
# detect_changes
# ---------------------------------------------------------------------------


class TestDetectChanges:
    """Tests for detect_changes()."""

    def test_detects_created_files(self, tmp_path: Path) -> None:
        """New files not in baseline should appear in 'created'."""
        (tmp_path / "old.py").write_text("v1", encoding="utf-8")
        baseline = capture_baseline(tmp_path, task_prefix="T01")
        # Add a new file after baseline
        (tmp_path / "new.py").write_text("v2", encoding="utf-8")
        changes = detect_changes(baseline, tmp_path)
        assert "new.py" in changes["created"]

    def test_detects_modified_files(self, tmp_path: Path) -> None:
        """Files with changed sha256 should appear in 'modified'."""
        (tmp_path / "mod.py").write_text("original", encoding="utf-8")
        baseline = capture_baseline(tmp_path)
        # Modify the file
        (tmp_path / "mod.py").write_text("changed content", encoding="utf-8")
        changes = detect_changes(baseline, tmp_path)
        assert "mod.py" in changes["modified"]

    def test_detects_deleted_files(self, tmp_path: Path) -> None:
        """Files in baseline but missing on disk should appear in 'deleted'."""
        (tmp_path / "gone.py").write_text("here", encoding="utf-8")
        baseline = capture_baseline(tmp_path)
        (tmp_path / "gone.py").unlink()
        changes = detect_changes(baseline, tmp_path)
        assert "gone.py" in changes["deleted"]

    def test_no_changes_when_identical(self, tmp_path: Path) -> None:
        """No changes reported when disk state matches baseline."""
        (tmp_path / "same.py").write_text("stable", encoding="utf-8")
        baseline = capture_baseline(tmp_path)
        changes = detect_changes(baseline, tmp_path)
        assert changes["created"] == []
        assert changes["modified"] == []
        assert changes["deleted"] == []

    def test_result_keys(self, tmp_path: Path) -> None:
        """Result dict should have exactly 'created', 'modified', 'deleted'."""
        changes = detect_changes(WorkspaceBaseline(), tmp_path)
        assert set(changes.keys()) == {"created", "modified", "deleted"}

    def test_lists_are_sorted(self, tmp_path: Path) -> None:
        """All change lists should be sorted alphabetically."""
        for name in ("z.py", "a.py", "m.py"):
            (tmp_path / name).write_text(name, encoding="utf-8")
        baseline = WorkspaceBaseline()  # empty — everything is "created"
        changes = detect_changes(baseline, tmp_path)
        assert changes["created"] == sorted(changes["created"])


# ---------------------------------------------------------------------------
# write_baseline / read_baseline round-trip
# ---------------------------------------------------------------------------


class TestBaselineRoundTrip:
    """Tests for write_baseline and read_baseline round-trip."""

    def test_write_then_read_produces_equivalent(self, tmp_path: Path) -> None:
        """Reading back a written baseline should produce equivalent data."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "app.py").write_text("x=1", encoding="utf-8")
        baseline = capture_baseline(project, task_prefix="T01")

        out = tmp_path / "snapshots" / "baseline.json"
        write_baseline(baseline, out)
        loaded = read_baseline(out)

        assert loaded.task_prefix == baseline.task_prefix
        assert set(loaded.files.keys()) == set(baseline.files.keys())
        for key in baseline.files:
            assert loaded.files[key].sha256 == baseline.files[key].sha256

    def test_write_creates_parent_directories(self, tmp_path: Path) -> None:
        """write_baseline should create intermediate directories."""
        out = tmp_path / "deep" / "nested" / "baseline.json"
        bl = WorkspaceBaseline(task_prefix="T99")
        write_baseline(bl, out)
        assert out.exists()

    def test_write_handles_os_error_gracefully(self, tmp_path: Path) -> None:
        """write_baseline should not raise on OSError — logs warning instead."""
        bl = WorkspaceBaseline()
        # Make a path that cannot be written
        bad_parent = tmp_path / "readonly"
        bad_parent.mkdir(mode=0o444)
        out = bad_parent / "baseline.json"
        # Should not raise
        write_baseline(bl, out)


# ---------------------------------------------------------------------------
# read_baseline error handling
# ---------------------------------------------------------------------------


class TestReadBaselineErrors:
    """Tests for read_baseline() error handling."""

    def test_file_not_found(self, tmp_path: Path) -> None:
        """Should raise FileNotFoundError when path does not exist."""
        missing = tmp_path / "nope.json"
        with pytest.raises(FileNotFoundError):
            read_baseline(missing)

    def test_invalid_json(self, tmp_path: Path) -> None:
        """Should raise ValueError when file contains invalid JSON."""
        bad = tmp_path / "corrupt.json"
        bad.write_text("{not valid json at all", encoding="utf-8")
        with pytest.raises(ValueError):
            read_baseline(bad)

    def test_corrupted_json_raises_value_error(self, tmp_path: Path) -> None:
        """Should raise ValueError for truncated JSON."""
        bad = tmp_path / "truncated.json"
        bad.write_text('{"timestamp": "2026-', encoding="utf-8")
        with pytest.raises(ValueError):
            read_baseline(bad)


# ---------------------------------------------------------------------------
# Additional edge cases
# ---------------------------------------------------------------------------


class TestCaptureEdgeCases:
    """Additional edge-case tests for capture_baseline."""

    def test_empty_project(self, tmp_path: Path) -> None:
        """Should return empty baseline for empty project directory."""
        bl = capture_baseline(tmp_path)
        assert bl.files == {}

    def test_hidden_file_at_root_skipped(self, tmp_path: Path) -> None:
        """Dot files at project root should be skipped."""
        (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
        bl = capture_baseline(tmp_path)
        assert ".env" not in bl.files

    def test_hidden_file_in_tasks_skipped(self, tmp_path: Path) -> None:
        """Dot files inside tasks/ should be skipped."""
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        (tasks / ".draft.md").write_text("draft", encoding="utf-8")
        bl = capture_baseline(tmp_path)
        assert "tasks/.draft.md" not in bl.files

    def test_pycache_skipped(self, tmp_path: Path) -> None:
        """__pycache__ directories should be pruned."""
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "mod.cpython-311.pyc").write_text("x", encoding="utf-8")
        bl = capture_baseline(tmp_path)
        assert not any("__pycache__" in p for p in bl.files)

    def test_node_modules_skipped(self, tmp_path: Path) -> None:
        """node_modules directories should be pruned."""
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "pkg.json").write_text("{}", encoding="utf-8")
        bl = capture_baseline(tmp_path)
        assert not any("node_modules" in p for p in bl.files)

    def test_architect_dir_skipped(self, tmp_path: Path) -> None:
        """.architect directories should be pruned."""
        arch = tmp_path / ".architect"
        arch.mkdir()
        (arch / "state.json").write_text("{}", encoding="utf-8")
        bl = capture_baseline(tmp_path)
        assert not any(".architect" in p for p in bl.files)


# ---------------------------------------------------------------------------
# Cross-platform path separator invariant
# ---------------------------------------------------------------------------


class TestBaselinePathSeparators:
    """Baseline file paths must always use forward slashes regardless of OS."""

    def test_capture_baseline_uses_forward_slashes(self, tmp_path: Path) -> None:
        """FileRecord paths must use forward slashes so comparisons are portable."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_example.md").write_text("# T01", encoding="utf-8")
        (tmp_path / "main.py").write_text("pass", encoding="utf-8")

        bl = capture_baseline(tmp_path)

        for rel_path in bl.files:
            assert "\\" not in rel_path, (
                f"Backslash found in baseline path {rel_path!r}; "
                "all stored paths must use forward slashes"
            )

    def test_detect_changes_uses_forward_slashes(self, tmp_path: Path) -> None:
        """detect_changes must report created paths with forward slashes."""
        bl = capture_baseline(tmp_path)
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01.md").write_text("# T01", encoding="utf-8")

        changes = detect_changes(bl, tmp_path)

        for path in changes["created"]:
            assert "\\" not in path, f"Backslash in created path {path!r}; must be forward slashes"

    def test_write_read_round_trip_uses_forward_slashes(self, tmp_path: Path) -> None:
        """Paths written to and read back from JSON must stay forward-slash."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01.md").write_text("# T01", encoding="utf-8")

        bl = capture_baseline(tmp_path)
        out = tmp_path / ".architect" / "baseline.json"
        write_baseline(bl, out)
        loaded = read_baseline(out)

        for rel_path in loaded.files:
            assert "\\" not in rel_path


# ---------------------------------------------------------------------------
# Gap closure — uncovered lines
# ---------------------------------------------------------------------------


class TestHashFileOSError:
    """Tests for _hash_file() OSError handling path."""

    def test_hash_file_os_error_returns_none(self, tmp_path: Path) -> None:
        """_hash_file should return None when reading the file raises OSError."""
        f = tmp_path / "unreadable.py"
        f.write_text("x=1", encoding="utf-8")
        from unittest.mock import patch

        with patch.object(Path, "read_bytes", side_effect=OSError("no access")):
            result = _hash_file(f)
        assert result is None


class TestCaptureBaselineTasksWalk:
    """Tests for capture_baseline() tasks/ walk edge cases."""

    def test_tasks_walk_skips_binary_files(self, tmp_path: Path) -> None:
        """Binary files in tasks/ should be silently skipped (result is None)."""
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        # Write binary content that fails UTF-8 decode
        (tasks / "binary.dat").write_bytes(b"\xff\xfe\x00\x01")
        bl = capture_baseline(tmp_path)
        assert "tasks/binary.dat" not in bl.files

    def test_tasks_walk_os_error(self, tmp_path: Path) -> None:
        """OSError during tasks/ walk should be logged, not raised."""
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        (tmp_path / "app.py").write_text("x=1", encoding="utf-8")
        from unittest.mock import patch

        with patch("os.walk", side_effect=OSError("no access")):
            bl = capture_baseline(tmp_path)
        # Should still return a baseline (empty, since both walks failed)
        assert isinstance(bl.files, dict)
        # Both walks failed, so no files captured
        assert bl.files == {}


class TestCaptureBaselineRootWalk:
    """Tests for capture_baseline() project root walk OSError."""

    def test_root_walk_os_error(self, tmp_path: Path) -> None:
        """OSError during project root walk should be logged, not raised."""
        import os as os_module

        original_walk = os_module.walk

        (tmp_path / "app.py").write_text("x=1", encoding="utf-8")
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        (tasks / "T01.md").write_text("# T01", encoding="utf-8")

        call_count = 0

        def mock_walk(path, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            # First call is tasks/ — use original walk
            if str(path) == str(tasks):
                return list(original_walk(path))
            # Second call is project root — raise OSError
            raise OSError("no access to root")

        from unittest.mock import patch

        with patch("os.walk", side_effect=mock_walk):
            bl = capture_baseline(tmp_path)
        # Should still have tasks/ files captured
        assert "tasks/T01.md" in bl.files


class TestReadBaselineErrorGapClosure:
    """Additional error paths for read_baseline()."""

    def test_read_baseline_os_error(self, tmp_path: Path) -> None:
        """read_baseline should raise OSError when file read fails."""
        f = tmp_path / "baseline.json"
        f.write_text("{}", encoding="utf-8")
        from unittest.mock import patch

        with patch.object(Path, "read_text", side_effect=OSError("no access")):
            with pytest.raises(OSError, match="Cannot read baseline file"):
                read_baseline(f)

    def test_read_baseline_validation_failure(self, tmp_path: Path) -> None:
        """read_baseline should raise ValueError when data fails model validation."""
        f = tmp_path / "baseline.json"
        # Valid JSON but missing required structure for WorkspaceBaseline
        f.write_text('{"timestamp": "not-a-date", "task_prefix": 123}', encoding="utf-8")
        with pytest.raises(ValueError, match="Baseline data failed validation"):
            read_baseline(f)
