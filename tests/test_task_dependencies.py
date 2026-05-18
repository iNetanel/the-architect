"""Tests for the task dependency system.

Covers the Task model's depends_on field, the dependency parser,
cycle detection, missing dependency detection, runner integration,
and terminal status constants.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from the_architect.core.progress import TERMINAL_STATUSES
from the_architect.core.runner import _check_task_dependencies
from the_architect.core.tasks import (
    Task,
    _extract_dependencies,
    detect_dependency_cycles,
    detect_missing_dependencies,
)

# ---------------------------------------------------------------------------
# T03.1 — Model and parser tests
# ---------------------------------------------------------------------------


class TestTaskDependsOnField:
    """Tests for the Task model's depends_on field."""

    def test_task_depends_on_default_empty(self) -> None:
        """Task created without depends_on has an empty list."""
        task = Task(
            name="T01_example",
            prefix="T01",
            number=1,
            path=Path("/tmp/T01_example.md"),
        )
        assert task.depends_on == []

    def test_task_depends_on_populated(self) -> None:
        """Task created with depends_on preserves the provided values."""
        task = Task(
            name="T03_example",
            prefix="T03",
            number=3,
            path=Path("/tmp/T03_example.md"),
            depends_on=["T01", "T02"],
        )
        assert task.depends_on == ["T01", "T02"]

    def test_task_depends_on_frozen(self) -> None:
        """Task is frozen so depends_on cannot be reassigned after creation."""
        task = Task(
            name="T01_example",
            prefix="T01",
            number=1,
            path=Path("/tmp/T01_example.md"),
            depends_on=["T01"],
        )
        with pytest.raises(Exception):  # pydantic FrozenInstanceError
            task.depends_on = ["T02"]


class TestExtractDependencies:
    """Tests for the _extract_dependencies() parser."""

    def test_extract_dependencies_valid_section(self, tmp_path: Path) -> None:
        """File with a valid ## Dependencies section returns the list."""
        file_path = tmp_path / "T03_example.md"
        file_path.write_text(
            "# T03 — Example\n\n## Dependencies\n- T01\n- T02\n",
            encoding="utf-8",
        )
        result = _extract_dependencies(file_path)
        assert result == ["T01", "T02"]

    def test_extract_dependencies_no_section(self, tmp_path: Path) -> None:
        """File without a ## Dependencies section returns empty list."""
        file_path = tmp_path / "T01_example.md"
        file_path.write_text("# T01 — Example\n\nSome content.\n", encoding="utf-8")
        result = _extract_dependencies(file_path)
        assert result == []

    def test_extract_dependencies_empty_section(self, tmp_path: Path) -> None:
        """## Dependencies followed immediately by another heading returns empty."""
        file_path = tmp_path / "T02_example.md"
        file_path.write_text(
            "# T02 — Example\n\n## Dependencies\n## Other Section\n- T01\n",
            encoding="utf-8",
        )
        result = _extract_dependencies(file_path)
        assert result == []

    def test_extract_dependencies_malformed_lines(self, tmp_path: Path) -> None:
        """Lines without a leading dash or with indentation are ignored."""
        file_path = tmp_path / "T03_example.md"
        file_path.write_text(
            "# T03 — Example\n\n## Dependencies\nT01\n  T02\n- T03\n",
            encoding="utf-8",
        )
        result = _extract_dependencies(file_path)
        assert result == ["T03"]

    def test_extract_dependencies_retro_prefix(self, tmp_path: Path) -> None:
        """Retro prefixes like T04R1 are parsed correctly."""
        file_path = tmp_path / "T05_example.md"
        file_path.write_text(
            "# T05 — Example\n\n## Dependencies\n- T04R1\n",
            encoding="utf-8",
        )
        result = _extract_dependencies(file_path)
        assert result == ["T04R1"]

    def test_extract_dependencies_split_prefix(self, tmp_path: Path) -> None:
        """Split prefixes like T01A are parsed correctly."""
        file_path = tmp_path / "T02_example.md"
        file_path.write_text(
            "# T02 — Example\n\n## Dependencies\n- T01A\n",
            encoding="utf-8",
        )
        result = _extract_dependencies(file_path)
        assert result == ["T01A"]

    def test_extract_dependencies_mixed_content(self, tmp_path: Path) -> None:
        """Valid deps mixed with comments and blank lines are parsed correctly."""
        file_path = tmp_path / "T04_example.md"
        file_path.write_text(
            "# T04 — Example\n\n"
            "## Dependencies\n"
            "- T01\n"
            "\n"
            "Some explanatory comment\n"
            "- T02\n"
            "\n"
            "Another comment\n",
            encoding="utf-8",
        )
        result = _extract_dependencies(file_path)
        assert result == ["T01", "T02"]

    def test_extract_dependencies_file_not_readable(self, tmp_path: Path) -> None:
        """OSError on file read returns empty list without raising."""
        file_path = tmp_path / "T99_example.md"
        # Create the file first so it exists, then make it unreadable
        file_path.write_text("content", encoding="utf-8")
        file_path.chmod(0o000)
        try:
            result = _extract_dependencies(file_path)
            assert result == []
        finally:
            file_path.chmod(0o644)

    def test_extract_dependencies_section_ends_at_next_heading(self, tmp_path: Path) -> None:
        """Dependency parsing stops at the next ## heading."""
        file_path = tmp_path / "T03_example.md"
        file_path.write_text(
            "# T03 — Example\n\n## Dependencies\n- T01\n## Description\n- T02\n",
            encoding="utf-8",
        )
        result = _extract_dependencies(file_path)
        assert result == ["T01"]

    def test_extract_dependencies_case_insensitive_header(self, tmp_path: Path) -> None:
        """## dependencies (lowercase) is recognized as a valid header."""
        file_path = tmp_path / "T02_example.md"
        file_path.write_text(
            "# T02 — Example\n\n## dependencies\n- T01\n",
            encoding="utf-8",
        )
        result = _extract_dependencies(file_path)
        assert result == ["T01"]


