"""Lifecycle hooks for The Architect.

Provides user-configurable shell commands that execute at run lifecycle points
(pre-run, post-task, post-run-success, post-run-failure). Hooks are stored
per-project in ``.architect/hooks.json`` and executed asynchronously via
subprocess with configurable timeouts.

Design notes
------------
- Hooks are non-blocking, non-fatal — a failed hook must NOT abort the run.
- Each hook has a configurable timeout (default: 30 seconds).
- Output is captured (stdout/stderr) for logging and diagnostics display.
- Follows the notifications.py silent-failure pattern.
- Storage follows the artifacts.py JSON persistence pattern.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from the_architect.core.fileutil import safe_atomic_write_json

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Directory and file for hooks storage (relative to project root).
HOOKS_DIR = Path(".architect")
HOOKS_FILE = HOOKS_DIR / "hooks.json"

# Default timeout for hook execution in seconds.
_DEFAULT_HOOK_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Hook Event Enum
# ---------------------------------------------------------------------------


class HookEvent(StrEnum):
    """Lifecycle events at which hooks can fire.

    Values:
        pre_run: Fires before any tasks execute.
        post_task: Fires after each task completes (success or failure).
        post_run_success: Fires when all tasks complete successfully.
        post_run_failure: Fires when any task fails or the run is aborted.
    """

    pre_run = "pre_run"
    post_task = "post_task"
    post_run_success = "post_run_success"
    post_run_failure = "post_run_failure"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class HookConfig(BaseModel):
    """Configuration for a single lifecycle hook.

    Attributes:
        event: The lifecycle event that triggers this hook.
        command: Shell command string to execute when the event fires.
        enabled: Whether this hook is active (default True).
        timeout: Maximum seconds allowed for command execution (default 30).
    """

    event: HookEvent = Field(description="Lifecycle event that triggers this hook")
    command: str = Field(description="Shell command string to execute")
    enabled: bool = Field(
        default=True,
        description="Whether this hook is active",
    )
    timeout: int = Field(
        default=_DEFAULT_HOOK_TIMEOUT,
        ge=1,
        description="Maximum seconds allowed for command execution",
    )


class HookResult(BaseModel):
    """Result of executing a single hook command.

    Attributes:
        event: The lifecycle event that triggered this hook.
        command: The shell command that was executed.
        exit_code: Process exit code, or None if execution failed entirely.
        stdout: Captured standard output (truncated to 1000 chars).
        stderr: Captured standard error (truncated to 1000 chars).
        duration_seconds: Wall-clock duration of the hook execution.
        timestamp: ISO 8601 timestamp when execution completed.
        error: Error message if execution failed entirely, empty string otherwise.
    """

    event: HookEvent = Field(description="Lifecycle event that triggered this hook")
    command: str = Field(description="Shell command that was executed")
    exit_code: int | None = Field(
        default=None,
        description="Process exit code, or None if execution failed entirely",
    )
    stdout: str = Field(
        default="",
        description="Captured standard output (truncated to 1000 chars)",
    )
    stderr: str = Field(
        default="",
        description="Captured standard error (truncated to 1000 chars)",
    )
    duration_seconds: float = Field(
        default=0.0,
        description="Wall-clock duration of the hook execution in seconds",
    )
    timestamp: str = Field(description="ISO 8601 timestamp when execution completed")
    error: str = Field(
        default="",
        description="Error message if execution failed entirely, empty string otherwise",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Maximum length for captured stdout/stderr in HookResult.
_OUTPUT_TRUNCATE = 1000


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Returns:
        ISO timestamp string with timezone info.
    """
    return datetime.now(tz=UTC).isoformat()


def _hooks_path(project: Path) -> Path:
    """Return the absolute path to the hooks JSON file for *project*.

    Args:
        project: The project root directory.

    Returns:
        Path to ``.architect/hooks.json``.
    """
    return project / HOOKS_FILE


def _truncate_output(text: str, max_len: int = _OUTPUT_TRUNCATE) -> str:
    """Truncate captured output to *max_len* characters.

    Args:
        text: Raw captured output.
        max_len: Maximum character length (default 1000).

    Returns:
        Truncated string with ``... [truncated]`` suffix if shortened.
    """
    if len(text) <= max_len:
        return text
    return text[:max_len] + "... [truncated]"


def _build_env(context: dict[str, str] | None) -> dict[str, str] | None:
    """Build the environment dict for the hook subprocess.

    Merges the current process environment with any *context* variables.
    Context variables override existing environment values.

    Args:
        context: Optional dict of environment variables to inject.

    Returns:
        A new environment dict, or ``None`` if no context is provided
        (meaning the subprocess inherits the parent environment).
    """
    if not context:
        return None
    env = os.environ.copy()
    env.update(context)
    return env


# ---------------------------------------------------------------------------
# Hook Execution Engine
# ---------------------------------------------------------------------------


