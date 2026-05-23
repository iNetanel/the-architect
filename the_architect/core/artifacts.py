"""Inter-task artifact storage for The Architect.

Artifacts enable upstream tasks to produce structured outputs (generated code,
schemas, test results, configuration) that downstream tasks can consume.  The
runner calls :func:`store_task_artifact` after a task completes and calls
:func:`load_upstream_artifacts` before a task starts so dependency-aware
context is injected into the execution prompt.

The file lives at ``<project>/.architect/artifacts/artifacts.json``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from the_architect.core.fileutil import safe_atomic_write_json

if TYPE_CHECKING:
    from the_architect.core.tasks import Task

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Directory and file for artifact storage (relative to project root).
ARTIFACTS_DIR = Path(".architect/artifacts")
ARTIFACTS_FILE = ARTIFACTS_DIR / "artifacts.json"

# Maximum total character length for injected upstream artifact content.
_ARTIFACT_INJECTION_MAX_CHARS = 10_000


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Artifact(BaseModel):
    """A single named artifact produced by a task.

    Attributes:
        task_id: Task prefix that produced this artifact (e.g. ``"T01"``).
        name: Human-readable artifact name (e.g. ``"schema"``).
        content: The artifact payload (text, JSON string, or file path).
        artifact_type: Kind of content — ``"text"``, ``"json"``, or ``"file"``.
        created_at: ISO 8601 timestamp when the artifact was stored.
    """

    task_id: str = Field(description="Task prefix that produced this artifact (e.g. 'T01')")
    name: str = Field(description="Human-readable artifact name (e.g. 'schema')")
    content: str = Field(description="Artifact payload (text, JSON string, or file path)")
    artifact_type: str = Field(
        default="text",
        description="Kind of content: 'text', 'json', or 'file'",
    )
    created_at: str = Field(description="ISO 8601 timestamp when stored")


class ArtifactStore(BaseModel):
    """Container for all artifacts in a project.

    Attributes:
        artifacts: List of all :class:`Artifact` objects.
    """

    artifacts: list[Artifact] = Field(
        default_factory=list,
        description="All artifacts across tasks",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Returns:
        ISO timestamp string with timezone info.
    """
    return datetime.now(tz=UTC).isoformat()


def _store_path(project: Path) -> Path:
    """Return the absolute path to the artifacts JSON file for *project*.

    Args:
        project: The project root directory.

    Returns:
        Path to ``.architect/artifacts/artifacts.json``.
    """
    return project / ARTIFACTS_FILE


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def load_artifact_store(project: Path) -> ArtifactStore:
    """Read the artifact store from disk.

    Returns an empty store if the file doesn't exist or contains invalid data.

    Args:
        project: The project root directory.

    Returns:
        An :class:`ArtifactStore` instance (may be empty).
    """
    store_path = _store_path(project)
    try:
        raw = store_path.read_text(encoding="utf-8")
        data: dict[str, Any] = json.loads(raw)
        return ArtifactStore.model_validate(data)
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return ArtifactStore()


def save_artifact_store(project: Path, store: ArtifactStore) -> None:
    """Write the artifact store to disk atomically.

    Creates the ``.architect/artifacts/`` directory if it doesn't exist.
    Uses atomic write (temp file + rename) so a reader never sees a partial
    write.

    Args:
        project: The project root directory.
        store: The :class:`ArtifactStore` to persist.
    """
    store_path = _store_path(project)
    safe_atomic_write_json(
        store_path,
        store.model_dump(),
        prefix=".artifacts_tmp_",
        log_label="Artifact store",
    )


# ---------------------------------------------------------------------------
# Public CRUD API
# ---------------------------------------------------------------------------


def store_task_artifact(
    project: Path,
    task_id: str,
    name: str,
    content: str,
    artifact_type: str = "text",
) -> Artifact:
    """Store a single artifact produced by a task.

    Appends to the existing store (loading first if needed) and writes
    atomically.

    Args:
        project: The project root directory.
        task_id: Task prefix that produced this artifact (e.g. ``"T01"``).
        name: Human-readable artifact name.
        content: The artifact payload.
        artifact_type: Kind of content — ``"text"``, ``"json"``, or ``"file"``.

    Returns:
        The :class:`Artifact` that was stored.
    """
    store = load_artifact_store(project)
    artifact = Artifact(
        task_id=task_id,
        name=name,
        content=content,
        artifact_type=artifact_type,
        created_at=_now_iso(),
    )
    store.artifacts.append(artifact)
    save_artifact_store(project, store)
    return artifact


