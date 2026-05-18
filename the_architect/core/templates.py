"""Goal template storage for The Architect.

The template system lets users define reusable goal descriptions with
parameterised placeholders (e.g. ``{feature_name}``).  Templates are stored
per-project in ``.architect/templates.json`` as a JSON array of template
objects.  Atomic writes ensure a reader never sees a partial file.

Follows the established JSON storage pattern matching ``presets.py`` and
``feedback.py``.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from the_architect.core.fileutil import safe_atomic_write_json

# Templates file location relative to project root
_TEMPLATES_FILE = Path(".architect/templates.json")
_TEMPLATES_PREFIX = ".templates_tmp_"
_TEMPLATES_LOG_LABEL = "Template state"

# Regex to match {variable} placeholders in goal text
_VARIABLE_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class GoalTemplate(BaseModel):
    """A reusable goal template with parameterised placeholders.

    Attributes:
        name: Unique template identifier (lowercase, hyphens only).
        goal_text: Goal description with ``{variable}`` placeholders.
        description: Human-readable description of the template's purpose.
        config_overrides: Mapping of ArchitectConfig field names to values.
        variables: List of placeholder names extracted from ``goal_text``.
        created_at: ISO 8601 timestamp when the template was first created.
        updated_at: ISO 8601 timestamp when the template was last modified.
    """

    name: str = Field(description="Unique template identifier (lowercase, hyphens only)")
    goal_text: str = Field(description="Goal text with {variable} placeholders")
    description: str = Field(description="Human-readable description")
    config_overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="ArchitectConfig field name -> value pairs",
    )
    variables: list[str] = Field(
        default_factory=list,
        description="Placeholder names extracted from goal_text",
    )
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Returns:
        ISO timestamp string with timezone info.
    """
    return datetime.now(tz=UTC).isoformat()


def _load_raw(project: Path) -> list[dict[str, Any]]:
    """Read the raw template list from disk.

    Args:
        project: The project root directory.

    Returns:
        A list of template dicts.  Returns an empty list if the file doesn't
        exist or contains invalid data.
    """
    templates_path = project / _TEMPLATES_FILE
    try:
        raw = templates_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        return []
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def _save_all(project: Path, templates: list[GoalTemplate]) -> None:
    """Write the full template list to disk atomically.

    Args:
        project: The project root directory.
        templates: The complete list of templates to persist.
    """
    templates_path = project / _TEMPLATES_FILE
    safe_atomic_write_json(
        templates_path,
        [t.model_dump() for t in templates],
        prefix=_TEMPLATES_PREFIX,
        log_label=_TEMPLATES_LOG_LABEL,
    )


def list_templates(project: Path) -> list[GoalTemplate]:
    """Return all saved templates for the project.

    Args:
        project: The project root directory.

    Returns:
        List of ``GoalTemplate`` objects, empty if none exist.
    """
    raw = _load_raw(project)
    templates: list[GoalTemplate] = []
    for entry in raw:
        try:
            templates.append(GoalTemplate.model_validate(entry))
        except (ValueError, TypeError):
            # Skip corrupted entries gracefully
            pass
    return templates


def create_template(
    project: Path,
    name: str,
    goal_text: str,
    description: str,
    config_overrides: dict[str, Any] | None = None,
) -> GoalTemplate:
    """Create a new goal template.

    Unlike presets, templates use create-only semantics: if a template with
    *name* already exists, a ``ValueError`` is raised to prevent accidental
    overwrites.

    Variables are auto-extracted from *goal_text* using
    :func:`extract_variables`.

    Args:
        project: The project root directory.
        name: The unique template identifier.
        goal_text: Goal description with ``{variable}`` placeholders.
        description: Human-readable description of the template.
        config_overrides: Config field name -> value pairs to store.

    Returns:
        The ``GoalTemplate`` object that was written.

    Raises:
        ValueError: If a template with *name* already exists.
    """
    now = _now_iso()

    # Check for duplicate name — create-only, no upsert
    existing = show_template(project, name)
    if existing is not None:
        raise ValueError(f"Template '{name}' already exists")

    template = GoalTemplate(
        name=name,
        goal_text=goal_text,
        description=description,
        config_overrides=config_overrides if config_overrides is not None else {},
        variables=extract_variables(goal_text),
        created_at=now,
        updated_at=now,
    )

    all_templates = list_templates(project)
    all_templates.append(template)
    _save_all(project, all_templates)
    return template


def show_template(project: Path, name: str) -> GoalTemplate | None:
    """Return a single template by name.

    Template name matching is case-sensitive.

    Args:
        project: The project root directory.
        name: The exact template name to look up.

    Returns:
        The ``GoalTemplate`` if found, or ``None`` if no template with that
        name exists.
    """
    for template in list_templates(project):
        if template.name == name:
            return template
    return None


def delete_template(project: Path, name: str) -> bool:
    """Remove a template by name.

    Silently succeeds if the template doesn't exist.

    Args:
        project: The project root directory.
        name: The template name to remove.

    Returns:
        ``True`` if a template was deleted, ``False`` if it didn't exist.
    """
    all_templates = list_templates(project)
    remaining = [t for t in all_templates if t.name != name]

    if len(remaining) == len(all_templates):
        return False

    _save_all(project, remaining)
    return True


def extract_variables(goal_text: str) -> list[str]:
    """Extract unique placeholder names from goal text.

    Finds all ``{variable}`` patterns matching ``[a-zA-Z_][a-zA-Z0-9_]*``
    and returns them sorted alphabetically.

    Args:
        goal_text: The goal description string containing placeholders.

    Returns:
        Sorted list of unique variable names (without braces).
    """
    matches = _VARIABLE_RE.findall(goal_text)
    return sorted(set(matches))


def substitute_variables(goal_text: str, variables: dict[str, str]) -> str:
    """Replace ``{variable}`` placeholders with values from a dictionary.

    Placeholders whose keys are absent from *variables* are left unchanged.

    Args:
        goal_text: The goal description string containing placeholders.
        variables: Mapping of placeholder names to replacement values.

    Returns:
        The goal text with all matched placeholders substituted.
    """

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        return variables.get(var_name, match.group(0))

    return _VARIABLE_RE.sub(_replace, goal_text)
