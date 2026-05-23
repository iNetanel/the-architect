"""Resume verification — deterministic baseline comparison after interrupted runs.

When a run is interrupted and the user chooses to resume, this module verifies
which completed tasks are still valid by comparing captured baselines against
current disk state.  This replaces the previous binary Execute/Replan choice
with an intelligent middle ground: valid tasks are skipped (saving tokens),
stale tasks are re-executed.

The verification is automatic during resume — no new config options are
needed.  Users can disable it with ``--no-verify-resume`` (T02 runner
integration).

Verification logic:

- **valid** — baseline exists, all tracked files exist on disk, all SHA-256
  checksums match current state.
- **stale** — baseline exists but one or more files changed (checksum mismatch
  or file missing on disk).
- **missing** — no baseline file found for this task (task ran without
  baseline capture).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from loguru import logger
from pydantic import BaseModel, Field

from the_architect.core.baseline import read_baseline

if TYPE_CHECKING:
    from the_architect.config import ArchitectConfig
    from the_architect.core.tasks import Task, TaskPlan


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ResumeVerificationResult(BaseModel):
    """Result of verifying a single completed task's baseline against disk state.

    Attributes:
        task_id: Task prefix (e.g. ``"T01"``).
        status: One of ``"valid"``, ``"stale"``, or ``"missing"``.
        reason: Human-readable explanation of the verification outcome.
        baseline_exists: Whether a baseline file was found for this task.
        baseline_age_seconds: Age of the baseline file in seconds, or
            ``None`` if no baseline was found.
    """

    task_id: str = Field(description="Task prefix (e.g. 'T01')")
    status: Literal["valid", "stale", "missing"] = Field(
        description=(
            "Verification outcome: 'valid' = baseline matches disk, "
            "'stale' = files changed since baseline, 'missing' = no baseline found"
        )
    )
    reason: str = Field(
        default="",
        description="Human-readable explanation of the verification outcome",
    )
    baseline_exists: bool = Field(
        default=False,
        description="Whether a baseline file was found for this task",
    )
    baseline_age_seconds: float | None = Field(
        default=None,
        description="Age of the baseline file in seconds, or None if not found",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def verify_completed_task(
    task: Task,
    project_root: Path,
    progress_file: Path,
) -> ResumeVerificationResult:
    """Verify whether a completed task's workspace state is still valid.

    Reads the baseline captured for *task* from
    ``.architect/baselines/<task_prefix>.json`` and compares every tracked
    file's SHA-256 checksum against the current disk state.

    Args:
        task: The completed :class:`~the_architect.core.tasks.Task` to verify.
        project_root: The project root directory containing ``.architect/``.
        progress_file: Path to ``PROGRESS.md`` (kept for future extension,
            not used in current implementation).

    Returns:
        A :class:`ResumeVerificationResult` with status ``"valid"``,
        ``"stale"``, or ``"missing"``.

    Examples:
        >>> result = verify_completed_task(task, project_root, progress)
        >>> result.status
        'valid'
    """
    task_prefix = task.prefix
    baseline_dir = project_root / ".architect" / "baselines"
    baseline_path = baseline_dir / f"{task_prefix}.json"

    # ── Check baseline existence ─────────────────────────────────────
    if not baseline_path.exists():
        return ResumeVerificationResult(
            task_id=task_prefix,
            status="missing",
            reason=(
                f"No baseline found for {task_prefix} (file: {baseline_path.name} in baselines/)"
            ),
            baseline_exists=False,
            baseline_age_seconds=None,
        )

    # ── Read baseline ────────────────────────────────────────────────
    try:
        baseline = read_baseline(baseline_path)
    except (ValueError, FileNotFoundError, OSError) as exc:
        # Corrupted or unreadable baseline — treat as stale to be safe
        logger.warning(
            "Resume verification: cannot read baseline for {}: {}",
            task_prefix,
            exc,
        )
        return ResumeVerificationResult(
            task_id=task_prefix,
            status="stale",
            reason=f"Baseline unreadable: {exc}",
            baseline_exists=True,
            baseline_age_seconds=_baseline_age(baseline_path),
        )

    baseline_age = _baseline_age(baseline_path)

    # ── Compare checksums against disk ───────────────────────────────
    missing_files: list[str] = []
    changed_files: list[str] = []

    for rel_path, record in baseline.files.items():
        disk_path = project_root / rel_path
        if not disk_path.exists():
            missing_files.append(rel_path)
            continue

        current_hash = _hash_file(disk_path)
        if current_hash is None:
            # File exists but cannot be read as UTF-8 — flag as changed
            changed_files.append(rel_path)
            continue

        if current_hash != record.sha256:
            changed_files.append(rel_path)

    # ── Determine status ─────────────────────────────────────────────
    if not missing_files and not changed_files:
        file_count = len(baseline.files)
        return ResumeVerificationResult(
            task_id=task_prefix,
            status="valid",
            reason=f"All {file_count} tracked file(s) match baseline",
            baseline_exists=True,
            baseline_age_seconds=baseline_age,
        )

    # Build detailed reason
    parts: list[str] = []
    if missing_files:
        parts.append(f"{len(missing_files)} file(s) missing")
    if changed_files:
        parts.append(f"{len(changed_files)} file(s) changed")
    reason = f"Baseline mismatch: {', '.join(parts)}"
    return ResumeVerificationResult(
        task_id=task_prefix,
        status="stale",
        reason=reason,
        baseline_exists=True,
        baseline_age_seconds=baseline_age,
    )


def verify_all_completed_tasks(
    plan: TaskPlan,
    config: ArchitectConfig,
) -> list[ResumeVerificationResult]:
    """Verify all completed tasks in a plan against their baselines.

    Iterates every task in *plan* whose status is ``"done"`` and calls
    :func:`verify_completed_task` for each.  Tasks that are not
    ``"done"`` (pending, running, failed) are silently skipped.

    Args:
        plan: The :class:`~the_architect.core.tasks.TaskPlan` containing
            all discovered tasks.
        config: The :class:`~the_architect.config.ArchitectConfig`
            providing project root and progress file paths.

    Returns:
        List of :class:`ResumeVerificationResult` objects, one per
        completed task, in plan order.

    Examples:
        >>> results = verify_all_completed_tasks(plan, config)
        >>> valid = [r for r in results if r.status == "valid"]
        >>> stale = [r for r in results if r.status == "stale"]
    """
    results: list[ResumeVerificationResult] = []
    project_root = config.project_root
    progress_file = config.progress_file

    for task in plan.tasks:
        if task.status.value != "done":
            continue

        result = verify_completed_task(task, project_root, progress_file)
        results.append(result)
        logger.debug(
            "Resume verification {}: {} — {}",
            task.prefix,
            result.status,
            result.reason,
        )

    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _baseline_age(baseline_path: Path) -> float:
    """Return the age of a baseline file in seconds.

    Args:
        baseline_path: Path to the baseline JSON file.

    Returns:
        Age in seconds since the file was last modified.  Returns 0.0
        if the file cannot be stat-ed.
    """
    try:
        mtime = baseline_path.stat().st_mtime
        return time.time() - mtime
    except OSError:
        return 0.0


def _hash_file(filepath: Path) -> str | None:
    """Compute the SHA-256 hex digest of a file's contents.

    Args:
        filepath: Absolute path to the file.

    Returns:
        The SHA-256 hex digest string, or ``None`` if the file cannot
        be read or decoded as UTF-8.
    """
    try:
        raw = filepath.read_bytes()
        raw.decode("utf-8")  # validate UTF-8
        import hashlib

        return hashlib.sha256(raw).hexdigest()
    except (UnicodeDecodeError, OSError):
        return None