# ---------------------------------------------------------------------------
# T03.2 — Cycle detection tests
# ---------------------------------------------------------------------------


class TestDetectDependencyCycles:
    """Tests for the detect_dependency_cycles() function."""

    def test_no_cycles_empty(self) -> None:
        """Empty task list returns no cycles."""
        assert detect_dependency_cycles([]) == []

    def test_no_cycles_no_deps(self) -> None:
        """Tasks with no depends_on return no cycles."""
        tasks = [
            Task(
                name="T01_a",
                prefix="T01",
                number=1,
                path=Path("/x/T01_a.md"),
            ),
            Task(
                name="T02_b",
                prefix="T02",
                number=2,
                path=Path("/x/T02_b.md"),
            ),
        ]
        assert detect_dependency_cycles(tasks) == []

    def test_no_cycles_linear_chain(self) -> None:
        """Linear chain T01 -> T02 -> T03 has no cycles."""
        tasks = [
            Task(
                name="T01_a",
                prefix="T01",
                number=1,
                path=Path("/x/T01_a.md"),
            ),
            Task(
                name="T02_b",
                prefix="T02",
                number=2,
                path=Path("/x/T02_b.md"),
                depends_on=["T01"],
            ),
            Task(
                name="T03_c",
                prefix="T03",
                number=3,
                path=Path("/x/T03_c.md"),
                depends_on=["T02"],
            ),
        ]
        assert detect_dependency_cycles(tasks) == []

    def test_simple_cycle(self) -> None:
        """T01 depends on T02 and T02 depends on T01 forms a cycle."""
        tasks = [
            Task(
                name="T01_a",
                prefix="T01",
                number=1,
                path=Path("/x/T01_a.md"),
                depends_on=["T02"],
            ),
            Task(
                name="T02_b",
                prefix="T02",
                number=2,
                path=Path("/x/T02_b.md"),
                depends_on=["T01"],
            ),
        ]
        cycles = detect_dependency_cycles(tasks)
        assert len(cycles) == 1
        assert set(cycles[0]) == {"T01", "T02"}

    def test_complex_cycle(self) -> None:
        """T01 -> T02 -> T03 -> T01 forms a three-node cycle."""
        tasks = [
            Task(
                name="T01_a",
                prefix="T01",
                number=1,
                path=Path("/x/T01_a.md"),
                depends_on=["T03"],
            ),
            Task(
                name="T02_b",
                prefix="T02",
                number=2,
                path=Path("/x/T02_b.md"),
                depends_on=["T01"],
            ),
            Task(
                name="T03_c",
                prefix="T03",
                number=3,
                path=Path("/x/T03_c.md"),
                depends_on=["T02"],
            ),
        ]
        cycles = detect_dependency_cycles(tasks)
        assert len(cycles) == 1
        assert set(cycles[0]) == {"T01", "T02", "T03"}

    def test_multiple_independent_cycles(self) -> None:
        """Two separate cycles are detected independently."""
        tasks = [
            Task(
                name="T01_a",
                prefix="T01",
                number=1,
                path=Path("/x/T01_a.md"),
                depends_on=["T02"],
            ),
            Task(
                name="T02_b",
                prefix="T02",
                number=2,
                path=Path("/x/T02_b.md"),
                depends_on=["T01"],
            ),
            Task(
                name="T03_c",
                prefix="T03",
                number=3,
                path=Path("/x/T03_c.md"),
                depends_on=["T04"],
            ),
            Task(
                name="T04_d",
                prefix="T04",
                number=4,
                path=Path("/x/T04_d.md"),
                depends_on=["T03"],
            ),
        ]
        cycles = detect_dependency_cycles(tasks)
        assert len(cycles) == 2
        cycle_sets = [set(c) for c in cycles]
        assert {"T01", "T02"} in cycle_sets
        assert {"T03", "T04"} in cycle_sets

    def test_self_dependency(self) -> None:
        """A task depending on itself is a cycle."""
        tasks = [
            Task(
                name="T01_a",
                prefix="T01",
                number=1,
                path=Path("/x/T01_a.md"),
                depends_on=["T01"],
            ),
        ]
        cycles = detect_dependency_cycles(tasks)
        assert len(cycles) == 1
        assert cycles[0] == ["T01"]

    def test_no_cycle_with_diamond(self) -> None:
        """Diamond dependency pattern has no cycle."""
        tasks = [
            Task(
                name="T01_a",
                prefix="T01",
                number=1,
                path=Path("/x/T01_a.md"),
            ),
            Task(
                name="T02_b",
                prefix="T02",
                number=2,
                path=Path("/x/T02_b.md"),
            ),
            Task(
                name="T03_c",
                prefix="T03",
                number=3,
                path=Path("/x/T03_c.md"),
                depends_on=["T01", "T02"],
            ),
        ]
        assert detect_dependency_cycles(tasks) == []


