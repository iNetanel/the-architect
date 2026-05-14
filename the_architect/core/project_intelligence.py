"""Structured project intelligence cache for planning.

The human-readable project memory remains ``ARCHITECT.md``.  This module writes
and validates a compact machine-readable companion at
``.architect/intelligence.json`` so the planner can reuse deterministic project
signals without re-scanning the repository on every prompt construction.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from loguru import logger

from the_architect.core.structure import Component, Dependency, StructureReport

INTELLIGENCE_JSON_FILE = Path(".architect/intelligence.json")
INTELLIGENCE_SCHEMA_VERSION = "1.0"
MAX_PROMPT_ITEMS = 12


def write_project_intelligence(project_dir: Path, report: StructureReport) -> Path:
    """Write deterministic structured project intelligence for the current scan.

    Args:
        project_dir: Project root directory.
        report: Fresh deterministic structure report.

    Returns:
        Path to ``.architect/intelligence.json``.
    """
    path = project_dir / INTELLIGENCE_JSON_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_project_intelligence(project_dir, report)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    logger.info(f"Written structured project intelligence: {path}")
    return path


def read_project_intelligence(project_dir: Path) -> dict[str, Any] | None:
    """Read and validate the structured project intelligence cache.

    Args:
        project_dir: Project root directory.

    Returns:
        Parsed intelligence payload, or ``None`` when missing or invalid.
    """
    path = project_dir / INTELLIGENCE_JSON_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if path.exists():
            logger.warning(f"Structured project intelligence is unreadable: {exc!r}")
        return None

    if not _is_valid_project_intelligence(data):
        logger.warning("Structured project intelligence is invalid; ignoring cache")
        return None
    return cast(dict[str, Any], data)


def format_project_intelligence_for_prompt(data: dict[str, Any] | None) -> str:
    """Format validated structured intelligence as a compact planner context block.

    Args:
        data: Validated structured intelligence payload.

    Returns:
        Markdown summary suitable for prompt injection, or an empty string.
    """
    if not data:
        return ""

    project = _as_dict(data.get("project"))
    commands = _as_dict(data.get("commands"))
    nodes = _as_list(data.get("nodes"))
    edges = _as_list(data.get("edges"))
    gaps = _as_list(data.get("known_gaps"))

    lines = [
        f"Project: {project.get('name') or 'unknown'}",
        f"Type: {project.get('type') or 'unknown'}",
        f"Repo shape: {project.get('repo_type') or 'unknown'}",
    ]

    command_lines = [f"- {name}: `{value}`" for name, value in sorted(commands.items()) if value]
    if command_lines:
        lines.extend(["", "Commands:", *command_lines[:MAX_PROMPT_ITEMS]])

    if nodes:
        lines.extend(["", "Key components:"])
        for node in nodes[:MAX_PROMPT_ITEMS]:
            node_data = _as_dict(node)
            name = node_data.get("name") or node_data.get("id") or "component"
            layer = node_data.get("layer") or "unknown"
            path = node_data.get("file") or "."
            purpose = node_data.get("purpose") or "detected component"
            lines.append(f"- {name} ({layer}) at `{path}` — {purpose}")

    if edges:
        lines.extend(["", "Detected relationships:"])
        for edge in edges[:MAX_PROMPT_ITEMS]:
            edge_data = _as_dict(edge)
            source = edge_data.get("from") or "unknown"
            target = edge_data.get("to") or "unknown"
            via = edge_data.get("via") or edge_data.get("kind") or "dependency"
            lines.append(f"- {source} -> {target} ({via})")

    if gaps:
        lines.extend(["", "Known intelligence gaps:"])
        lines.extend(f"- {gap}" for gap in gaps[:MAX_PROMPT_ITEMS] if gap)

    return "\n".join(lines).strip()


def build_project_intelligence(project_dir: Path, report: StructureReport) -> dict[str, Any]:
    """Build deterministic structured intelligence from repository signals.

    Args:
        project_dir: Project root directory.
        report: Fresh deterministic structure report.

    Returns:
        JSON-serialisable intelligence payload.
    """
    components = _all_components(report.components)
    nodes = [_component_to_node(component) for component in components]
    edges = [_dependency_to_edge(dependency) for dependency in report.dependencies]
    commands = _detect_commands(project_dir, components)
    project_name = _detect_project_name(project_dir)
    project_type = _detect_project_type(project_dir, components)
    domains = _detect_domains(project_name, components)
    conventions = _detect_conventions(project_dir)
    known_gaps = _known_gaps(project_type, commands, nodes, edges)

    return {
        "version": INTELLIGENCE_SCHEMA_VERSION,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "project": {
            "name": project_name,
            "type": project_type,
            "repo_type": report.repo_type.value,
            "root": ".",
        },
        "tech_stack": _tech_stack(components),
        "commands": commands,
        "nodes": nodes,
        "edges": edges,
        "flows": _flows(project_type, nodes, edges),
        "domains": domains,
        "conventions": conventions,
        "risks": [],
        "known_gaps": known_gaps,
    }


def _is_valid_project_intelligence(value: object) -> bool:
    """Return True when a parsed payload has the minimum planner contract."""
    if not isinstance(value, dict):
        return False
    if value.get("version") != INTELLIGENCE_SCHEMA_VERSION:
        return False
    project = value.get("project")
    if not isinstance(project, dict):
        return False
    required_lists = ("tech_stack", "nodes", "edges", "flows", "domains", "risks")
    if any(not isinstance(value.get(key), list) for key in required_lists):
        return False
    return isinstance(value.get("commands"), dict) and isinstance(value.get("conventions"), dict)


def _all_components(components: list[Component]) -> list[Component]:
    """Return top-level and nested components in deterministic order."""
    result: list[Component] = []

    def visit(component: Component) -> None:
        result.append(component)
        for child in component.sub_components:
            visit(child)

    for component in components:
        visit(component)
    return result


def _component_to_node(component: Component) -> dict[str, Any]:
    """Convert a detected component to a knowledge-graph node."""
    purpose = component.description or component.role or component.framework or component.language
    return {
        "id": _node_id(component.path),
        "name": _component_name(component),
        "layer": component.role or component.framework or component.language or "Project",
        "file": component.path,
        "purpose": purpose or "Detected project component",
        "exports": [],
    }


def _dependency_to_edge(dependency: Dependency) -> dict[str, str]:
    """Convert a detected dependency to a knowledge-graph edge."""
    return {
        "from": _node_id(dependency.source),
        "to": _node_id(dependency.target),
        "kind": "depends_on",
        "via": dependency.via,
    }


def _node_id(path: str) -> str:
    """Return a stable kebab-case node id for a component path."""
    cleaned = path.strip().strip("./") or "root"
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", cleaned).strip("-").lower()
    return cleaned or "root"


def _component_name(component: Component) -> str:
    """Return a display name for a component."""
    path = component.path.strip().strip("/") or "root"
    if path in {".", "./"}:
        return "Root project"
    return path.split("/")[-1] or path


def _detect_project_name(project_dir: Path) -> str:
    """Detect the project name from common manifests or the directory name."""
    pyproject = _read_toml(project_dir / "pyproject.toml")
    project = _as_dict(pyproject.get("project"))
    name = project.get("name")
    if isinstance(name, str) and name:
        return name

    package = _read_json(project_dir / "package.json")
    name = package.get("name")
    if isinstance(name, str) and name:
        return name

    return project_dir.name


def _detect_project_type(project_dir: Path, components: list[Component]) -> str:
    """Infer a broad project type from manifests, frameworks, roles, and paths."""
    role_text = " ".join(
        item.lower()
        for component in components
        for item in (component.role, component.framework, component.language, component.path)
        if item
    )
    root_files = (
        {path.name.lower() for path in project_dir.iterdir()} if project_dir.exists() else set()
    )

    if any(marker in root_files for marker in ("project.godot",)) or any(
        name.endswith((".uproject", ".unity")) for name in root_files
    ):
        return "Game"
    if "androidmanifest.xml" in role_text or any(
        name.endswith(".xcodeproj") for name in root_files
    ):
        return "Mobile app"
    if "main.tf" in root_files or any(
        path.suffix.lower() == ".tf" for path in project_dir.glob("*.tf")
    ):
        return "Infrastructure-as-Code"
    if "cli" in role_text or _has_cli_entry(project_dir):
        return "CLI tool"
    if any(term in role_text for term in ("fastapi", "django", "flask", "express", "api")):
        if any(term in role_text for term in ("react", "next.js", "vue", "svelte", "web ui")):
            return "Full-stack web"
        return "Backend service / API"
    if any(
        term in role_text for term in ("react", "next.js", "vue", "svelte", "angular", "web ui")
    ):
        return "Frontend / SPA"
    if any(term in role_text for term in ("torch", "tensorflow", "sklearn", "notebooks")):
        return "ML / AI project"
    if any(term in role_text for term in ("library", "sdk", "package")):
        return "Library / SDK / Package"
    if components:
        return "Mixed / Other"
    return "Unknown"


def _has_cli_entry(project_dir: Path) -> bool:
    """Return True when common manifests expose CLI entry points."""
    pyproject = _read_toml(project_dir / "pyproject.toml")
    project = _as_dict(pyproject.get("project"))
    if _as_dict(project.get("scripts")):
        return True
    package = _read_json(project_dir / "package.json")
    return bool(package.get("bin"))


def _detect_commands(project_dir: Path, components: list[Component]) -> dict[str, str]:
    """Detect test, lint, build, and dev commands from components and manifests."""
    commands = {"test": "", "lint": "", "build": "", "dev": ""}

    for component in components:
        if not commands["test"] and component.test_command:
            commands["test"] = _prefix_command(component.path, component.test_command)
        if not commands["lint"] and component.lint_command:
            commands["lint"] = _prefix_command(component.path, component.lint_command)

    package = _read_json(project_dir / "package.json")
    scripts = _as_dict(package.get("scripts"))
    for key, script_name in (
        ("test", "test"),
        ("lint", "lint"),
        ("build", "build"),
        ("dev", "dev"),
    ):
        if not commands[key] and isinstance(scripts.get(script_name), str):
            commands[key] = f"npm run {script_name}"

    pyproject_text = _read_text(project_dir / "pyproject.toml").lower()
    if not commands["test"] and "pytest" in pyproject_text:
        commands["test"] = "pytest tests/ -v --tb=short"
    if not commands["lint"] and "ruff" in pyproject_text:
        commands["lint"] = "ruff check ."
    if not commands["build"] and (project_dir / "pyproject.toml").exists():
        commands["build"] = "python -m build"

    return commands


def _prefix_command(component_path: str, command: str) -> str:
    """Prefix component commands with a directory change when needed."""
    normalized = component_path.strip().strip("/")
    if normalized in {"", "."}:
        return command
    return f"cd {normalized} && {command}"


def _tech_stack(components: list[Component]) -> list[dict[str, str]]:
    """Build a compact tech stack list from detected components."""
    stack: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for component in components:
        for label, value in (("Language", component.language), ("Framework", component.framework)):
            if not value:
                continue
            key = (component.path, label, value)
            if key in seen:
                continue
            seen.add(key)
            stack.append({"layer": component.path or ".", "tech": value, "kind": label})
        for dep in component.key_deps[:8]:
            key = (component.path, "Dependency", dep)
            if key not in seen:
                seen.add(key)
                stack.append({"layer": component.path or ".", "tech": dep, "kind": "Dependency"})
    return stack


def _detect_domains(project_name: str, components: list[Component]) -> list[dict[str, Any]]:
    """Infer a lightweight domain view from component descriptions and roles."""
    domains = []
    for component in components[:MAX_PROMPT_ITEMS]:
        capability = (
            component.description or component.role or component.framework or component.language
        )
        if not capability:
            continue
        domains.append(
            {
                "name": _component_name(component),
                "capability": capability,
                "components": [_node_id(component.path)],
            }
        )
    if not domains:
        domains.append(
            {
                "name": project_name,
                "capability": "Project capability not determined from manifests.",
                "components": [],
            }
        )
    return domains


def _flows(
    project_type: str, nodes: list[dict[str, Any]], edges: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """Create a conservative flow hint from detected relationships."""
    if not nodes:
        return []
    if edges:
        steps = [edges[0]["from"], edges[0]["to"]]
    else:
        steps = [str(nodes[0].get("id", "root"))]
    return [{"name": "Primary detected flow", "type": project_type, "steps": steps}]


def _detect_conventions(project_dir: Path) -> dict[str, str]:
    """Detect lightweight project conventions without deep source scanning."""
    conventions: dict[str, str] = {}
    if (project_dir / "pyproject.toml").exists():
        conventions["python"] = "Use pyproject.toml as the Python tooling source of truth."
    if (project_dir / "package.json").exists():
        conventions["javascript"] = "Use package.json scripts and dependency declarations."
    if (project_dir / "AGENTS.md").exists():
        conventions["opencode_rules"] = "Read AGENTS.md before agent-driven work."
    if (project_dir / "CLAUDE.md").exists():
        conventions["claude_rules"] = "Read CLAUDE.md before Claude Code work."
    return conventions


def _known_gaps(
    project_type: str,
    commands: dict[str, str],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, str]],
) -> list[str]:
    """Return deterministic gaps the planner should handle cautiously."""
    gaps: list[str] = []
    if not commands.get("test"):
        gaps.append("No deterministic test command was detected; inspect docs before verification.")
    if not nodes:
        gaps.append("No component graph nodes were detected; use focused source exploration.")
    if len(nodes) > 1 and not edges:
        gaps.append("No inter-component dependencies were detected automatically.")
    if project_type == "Unknown":
        gaps.append("Project type could not be determined from common manifest signals.")
    return gaps


def _read_text(path: Path) -> str:
    """Read a text file, returning an empty string on failure."""
    try:
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError:
        return ""


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object, returning an empty dict on failure."""
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_toml(path: Path) -> dict[str, Any]:
    """Read a TOML object, returning an empty dict on failure."""
    if not path.exists():
        return {}
    try:
        import tomllib

        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _as_dict(value: object) -> dict[str, Any]:
    """Return ``value`` as a dict when possible."""
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    """Return ``value`` as a list when possible."""
    return value if isinstance(value, list) else []
