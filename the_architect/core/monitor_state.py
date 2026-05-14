"""Monitor state file management for The Architect's tmux dashboard.

The runner writes a JSON state file after every significant event so the
dashboard process can read it and render a live view without coupling to
the runner process.

The file lives at ``<project>/.architect/monitor_state.json``.
Writes are atomic: we write to a temp file then rename, so the dashboard
never reads a partially written file.

All writes are best-effort — if they fail, the run continues unaffected.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from the_architect.core.fileutil import safe_atomic_write_json

if TYPE_CHECKING:
    from the_architect.core.tasks import Task

# State file location relative to project root
MONITOR_STATE_FILE = Path(".architect/monitor_state.json")

# Stop-flag file — written by Ctrl+C handler to request graceful stop
STOP_FLAG_FILE = Path(".architect/monitor_stop.flag")

# Kill-flag file — written by Ctrl+C handler to request immediate kill
KILL_FLAG_FILE = Path(".architect/monitor_kill.flag")


# ---------------------------------------------------------------------------
# Run status constants
# ---------------------------------------------------------------------------

TASK_STATUS_DONE = "done"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_PENDING = "pending"
TASK_STATUS_FAILED = "failed"

RUN_STATUS_PLANNING = "PLANNING"
RUN_STATUS_RUNNING = "RUNNING"
RUN_STATUS_COOLDOWN = "COOLDOWN WAIT"
RUN_STATUS_REPLANNING = "REPLANNING"
RUN_STATUS_PAUSED = "PAUSED"
RUN_STATUS_DONE = "DONE"
RUN_STATUS_FAILED = "FAILED"
RUN_STATUS_STOPPING = "STOPPING"
RUN_STATUS_KILLED = "KILLED"


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Returns:
        ISO timestamp string with timezone info.
    """
    return datetime.now(tz=UTC).isoformat()


def write_monitor_state(project_dir: Path, state: dict[str, Any]) -> None:
    """Write the monitor state to disk atomically.

    Writes to a temp file in the same directory, then renames it to the
    final path.  This ensures the dashboard never reads a partial write.
    On platforms where the destination file may be held open by a reader
    process (e.g. the dashboard on Windows), the rename is retried briefly
    before failing.

    Errors are logged at debug level and silently swallowed — the state
    file is optional infrastructure and must never crash the run.

    Args:
        project_dir: The project root directory.
        state: The state dictionary to serialise.
    """
    state_path = project_dir / MONITOR_STATE_FILE
    safe_atomic_write_json(
        state_path,
        state,
        prefix=".monitor_state_tmp_",
        log_label="Monitor state",
    )


def read_monitor_state(project_dir: Path) -> dict[str, Any] | None:
    """Read the monitor state from disk.

    Args:
        project_dir: The project root directory.

    Returns:
        Parsed state dict, or None if the file doesn't exist or is unreadable.
    """
    state_path = project_dir / MONITOR_STATE_FILE
    try:
        raw = state_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        return None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def write_planning_state(project_dir: Path, goal: str = "") -> None:
    """Write a PLANNING status to the monitor state file.

    Called before the planning prompts are shown so the dashboard
    immediately displays "Planning in progress" instead of "Waiting
    for run to start".  This is best-effort — failures are silently
    swallowed.

    Args:
        project_dir: The project root directory.
        goal: The user's goal text (shown on the dashboard if available).
    """
    write_monitor_state(
        project_dir,
        {
            "status": RUN_STATUS_PLANNING,
            "project_name": project_dir.name,
            "goal": goal,
            "started_at": _now_iso(),
            "tasks": [],
            "current_task_id": None,
            "current_task_title": None,
        },
    )


def check_stop_flag(project_dir: Path) -> bool:
    """Check whether a graceful stop has been requested.

    Args:
        project_dir: The project root directory.

    Returns:
        True if the stop flag file exists.
    """
    return (project_dir / STOP_FLAG_FILE).exists()


def check_kill_flag(project_dir: Path) -> bool:
    """Check whether an immediate kill has been requested.

    Args:
        project_dir: The project root directory.

    Returns:
        True if the kill flag file exists.
    """
    return (project_dir / KILL_FLAG_FILE).exists()