# ---------------------------------------------------------------------------
# T03.3 — Missing dependency tests
# ---------------------------------------------------------------------------


class TestDetectMissingDependencies:
    """Tests for the detect_missing_dependencies() function."""

    def test_no_missing_empty(self) -> None:
        """Empty task list returns empty dict."""
        assert detect_missing_dependencies([]) == {}

    def test_no_missing_no_deps(self) -> None:
        """Tasks with no deps return empty dict."""
        tasks = [
            Task(
                name="T01_a",
                prefix="T01",
                number=1,
                path=Path("/x/T01_a.md"),
            ),
        ]
        assert detect_missing_dependencies(tasks) == {}

    def test_no_missing_all_present(self) -> None:
        """All dependencies exist — returns empty dict."""
        tasks = [
            Task(
                name="T01_a",
                prefix="T01",
                number=1,
                path=Path("/x/T01_a.md"),
            ),
            Task(
                name="T02_b",
                prefix="T02",
                number=2,
                path=Path("/x/T02_b.md"),
                depends_on=["T01"],
            ),
        ]
        assert detect_missing_dependencies(tasks) == {}

    def test_some_missing(self) -> None:
        """T02 depends on T99 which doesn't exist."""
        tasks = [
            Task(
                name="T02_b",
                prefix="T02",
                number=2,
                path=Path("/x/T02_b.md"),
                depends_on=["T99"],
            ),
        ]
        result = detect_missing_dependencies(tasks)
        assert result == {"T02": ["T99"]}

    def test_all_missing(self) -> None:
        """T01 depends on two non-existent tasks."""
        tasks = [
            Task(
                name="T01_a",
                prefix="T01",
                number=1,
                path=Path("/x/T01_a.md"),
                depends_on=["T99", "T98"],
            ),
        ]
        result = detect_missing_dependencies(tasks)
        assert result == {"T01": ["T99", "T98"]}

    def test_multiple_tasks_missing(self) -> None:
        """Two tasks each with different missing deps."""
        tasks = [
            Task(
                name="T02_b",
                prefix="T02",
                number=2,
                path=Path("/x/T02_b.md"),
                depends_on=["T99"],
            ),
            Task(
                name="T03_c",
                prefix="T03",
                number=3,
                path=Path("/x/T03_c.md"),
                depends_on=["T88", "T77"],
            ),
        ]
        result = detect_missing_dependencies(tasks)
        assert result == {"T02": ["T99"], "T03": ["T88", "T77"]}


