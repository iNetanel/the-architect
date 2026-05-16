"""Task execution engine for The Architect."""

from __future__ import annotations

import asyncio
import datetime
import os
import re
import shutil
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import BaseModel, Field

from the_architect.config import ArchitectConfig
from the_architect.core.progress import (
    reconcile_progress_with_task_files,
    reconcile_task_status,
    task_is_done,
    task_is_resolved,
)
from the_architect.core.tasks import Task, TaskPlan, discover_tasks

_STREAM_LEFT_PAD = "  "
_PROVIDER_IDLE_TIMEOUT_SECONDS = 900.0
_PROVIDER_SLEEP_WAKE_GAP_SECONDS = 120.0
_PROVIDER_READ_PROBE_SECONDS = 5.0
_FORCED_TERMINATION_EXIT_CODE = -int(getattr(signal, "SIGKILL", signal.SIGTERM))
_PROGRESS_FLUSH_DELAY_SECONDS = 2.0
_OUTCOME_SECTION_MARKER = "=== TASK OUTCOME ==="
_OUTCOME_FIELD_LABELS = {
    "summary": "Summary",
    "files": "Files",
    "verification": "Verification",
    "impact": "Impact",
}


class StreamRenderer:
    """Abstract renderer for provider stream output."""

    def write_line(self, line: str) -> None:
        """Render a single provider output line."""

    def set_footer(self, text: str) -> None:
        """Update footer/status text if supported."""

    def clear_footer(self) -> None:
        """Clear footer/status text if supported."""

    def close(self) -> None:
        """Release renderer resources."""


class PlainStreamRenderer(StreamRenderer):
    """Current direct-to-stdout streaming behavior."""

    def write_line(self, line: str) -> None:
        _write_stream_line(line)

    def set_footer(self, text: str) -> None:
        return

    def clear_footer(self) -> None:
        return

    def close(self) -> None:
        return


class ManagedExecutionRenderer(StreamRenderer):
    """Compatibility shim — currently an alias for :class:`PlainStreamRenderer`.

    An earlier experiment rendered provider output inside an alternate-screen
    surface with a live footer, but it proved visually unstable (blinking,
    footer/output mixing, animation stalls under streaming load).  Doing that
    correctly requires a real TUI toolkit (``prompt_toolkit``, ``Textual``,
    or ``rich.Live``), which is a larger change.  Until that work lands, the
    managed renderer simply behaves like the plain streaming renderer so
    execution output scrolls naturally with no broken UI surface.
    """

    def write_line(self, line: str) -> None:
        _write_stream_line(line)

    def set_footer(self, text: str) -> None:
        return

    def clear_footer(self) -> None:
        return

    def close(self) -> None:
        return


def _set_renderer_footer(renderer: StreamRenderer | None, text: str) -> None:
    """No-op until a real TUI footer is implemented."""
    return


def _footer_text(label: str, status: str) -> str:
    """Build footer text with stable label and dynamic status (unused today)."""
    return f"{label} | {status}"


def _stream_width() -> int | None:
    """Return the effective width for streamed output, or None to use full width."""
    return None


def _write_stream_line(line: str) -> None:
    """Write streamed provider output with left breathing room."""
    sys.stdout.write(f"{_STREAM_LEFT_PAD}{line}\n")
    sys.stdout.flush()


if TYPE_CHECKING:
    from the_architect.core.baseline import WorkspaceBaseline
    from the_architect.core.circuit import AttemptSummary
    from the_architect.core.provider import ArchitectProvider


# ---------------------------------------------------------------------------
# Execution result models
# ---------------------------------------------------------------------------


class TokenUsage(BaseModel):
    """Token usage for a single task run."""

    input_tokens: int = Field(default=0, description="Prompt/input tokens")
    output_tokens: int = Field(default=0, description="Completion/output tokens")
    cache_read_tokens: int = Field(default=0, description="Tokens read from cache")
    cache_write_tokens: int = Field(default=0, description="Tokens written to cache")

    @property
    def total(self) -> int:
        """Total tokens (input + output)."""
        return self.input_tokens + self.output_tokens

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


class TaskResult(BaseModel):
    """Result of a single task execution — used by the success screen."""

    prefix: str = Field(description="Task prefix e.g. T01")
    title: str = Field(default="", description="Human-readable task title")
    status: str = Field(description="done, failed, or skipped")
    duration_seconds: float = Field(default=0.0, description="Wall-clock duration in seconds")
    attempts: int = Field(default=1, description="Number of attempts made")
    tokens: TokenUsage = Field(default_factory=TokenUsage, description="Token usage")
    model: str = Field(default="", description="Model used for the successful attempt")
    rate_limit_hit: bool = Field(
        default=False,
        description="True if a rate-limit error was detected during this attempt",
    )
    accumulated_text: str = Field(
        default="",
        description="Agent text output from the last attempt — used for error classification",
    )
    exit_code: int = Field(
        default=0,
        description="Provider subprocess exit code from the last attempt",
    )
    cooldown_until: int = Field(
        default=0,
        description=(
            "Unix timestamp when the provider cooldown resets "
            "(from rate_limit_event.resetsAt). 0 means not set."
        ),
    )
    outcome_summary: str = Field(
        default="",
        description="Structured task outcome summary extracted after the final attempt",
    )
    baseline_path: str = Field(
        default="",
        description=(
            "Absolute path to the baseline JSON file for this task "
            "(.architect/baselines/<task_name>.json). Empty string when "
            "baseline capture is disabled or failed."
        ),
    )
    interrupted: bool = Field(
        default=False,
        description="True when the provider attempt was interrupted by local execution conditions.",
    )
    interruption_reason: str = Field(
        default="",
        description="Human-readable reason for an interrupted provider attempt.",
    )


# ---------------------------------------------------------------------------
# Output analysis — completion promises, error signals, progress signals (IMP-01, IMP-03)
# ---------------------------------------------------------------------------


class OutputAnalysis(BaseModel):
    """Analysis of agent output from a single attempt.

    Extracts structured signals from the agent's text output to make
    smarter completion and retry decisions.

    Signal types:
    - **completion_promises**: ``<promise>PREFIX_COMPLETE</promise>`` tags
    - **error_signals**: phrases indicating the agent is stuck or blocked
    - **progress_signals**: phrases indicating the agent is making progress
    - **agent_self_assessment**: the agent's own assessment of task state
    """

    completion_promises: list[str] = Field(
        default_factory=list,
        description="Task prefixes extracted from <promise>PREFIX_COMPLETE</promise> tags",
    )
    error_signals: list[str] = Field(
        default_factory=list,
        description="Phrases indicating the agent is stuck, blocked, or unable to proceed",
    )
    progress_signals: list[str] = Field(
        default_factory=list,
        description="Phrases indicating the agent is making progress or tests are passing",
    )
    agent_self_assessment: str = Field(
        default="unknown",
        description=(
            "Agent's own assessment of task state: 'complete', 'in_progress', 'stuck', or 'unknown'"
        ),
    )

    @property
    def has_completion_promise(self) -> bool:
        """True if any completion promise was detected in agent output."""
        return len(self.completion_promises) > 0

    @property
    def has_progress_signal(self) -> bool:
        """True if any progress signal was detected in agent output."""
        return len(self.progress_signals) > 0

    @property
    def is_stuck(self) -> bool:
        """True if the agent appears stuck (2+ error signals detected).

        Requires multiple error signals to avoid false positives from a
        single mention of difficulty.
        """
        return len(self.error_signals) >= 2


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------


_PROMISE_PATTERN = re.compile(r"<promise>([TR]\d+)_COMPLETE</promise>")

_ERROR_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"I('m| am) (stuck|blocked|unable to)", re.IGNORECASE),
    re.compile(r"I can't (proceed|continue|figure out|resolve)", re.IGNORECASE),
    re.compile(r"no (clear|obvious) (path|way) forward", re.IGNORECASE),
    re.compile(r"this (seems|appears) impossible", re.IGNORECASE),
    re.compile(r"unable to (resolve|fix|solve|complete)", re.IGNORECASE),
    re.compile(r"blocked by (an|a|the)? ?(error|issue|problem|dependency)", re.IGNORECASE),
]

_PROGRESS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"all tests (pass|are passing|green)", re.IGNORECASE),
    re.compile(r"(\d+) tests? (passing|passed|green)", re.IGNORECASE),
    re.compile(r"no (errors|failures|issues) (found|remaining)", re.IGNORECASE),
    re.compile(r"task (is |)(complete|done|finished)", re.IGNORECASE),
    re.compile(r"all (items|sub-?tasks|requirements) (complete|done|implemented)", re.IGNORECASE),
    re.compile(r"successfully (implemented|created|wrote|fixed)", re.IGNORECASE),
]

_SELF_ASSESSMENT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"task (is |)(complete|done|finished)", re.IGNORECASE), "complete"),
    (re.compile(r"I('m| am) (stuck|blocked|unable to)", re.IGNORECASE), "stuck"),
    (re.compile(r"I can't (proceed|continue|figure out|resolve)", re.IGNORECASE), "stuck"),
    (re.compile(r"still (working|need to|have to|must)", re.IGNORECASE), "in_progress"),
    (
        re.compile(r"(remaining|outstanding) (work|items|tasks|issues)", re.IGNORECASE),
        "in_progress",
    ),
]


def extract_completion_promises(text: str) -> list[str]:
    """Extract ``<promise>PREFIX_COMPLETE</promise>`` tags from agent output.

    The agent emits these tags to signal genuine task completion.  Each
    match yields the task prefix (e.g. ``T01``, ``R02``) without the
    ``_COMPLETE`` suffix.

    Args:
        text: The accumulated text output from the agent.

    Returns:
        List of task prefixes found in promise tags.
    """
    return _PROMISE_PATTERN.findall(text)


def extract_error_signals(text: str) -> list[str]:
    """Extract phrases indicating the agent is stuck or blocked.

    Scans the agent's text output for patterns like "I'm stuck",
    "I can't proceed", "no clear path forward", etc.  Returns the
    matched text for each pattern found.

    Args:
        text: The accumulated text output from the agent.

    Returns:
        List of matched phrases indicating the agent is blocked.
    """
    signals: list[str] = []
    for pattern in _ERROR_PATTERNS:
        for match in pattern.finditer(text):
            signals.append(match.group(0))
    return signals


def extract_progress_signals(text: str) -> list[str]:
    """Extract phrases indicating the agent is making progress.

    Scans the agent's text output for patterns like "all tests pass",
    "no errors remaining", "task is complete", etc.  Returns the
    matched text for each pattern found.

    Args:
        text: The accumulated text output from the agent.

    Returns:
        List of matched phrases indicating progress.
    """
    signals: list[str] = []
    for pattern in _PROGRESS_PATTERNS:
        for match in pattern.finditer(text):
            signals.append(match.group(0))
    return signals


def _determine_self_assessment(text: str) -> str:
    """Determine the agent's self-assessment from its text output.

    Scans the text in order and returns the first matching assessment
    category.  Priority: "stuck" > "complete" > "in_progress" > "unknown".

    "stuck" takes priority over "complete" because a stuck agent that
    also says "task complete" is likely hallucinating completion.

    Args:
        text: The accumulated text output from the agent.

    Returns:
        One of "complete", "stuck", "in_progress", or "unknown".
    """
    # Check stuck patterns first — they override everything
    for pattern, label in _SELF_ASSESSMENT_PATTERNS:
        if label == "stuck" and pattern.search(text):
            return "stuck"

    # Then check complete
    for pattern, label in _SELF_ASSESSMENT_PATTERNS:
        if label == "complete" and pattern.search(text):
            return "complete"

    # Then in_progress
    for pattern, label in _SELF_ASSESSMENT_PATTERNS:
        if label == "in_progress" and pattern.search(text):
            return "in_progress"

    return "unknown"


def analyze_output(accumulated_text: str) -> OutputAnalysis:
    """Analyze accumulated agent text output for structured signals.

    Extracts completion promises, error signals, progress signals, and
    the agent's self-assessment from its text output.  These signals
    enable smarter completion and retry decisions.

    Args:
        accumulated_text: All text output from the agent during a run.

    Returns:
        OutputAnalysis with all detected signals.
    """
    return OutputAnalysis(
        completion_promises=extract_completion_promises(accumulated_text),
        error_signals=extract_error_signals(accumulated_text),
        progress_signals=extract_progress_signals(accumulated_text),
        agent_self_assessment=_determine_self_assessment(accumulated_text),
    )


# ---------------------------------------------------------------------------
# Lock file for concurrent run prevention
# ---------------------------------------------------------------------------

LOCK_FILE = Path(".architect/runner.lock")


def acquire_lock(project_dir: Path) -> bool:
    """Try to create .architect/runner.lock atomically. Returns False if already locked.

    If a lock file exists but the owning process is dead (stale lock),
    the lock is removed and re-acquired automatically.  This handles
    the common case where The Architect was killed (Ctrl+C, terminal close)
    without going through the ``finally: release_lock()`` path.

    Args:
        project_dir: The project root directory.

    Returns:
        True if lock was acquired, False if another live process holds it.
    """
    lock_path = project_dir / LOCK_FILE
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    pid_bytes = str(os.getpid()).encode()

    # Attempt atomic creation: O_CREAT | O_EXCL ensures the file is created
    # only if it does not already exist, avoiding the TOCTOU race.
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, pid_bytes)
        finally:
            os.close(fd)
        return True
    except FileExistsError:
        # Lock file already exists — check if it is stale
        if _is_lock_stale(lock_path):
            logger.warning("Removing stale lock file (owning process is dead)")
            try:
                lock_path.unlink()
            except OSError:
                return False
            # Retry once after removing stale lock
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, pid_bytes)
                finally:
                    os.close(fd)
                return True
            except OSError:
                return False
        return False
    except OSError:
        return False


def _is_lock_stale(lock_path: Path) -> bool:
    """Check whether a lock file belongs to a dead process.

    Reads the PID from the lock file and checks if that process is
    still running.  If the PID cannot be read or the process is dead,
    the lock is considered stale.

    Args:
        lock_path: Path to the lock file.

    Returns:
        True if the owning process is dead or PID is unreadable.
    """
    try:
        pid_str = lock_path.read_text(encoding="utf-8").strip()
        pid = int(pid_str)
    except (OSError, ValueError):
        # Can't read PID — treat as stale
        return True

    # Check if the process is still alive
    try:
        # Signal 0 = existence check only — does NOT kill the process.
        # Cross-platform: works on Windows, Linux, and macOS (Python 3.x).
        # Raises ProcessLookupError if the process is dead (stale lock).
        # Raises PermissionError if the process is alive but unowned (live lock).
        os.kill(pid, 0)
    except PermissionError:
        # Process exists but we can't signal it — assume alive.
        # Must be listed before the broad OSError clause because
        # PermissionError is a subclass of OSError.
        return False
    except (ProcessLookupError, OSError):
        # ProcessLookupError on Linux/macOS, plain OSError on Windows —
        # both mean the process is dead, so the lock is stale.
        return True

    # Process is alive — lock is valid
    return False