def clear_stop_flags(project_dir: Path) -> None:
    """Remove both stop and kill flag files.

    Args:
        project_dir: The project root directory.
    """
    for flag in (STOP_FLAG_FILE, KILL_FLAG_FILE):
        try:
            (project_dir / flag).unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# MonitorStateWriter — convenience wrapper used by the runner
# ---------------------------------------------------------------------------


class MonitorStateWriter:
    """Writes monitor state after each significant runner event.

    Instantiated once per run and passed callbacks via the runner's
    on_task_start / on_task_done / on_attempt_start / on_attempt_done
    hooks.  All writes are best-effort — failures are logged at debug
    level and never propagate.

    Args:
        project_dir: The project root directory.
        tasks: The full ordered list of tasks for this run.
        free_rotator: Optional FreeModelRotator instance (for free-mode stats).
        max_retries: Maximum retry count from config.
    """

    def __init__(
        self,
        project_dir: Path,
        tasks: list[Task],
        free_rotator: object | None = None,
        max_retries: int = 3,
    ) -> None:
        self._project_dir = project_dir
        self._tasks = tasks
        self._free_rotator = free_rotator
        self._max_retries = max_retries
        self._run_started_at = _now_iso()
        self._run_start_epoch = time.time()
        self._current_task_id: str | None = None
        self._current_task_title: str | None = None
        self._current_attempt: int = 0
        self._status: str = RUN_STATUS_RUNNING
        self._session_tokens: int = 0
        self._last_attempt_tokens: int = 0
        self._task_statuses: dict[str, str] = {
            t.prefix: (TASK_STATUS_DONE if t.status.value == "done" else TASK_STATUS_PENDING)
            for t in tasks
        }
        self._replanned_tasks: set[str] = set()
        self._cooldown_active: bool = False
        self._cooldown_started_at: str | None = None
        self._cooldown_wait_count: int = 0
        self._circuit_state: str = "CLOSED"
        self._circuit_no_progress: int = 0
        self._circuit_same_error: int = 0
        self._circuit_no_progress_threshold: int = 3
        self._circuit_same_error_threshold: int = 3
        self._model_current: str = ""
        self._model_rotation_count: int = 0
        self._graceful_stop_requested: bool = False
        self._session_cost_usd: float = 0.0
        self._last_task_cost_usd: float = 0.0
        self._model_costs: dict[str, float] = {}  # model_name -> cumulative USD

        # Flush the initial RUNNING state to disk immediately so the
        # dashboard transitions from PLANNING → RUNNING as soon as
        # execution begins — not when the first on_task_start() fires.
        self._flush()

    # ------------------------------------------------------------------
    # Event hooks (called by runner callbacks)
    # ------------------------------------------------------------------

    def on_task_start(self, task: Task) -> None:
        """Record that a task has started.

        Args:
            task: The task that is starting.
        """
        self._current_task_id = task.prefix
        self._current_task_title = task.title or task.name
        self._current_attempt = 1
        self._status = RUN_STATUS_RUNNING
        self._task_statuses[task.prefix] = TASK_STATUS_RUNNING
        self._flush()

    def on_task_done(
        self,
        task_id: str,
        tokens: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        model: str = "",
    ) -> None:
        """Record that a task completed successfully.

        Args:
            task_id: The task prefix that completed.
            tokens: Total token count for the last attempt (legacy).
            input_tokens: Prompt/input tokens for cost calculation.
            output_tokens: Completion/output tokens for cost calculation.
            cache_read_tokens: Cache read tokens for cost calculation.
            cache_write_tokens: Cache write tokens for cost calculation.
            model: Model name used — required for cost estimation.
        """
        self._task_statuses[task_id] = TASK_STATUS_DONE
        self._session_tokens += tokens
        self._last_attempt_tokens = tokens
        if model and (input_tokens or output_tokens):
            try:
                from the_architect.core.token_ledger import estimate_cost_detailed

                cost = estimate_cost_detailed(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_tokens=cache_write_tokens,
                    model=model,
                )
                self._session_cost_usd += cost
                self._last_task_cost_usd = cost
                self._model_costs[model] = self._model_costs.get(model, 0.0) + cost
            except Exception:
                pass
        self._flush()

    def on_task_failed(
        self,
        task_id: str,
        tokens: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        model: str = "",
    ) -> None:
        """Record that a task failed all retries.

        Args:
            task_id: The task prefix that failed.
            tokens: Total token count for the last attempt (legacy).
            input_tokens: Prompt/input tokens for cost calculation.
            output_tokens: Completion/output tokens for cost calculation.
            cache_read_tokens: Cache read tokens for cost calculation.
            cache_write_tokens: Cache write tokens for cost calculation.
            model: Model name used — required for cost estimation.
        """
        self._task_statuses[task_id] = TASK_STATUS_FAILED
        self._session_tokens += tokens
        self._last_attempt_tokens = tokens
        if model and (input_tokens or output_tokens):
            try:
                from the_architect.core.token_ledger import estimate_cost_detailed

                cost = estimate_cost_detailed(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_tokens=cache_write_tokens,
                    model=model,
                )
                self._session_cost_usd += cost
                self._last_task_cost_usd = cost
                self._model_costs[model] = self._model_costs.get(model, 0.0) + cost
            except Exception:
                pass
        self._flush()

    def on_attempt_start(self, attempt_num: int, model: str | None) -> None:
        """Record that a new attempt has started.

        Args:
            attempt_num: The 1-based attempt number.
            model: The model being used for this attempt.
        """
        self._current_attempt = attempt_num
        if model:
            if self._model_current and self._model_current != model:
                self._model_rotation_count += 1
            self._model_current = model
        self._flush()

    def on_attempt_done(self, attempt_num: int, success: bool, tokens: int = 0) -> None:
        """Record that an attempt has finished.

        Args:
            attempt_num: The 1-based attempt number.
            success: Whether the attempt succeeded.
            tokens: Token count for this attempt.
        """
        self._last_attempt_tokens = tokens
        self._session_tokens += tokens
        self._flush()

    def on_cooldown_start(self, task_id: str, wait_count: int) -> None:
        """Record that a provider cooldown wait has started.

        Args:
            task_id: The task that triggered the cooldown.
            wait_count: Total cooldown wait count for this task.
        """
        self._cooldown_active = True
        self._cooldown_started_at = _now_iso()
        self._cooldown_wait_count = wait_count
        self._status = RUN_STATUS_COOLDOWN
        self._flush()

    def on_cooldown_end(self) -> None:
        """Record that a provider cooldown wait has ended."""
        self._cooldown_active = False
        self._cooldown_started_at = None
        self._status = RUN_STATUS_RUNNING
        self._flush()

    def on_circuit_state_change(
        self,
        state: str,
        no_progress: int,
        same_error: int,
        no_progress_threshold: int = 3,
        same_error_threshold: int = 3,
    ) -> None:
        """Record a circuit breaker state change.

        Args:
            state: New circuit state ("CLOSED", "OPEN", "HALF_OPEN").
            no_progress: Current consecutive no-progress count.
            same_error: Current consecutive same-error count.
            no_progress_threshold: Threshold for no-progress trips.
            same_error_threshold: Threshold for same-error trips.
        """
        self._circuit_state = state
        self._circuit_no_progress = no_progress
        self._circuit_same_error = same_error
        self._circuit_no_progress_threshold = no_progress_threshold
        self._circuit_same_error_threshold = same_error_threshold
        self._flush()

    def on_model_rotated(self, new_model: str) -> None:
        """Record a model rotation event.

        Args:
            new_model: The new model being used.
        """
        if self._model_current and self._model_current != new_model:
            self._model_rotation_count += 1
        self._model_current = new_model
        self._flush()

    def on_replan(self, task_id: str) -> None:
        """Record that a task was replanned.

        Args:
            task_id: The task that was replanned.
        """
        self._replanned_tasks.add(task_id)
        self._status = RUN_STATUS_REPLANNING
        self._flush()

    def on_replan_done(self) -> None:
        """Record that replanning has finished."""
        self._status = RUN_STATUS_RUNNING
        self._flush()

    def on_run_done(self, success: bool) -> None:
        """Record that the entire run has completed.

        Args:
            success: Whether all tasks completed successfully.
        """
        self._status = RUN_STATUS_DONE if success else RUN_STATUS_FAILED
        self._current_task_id = None
        self._flush()

    def on_graceful_stop_requested(self) -> None:
        """Record that a graceful stop has been requested."""
        self._graceful_stop_requested = True
        self._status = RUN_STATUS_STOPPING
        self._flush()

    def on_killed(self) -> None:
        """Record that the run was killed immediately."""
        self._status = RUN_STATUS_KILLED
        self._flush()

    def add_tasks(self, new_tasks: list[Task]) -> None:
        """Add new tasks to the monitor (e.g. retrospective R-prefixed tasks).

        Tasks whose prefix is already tracked are skipped.  After adding,
        the state is flushed so the dashboard picks up the new tasks.

        Args:
            new_tasks: The tasks to add.
        """
        for t in new_tasks:
            if t.prefix not in self._task_statuses:
                self._tasks.append(t)
                self._task_statuses[t.prefix] = TASK_STATUS_PENDING
        self._flush()

    # ------------------------------------------------------------------
    # State assembly and flush
    # ------------------------------------------------------------------

    def _flush(self) -> None:
        """Assemble and write the current state to disk."""
        tasks_list = []
        for task in self._tasks:
            tasks_list.append(
                {
                    "id": task.prefix,
                    "title": task.title or task.name,
                    "status": self._task_statuses.get(task.prefix, TASK_STATUS_PENDING),
                    "replanned": task.prefix in self._replanned_tasks,
                }
            )

        # Free model info
        free_mode = False
        free_remaining = 0
        if self._free_rotator is not None:
            free_mode = True
            try:
                free_remaining = getattr(self._free_rotator, "remaining_count", 0)
            except Exception:
                free_remaining = 0

        # Cooldown remaining seconds
        cooldown_remaining_seconds: int | None = None
        if self._cooldown_active and self._cooldown_started_at:
            try:
                started = datetime.fromisoformat(self._cooldown_started_at)
                now = datetime.now(tz=UTC)
                if started.tzinfo is None:
                    started = started.replace(tzinfo=UTC)
                elapsed = (now - started).total_seconds()
                remaining = max(0, 3600 - int(elapsed))
                cooldown_remaining_seconds = remaining
            except Exception:
                cooldown_remaining_seconds = None

        total_tasks = len(self._tasks)
        tasks_completed = sum(1 for s in self._task_statuses.values() if s == TASK_STATUS_DONE)

        state: dict[str, Any] = {
            "project_name": self._project_dir.name,
            "run_started_at": self._run_started_at,
            "current_task_id": self._current_task_id,
            "current_task_title": self._current_task_title,
            "current_attempt": self._current_attempt,
            "total_tasks": total_tasks,
            "tasks_completed": tasks_completed,
            "status": self._status,
            "tasks": tasks_list,
            "circuit_breaker": {
                "state": self._circuit_state,
                "no_progress_count": self._circuit_no_progress,
                "same_error_count": self._circuit_same_error,
                "thresholds": {
                    "no_progress": self._circuit_no_progress_threshold,
                    "same_error": self._circuit_same_error_threshold,
                },
            },
            "cooldown": {
                "active": self._cooldown_active,
                "wait_started_at": self._cooldown_started_at,
                "wait_count": self._cooldown_wait_count,
                "remaining_seconds": cooldown_remaining_seconds,
            },
            "model": {
                "current": self._model_current,
                "free_mode": free_mode,
                "free_remaining": free_remaining,
                "rotation_count": self._model_rotation_count,
            },
            "tokens": {
                "session_total": self._session_tokens,
                "last_attempt": self._last_attempt_tokens,
                "session_cost_usd": round(self._session_cost_usd, 6),
                "last_task_cost_usd": round(self._last_task_cost_usd, 6),
                "model_costs": {model: round(cost, 6) for model, cost in self._model_costs.items()},
            },
            "graceful_stop_requested": self._graceful_stop_requested,
            "max_retries": self._max_retries,
        }

        write_monitor_state(self._project_dir, state)
