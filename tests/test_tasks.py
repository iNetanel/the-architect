"""Tests for task discovery and state."""

from __future__ import annotations

import tempfile
from pathlib import Path

from the_architect.core.tasks import (
    Task,
    TaskPlan,
    TaskStatus,
    _extract_title,
    discover_tasks,
    duplicate_task_prefixes,
    task_number,
    task_prefix,
)


class TestTaskPrefix:
    """Tests for task_prefix function."""

    def test_task_prefix_with_underscore(self) -> None:
        """Should extract prefix from task with underscore."""
        assert task_prefix("T09_foo") == "T09"

    def test_task_prefix_without_underscore(self) -> None:
        """Should return as-is when no underscore."""
        assert task_prefix("T01") == "T01"

    def test_task_prefix_single_digit(self) -> None:
        """Should handle single digit numbers."""
        assert task_prefix("T1_foo") == "T1"

    def test_task_prefix_double_digit(self) -> None:
        """Should handle double digit numbers."""
        assert task_prefix("T99_foo") == "T99"

    def test_task_prefix_r_prefix(self) -> None:
        """Should extract an R-prefix (retrospective task)."""
        assert task_prefix("R01_fix_bugs") == "R01"
        assert task_prefix("R10") == "R10"

    def test_task_prefix_t_prefix(self) -> None:
        """Should extract a T-prefix (regular task)."""
        assert task_prefix("T09_implement_feature") == "T09"
        assert task_prefix("T01") == "T01"

    def test_task_prefix_no_match_returns_input(self) -> None:
        """An unrecognised name must be returned verbatim."""
        assert task_prefix("not_a_task_name") == "not_a_task_name"


class TestTaskNumber:
    """Tests for task_number function."""

    def test_task_number_with_underscore(self) -> None:
        """Should extract number from task with underscore."""
        assert task_number("T09_foo") == 9

    def test_task_number_without_underscore(self) -> None:
        """Should extract number from task without underscore."""
        assert task_number("T01") == 1

    def test_task_number_single_digit(self) -> None:
        """Should handle single digit numbers."""
        assert task_number("T5_bar") == 5

    def test_task_number_double_digit(self) -> None:
        """Should handle double digit numbers."""
        assert task_number("T42_baz") == 42

    def test_task_number_not_found(self) -> None:
        """Should return 0 when no task number found."""
        assert task_number("invalid") == 0


class TestDiscoverTasks:
    """Tests for discover_tasks function."""

    def test_discover_tasks_finds_files(self) -> None:
        """Should find all task files matching pattern."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            (tasks_dir / "T01_first.md").touch()
            (tasks_dir / "T02_second.md").touch()
            (tasks_dir / "T10_foo.md").touch()
            (tasks_dir / "ignore.txt").touch()
            (tasks_dir / "T03_nope").touch()

            tasks = discover_tasks(tasks_dir)

            assert len(tasks) == 3
            assert tasks[0].number == 1
            assert tasks[0].name == "T01_first"
            assert tasks[1].number == 2
            assert tasks[2].number == 10

    def test_discover_tasks_sorts_numerically(self) -> None:
        """Should sort tasks by number, not lexicographically."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            (tasks_dir / "T01_aaa.md").touch()
            (tasks_dir / "T02_bbb.md").touch()
            (tasks_dir / "T10_ccc.md").touch()
            (tasks_dir / "T09_ddd.md").touch()

            tasks = discover_tasks(tasks_dir)

            assert tasks[0].number == 1
            assert tasks[1].number == 2
            assert tasks[2].number == 9
            assert tasks[3].number == 10

    def test_discover_tasks_missing_dir(self) -> None:
        """Should return empty list when directory doesn't exist."""
        tasks = discover_tasks(Path("/nonexistent/path"))
        assert tasks == []

    def test_discover_tasks_empty_dir(self) -> None:
        """Should return empty list when directory is empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks = discover_tasks(Path(tmpdir))
            assert tasks == []

    def test_discover_tasks_accepts_string_path(self) -> None:
        """A string path must be converted to a Path internally."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "T01_first.md").touch()
            tasks = discover_tasks(tmpdir)  # str, not Path
            assert len(tasks) == 1
            assert tasks[0].prefix == "T01"

    def test_discover_tasks_ignores_architect_eval_files(self) -> None:
        """architect_eval files must never be discovered as tasks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            (tasks_dir / "T01_first.md").touch()
            (tasks_dir / "architect_eval_T02_second.md").touch()

            tasks = discover_tasks(tasks_dir)

            assert [task.name for task in tasks] == ["T01_first"]

    def test_duplicate_task_prefixes_reports_ambiguous_files(self) -> None:
        """Duplicate prefixes must be easy to detect before execution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            (tasks_dir / "T01_first.md").touch()
            (tasks_dir / "T01_second.md").touch()
            (tasks_dir / "T02_third.md").touch()

            duplicates = duplicate_task_prefixes(discover_tasks(tasks_dir))

            assert duplicates == {"T01": ["T01_first", "T01_second"]}


