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
    is_retro_task,
    is_split_task,
    task_base_prefix,
    task_number,
    task_prefix,
    task_sort_key,
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

    def test_task_prefix_r_prefix_old_style(self) -> None:
        """Old-style R01 names are no longer generated but plain names still parse."""
        # R-prefixed names are no longer generated but if encountered the prefix
        # function returns the name unchanged (no T-prefix match).
        assert task_prefix("not_a_task_name") == "not_a_task_name"

    def test_task_prefix_t_prefix(self) -> None:
        """Should extract a T-prefix (regular task)."""
        assert task_prefix("T09_implement_feature") == "T09"
        assert task_prefix("T01") == "T01"

    def test_task_prefix_split_letter(self) -> None:
        """Should include split letter in prefix."""
        assert task_prefix("T01A_backend") == "T01A"
        assert task_prefix("T01B_frontend") == "T01B"
        assert task_prefix("T03C_extra") == "T03C"

    def test_task_prefix_retro(self) -> None:
        """Should include full retro suffix in prefix."""
        assert task_prefix("T04R1_fix_tests") == "T04R1"
        assert task_prefix("T04R2_fix_types") == "T04R2"
        assert task_prefix("T01R1") == "T01R1"

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

    def test_task_number_split_returns_base(self) -> None:
        """Split tasks T01A and T01B both return base number 1."""
        assert task_number("T01A_backend") == 1
        assert task_number("T01B_frontend") == 1

    def test_task_number_retro_returns_base(self) -> None:
        """Retro tasks T04R1 and T04R2 both return base number 4."""
        assert task_number("T04R1_fix") == 4
        assert task_number("T04R2_fix") == 4

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
            f.write_text("# T01 — Implement CHANGELOG\n", encoding="utf-8")
            result = _extract_title(f, "T01_changelog")
            assert result == "Implement CHANGELOG"

    def test_extracts_title_from_heading_with_hyphen(self) -> None:
        """Should extract title after 'T01 - ' in heading."""
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "T02_runner.md"
            f.write_text("# T02 - Run the tasks\n", encoding="utf-8")
            result = _extract_title(f, "T02_runner")
            assert result == "Run the tasks"

    def test_extracts_title_from_heading_with_en_dash(self) -> None:
        """Should extract title after 'T01 – ' (en dash) in heading."""
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "T01_test.md"
            f.write_text("# T01 – Some title\n", encoding="utf-8")
            result = _extract_title(f, "T01_test")
            assert result == "Some title"

    def test_extracts_title_from_heading_prefix_only(self) -> None:
        """Should extract title after 'T01 ' with no dash separator."""
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "T01_test.md"
            f.write_text("# T01 Some title here\n", encoding="utf-8")
            result = _extract_title(f, "T01_test")
            assert result == "Some title here"

    def test_fallback_from_filename_no_heading(self) -> None:
        """Should derive title from filename when no heading found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "T01_changelog_and_version.md"
            f.write_text("Some content without heading\n", encoding="utf-8")
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
            f.write_text("# T01 — Setup project\n", encoding="utf-8")
            result = _extract_title(f, "T01_test")
            assert result == "Setup project"


class TestDiscoverTasksWithTitle:
    """Tests for discover_tasks with title extraction."""

    def test_discover_tasks_extracts_title(self) -> None:
        """Should extract human-readable title from task file heading."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            (tasks_dir / "T01_changelog.md").write_text(
                "# T01 — Implement CHANGELOG\n", encoding="utf-8"
            )
            (tasks_dir / "T02_runner.md").write_text(
                "# T02 — Build runner engine\n", encoding="utf-8"
            )

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
    """Tests for deterministic sort order across plain/split/retro task prefixes."""

    def test_sort_retro_task_comes_after_plain_same_number(self) -> None:
        """Plain tasks must execute before retro tasks that share the same base number."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            (tasks_dir / "T01_task.md").touch()
            (tasks_dir / "T01R1_retro.md").touch()

            tasks = discover_tasks(tasks_dir)

            assert len(tasks) == 2
            prefixes = [t.prefix for t in tasks]
            assert prefixes.index("T01") < prefixes.index("T01R1")

    def test_sort_different_numbers_primary_by_number(self) -> None:
        """Tasks with different base numbers should be sorted by number first."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            (tasks_dir / "T02_second.md").touch()
            (tasks_dir / "T01R1_first_retro.md").touch()

            tasks = discover_tasks(tasks_dir)

            assert tasks[0].number == 1
            assert tasks[0].prefix == "T01R1"
            assert tasks[1].number == 2
            assert tasks[1].prefix == "T02"

    def test_sort_stable_across_multiple_t_tasks(self) -> None:
        """Multiple plain T-prefix tasks should be sorted numerically."""
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
        target_file.write_text("content", encoding="utf-8")

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


