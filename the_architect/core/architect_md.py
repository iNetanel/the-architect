"""ARCHITECT.md — persistent project intelligence file management.

ARCHITECT.md is The Architect's long-term memory for a specific project.
It accumulates knowledge across all planning sessions and execution cycles.

The file lives at ``<project>/ARCHITECT.md`` while run-state tracking lives in
``<project>/tasks/PROGRESS.md``.

Ownership rules:
    - The Architect **tool** owns the structure section — rewritten fresh
      on every planning session.
    - All other sections are **append-only** — new entries are added but
      existing entries are never removed by The Architect.
    - The user can manually edit or remove entries at any time.

Section layout:
    - Project Overview (durable product/repo purpose)
    - Repository Map (managed by tool)
    - Tech Stack
    - Architecture
    - Key Flows
    - Shared Contracts
    - Code Locations
    - Build, Test, and Verification
    - Style and Code Standards
    - Agent and AI Conventions
    - Data and Storage
    - Environment and Secrets
    - Operational Constraints
    - Permanent Decisions (append-only)
    - Known Constraints (append-only)
    - Lessons Learned (append-only)
    - Best Practices (append-only)

Writes are atomic: temp file then rename, so readers never see partial content.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from the_architect.core.fileutil import atomic_write_text
from the_architect.core.structure import Component, StructureReport, format_structure_report

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ARCHITECT_MD_FILE = Path("ARCHITECT.md")

# Section markers used for parsing
_STRUCTURE_START = "## Repository Map"
_STRUCTURE_END = "## Tech Stack"
_DECISIONS_START = "## Permanent Decisions"
_CONSTRAINTS_START = "## Known Constraints"
_LESSONS_START = "## Lessons Learned"
_BEST_PRACTICES_START = "## Best Practices"
_AUTO_INTELLIGENCE_HEADING = "### Auto-Detected Project Intelligence"


# ---------------------------------------------------------------------------
# Template for new ARCHITECT.md
# ---------------------------------------------------------------------------

_ARCHITECT_MD_TEMPLATE = """\
# ARCHITECT.md — Project Intelligence

> This file is The Architect's persistent memory for this project.
> It is read at the start of every planning session and every task execution.
> It stores durable project intelligence only — not run history.
> Run/package history belongs in tasks/SUMMARY.md and archived task packages.
> The Repository Map section is updated automatically on each plan.
> Other sections accumulate durable knowledge over time — never add temporary
> task notes here unless they will help future unrelated work.

---

## Project Overview

> What this product/project is, who it serves, and the main capabilities it owns.

- _No project overview recorded yet._

---

## Repository Map

{{STRUCTURE_SECTION}}

---

## Tech Stack

> Languages, frameworks, package managers, runtimes, databases, storage,
> and external services by repo/component.

- _No durable tech stack notes recorded yet._

---

## Architecture

> Major systems, ownership boundaries, and how components connect.

- _No architecture notes recorded yet._

---

## Key Flows

> Important runtime flows such as auth, lifecycle transitions, streaming,
> agents, scheduling, persistence, and deployment.

- _No key flows recorded yet._

---

## Shared Contracts

> Stable API shapes, schemas, events, config keys, stage names, agent names,
> and cross-component contracts.

- _No shared contracts recorded yet._

---

## Code Locations

> Where important systems live so agents can start focused exploration quickly.

- _No code locations recorded yet._

---

## Build, Test, and Verification

> Commands and verification expectations by repo/component.

- _No verification commands recorded yet._

---

## Style and Code Standards

> Coding style, naming, file-size guidance, class/function boundaries, logging,
> typing, testing, comments, and frontend/backend conventions.

- _No style standards recorded yet._

---

## Agent and AI Conventions

> Agent configs, prompt locations, model routing, tool metadata,
> AI communication patterns, and provider-specific conventions.

- _No agent conventions recorded yet._

---

## Data and Storage

