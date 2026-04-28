"""ARCHITECT.md — persistent project intelligence file management.

ARCHITECT.md is The Architect's long-term memory for a specific project.
It accumulates knowledge across all planning sessions and execution cycles.

The file lives at ``<project>/ARCHITECT.md`` alongside PROGRESS.md.

Ownership rules:
    - The Architect **tool** owns the structure section — rewritten fresh
      on every planning session.
    - All other sections are **append-only** — new entries are added but
      existing entries are never removed by The Architect.
    - The user can manually edit or remove entries at any time.

Section layout:
    - Project Structure (managed by tool)
    - Permanent Decisions (append-only)
    - Known Constraints (append-only)
    - Lessons Learned (append-only)
    - Best Practices (append-only)
    - Planning History (append-only)

Writes are atomic: temp file then rename, so readers never see partial content.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from the_architect.core.structure import StructureReport, format_structure_report

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ARCHITECT_MD_FILE = Path("ARCHITECT.md")

# Section markers used for parsing
_STRUCTURE_START = "## Project Structure"
_STRUCTURE_END = "## Permanent Decisions"
_DECISIONS_START = "## Permanent Decisions"
_CONSTRAINTS_START = "## Known Constraints"
_LESSONS_START = "## Lessons Learned"
_BEST_PRACTICES_START = "## Best Practices"
_PLANNING_HISTORY_START = "## Planning History"


# ---------------------------------------------------------------------------
# Template for new ARCHITECT.md
# ---------------------------------------------------------------------------

_ARCHITECT_MD_TEMPLATE = """\
# ARCHITECT.md — Project Intelligence

> This file is The Architect's persistent memory for this project.
> It is read at the start of every planning session and every task execution.
> The structure section is updated automatically on each plan.
> All other sections accumulate knowledge over time — never delete entries
> unless they are factually wrong.

---

## Project Structure

{{STRUCTURE_SECTION}}

---

## Permanent Decisions

> Decisions made during planning that must not be revisited.

| Decision | Value | Reason | Added |
|----------|-------|--------|-------|

---

## Known Constraints

> Things the architect and execution agents must always respect.

- _No constraints recorded yet._

---

## Lessons Learned

> Discovered during execution. Informs future planning.

- _No lessons recorded yet._

---

## Best Practices

> Patterns that emerged from working with this codebase.

- _No best practices recorded yet._

---

## Planning History

> Summary of each planning session.