def release_lock(project_dir: Path) -> None:
    """Remove .architect/runner.lock.

    Args:
        project_dir: The project root directory.
    """
    lock_path = project_dir / LOCK_FILE
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def setup_logging(log_dir: Path, verbose: bool = False) -> None:
    """Configure loguru with console and file handlers.

    Args:
        log_dir: Directory for log files. Must be a ``Path`` or ``str``;
            any other type (for example a ``MagicMock`` leaked from a test
            fixture) raises ``TypeError`` immediately instead of silently
            creating a log file under a mock-derived directory name.
        verbose: If True, set console to DEBUG level.

    Raises:
        TypeError: If ``log_dir`` is not a ``Path`` or ``str``.
    """
    # Defensive guard. ``MagicMock`` implements ``__fspath__`` via its
    # auto-stubbing, so ``Path(mock)`` would silently build a junk path
    # and loguru would write files relative to CWD. Reject anything that
    # isn't an honest Path or str up front — a loud failure is safer than
    # a phantom log file written into the repo root.
    if not isinstance(log_dir, (Path, str)):
        raise TypeError(f"log_dir must be a Path or str, got {type(log_dir).__name__}")
    log_dir = Path(log_dir)

    logger.remove()

    console_level = "DEBUG" if verbose else "INFO"
    logger.add(
        sys.stderr,
        level=console_level,
        colorize=sys.stderr.isatty() and not bool(os.environ.get("NO_COLOR")),
        format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    )

    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_dir / "the_architect.log",
        level="DEBUG",
        rotation="10 MB",
        format="{time} | {level} | {name}:{line} | {message}",
        retention=10,
    )


# ---------------------------------------------------------------------------
# stdbuf detection
# ---------------------------------------------------------------------------


def has_stdbuf() -> bool:
    """Check if stdbuf is available on this system.

    Returns:
        True if stdbuf is found in PATH, False otherwise.
    """
    return shutil.which("stdbuf") is not None


# ---------------------------------------------------------------------------
# Command building
# ---------------------------------------------------------------------------


def opencode_path_for_command() -> str:
    """Return the resolved path to the opencode binary, or 'opencode' as fallback.

    Uses shutil.which() to resolve the absolute path so the correct binary
    is invoked regardless of how opencode was installed (npm global, npx,
    brew, local project install, nvm-managed node).

    Returns:
        Absolute path string, or 'opencode' if not found (subprocess will
        raise FileNotFoundError with a clear message).
    """
    return shutil.which("opencode") or "opencode"


def build_opencode_command(
    instruction: str,
    model_override: str | None = None,
    agent_override: str | None = None,
) -> list[str]:
    """Build the opencode run command as a list of arguments.

    Uses ``--format json`` so opencode emits structured JSON events that
    The Architect can parse for token usage and display rendering.  Each line
    of stdout is a JSON event; non-JSON lines are printed as-is.

    Args:
        instruction: The instruction string to pass to opencode run.
        model_override: Optional model name to pass via --model flag.
        agent_override: Optional agent name to pass via --agent flag.

    Returns:
        List of command components ready for subprocess execution.
    """
    opencode_bin = opencode_path_for_command()
    cmd: list[str] = [opencode_bin, "run", "--format", "json", "--dangerously-skip-permissions"]

    if model_override:
        cmd.extend(["--model", model_override])

    if agent_override:
        cmd.extend(["--agent", agent_override])

    cmd.extend(["--", instruction])

    return cmd


# ---------------------------------------------------------------------------
# Subprocess streaming
# ---------------------------------------------------------------------------


class StreamResult(BaseModel):
    """Result of running opencode — exit code, tokens, accumulated text, rate-limit signal."""

    exit_code: int = Field(description="Exit code of the opencode subprocess")
    tokens: TokenUsage = Field(
        default_factory=TokenUsage,
        description="Accumulated token usage from all step_finish events",
    )
    accumulated_text: str = Field(
        default="",
        description=(
            "All text content produced by the agent during the run (from 'text' JSON events)"
        ),
    )
    rate_limit_hit: bool = Field(
        default=False,
        description=(
            "True if a rate-limit error was detected during the run (429, rate_limit, etc.)"
        ),
    )
    cooldown_until: int = Field(
        default=0,
        description=(
            "Unix timestamp when the provider cooldown resets (from rate_limit_event.resetsAt). "
            "0 means not set.  When non-zero, the caller should wait "
            "until this time before retrying."
        ),
    )
    interrupted: bool = Field(
        default=False,
        description="True when the provider subprocess was terminated by the runner.",
    )
    interruption_reason: str = Field(
        default="",
        description="Human-readable reason for a runner-terminated provider subprocess.",
    )


# ---------------------------------------------------------------------------
# Live subprocess registry (shutdown aid)
# ---------------------------------------------------------------------------
#
# Every call to :func:`stream_provider` registers its child subprocess
# here for the duration of the run. When the TUI is torn down (user
# hit Ctrl+C, app exited), the outer runner calls
# :func:`kill_active_subprocesses` to terminate anything the worker
# thread left behind. Without this registry, a daemon worker thread
# abandoned by `App.run()` returning would leave the opencode / claude
# subprocess running in the background — the exact symptom issue 2
# was reporting.

_ACTIVE_PROCS: set[asyncio.subprocess.Process] = set()
_ACTIVE_PROCS_LOCK = threading.Lock()

# Registry of task prefixes whose last failure was caused exclusively by a
# sleep/wake gap (not a real agent error).  The Infinite Loop driver reads
# this after a failed run to decide whether to reset those tasks to Pending
# and continue the loop rather than dying.  Cleared at the start of each
# ``run_task`` call so stale entries from previous iterations don't linger.
_SLEEP_INTERRUPTED_TASKS: set[str] = set()
_SLEEP_INTERRUPTED_TASKS_LOCK = threading.Lock()


def get_sleep_interrupted_tasks() -> frozenset[str]:
    """Return the set of task prefixes interrupted only by sleep/wake gaps.

    Thread-safe snapshot. Used by the Infinite Loop driver to detect
    runs that failed solely due to the host machine sleeping, so it can
    reset those tasks to Pending and continue instead of exiting.
    """
    with _SLEEP_INTERRUPTED_TASKS_LOCK:
        return frozenset(_SLEEP_INTERRUPTED_TASKS)


def _mark_sleep_interrupted(task_prefix: str) -> None:
    """Record that ``task_prefix`` was killed only by a sleep/wake gap."""
    with _SLEEP_INTERRUPTED_TASKS_LOCK:
        _SLEEP_INTERRUPTED_TASKS.add(task_prefix)


def _clear_sleep_interrupted(task_prefix: str) -> None:
    """Clear the sleep-interrupted flag for ``task_prefix`` (e.g. on success)."""
    with _SLEEP_INTERRUPTED_TASKS_LOCK:
        _SLEEP_INTERRUPTED_TASKS.discard(task_prefix)


def _register_process(proc: asyncio.subprocess.Process) -> None:
    with _ACTIVE_PROCS_LOCK:
        _ACTIVE_PROCS.add(proc)


def _unregister_process(proc: asyncio.subprocess.Process) -> None:
    with _ACTIVE_PROCS_LOCK:
        _ACTIVE_PROCS.discard(proc)