> Databases, buckets, collections, object paths, persistence conventions,
> and data ownership boundaries.

- _No data/storage notes recorded yet._

---

## Environment and Secrets

> Environment files, required variables, secret-handling rules, local services,
> and setup constraints.

- _No environment notes recorded yet._

---

## Operational Constraints

> Ports, background services, rate limits, dangerous commands,
> deployment assumptions, and runtime limits.

- _No operational constraints recorded yet._

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

"""

_STANDARD_SECTION_DEFAULTS: dict[str, str] = {
    "Project Overview": (
        "> What this product/project is, who it serves, and the main capabilities it "
        "owns.\n\n"
        "- _No project overview recorded yet._"
    ),
    "Tech Stack": (
        "> Languages, frameworks, package managers, runtimes, databases, storage, "
        "and external services by repo/component.\n\n"
        "- _No durable tech stack notes recorded yet._"
    ),
    "Architecture": (
        "> Major systems, ownership boundaries, and how components connect.\n\n"
        "- _No architecture notes recorded yet._"
    ),
    "Key Flows": (
        "> Important runtime flows such as auth, lifecycle transitions, streaming, "
        "agents, scheduling, persistence, and deployment.\n\n"
        "- _No key flows recorded yet._"
    ),
    "Shared Contracts": (
        "> Stable API shapes, schemas, events, config keys, stage names, agent names, "
        "and cross-component contracts.\n\n"
        "- _No shared contracts recorded yet._"
    ),
    "Code Locations": (
        "> Where important systems live so agents can start focused exploration quickly.\n\n"
        "- _No code locations recorded yet._"
    ),
    "Build, Test, and Verification": (
        "> Commands and verification expectations by repo/component.\n\n"
        "- _No verification commands recorded yet._"
    ),
    "Style and Code Standards": (
        "> Coding style, naming, file-size guidance, class/function boundaries, "
        "logging, typing, testing, comments, and frontend/backend conventions.\n\n"
        "- _No style standards recorded yet._"
    ),
    "Agent and AI Conventions": (
        "> Agent configs, prompt locations, model routing, tool metadata, "
        "AI communication patterns, and provider-specific conventions.\n\n"
        "- _No agent conventions recorded yet._"
    ),
    "Data and Storage": (
        "> Databases, buckets, collections, object paths, persistence conventions, "
        "and data ownership boundaries.\n\n"
        "- _No data/storage notes recorded yet._"
    ),
    "Environment and Secrets": (
        "> Environment files, required variables, secret-handling rules, local services, "
        "and setup constraints.\n\n"
        "- _No environment notes recorded yet._"
    ),
    "Operational Constraints": (
        "> Ports, background services, rate limits, dangerous commands, "
        "deployment assumptions, and runtime limits.\n\n"
        "- _No operational constraints recorded yet._"
    ),
}


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
    """Extract the Repository Map section from ARCHITECT.md content.

    Args:
        content: Raw ARCHITECT.md content.

    Returns:
        The structure section text, or empty string if not found.
    """
    sections = parse_sections(content)
    return sections.get("Repository Map", sections.get("Project Structure", ""))


# ---------------------------------------------------------------------------
# Write operations (atomic)
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, content: str) -> None:
    """Write content to a file atomically using temp file + rename.

    Delegates to :func:`~the_architect.core.fileutil.atomic_write_text`
    which handles cross-platform rename retries.

    Args:
        path: Target file path.
        content: Content to write.
    """
    try:
        atomic_write_text(path, content, prefix=".architect_md_tmp_")
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
    """Update only the Repository Map section in ARCHITECT.md.

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

    if "Repository Map" not in sections:
        if "Project Structure" in sections:
            sections["Repository Map"] = sections.pop("Project Structure")
        else:
            # Malformed — recreate fresh
            logger.warning("ARCHITECT.md has no Repository Map section — recreating")
            create_architect_md(project_dir, structure_section)
            return

    # Replace structure section, keep everything else
    sections["Repository Map"] = structure_section
    for section_name, default_body in _STANDARD_SECTION_DEFAULTS.items():
        sections.setdefault(section_name, default_body)

    # Rebuild the file
    new_content = _rebuild_architect_md(sections, content)
    _atomic_write(path, new_content)
    logger.debug("Updated ARCHITECT.md repository map section")


