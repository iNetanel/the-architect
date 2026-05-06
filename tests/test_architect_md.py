"""Tests for the_architect.core.architect_md — ARCHITECT.md management."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from the_architect.core.architect_md import (
    append_best_practice,
    append_constraint,
    append_lesson,
    append_permanent_decision,
    append_planning_history,
    create_architect_md,
    extract_structure_section,
    parse_sections,
    read_architect_md,
    update_structure_section,
    write_or_update_architect_md,
)
from the_architect.core.structure import Component, Dependency, RepoType, StructureReport

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_report(tmp_path: Path) -> StructureReport:
    """Build a minimal StructureReport for testing."""
    return StructureReport(
        repo_type=RepoType.SINGLE_REPO,
        components=[],
        dependencies=[],
        shared_resources=[],
    )


# ---------------------------------------------------------------------------
# read_architect_md
# ---------------------------------------------------------------------------


class TestReadArchitectMd:
    """Tests for read_architect_md()."""

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        """Should return None when ARCHITECT.md does not exist."""
        assert read_architect_md(tmp_path) is None

    def test_returns_content_when_present(self, tmp_path: Path) -> None:
        """Should return file content when ARCHITECT.md exists."""
        arch_md = tmp_path / "ARCHITECT.md"
        arch_md.write_text("# ARCHITECT.md\nSome content", encoding="utf-8")
        result = read_architect_md(tmp_path)
        assert result is not None
        assert "Some content" in result


# ---------------------------------------------------------------------------
# create_architect_md
# ---------------------------------------------------------------------------


class TestCreateArchitectMd:
    """Tests for create_architect_md()."""

    def test_creates_file(self, tmp_path: Path) -> None:
        """Should create ARCHITECT.md with the standard template."""
        path = create_architect_md(tmp_path, structure_section="Single repo")
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "ARCHITECT.md" in content
        assert "Project Overview" in content
        assert "Repository Map" in content
        assert "Tech Stack" in content
        assert "Shared Contracts" in content
        assert "Build, Test, and Verification" in content
        assert "Style and Code Standards" in content
        assert "Permanent Decisions" in content
        assert "Known Constraints" in content
        assert "Lessons Learned" in content
        assert "Best Practices" in content
        assert "Planning History" not in content

    def test_injects_structure_section(self, tmp_path: Path) -> None:
        """Should inject the provided structure section into the file."""
        path = create_architect_md(tmp_path, structure_section="Monorepo with 3 components")
        content = path.read_text(encoding="utf-8")
        assert "Monorepo with 3 components" in content


# ---------------------------------------------------------------------------
# parse_sections
# ---------------------------------------------------------------------------


class TestParseSections:
    """Tests for parse_sections()."""

    def test_parses_all_standard_sections(self, tmp_path: Path) -> None:
        """Should parse all standard ARCHITECT.md sections."""
        path = create_architect_md(tmp_path, structure_section="test")
        content = path.read_text(encoding="utf-8")
        sections = parse_sections(content)
        assert "Project Overview" in sections
        assert "Repository Map" in sections
        assert "Tech Stack" in sections
        assert "Permanent Decisions" in sections
        assert "Known Constraints" in sections
        assert "Lessons Learned" in sections
        assert "Best Practices" in sections
        assert "Planning History" not in sections

    def test_returns_empty_dict_for_empty_content(self) -> None:
        """Should return empty dict for empty content."""
        assert parse_sections("") == {}


# ---------------------------------------------------------------------------
# update_structure_section
# ---------------------------------------------------------------------------


class TestUpdateStructureSection:
    """Tests for update_structure_section()."""

    def test_updates_structure_in_existing_file(self, tmp_path: Path) -> None:
        """Should update the Project Structure section in an existing file."""
        create_architect_md(tmp_path, structure_section="old structure")
        update_structure_section(tmp_path, "new structure content")
        content = read_architect_md(tmp_path)
        assert content is not None
        assert "new structure content" in content

    def test_creates_file_if_missing(self, tmp_path: Path) -> None:
        """Should create ARCHITECT.md if it does not exist."""
        assert not (tmp_path / "ARCHITECT.md").exists()
        update_structure_section(tmp_path, "fresh structure")
        assert (tmp_path / "ARCHITECT.md").exists()
        content = read_architect_md(tmp_path)
        assert content is not None
        assert "fresh structure" in content


# ---------------------------------------------------------------------------
# append_permanent_decision
# ---------------------------------------------------------------------------


class TestAppendPermanentDecision:
    """Tests for append_permanent_decision()."""

    def test_appends_decision_row(self, tmp_path: Path) -> None:
        """Should append a new row to the Permanent Decisions table."""
        create_architect_md(tmp_path, structure_section="test")
        append_permanent_decision(
            tmp_path,
            decision="Use PostgreSQL",
            value="PostgreSQL 15",
            reason="team familiarity",
        )
        content = read_architect_md(tmp_path)
        assert content is not None
        assert "Use PostgreSQL" in content
        assert "PostgreSQL 15" in content
        assert "team familiarity" in content

    def test_multiple_decisions_all_present(self, tmp_path: Path) -> None:
        """Should accumulate multiple decisions."""
        create_architect_md(tmp_path, structure_section="test")
        append_permanent_decision(tmp_path, "Decision A", "Value A", "Reason A")
        append_permanent_decision(tmp_path, "Decision B", "Value B", "Reason B")
        content = read_architect_md(tmp_path) or ""
        assert "Decision A" in content
        assert "Decision B" in content


# ---------------------------------------------------------------------------
# append_constraint
# ---------------------------------------------------------------------------


class TestAppendConstraint:
    """Tests for append_constraint()."""

    def test_appends_constraint(self, tmp_path: Path) -> None:
        """Should append a constraint to the Known Constraints section."""
        create_architect_md(tmp_path, structure_section="test")
        append_constraint(tmp_path, "Never use print() — use loguru only")
        content = read_architect_md(tmp_path)
        assert content is not None
        assert "Never use print()" in content


# ---------------------------------------------------------------------------
# append_lesson
# ---------------------------------------------------------------------------


class TestAppendLesson:
    """Tests for append_lesson()."""

    def test_appends_lesson(self, tmp_path: Path) -> None:
        """Should append a lesson to the Lessons Learned section."""
        create_architect_md(tmp_path, structure_section="test")
        append_lesson(tmp_path, task_id="T01", lesson="Always run tests before marking Done")
        content = read_architect_md(tmp_path)
        assert content is not None
        assert "Always run tests" in content


# ---------------------------------------------------------------------------
# append_best_practice
# ---------------------------------------------------------------------------


class TestAppendBestPractice:
    """Tests for append_best_practice()."""

    def test_appends_best_practice(self, tmp_path: Path) -> None:
        """Should append a best practice to the Best Practices section."""
        create_architect_md(tmp_path, structure_section="test")
        append_best_practice(tmp_path, "Use type hints on all public functions")
        content = read_architect_md(tmp_path)
        assert content is not None
        assert "type hints" in content


# ---------------------------------------------------------------------------
# append_planning_history
# ---------------------------------------------------------------------------


class TestAppendPlanningHistory:
    """Tests for deprecated append_planning_history()."""

    def test_does_not_append_planning_history_row(self, tmp_path: Path) -> None:
        """ARCHITECT.md no longer stores run history."""
        create_architect_md(tmp_path, structure_section="test")
        append_planning_history(
            tmp_path,
            goal="Build a REST API",
            tasks_created=5,
            notes="First planning session",
        )
        content = read_architect_md(tmp_path)
        assert content is not None
        assert "Build a REST API" not in content
        assert "First planning session" not in content


# ---------------------------------------------------------------------------
# write_or_update_architect_md
# ---------------------------------------------------------------------------


class TestWriteOrUpdateArchitectMd:
    """Tests for write_or_update_architect_md() — the main entry point."""

    def test_creates_file_on_first_call(self, tmp_path: Path) -> None:
        """Should create ARCHITECT.md when it does not exist."""
        report = _make_report(tmp_path)
        assert not (tmp_path / "ARCHITECT.md").exists()
        write_or_update_architect_md(tmp_path, report)
        assert (tmp_path / "ARCHITECT.md").exists()

    def test_updates_structure_on_subsequent_calls(self, tmp_path: Path) -> None:
        """Should update the structure section on subsequent calls."""
        report = _make_report(tmp_path)
        write_or_update_architect_md(tmp_path, report)
        # Call again — should update without error
        write_or_update_architect_md(tmp_path, report)
        content = read_architect_md(tmp_path)
        assert content is not None
        # File should still have all standard sections
        assert "Repository Map" in content
        assert "Permanent Decisions" in content
        assert "Known Constraints" in content

    def test_preserves_existing_decisions(self, tmp_path: Path) -> None:
        """Should not wipe existing decisions when updating structure."""
        report = _make_report(tmp_path)
        write_or_update_architect_md(tmp_path, report)
        append_permanent_decision(tmp_path, "Keep this", "yes", "important")
        # Update structure again
        write_or_update_architect_md(tmp_path, report)
        content = read_architect_md(tmp_path)
        assert content is not None
        assert "Keep this" in content

    def test_enriches_durable_sections_from_structure_report(self, tmp_path: Path) -> None:
        """Detected component facts should populate durable memory sections."""
        report = StructureReport(
            repo_type=RepoType.MONOREPO,
            components=[
                Component(
                    path="frontend/",
                    language="TypeScript",
                    framework="Next.js",
                    role="Web UI",
                    description="Customer web application",
                    key_deps=["next", "react"],
                    test_command="npm test",
                    lint_command="npm run lint",
                ),
                Component(
                    path="backend/",
                    language="Python",
                    framework="FastAPI",
                    role="API server",
                    description="HTTP API",
                    key_deps=["fastapi", "pydantic"],
                    test_command="pytest tests/ -v --tb=short",
                ),
            ],
            dependencies=[
                Dependency("frontend/", "backend/", "docker-compose depends_on"),
            ],
            shared_resources=["docker-compose.yml — wires services"],
        )

        write_or_update_architect_md(tmp_path, report)

        content = read_architect_md(tmp_path) or ""
        assert "Auto-Detected Project Intelligence" in content
        assert "`frontend/` — TypeScript · Next.js · Web UI" in content
        assert "mission: Customer web application" in content
        assert "test `npm test`" in content
        assert "frontend/ → backend/" in content
        assert "docker-compose.yml" in content
        assert "_No durable tech stack notes recorded yet._" not in content

    def test_enrichment_refreshes_auto_block_without_dropping_manual_notes(
        self, tmp_path: Path
    ) -> None:
        """Auto-detected memory should refresh without duplicating or removing notes."""
        first = StructureReport(
            repo_type=RepoType.SINGLE_REPO,
            components=[Component(path="api/", language="Python", framework="Flask")],
        )
        second = StructureReport(
            repo_type=RepoType.SINGLE_REPO,
            components=[Component(path="api/", language="Python", framework="FastAPI")],
        )
        write_or_update_architect_md(tmp_path, first)
        arch_md = tmp_path / "ARCHITECT.md"
        content = arch_md.read_text(encoding="utf-8")
        content = content.replace(
            "## Tech Stack\n\n",
            "## Tech Stack\n\n- Manual note: Python services use typed models.\n\n",
            1,
        )
        arch_md.write_text(content, encoding="utf-8")

        write_or_update_architect_md(tmp_path, second)

        updated = arch_md.read_text(encoding="utf-8")
        assert "Manual note: Python services use typed models" in updated
        assert "FastAPI" in updated
        assert updated.count("### Auto-Detected Project Intelligence") == 6


# ---------------------------------------------------------------------------
# read_architect_md — OSError path
# ---------------------------------------------------------------------------


class TestReadArchitectMdOSError:
    """Tests for read_architect_md() when reading fails with OSError."""

    def test_returns_none_on_os_error(self, tmp_path: Path) -> None:
        """Should return None when the file exists but cannot be read."""
        arch_md = tmp_path / "ARCHITECT.md"
        arch_md.write_text("content", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
            result = read_architect_md(tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# extract_structure_section
# ---------------------------------------------------------------------------


class TestExtractStructureSection:
    """Tests for extract_structure_section()."""

    def test_returns_section_content(self, tmp_path: Path) -> None:
        """Should return the Project Structure section content."""
        create_architect_md(tmp_path, structure_section="My structure info")
        content = read_architect_md(tmp_path) or ""
        result = extract_structure_section(content)
        assert "My structure info" in result

    def test_returns_empty_string_when_section_missing(self) -> None:
        """Should return empty string when Project Structure section is absent."""
        content = "## Other Section\n\nSome content\n"
        result = extract_structure_section(content)
        assert result == ""


# ---------------------------------------------------------------------------
# _atomic_write failure paths
# ---------------------------------------------------------------------------


class TestAtomicWriteFailure:
    """Tests for _atomic_write failure handling (lines 208-215)."""

    def test_replace_failure_is_swallowed(self, tmp_path: Path) -> None:
        """Should not raise when os.replace fails during atomic write."""
        with patch("os.replace", side_effect=OSError("cannot replace")):
            path = create_architect_md(tmp_path, "test structure")
        # Function returns normally — exception is logged, not raised
        assert isinstance(path, Path)

    def test_replace_and_unlink_failure_is_swallowed(self, tmp_path: Path) -> None:
        """Should not raise when both os.replace and os.unlink fail."""
        with (
            patch("os.replace", side_effect=OSError("replace failed")),
            patch("os.unlink", side_effect=OSError("unlink failed")),
        ):
            path = create_architect_md(tmp_path, "test structure")
        # Function returns normally — both exceptions are caught and logged
        assert isinstance(path, Path)


# ---------------------------------------------------------------------------
# update_structure_section — OSError and malformed content paths
# ---------------------------------------------------------------------------


class TestUpdateStructureSectionErrorPaths:
    """Tests for update_structure_section() error handling."""

    def test_recreates_on_read_error(self, tmp_path: Path) -> None:
        """Should recreate ARCHITECT.md when reading the existing file fails."""
        arch_md = tmp_path / "ARCHITECT.md"
        arch_md.write_text("## Project Structure\n\nold\n", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=OSError("read error")):
            update_structure_section(tmp_path, "fresh structure")
        content = read_architect_md(tmp_path)
        assert content is not None
        assert "fresh structure" in content

    def test_recreates_on_malformed_content(self, tmp_path: Path) -> None:
        """Should recreate ARCHITECT.md when Project Structure section is missing."""
        arch_md = tmp_path / "ARCHITECT.md"
        arch_md.write_text(
            "# ARCHITECT.md\n\n## Other Section\n\nSome content\n",
            encoding="utf-8",
        )
        update_structure_section(tmp_path, "new structure content")
        content = read_architect_md(tmp_path)
        assert content is not None
        assert "new structure content" in content
        assert "Repository Map" in content


# ---------------------------------------------------------------------------
# append_* functions — file does not exist
# ---------------------------------------------------------------------------


class TestAppendFunctionsNoFile:
    """Tests for append_* functions when ARCHITECT.md does not exist."""

    def test_append_permanent_decision_no_file(self, tmp_path: Path) -> None:
        """Should return silently when ARCHITECT.md does not exist."""
        assert not (tmp_path / "ARCHITECT.md").exists()
        append_permanent_decision(tmp_path, "decision", "value", "reason")
        # Should not create the file
        assert not (tmp_path / "ARCHITECT.md").exists()

    def test_append_constraint_no_file(self, tmp_path: Path) -> None:
        """Should return silently when ARCHITECT.md does not exist."""
        append_constraint(tmp_path, "a constraint")
        assert not (tmp_path / "ARCHITECT.md").exists()

    def test_append_lesson_no_file(self, tmp_path: Path) -> None:
        """Should return silently when ARCHITECT.md does not exist."""
        append_lesson(tmp_path, "T01", "a lesson")
        assert not (tmp_path / "ARCHITECT.md").exists()

    def test_append_best_practice_no_file(self, tmp_path: Path) -> None:
        """Should return silently when ARCHITECT.md does not exist."""
        append_best_practice(tmp_path, "a practice")
        assert not (tmp_path / "ARCHITECT.md").exists()

    def test_append_planning_history_no_file(self, tmp_path: Path) -> None:
        """Should return silently when ARCHITECT.md does not exist."""
        append_planning_history(tmp_path, "goal", "T01", "notes")
        assert not (tmp_path / "ARCHITECT.md").exists()


# ---------------------------------------------------------------------------
# _append_to_section_table — branch coverage
# ---------------------------------------------------------------------------


class TestAppendToSectionTableBranches:
    """Tests for _append_to_section_table branch paths via public API."""

    def test_replaces_placeholder_row_at_section_boundary(self, tmp_path: Path) -> None:
        """Should replace placeholder table row when section ends at another heading."""
        arch_md = tmp_path / "ARCHITECT.md"
        arch_md.write_text(
            "# ARCHITECT.md\n\n"
            "## Project Structure\n\ntest\n\n"
            "---\n\n"
            "## Permanent Decisions\n\n"
            "| Decision | Value | Reason | Added |\n"
            "|----------|-------|--------|-------|\n"
            "| | | | |\n\n"
            "---\n\n"
            "## Known Constraints\n\n"
            "- _No constraints recorded yet._\n",
            encoding="utf-8",
        )
        append_permanent_decision(tmp_path, "New Decision", "New Value", "New Reason")
        content = arch_md.read_text(encoding="utf-8")
        assert "New Decision" in content
        assert "New Value" in content
        # Placeholder row should have been replaced
        lines = content.splitlines()
        placeholder_found = any(line.strip() == "| | | | |" for line in lines)
        assert not placeholder_found

    def test_appends_row_when_no_table_rows_at_section_boundary(self, tmp_path: Path) -> None:
        """Should append row when section has no table rows and ends at another heading."""
        arch_md = tmp_path / "ARCHITECT.md"
        arch_md.write_text(
            "# ARCHITECT.md\n\n"
            "## Project Structure\n\ntest\n\n"
            "---\n\n"
            "## Permanent Decisions\n\n"
            "No table here\n\n"
            "---\n\n"
            "## Known Constraints\n\n"
            "- _No constraints recorded yet._\n",
            encoding="utf-8",
        )
        append_permanent_decision(tmp_path, "Decision A", "Val A", "Reason A")
        content = arch_md.read_text(encoding="utf-8")
        assert "Decision A" in content

    def test_replaces_placeholder_row_at_end_of_file(self, tmp_path: Path) -> None:
        """Should replace placeholder table row when section is at end of file."""
        arch_md = tmp_path / "ARCHITECT.md"
        arch_md.write_text(
            "# ARCHITECT.md\n\n"
            "## Permanent Decisions\n\n"
            "| Decision | Value | Reason | Added |\n"
            "|----------|-------|--------|-------|\n"
            "| | | | |\n",
            encoding="utf-8",
        )
        append_permanent_decision(tmp_path, "Decision", "Value", "Reason")
        content = arch_md.read_text(encoding="utf-8")
        assert "Decision" in content

    def test_appends_row_when_no_table_rows_at_end_of_file(self, tmp_path: Path) -> None:
        """Should append row when section has no table rows and is at end of file."""
        arch_md = tmp_path / "ARCHITECT.md"
        arch_md.write_text(
            "# ARCHITECT.md\n\n## Permanent Decisions\n",
            encoding="utf-8",
        )
        append_permanent_decision(tmp_path, "Decision", "Value", "Reason")
        content = arch_md.read_text(encoding="utf-8")
        assert "Decision" in content


class TestAppendToSectionTableOSError:
    """Tests for _append_to_section_table when reading fails."""

    def test_returns_on_os_error(self, tmp_path: Path) -> None:
        """Should return silently when reading ARCHITECT.md fails with OSError."""
        arch_md = tmp_path / "ARCHITECT.md"
        arch_md.write_text("## Permanent Decisions\n\n| D | V |\n", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=OSError("read error")):
            # Should not raise
            append_permanent_decision(tmp_path, "decision", "value", "reason")


class TestAppendToSectionTableWriteFailure:
    """Tests for _append_to_section_table when writing fails."""

    def test_handles_write_failure(self, tmp_path: Path) -> None:
        """Should not raise when _atomic_write fails inside _append_to_section_table."""
        create_architect_md(tmp_path, "test")
        with patch(
            "the_architect.core.architect_md._atomic_write",
            side_effect=RuntimeError("write error"),
        ):
            # Should not raise — the exception is caught and silently swallowed
            append_permanent_decision(tmp_path, "decision", "value", "reason")


# ---------------------------------------------------------------------------
# _append_to_section_list — branch coverage
# ---------------------------------------------------------------------------


class TestAppendToSectionListPlaceholder:
    """Tests for _append_to_section_list placeholder replacement."""

    def test_replaces_placeholder_entry(self, tmp_path: Path) -> None:
        """Should replace _No ... yet._ placeholder with the new entry."""
        arch_md = tmp_path / "ARCHITECT.md"
        arch_md.write_text(
            "# ARCHITECT.md\n\n"
            "## Known Constraints\n\n"
            "_No constraints recorded yet._\n\n"
            "## Lessons Learned\n\n"
            "Other content\n",
            encoding="utf-8",
        )
        append_constraint(tmp_path, "New constraint here")
        content = arch_md.read_text(encoding="utf-8")
        assert "New constraint here" in content
        assert "_No constraints recorded yet._" not in content


class TestAppendToSectionListEndOfFile:
    """Tests for _append_to_section_list end-of-file insertion."""

    def test_appends_at_end_of_file(self, tmp_path: Path) -> None:
        """Should append entry when section is at end of file with no placeholder."""
        arch_md = tmp_path / "ARCHITECT.md"
        arch_md.write_text(
            "# ARCHITECT.md\n\n## Best Practices\n\nSome existing practice\n",
            encoding="utf-8",
        )
        append_best_practice(tmp_path, "New practice here")
        content = arch_md.read_text(encoding="utf-8")
        assert "New practice here" in content


class TestAppendToSectionListOSError:
    """Tests for _append_to_section_list when reading fails."""

    def test_returns_on_os_error(self, tmp_path: Path) -> None:
        """Should return silently when reading ARCHITECT.md fails with OSError."""
        arch_md = tmp_path / "ARCHITECT.md"
        arch_md.write_text("## Best Practices\n\n_No best practices yet._\n", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=OSError("read error")):
            # Should not raise
            append_best_practice(tmp_path, "a practice")


class TestAppendToSectionListWriteFailure:
    """Tests for _append_to_section_list when writing fails."""

    def test_handles_write_failure(self, tmp_path: Path) -> None:
        """Should not raise when _atomic_write fails inside _append_to_section_list."""
        arch_md = tmp_path / "ARCHITECT.md"
        arch_md.write_text(
            "# ARCHITECT.md\n\n## Best Practices\n\n_No best practices recorded yet._\n",
            encoding="utf-8",
        )
        with patch(
            "the_architect.core.architect_md._atomic_write",
            side_effect=RuntimeError("write error"),
        ):
            # Should not raise — the exception is caught and silently swallowed
            append_best_practice(tmp_path, "a practice")