# ---------------------------------------------------------------------------
# T03.4 — Runner integration tests
# ---------------------------------------------------------------------------


class TestCheckTaskDependencies:
    """Tests for the runner's _check_task_dependencies() function."""

    def test_no_deps_always_satisfied(self) -> None:
        """Task with empty depends_on is always satisfied."""
        task = Task(
            name="T01_a",
            prefix="T01",
            number=1,
            path=Path("/x/T01_a.md"),
        )
        satisfied, reason = _check_task_dependencies(task, set())
        assert satisfied is True
        assert reason == ""

    def test_deps_satisfied(self) -> None:
        """All deps in completed_prefixes means satisfied."""
        task = Task(
            name="T03_c",
            prefix="T03",
            number=3,
            path=Path("/x/T03_c.md"),
            depends_on=["T01"],
        )
        satisfied, reason = _check_task_dependencies(task, {"T01"})
        assert satisfied is True
        assert reason == ""

    def test_deps_unmet_single(self) -> None:
        """Single unmet dependency returns False with the prefix."""
        task = Task(
            name="T03_c",
            prefix="T03",
            number=3,
            path=Path("/x/T03_c.md"),
            depends_on=["T01"],
        )
        satisfied, reason = _check_task_dependencies(task, set())
        assert satisfied is False
        assert reason == "T01"

    def test_deps_unmet_multiple(self) -> None:
        """Multiple unmet deps returned as comma-separated sorted string."""
        task = Task(
            name="T05_e",
            prefix="T05",
            number=5,
            path=Path("/x/T05_e.md"),
            depends_on=["T01", "T02"],
        )
        satisfied, reason = _check_task_dependencies(task, set())
        assert satisfied is False
        assert reason == "T01, T02"

    def test_deps_partially_met(self) -> None:
        """Only unmet deps appear in the skip reason."""
        task = Task(
            name="T05_e",
            prefix="T05",
            number=5,
            path=Path("/x/T05_e.md"),
            depends_on=["T01", "T02"],
        )
        satisfied, reason = _check_task_dependencies(task, {"T01"})
        assert satisfied is False
        assert reason == "T02"

    def test_unmet_deps_sorted(self) -> None:
        """Unmet deps are returned sorted alphabetically."""
        task = Task(
            name="T10_j",
            prefix="T10",
            number=10,
            path=Path("/x/T10_j.md"),
            depends_on=["T03", "T01", "T02"],
        )
        satisfied, reason = _check_task_dependencies(task, set())
        assert satisfied is False
        assert reason == "T01, T02, T03"


class TestTerminalStatuses:
    """Tests for the TERMINAL_STATUSES constant."""

    def test_skipped_is_terminal(self) -> None:
        """Skipped is included in TERMINAL_STATUSES."""
        assert "Skipped" in TERMINAL_STATUSES

    def test_all_expected_terminal_statuses(self) -> None:
        """TERMINAL_STATUSES contains Done, Failed, Blocked, Skipped."""
        expected = {"Done", "Failed", "Blocked", "Skipped"}
        assert set(TERMINAL_STATUSES) == expected
