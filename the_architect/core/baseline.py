"""Workspace baseline capture and change detection for The Architect.

Captures SHA-256 checksums of key project files at a point in time,
persists the snapshot as JSON, and compares two snapshots to detect
created, modified, or deleted files.  Used by the inter-task reassessment
and retrospective review pipelines to verify what changed on disk.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import BaseModel, Field

from the_architect.core.fileutil import atomic_write_text

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# File extensions tracked at the project root level.
_TRACKED_EXTENSIONS: set[str] = {".py", ".toml", ".json", ".md"}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class FileRecord(BaseModel):
    """Checksum record for a single file."""

    path: str = Field(description="Relative path from project_dir")
    sha256: str = Field(description="SHA-256 hex digest of file contents")
    size: int = Field(description="File size in bytes")


class WorkspaceBaseline(BaseModel):
    """Captured state of key project files at a point in time.

    Records the SHA-256 digest and size of every tracked file so that
    a later comparison can detect which files were created, modified,
    or deleted since the baseline was captured.
    """

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the baseline was captured (UTC)",
    )
    task_prefix: str = Field(
        default="", description="Task prefix (e.g. T01) this baseline is tied to"
    )
    files: dict[str, FileRecord] = Field(
        default_factory=dict,
        description="Mapping of relative path to checksum record",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_hidden(path: Path, base: Path) -> bool:
    """Return True if any component of the relative path is hidden (starts with '.').

    Args:
        path: The absolute path to check.
        base: The base directory to compute the relative path from.

    Returns:
        True if any path component (file or directory) starts with a dot.
    """
    try:
        rel = path.relative_to(base)
    except ValueError:
        return True
    return any(part.startswith(".") for part in rel.parts)


def _hash_file(filepath: Path) -> tuple[str, int] | None:
    """Read a file as UTF-8 and return (sha256_hex, size) or None on decode failure.

    Args:
        filepath: Absolute path to the file.

    Returns:
        Tuple of (sha256 hex digest, file size in bytes), or None if the
        file cannot be decoded as UTF-8.
    """
    try:
        raw = filepath.read_bytes()
        raw.decode("utf-8")  # validate UTF-8 without storing the string
        digest = hashlib.sha256(raw).hexdigest()
        return digest, len(raw)
    except UnicodeDecodeError:
        return None
    except OSError as exc:
        logger.warning(f"Baseline: cannot read file {filepath}: {exc!r}")
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def capture_baseline(project_dir: Path, task_prefix: str = "") -> WorkspaceBaseline:
    """Walk the workspace and capture SHA-256 checksums of tracked files.

    Two regions are scanned:

    1. **tasks/** — every regular file (symlinks and hidden dirs skipped).
    2. **Project root** — files with extensions ``.py``, ``.toml``,
       ``.json``, ``.md`` (symlinks and hidden files/dirs skipped).

    Files that cannot be decoded as UTF-8 are silently skipped.
    ``OSError`` during directory walks is logged as a warning and does
    not abort the capture.

    Args:
        project_dir: The project root directory.
        task_prefix: Optional task identifier (e.g. ``"T01"``).

    Returns:
        A :class:`WorkspaceBaseline` with timestamp, task_prefix, and
        a dict of relative-path-to-:class:`FileRecord`.
    """
    files: dict[str, FileRecord] = {}

    # ── Walk tasks/ ──────────────────────────────────────────────────
    tasks_dir = project_dir / "tasks"
    try:
        for dirpath, dirnames, filenames in os.walk(tasks_dir):
            # Prune hidden directories in-place so walk won't descend
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fname in filenames:
                filepath = Path(dirpath) / fname
                if filepath.is_symlink() or filepath.name.startswith("."):
                    continue
                result = _hash_file(filepath)
                if result is None:
                    continue
                rel = filepath.relative_to(project_dir).as_posix()
                files[rel] = FileRecord(path=rel, sha256=result[0], size=result[1])
    except OSError as exc:
        logger.warning(f"Baseline: cannot walk tasks/ directory {tasks_dir}: {exc!r}")

    # ── Walk project root (tracked extensions only) ──────────────────
    try:
        for dirpath, dirnames, filenames in os.walk(project_dir):
            # Prune hidden directories and common non-project dirs
            dirnames[:] = [
                d
                for d in dirnames
                if not d.startswith(".")
                and d not in {"__pycache__", ".git", ".architect", "node_modules"}
            ]
            for fname in filenames:
                filepath = Path(dirpath) / fname
                if filepath.is_symlink() or filepath.name.startswith("."):
                    continue
                if filepath.suffix.lower() not in _TRACKED_EXTENSIONS:
                    continue
                result = _hash_file(filepath)
                if result is None:
                    continue
                rel = filepath.relative_to(project_dir).as_posix()
                files[rel] = FileRecord(path=rel, sha256=result[0], size=result[1])
    except OSError as exc:
        logger.warning(f"Baseline: cannot walk project root {project_dir}: {exc!r}")

    return WorkspaceBaseline(timestamp=datetime.now(UTC), task_prefix=task_prefix, files=files)


def detect_changes(baseline: WorkspaceBaseline, project_dir: Path) -> dict[str, list[str]]:
    """Compare current workspace state against a stored baseline.

    Captures a fresh baseline via :func:`capture_baseline` and diffs
    file sets and SHA-256 digests to find created, modified, and
    deleted files.  Each list is sorted for deterministic output.

    Args:
        baseline: Previously captured :class:`WorkspaceBaseline`.
        project_dir: The project root directory.

    Returns:
        Dict with keys ``"created"``, ``"modified"``, and ``"deleted"``,
        each mapping to a sorted list of relative file paths.
    """
    current = capture_baseline(project_dir)

    baseline_keys = set(baseline.files.keys())
    current_keys = set(current.files.keys())

    created = sorted(current_keys - baseline_keys)
    deleted = sorted(baseline_keys - current_keys)

    modified: list[str] = []
    for path in sorted(baseline_keys & current_keys):
        if baseline.files[path].sha256 != current.files[path].sha256:
            modified.append(path)

    return {"created": created, "modified": modified, "deleted": deleted}


def write_baseline(baseline: WorkspaceBaseline, path: Path) -> None:
    """Serialize a baseline to a JSON file atomically.

    Creates parent directories as needed.  Uses Pydantic's
    ``model_dump_json`` for clean datetime serialization and
    :func:`~the_architect.core.fileutil.atomic_write_text` for a
    cross-platform temp-file + rename so the file is never partially
    written.  Errors are logged as warnings and do not propagate.

    Args:
        baseline: The :class:`WorkspaceBaseline` to persist.
        path: Destination file path (will be overwritten if it exists).
    """
    try:
        content = baseline.model_dump_json(indent=2)
        atomic_write_text(path, content, prefix=".baseline_tmp_")
    except OSError as exc:
        logger.warning(f"Baseline: cannot write baseline to {path}: {exc!r}")


def read_baseline(path: Path) -> WorkspaceBaseline:
    """Read and validate a baseline from a JSON file.

    Args:
        path: Path to a previously written baseline JSON file.

    Returns:
        A :class:`WorkspaceBaseline` instance.

    Raises:
        FileNotFoundError: If the baseline file does not exist.
        ValueError: If the file contains invalid JSON or fails model
            validation.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"Baseline file not found: {path}") from None
    except OSError as exc:
        raise OSError(f"Cannot read baseline file {path}: {exc!r}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Baseline file contains invalid JSON: {exc}") from None

    try:
        return WorkspaceBaseline.model_validate(data)
    except Exception as exc:
        raise ValueError(f"Baseline data failed validation: {exc}") from None