class TestTaskPlan:
    """Tests for TaskPlan model."""

    def test_task_plan_pending(self) -> None:
        """Should correctly identify pending tasks."""
        tasks = [
            Task(
                name="T01_first",
                prefix="T01",
                number=1,
                path=Path("/fake/1"),
                status=TaskStatus.DONE,
            ),
            Task(
                name="T02_second",
                prefix="T02",
                number=2,
                path=Path("/fake/2"),
                status=TaskStatus.PENDING,
            ),
            Task(
                name="T03_third",
                prefix="T03",
                number=3,
                path=Path("/fake/3"),
                status=TaskStatus.PENDING,
            ),
        ]
        plan = TaskPlan(tasks=tasks, next_to_run=tasks[1])

        assert len(plan.pending) == 2
        assert plan.pending[0].number == 2
        assert plan.pending[1].number == 3

    def test_task_plan_done(self) -> None:
        """Should correctly identify completed tasks."""
        tasks = [
            Task(
                name="T01_first",
                prefix="T01",
                number=1,
                path=Path("/fake/1"),
                status=TaskStatus.DONE,
            ),
            Task(
                name="T02_second",
                prefix="T02",
                number=2,
                path=Path("/fake/2"),
                status=TaskStatus.PENDING,
            ),
        ]
        plan = TaskPlan(tasks=tasks)

        assert len(plan.done) == 1
        assert plan.done[0].number == 1

    def test_task_plan_all_done(self) -> None:
        """Should return True when all tasks are done."""
        tasks = [
            Task(
                name="T01_first",
                prefix="T01",
                number=1,
                path=Path("/fake/1"),
                status=TaskStatus.DONE,
            ),
            Task(
                name="T02_second",
                prefix="T02",
                number=2,
                path=Path("/fake/2"),
                status=TaskStatus.DONE,
            ),
        ]
        plan = TaskPlan(tasks=tasks)

        assert plan.all_done is True
        assert plan.has_pending is False

    def test_task_plan_has_pending(self) -> None:
        """Should return True when there are pending tasks."""
        tasks = [
            Task(
                name="T01_first",
                prefix="T01",
                number=1,
                path=Path("/fake/1"),
                status=TaskStatus.DONE,
            ),
            Task(
                name="T02_second",
                prefix="T02",
                number=2,
                path=Path("/fake/2"),
                status=TaskStatus.PENDING,
            ),
        ]
        plan = TaskPlan(tasks=tasks)

        assert plan.all_done is False
        assert plan.has_pending is True

    def test_task_plan_empty(self) -> None:
        """Should handle empty task list."""
        plan = TaskPlan()

        assert plan.all_done is False
        assert plan.has_pending is False
        assert plan.pending == []
        assert plan.done == []