def enrich_from_structure_report(project_dir: Path, report: StructureReport) -> None:
    """Promote detected repository facts into durable ARCHITECT.md sections.

    The Repository Map is comprehensive but easy to miss. This enrichment keeps
    high-value facts in the semantic sections humans and agents scan first.

    Args:
        project_dir: The project root directory.
        report: The current structure detection report.
    """
    path = project_dir / ARCHITECT_MD_FILE
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return

    sections = parse_sections(content)
    if not sections:
        return

    generated = _generated_intelligence_sections(report)
    project_facts = _detected_project_intelligence_sections(project_dir)
    for section_name, block in project_facts.items():
        if section_name in generated:
            generated[section_name] = f"{generated[section_name]}\n{block}"
        else:
            generated[section_name] = block

    for section_name, block in generated.items():
        existing = sections.get(section_name, _STANDARD_SECTION_DEFAULTS.get(section_name, ""))
        sections[section_name] = _upsert_auto_intelligence_block(existing, block)

    _atomic_write(path, _rebuild_architect_md(sections, content))


def _all_components(report: StructureReport) -> list[Component]:
    """Return all top-level and nested components in stable order."""
    result: list[Component] = []

    def visit(component: Component) -> None:
        result.append(component)
        for child in component.sub_components:
            visit(child)

    for component in report.components:
        visit(component)
    return result


def _component_label(component: Component) -> str:
    """Return a compact component label for generated memory sections."""
    details = [item for item in (component.language, component.framework, component.role) if item]
    suffix = f" — {' · '.join(details)}" if details else ""
    return f"`{component.path}`{suffix}"


def _generated_intelligence_sections(report: StructureReport) -> dict[str, str]:
    """Build deterministic project-memory blocks from a structure report."""
    components = _all_components(report)
    component_count = len(components)
    component_word = "component" if component_count == 1 else "components"

    overview = [
        f"- Project shape: {report.repo_type.value} with {component_count} detected "
        f"{component_word}.",
        "- Treat the Repository Map as the source of truth for detected paths, "
        "dependencies, and verification commands; this section is refreshed on each plan.",
    ]

    tech_stack: list[str] = []
    code_locations: list[str] = []
    verification: list[str] = []
    architecture: list[str] = []

    if components:
        architecture.append(
            "- Component authority: each component owns implementation under its path; "
            "cross-component behavior should be coordinated through explicit contracts or "
            "integration tasks."
        )
        for component in components:
            tech_bits = [item for item in (component.language, component.framework) if item]
            if component.key_deps:
                tech_bits.append("stack: " + ", ".join(component.key_deps[:8]))
            if tech_bits:
                tech_stack.append(f"- {_component_label(component)}: {'; '.join(tech_bits)}.")

            mission = component.description or component.role or "detected project component"
            code_locations.append(
                f"- `{component.path}` — mission: {mission}; authority: files and behavior "
                "inside this path unless a task states a cross-component contract."
            )

            commands = []
            if component.test_command:
                commands.append(f"test `{component.test_command}`")
            if component.lint_command:
                commands.append(f"lint `{component.lint_command}`")
            if commands:
                verification.append(f"- `{component.path}`: {'; '.join(commands)}.")

    if report.dependencies:
        architecture.append("- Detected component dependencies:")
        architecture.extend(f"  - {dependency}" for dependency in report.dependencies)
    elif components:
        architecture.append(
            "- No explicit inter-component dependencies were detected automatically."
        )

    operational: list[str] = []
    if report.shared_resources:
        architecture.append("- Shared resources detected at the project boundary:")
        architecture.extend(f"  - {resource}" for resource in report.shared_resources)
        operational.extend(
            f"- Shared resource: {resource}." for resource in report.shared_resources
        )

    if not tech_stack:
        tech_stack.append("- No language/framework signal files were detected automatically.")
    if not code_locations:
        code_locations.append("- No component directories were detected automatically.")
    if not verification:
        verification.append(
            "- No test or lint commands were detected automatically; inspect project docs, "
            "package scripts, Makefiles, and CI before marking work complete."
        )
    if not operational:
        operational.append(
            "- Stay inside the project root and follow component ownership boundaries unless "
            "the task explicitly requires integration work."
        )

    return {
        "Project Overview": "\n".join(overview),
        "Tech Stack": "\n".join(tech_stack),
        "Architecture": "\n".join(architecture),
        "Code Locations": "\n".join(code_locations),
        "Build, Test, and Verification": "\n".join(verification),
        "Operational Constraints": "\n".join(operational),
    }