def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Kill ``proc`` and its process group (best-effort, cross-platform).

    Uses ``SIGKILL`` (Unix) / forceful ``TerminateProcess`` (Windows
    via ``proc.kill``) rather than ``SIGTERM`` because this function
    is only called from shutdown paths — the user has already hit
    Ctrl+C or pressed "Exit" and expects the backend to stop *now*,
    not after the provider decides to flush its buffers. The few
    seconds that SIGTERM saved when everything was healthy weren't
    worth the "Ctrl+C doesn't actually kill opencode" bug it caused
    when the provider was mid-long-running tool call.

    Unix: the process was spawned with ``start_new_session=True``,
    so ``os.killpg`` terminates the whole session including any
    grandchildren (npm → node → opencode helpers). Windows: fall
    back to ``proc.kill`` which forcefully terminates the single
    process (Windows job-object handling is a larger fix).
    """
    if proc.returncode is not None:
        return
    # Try process group first on POSIX so we also kill helper
    # processes the provider spawned (sandboxes, editors, etc.).
    if hasattr(os, "killpg") and hasattr(os, "getpgid"):
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        except Exception:
            pass
    # Always call proc.kill as a backstop (also covers Windows and
    # the rare case where os.killpg failed but the process itself
    # is still reachable via its direct pid).
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    except Exception:
        pass


def kill_active_subprocesses() -> int:
    """Terminate every subprocess currently tracked by the runner.

    Called from the TUI shutdown path so that pressing Ctrl+C in the
    Textual app really does stop the backend provider, not just hide
    the UI. Returns the number of processes it attempted to kill.
    """
    with _ACTIVE_PROCS_LOCK:
        procs = list(_ACTIVE_PROCS)
    for proc in procs:
        _kill_process_tree(proc)
    return len(procs)


def _provider_idle_timeout_seconds() -> float:
    """Return provider stdout idle timeout, allowing env override for long runs."""
    raw = os.environ.get("ARCHITECT_PROVIDER_IDLE_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return _PROVIDER_IDLE_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "Invalid ARCHITECT_PROVIDER_IDLE_TIMEOUT_SECONDS value "
            f"{raw!r}; using default {int(_PROVIDER_IDLE_TIMEOUT_SECONDS)}s"
        )
        return _PROVIDER_IDLE_TIMEOUT_SECONDS
    return max(0.0, value)


def _provider_sleep_wake_gap_seconds() -> float:
    """Return wall-clock gap threshold used to detect computer sleep/wake pauses."""
    raw = os.environ.get("ARCHITECT_SLEEP_WAKE_GAP_SECONDS", "").strip()
    if not raw:
        return _PROVIDER_SLEEP_WAKE_GAP_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "Invalid ARCHITECT_SLEEP_WAKE_GAP_SECONDS value "
            f"{raw!r}; using default {int(_PROVIDER_SLEEP_WAKE_GAP_SECONDS)}s"
        )
        return _PROVIDER_SLEEP_WAKE_GAP_SECONDS
    return max(0.0, value)


def _provider_read_probe_seconds(
    idle_timeout_seconds: float, sleep_wake_gap_seconds: float
) -> float:
    """Return short readline probe interval for idle and sleep/wake detection."""
    candidates = [_PROVIDER_READ_PROBE_SECONDS]
    if idle_timeout_seconds > 0:
        candidates.append(idle_timeout_seconds)
    if sleep_wake_gap_seconds > 0:
        candidates.append(sleep_wake_gap_seconds)
    return max(0.01, min(candidates))


async def stream_provider(
    instruction: str,
    project_dir: Path,
    provider: ArchitectProvider,
    model_override: str | None = None,
    agent_override: str | None = None,
    log_path: Path | None = None,
    config_override: Path | None = None,
    on_first_output: Callable[[], None] | None = None,
    renderer: StreamRenderer | None = None,
) -> StreamResult:
    """Run any supported AI CLI provider, parse output, and render to terminal.

    This is the provider-agnostic core of The Architect's execution engine.
    It works with both OpenCode (JSON events) and Claude Code (plain text)
    by delegating output parsing to ``provider.parse_output_line()``.

    For OpenCode:
      - Invokes ``opencode run --format json --dangerously-skip-permissions``
      - Each stdout line is a JSON event; parsed for tokens + display text
      - ``config_override`` sets ``OPENCODE_CONFIG`` for planning runs

    For Claude Code:
      - Invokes ``claude --dangerously-skip-permissions --print <instruction>``
      - Each stdout line is plain text; wrapped as a ``"text"`` event
      - Token counts are not available (no structured output)
      - ``config_override`` is ignored (Claude Code has no config file override)

    Args:
        instruction: The instruction string to execute.
        project_dir: The project root directory (cwd for the provider).
        provider: The :class:`~the_architect.core.provider.ArchitectProvider`
            to use for command building and output parsing.
        model_override: Optional model name to pass via --model flag.
        agent_override: Optional agent name (only used by providers that
            support named agents, e.g. OpenCode).
        log_path: Optional path to write the raw output log.
        config_override: Provider-specific config override (e.g. OpenCode's
            ``OPENCODE_CONFIG``).  Passed to ``provider.get_env_overrides()``.

    Returns:
        StreamResult with exit code, accumulated token usage, and accumulated
        agent text output (for completion promise detection).

    Raises:
        FileNotFoundError: If *project_dir* does not exist.
    """
    project_dir = project_dir.resolve()
    if not project_dir.is_dir():
        raise FileNotFoundError(f"Project directory does not exist: {project_dir}")

    # For providers that don't support agents, ignore agent_override
    effective_agent = agent_override if provider.supports_agents() else None

    cmd = provider.build_command(instruction, model_override, effective_agent)

    # Determine instruction delivery: stdin pipe vs command-line argument.
    # Providers that set instruction_via_stdin=True do not include the
    # instruction in the command list — we write it to the process stdin
    # instead.  This is the correct solution for the Windows CreateProcess
    # command-line length limit (32 767 chars), which is reliably exceeded
    # when planning prompts + ARCHITECT.md + execution-protocol.md are all
    # concatenated into one argument (FileNotFoundError error 206 on Windows).
    _use_stdin = getattr(provider, "instruction_via_stdin", False)
    _stdin_mode = asyncio.subprocess.PIPE if _use_stdin else None

    # Warn when passing a large instruction as a command-line argument on any
    # platform so operators are alerted before hitting OS limits.
    _WIN_CMDLINE_LIMIT = 32_767
    if not _use_stdin and len(instruction) > _WIN_CMDLINE_LIMIT // 2:
        logger.warning(
            f"Instruction for {provider.display_name} is {len(instruction)} chars — "
            f"approaching the Windows CreateProcess command-line limit of {_WIN_CMDLINE_LIMIT}. "
            "Consider enabling instruction_via_stdin on the provider."
        )

    # Build environment: inherit parent env + provider-specific overrides
    env = {
        **os.environ.copy(),
        "OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS": "900000",
    }

    if "PATH" not in env or not env.get("PATH"):
        logger.warning(
            "PATH is not set in subprocess environment — provider binary may not be found. "
            "Set PATH explicitly or use an absolute path."
        )

    # Apply provider-specific env overrides (e.g. OPENCODE_CONFIG for planning)
    env.update(provider.get_env_overrides(config_override))

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)

    logger.debug(f"Running {provider.display_name} in {project_dir}: {' '.join(cmd[:6])}...")

    _SUBPROCESS_READ_LIMIT = 10 * 1024 * 1024  # 10 MB

    accumulated_tokens = TokenUsage()
    accumulated_text_parts: list[str] = []
    rate_limit_detected: bool = False
    cooldown_until: int = 0  # Unix timestamp from rate_limit_event.resetsAt (0 = not set)
    process: asyncio.subprocess.Process | None = None
    exit_code: int = -1
    first_output_fired: bool = False
    idle_timeout_seconds = _provider_idle_timeout_seconds()
    sleep_wake_gap_seconds = _provider_sleep_wake_gap_seconds()
    read_probe_seconds = _provider_read_probe_seconds(
        idle_timeout_seconds,
        sleep_wake_gap_seconds,
    )
    interrupted: bool = False
    interruption_reason: str = ""

    def _fire_first_output() -> None:
        """Fire the on_first_output callback exactly once, swallowing errors."""
        nonlocal first_output_fired
        if first_output_fired or on_first_output is None:
            return
        first_output_fired = True
        try:
            on_first_output()
        except Exception as exc:
            logger.debug(f"on_first_output callback raised: {exc!r}")

    try:
        render = renderer or PlainStreamRenderer()
        # Spawn in a new session on POSIX so we can kill the whole
        # process group (opencode / claude → its own children)
        # cleanly when the user hits Ctrl+C. On Windows,
        # ``start_new_session`` isn't supported by asyncio, so we
        # just omit it and rely on ``proc.kill()`` in shutdown.
        _spawn_kwargs: dict[str, Any] = {}
        if os.name == "posix":
            _spawn_kwargs["start_new_session"] = True
        process = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            cwd=str(project_dir),
            stdin=_stdin_mode,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,
            limit=_SUBPROCESS_READ_LIMIT,
            **_spawn_kwargs,
        )
        _register_process(process)

        # When the provider reads from stdin, write the instruction and
        # close the write end so the CLI sees EOF and starts processing.
        # asyncio.StreamWriter.write() is synchronous; drain() and
        # wait_closed() are coroutines.
        if _use_stdin and process.stdin is not None:
            try:
                process.stdin.write(instruction.encode("utf-8"))
                await process.stdin.drain()
                process.stdin.close()
                await process.stdin.wait_closed()
            except Exception as stdin_exc:
                logger.debug(f"stdin write failed: {stdin_exc!r}")

        if process.stdout is None:
            raise RuntimeError(f"Failed to capture {provider.display_name} stdout")

        stdout_reader = process.stdout

        async def _read_stdout() -> None:
            """Read stdout line by line, parse events, render to terminal, and log."""
            nonlocal accumulated_tokens
            nonlocal rate_limit_detected
            nonlocal cooldown_until
            nonlocal interrupted
            nonlocal interruption_reason

            log_file = None
            if log_path is not None:
                try:
                    log_file = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
                except OSError:
                    log_file = None

            try:
                last_output_wall = time.time()
                last_probe_wall = last_output_wall
                while True:
                    try:
                        if idle_timeout_seconds <= 0 and sleep_wake_gap_seconds <= 0:
                            line_bytes = await stdout_reader.readline()
                        else:
                            line_bytes = await asyncio.wait_for(
                                stdout_reader.readline(),
                                timeout=read_probe_seconds,
                            )
                    except TimeoutError:
                        now_wall = time.time()
                        wall_gap = now_wall - last_probe_wall
                        idle_elapsed = now_wall - last_output_wall
                        last_probe_wall = now_wall

                        if sleep_wake_gap_seconds > 0 and wall_gap >= sleep_wake_gap_seconds:
                            message = (
                                "Provider execution paused for "
                                f"{int(wall_gap)}s, likely because the computer slept or was "
                                "suspended; terminating stale subprocess so the attempt can retry."
                            )
                            interrupted = True
                            interruption_reason = "sleep_wake_gap"
                            accumulated_text_parts.append(message)
                            logger.warning(message)
                            if process is not None and process.returncode is None:
                                _kill_process_tree(process)
                            break

                        if idle_timeout_seconds > 0 and idle_elapsed >= idle_timeout_seconds:
                            message = (
                                f"Provider produced no stdout for {int(idle_timeout_seconds)}s; "
                                "terminating stalled subprocess."
                            )
                            interrupted = True
                            interruption_reason = "idle_timeout"
                            accumulated_text_parts.append(message)
                            logger.warning(message)
                            if process is not None and process.returncode is None:
                                _kill_process_tree(process)
                            break

                        await asyncio.sleep(0)
                        continue
                    if not line_bytes:
                        break
                    last_output_wall = time.time()
                    last_probe_wall = last_output_wall
                    line = line_bytes.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")

                    # Append raw line to log file
                    if log_file is not None:
                        try:
                            log_file.write(line + "\n")
                            log_file.flush()
                        except OSError:
                            pass

                    # Delegate parsing to the provider
                    parsed = provider.parse_output_line(line)
                    if parsed is not None:
                        # Accumulate token usage
                        if parsed.tokens is not None:
                            accumulated_tokens = accumulated_tokens + parsed.tokens  # type: ignore[operator]

                        # Accumulate agent text for promise detection.
                        # OpenCode emits event_type="text"; Claude Code emits
                        # event_type="assistant".  Accept both so accumulated_text
                        # is populated regardless of provider — this is required for
                        # cooldown detection, retry context, and promise scanning.
                        if parsed.event_type in ("text", "assistant") and parsed.display_lines:
                            accumulated_text_parts.append("\n".join(parsed.display_lines))
                        elif parsed.display_lines and (
                            parsed.event_type in ("error", "result")
                            or parsed.rate_limit
                            or parsed.model_not_found
                        ):
                            accumulated_text_parts.append("\n".join(parsed.display_lines))
                        elif (parsed.rate_limit or parsed.model_not_found) and line.strip():
                            accumulated_text_parts.append(line)

                        # Render display lines to terminal.  Fire the
                        # on_first_output callback the first time we are
                        # about to write user-visible output so the spinner
                        # disappears exactly when real content starts.
                        if parsed.display_lines:
                            _fire_first_output()
                        for dl in parsed.display_lines:
                            render.write_line(dl)

                        # Rate-limit / model-not-found detection.
                        # Also capture resetsAt from rate_limit_event for precise cooldown timing.
                        if parsed.rate_limit or parsed.model_not_found:
                            if not rate_limit_detected:
                                rate_limit_detected = True
                                if parsed.rate_limit:
                                    logger.warning("Rate-limit signal detected mid-stream")
                            # Capture the provider's reset timestamp when present
                            if parsed.cooldown_until and parsed.cooldown_until > cooldown_until:
                                cooldown_until = parsed.cooldown_until
                            else:
                                logger.warning("Model-not-found signal detected mid-stream")
                    else:
                        # Provider returned None → print raw line as-is
                        if line.strip():
                            _fire_first_output()
                            render.write_line(line)

            except asyncio.CancelledError:
                pass
            except ValueError as exc:
                logger.warning(f"Stdout line exceeded buffer limit, stopping reader: {exc}")
            except Exception as exc:
                logger.error(
                    f"Unexpected error reading {provider.display_name} stdout, "
                    f"stopping reader: {exc!r}"
                )
            finally:
                if log_file is not None:
                    try:
                        log_file.close()
                    except OSError:
                        pass

        # Read stdout in the background while the process runs
        reader_task = asyncio.create_task(_read_stdout())
        exit_code = await process.wait()
        # Give the reader a moment to finish processing remaining lines.
        # 30 s is generous — the reader should drain the pipe buffer almost
        # instantly now that render.write_line is non-blocking.  The larger
        # budget guards against any future slow path without silently
        # discarding the tail of a provider's output.
        try:
            await asyncio.wait_for(reader_task, timeout=30.0)
        except TimeoutError:
            reader_task.cancel()
        if interrupted and exit_code == 0:
            exit_code = _FORCED_TERMINATION_EXIT_CODE

    except FileNotFoundError:
        raise
    except asyncio.CancelledError:
        # Propagated when the caller (or the TUI shutdown path) cancels
        # the enclosing task. Kill the subprocess before re-raising so
        # we don't leak a running provider into the background.
        if process is not None and process.returncode is None:
            _kill_process_tree(process)
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except Exception:
                pass
        raise
    except Exception as exc:
        logger.error(f"Failed to run {provider.display_name} subprocess: {exc!r}")
        exit_code = -1

        if process is not None and process.returncode is None:
            try:
                process.kill()
                await process.wait()
            except Exception:
                pass
    finally:
        # Always release the subprocess registration. If the process
        # is still alive (e.g. the user hit Ctrl+C and the outer
        # shutdown path is racing with this one), terminate it — we
        # must never return to the caller with a live provider still
        # running.
        if process is not None:
            if process.returncode is None:
                _kill_process_tree(process)
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except Exception:
                    pass
            _unregister_process(process)

    try:
        render.close()
    except Exception:
        pass

    return StreamResult(
        exit_code=exit_code,
        tokens=accumulated_tokens,
        accumulated_text="\n".join(accumulated_text_parts),
        rate_limit_hit=rate_limit_detected,
        cooldown_until=cooldown_until,
        interrupted=interrupted,
        interruption_reason=interruption_reason,
    )


async def stream_opencode(
    instruction: str,
    project_dir: Path,
    model_override: str | None = None,
    agent_override: str | None = None,
    log_path: Path | None = None,
    config_override: Path | None = None,
) -> StreamResult:
    """Run opencode — backward-compat shim that delegates to :func:`stream_provider`.

    All new code should call :func:`stream_provider` directly and pass an
    explicit provider.  This shim exists so existing call sites continue
    to work without modification.

    Args:
        instruction: The instruction string to execute.
        project_dir: The project root directory (cwd for opencode).
        model_override: Optional model name to pass via --model flag.
        agent_override: Optional agent name to pass via --agent flag.
        log_path: Optional path to write raw JSON event log.
        config_override: If set, passed as ``OPENCODE_CONFIG`` env var.

    Returns:
        StreamResult with exit code, accumulated token usage, and accumulated
        agent text output (for completion promise detection).

    Raises:
        FileNotFoundError: If *project_dir* does not exist.
    """
    from the_architect.core.opencode_provider import OpenCodeProvider

    return await stream_provider(
        instruction=instruction,
        project_dir=project_dir,
        provider=OpenCodeProvider(),
        model_override=model_override,
        agent_override=agent_override,
        log_path=log_path,
        config_override=config_override,
    )


def _tool_result_lines(
    tool_name: str,
    output: str,
    metadata: dict[str, object],
    title: str,
) -> list[str]:
    """Build display lines for a completed tool call's result.

    Returns one or more lines that, combined with the tool call line,
    show the user what the tool produced — matching OpenCode's own
    display as closely as possible.

    The first line is appended after ``←`` on the tool call line.
    Subsequent lines are shown indented on their own lines.

    Args:
        tool_name: The tool that was called (read, glob, grep, etc.).
        output: The raw output string from the tool.
        metadata: The metadata dict from the tool state (may be empty).
        title: The short title from the tool state (may be empty).

    Returns:
        List of result lines (may be empty if nothing useful to show).
    """
    # ── Tool-specific result rendering ──────────────────────────────

    if tool_name == "glob" and isinstance(metadata, dict):
        count = metadata.get("count")
        if isinstance(count, (int, float)) and int(count) >= 0:
            truncated = " (truncated)" if metadata.get("truncated") else ""
            return [f"{int(count)} match{'es' if int(count) != 1 else ''}{truncated}"]

    if tool_name == "grep" and isinstance(metadata, dict):
        matches = metadata.get("matches")
        if isinstance(matches, (int, float)) and int(matches) >= 0:
            truncated = " (truncated)" if metadata.get("truncated") else ""
            return [f"{int(matches)} match{'es' if int(matches) != 1 else ''}{truncated}"]

    if tool_name in ("read", "view"):
        # For read/view, show the file preview (from metadata) or
        # the first few lines of the output
        if isinstance(metadata, dict):
            preview = metadata.get("preview")
            if isinstance(preview, str) and preview.strip():
                preview_lines = preview.strip().split("\n")
                # Show up to 5 lines of preview
                shown = preview_lines[:5]
                result = [shown[0][:120]]  # First line is the summary
                for pl in shown[1:]:
                    result.append(pl[:120])
                if len(preview_lines) > 5:
                    result.append(f"… ({len(preview_lines) - 5} more lines)")
                return result
        # Fallback: show first few lines of raw output
        output_str = str(output).strip() if output else ""
        if output_str:
            output_lines = output_str.split("\n")
            shown = output_lines[:3]
            result = [shown[0][:120]]
            for ol in shown[1:]:
                result.append(ol[:120])
            if len(output_lines) > 3:
                result.append(f"… ({len(output_lines) - 3} more lines)")
            return result
        return []

    if tool_name in ("write", "edit"):
        output_str = str(output).strip() if output else ""
        if "success" in output_str.lower() or "wrote" in output_str.lower():
            return [output_str.split("\n")[0][:80]]
        return ["done"]

    if tool_name == "bash":
        # Show bash output — up to 10 lines, then truncate
        output_str = str(output).strip() if output else ""
        if output_str:
            output_lines = output_str.split("\n")
            shown = output_lines[:10]
            result = []
            for ol in shown:
                result.append(ol[:200])
            if len(output_lines) > 10:
                result.append(f"… (+{len(output_lines) - 10} more lines)")
            return result
        return []

    if tool_name == "todowrite":
        # Parse the todo items from the output and show them
        output_str = str(output).strip() if output else ""
        if output_str:
            # The output is typically JSON with a todos array
            import json as _json

            try:
                data = _json.loads(output_str)
                if isinstance(data, dict) and "todos" in data:
                    todos = data["todos"]
                    if isinstance(todos, list):
                        result = []
                        for todo in todos[:15]:
                            if isinstance(todo, dict):
                                status_icon = "✓" if todo.get("status") == "completed" else "○"
                                content = str(todo.get("content", ""))[:80]
                                result.append(f"{status_icon} {content}")
                            else:
                                result.append(str(todo)[:80])
                        if len(todos) > 15:
                            result.append(f"… (+{len(todos) - 15} more)")
                        return result
            except (ValueError, _json.JSONDecodeError):
                pass
            # Fallback: show first few lines
            output_lines = output_str.split("\n")
            return [ol[:120] for ol in output_lines[:5]]

    # ── Generic fallback ────────────────────────────────────────────
    if title and str(title).strip():
        return [str(title).strip()[:120]]

    output_str = str(output).strip() if output else ""
    if output_str:
        first_line = output_str.split("\n")[0][:120]
        return [first_line]

    return []


def _parse_opencode_event(line: str) -> tuple[str, list[str], TokenUsage | None] | None:
    """Parse a single opencode JSON event line.

    Supports both the current opencode v1.4+ format (``text``, ``tool_use``,
    ``step_start``, ``step_finish``) and the legacy format (``assistant``,
    ``tool``, ``error``) for backward compatibility.

    Returns ``(event_type, display_lines, token_usage)`` or ``None``
    if the line is not valid JSON.  ``display_lines`` is a list of
    strings to show in the TUI — empty list for events that shouldn't
    be displayed (e.g. internal state changes).  Multi-line tool
    output (bash results, file previews, todo lists) produces
    multiple display lines so the user sees exactly what OpenCode
    would show.

    Args:
        line: A single line from opencode's JSON output.

    Returns:
        Parsed tuple or None if not a JSON event.
    """
    import json as _json

    try:
        event = _json.loads(line)
    except (ValueError, _json.JSONDecodeError):
        return None

    etype = event.get("type", "")
    lines: list[str] = []
    tokens: TokenUsage | None = None

    # ── Token usage extraction ──────────────────────────────────────
    # v1.4+ format: tokens are inside event["part"]["tokens"]
    # Legacy format: tokens are at event["usage"]
    part = event.get("part", {})
    if isinstance(part, dict):
        part_tokens = part.get("tokens")
        if isinstance(part_tokens, dict):
            cache = part_tokens.get("cache", {})
            tokens = TokenUsage(
                input_tokens=part_tokens.get("input", 0),
                output_tokens=part_tokens.get("output", 0),
                cache_read_tokens=cache.get("read", 0) if isinstance(cache, dict) else 0,
                cache_write_tokens=cache.get("write", 0) if isinstance(cache, dict) else 0,
            )

    if tokens is None:
        usage = event.get("usage")
        if isinstance(usage, dict):
            tokens = TokenUsage(
                input_tokens=int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
                output_tokens=int(
                    usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
                ),
                cache_read_tokens=usage.get("cache_read_input_tokens", 0),
                cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
            )

    # ── Display text extraction ─────────────────────────────────────
    # v1.4+ format
    if etype == "text":
        # Text content: {"type":"text","part":{"text":"..."}}
        # Agent text can be multi-line — split into individual lines
        t = (part.get("text") or "").strip() if isinstance(part, dict) else ""
        if t:
            lines.extend(t.split("\n"))

    elif etype == "tool_use":
        # Tool call: {"type":"tool_use","part":{"tool":"read","state":{"input":{...}}}}
        # opencode uses camelCase for input fields (filePath, not file_path).
        # The state also includes "title" (short label), "metadata" (result
        # counts / previews), and "output" (full result text).
        tool_name = (part.get("tool") or "") if isinstance(part, dict) else ""
        state = part.get("state", {}) if isinstance(part, dict) else {}
        inp = state.get("input", {}) if isinstance(state, dict) else {}
        status = state.get("status", "") if isinstance(state, dict) else ""
        output = state.get("output", "") if isinstance(state, dict) else ""
        tool_title = state.get("title", "") if isinstance(state, dict) else ""
        metadata = state.get("metadata", {}) if isinstance(state, dict) else {}

        # Helper: opencode uses both camelCase and snake_case for input
        # field names depending on version.  Check both variants.
        def _inp(key: str, alt: str = "") -> str:
            """Get input value, checking both camelCase and snake_case keys."""
            val = inp.get(key, "")
            if not val and alt:
                val = inp.get(alt, "")
            return str(val) if val else ""

        # ── Build the tool call line ────────────────────────────────
        call_line = ""
        if tool_name in ("write", "edit"):
            path = _inp("path", "filePath") or _inp("file_path")
            call_line = f"→ {tool_name} {path}"
        elif tool_name == "bash":
            cmd_str = _inp("command")[:80]
            call_line = f"$ {cmd_str}"
        elif tool_name in ("read", "view"):
            path = _inp("filePath", "file_path") or _inp("path")
            offset = inp.get("offset")
            limit = inp.get("limit")
            detail = str(path)
            if offset is not None or limit is not None:
                detail += f" (L{offset or 0}"
                if limit:
                    detail += f"-{int(offset or 0) + int(limit)}"
                detail += ")"
            call_line = f"→ {tool_name} {detail}"
        elif tool_name == "glob":
            pattern = _inp("pattern")
            path = _inp("path")
            detail = str(pattern)
            if path:
                detail += f" in {path}"
            call_line = f"→ {tool_name} {detail}"
        elif tool_name == "grep":
            pattern = _inp("pattern")
            include = _inp("include")
            path = _inp("path")
            detail = f'"{pattern}"'
            if include:
                detail += f" ({include})"
            if path:
                detail += f" in {path}"
            call_line = f"→ {tool_name} {detail}"
        elif tool_name == "ls":
            path = _inp("path")
            call_line = f"→ ls {path}" if path else "→ ls"
        elif tool_name == "fetch":
            url = _inp("url")
            call_line = f"→ fetch {url}" if url else "→ fetch"
        elif tool_name == "diagnostics":
            fpath = _inp("filePath", "file_path")
            call_line = f"→ diagnostics {fpath}" if fpath else "→ diagnostics"
        elif tool_name == "sourcegraph":
            query = _inp("query")
            call_line = f'→ sourcegraph "{query}"' if query else "→ sourcegraph"
        elif tool_name == "todowrite":
            call_line = "→ todowrite"
        elif tool_name == "agent":
            prompt_preview = _inp("prompt")[:60]
            call_line = f"→ agent {prompt_preview}" if prompt_preview else "→ agent"
        elif tool_name:
            # Generic fallback — show tool name + first input value
            first_val = ""
            if isinstance(inp, dict):
                for _k, v in inp.items():
                    if v and str(v).strip():
                        first_val = str(v)[:60]
                        break
            call_line = f"→ {tool_name} {first_val}" if first_val else f"→ {tool_name}"

        if not call_line:
            return (etype, [], tokens)

        # ── Build result lines when tool completes ──────────────────
        if status == "completed":
            result_lines = _tool_result_lines(tool_name, output, metadata, tool_title)
            if result_lines:
                # First result line goes on same line as call: "→ read foo.py  ← 24 matches"
                lines.append(f"{call_line}  ← {result_lines[0]}")
                # Subsequent result lines go on their own lines
                for rl in result_lines[1:]:
                    lines.append(f"  {rl}")
            else:
                lines.append(call_line)
        else:
            # Tool is still running — just show the call
            lines.append(call_line)

    elif etype == "step_start":
        # Step start — no display text needed
        pass

    elif etype == "step_finish":
        # Step finish — no display text needed (tokens extracted above)
        pass

    # Legacy format (older opencode versions)
    elif etype == "assistant":
        for content_part in event.get("message", {}).get("content", []):
            if content_part.get("type") == "text":
                t = content_part.get("text", "").strip()
                if t:
                    lines.extend(t.split("\n"))
                    break
    elif etype == "tool":
        tool = event.get("tool", {})
        name = tool.get("name", "")
        inp = tool.get("input", {})

        # Helper for legacy format — also check camelCase variants
        def _leg_inp(key: str, alt: str = "") -> str:
            """Get input value, checking both camelCase and snake_case keys."""
            val = inp.get(key, "")
            if not val and alt:
                val = inp.get(alt, "")
            return str(val) if val else ""

        call_line = ""
        if name in ("write", "edit"):
            path = _leg_inp("path", "filePath") or _leg_inp("file_path")
            call_line = f"→ {name} {path}"
        elif name == "bash":
            cmd_str = _leg_inp("command")[:80]
            call_line = f"$ {cmd_str}"
        elif name in ("read", "view"):
            path = _leg_inp("filePath", "file_path") or _leg_inp("path")
            call_line = f"→ {name} {path}"
        elif name == "glob":
            pattern = _leg_inp("pattern")
            path = _leg_inp("path")
            detail = str(pattern)
            if path:
                detail += f" in {path}"
            call_line = f"→ {name} {detail}"
        elif name == "grep":
            pattern = _leg_inp("pattern")
            include = _leg_inp("include")
            path = _leg_inp("path")
            detail = f'"{pattern}"'
            if include:
                detail += f" ({include})"
            if path:
                detail += f" in {path}"
            call_line = f"→ {name} {detail}"
        elif name == "ls":
            path = _leg_inp("path")
            call_line = f"→ ls {path}" if path else "→ ls"
        elif name == "fetch":
            url = _leg_inp("url")
            call_line = f"→ fetch {url}" if url else "→ fetch"
        elif name:
            first_val = ""
            if isinstance(inp, dict):
                for _k, v in inp.items():
                    if v and str(v).strip():
                        first_val = str(v)[:60]
                        break
            call_line = f"→ {name} {first_val}" if first_val else f"→ {name}"

        if call_line:
            lines.append(call_line)

    # Error handling (both formats)
    if etype == "error":
        lines.append(f"Error: {event.get('message', event.get('error', ''))}")

    return (etype, lines, tokens)


# ---------------------------------------------------------------------------
# Instruction builder
# ---------------------------------------------------------------------------


def is_task_complete(
    task_prefix: str,
    output_analysis: OutputAnalysis,
    progress_done: bool,
    exit_code: int,
) -> tuple[bool, list[str]]:
    """Determine task completion using multiple corroborating signals (IMP-06).

    Uses four independent signals to decide whether a task is complete.
    Requires at least 2 positive signals, with special handling for strong
    single signals (promise tag, PROGRESS.md) to maintain backward
    compatibility.

    Signals:
        1. **Promise tag** — agent output contained ``<promise>TXX_COMPLETE</promise>``
        2. **PROGRESS.md** — PROGRESS.md shows Done for this task prefix
        3. **Clean exit** — opencode exited with code 0
        4. **Progress signal** — agent said "all tests pass", "task complete" etc.

    Rules:
        - 2+ positive signals → Done
        - Promise tag alone → Done (strong, explicit, agent-declared)
        - PROGRESS.md alone → Done with warning (backward compat, but suspicious)
        - Exit code alone → NOT done (too weak — opencode exits 0 even on timeout)
        - Progress signal alone → NOT done (too weak — could be from earlier text)

    Args:
        task_prefix: The task prefix to check (e.g. ``T01``).
        output_analysis: Analysis of the agent's text output.
        progress_done: Whether PROGRESS.md shows Done for this task.
        exit_code: The opencode subprocess exit code.

    Returns:
        Tuple of (is_done, active_signals) where active_signals is a list
        of signal names that fired, for logging.
    """
    promise_match = task_prefix in output_analysis.completion_promises
    clean_exit = exit_code == 0
    has_progress = output_analysis.has_progress_signal

    active: list[str] = []
    if promise_match:
        active.append("promise tag")
    if progress_done:
        active.append("PROGRESS.md")
    if clean_exit:
        active.append("clean exit")
    if has_progress:
        active.append("progress signal")

    positive = len(active)

    # 2+ signals → unambiguously done
    if positive >= 2:
        return True, active

    # Promise tag alone → strong enough on its own
    if promise_match:
        return True, active

    # PROGRESS.md alone → trust it but warn (agent may have marked done prematurely)
    if progress_done:
        logger.warning(
            f"Task {task_prefix} marked Done in PROGRESS.md but no other "
            f"completion signal detected — possible false positive"
        )
        return True, active

    # Exit code or progress signal alone → not sufficient
    return False, active


def _try_parse_json(line: str) -> dict[str, Any] | None:
    """Attempt to parse a line as JSON. Returns None on failure.

    Args:
        line: A raw text line from the log file.

    Returns:
        Parsed dict, or None if the line is not valid JSON.
    """
    import json

    try:
        obj = json.loads(line)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def summarize_previous_attempt(log_path: Path) -> str:
    """Parse a previous attempt's JSON event log into a concise summary.

    Scans the log for tool events to extract:
    - Files written or edited
    - Files read
    - Bash commands run
    - Test failures (pytest FAILED/ERROR in bash output)

    This summary is injected into the retry instruction so the agent
    knows what was already done and what went wrong, without needing
    conversational memory.

    Args:
        log_path: Path to the previous attempt's ``.log`` file.

    Returns:
        A multi-line summary string, or empty string if the log doesn't
        exist or contains no useful events.
    """
    if not log_path.exists():
        return ""

    files_written: set[str] = set()
    files_read: set[str] = set()
    errors: list[str] = []
    bash_count = 0

    try:
        raw = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    for line in raw.splitlines():
        event = _try_parse_json(line)
        if event is None:
            continue

        part = event.get("part", {})
        if not isinstance(part, dict):
            continue

        tool = part.get("tool", "")
        state = part.get("state", {})
        if not isinstance(state, dict):
            continue

        status = state.get("status", "")
        if status != "completed":
            continue

        inp = state.get("input", {})
        if not isinstance(inp, dict):
            inp = {}

        if tool in ("write", "edit"):
            path = inp.get("filePath") or inp.get("path", "")
            if path:
                files_written.add(str(path))

        elif tool in ("read", "view"):
            path = inp.get("filePath") or inp.get("path", "")
            if path:
                files_read.add(str(path))

        elif tool == "bash":
            bash_count += 1
            cmd = inp.get("command", "")
            output = str(state.get("output", ""))
            # Detect test failures from pytest output
            if "pytest" in cmd and ("FAILED" in output or "ERROR" in output):
                # Extract just the FAILED lines (up to 3)
                failed_lines = [
                    ln.strip() for ln in output.splitlines() if ln.strip().startswith("FAILED")
                ][:3]
                if failed_lines:
                    errors.append("Test failures: " + "; ".join(failed_lines))
                else:
                    errors.append(f"pytest exited with errors (cmd: {cmd[:60]})")

    parts: list[str] = []
    if files_written:
        sorted_written = sorted(files_written)[:20]
        parts.append(f"Files written/edited: {', '.join(sorted_written)}")
    if errors:
        parts.append(f"Errors detected: {'; '.join(errors[:5])}")
    if bash_count:
        parts.append(f"Bash commands run: {bash_count}")
    if files_read and not files_written and not errors:
        # Only mention reads if there's nothing else — avoids noise
        parts.append(f"Files read: {', '.join(sorted(files_read)[:10])}")

    return "\n".join(parts)


def build_attempt_summary(
    task_id: str,
    attempt_number: int,
    log_path: Path,
    completion_detected: bool,
    total_tokens: int = 0,
    accumulated_text: str = "",
    exit_code: int = 0,
    rate_limit_hit: bool = False,
    cooldown_until: int = 0,
) -> AttemptSummary:
    """Build a structured :class:`AttemptSummary` for the circuit breaker.

    Parses the attempt's JSON event log to extract files written, bash
    commands run, and error text from failed bash commands.  Does NOT
    duplicate the parsing logic in :func:`summarize_previous_attempt` —
    both functions scan the same log but produce different output shapes.

    Args:
        task_id: Task prefix, e.g. ``T03``.
        attempt_number: 1-based attempt number.
        log_path: Path to the attempt's ``.log`` file.
        completion_detected: Whether the task was marked Done this attempt.
        total_tokens: Total tokens used (input + output); 0 if unavailable.
        accumulated_text: Full accumulated agent text output (for cooldown
            signal detection).
        exit_code: opencode subprocess exit code (for HTTP status detection).
        rate_limit_hit: True if the provider signalled a rate limit via a
            structured event (reliable even when accumulated_text is empty).
        cooldown_until: Unix timestamp when the provider cooldown resets
            (from rate_limit_event.resetsAt).  0 means not set.

    Returns:
        :class:`AttemptSummary` ready to pass to the circuit breaker.
    """
    from the_architect.core.circuit import AttemptSummary

    files_written: list[str] = []
    bash_errors: list[str] = []
    bash_commands_run = 0

    if log_path.exists():
        try:
            raw = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            raw = ""

        for line in raw.splitlines():
            event = _try_parse_json(line)
            if event is None:
                continue

            part = event.get("part", {})
            if not isinstance(part, dict):
                continue

            tool = part.get("tool", "")
            state = part.get("state", {})
            if not isinstance(state, dict):
                continue

            status = state.get("status", "")
            if status != "completed":
                continue

            inp = state.get("input", {})
            if not isinstance(inp, dict):
                inp = {}

            if tool in ("write", "edit"):
                path = inp.get("filePath") or inp.get("path", "")
                if path:
                    files_written.append(str(path))

            elif tool == "bash":
                bash_commands_run += 1
                output = str(state.get("output", ""))
                exit_code_raw = state.get("exit_code")
                # Treat non-zero exit code as a failed command
                failed = False
                if exit_code_raw is not None:
                    try:
                        failed = int(exit_code_raw) != 0
                    except (TypeError, ValueError):
                        pass
                # Also detect common error patterns in output even without exit code
                if not failed and output:
                    error_indicators = [
                        "error:",
                        "Error:",
                        "ERROR:",
                        "Traceback",
                        "FAILED",
                        "ModuleNotFoundError",
                        "ImportError",
                        "FileNotFoundError",
                        "PermissionError",
                        "SyntaxError",
                        "TypeError",
                    ]
                    failed = any(ind in output for ind in error_indicators)

                if failed and output.strip():
                    # Capture up to 500 chars of error output
                    bash_errors.append(output.strip()[:500])

    # Extract accumulated text from log for cooldown detection.
    # Caller-supplied accumulated_text takes priority (always use it when
    # available — it comes directly from stream_result and is always correct).
    # The log-file fallback handles the case where accumulated_text was not
    # passed (e.g. direct calls from tests or the circuit replan path).
    log_accumulated_text = accumulated_text  # caller-supplied takes priority
    if not log_accumulated_text and log_path.exists():
        text_parts: list[str] = []
        try:
            raw_for_text = log_path.read_text(encoding="utf-8", errors="replace")
            for line in raw_for_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                ev = _try_parse_json(line)
                if ev is not None:
                    # OpenCode JSON event format
                    if ev.get("type") == "text":
                        part = ev.get("part", {})
                        if isinstance(part, dict):
                            t = (part.get("text") or "").strip()
                            if t:
                                text_parts.append(t)
                    # Also capture error events (these carry rate-limit messages)
                    elif ev.get("type") == "error":
                        msg = str(ev.get("message", ev.get("error", ""))).strip()
                        if msg:
                            text_parts.append(msg)
                else:
                    # Plain text line (Claude Code output) — include directly
                    text_parts.append(line)
        except OSError:
            pass
        log_accumulated_text = "\n".join(text_parts)

    return AttemptSummary(
        task_id=task_id,
        attempt_number=attempt_number,
        completion_detected=completion_detected,
        files_written=files_written,
        bash_commands_run=bash_commands_run,
        bash_errors=bash_errors,
        total_tokens=total_tokens,
        accumulated_text=log_accumulated_text,
        exit_code=exit_code,
        rate_limit_hit=rate_limit_hit,
        cooldown_until=cooldown_until,
    )


def build_instruction(
    task: Task,
    attempt: int,
    config: ArchitectConfig,
    previous_summary: str = "",
    architect_md_content: str = "",
) -> str:
    """Build the instruction string for opencode run.

    Prepends the The Architect execution protocol so the user's agent understands
    how PROGRESS.md works, how Done is detected, and what rules to follow.
    Then gives the specific task instruction with a project-root boundary.

    On retry attempts (attempt > 1), injects a structured summary of what
    the previous attempt did — files written, errors detected, bash commands
    run — so the agent can pick up where it left off without re-discovering
    the problem from scratch (IMP-05).

    Args:
        task: The task to build instructions for.
        attempt: The current attempt number (1-based).
        config: The The Architect configuration.
        previous_summary: Optional summary of the previous attempt's work,
            injected into the retry instruction.
        architect_md_content: Optional ARCHITECT.md content to inject into
            the instruction so the build agent can read accumulated project
            knowledge.

    Returns:
        A complete instruction string for opencode.
    """
    import importlib.resources as resources

    lines: list[str] = []

    # --- Execution protocol (explains The Architect to the user's agent) ---
    local_protocol = config.project_root / ".architect" / "prompts" / "execution-protocol.md"
    protocol_text = ""
    try:
        protocol_text = local_protocol.read_text(encoding="utf-8").strip()
    except OSError:
        protocol_text = ""
    if not protocol_text:
        protocol_source = (
            resources.files("the_architect.resources.prompts") / "execution-protocol.md"
        )
        protocol_text = protocol_source.read_text(encoding="utf-8").strip()
    lines.append(protocol_text)

    lines.append("")
    lines.append("---")
    lines.append("")

    # --- ARCHITECT.md — persistent project intelligence ---
    if architect_md_content:
        lines.append("=== ARCHITECT.md — Persistent Project Intelligence ===")
        lines.append(architect_md_content)
        lines.append("")
        lines.append("---")
        lines.append("")

    # --- Task-specific instruction ---
    project_root = str(config.project_root)
    progress_rel = config.progress_file.relative_to(config.project_root).as_posix()
    task_rel = task.path.name

    lines.append(f"PROJECT ROOT: {project_root}")
    lines.append(
        f"TASK PREFIX: {task.prefix} — when complete, output exactly "
        f"<promise>{task.prefix}_COMPLETE</promise>."
    )
    lines.append(
        "BOUNDARY: You MUST NOT read, write, or modify any file outside this project root. "
        "Do not use absolute paths that point outside this directory. "
        "Do not `cd` above this directory. All work must stay within the project root."
    )
    lines.append("")
    if config.integrity:
        lines.append("=== FILE INTEGRITY PROTOCOL ===")
        lines.append(
            "Before modifying any existing file, create a same-directory snapshot named "
            "architect_eval_<original_filename>. This is mandatory for existing files and "
            "is how you protect against truncated mid-write corruption."
        )
        lines.append("")
        lines.append("Follow this protocol exactly:")
        lines.append(
            "1. Before editing an existing file, copy it to architect_eval_<filename> in the "
            "same directory. Do not create snapshots for brand-new files."
        )
        lines.append(
            "2. Make your change to the original file normally. Never create snapshots for "
            "architect_eval_* files themselves."
        )
        lines.append(
            "3. Validate the rewritten file against the snapshot before considering the edit "
            "complete. Check for obvious truncation, incomplete endings, missing major sections, "
            "and any large unexpected size shrinkage. Size is a warning signal, "
            "not an absolute rule."
        )
        lines.append(
            "4. If validation passes, delete the architect_eval_* snapshot immediately. A deleted "
            "snapshot means the file was verified clean."
        )
        lines.append(
            "5. If validation fails, restore from the snapshot, diagnose the problem, retry the "
            "write, and only delete the snapshot after a clean validation."
        )
        lines.append("")
        lines.append(
            "Never leave architect_eval_* files behind after a successful task. Any leftover "
            "snapshot will be treated by The Architect as a corruption signal during reassessment "
            "and retrospective review."
        )
        lines.append("=== END FILE INTEGRITY PROTOCOL ===")
        lines.append("")
    # Point to tasks/INSTRUCTIONS.md if it exists — The Architect's master context file
    instructions_md = config.tasks_dir / "INSTRUCTIONS.md"
    if instructions_md.exists():
        lines.append(
            f"Read tasks/INSTRUCTIONS.md for project context, "
            f"then read {progress_rel}, then read {task_rel} "
            "and complete every task in it — work autonomously without asking "
            "the human for confirmation."
        )
    else:
        lines.append(
            f"Read {progress_rel} then read {task_rel} and complete every task in it "
            "— work autonomously without asking the human for confirmation."
        )

    lines.append("")
    lines.append("Before you finish, record concise execution evidence in PROGRESS.md:")
    lines.append("- what changed")
    lines.append("- files touched")
    lines.append("- verification/tests run")
    lines.append("- whether later tasks should change because of new discoveries")
    lines.append("")
    lines.append("At the very end of your response, include this exact structured block:")
    lines.append(_OUTCOME_SECTION_MARKER)
    lines.append("Summary: <one sentence>")
    lines.append("Files: <comma-separated files or none>")
    lines.append("Verification: <commands/tests run or none>")
    lines.append("Impact: <none or possible>")

    if attempt > 1:
        lines.append("")
        lines.append(f"🔄 RETRY ATTEMPT {attempt}/{config.max_retries}")
        lines.append(
            "Your previous work persists in files on disk. "
            "Do NOT assume the code is in its original state — read the current "
            "state of each file before touching it."
        )

        # retry_prompt_mode="focused" (default) — structured step-by-step guidance
        # retry_prompt_mode="same" — minimal note, Ralph-style identical prompt
        if config.retry_prompt_mode != "same":
            lines.append("")
            lines.append("BEFORE writing any code, do this in order:")
            lines.append(
                "1. Read PROGRESS.md — check which sub-tasks are already marked Done. "
                "Do NOT redo them."
            )
            lines.append(
                "2. Run the test suite — diagnose what is actually failing right now. Do not guess."
            )
            lines.append("3. Fix only what is broken. Skip everything that already works.")
            lines.append(
                "4. When ALL items are complete and tests pass: update PROGRESS.md, "
                f"then output <promise>{task.prefix}_COMPLETE</promise>."
            )
            lines.append(
                "If the task is already fully complete (tests pass, nothing left to do): "
                "mark it Done in PROGRESS.md immediately and output the promise tag — "
                "do not redo work just because this is a retry."
            )

        if previous_summary:
            lines.append("")
            lines.append("=== PREVIOUS ATTEMPT CONTEXT ===")
            lines.append(previous_summary)
            lines.append(
                "Focus on fixing the errors above. Do not redo work that is already complete."
            )

    # Note: opencode reads AGENTS.md automatically from the project root.
    # We do not inject it here — it belongs to the user and opencode handles it natively.

    # Inject docs path only when it actually exists on disk.
    if config.docs_path:
        docs_path = config.docs_path
        # If relative, resolve against project root
        if not docs_path.is_absolute():
            docs_path = config.project_root / docs_path
        if docs_path.exists():
            lines.append("")
            lines.append(f"Project documentation is available at: {docs_path}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------


def select_model(
    attempt: int,
    config: ArchitectConfig,
    model_override: str | None = None,
) -> str | None:
    """Determine which model to use for this attempt.

    Priority order:
    1. model_override — explicit override (used for retries), highest priority
    2. config.standalone_mode — bypass provider config entirely
    3. None — let the provider use its configured default model

    Args:
        attempt: The current attempt number (1-based).
        config: The The Architect configuration.
        model_override: Optional explicit model override.

    Returns:
        Model name string to pass via ``--model``, or None to use the provider's default.
    """
    if model_override:
        return model_override
    if config.standalone_mode:
        return config.standalone_mode
    return None


def _extract_task_outcome_summary(text: str) -> str:
    """Extract a compact structured outcome summary from agent output."""
    import re as _re

    if _OUTCOME_SECTION_MARKER in text:
        section = text.split(_OUTCOME_SECTION_MARKER, 1)[1]
        outcome_lines = [line.strip() for line in section.splitlines() if line.strip()]
        values: dict[str, str] = {}
        for line in outcome_lines:
            for key, label in _OUTCOME_FIELD_LABELS.items():
                prefix = f"{label}:"
                if line.startswith(prefix):
                    values[key] = line[len(prefix) :].strip()
                    break
        if values:
            structured: list[str] = []
            if values.get("summary"):
                structured.append(f"Summary: {values['summary']}")
            if values.get("files"):
                structured.append(f"Files: {values['files']}")
            if values.get("verification"):
                structured.append(f"Verification: {values['verification']}")
            impact_value = values.get("impact", "none").lower()
            structured.append(
                "Downstream impact: possible"
                if impact_value in {"possible", "yes", "changed"}
                else "Downstream impact: none"
            )
            return "\n".join(structured)

    lines: list[str] = []
    lowered = text.lower()

    files = sorted(set(_re.findall(r"\b[\w./-]+\.[A-Za-z0-9]+\b", text)))
    if files:
        lines.append(f"Files: {', '.join(files[:6])}")

    verifications = _re.findall(
        r"(?:pytest|ruff check|ruff format|mypy|npm test|pnpm test|cargo test|go test)[^\n]*",
        text,
        _re.IGNORECASE,
    )
    if verifications:
        cleaned = [match.strip() for match in verifications[:3]]
        lines.append(f"Verification: {'; '.join(cleaned)}")

    impact_markers = [
        "next tasks",
        "downstream",
        "follow-up",
        "remaining tasks",
        "architecture changed",
        "assumption",
    ]
    impact = "possible" if any(marker in lowered for marker in impact_markers) else "none"
    lines.append(f"Downstream impact: {impact}")

    progress_signals = extract_progress_signals(text)
    if progress_signals:
        lines.append(f"Outcome: {progress_signals[0]}")

    return "\n".join(lines[:4])


def _task_outcome_summary_for_exit(text: str, exit_code: int | None) -> str:
    """Return a task outcome summary with explicit killed-process diagnostics."""
    summary = _extract_task_outcome_summary(text)
    # ``signal.SIGKILL`` does not exist on Windows — use the module-level
    # constant which was already guarded with getattr() at definition time.
    if exit_code == _FORCED_TERMINATION_EXIT_CODE:
        killed = (
            f"Provider process killed (SIGKILL / exit {_FORCED_TERMINATION_EXIT_CODE});"
            " no reliable task output was produced."
        )
        return f"{summary}\n{killed}" if summary else killed
    return summary


# ---------------------------------------------------------------------------
# Single attempt
# ---------------------------------------------------------------------------


async def run_task_once(
    task: Task,
    attempt: int,
    config: ArchitectConfig,
    model_override: str | None = None,
    architect_md_content: str = "",
    provider: ArchitectProvider | None = None,
    on_first_output: Callable[[], None] | None = None,
    renderer: StreamRenderer | None = None,
) -> TaskResult:
    """Run one attempt of a task.

    Provider output goes directly to the terminal — no piping or
    reformatting.  The log file captures the raw output for retry context.

    Args:
        task: The task to run.
        attempt: The attempt number (1-based).
        config: The The Architect configuration.
        model_override: Optional model to use for this attempt.
        architect_md_content: Optional ARCHITECT.md content to inject.
        provider: The AI CLI provider to use.  Defaults to OpenCode when
            not specified (backward-compatible behaviour).

    Returns:
        TaskResult with status, duration, tokens, and model info.
    """
    if provider is None:
        from the_architect.core.opencode_provider import OpenCodeProvider

        provider = OpenCodeProvider()

    if attempt == 1:
        log_path = config.log_dir / f"{task.name}.log"
    else:
        log_path = config.log_dir / f"{task.name}.attempt{attempt}.log"

    config.log_dir.mkdir(parents=True, exist_ok=True)

    # Carry forward context from the previous attempt (IMP-05 / IMP-10)
    # Skipped when carry_context=False (Ralph-style same-prompt retries)
    previous_summary = ""
    if attempt > 1 and config.carry_context:
        prev_log = (
            config.log_dir / f"{task.name}.log"
            if attempt == 2
            else config.log_dir / f"{task.name}.attempt{attempt - 1}.log"
        )
        previous_summary = summarize_previous_attempt(prev_log)
        if previous_summary:
            logger.info(f"Task {task.prefix} attempt {attempt}: injecting previous attempt summary")

    instruction = build_instruction(
        task,
        attempt,
        config,
        previous_summary=previous_summary,
        architect_md_content=architect_md_content,
    )
    model = select_model(attempt, config, model_override)

    # When no explicit model override is set, resolve the actual model from
    # the provider so TaskResult.model is populated (for tasks/SUMMARY.md, terminal
    # summary, and PROGRESS.md).  Without this, the model column is always
    # empty because select_model returns None for the default case.
    if model is None:
        project_dir = config.project_root
        # Pass execution_agent directly — get_resolved_model handles "" by
        # reading default_agent from the config, so we don't hardcode "build".
        try:
            resolved = provider.get_resolved_model(project_dir, config.execution_agent or "")
            if resolved:
                model = resolved
        except Exception:
            pass  # Non-fatal — model stays None, will be stored as ""

    logger.info(
        f"Running task {task.prefix} (attempt {attempt}/{config.max_retries}) "
        f"provider={provider.name} agent={config.execution_agent or 'default'} "
        f"model={model or 'default'} log={log_path.name}"
    )

    _set_renderer_footer(
        renderer,
        _footer_text(
            f"{task.prefix} {task.title or task.name}",
            f"attempt {attempt}/{config.max_retries} | model {model or 'default'}",
        ),
    )

    # ── Workspace baseline capture ──────────────────────────────────────────
    baseline_path_str = ""
    captured_baseline: WorkspaceBaseline | None = None
    if config.workspace_baseline:
        try:
            from the_architect.core.baseline import (  # noqa: I001
                capture_baseline as _capture_baseline,
                write_baseline as _write_baseline,
            )

            baselines_dir = config.project_root / ".architect" / "baselines"
            baselines_dir.mkdir(parents=True, exist_ok=True)
            baseline_file = baselines_dir / f"{task.name}.json"

            captured_baseline = _capture_baseline(config.project_root, task.prefix)
            _write_baseline(captured_baseline, baseline_file)
            baseline_path_str = str(baseline_file.resolve())
            logger.info(
                f"Task {task.prefix}: baseline captured with "
                f"{len(captured_baseline.files)} files → {baseline_file.name}"
            )
        except Exception as baseline_exc:
            logger.warning(
                f"Task {task.prefix}: baseline capture failed — "
                f"continuing without baseline: {baseline_exc!r}"
            )
            captured_baseline = None

    start_time = time.monotonic()

    try:
        stream_result = await stream_provider(
            instruction=instruction,
            project_dir=config.project_root,
            provider=provider,
            model_override=model,
            agent_override=config.execution_agent or None,
            log_path=log_path,
            on_first_output=on_first_output,
            renderer=renderer,
        )

        duration = time.monotonic() - start_time

        # Give any file writes a moment to flush before checking PROGRESS.md.
        await asyncio.sleep(_PROGRESS_FLUSH_DELAY_SECONDS)

        if stream_result.interrupted or stream_result.exit_code != 0:
            logger.warning(
                f"Task {task.prefix} attempt {attempt} exited with code {stream_result.exit_code}"
            )
            if stream_result.interrupted:
                logger.warning(
                    f"Task {task.prefix} provider attempt interrupted: "
                    f"{stream_result.interruption_reason or 'unknown'}"
                )
            if stream_result.exit_code == _FORCED_TERMINATION_EXIT_CODE:
                logger.warning(
                    f"Task {task.prefix} provider process was killed with SIGKILL; "
                    "treating output as interrupted"
                )
            # Also check for rate-limit or model-not-found in the accumulated text
            # (belt-and-suspenders with the mid-stream detection — some errors
            # only appear in text output)
            from the_architect.core.free_models import is_model_not_found_error, is_rate_limit_error

            rl_hit = (
                stream_result.rate_limit_hit
                or is_rate_limit_error(stream_result.accumulated_text, stream_result.exit_code)
                or is_model_not_found_error(stream_result.accumulated_text, stream_result.exit_code)
            )
            return TaskResult(
                prefix=task.prefix,
                title=task.title or task.name,
                status="failed",
                duration_seconds=duration,
                attempts=attempt,
                tokens=stream_result.tokens,
                model=model or "",
                rate_limit_hit=rl_hit,
                accumulated_text=stream_result.accumulated_text,
                exit_code=stream_result.exit_code,
                cooldown_until=stream_result.cooldown_until,
                interrupted=stream_result.interrupted,
                interruption_reason=stream_result.interruption_reason,
                outcome_summary=_task_outcome_summary_for_exit(
                    stream_result.accumulated_text,
                    stream_result.exit_code,
                ),
                baseline_path=baseline_path_str,
            )

        # Multi-signal completion check (IMP-01 + IMP-06):
        # Requires corroboration from at least 2 of 4 signals, with special
        # handling for strong single signals (promise tag, PROGRESS.md).
        output_analysis = analyze_output(stream_result.accumulated_text)
        progress_done = task_is_done(config.progress_file, task.prefix)

        done, active_signals = is_task_complete(
            task_prefix=task.prefix,
            output_analysis=output_analysis,
            progress_done=progress_done,
            exit_code=stream_result.exit_code,
        )

        if done:
            logger.info(
                f"Task {task.prefix} marked Done after attempt {attempt} "
                f"(signals: {', '.join(active_signals)})"
            )
        else:
            logger.warning(
                f"Task {task.prefix} NOT marked Done after attempt {attempt} "
                f"(signals fired: {', '.join(active_signals) or 'none'})"
            )

        # Check for rate-limit or model-not-found even on exit_code=0 (some
        # providers return rate-limit info in the text output without a non-zero
        # exit code, and model-not-found errors can also appear with exit_code=0)
        from the_architect.core.free_models import is_model_not_found_error, is_rate_limit_error

        rl_hit = (
            stream_result.rate_limit_hit
            or is_rate_limit_error(stream_result.accumulated_text, stream_result.exit_code)
            or is_model_not_found_error(stream_result.accumulated_text, stream_result.exit_code)
        )

        # ── Detect baseline changes ──────────────────────────────────────
        outcome_summary = _extract_task_outcome_summary(stream_result.accumulated_text)
        if captured_baseline is not None and baseline_path_str:
            try:
                from the_architect.core.baseline import (
                    detect_changes as _detect_changes,
                )

                changes = _detect_changes(captured_baseline, config.project_root)
                created = changes.get("created", [])
                modified = changes.get("modified", [])
                deleted = changes.get("deleted", [])
                change_parts: list[str] = []
                if created:
                    change_parts.append(f"created: {len(created)} file(s)")
                if modified:
                    change_parts.append(f"modified: {len(modified)} file(s)")
                if deleted:
                    change_parts.append(f"deleted: {len(deleted)} file(s)")
                if change_parts:
                    baseline_summary = "Baseline changes: " + ", ".join(change_parts)
                    outcome_summary = (
                        f"{outcome_summary}\n{baseline_summary}"
                        if outcome_summary
                        else baseline_summary
                    )
                    logger.info(f"Task {task.prefix}: {baseline_summary}")
                else:
                    logger.info(f"Task {task.prefix}: baseline — no changes detected")
            except Exception as change_exc:
                logger.warning(
                    f"Task {task.prefix}: baseline change detection failed: {change_exc!r}"
                )

        return TaskResult(
            prefix=task.prefix,
            title=task.title or task.name,
            status="done" if done else "failed",
            duration_seconds=duration,
            attempts=attempt,
            tokens=stream_result.tokens,
            model=model or "",
            rate_limit_hit=rl_hit,
            accumulated_text=stream_result.accumulated_text,
            exit_code=stream_result.exit_code,
            cooldown_until=stream_result.cooldown_until,
            interrupted=stream_result.interrupted,
            interruption_reason=stream_result.interruption_reason,
            outcome_summary=outcome_summary,
            baseline_path=baseline_path_str,
        )

    except Exception as exc:
        # Catch-all: any unexpected exception (OSError, asyncio errors, etc.)
        # must NOT propagate up and crash the entire multi-task run.
        # Return a failed TaskResult so the retry logic can try again.
        duration = time.monotonic() - start_time
        logger.error(f"Task {task.prefix} attempt {attempt} crashed with unexpected error: {exc!r}")
        return TaskResult(
            prefix=task.prefix,
            title=task.title or task.name,
            status="failed",
            duration_seconds=duration,
            attempts=attempt,
            tokens=TokenUsage(),
            model=model or "",
            outcome_summary="Downstream impact: none",
            baseline_path=baseline_path_str,
        )


# ---------------------------------------------------------------------------
# Task with retries
# ---------------------------------------------------------------------------


async def run_task(
    task: Task,
    config: ArchitectConfig,
    on_attempt_start: Callable[[int, str | None], None] | None = None,
    on_attempt_done: Callable[[int, bool], None] | None = None,
    free_rotator: object | None = None,
    on_model_switched: Callable[[str, str | None], None] | None = None,
    circuit_breaker: object | None = None,
    on_circuit_event: Callable[[str, dict[str, Any]], None] | None = None,
    provider: ArchitectProvider | None = None,
    on_first_output: Callable[[], None] | None = None,
    renderer: StreamRenderer | None = None,
) -> TaskResult:
    """Run a task with automatic retries, model fallbacks, and circuit breaking.

    Attempt 1: no model override (uses opencode.json default)
    Attempt 2: config.retry_model_2 (if set)
    Attempt 3: config.retry_model_3 (if set)

    When ``free_rotator`` is provided (``--free`` mode), the retry logic
    changes: instead of using ``retry_model_2/3``, The Architect rotates through
    free-tier OpenRouter models.  When a rate limit is detected on the
    current model, the rotator switches to the next free model.  When all free
    models are exhausted, it falls back to the default model.

    When ``circuit_breaker`` is provided, the circuit state is checked
    before each attempt and updated after each attempt.  If the circuit is
    OPEN, the task is skipped immediately.  When the circuit opens during
    a run and the recovery action is REPLAN, the architect agent is called
    to rewrite the failing task.

    When ``on_circuit_event`` is provided, it is called with an event name
    and a data dict whenever a circuit/cooldown/replan event occurs.  This
    enables the monitor state writer to update the dashboard in real time.

    Pauses config.retry_pause seconds between attempts.
    opencode output goes directly to the terminal for all attempts.

    Args:
        task: The task to run.
        config: The The Architect configuration.
        on_attempt_start: Called when an attempt starts with (attempt_number, model).
        on_attempt_done: Called when an attempt finishes with (attempt_number, success).
        free_rotator: Optional FreeModelRotator for --free mode.
        on_model_switched: Called when --free mode rotates models with (old_model, new_model).
        circuit_breaker: Optional CircuitBreaker instance for failure pattern detection.
        on_circuit_event: Optional callback for circuit/cooldown/replan events.
            Called with (event_name, data_dict) where event_name is one of:
            "circuit_state_change", "cooldown_start", "cooldown_end", "replan_start", "replan_end".

    Returns:
        TaskResult with final status, accumulated tokens, and model info.
    """
    from the_architect.core.circuit import CircuitBreaker, RecoveryAction
    from the_architect.core.free_models import FreeModelRotator

    cb: CircuitBreaker | None = (
        circuit_breaker if isinstance(circuit_breaker, CircuitBreaker) else None
    )

    retry_models: dict[int, str | None] = {
        2: config.retry_model_2 or None,
        3: config.retry_model_3 or None,
    }

    accumulated_tokens = TokenUsage()
    total_attempts = 0
    last_result: TaskResult | None = None
    # Track the current model override for free mode
    free_model_override: str | None = None

    # In --free mode, start with the first free model
    if free_rotator is not None and isinstance(free_rotator, FreeModelRotator):
        free_model_override = free_rotator.current_model

    # ── Read ARCHITECT.md for injection into execution instructions ────
    architect_md_content = ""
    try:
        from the_architect.core.architect_md import read_architect_md

        architect_md_content = read_architect_md(config.project_root) or ""
    except Exception:
        pass  # Non-fatal — execution proceeds without ARCHITECT.md context

    # ── Pre-run circuit check ───────────────────────────────────────────
    if cb is not None:
        allowed, reason = cb.can_run(task.prefix)
        if not allowed:
            # Handle cooldown-wait resume from a previous run
            if reason.startswith("cooldown_wait_resume:"):
                try:
                    remaining_secs = int(reason.split(":")[1])
                    logger.info(
                        f"Task {task.prefix}: resuming cooldown wait — {remaining_secs}s remaining"
                    )
                    await cb.handle_cooldown_wait(task.prefix)
                    # After wait, allow the run to proceed
                except Exception as cw_exc:
                    logger.error(f"Circuit cooldown resume failed for {task.prefix}: {cw_exc!r}")
            else:
                logger.warning(f"Task {task.prefix} skipped by circuit breaker: {reason}")
                return TaskResult(
                    prefix=task.prefix,
                    title=task.title or task.name,
                    status="failed",
                    duration_seconds=0.0,
                    attempts=0,
                    tokens=TokenUsage(),
                    model="",
                )

    # Use a while loop so cooldown waits and sleep-wake gaps don't consume
    # a retry slot.  ``attempt`` is only incremented on real (non-sleep,
    # non-cooldown) attempts.  ``sleep_wake_retries`` tracks bonus retries
    # granted when the machine woke from sleep mid-attempt; these are
    # capped separately so a pathological suspend loop can't spin forever.
    _MAX_SLEEP_WAKE_BONUS_RETRIES = 10
    attempt = 0
    sleep_wake_retries = 0
    while attempt < config.max_retries:
        attempt += 1

        # ── Per-attempt circuit check (OPEN may have been set by a previous
        #    attempt in this same run — e.g. threshold breached on attempt 2) ──
        if cb is not None and attempt > 1:
            allowed, reason = cb.can_run(task.prefix)
            if not allowed:
                logger.warning(
                    f"Task {task.prefix} attempt {attempt} skipped by circuit breaker: {reason}"
                )
                break

        # Determine model override for this attempt
        if free_rotator is not None and isinstance(free_rotator, FreeModelRotator):
            # --free mode: use the rotator's current model
            model_override = free_model_override
        else:
            # Normal mode: use retry_model_2/3
            model_override = retry_models.get(attempt)

        model_for_attempt: str | None = None

        if on_attempt_start:
            try:
                model_for_attempt = select_model(attempt, config, model_override)
                # When select_model returns None (no override, no standalone mode),
                # resolve the actual model from the provider so the side panel shows
                # the real model name instead of blank.  Mirrors what run_task_once
                # does before building the command.
                if model_for_attempt is None and provider is not None:
                    try:
                        project_dir_cb = config.project_root
                        resolved_cb = provider.get_resolved_model(
                            project_dir_cb, config.execution_agent or ""
                        )
                        if resolved_cb:
                            model_for_attempt = resolved_cb
                    except Exception:
                        pass  # Non-fatal — callback fires with None
                on_attempt_start(attempt, model_for_attempt)
            except Exception:
                pass  # Callback failure must not stop the task
        _set_renderer_footer(
            renderer,
            _footer_text(
                f"{task.prefix} {task.title or task.name}",
                "attempt "
                f"{attempt}/{config.max_retries} | starting {model_for_attempt or 'default'}",
            ),
        )

        try:
            result = await run_task_once(
                task=task,
                attempt=attempt,
                config=config,
                model_override=model_override,
                architect_md_content=architect_md_content,
                provider=provider,
                on_first_output=on_first_output,
                renderer=renderer,
            )
        except Exception as exc:
            # run_task_once should never raise (it has its own catch-all),
            # but if it does, synthesize a failed result and continue.
            logger.error(f"Task {task.prefix} attempt {attempt} raised unexpectedly: {exc!r}")
            result = TaskResult(
                prefix=task.prefix,
                title=task.title or task.name,
                status="failed",
                duration_seconds=0.0,
                attempts=attempt,
                tokens=TokenUsage(),
                model=model_override or "",
            )

        total_attempts = attempt
        accumulated_tokens = accumulated_tokens + result.tokens
        last_result = result

        success = result.status == "done"

        if not success:
            from the_architect.core.circuit import ProviderErrorKind, detect_provider_error

            provider_error = detect_provider_error(result.accumulated_text, result.exit_code)
            if provider_error is not None and provider_error.kind in (
                ProviderErrorKind.UPDATE_REQUIRED,
                ProviderErrorKind.MISCONFIGURED,
                ProviderErrorKind.QUOTA_EXHAUSTED,
            ):
                msg = f"Provider stopped: {provider_error.message}. {provider_error.action}"
                logger.error(f"Task {task.prefix} aborted — {msg}")
                if renderer is not None:
                    try:
                        renderer.write_line(f"Warning: {msg}")
                    except Exception:
                        pass
                break

        # ── Circuit breaker: record attempt ─────────────────────────────
        cooldown_triggered = False
        if result.interrupted:
            logger.warning(
                f"Task {task.prefix} attempt {attempt} interrupted locally "
                f"({result.interruption_reason or 'unknown'}); skipping circuit counters"
            )
        elif cb is not None:
            log_path = (
                config.log_dir / f"{task.name}.log"
                if attempt == 1
                else config.log_dir / f"{task.name}.attempt{attempt}.log"
            )
            try:
                attempt_summary = build_attempt_summary(
                    task_id=task.prefix,
                    attempt_number=attempt,
                    log_path=log_path,
                    completion_detected=success,
                    total_tokens=result.tokens.total,
                    # Pass all signal fields directly from the stream result so
                    # cooldown detection works for all providers.  rate_limit_hit
                    # and cooldown_until are reliable even when accumulated_text
                    # is empty (e.g. Claude Code stream-json structured events).
                    accumulated_text=result.accumulated_text,
                    exit_code=result.exit_code,
                    rate_limit_hit=result.rate_limit_hit,
                    cooldown_until=result.cooldown_until,
                )
                cb_state = cb.record_attempt(attempt_summary)

                from the_architect.core.circuit import CircuitState

                # Fire circuit state change event for the monitor
                if on_circuit_event:
                    try:
                        on_circuit_event(
                            "circuit_state_change",
                            {
                                "state": cb_state.state.value,
                                "no_progress": cb_state.consecutive_no_progress,
                                "same_error": cb_state.consecutive_same_error,
                                "no_progress_threshold": config.circuit_no_progress_threshold,
                                "same_error_threshold": config.circuit_same_error_threshold,
                            },
                        )
                    except Exception:
                        pass  # Callback failure must not stop the task

                # Handle COOLDOWN_WAIT — wait then retry without consuming a slot
                if (
                    not success
                    and cb_state.cooldown_waiting
                    and cb_state.recovery_action == RecoveryAction.COOLDOWN_WAIT
                ):
                    cooldown_triggered = True
                    if on_circuit_event:
                        try:
                            on_circuit_event(
                                "cooldown_start",
                                {
                                    "task_id": task.prefix,
                                    "wait_count": cb_state.cooldown_wait_count,
                                },
                            )
                        except Exception:
                            pass
                    _set_renderer_footer(
                        renderer,
                        _footer_text(
                            f"{task.prefix} {task.title or task.name}",
                            f"cooldown wait | attempt {attempt}/{config.max_retries}",
                        ),
                    )
                    try:
                        await cb.handle_cooldown_wait(task.prefix)
                    except Exception as cw_exc:
                        logger.error(f"Circuit cooldown wait failed for {task.prefix}: {cw_exc!r}")
                    if on_circuit_event:
                        try:
                            on_circuit_event("cooldown_end", {})
                        except Exception:
                            pass
                    _set_renderer_footer(
                        renderer,
                        _footer_text(
                            f"{task.prefix} {task.title or task.name}",
                            f"cooldown ended | attempt {attempt}/{config.max_retries}",
                        ),
                    )
                    # Decrement attempt so this slot is not consumed
                    attempt -= 1

                # If circuit just opened with REPLAN action, trigger replan
                elif (
                    not success
                    and cb_state.state == CircuitState.OPEN
                    and cb_state.recovery_action == RecoveryAction.REPLAN
                ):
                    logger.info(f"Circuit {task.prefix}: triggering replan")
                    if on_circuit_event:
                        try:
                            on_circuit_event("replan_start", {"task_id": task.prefix})
                        except Exception:
                            pass
                    _set_renderer_footer(
                        renderer,
                        _footer_text(f"{task.prefix} {task.title or task.name}", "replanning task"),
                    )
                    try:
                        await cb.attempt_replan(
                            task_id=task.prefix,
                            task_file=task.path,
                            progress_file=config.progress_file,
                        )
                    except Exception as replan_exc:
                        logger.error(
                            f"Circuit replan for {task.prefix} raised unexpectedly: {replan_exc!r}"
                        )
                    if on_circuit_event:
                        try:
                            on_circuit_event("replan_end", {"task_id": task.prefix})
                        except Exception:
                            pass
                    _set_renderer_footer(
                        renderer,
                        _footer_text(f"{task.prefix} {task.title or task.name}", "replan complete"),
                    )
            except Exception as cb_exc:
                # Circuit breaker errors must NEVER crash the run
                logger.error(
                    f"Circuit breaker error for {task.prefix} attempt {attempt}: {cb_exc!r}"
                )

        if on_attempt_done and not cooldown_triggered:
            try:
                on_attempt_done(attempt, success)
            except Exception:
                pass  # Callback failure must not stop the task

        if success:
            # Reset circuit state on success
            if cb is not None:
                try:
                    cb.reset_task(task.prefix)
                except Exception:
                    pass

            # This task succeeded — clear any sleep-interrupted flag so the
            # Infinite Loop driver doesn't mistakenly reset it to Pending.
            _clear_sleep_interrupted(task.prefix)

            # Update the result with accumulated tokens and total attempts.
            # outcome_summary must be forwarded so downstream reassessment
            # can check "Downstream impact: possible" on the returned result.
            return TaskResult(
                prefix=result.prefix,
                title=result.title,
                status="done",
                duration_seconds=result.duration_seconds,
                attempts=total_attempts,
                tokens=accumulated_tokens,
                model=result.model,
                outcome_summary=result.outcome_summary,
            )

        # If cooldown was triggered, skip normal retry pause and loop immediately
        if cooldown_triggered:
            continue

        # ── Sleep/wake gap: don't consume a retry slot ────────────────────
        # When the machine woke from sleep mid-attempt the subprocess was
        # killed by the sleep-wake gap detector (not by a real failure).
        # Charging a retry slot against the agent for a hardware event is
        # unfair and will exhaust ``max_retries`` on a single long sleep.
        # Instead: decrement ``attempt`` (same pattern as cooldown_wait) so
        # the next iteration reuses the same slot.  A separate ``sleep_wake_retries``
        # counter caps this at ``_MAX_SLEEP_WAKE_BONUS_RETRIES`` so a
        # pathological suspend loop never spins forever.
        if (
            not success
            and result.interruption_reason == "sleep_wake_gap"
            and sleep_wake_retries < _MAX_SLEEP_WAKE_BONUS_RETRIES
        ):
            sleep_wake_retries += 1
            # Record that this task was sleep-interrupted (belt-and-suspenders
            # for the Infinite Loop driver's fallback reset path).
            _mark_sleep_interrupted(task.prefix)
            logger.warning(
                f"Task {task.prefix} attempt {attempt} was sleep-interrupted; "
                f"granting bonus retry (sleep_wake_retries={sleep_wake_retries}/"
                f"{_MAX_SLEEP_WAKE_BONUS_RETRIES})"
            )
            if on_circuit_event:
                try:
                    on_circuit_event(
                        "sleep_detected",
                        {
                            "task_id": task.prefix,
                            "sleep_retries": str(sleep_wake_retries),
                        },
                    )
                except Exception:
                    pass
            _set_renderer_footer(
                renderer,
                _footer_text(
                    f"{task.prefix} {task.title or task.name}",
                    f"woke from sleep | retrying (sleep-retry {sleep_wake_retries})",
                ),
            )
            # Brief pause so the retry doesn't hammer the API before the
            # network stack has fully recovered after wake.
            try:
                await asyncio.sleep(config.retry_pause)
            except asyncio.CancelledError:
                logger.warning(f"Sleep-wake retry pause cancelled for task {task.prefix}")
                break
            attempt -= 1  # don't consume the retry slot
            if on_circuit_event:
                try:
                    on_circuit_event("wake_resumed", {"task_id": task.prefix})
                except Exception:
                    pass
            continue

        # ── Free mode: rotate model on rate limit or model-not-found ──────
        if (
            result.rate_limit_hit
            and free_rotator is not None
            and isinstance(free_rotator, FreeModelRotator)
            and free_model_override is not None
        ):
            old_model = free_model_override
            new_model = free_rotator.mark_rate_limited(old_model)
            free_model_override = new_model  # Could be None if all exhausted
            if on_model_switched:
                try:
                    on_model_switched(old_model, new_model)
                except Exception:
                    pass  # Callback failure must not stop the task
            _set_renderer_footer(
                renderer,
                _footer_text(
                    f"{task.prefix} {task.title or task.name}",
                    f"switching model | now {new_model or 'default'}",
                ),
            )
            # Distinguish rotation reason for logging
            from the_architect.core.free_models import is_model_not_found_error

            reason = (
                "model not found"
                if is_model_not_found_error(result.accumulated_text, 0)
                else "rate limit"
            )
            logger.info(
                f"Free mode: {reason} on {old_model} → "
                f"{'switching to ' + new_model if new_model else 'falling back to default'}"
            )

        if attempt < config.max_retries:
            logger.debug(f"Pausing {config.retry_pause}s before retry")
            try:
                await asyncio.sleep(config.retry_pause)
            except asyncio.CancelledError:
                logger.warning(f"Retry pause cancelled for task {task.prefix}")
                break

            # Check for actionable provider errors (update required, etc.)
            # Retrying won't fix these — fail fast with a clear message.
            from the_architect.core.circuit import ProviderErrorKind, detect_provider_error

            provider_error = detect_provider_error(
                result.accumulated_text,
                result.exit_code,
            )
            if (
                provider_error is not None
                and provider_error.kind == ProviderErrorKind.UPDATE_REQUIRED
                and provider is not None
            ):
                update_msg = provider.check_update_available()
                msg = update_msg or provider_error.action
                logger.error(f"Task {task.prefix} aborted — provider update required: {msg}")
                # Don't waste retry attempts on something the user must fix
                break

    logger.error(f"Task {task.prefix} failed after {config.max_retries} attempts")
    # Return failed result with accumulated tokens and the outcome summary from
    # the last attempt so _record_task_outcome / tasks/SUMMARY.md have useful context.
    _last_reason = last_result.interruption_reason if last_result else ""
    if _last_reason == "sleep_wake_gap":
        # Belt-and-suspenders: ensure the Infinite Loop driver can detect
        # this even if sleep_wake_retries was somehow exhausted.
        _mark_sleep_interrupted(task.prefix)
    return TaskResult(
        prefix=task.prefix,
        title=task.title or task.name,
        status="failed",
        duration_seconds=last_result.duration_seconds if last_result else 0.0,
        attempts=total_attempts,
        tokens=accumulated_tokens,
        model=last_result.model if last_result else "",
        outcome_summary=last_result.outcome_summary if last_result else "",
        interrupted=last_result.interrupted if last_result else False,
        interruption_reason=_last_reason,
    )


# ---------------------------------------------------------------------------
# Hourly token budget
# ---------------------------------------------------------------------------


class HourlyTokenBudget:
    """Tracks token usage against a rolling hourly budget.

    When ``budget`` is 0 the tracker is disabled — all checks are no-ops.

    The window starts when the first tokens are recorded.  After one hour
    elapses the window resets automatically so the budget applies to each
    new hour of the run independently.

    Usage::

        budget = HourlyTokenBudget(config.token_budget_per_hour)
        budget.add(task_result.tokens.total)
        if budget.exceeded():
            wait_secs = budget.seconds_until_reset()
            await budget.wait_for_reset()   # logs progress every minute

    Args:
        budget: Maximum tokens per hour.  0 = disabled.
    """

    def __init__(self, budget: int) -> None:
        self._budget = budget
        self._tokens_this_hour: int = 0
        self._window_start: float | None = None  # monotonic time

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """True when a non-zero budget is configured."""
        return self._budget > 0

    def add(self, tokens: int) -> None:
        """Record ``tokens`` used by the most recently completed task.

        Resets the window automatically if an hour has already elapsed.

        Args:
            tokens: Number of tokens to add (input + output).
        """
        if not self.enabled or tokens <= 0:
            return

        now = time.monotonic()

        # Start or reset the window
        if self._window_start is None:
            self._window_start = now
            self._tokens_this_hour = 0
        elif now - self._window_start >= 3600:
            logger.debug(
                f"Token budget: hour window elapsed — resetting "
                f"(used {self._tokens_this_hour:,} / {self._budget:,})"
            )
            self._window_start = now
            self._tokens_this_hour = 0

        self._tokens_this_hour += tokens
        logger.debug(
            f"Token budget: +{tokens:,} → {self._tokens_this_hour:,} / {self._budget:,} this hour"
        )

    def exceeded(self) -> bool:
        """True when the hourly budget has been exceeded.

        Always False when the budget is disabled or the window has not
        started yet.

        Returns:
            True if tokens used this hour exceed the configured budget.
        """
        if not self.enabled or self._window_start is None:
            return False
        return self._tokens_this_hour >= self._budget

    def seconds_until_reset(self) -> float:
        """Seconds remaining until the current hour window resets.

        Returns 0.0 if the window has already elapsed or is not started.

        Returns:
            Remaining seconds in the current hour window.
        """
        if self._window_start is None:
            return 0.0
        elapsed = time.monotonic() - self._window_start
        return max(0.0, 3600.0 - elapsed)

    async def wait_for_reset(self) -> None:
        """Pause until the current hour window resets.

        Logs progress every minute so the user knows the run is alive.
        After the wait, resets the window so execution can continue.
        """
        remaining = self.seconds_until_reset()
        if remaining <= 0:
            self._reset_window()
            return

        logger.info(
            f"Token budget: hourly limit of {self._budget:,} tokens reached "
            f"({self._tokens_this_hour:,} used) — pausing for "
            f"{int(remaining)}s until the hour resets"
        )

        waited = 0.0
        while waited < remaining:
            chunk = min(60.0, remaining - waited)
            try:
                await asyncio.sleep(chunk)
            except asyncio.CancelledError:
                logger.warning("Token budget wait interrupted")
                break
            waited += chunk
            still_remaining = remaining - waited
            if still_remaining > 0:
                logger.info(
                    f"Token budget: waiting for hour reset — {int(still_remaining)}s remaining"
                )

        self._reset_window()
        logger.info("Token budget: hour window reset — resuming execution")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reset_window(self) -> None:
        """Reset the window to now with zero tokens."""
        self._window_start = time.monotonic()
        self._tokens_this_hour = 0


# ---------------------------------------------------------------------------
# Full run loop
# ---------------------------------------------------------------------------


def _reconcile_progress_after_attempt(
    progress_file: Path,
    task_result: TaskResult,
    max_retries: int,
) -> None:
    """Write the runner's authoritative verdict for a task into PROGRESS.md.

    The multi-signal completion check (see :func:`is_task_complete`) decides
    whether a task attempt is ``done`` or ``failed``, but that verdict
    lives only in the returned :class:`TaskResult` object.  Without this
    reconciliation step the verdict is lost across runs, and a task whose
    executor agent forgot to rewrite PROGRESS.md would be re-picked on the
    next loop iteration — the original "task repeats after retrospective"
    bug.

    Behaviour:

    - ``done``       → write ``Done`` and today's date.
    - ``failed``     → write ``Failed`` and an attempt-count annotation.
    - ``skipped``    → leave the row untouched (the task was bypassed by
                       the circuit breaker or a similar policy and is
                       expected to be retried on the next run).
    - anything else  → no-op.

    Missing or unreadable PROGRESS.md files are logged but never raise —
    this function must never crash the run loop.

    Args:
        progress_file: Path to the project's PROGRESS.md.
        task_result: The runner's verdict for the task that just finished.
        max_retries: Configured maximum retries, used in the ``Failed``
            annotation (``"Failed (3 attempts)"``).
    """
    try:
        status = task_result.status
        if status == "done":
            # Overwrite Completed with today's date.  Even if the agent
            # already wrote "Done", reconciliation is idempotent and
            # guarantees the date cell is filled — some agents write
            # "Done" with a literal em-dash which is not useful.
            today = datetime.date.today().isoformat()
            reconcile_task_status(progress_file, task_result.prefix, "Done", completed=today)
        elif status == "failed":
            attempts = task_result.attempts or max_retries
            annotation = f"{attempts} attempts" if attempts != 1 else "1 attempt"
            if not reconcile_task_status(
                progress_file,
                task_result.prefix,
                "Failed",
                completed=annotation,
            ):
                tasks_dir = progress_file.parent
                repaired = reconcile_progress_with_task_files(
                    progress_file, discover_tasks(tasks_dir)
                )
                if repaired and reconcile_task_status(
                    progress_file,
                    task_result.prefix,
                    "Failed",
                    completed=annotation,
                ):
                    logger.info(
                        f"Repaired missing PROGRESS.md row and persisted Failed status for "
                        f"{task_result.prefix} ({annotation})"
                    )
                else:
                    logger.warning(
                        f"PROGRESS.md has no row for {task_result.prefix} — "
                        "cannot persist Failed status"
                    )
            else:
                logger.info(
                    f"Persisted Failed status for {task_result.prefix} "
                    f"({annotation}) in PROGRESS.md"
                )
    except Exception as exc:
        # Never allow reconciliation to take down the run.
        logger.warning(f"Progress reconciliation failed for {task_result.prefix}: {exc!r}")


async def run_all(
    plan: TaskPlan,
    config: ArchitectConfig,
    on_task_start: Callable[[Task], None] | None = None,
    on_task_done: Callable[[TaskResult], None] | None = None,
    on_task_failed: Callable[[TaskResult], None] | None = None,
    on_attempt_start: Callable[[int, str | None], None] | None = None,
    on_attempt_done: Callable[[int, bool], None] | None = None,
    on_task_pause: Callable[[int], None] | None = None,
    free_rotator: object | None = None,
    on_model_switched: Callable[[str, str | None], None] | None = None,
    on_circuit_event: Callable[[str, dict[str, Any]], None] | None = None,
    provider: ArchitectProvider | None = None,
    on_first_output: Callable[[], None] | None = None,
    renderer: StreamRenderer | None = None,
) -> bool:
    """Run all pending tasks in order.

    opencode output goes directly to the terminal for every task.
    Pauses config.pause_between_tasks seconds between tasks.
    Uses a lock file to prevent concurrent runs.

    Loads the circuit breaker state at the start of the run and passes
    the breaker instance to each task execution.  Circuit state is
    persisted after every attempt.

    Args:
        plan: The task plan containing tasks to run.
        config: The ArchitectConfig configuration.
        on_task_start: Called when a task starts (receives the Task).
        on_task_done: Called when a task succeeds (receives the TaskResult).
        on_task_failed: Called when a task fails all retries (receives the TaskResult).
        on_attempt_start: Called when an attempt starts with (attempt_number, model).
        on_attempt_done: Called when an attempt finishes with (attempt_number, success).
        on_task_pause: Called with the pause duration (seconds) before sleeping between tasks.
        free_rotator: Optional FreeModelRotator for --free mode.
        on_model_switched: Called when --free mode rotates models with (old_model, new_model).
        on_first_output: Called (at most once per task) the first time the
            provider produces a user-visible line of output.  Used by the CLI
            to stop the startup spinner the moment real output begins.

    Returns:
        True if all tasks completed successfully.

    Raises:
        RuntimeError: If a concurrent run is detected (lock file exists).
    """
    project_dir = config.project_root

    if not acquire_lock(project_dir):
        raise RuntimeError(
            "Another The Architect run is in progress. "
            "Check for a stale lock file at .architect/runner.lock "
            "or wait for the other process to complete."
        )

    # Load circuit breaker state once per run, passing free_rotator so it
    # can check whether free mode still has models available.
    circuit_breaker: object | None = None
    try:
        from the_architect.core.circuit import load_circuit_state

        circuit_breaker = load_circuit_state(
            project_dir, config, free_rotator=free_rotator, provider=provider
        )
    except Exception as exc:
        logger.warning(f"Circuit breaker failed to load — running without it: {exc!r}")

    try:
        return await _run_all_inner(
            plan,
            config,
            on_task_start,
            on_task_done,
            on_task_failed,
            on_attempt_start,
            on_attempt_done,
            on_task_pause,
            free_rotator,
            on_model_switched,
            circuit_breaker=circuit_breaker,
            on_circuit_event=on_circuit_event,
            provider=provider,
            on_first_output=on_first_output,
            renderer=renderer,
        )
    finally:
        release_lock(project_dir)


async def _run_all_inner(
    plan: TaskPlan,
    config: ArchitectConfig,
    on_task_start: Callable[[Task], None] | None = None,
    on_task_done: Callable[[TaskResult], None] | None = None,
    on_task_failed: Callable[[TaskResult], None] | None = None,
    on_attempt_start: Callable[[int, str | None], None] | None = None,
    on_attempt_done: Callable[[int, bool], None] | None = None,
    on_task_pause: Callable[[int], None] | None = None,
    free_rotator: object | None = None,
    on_model_switched: Callable[[str, str | None], None] | None = None,
    circuit_breaker: object | None = None,
    on_circuit_event: Callable[[str, dict[str, Any]], None] | None = None,
    provider: ArchitectProvider | None = None,
    on_first_output: Callable[[], None] | None = None,
    renderer: StreamRenderer | None = None,
) -> bool:
    """Inner run_all implementation (after lock acquisition)."""
    # Track which tasks we actually attempted (skipping already-done ones).
    # Only these tasks are checked in the final "all ok" verdict — old
    # tasks from previous plans that still exist in tasks/ but aren't in
    # the current PROGRESS.md must NOT cause a false failure.
    attempted: list[Task] = []

    # Hourly token budget — disabled when token_budget_per_hour == 0
    token_budget = HourlyTokenBudget(config.token_budget_per_hour)

    for task in plan.tasks:
        # A task is skipped when its PROGRESS.md row is in any terminal
        # state (Done, Failed, or Blocked) — not just Done.  This closes
        # the "repeat after retrospective" loop: once the runner has
        # persisted Failed for a task that exhausted its retries, the
        # next run iteration will not silently re-pick it.  A human (or
        # a reviewer-created R-task) must flip the row back to Pending
        # to resume work.
        if task.status.value == "done" or task_is_resolved(config.progress_file, task.prefix):
            logger.debug(f"Skipping already-resolved task {task.prefix}")
            continue

        attempted.append(task)

        if on_task_start:
            try:
                on_task_start(task)
            except Exception:
                pass  # Callback failure must not stop the run

        try:
            task_result = await run_task(
                task=task,
                config=config,
                on_attempt_start=on_attempt_start,
                on_attempt_done=on_attempt_done,
                free_rotator=free_rotator,
                on_model_switched=on_model_switched,
                circuit_breaker=circuit_breaker,
                on_circuit_event=on_circuit_event,
                provider=provider,
                on_first_output=on_first_output,
                renderer=renderer,
            )
        except Exception as exc:
            # run_task should never raise (it has its own catch-all), but if
            # it does, synthesize a failed result and continue.
            logger.error(f"Task {task.prefix} raised unexpectedly during run_task: {exc!r}")
            task_result = TaskResult(
                prefix=task.prefix,
                title=task.title or task.name,
                status="failed",
                duration_seconds=0.0,
                attempts=0,
                tokens=TokenUsage(),
                model="",
            )

        # ── Reconcile PROGRESS.md with the runner's authoritative verdict ──
        # This is the single point that persists "the runner saw this task
        # complete" (or "the runner exhausted retries") into PROGRESS.md,
        # regardless of whether the agent remembered to update the file
        # itself.  Call it for every task before branching on success or
        # failure so the file is always consistent with reality.
        _reconcile_progress_after_attempt(config.progress_file, task_result, config.max_retries)

        if task_result.status == "done":
            if on_task_done:
                try:
                    on_task_done(task_result)
                except Exception:
                    pass  # Callback failure must not stop the run
        else:
            if on_task_failed:
                try:
                    on_task_failed(task_result)
                except Exception:
                    pass  # Callback failure must not stop the run
            logger.error(
                f"Task {task.prefix} failed after {config.max_retries} attempts — "
                "stopping (subsequent tasks depend on this one).  PROGRESS.md has "
                "been updated to Failed so the next run will not silently re-pick it."
            )
            return False

        # ── Hourly token budget check ────────────────────────────────────
        # Add this task's tokens to the rolling hour window.  If the budget
        # is now exceeded, pause for the remainder of the hour before
        # continuing to the next task.  This never consumes retry slots and
        # never changes circuit state — it is purely a spend throttle.
        if token_budget.enabled:
            token_budget.add(task_result.tokens.total)
            if token_budget.exceeded():
                remaining = token_budget.seconds_until_reset()
                logger.info(
                    f"Token budget: {config.token_budget_per_hour:,} tokens/hour exceeded "
                    f"after task {task.prefix} — waiting {int(remaining)}s for hour reset"
                )
                try:
                    await token_budget.wait_for_reset()
                except asyncio.CancelledError:
                    logger.warning("Token budget wait cancelled — stopping run")
                    break

        # ── Inter-task pause ─────────────────────────────────────────────
        if task != plan.tasks[-1]:
            logger.debug(f"Pausing {config.pause_between_tasks}s between tasks")
            if on_task_pause:
                # The callback (e.g. _countdown in cli.py) performs the full
                # blocking sleep internally — do NOT also await asyncio.sleep,
                # as that would double the pause duration.
                try:
                    on_task_pause(config.pause_between_tasks)
                except Exception:
                    pass  # Callback failure must not stop the run
            else:
                try:
                    await asyncio.sleep(config.pause_between_tasks)
                except asyncio.CancelledError:
                    logger.warning("Inter-task pause cancelled")
                    break

    # Only check tasks we actually attempted — not old tasks from previous
    # plans that may still exist in tasks/ but aren't in PROGRESS.md.
    all_ok = all(task_is_done(config.progress_file, task.prefix) for task in attempted)
    return all_ok