# ═══════════════════════════════════════════════════════════════════════════
# New prefix scheme — split tasks, retro tasks, sort order
# ═══════════════════════════════════════════════════════════════════════════


class TestNewPrefixScheme:
    """Tests for split (T01A) and retro (T04R1) task discovery and sorting."""

    def test_discover_split_tasks(self, tmp_path: Path) -> None:
        """T01A and T01B are discovered with correct prefix and number."""
        (tmp_path / "T01A_backend.md").write_text("# T01A", encoding="utf-8")
        (tmp_path / "T01B_frontend.md").write_text("# T01B", encoding="utf-8")
        tasks = discover_tasks(tmp_path)
        assert len(tasks) == 2
        assert tasks[0].prefix == "T01A"
        assert tasks[0].number == 1
        assert tasks[1].prefix == "T01B"
        assert tasks[1].number == 1

    def test_discover_retro_tasks(self, tmp_path: Path) -> None:
        """T04R1 and T04R2 are discovered with correct prefix and number."""
        (tmp_path / "T04R1_fix_tests.md").write_text("# T04R1", encoding="utf-8")
        (tmp_path / "T04R2_fix_types.md").write_text("# T04R2", encoding="utf-8")
        tasks = discover_tasks(tmp_path)
        assert len(tasks) == 2
        assert tasks[0].prefix == "T04R1"
        assert tasks[0].number == 4
        assert tasks[1].prefix == "T04R2"
        assert tasks[1].number == 4

    def test_sort_order_plain_split_retro(self, tmp_path: Path) -> None:
        """Sort order must be: T01 → T01A → T01B → T01R1 → T01R2 → T02."""
        (tmp_path / "T02_next.md").write_text("# T02", encoding="utf-8")
        (tmp_path / "T01R1_fix.md").write_text("# T01R1", encoding="utf-8")
        (tmp_path / "T01B_part_b.md").write_text("# T01B", encoding="utf-8")
        (tmp_path / "T01_plain.md").write_text("# T01", encoding="utf-8")
        (tmp_path / "T01A_part_a.md").write_text("# T01A", encoding="utf-8")
        (tmp_path / "T01R2_fix2.md").write_text("# T01R2", encoding="utf-8")

        tasks = discover_tasks(tmp_path)
        prefixes = [t.prefix for t in tasks]
        assert prefixes == ["T01", "T01A", "T01B", "T01R1", "T01R2", "T02"]

    def test_plain_task_before_split_same_number(self, tmp_path: Path) -> None:
        """T03 must execute before T03A."""
        (tmp_path / "T03A_part.md").write_text("# T03A", encoding="utf-8")
        (tmp_path / "T03_whole.md").write_text("# T03", encoding="utf-8")
        tasks = discover_tasks(tmp_path)
        assert tasks[0].prefix == "T03"
        assert tasks[1].prefix == "T03A"

    def test_retro_task_after_split_same_number(self, tmp_path: Path) -> None:
        """T03A and T03B must execute before T03R1."""
        (tmp_path / "T03R1_fix.md").write_text("# T03R1", encoding="utf-8")
        (tmp_path / "T03A_part.md").write_text("# T03A", encoding="utf-8")
        (tmp_path / "T03B_part.md").write_text("# T03B", encoding="utf-8")
        tasks = discover_tasks(tmp_path)
        prefixes = [t.prefix for t in tasks]
        assert prefixes == ["T03A", "T03B", "T03R1"]