def _read_toml(path: Path) -> dict[str, object]:
    """Read a TOML file with safe defaults.

    Args:
        path: TOML file path.

    Returns:
        Parsed TOML data, or an empty dict on read/parse failure.
    """
    if not path.exists():
        return {}

    try:
        import tomllib

        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _read_json(path: Path) -> dict[str, object]:
    """Read a JSON object with safe defaults."""
    if not path.exists():
        return {}

    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _as_dict(value: object) -> dict[str, object]:
    """Return value as a dict when possible."""
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    """Return value as a list when possible."""
    return value if isinstance(value, list) else []


def _script_lines_from_pyproject(project_dir: Path) -> list[str]:
    """Detect Python entry point contracts from root pyproject.toml."""
    pyproject = _read_toml(project_dir / "pyproject.toml")
    project = _as_dict(pyproject.get("project"))
    scripts = _as_dict(project.get("scripts"))
    lines = []
    for name, target in sorted(scripts.items()):
        if isinstance(target, str):
            lines.append(f"- CLI entry point `{name}` resolves to `{target}`.")
    return lines


def _verification_lines(project_dir: Path) -> list[str]:
    """Detect repo-level verification commands from common config files."""
    lines: list[str] = []
    pyproject_path = project_dir / "pyproject.toml"
    pyproject_content = ""
    if pyproject_path.exists():
        try:
            pyproject_content = pyproject_path.read_text(encoding="utf-8").lower()
        except OSError:
            pyproject_content = ""

    if pyproject_content:
        if "pytest" in pyproject_content:
            lines.append("- Python tests: `pytest tests/ -v --tb=short`.")
        if "ruff" in pyproject_content:
            lines.append("- Python lint/format: `ruff check .` and `ruff format --check .`.")
        if "mypy" in pyproject_content:
            lines.append(
                "- Python typecheck: `mypy the_architect/` when this package path exists; "
                "otherwise inspect pyproject for the typed package path."
            )

    pkg = _read_json(project_dir / "package.json")
    scripts = _as_dict(pkg.get("scripts"))
    if scripts:
        if "test" in scripts:
            lines.append("- JavaScript tests: `npm test`.")
        if "lint" in scripts:
            lines.append("- JavaScript lint: `npm run lint`.")
        if "typecheck" in scripts:
            lines.append("- JavaScript typecheck: `npm run typecheck`.")

    workflows_dir = project_dir / ".github" / "workflows"
    if workflows_dir.is_dir():
        try:
            workflows = sorted(
                p.name
                for p in workflows_dir.iterdir()
                if p.is_file() and p.suffix.lower() in {".yml", ".yaml"}
            )
        except OSError:
            workflows = []
        if workflows:
            lines.append(
                f"- CI workflows: {', '.join(f'`.github/workflows/{name}`' for name in workflows)}."
            )

    return lines


