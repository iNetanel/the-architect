"""Tests for the_architect.core.architect_md — ARCHITECT.md management."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from the_architect.core.architect_md import (
    _all_components,
    _as_dict,
    _as_list,
    _detected_project_intelligence_sections,
    _read_json,
    _read_toml,
    _remove_auto_intelligence_block,
    _script_lines_from_pyproject,
    _verification_lines,
    append_best_practice,
    append_constraint,
    append_lesson,
    append_permanent_decision,
    append_planning_history,
    create_architect_md,
    enrich_from_structure_report,
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
        assert updated.count("### Auto-Detected Project Intelligence") == 8

    def test_enrichment_adds_repo_level_docs_ci_and_agent_contracts(self, tmp_path: Path) -> None:
        """Pre-planner memory should capture repo-level facts beyond components."""
        (tmp_path / "the_architect" / "resources" / "prompts").mkdir(parents=True)
        (tmp_path / "tests").mkdir()
        (tmp_path / "documentation").mkdir()
        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        (tmp_path / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
        (tmp_path / "README.md").write_text("# Example\n", encoding="utf-8")
        (tmp_path / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
        (tmp_path / "version.py").write_text("__build__ = 1\n", encoding="utf-8")
        (tmp_path / "documentation" / "PRACTICES.md").write_text("# Practices\n", encoding="utf-8")
        (tmp_path / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(
            "\n".join(
                [
                    "[build-system]",
                    'build-backend = "hatchling.build"',
                    "[project]",
                    'name = "example"',
                    'description = "Example package"',
                    'dependencies = ["pytest", "ruff", "mypy"]',
                    "[project.scripts]",
                    'example = "example.cli:main"',
                ]
            ),
            encoding="utf-8",
        )
        report = StructureReport(
            repo_type=RepoType.SINGLE_REPO,
            components=[Component(path="./", language="Python", test_command="pytest tests/ -v")],
        )

        write_or_update_architect_md(tmp_path, report)

        content = read_architect_md(tmp_path) or ""
        assert "Root Python project: `example`" in content
        assert "Python build backend: `hatchling.build`" in content
        assert "CLI entry point `example` resolves to `example.cli:main`" in content
        assert "documentation/`" in content or "`documentation/`" in content
        assert "`.github/workflows/ci.yml`" in content
        assert "`AGENTS.md` is a provider/user rule file" in content
        assert "`the_architect/resources/prompts/`" in content
        assert "Root `version.py` exists" in content


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


# ---------------------------------------------------------------------------
# TestIoHelpers — _read_toml, _read_json, _as_dict, _as_list
# ---------------------------------------------------------------------------


class TestIoHelpers:
    """Tests for the IO helper functions in architect_md.py."""

    # --- _read_toml ---

    def test_read_toml_returns_empty_when_file_missing(self, tmp_path: Path) -> None:
        """Should return {} when the TOML file does not exist."""
        result = _read_toml(tmp_path / "nonexistent.toml")
        assert result == {}

    def test_read_toml_returns_parsed_data(self, tmp_path: Path) -> None:
        """Should parse and return dict from a valid TOML file."""
        toml_file = tmp_path / "config.toml"
        toml_file.write_text('[project]\nname = "test"\nversion = "1.0.0"\n', encoding="utf-8")
        result = _read_toml(toml_file)
        assert result == {"project": {"name": "test", "version": "1.0.0"}}

    def test_read_toml_returns_empty_on_os_error(self, tmp_path: Path) -> None:
        """Should return {} when read_text raises OSError."""
        toml_file = tmp_path / "config.toml"
        toml_file.write_text("[project]\nname = 'x'\n", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=OSError("disk error")):
            result = _read_toml(toml_file)
        assert result == {}

    def test_read_toml_returns_empty_on_decode_error(self, tmp_path: Path) -> None:
        """Should return {} when the file contains invalid TOML."""
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text("[[[[invalid toml\n", encoding="utf-8")
        result = _read_toml(toml_file)
        assert result == {}

    # --- _read_json ---

    def test_read_json_returns_empty_when_file_missing(self, tmp_path: Path) -> None:
        """Should return {} when the JSON file does not exist."""
        result = _read_json(tmp_path / "nonexistent.json")
        assert result == {}

    def test_read_json_returns_parsed_dict(self, tmp_path: Path) -> None:
        """Should parse and return dict from a valid JSON object file."""
        json_file = tmp_path / "data.json"
        json_file.write_text(json.dumps({"key": "value"}), encoding="utf-8")
        result = _read_json(json_file)
        assert result == {"key": "value"}

    def test_read_json_returns_empty_when_top_level_is_list(self, tmp_path: Path) -> None:
        """Should return {} when the JSON top-level value is a list, not a dict."""
        json_file = tmp_path / "list.json"
        json_file.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        result = _read_json(json_file)
        assert result == {}

    def test_read_json_returns_empty_on_decode_error(self, tmp_path: Path) -> None:
        """Should return {} when the file contains invalid JSON."""
        json_file = tmp_path / "bad.json"
        json_file.write_text("{not valid json}", encoding="utf-8")
        result = _read_json(json_file)
        assert result == {}

    # --- _as_dict ---

    def test_as_dict_returns_dict_unchanged(self) -> None:
        """Should return the dict as-is when given a dict."""
        data: dict[str, object] = {"a": 1, "b": "two"}
        assert _as_dict(data) == {"a": 1, "b": "two"}

    def test_as_dict_returns_empty_for_non_dict(self) -> None:
        """Should return {} when given a non-dict value."""
        assert _as_dict("string") == {}
        assert _as_dict(42) == {}
        assert _as_dict([1, 2]) == {}
        assert _as_dict(None) == {}

    # --- _as_list ---

    def test_as_list_returns_list_unchanged(self) -> None:
        """Should return the list as-is when given a list."""
        data = [1, "two", 3.0]
        assert _as_list(data) == [1, "two", 3.0]

    def test_as_list_returns_empty_for_non_list(self) -> None:
        """Should return [] when given a non-list value."""
        assert _as_list({"a": 1}) == []
        assert _as_list("string") == []
        assert _as_list(42) == []
        assert _as_list(None) == []


# ---------------------------------------------------------------------------
# TestScriptAndVerificationHelpers — _script_lines_from_pyproject, _verification_lines
# ---------------------------------------------------------------------------


class TestScriptAndVerificationHelpers:
    """Tests for _script_lines_from_pyproject() and _verification_lines()."""

    # --- _script_lines_from_pyproject ---

    def test_script_lines_empty_when_no_pyproject(self, tmp_path: Path) -> None:
        """Should return [] when no pyproject.toml exists."""
        result = _script_lines_from_pyproject(tmp_path)
        assert result == []

    def test_script_lines_empty_when_no_scripts_section(self, tmp_path: Path) -> None:
        """Should return [] when pyproject.toml has no [project.scripts] section."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "mypkg"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        result = _script_lines_from_pyproject(tmp_path)
        assert result == []

    def test_script_lines_extracts_entry_points(self, tmp_path: Path) -> None:
        """Should return a line for each string entry in [project.scripts]."""
        (tmp_path / "pyproject.toml").write_text(
            '[project.scripts]\nmy-cli = "my_pkg.cli:main"\n', encoding="utf-8"
        )
        result = _script_lines_from_pyproject(tmp_path)
        assert any("my-cli" in line and "my_pkg.cli:main" in line for line in result)

    def test_script_lines_skips_non_string_values(self, tmp_path: Path) -> None:
        """Should skip entries whose target value is not a string."""
        # TOML doesn't allow raw integers as values in [project.scripts] in practice,
        # but _script_lines_from_pyproject guards isinstance(target, str). We test via
        # _read_toml by writing a valid TOML with a boolean (non-string) value.
        (tmp_path / "pyproject.toml").write_text(
            '[project.scripts]\nbad = true\ngood = "pkg.cli:main"\n', encoding="utf-8"
        )
        result = _script_lines_from_pyproject(tmp_path)
        assert not any("bad" in line for line in result)
        assert any("good" in line for line in result)

    # --- _verification_lines ---

    def test_verification_lines_detects_pytest(self, tmp_path: Path) -> None:
        """Should include pytest command when pyproject.toml mentions pytest."""
        (tmp_path / "pyproject.toml").write_text(
            '[tool.pytest]\naddopts = "-v"\n', encoding="utf-8"
        )
        result = _verification_lines(tmp_path)
        assert any("pytest tests/ -v --tb=short" in line for line in result)

    def test_verification_lines_detects_ruff(self, tmp_path: Path) -> None:
        """Should include ruff command when pyproject.toml mentions ruff."""
        (tmp_path / "pyproject.toml").write_text(
            "[tool.ruff]\nline-length = 100\n", encoding="utf-8"
        )
        result = _verification_lines(tmp_path)
        assert any("ruff check ." in line for line in result)

    def test_verification_lines_detects_mypy(self, tmp_path: Path) -> None:
        """Should include mypy command when pyproject.toml mentions mypy."""
        (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n", encoding="utf-8")
        result = _verification_lines(tmp_path)
        assert any("mypy" in line for line in result)

    def test_verification_lines_empty_when_no_pyproject(self, tmp_path: Path) -> None:
        """Should not include Python lines when no pyproject.toml is present."""
        result = _verification_lines(tmp_path)
        assert not any("pytest" in line or "ruff" in line or "mypy" in line for line in result)

    def test_verification_lines_detects_npm_test_and_lint(self, tmp_path: Path) -> None:
        """Should include npm test and lint lines when package.json has those scripts."""
        pkg = {"scripts": {"test": "jest", "lint": "eslint ."}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        result = _verification_lines(tmp_path)
        assert any("npm test" in line for line in result)
        assert any("npm run lint" in line for line in result)

    def test_verification_lines_detects_npm_typecheck(self, tmp_path: Path) -> None:
        """Should include npm typecheck line when package.json has a typecheck script."""
        pkg = {"scripts": {"typecheck": "tsc --noEmit"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        result = _verification_lines(tmp_path)
        assert any("npm run typecheck" in line for line in result)

    def test_verification_lines_detects_ci_workflows(self, tmp_path: Path) -> None:
        """Should include CI workflow file names when .github/workflows/ contains yml files."""
        workflows_dir = tmp_path / ".github" / "workflows"
        workflows_dir.mkdir(parents=True)
        (workflows_dir / "ci.yml").write_text("name: CI\n", encoding="utf-8")
        result = _verification_lines(tmp_path)
        assert any("ci.yml" in line for line in result)

    def test_verification_lines_handles_oserror_on_pyproject_read(self, tmp_path: Path) -> None:
        """Should not raise and should omit Python lines when reading pyproject.toml fails."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.pytest]\n", encoding="utf-8")

        original_read_text = Path.read_text

        def selective_raise(self: Path, *args: object, **kwargs: object) -> str:
            if self.name == "pyproject.toml":
                raise OSError("permission denied")
            return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

        with patch.object(Path, "read_text", selective_raise):
            result = _verification_lines(tmp_path)

        assert not any("pytest" in line or "ruff" in line or "mypy" in line for line in result)

    def test_verification_lines_handles_oserror_on_workflows_iterdir(self, tmp_path: Path) -> None:
        """Should not raise and should skip CI lines when iterdir() fails on workflows dir."""
        workflows_dir = tmp_path / ".github" / "workflows"
        workflows_dir.mkdir(parents=True)

        with patch.object(Path, "iterdir", side_effect=OSError("permission denied")):
            result = _verification_lines(tmp_path)

        assert not any(".github/workflows" in line for line in result)


# ---------------------------------------------------------------------------
# TestDetectedProjectIntelligenceSections — _detected_project_intelligence_sections
# ---------------------------------------------------------------------------


class TestDetectedProjectIntelligenceSections:
    """Tests for _detected_project_intelligence_sections() branch coverage."""

    def test_dev_opencode_dir_adds_agent_convention(self, tmp_path: Path) -> None:
        """Should add an Agent and AI Conventions entry when dev/opencode/ exists."""
        (tmp_path / "dev" / "opencode").mkdir(parents=True)
        result = _detected_project_intelligence_sections(tmp_path)
        assert "Agent and AI Conventions" in result
        assert "dev/opencode/" in result["Agent and AI Conventions"]

    def test_tasks_dir_adds_data_storage_entry(self, tmp_path: Path) -> None:
        """Should mention tasks/ in Data and Storage when the tasks/ dir exists."""
        (tmp_path / "tasks").mkdir()
        result = _detected_project_intelligence_sections(tmp_path)
        assert "Data and Storage" in result
        assert "tasks/" in result["Data and Storage"]

    def test_architect_dir_detected_in_data_storage(self, tmp_path: Path) -> None:
        """Should record .architect/ runtime state path when .architect/ exists."""
        (tmp_path / ".architect").mkdir()
        result = _detected_project_intelligence_sections(tmp_path)
        assert "Data and Storage" in result
        assert ".architect/" in result["Data and Storage"]
        assert "stores Architect runtime state" in result["Data and Storage"]

    def test_no_architect_dir_uses_runtime_creates_note(self, tmp_path: Path) -> None:
        """Should mention 'creates .architect/ at runtime' when .architect/ is absent."""
        result = _detected_project_intelligence_sections(tmp_path)
        assert "Data and Storage" in result
        assert "creates `.architect/` at runtime" in result["Data and Storage"]

    def test_env_file_adds_environment_secrets_entry(self, tmp_path: Path) -> None:
        """Should add Environment and Secrets entry when .env exists."""
        (tmp_path / ".env").write_text("SECRET=x\n", encoding="utf-8")
        result = _detected_project_intelligence_sections(tmp_path)
        assert "Environment and Secrets" in result
        assert "never commit secrets" in result["Environment and Secrets"]

    def test_env_example_also_triggers_environment_entry(self, tmp_path: Path) -> None:
        """Should add Environment and Secrets entry when only .env.example exists."""
        (tmp_path / ".env.example").write_text("SECRET=\n", encoding="utf-8")
        result = _detected_project_intelligence_sections(tmp_path)
        assert "Environment and Secrets" in result
        assert "never commit secrets" in result["Environment and Secrets"]

    def test_version_py_adds_operational_constraint(self, tmp_path: Path) -> None:
        """Should add an Operational Constraints entry when version.py exists."""
        (tmp_path / "version.py").write_text('__version__ = "1.0"\n', encoding="utf-8")
        result = _detected_project_intelligence_sections(tmp_path)
        assert "Operational Constraints" in result
        assert "version.py" in result["Operational Constraints"]

    def test_changelog_adds_operational_constraint(self, tmp_path: Path) -> None:
        """Should mention CHANGELOG.md in Operational Constraints when it exists."""
        (tmp_path / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
        result = _detected_project_intelligence_sections(tmp_path)
        assert "Operational Constraints" in result
        assert "CHANGELOG.md" in result["Operational Constraints"]

    def test_readme_adds_code_location(self, tmp_path: Path) -> None:
        """Should add a Code Locations entry for README.md when it exists."""
        (tmp_path / "README.md").write_text("# My Project\n", encoding="utf-8")
        result = _detected_project_intelligence_sections(tmp_path)
        assert "Code Locations" in result
        assert "README.md" in result["Code Locations"]

    def test_docs_dir_adds_documentation_entries(self, tmp_path: Path) -> None:
        """Should add Code Locations and Agent and AI Conventions entries when docs/ exists."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "ARCHITECTURE.md").write_text("# Arch\n", encoding="utf-8")
        result = _detected_project_intelligence_sections(tmp_path)
        assert "Code Locations" in result
        assert "docs/" in result["Code Locations"]

    def test_documentation_dir_preferred_over_docs(self, tmp_path: Path) -> None:
        """Both documentation/ and docs/ should appear in Code Locations when both exist."""
        (tmp_path / "documentation").mkdir()
        (tmp_path / "docs").mkdir()
        result = _detected_project_intelligence_sections(tmp_path)
        assert "Code Locations" in result
        code_locs = result["Code Locations"]
        assert "documentation/" in code_locs
        assert "docs/" in code_locs

    def test_tests_dir_adds_code_location(self, tmp_path: Path) -> None:
        """Should add a Code Locations entry for tests/ when that directory exists."""
        (tmp_path / "tests").mkdir()
        result = _detected_project_intelligence_sections(tmp_path)
        assert "Code Locations" in result
        assert "tests/" in result["Code Locations"]

    def test_empty_project_returns_shared_contracts_and_operational(self, tmp_path: Path) -> None:
        """Should always return Shared Contracts and Operational Constraints entries."""
        result = _detected_project_intelligence_sections(tmp_path)
        assert "Shared Contracts" in result
        assert "Operational Constraints" in result


# ---------------------------------------------------------------------------
# TestEnrichFromStructureReportEdgeCases — lines 470-471, 475
# ---------------------------------------------------------------------------


class TestEnrichFromStructureReportEdgeCases:
    """Tests for enrich_from_structure_report() early-exit paths."""

    def test_enrich_returns_early_when_file_missing(self, tmp_path: Path) -> None:
        """Should return silently when ARCHITECT.md does not exist (FileNotFoundError)."""
        report = _make_report(tmp_path)
        enrich_from_structure_report(tmp_path, report)
        assert not (tmp_path / "ARCHITECT.md").exists()

    def test_enrich_returns_early_when_file_unreadable(self, tmp_path: Path) -> None:
        """Should return silently when reading ARCHITECT.md raises OSError."""
        arch_md = tmp_path / "ARCHITECT.md"
        arch_md.write_text("# ARCHITECT.md\n", encoding="utf-8")
        report = _make_report(tmp_path)
        with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
            enrich_from_structure_report(tmp_path, report)
        # File content unchanged — function returned before writing
        assert arch_md.read_text(encoding="utf-8") == "# ARCHITECT.md\n"

    def test_enrich_returns_early_when_content_is_empty(self, tmp_path: Path) -> None:
        """Should return silently when ARCHITECT.md is empty (parse_sections returns {})."""
        arch_md = tmp_path / "ARCHITECT.md"
        arch_md.write_text("", encoding="utf-8")
        report = _make_report(tmp_path)
        enrich_from_structure_report(tmp_path, report)
        assert arch_md.read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# TestAllComponentsWithSubComponents — line 499
# ---------------------------------------------------------------------------


class TestAllComponentsWithSubComponents:
    """Tests for _all_components() with nested sub_components."""

    def test_all_components_includes_sub_components(self) -> None:
        """Should return both parent and child when a component has sub_components."""
        child = Component(path="src/child/", language="Python")
        parent = Component(path="src/", language="Python", sub_components=[child])
        report = StructureReport(
            repo_type=RepoType.SINGLE_REPO,
            components=[parent],
            dependencies=[],
            shared_resources=[],
        )
        result = _all_components(report)
        assert len(result) == 2
        assert parent in result
        assert child in result
        assert result.index(parent) < result.index(child)


# ---------------------------------------------------------------------------
# TestUpdateStructureSectionLegacy — line 439
# ---------------------------------------------------------------------------


class TestUpdateStructureSectionLegacy:
    """Tests for update_structure_section() with legacy 'Project Structure' key."""

    def test_update_structure_section_handles_old_project_structure_key(
        self, tmp_path: Path
    ) -> None:
        """Should rename 'Project Structure' to 'Repository Map' and inject new content."""
        arch_md = tmp_path / "ARCHITECT.md"
        arch_md.write_text(
            "# ARCHITECT.md\n\n---\n\n## Project Structure\n\nOld content\n",
            encoding="utf-8",
        )
        update_structure_section(tmp_path, "New structure content")
        content = read_architect_md(tmp_path)
        assert content is not None
        assert "## Repository Map" in content
        assert "New structure content" in content
        assert "## Project Structure" not in content


# ---------------------------------------------------------------------------
# TestDetectedProjectIntelligenceSectionsOSError — lines 757-758
# ---------------------------------------------------------------------------


class TestDetectedProjectIntelligenceSectionsOSError:
    """Tests for _detected_project_intelligence_sections() OSError on docs_dir.iterdir()."""

    def test_docs_dir_iterdir_oserror_returns_empty_docs(self, tmp_path: Path) -> None:
        """Should not raise and should still add Code Locations entry when iterdir() fails."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        with patch.object(Path, "iterdir", side_effect=OSError("permission denied")):
            result = _detected_project_intelligence_sections(tmp_path)
        assert "Code Locations" in result
        assert "docs/" in result["Code Locations"]


# ---------------------------------------------------------------------------
# TestRemoveAutoIntelligenceBlock — line 859
# ---------------------------------------------------------------------------


class TestRemoveAutoIntelligenceBlock:
    """Tests for _remove_auto_intelligence_block() heading-after-block branch."""

    def test_remove_auto_block_stops_skipping_at_next_subsection(self) -> None:
        """Should preserve content in a ### subsection that follows the auto block."""
        body = (
            "### Auto-Detected Project Intelligence\n"
            "- item to remove\n"
            "### Other Subsection\n"
            "- kept content"
        )
        result = _remove_auto_intelligence_block(body)
        assert "Other Subsection" in result
        assert "kept content" in result
        assert "item to remove" not in result


# ---------------------------------------------------------------------------
# TestRebuildArchitectMdBlankCollapse — line 943
# ---------------------------------------------------------------------------


class TestRebuildArchitectMdBlankCollapse:
    """Tests for _rebuild_architect_md() consecutive blank-line collapse in header."""

    def test_rebuild_collapses_consecutive_blank_lines_in_header(self, tmp_path: Path) -> None:
        """Should collapse consecutive blank lines in the header area of ARCHITECT.md."""
        arch_md = tmp_path / "ARCHITECT.md"
        # Write a file whose header has two consecutive blank lines (triggers line 943)
        arch_md.write_text(
            "# ARCHITECT.md\n\n\n> Some description\n\n---\n\n## Repository Map\n\nOld structure\n",
            encoding="utf-8",
        )
        update_structure_section(tmp_path, "Refreshed structure")
        result = arch_md.read_text(encoding="utf-8")
        # Consecutive blank lines in header should be collapsed to a single blank line
        assert "\n\n\n" not in result
        assert "Refreshed structure" in result


# ---------------------------------------------------------------------------
# TestAppendToSectionTableEndOfFileRealRow — line 1162
# ---------------------------------------------------------------------------


class TestAppendToSectionTableEndOfFileRealRow:
    """Tests for end-of-file insertion after a real row."""

    def test_append_permanent_decision_at_end_of_file_with_real_row(self, tmp_path: Path) -> None:
        """Should append new row after an existing real row when section is at end of file."""
        arch_md = tmp_path / "ARCHITECT.md"
        arch_md.write_text(
            "# ARCHITECT.md\n\n"
            "## Permanent Decisions\n\n"
            "| Decision | Value | Reason | Added |\n"
            "|----------|-------|--------|-------|\n"
            "| Existing | Val | Reason | 2026-01-01 |",
            encoding="utf-8",
        )
        append_permanent_decision(tmp_path, "New Decision", "New Value", "New Reason")
        content = arch_md.read_text(encoding="utf-8")
        assert "New Decision" in content
        assert "Existing" in content