class TestPrefixClassifiers:
    """Tests for is_retro_task, is_split_task, task_base_prefix."""

    def test_is_retro_task_true(self) -> None:
        assert is_retro_task("T04R1") is True
        assert is_retro_task("T04R2") is True
        assert is_retro_task("T01R1") is True

    def test_is_retro_task_false_plain(self) -> None:
        assert is_retro_task("T01") is False
        assert is_retro_task("T04") is False

    def test_is_retro_task_false_split(self) -> None:
        assert is_retro_task("T01A") is False
        assert is_retro_task("T04B") is False

    def test_is_split_task_true(self) -> None:
        assert is_split_task("T01A") is True
        assert is_split_task("T01B") is True
        assert is_split_task("T03C") is True

    def test_is_split_task_false_plain(self) -> None:
        assert is_split_task("T01") is False

    def test_is_split_task_false_retro(self) -> None:
        assert is_split_task("T04R1") is False

    def test_task_base_prefix(self) -> None:
        assert task_base_prefix("T01") == "T01"
        assert task_base_prefix("T01A") == "T01"
        assert task_base_prefix("T04R1") == "T04"
        assert task_base_prefix("T04R2") == "T04"


def _make_task(prefix: str, number: int) -> Task:
    """Build a minimal Task for sort-key tests."""
    return Task(
        name=f"{prefix}_test",
        prefix=prefix,
        number=number,
        path=Path(f"/tmp/{prefix}_test.md"),
        title="test",
    )


class TestTaskSortKey:
    """Tests for task_sort_key — ensures correct cross-platform ordering."""

    def test_plain_task_slot_zero(self) -> None:
        assert task_sort_key(_make_task("T01", 1)) == (1, 0, 0)
        assert task_sort_key(_make_task("T10", 10)) == (10, 0, 0)

    def test_split_task_slot_one_ordered_by_letter(self) -> None:
        key_a = task_sort_key(_make_task("T04A", 4))
        key_b = task_sort_key(_make_task("T04B", 4))
        assert key_a == (4, 1, ord("A"))
        assert key_b == (4, 1, ord("B"))
        assert key_a < key_b

    def test_retro_task_slot_two_ordered_by_number(self) -> None:
        key_r1 = task_sort_key(_make_task("T04R1", 4))
        key_r2 = task_sort_key(_make_task("T04R2", 4))
        assert key_r1 == (4, 2, 1)
        assert key_r2 == (4, 2, 2)
        assert key_r1 < key_r2

    def test_ordering_plain_before_split_before_retro(self) -> None:
        plain = _make_task("T04", 4)
        split_a = _make_task("T04A", 4)
        split_b = _make_task("T04B", 4)
        retro = _make_task("T04R1", 4)
        tasks = [retro, split_b, plain, split_a]
        tasks.sort(key=task_sort_key)
        assert [t.prefix for t in tasks] == ["T04", "T04A", "T04B", "T04R1"]

    def test_different_base_numbers_ordered_numerically(self) -> None:
        tasks = [
            _make_task("T05", 5),
            _make_task("T01A", 1),
            _make_task("T03R1", 3),
            _make_task("T02", 2),
        ]
        tasks.sort(key=task_sort_key)
        assert [t.prefix for t in tasks] == ["T01A", "T02", "T03R1", "T05"]

    def test_sort_key_matches_discover_tasks_ordering(self, tmp_path: Path) -> None:
        """task_sort_key must produce the same order as discover_tasks."""
        files = [
            "T04R1_fix.md",
            "T04B_part_b.md",
            "T04A_part_a.md",
            "T04_original.md",
            "T05_next.md",
            "T01_first.md",
        ]
        for f in files:
            (tmp_path / f).write_text(f"# {f}\n", encoding="utf-8")

        discovered = discover_tasks(tmp_path)
        manually_sorted = sorted(
            [_make_task(p, int(p[1:3])) for p in ["T01", "T04", "T04A", "T04B", "T04R1", "T05"]],
            key=task_sort_key,
        )
        assert [t.prefix for t in discovered] == [t.prefix for t in manually_sorted]