def _detected_project_intelligence_sections(project_dir: Path) -> dict[str, str]:
    """Detect durable project intelligence outside component manifests.

    This is the deterministic pre-planner memory pass. It intentionally avoids
    deep recursive source inspection so it remains safe for huge monorepos while
    still capturing high-value root contracts, docs, prompts, CI, and runtime
    storage locations.
    """
    sections: dict[str, list[str]] = {
        "Project Overview": [],
        "Tech Stack": [],
        "Key Flows": [],
        "Shared Contracts": [],
        "Code Locations": [],
        "Build, Test, and Verification": [],
        "Style and Code Standards": [],
        "Agent and AI Conventions": [],
        "Data and Storage": [],
        "Environment and Secrets": [],
        "Operational Constraints": [],
    }

    pyproject = _read_toml(project_dir / "pyproject.toml")
    project = _as_dict(pyproject.get("project"))
    if project:
        name = project.get("name")
        description = project.get("description")
        if isinstance(name, str):
            overview = f"- Root Python project: `{name}`"
            if isinstance(description, str) and description:
                overview += f" - {description}"
            overview += "."
            sections["Project Overview"].append(overview)

        build_system = _as_dict(pyproject.get("build-system"))
        backend = build_system.get("build-backend")
        if isinstance(backend, str):
            sections["Tech Stack"].append(f"- Python build backend: `{backend}`.")

        scripts = _script_lines_from_pyproject(project_dir)
        sections["Key Flows"].extend(scripts)
        sections["Shared Contracts"].extend(scripts)

    for docs_dir_name in ("documentation", "docs"):
        docs_dir = project_dir / docs_dir_name
        if docs_dir.is_dir():
            try:
                docs = sorted(
                    p.name
                    for p in docs_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in {".md", ".rst", ".txt"}
                )[:12]
            except OSError:
                docs = []
            suffix = f": {', '.join(f'`{docs_dir_name}/{name}`' for name in docs)}" if docs else ""
            sections["Code Locations"].append(
                f"- `{docs_dir_name}/` - project documentation and durable technical "
                f"references{suffix}."
            )
            sections["Agent and AI Conventions"].append(
                f"- Check `{docs_dir_name}/` for canonical project practices before broad changes."
            )

    if (project_dir / "README.md").exists():
        sections["Code Locations"].append(
            "- `README.md` - user-facing overview and CLI/reference documentation."
        )
    if (project_dir / "CHANGELOG.md").exists():
        sections["Operational Constraints"].append(
            "- `CHANGELOG.md` records user-visible changes; update it when project "
            "rules require release notes."
        )
    if (project_dir / "version.py").exists():
        sections["Operational Constraints"].append(
            "- Root `version.py` exists; inspect it for project-specific version/build "
            "rules before release or task completion work."
        )

    for rules_name in ("AGENTS.md", "CLAUDE.md"):
        if (project_dir / rules_name).exists():
            sections["Agent and AI Conventions"].append(
                f"- `{rules_name}` is a provider/user rule file; read and follow it, "
                "but do not treat it as generated project memory."
            )

    prompts_dir = project_dir / "the_architect" / "resources" / "prompts"
    if prompts_dir.is_dir():
        sections["Agent and AI Conventions"].append(
            "- `the_architect/resources/prompts/` contains packaged Architect prompts; "
            "prompt changes affect planner/reviewer/executor behavior and need extra review."
        )
        sections["Code Locations"].append(
            "- `the_architect/resources/prompts/` - packaged prompts injected into provider runs."
        )

    dev_opencode = project_dir / "dev" / "opencode"
    if dev_opencode.is_dir():
        sections["Agent and AI Conventions"].append(
            "- `dev/opencode/` contains this repo's OpenCode development config and "
            "agent prompt files."
        )

    if (project_dir / "tests").is_dir():
        sections["Code Locations"].append(
            "- `tests/` - automated test suite; mirror source module names when adding coverage."
        )

    sections["Build, Test, and Verification"].extend(_verification_lines(project_dir))

    if (project_dir / "tasks").exists():
        sections["Data and Storage"].append(
            "- `tasks/` stores Architect task packages and `tasks/PROGRESS.md` for "
            "current run state."
        )
    if (project_dir / ".architect").exists():
        sections["Data and Storage"].append(
            "- `.architect/` stores Architect runtime state such as logs, locks, "
            "circuit state, prompts, and monitor data."
        )
    else:
        sections["Data and Storage"].append(
            "- The Architect creates `.architect/` at runtime for logs, locks, "
            "circuit state, prompts, and monitor data."
        )

    if (project_dir / ".env").exists() or (project_dir / ".env.example").exists():
        sections["Environment and Secrets"].append(
            "- Environment files are present; never commit secrets and prefer documented "
            "sample values."
        )

    # Generic but durable for every project The Architect manages.
    sections["Shared Contracts"].append(
        "- `ARCHITECT.md` stores durable project intelligence; current run state belongs "
        "in `tasks/PROGRESS.md` and package history in `tasks/SUMMARY.md`."
    )
    sections["Operational Constraints"].append(
        "- Keep generated task state in `tasks/`; do not mix run history into `ARCHITECT.md`."
    )

    return {name: "\n".join(lines) for name, lines in sections.items() if lines}