def load_task_artifacts(project: Path, task_id: str) -> list[Artifact]:
    """Load all artifacts produced by a specific task.

    Args:
        project: The project root directory.
        task_id: Task prefix to filter by (e.g. ``"T01"``).

    Returns:
        List of :class:`Artifact` objects for the given task (may be empty).
    """
    store = load_artifact_store(project)
    return [a for a in store.artifacts if a.task_id == task_id]


def load_upstream_artifacts(
    project: Path,
    task_id: str,
    all_tasks: list[Task],
) -> list[Artifact]:
    """Load artifacts from all dependency tasks of *task_id*.

    Resolves the ``depends_on`` field on the matching :class:`Task` model,
    then returns all artifacts whose ``task_id`` matches a dependency.
    Results are sorted by dependency task_id then artifact name for
    deterministic ordering.

    Args:
        project: The project root directory.
        task_id: The task prefix whose upstream artifacts to load.
        all_tasks: Full list of tasks (used to resolve ``depends_on``).

    Returns:
        List of :class:`Artifact` objects from dependency tasks (may be empty).
    """
    # Find the task to resolve its dependencies
    target_task: Task | None = None
    for task in all_tasks:
        if task.prefix == task_id:
            target_task = task
            break
    if target_task is None:
        return []

    if not target_task.depends_on:
        return []

    # Load all artifacts and filter by dependency task_ids
    store = load_artifact_store(project)
    dep_ids = set(target_task.depends_on)
    upstream = [a for a in store.artifacts if a.task_id in dep_ids]
    # Deterministic ordering: by source task_id, then artifact name
    upstream.sort(key=lambda a: (a.task_id, a.name))
    return upstream


def list_artifacts(project: Path) -> list[Artifact]:
    """Return all artifacts in the store.

    Args:
        project: The project root directory.

    Returns:
        List of all :class:`Artifact` objects (may be empty).
    """
    store = load_artifact_store(project)
    return list(store.artifacts)


def clear_task_artifacts(project: Path, task_id: str) -> None:
    """Remove all artifacts produced by a specific task.

    Silently succeeds if no artifacts exist for the task.

    Args:
        project: The project root directory.
        task_id: Task prefix whose artifacts should be removed.
    """
    store = load_artifact_store(project)
    store.artifacts = [a for a in store.artifacts if a.task_id != task_id]
    save_artifact_store(project, store)


# ---------------------------------------------------------------------------
# Injection helper (used by runner in T02)
# ---------------------------------------------------------------------------


def format_upstream_artifacts(
    artifacts: list[Artifact],
    max_chars: int = _ARTIFACT_INJECTION_MAX_CHARS,
) -> str:
    """Format upstream artifacts as a text block for prompt injection.

    Produces a ``=== UPSTREAM ARTIFACTS ===`` section suitable for
    injection into the runner's ``build_instruction()`` output.  If total
    content exceeds *max_chars*, the output is truncated with a warning.

    Args:
        artifacts: List of upstream :class:`Artifact` objects.
        max_chars: Maximum total character length (default 10,000).

    Returns:
        Formatted string ready for injection, or empty string if no artifacts.
    """
    if not artifacts:
        return ""

    lines: list[str] = []
    lines.append("=== UPSTREAM ARTIFACTS ===")
    lines.append(
        "The following artifacts were produced by tasks your current task "
        "depends on. Use them as context for your work."
    )
    lines.append("")

    # Group by task_id for readability
    by_task: dict[str, list[Artifact]] = {}
    for artifact in artifacts:
        by_task.setdefault(artifact.task_id, []).append(artifact)

    for source_task in sorted(by_task.keys()):
        task_artifacts = by_task[source_task]
        lines.append(f"--- Artifacts from {source_task} ---")
        for artifact in task_artifacts:
            lines.append(f"  Name: {artifact.name}")
            lines.append(f"  Type: {artifact.artifact_type}")
            lines.append("  Content:")
            lines.append(artifact.content)
            lines.append("")

    text = "\n".join(lines)

    if len(text) > max_chars:
        truncated = text[:max_chars]
        # Truncate at a line boundary to avoid mid-line cuts
        last_newline = truncated.rfind("\n")
        if last_newline > 0:
            truncated = truncated[:last_newline]
        truncated += "\n\n[WARNING: Artifact content truncated — exceeded maximum size limit]"
        return truncated

    return text