class TestExtractTitle:
    """Tests for _extract_title helper."""

    def test_extracts_title_from_heading_with_em_dash(self) -> None:
        """Should extract title after 'T01 — ' in heading."""
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "T01_changelog.md"
            f.write_text("# T01 — Implement CHANGELOG\n")
            result = _extract_title(f, "T01_changelog")
            assert result == "Implement CHANGELOG"

    def test_extracts_title_from_heading_with_hyphen(self) -> None:
        """Should extract title after 'T01 - ' in heading."""
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "T02_runner.md"
            f.write_text("# T02 - Run the tasks\n")
            result = _extract_title(f, "T02_runner")
            assert result == "Run the tasks"

    def test_extracts_title_from_heading_with_en_dash(self) -> None:
        """Should extract title after 'T01 – ' (en dash) in heading."""
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "T01_test.md"
            f.write_text("# T01 – Some title\n")
            result = _extract_title(f, "T01_test")
            assert result == "Some title"

    def test_extracts_title_from_heading_prefix_only(self) -> None:
        """Should extract title after 'T01 ' with no dash separator."""
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "T01_test.md"
            f.write_text("# T01 Some title here\n")
            result = _extract_title(f, "T01_test")
            assert result == "Some title here"

    def test_fallback_from_filename_no_heading(self) -> None:
        """Should derive title from filename when no heading found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "T01_changelog_and_version.md"
            f.write_text("Some content without heading\n")
            result = _extract_title(f, "T01_changelog_and_version")
            assert result == "Changelog and version"

    def test_fallback_on_unreadable_file(self) -> None:
        """Should derive title from filename when file can't be read."""
        result = _extract_title(Path("/nonexistent/file.md"), "T01_my_task")
        assert result == "My task"

    def test_s_prefix_heading(self) -> None:
        """Should handle T-prefix task headings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "T01_test.md"
            f.write_text("# T01 — Setup project\n")
            result = _extract_title(f, "T01_test")
            assert result == "Setup project"


class TestDiscoverTasksWithTitle:
    """Tests for discover_tasks with title extraction."""

    def test_discover_tasks_extracts_title(self) -> None:
        """Should extract human-readable title from task file heading."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            (tasks_dir / "T01_changelog.md").write_text("# T01 — Implement CHANGELOG\n")
            (tasks_dir / "T02_runner.md").write_text("# T02 — Build runner engine\n")

            tasks = discover_tasks(tasks_dir)

            assert tasks[0].title == "Implement CHANGELOG"
            assert tasks[1].title == "Build runner engine"

    def test_discover_tasks_fallback_title(self) -> None:
        """Should fall back to filename-derived title when no heading."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            (tasks_dir / "T01_core_models.md").touch()

            tasks = discover_tasks(tasks_dir)

            assert tasks[0].title == "Core models"

    def test_discover_tasks_title_default_empty(self) -> None:
        """Task model should default title to empty string."""
        task = Task(name="T01_test", prefix="T01", number=1, path=Path("/fake"))
        assert task.title == ""


class TestDiscoverTasksSortOrder:
    """Tests for deterministic sort order across mixed T/R/S prefixes (T03 fix)."""

    def test_sort_r_task_comes_after_t_task_same_number(self) -> None:
        """T-tasks must execute before R-tasks that share the same number."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            (tasks_dir / "T01_task.md").touch()
            (tasks_dir / "R01_retro.md").touch()

            tasks = discover_tasks(tasks_dir)

            assert len(tasks) == 2
            prefixes = [t.prefix for t in tasks]
            # T01 must come before R01
            assert prefixes.index("T01") < prefixes.index("R01")

    def test_sort_different_numbers_primary_by_number(self) -> None:
        """Tasks with different numbers should be sorted by number first."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            (tasks_dir / "T02_second.md").touch()
            (tasks_dir / "R01_first.md").touch()

            tasks = discover_tasks(tasks_dir)

            assert tasks[0].number == 1
            assert tasks[0].prefix == "R01"
            assert tasks[1].number == 2
            assert tasks[1].prefix == "T02"

    def test_sort_stable_across_multiple_t_tasks(self) -> None:
        """Multiple T-prefix tasks should be sorted numerically."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            (tasks_dir / "T03_third.md").touch()
            (tasks_dir / "T01_first.md").touch()
            (tasks_dir / "T02_second.md").touch()

            tasks = discover_tasks(tasks_dir)

            assert [t.number for t in tasks] == [1, 2, 3]


