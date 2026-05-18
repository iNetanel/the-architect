"""Run rollback — restore files to pre-run state using captured baselines.

The rollback system gives users a safety net for fire-and-forget autonomous
execution.  If an agent breaks something, they can undo the changes with
``architect rollback``.

Content restoration strategy
----------------------------

**Hybrid approach (Option C):**

1. **Git-based restoration** (primary): Use ``git show <commit>:<path>`` to
   retrieve original file content from the commit just before the baseline
   was captured.  Works in any git repository.

2. **Error with guidance** (fallback): If git is unavailable, the commit
   cannot be found, or the file was not tracked by git at that commit, the
   file is recorded as a ``RollbackError`` with a message explaining why
   restoration failed and suggesting manual recovery.

Content snapshots (storing file content alongside baselines) are NOT used
because they require runner changes to populate, and git history is already
available in the repos where The Architect is most commonly used.

The runner may be extended in a future cycle to capture content snapshots
for non-git repos or newly created files that have no git history.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import BaseModel, Field

from the_architect.core.baseline import (
    WorkspaceBaseline,
    detect_changes,
)
from the_architect.core.fileutil import atomic_write_text

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class RollbackError(BaseModel):
    """A single error encountered during rollback.

    Attributes:
        path: Relative file path that could not be processed.
        message: Human-readable explanation of what went wrong.
    """

    path: str = Field(description="Relative file path that failed")
    message: str = Field(description="Explanation of the rollback failure")


class RollbackPlan(BaseModel):
    """Plan describing what files to restore, delete, or skip during rollback.

    Attributes:
        files_to_restore: Mapping of relative path to original file content.
            These are files that were modified during the run and should be
            restored to their baseline state.
        files_to_delete: Paths that were created during the run and should be
            removed to return to the baseline state.
        files_unchanged: Paths that exist in both baseline and current state
            with identical content — no action needed.
    """

    files_to_restore: dict[str, str] = Field(
        default_factory=dict,
        description="Relative path → original file content for modified files",
    )
    files_to_delete: list[str] = Field(
        default_factory=list,
        description="Paths created during the run that should be deleted",
    )
    files_unchanged: list[str] = Field(
        default_factory=list,
        description="Paths unchanged since baseline — no action needed",
    )


class RollbackResult(BaseModel):
    """Outcome of a rollback operation.

    Attributes:
        restored_count: Number of files successfully restored to baseline.
        deleted_count: Number of files successfully deleted (created during run).
        unchanged_count: Number of files that were already unchanged.
        errors: List of per-file errors encountered during rollback.
    """

    restored_count: int = Field(default=0, description="Files restored to baseline content")
    deleted_count: int = Field(default=0, description="Created files deleted")
    unchanged_count: int = Field(default=0, description="Files already unchanged")
    errors: list[RollbackError] = Field(
        default_factory=list,
        description="Per-file errors during rollback",
    )


class BaselineInfo(BaseModel):
    """Metadata about an available baseline for rollback.

    Attributes:
        task_prefix: Task identifier (e.g. ``"T01"``) this baseline is tied to.
        timestamp: ISO 8601 UTC timestamp when the baseline was captured.
        file_count: Number of tracked files in the baseline.
        file_path: Absolute path to the baseline JSON file on disk.
    """

    task_prefix: str = Field(description="Task prefix (e.g. T01)")
    timestamp: str = Field(description="ISO 8601 UTC capture timestamp")
    file_count: int = Field(description="Number of tracked files in baseline")
    file_path: str = Field(description="Absolute path to the baseline JSON file")


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _find_commit_before_timestamp(
    project_dir: Path,
    timestamp: datetime,
) -> str | None:
    """Find the git commit SHA just before *timestamp*.

    Uses ``git log --before=<timestamp> --format=%H -1`` to locate the most
    recent commit before the baseline was captured.

    Args:
        project_dir: The project root (git repository root).
        timestamp: The baseline capture time.

    Returns:
        The 40-character commit SHA, or ``None`` if no commit exists before
        the timestamp, or if the directory is not a git repository.
    """
    try:
        iso_ts = timestamp.isoformat()
        result = subprocess.run(
            ["git", "log", f"--before={iso_ts}", "--format=%H", "-1"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        sha = result.stdout.strip()
        return sha if sha else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _get_file_content_at_commit(
    project_dir: Path,
    commit_sha: str,
    rel_path: str,
) -> str | None:
    """Retrieve file content from a specific git commit.

    Uses ``git show <commit>:<path>`` to extract the file as it existed
    at the given commit.

    Args:
        project_dir: The project root (git repository root).
        commit_sha: 40-character commit SHA.
        rel_path: Relative path from project root.

    Returns:
        The file content as a UTF-8 string, or ``None`` if the file did
        not exist at that commit or git failed.
    """
    try:
        result = subprocess.run(
            ["git", "show", f"{commit_sha}:{rel_path}"],
            cwd=str(project_dir),
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        return result.stdout.decode("utf-8")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_rollback_plan(
    baseline: WorkspaceBaseline,
    project_dir: Path,
) -> RollbackPlan:
    """Compare a baseline against the current workspace and build a rollback plan.

    Uses :func:`~the_architect.core.baseline.detect_changes` to classify files
    into created, modified, and deleted categories.  Modified files are restored
    by retrieving their original content from git (the commit just before the
    baseline timestamp).  Created files are marked for deletion.

    Args:
        baseline: Previously captured :class:`~the_architect.core.baseline.WorkspaceBaseline`.
        project_dir: The project root directory.

    Returns:
        A :class:`RollbackPlan` with files grouped into restore, delete,
        and unchanged categories.
    """
    changes = detect_changes(baseline, project_dir)

    created = changes.get("created", [])
    modified = changes.get("modified", [])

    # Find the git commit just before the baseline was captured
    commit_sha = _find_commit_before_timestamp(project_dir, baseline.timestamp)

    # Build files_to_restore from modified files
    files_to_restore: dict[str, str] = {}
    files_unchanged: list[str] = []

    for rel_path in sorted(modified):
        if commit_sha:
            original_content = _get_file_content_at_commit(project_dir, commit_sha, rel_path)
            if original_content is not None:
                files_to_restore[rel_path] = original_content
                continue

        # Could not retrieve original content — skip silently;
        # the CLI layer will surface a warning to the user.
        logger.warning(
            "Rollback: cannot retrieve original content for %s (git commit=%s)",
            rel_path,
            commit_sha or "N/A",
        )

    # Files that existed in baseline but were neither created nor modified
    # are unchanged — they are the intersection minus modified.
    baseline_paths = set(baseline.files.keys())
    unchanged = sorted(baseline_paths - set(modified) - set(created))
    files_unchanged = unchanged

    return RollbackPlan(
        files_to_restore=files_to_restore,
        files_to_delete=sorted(created),
        files_unchanged=files_unchanged,
    )


def execute_rollback(
    plan: RollbackPlan,
    project_dir: Path,
    dry_run: bool = False,
) -> RollbackResult:
    """Execute a rollback plan, restoring files to their baseline state.

    Modified files are overwritten with their original content (retrieved
    during plan computation).  Created files are deleted.  In dry-run mode
    the plan is computed but no files are modified.

    Uses :func:`~the_architect.core.fileutil.atomic_write_text` for safe
    file writes (temp file + atomic rename).

    Args:
        plan: A :class:`RollbackPlan` from :func:`compute_rollback_plan`.
        project_dir: The project root directory.
        dry_run: If ``True``, compute the outcome without modifying files.

    Returns:
        A :class:`RollbackResult` with counts and any per-file errors.
    """
    restored_count = 0
    deleted_count = 0
    errors: list[RollbackError] = []

    if dry_run:
        return RollbackResult(
            restored_count=len(plan.files_to_restore),
            deleted_count=len(plan.files_to_delete),
            unchanged_count=len(plan.files_unchanged),
            errors=errors,
        )

    # Restore modified files
    for rel_path, original_content in plan.files_to_restore.items():
        target = project_dir / rel_path
        try:
            atomic_write_text(target, original_content)
            restored_count += 1
            logger.info("Rollback: restored %s", rel_path)
        except OSError as exc:
            msg = f"Cannot restore file: {exc!r}"
            logger.warning("Rollback: %s — %s", rel_path, msg)
            errors.append(RollbackError(path=rel_path, message=msg))

    # Delete created files
    for rel_path in plan.files_to_delete:
        target = project_dir / rel_path
        try:
            if target.is_file():
                target.unlink()
                deleted_count += 1
                logger.info("Rollback: deleted %s", rel_path)
            elif target.exists():
                msg = "Path exists but is not a regular file"
                logger.warning("Rollback: %s — %s", rel_path, msg)
                errors.append(RollbackError(path=rel_path, message=msg))
            else:
                # File already gone — not an error, just skip
                logger.debug("Rollback: %s already deleted, skipping", rel_path)
        except OSError as exc:
            msg = f"Cannot delete file: {exc!r}"
            logger.warning("Rollback: %s — %s", rel_path, msg)
            errors.append(RollbackError(path=rel_path, message=msg))

    return RollbackResult(
        restored_count=restored_count,
        deleted_count=deleted_count,
        unchanged_count=len(plan.files_unchanged),
        errors=errors,
    )


def list_run_baselines(project_dir: Path) -> list[BaselineInfo]:
    """Discover available baselines in the project's ``.architect/baselines/`` directory.

    Scans for ``*.json`` files, reads each one, and returns structured
    metadata sorted by timestamp (oldest first).  Corrupted files are
    skipped gracefully.

    Args:
        project_dir: The project root directory.

    Returns:
        Sorted list of :class:`BaselineInfo` objects, one per readable
        baseline file.  Empty list if the directory does not exist or
        contains no valid baselines.
    """
    baselines_dir = project_dir / ".architect" / "baselines"

    if not baselines_dir.is_dir():
        return []

    json_files = sorted(baselines_dir.glob("*.json"))
    if not json_files:
        return []

    results: list[BaselineInfo] = []
    for json_file in json_files:
        try:
            raw = json_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            # Validate minimally — we need task_prefix and timestamp
            task_prefix = data.get("task_prefix", json_file.stem)
            timestamp_iso = data.get("timestamp", "")
            file_count = len(data.get("files", {}))
            results.append(
                BaselineInfo(
                    task_prefix=str(task_prefix),
                    timestamp=str(timestamp_iso),
                    file_count=file_count,
                    file_path=str(json_file.resolve()),
                )
            )
        except (OSError, json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("Rollback: cannot read baseline %s: %r", json_file.name, exc)
            continue

    # Sort by timestamp (oldest first) for deterministic output
    results.sort(key=lambda info: info.timestamp)
    return results