| Date | Goal | Tasks Created | Notes |
|------|------|---------------|-------|
"""


# ---------------------------------------------------------------------------
# Read / parse
# ---------------------------------------------------------------------------


def read_architect_md(project_dir: Path) -> str | None:
    """Read ARCHITECT.md content.

    Args:
        project_dir: The project root directory.

    Returns:
        File content string, or None if the file does not exist.
    """
    path = project_dir / ARCHITECT_MD_FILE
    try:
        if path.exists():
            return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(f"Failed to read ARCHITECT.md: {exc!r}")
    return None


def parse_sections(content: str) -> dict[str, str]:
    """Parse ARCHITECT.md into named sections.

    Splits on ``## `` headings and returns a dict mapping section names
    (without the ``## `` prefix) to their content.

    Args:
        content: Raw ARCHITECT.md content.

    Returns:
        Dict mapping section heading to section body text.
    """
    sections: dict[str, str] = {}
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in content.splitlines():
        if line.startswith("## "):
            # Save previous section
            if current_heading is not None:
                sections[current_heading] = "\n".join(current_lines)
            current_heading = line[3:].strip()
            current_lines = []
        elif current_heading is not None:
            current_lines.append(line)

    # Save last section
    if current_heading is not None:
        sections[current_heading] = "\n".join(current_lines)

    return sections


def extract_structure_section(content: str) -> str:
    """Extract the Project Structure section from ARCHITECT.md content.

    Args:
        content: Raw ARCHITECT.md content.

    Returns:
        The structure section text, or empty string if not found.
    """
    sections = parse_sections(content)
    return sections.get("Project Structure", "")


# ---------------------------------------------------------------------------
# Write operations (atomic)
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, content: str) -> None:
    """Write content to a file atomically using temp file + rename.

    Args:
        path: Target file path.
        content: Content to write.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=path.parent,
            prefix=".architect_md_tmp_",
            suffix=".md",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as exc:
        logger.warning(f"ARCHITECT.md atomic write failed: {exc!r}")


def create_architect_md(project_dir: Path, structure_section: str) -> Path:
    """Create a new ARCHITECT.md with the given structure section.

    Called on the first ever ``architect --plan`` in a project.

    Args:
        project_dir: The project root directory.
        structure_section: The formatted structure section content.

    Returns:
        Path to the created file.
    """
    content = _ARCHITECT_MD_TEMPLATE.replace("{{STRUCTURE_SECTION}}", structure_section)
    path = project_dir / ARCHITECT_MD_FILE
    _atomic_write(path, content)
    logger.info(f"Created ARCHITECT.md at {path}")
    return path


def update_structure_section(project_dir: Path, structure_section: str) -> None:
    """Update only the Project Structure section in ARCHITECT.md.

    Rewrites the structure section fresh while preserving all other
    sections exactly as they are.

    If ARCHITECT.md doesn't exist, creates it.
    If it's malformed (can't parse sections), recreates it fresh.

    Args:
        project_dir: The project root directory.
        structure_section: The new formatted structure section content.
    """
    path = project_dir / ARCHITECT_MD_FILE

    if not path.exists():
        create_architect_md(project_dir, structure_section)
        return

    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        create_architect_md(project_dir, structure_section)
        return

    # Parse sections
    sections = parse_sections(content)

    if "Project Structure" not in sections:
        # Malformed — recreate fresh
        logger.warning("ARCHITECT.md has no Project Structure section — recreating")
        create_architect_md(project_dir, structure_section)
        return

    # Replace structure section, keep everything else
    sections["Project Structure"] = structure_section

    # Rebuild the file
    new_content = _rebuild_architect_md(sections, content)
    _atomic_write(path, new_content)
    logger.debug("Updated ARCHITECT.md structure section")


def _clean_section_body(body: str) -> str:
    """Strip leading/trailing blank lines and stray ``---`` dividers from a section body.

    The AI agent sometimes appends ``---`` lines inside section bodies when
    updating ARCHITECT.md directly.  These cause duplicate dividers when the
    file is rebuilt (since ``_rebuild_architect_md`` adds its own ``---``
    between sections).  This function removes them so the rebuilt file is clean.

    Args:
        body: Raw section body text (everything between the ``## Heading`` line
            and the next ``## Heading`` or end of file).

    Returns:
        Cleaned section body with no leading/trailing blank lines and no
        standalone ``---`` lines.
    """
    lines = body.splitlines()
    # Remove standalone --- lines (horizontal rules added by the AI or old rebuilds)
    cleaned = [line for line in lines if line.strip() != "---"]
    # Strip leading and trailing blank lines
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    return "\n".join(cleaned)


def _rebuild_architect_md(sections: dict[str, str], original_content: str) -> str:
    """Rebuild ARCHITECT.md from parsed sections.

    Preserves the original heading order and any content that isn't
    in a recognised section (like the header block).  Strips stray ``---``
    dividers from section bodies so the rebuilt file has exactly one ``---``
    separator between each section.

    Args:
        sections: Parsed sections dict.
        original_content: Original file content (for header extraction).

    Returns:
        Rebuilt file content string.
    """
    # Extract the header (everything before the first ## heading)
    header_lines: list[str] = []
    for line in original_content.splitlines():
        if line.startswith("## "):
            break
        header_lines.append(line)

    # Strip trailing blank lines from header
    while header_lines and not header_lines[-1].strip():
        header_lines.pop()

    # Known section order
    section_order = [
        "Project Structure",
        "Permanent Decisions",
        "Known Constraints",
        "Lessons Learned",
        "Best Practices",
        "Planning History",
    ]

    parts: list[str] = []
    parts.append("\n".join(header_lines))

    for section_name in section_order:
        if section_name in sections:
            body = _clean_section_body(sections[section_name])
            parts.append("")
            parts.append("---")
            parts.append("")
            parts.append(f"## {section_name}")
            parts.append("")
            parts.append(body)

    # Ensure file ends with a single newline
    result = "\n".join(parts)
    return result.rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# Append helpers (used by planner and agents)
# ---------------------------------------------------------------------------


def append_permanent_decision(
    project_dir: Path,
    decision: str,
    value: str,
    reason: str,
) -> None:
    """Append a permanent decision to ARCHITECT.md.

    Args:
        project_dir: The project root directory.
        decision: The decision name.
        value: The decision value.
        reason: The reason for the decision.
    """
    path = project_dir / ARCHITECT_MD_FILE
    if not path.exists():
        return

    date = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    row = f"| {decision} | {value} | {reason} | {date} |"

    _append_to_section_table(path, _DECISIONS_START, row)


def append_constraint(project_dir: Path, constraint: str) -> None:
    """Append a known constraint to ARCHITECT.md.

    Args:
        project_dir: The project root directory.
        constraint: The constraint description.
    """
    path = project_dir / ARCHITECT_MD_FILE
    if not path.exists():
        return

    _append_to_section_list(path, _CONSTRAINTS_START, f"- {constraint}")


def append_lesson(
    project_dir: Path,
    task_id: str,
    lesson: str,
) -> None:
    """Append a lesson learned to ARCHITECT.md.

    Args:
        project_dir: The project root directory.
        task_id: The task prefix that produced the lesson.
        lesson: The lesson description.
    """
    path = project_dir / ARCHITECT_MD_FILE
    if not path.exists():
        return

    date = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    _append_to_section_list(path, _LESSONS_START, f"- {date} {task_id}: {lesson}")


def append_best_practice(project_dir: Path, practice: str) -> None:
    """Append a best practice to ARCHITECT.md.

    Args:
        project_dir: The project root directory.
        practice: The best practice description.
    """
    path = project_dir / ARCHITECT_MD_FILE
    if not path.exists():
        return

    _append_to_section_list(path, _BEST_PRACTICES_START, f"- {practice}")


def append_planning_history(
    project_dir: Path,
    goal: str,
    tasks_created: str,
    notes: str = "",
) -> None:
    """Append a row to the Planning History table.

    Args:
        project_dir: The project root directory.
        goal: The planning goal.
        tasks_created: Description of tasks created (e.g. "T01-T09").
        notes: Optional notes about this planning session.
    """
    path = project_dir / ARCHITECT_MD_FILE
    if not path.exists():
        return

    date = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    row = f"| {date} | {goal} | {tasks_created} | {notes} |"

    _append_to_section_table(path, _PLANNING_HISTORY_START, row)


# ---------------------------------------------------------------------------
# Internal append helpers
# ---------------------------------------------------------------------------


def _is_placeholder_table_row(row: str) -> bool:
    """Return True if a table row is an empty placeholder (all cells blank).

    Args:
        row: A markdown table row string.

    Returns:
        True if every cell in the row is empty or whitespace-only.
    """
    # Strip outer pipes and split on |
    stripped = row.strip().strip("|")
    cells = stripped.split("|")
    return all(not cell.strip() for cell in cells)


def _append_to_section_table(path: Path, section_marker: str, row: str) -> None:
    """Append a table row after a section heading in ARCHITECT.md.

    Finds the section, then finds the last real table row (starting with ``|``),
    and appends the new row after it.  If the last row is an empty placeholder
    (all cells blank), it is replaced instead of appended.

    Stray ``---`` lines inside the section (written by the AI agent) are
    ignored when locating the last table row.

    Args:
        path: Path to ARCHITECT.md.
        section_marker: The ``## `` heading text to find (e.g. ``"## Planning History"``).
        row: The table row to insert (must start and end with ``|``).
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return

    lines = content.splitlines()
    new_lines: list[str] = []
    in_section = False
    last_table_row_idx = -1
    inserted = False

    for line in lines:
        if line.strip() == section_marker:
            in_section = True
            new_lines.append(line)
            continue

        if in_section and line.startswith("## "):
            # End of section — insert the row before this heading
            if not inserted:
                if last_table_row_idx >= 0 and _is_placeholder_table_row(
                    new_lines[last_table_row_idx]
                ):
                    new_lines[last_table_row_idx] = row
                elif last_table_row_idx >= 0:
                    new_lines.append(row)
                else:
                    new_lines.append(row)
                inserted = True
            in_section = False
            new_lines.append(line)
            continue

        if in_section and line.startswith("|"):
            last_table_row_idx = len(new_lines)

        new_lines.append(line)

    # If we're still in the section at end of file
    if in_section and not inserted:
        if last_table_row_idx >= 0 and _is_placeholder_table_row(new_lines[last_table_row_idx]):
            new_lines[last_table_row_idx] = row
        elif last_table_row_idx >= 0:
            new_lines.append(row)
        else:
            new_lines.append(row)

    try:
        _atomic_write(path, "\n".join(new_lines) + "\n")
    except Exception:
        pass


def _append_to_section_list(path: Path, section_marker: str, entry: str) -> None:
    """Append a list entry to a section in ARCHITECT.md.

    Replaces the placeholder entry (``_No ... recorded yet._``) if present,
    otherwise appends after the last list item.

    Args:
        path: Path to ARCHITECT.md.
        section_marker: The ``## `` heading text to find.
        entry: The list entry to insert.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return

    lines = content.splitlines()
    new_lines: list[str] = []
    in_section = False
    inserted = False

    for i, line in enumerate(lines):
        if line.strip() == section_marker:
            in_section = True
            new_lines.append(line)
            continue

        if in_section and line.startswith("## "):
            # End of section — insert before this heading
            if not inserted:
                new_lines.append(entry)
                inserted = True
            in_section = False
            new_lines.append(line)
            continue

        if in_section:
            # Replace placeholder
            if line.strip().startswith("_No") and line.strip().endswith("yet._"):
                new_lines.append(entry)
                inserted = True
                continue

        new_lines.append(line)

    # If we're still in the section at end of file
    if in_section and not inserted:
        new_lines.append(entry)

    try:
        _atomic_write(path, "\n".join(new_lines) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Integration with structure detection
# ---------------------------------------------------------------------------


def write_or_update_architect_md(
    project_dir: Path,
    report: StructureReport,
) -> Path:
    """Create or update ARCHITECT.md with a fresh structure section.

    This is the main entry point called from the planning flow.
    On first run, creates the file. On subsequent runs, rewrites
    only the structure section and preserves all other sections.

    Args:
        project_dir: The project root directory.
        report: The structure detection report.

    Returns:
        Path to the ARCHITECT.md file.
    """
    structure_section = format_structure_report(report)
    path = project_dir / ARCHITECT_MD_FILE

    if path.exists():
        update_structure_section(project_dir, structure_section)
    else:
        create_architect_md(project_dir, structure_section)

    return path