async def execute_hook(
    hook: HookConfig,
    context: dict[str, str] | None = None,
) -> HookResult:
    """Execute a single hook command asynchronously.

    Runs the hook's shell command via ``asyncio.create_subprocess_exec`` with
    ``shell=True``, captures stdout and stderr, enforces the hook's timeout,
    and returns a :class:`HookResult` with the execution outcome.

    The call is non-fatal: ``FileNotFoundError``, ``TimeoutExpired``, and
    generic exceptions are all caught and recorded in the result rather
    than propagated.

    Args:
        hook: The :class:`HookConfig` describing the command to run.
        context: Optional dict of environment variables to inject into the
            subprocess (e.g. task metadata for ``post_task`` hooks).

    Returns:
        A :class:`HookResult` with exit code, captured output, and timing.
    """
    start = asyncio.get_event_loop().time()

    result = HookResult(
        event=hook.event,
        command=hook.command,
        timestamp=_now_iso(),
    )

    try:
        env = _build_env(context)

        process = await asyncio.create_subprocess_shell(
            hook.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=hook.timeout,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            elapsed = asyncio.get_event_loop().time() - start
            result.exit_code = None
            result.stdout = ""
            result.stderr = ""
            result.duration_seconds = round(elapsed, 3)
            result.error = f"Hook timed out after {hook.timeout}s"
            logger.debug(f"Hook '{hook.event}' timed out: {hook.command!r}")
            return result

        elapsed = asyncio.get_event_loop().time() - start
        result.exit_code = process.returncode
        result.stdout = _truncate_output(stdout_bytes.decode("utf-8", errors="replace"))
        result.stderr = _truncate_output(stderr_bytes.decode("utf-8", errors="replace"))
        result.duration_seconds = round(elapsed, 3)

        if process.returncode != 0:
            logger.debug(f"Hook '{hook.event}' exited {process.returncode}: {hook.command!r}")
        else:
            logger.debug(f"Hook '{hook.event}' completed: {hook.command!r}")

    except FileNotFoundError:
        elapsed = asyncio.get_event_loop().time() - start
        result.duration_seconds = round(elapsed, 3)
        result.error = f"Command not found: {shlex.split(hook.command)[0]}"
        logger.debug(f"Hook '{hook.event}' command not found: {hook.command!r}")
    except Exception as exc:
        elapsed = asyncio.get_event_loop().time() - start
        result.duration_seconds = round(elapsed, 3)
        result.error = str(exc)
        logger.debug(f"Hook '{hook.event}' failed (non-fatal): {exc!r} for {hook.command!r}")

    return result


async def execute_hooks_for_event(
    hooks: list[HookConfig],
    event: HookEvent,
    context: dict[str, str] | None = None,
) -> list[HookResult]:
    """Execute all enabled hooks matching *event*.

    Filters *hooks* to those whose ``event`` matches and whose ``enabled``
    flag is True, then executes them sequentially (preserving registration
    order). Returns a list of :class:`HookResult` objects.

    Args:
        hooks: Full list of hook configurations.
        event: The lifecycle event to fire hooks for.
        context: Optional dict of environment variables to inject.

    Returns:
        List of :class:`HookResult` objects (one per matching enabled hook).
    """
    matching = [h for h in hooks if h.event == event and h.enabled]
    results: list[HookResult] = []
    for hook in matching:
        result = await execute_hook(hook, context=context)
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def load_hooks(project: Path) -> list[HookConfig]:
    """Read the hooks configuration from disk.

    Returns an empty list if the file doesn't exist or contains invalid data.

    Args:
        project: The project root directory.

    Returns:
        List of :class:`HookConfig` objects (may be empty).
    """
    hooks_path = _hooks_path(project)
    try:
        raw = hooks_path.read_text(encoding="utf-8")
        data: list[Any] = json.loads(raw)
        return [HookConfig.model_validate(item) for item in data]
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return []


def save_hooks(project: Path, hooks: list[HookConfig]) -> None:
    """Write the hooks configuration to disk atomically.

    Creates the ``.architect/`` directory if it doesn't exist.
    Uses atomic write (temp file + rename) so a reader never sees a partial
    write.

    Args:
        project: The project root directory.
        hooks: The list of :class:`HookConfig` objects to persist.
    """
    hooks_path = _hooks_path(project)
    safe_atomic_write_json(
        hooks_path,
        [h.model_dump() for h in hooks],
        prefix=".hooks_tmp_",
        log_label="Hooks config",
    )


# ---------------------------------------------------------------------------
# Public CRUD API
# ---------------------------------------------------------------------------


def add_hook(project: Path, hook: HookConfig) -> HookConfig:
    """Add a hook to the project's hook configuration.

    Appends to the existing list and writes atomically.

    Args:
        project: The project root directory.
        hook: The :class:`HookConfig` to add.

    Returns:
        The :class:`HookConfig` that was added.
    """
    hooks = load_hooks(project)
    hooks.append(hook)
    save_hooks(project, hooks)
    return hook


def remove_hook(project: Path, index: int) -> bool:
    """Remove a hook by its zero-based index in the configuration list.

    Silently succeeds if the index is out of range.

    Args:
        project: The project root directory.
        index: Zero-based index of the hook to remove.

    Returns:
        ``True`` if a hook was removed, ``False`` if index was out of range.
    """
    hooks = load_hooks(project)
    if 0 <= index < len(hooks):
        hooks.pop(index)
        save_hooks(project, hooks)
        return True
    return False


def list_hooks(project: Path) -> list[HookConfig]:
    """Return all hooks for the project.

    Args:
        project: The project root directory.

    Returns:
        List of all :class:`HookConfig` objects (may be empty).
    """
    return load_hooks(project)