def _remove_auto_intelligence_block(body: str) -> str:
    """Remove the generated intelligence block from a section body."""
    lines = body.splitlines()
    kept: list[str] = []
    skipping = False

    for line in lines:
        if line.strip() == _AUTO_INTELLIGENCE_HEADING:
            skipping = True
            continue
        if skipping and line.startswith("### "):
            skipping = False
        if not skipping:
            kept.append(line)

    return "\n".join(kept).strip()


def _upsert_auto_intelligence_block(body: str, block: str) -> str:
    """Replace the generated block while preserving human/agent notes."""
    cleaned = _remove_auto_intelligence_block(body)
    cleaned_lines = [
        line
        for line in cleaned.splitlines()
        if not (line.strip().startswith("- _No ") and line.strip().endswith("recorded yet._"))
    ]
    cleaned = "\n".join(cleaned_lines).strip()

    parts = [cleaned] if cleaned else []
    parts.extend([_AUTO_INTELLIGENCE_HEADING, "", block.strip()])
    return "\n\n".join(part for part in parts if part).strip()


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

    # The rebuilder owns section separators. Older/agent-written files may have
    # accumulated horizontal rules in the header; keeping them would duplicate
    # separators before the first section forever.
    header_lines = [line for line in header_lines if line.strip() != "---"]
    collapsed_header: list[str] = []
    previous_blank = False
    for line in header_lines:
        is_blank = not line.strip()
        if is_blank and previous_blank:
            continue
        collapsed_header.append(line)
        previous_blank = is_blank
    header_lines = collapsed_header
    while header_lines and not header_lines[-1].strip():
        header_lines.pop()

    # Known section order. Run history is intentionally excluded from
    # ARCHITECT.md; detailed run history lives in tasks/SUMMARY.md and archives.
    section_order = [
        "Project Overview",
        "Repository Map",
        "Tech Stack",
        "Architecture",
        "Key Flows",
        "Shared Contracts",
        "Code Locations",
        "Build, Test, and Verification",
        "Style and Code Standards",
        "Agent and AI Conventions",
        "Data and Storage",
        "Environment and Secrets",
        "Operational Constraints",
        "Permanent Decisions",
        "Known Constraints",
        "Lessons Learned",
        "Best Practices",
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
    """Deprecated no-op: run history now belongs in tasks/SUMMARY.md.

    Args:
        project_dir: The project root directory.
        goal: The planning goal.
        tasks_created: Description of tasks created (e.g. "T01-T09").
        notes: Optional notes about this planning session.
    """
    return


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
        section_marker: The ``## `` heading text to find (e.g. ``"## Permanent Decisions"``).
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

    enrich_from_structure_report(project_dir, report)

    return path