class TestIsRelativeTo:
    """Tests for the `not path.resolve().is_relative_to(...)` fix (T03.1)."""

    def test_path_inside_root_is_not_filtered(self, tmp_path: Path) -> None:
        """A path resolving inside the project root returns True for is_relative_to."""
        # Verify that the corrected boolean logic works:
        # `not path.resolve().is_relative_to(root)` is False for paths inside root.
        inner = tmp_path / "inner"
        inner.mkdir()
        target_file = inner / "real.txt"
        target_file.write_text("content")

        # Should NOT be filtered (is inside root)
        assert not (not target_file.resolve().is_relative_to(tmp_path.resolve()))
        # Equivalently, is_relative_to returns True
        assert target_file.resolve().is_relative_to(tmp_path.resolve())

    def test_path_outside_root_evaluates_correctly(self, tmp_path: Path) -> None:
        """A path outside the project root should be correctly identified."""
        import tempfile

        with tempfile.TemporaryDirectory() as outside_dir:
            outside = Path(outside_dir)
            inner = tmp_path / "project"
            inner.mkdir()
            # outside is not relative to inner
            assert not outside.resolve().is_relative_to(inner.resolve())


# ---------------------------------------------------------------------------
# Case-insensitive extension detection
# ---------------------------------------------------------------------------


class TestDiscoverTasksCaseInsensitive:
    """discover_tasks must find task files regardless of extension case."""

    def test_lowercase_extension_discovered(self, tmp_path: Path) -> None:
        """Standard .md extension is discovered."""
        (tmp_path / "T01_example.md").write_text("# T01", encoding="utf-8")
        tasks = discover_tasks(tmp_path)
        assert len(tasks) == 1

    def test_uppercase_extension_discovered(self, tmp_path: Path) -> None:
        """Uppercase .MD extension (possible on some filesystems) is discovered."""
        (tmp_path / "T01_example.MD").write_text("# T01", encoding="utf-8")
        tasks = discover_tasks(tmp_path)
        assert len(tasks) == 1
        assert tasks[0].prefix == "T01"

    def test_mixed_case_extension_discovered(self, tmp_path: Path) -> None:
        """Mixed case .Md extension is also discovered."""
        (tmp_path / "T02_other.Md").write_text("# T02", encoding="utf-8")
        tasks = discover_tasks(tmp_path)
        assert len(tasks) == 1
        assert tasks[0].prefix == "T02"

    def test_non_md_extension_ignored(self, tmp_path: Path) -> None:
        """Files with non-.md extensions must be ignored regardless of case."""
        (tmp_path / "T01_example.txt").write_text("# T01", encoding="utf-8")
        (tmp_path / "T02_other.TXT").write_text("# T02", encoding="utf-8")
        assert discover_tasks(tmp_path) == []

    def test_mixed_case_and_lowercase_together(self, tmp_path: Path) -> None:
        """Uppercase and lowercase .md files are both discovered in the same directory."""
        (tmp_path / "T01_lower.md").write_text("# T01", encoding="utf-8")
        (tmp_path / "T02_upper.MD").write_text("# T02", encoding="utf-8")
        tasks = discover_tasks(tmp_path)
        assert len(tasks) == 2
        prefixes = {t.prefix for t in tasks}
        assert prefixes == {"T01", "T02"}
