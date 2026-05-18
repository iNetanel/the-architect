"""Preset storage for The Architect.

The preset system lets users save and recall named configuration combinations
(e.g. \"sprint\", \"quick-fix\", \"deep-work\").  Presets are stored per-project
in ``.architect/presets.json`` as a JSON array of preset objects.  Atomic
writes ensure a reader never sees a partial file.

Follows the established JSON storage pattern matching ``feedback.py`` and
``monitor_state.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from the_architect.core.fileutil import safe_atomic_write_json

# Presets file location relative to project root
PRESETS_FILE = Path(".architect/presets.json")


class Preset(BaseModel):
    """A saved configuration preset.

    Attributes:
        name: Unique preset identifier (lowercase, hyphen-separated, no spaces).
        description: Human-readable description of what this preset configures.
        config_overrides: Mapping of ArchitectConfig field names to values.
            When applied, these are merged into ``architect.toml`` via
            :func:`the_architect.config.write_config`.
        created_at: ISO 8601 timestamp when the preset was first created.
        updated_at: ISO 8601 timestamp when the preset was last modified.
    """

    name: str = Field(description="Unique preset identifier (lowercase, hyphens only)")
    description: str = Field(description="Human-readable description")
    config_overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="ArchitectConfig field name → value pairs",
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
    """Read the raw preset list from disk.

    Args:
        project: The project root directory.

    Returns:
        A list of preset dicts.  Returns an empty list if the file doesn't
        exist or contains invalid data.
    """
    presets_path = project / PRESETS_FILE
    try:
        raw = presets_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        return []
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def _save_all(project: Path, presets: list[Preset]) -> None:
    """Write the full preset list to disk atomically.

    Args:
        project: The project root directory.
        presets: The complete list of presets to persist.
    """
    presets_path = project / PRESETS_FILE
    safe_atomic_write_json(
        presets_path,
        [p.model_dump() for p in presets],
        prefix=".presets_tmp_",
        log_label="Preset state",
    )


def list_presets(project: Path) -> list[Preset]:
    """Return all saved presets for the project.

    Args:
        project: The project root directory.

    Returns:
        List of ``Preset`` objects, empty if none exist.
    """
    raw = _load_raw(project)
    presets: list[Preset] = []
    for entry in raw:
        try:
            presets.append(Preset.model_validate(entry))
        except (ValueError, TypeError):
            # Skip corrupted entries gracefully
            pass
    return presets


def get_preset(project: Path, name: str) -> Preset | None:
    """Return a single preset by name.

    Preset name matching is case-sensitive.

    Args:
        project: The project root directory.
        name: The exact preset name to look up.

    Returns:
        The ``Preset`` if found, or ``None`` if no preset with that name exists.
    """
    for preset in list_presets(project):
        if preset.name == name:
            return preset
    return None


def save_preset(
    project: Path,
    name: str,
    description: str,
    config_overrides: dict[str, Any],
) -> Preset:
    """Create or update a preset atomically.

    If a preset with *name* already exists, its ``description`` and
    ``config_overrides`` are replaced and ``updated_at`` is refreshed.
    The original ``created_at`` timestamp is preserved.

    Args:
        project: The project root directory.
        name: The unique preset identifier.
        description: Human-readable description of the preset.
        config_overrides: Config field name → value pairs to store.

    Returns:
        The ``Preset`` object that was written.
    """
    now = _now_iso()
    existing = get_preset(project, name)

    if existing:
        preset = Preset(
            name=name,
            description=description,
            config_overrides=config_overrides,
            created_at=existing.created_at,
            updated_at=now,
        )
    else:
        preset = Preset(
            name=name,
            description=description,
            config_overrides=config_overrides,
            created_at=now,
            updated_at=now,
        )

    all_presets = [p for p in list_presets(project) if p.name != name]
    all_presets.append(preset)
    _save_all(project, all_presets)
    return preset


def delete_preset(project: Path, name: str) -> bool:
    """Remove a preset by name.

    Silently succeeds if the preset doesn't exist.

    Args:
        project: The project root directory.
        name: The preset name to remove.

    Returns:
        ``True`` if a preset was deleted, ``False`` if it didn't exist.
    """
    all_presets = list_presets(project)
    remaining = [p for p in all_presets if p.name != name]

    if len(remaining) == len(all_presets):
        return False

    _save_all(project, remaining)
    return True


def clear_presets(project: Path) -> int:
    """Remove all presets from the project.

    Args:
        project: The project root directory.

    Returns:
        The number of presets that were removed.
    """
    all_presets = list_presets(project)
    count = len(all_presets)
    if count > 0:
        _save_all(project, [])
    return count
