"""Project-level health checks for The Architect.

Validates project state before autonomous runs — lock files, task
consistency, baselines, circuit state, token ledger, and presets.

Each check function returns a :class:`HealthCheck` with a status of
``"ok"``, ``"warn"``, or ``"fail"``.  Missing files and directories
are treated as ``"ok"`` with a ``"not found"`` detail — they are not
errors.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# HealthCheck model
# ---------------------------------------------------------------------------


@dataclass
class HealthCheck:
    """Result of a single health check.

    Attributes:
        status: One of ``"ok"``, ``"warn"``, or ``"fail"``.
        label: Human-readable name of the check.
        detail: Explanation or diagnostic detail.
    """

    status: Literal["ok", "warn", "fail"]
    label: str
    detail: str


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------


def check_lock_file(project: Path) -> HealthCheck:
    """Check for a stale runner lock file.

    An active ``.architect/runner.lock`` means another Architect process
    may be running.  A missing lock file is healthy.

    Args:
        project: The project root directory.

    Returns:
        HealthCheck with status ``"fail"`` if lock exists, ``"ok"`` otherwise.
    """
    lock_file = project / ".architect" / "runner.lock"
    if lock_file.exists():
        return HealthCheck(
            status="fail",
            label="Lock file",
            detail="runner.lock exists — another Architect process may be active",
        )
    return HealthCheck(
        status="ok",
        label="Lock file",
        detail="No runner.lock found",
    )


def check_task_consistency(project: Path) -> HealthCheck:
    """Check that task files in ``tasks/`` align with PROGRESS.md entries.

    Compares discovered task file prefixes against the task rows in
    PROGRESS.md.  Mismatches (files without rows or rows without files)
    produce a ``"warn"``.  A missing PROGRESS.md or tasks/ directory is
    ``"ok"`` with a ``"not found"`` detail.

    Args:
        project: The project root directory.

    Returns:
        HealthCheck describing task consistency status.
    """
    tasks_dir = project / "tasks"
    progress_file = tasks_dir / "PROGRESS.md"

    if not tasks_dir.exists() or not progress_file.exists():
        return HealthCheck(
            status="ok",
            label="Task consistency",
            detail="No tasks/ or PROGRESS.md found",
        )

    # Discover task file prefixes
    task_files: set[str] = set()
    _TASK_FILE_RE = __import__("re").compile(r"^(T\d+(?:R\d+|[A-Z])?)_")
    try:
        for entry in tasks_dir.iterdir():
            if (
                entry.is_file()
                and entry.suffix == ".md"
                and entry.name
                not in (
                    "PROGRESS.md",
                    "INSTRUCTIONS.md",
                    "SUMMARY.md",
                    "GOAL.md",
                )
            ):
                m = _TASK_FILE_RE.match(entry.name)
                if m:
                    task_files.add(m.group(1))
    except OSError as exc:
        return HealthCheck(
            status="warn",
            label="Task consistency",
            detail=f"Cannot read tasks/ directory: {exc}",
        )

    if not task_files:
        return HealthCheck(
            status="ok",
            label="Task consistency",
            detail="No task files found",
        )

    # Extract prefixes from PROGRESS.md rows
    progress_rows: set[str] = set()
    _ROW_RE = __import__("re").compile(
        r"^\|\s*(T\d+(?:R\d+|[A-Z])?)\s+\|",
        __import__("re").MULTILINE,
    )
    try:
        content = progress_file.read_text(encoding="utf-8")
        for m in _ROW_RE.finditer(content):
            progress_rows.add(m.group(1))
    except OSError as exc:
        return HealthCheck(
            status="warn",
            label="Task consistency",
            detail=f"Cannot read PROGRESS.md: {exc}",
        )

    files_without_rows = task_files - progress_rows
    rows_without_files = progress_rows - task_files

    if not files_without_rows and not rows_without_files:
        return HealthCheck(
            status="ok",
            label="Task consistency",
            detail=f"{len(task_files)} task file(s) match PROGRESS.md entries",
        )

    issues: list[str] = []
    if files_without_rows:
        issues.append(f"{len(files_without_rows)} file(s) missing from PROGRESS.md")
    if rows_without_files:
        issues.append(f"{len(rows_without_files)} PROGRESS.md row(s) without task file")

    return HealthCheck(
        status="warn",
        label="Task consistency",
        detail="; ".join(issues),
    )


def check_baselines(project: Path) -> HealthCheck:
    """Check that baseline files in ``.architect/baselines/`` are valid JSON.

    Iterates every ``*.json`` file in the baselines directory and attempts
    to parse it.  Corrupted files produce a ``"warn"`` with a count.
    A missing baselines directory is ``"ok"``.

    Args:
        project: The project root directory.

    Returns:
        HealthCheck describing baseline integrity.
    """
    baselines_dir = project / ".architect" / "baselines"

    if not baselines_dir.exists():
        return HealthCheck(
            status="ok",
            label="Baselines",
            detail="No .architect/baselines/ directory found",
        )

    json_files = sorted(baselines_dir.glob("*.json"))
    if not json_files:
        return HealthCheck(
            status="ok",
            label="Baselines",
            detail="No baseline files found",
        )

    corrupted: list[str] = []
    for fp in json_files:
        try:
            raw = fp.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                corrupted.append(fp.name)
        except (json.JSONDecodeError, OSError):
            corrupted.append(fp.name)

    valid_count = len(json_files) - len(corrupted)
    if not corrupted:
        return HealthCheck(
            status="ok",
            label="Baselines",
            detail=f"{valid_count} valid baseline file(s)",
        )

    return HealthCheck(
        status="warn",
        label="Baselines",
        detail=f"{valid_count} valid, {len(corrupted)} corrupted: {', '.join(corrupted[:5])}",
    )


def check_circuit_state(project: Path) -> HealthCheck:
    """Check circuit breaker state for OPEN or HALF_OPEN circuits.

    Reads ``.architect/circuit.json`` and counts circuits by state.
    OPEN or HALF_OPEN circuits produce a ``"warn"``.  A missing file
    is ``"ok"``.

    Args:
        project: The project root directory.

    Returns:
        HealthCheck describing circuit breaker health.
    """
    circuit_file = project / ".architect" / "circuit.json"

    if not circuit_file.exists():
        return HealthCheck(
            status="ok",
            label="Circuit state",
            detail="No circuit.json found",
        )

    try:
        raw = circuit_file.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return HealthCheck(
                status="warn",
                label="Circuit state",
                detail="circuit.json has unexpected format (not a JSON object)",
            )
    except (json.JSONDecodeError, OSError) as exc:
        return HealthCheck(
            status="warn",
            label="Circuit state",
            detail=f"Cannot read circuit.json: {exc}",
        )

    counts: dict[str, int] = {"CLOSED": 0, "OPEN": 0, "HALF_OPEN": 0}
    for task_id, state in data.items():
        if isinstance(state, dict):
            st = state.get("state", "CLOSED")
            if st in counts:
                counts[st] += 1
            else:
                counts["CLOSED"] += 1
        else:
            counts["CLOSED"] += 1

    open_count = counts["OPEN"]
    half_open_count = counts["HALF_OPEN"]
    total = sum(counts.values())

    if open_count == 0 and half_open_count == 0:
        return HealthCheck(
            status="ok",
            label="Circuit state",
            detail=f"{total} task(s), all CLOSED",
        )

    parts: list[str] = []
    if open_count:
        parts.append(f"{open_count} OPEN")
    if half_open_count:
        parts.append(f"{half_open_count} HALF_OPEN")

    return HealthCheck(
        status="warn",
        label="Circuit state",
        detail=f"{', '.join(parts)} out of {total} task(s)",
    )


def check_token_ledger(project: Path) -> HealthCheck:
    """Check that the token ledger is readable and report record count.

    Reads ``.architect/token_ledger.json`` and counts run records.
    A corrupted ledger produces a ``"warn"``.  A missing ledger is ``"ok"``.

    Args:
        project: The project root directory.

    Returns:
        HealthCheck describing token ledger status.
    """
    ledger_file = project / ".architect" / "token_ledger.json"

    if not ledger_file.exists():
        return HealthCheck(
            status="ok",
            label="Token ledger",
            detail="No token_ledger.json found",
        )

    try:
        raw = ledger_file.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, list):
            return HealthCheck(
                status="warn",
                label="Token ledger",
                detail="token_ledger.json has unexpected format (not a JSON array)",
            )
        return HealthCheck(
            status="ok",
            label="Token ledger",
            detail=f"{len(data)} run record(s)",
        )
    except json.JSONDecodeError as exc:
        return HealthCheck(
            status="warn",
            label="Token ledger",
            detail=f"Corrupted JSON: {exc}",
        )
    except OSError as exc:
        return HealthCheck(
            status="warn",
            label="Token ledger",
            detail=f"Cannot read file: {exc}",
        )


def check_presets(project: Path) -> HealthCheck:
    """Check that the presets file is valid (if it exists).

    Reads ``.architect/presets.json`` and validates the preset entries.
    A missing file is ``"ok"``.  Corrupted entries produce a ``"warn"``.

    Args:
        project: The project root directory.

    Returns:
        HealthCheck describing presets status.
    """
    presets_file = project / ".architect" / "presets.json"

    if not presets_file.exists():
        return HealthCheck(
            status="ok",
            label="Presets",
            detail="No presets.json found",
        )

    try:
        raw = presets_file.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, list):
            return HealthCheck(
                status="warn",
                label="Presets",
                detail="presets.json has unexpected format (not a JSON array)",
            )

        # Validate each preset has required fields
        valid = 0
        invalid = 0
        for entry in data:
            if isinstance(entry, dict) and "name" in entry:
                valid += 1
            else:
                invalid += 1

        if invalid == 0:
            return HealthCheck(
                status="ok",
                label="Presets",
                detail=f"{valid} valid preset(s)",
            )

        return HealthCheck(
            status="warn",
            label="Presets",
            detail=f"{valid} valid, {invalid} invalid preset(s)",
        )
    except json.JSONDecodeError as exc:
        return HealthCheck(
            status="warn",
            label="Presets",
            detail=f"Corrupted JSON: {exc}",
        )
    except OSError as exc:
        return HealthCheck(
            status="warn",
            label="Presets",
            detail=f"Cannot read file: {exc}",
        )


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def run_project_checks(project: Path) -> list[HealthCheck]:
    """Run all project health checks and return results.

    Executes every check function in a deterministic order and returns
    the list of :class:`HealthCheck` results.  Callers are responsible
    for rendering the results (CLI table, JSON output, etc.).

    Args:
        project: The project root directory.

    Returns:
        Ordered list of HealthCheck results covering lock file, task
        consistency, baselines, circuit state, token ledger, and presets.
    """
    return [
        check_lock_file(project),
        check_task_consistency(project),
        check_baselines(project),
        check_circuit_state(project),
        check_token_ledger(project),
        check_presets(project),
    ]
