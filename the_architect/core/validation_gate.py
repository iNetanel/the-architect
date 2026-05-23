"""Post-task validation gate — configurable CI checks after each task.

Runs lint, test, and typecheck commands after a task signals completion to
provide independent verification before the runner proceeds to the next task.

Supports cross-project-type detection: Python (pyproject.toml), JavaScript/
TypeScript (package.json), Go (go.mod), and Rust (Cargo.toml).  Each project
type gets appropriate default commands; unrecognized project types receive
empty defaults so the gate is effectively disabled without false failures.

If checks fail, the task is reclassified as failed and the circuit breaker
is notified.

The gate is controlled by :class:`ValidationGateConfig` which can be set
per-project in ``architect.toml``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from loguru import logger

from the_architect.core.validation_gate_models import (
    GateCheckResult,
    ValidationGateConfig,
    ValidationGateResult,
)

if TYPE_CHECKING:
    from the_architect.core.provider import ArchitectProvider
    from the_architect.core.runner import StreamRenderer

# ---------------------------------------------------------------------------
# Default CI commands — project-type-specific
# ---------------------------------------------------------------------------

_PYTHON_DEFAULTS: dict[str, str] = {
    "lint": "ruff check .",
    "test": "pytest tests/ -q",
    "typecheck": "mypy the_architect/",
}

_JAVASCRIPT_DEFAULTS: dict[str, str] = {
    "lint": "npx eslint .",
    "test": "npm test",
}

_GO_DEFAULTS: dict[str, str] = {
    "lint": "go vet ./...",
    "test": "go test ./...",
}

_RUST_DEFAULTS: dict[str, str] = {
    "lint": "cargo clippy",
    "test": "cargo test",
}

# Backward-compatible alias — points to Python defaults for any code that
# references _DEFAULT_COMMANDS directly.
_DEFAULT_COMMANDS: dict[str, str] = _PYTHON_DEFAULTS

# ---------------------------------------------------------------------------
# Project type detection and command discovery
# ---------------------------------------------------------------------------


def _detect_project_type(
    project_root: Path,
) -> Literal["python", "javascript", "go", "rust", "unknown"]:
    """Detect project type by checking for manifest files.

    Priority order: pyproject.toml > package.json > go.mod > Cargo.toml.
    Returns the first match or ``"unknown"`` if none found.

    Args:
        project_root: The project root directory to scan.

    Returns:
        Project type string or ``"unknown"`` if no recognized manifest exists.
    """
    if (project_root / "pyproject.toml").exists():
        return "python"
    if (project_root / "package.json").exists():
        return "javascript"
    if (project_root / "go.mod").exists():
        return "go"
    if (project_root / "Cargo.toml").exists():
        return "rust"
    return "unknown"


def _discover_commands(
    project_root: Path,
    custom_commands: dict[str, str] | None = None,
) -> dict[str, str]:
    """Discover CI commands from project manifest files and custom config.

    Detects project type by checking for manifest files in priority order:
    1. ``pyproject.toml`` — Python (ruff, pytest, mypy)
    2. ``package.json`` — JavaScript/TypeScript (eslint, npm test)
    3. ``go.mod`` — Go (go vet, go test)
    4. ``Cargo.toml`` — Rust (cargo clippy, cargo test)

    When no recognized manifest exists, returns empty defaults so the gate
    is effectively disabled for unknown project types.

    Custom commands from ``architect.toml`` override built-in defaults by key.
    Custom check names not matching built-in names are added as-is.

    Args:
        project_root: The project root directory.
        custom_commands: Optional mapping of check name to shell command string.

    Returns:
        Mapping of check name to shell command string.
    """
    # Determine project type by manifest file presence (priority order)
    project_type = _detect_project_type(project_root)

    # Start with appropriate defaults for detected project type
    if project_type == "python":
        commands: dict[str, str] = dict(_PYTHON_DEFAULTS)
    elif project_type == "javascript":
        commands = dict(_JAVASCRIPT_DEFAULTS)
    elif project_type == "go":
        commands = dict(_GO_DEFAULTS)
    elif project_type == "rust":
        commands = dict(_RUST_DEFAULTS)
    else:
        # Unknown project type — no recognized manifest
        # Gate effectively disabled (empty commands, all checks will be skipped)
        commands = {}

    # Apply custom commands — they override built-in defaults by key
    if custom_commands:
        commands.update(custom_commands)

    return commands


# ---------------------------------------------------------------------------
# Single check execution
# ---------------------------------------------------------------------------


async def _run_single_check(
    check_name: str,
    command: str,
    project_root: Path,
    timeout: int,
) -> GateCheckResult:
    """Execute a single validation check via subprocess.

    Runs ``command`` in ``project_root`` with a timeout, captures stdout
    and stderr, and returns a structured result.

    Args:
        check_name: Human-readable name (e.g. ``"lint"``).
        command: Shell command to execute.
        project_root: Working directory for the subprocess.
        timeout: Maximum seconds before the check is killed.

    Returns:
        GateCheckResult with pass/fail/error status.
    """
    start = __import__("time").monotonic()
    output_snippet: str = ""
    status: Literal["pass", "fail", "error"] = "pass"

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(project_root),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            elapsed = __import__("time").monotonic() - start
            return GateCheckResult(
                name=check_name,
                status="error",
                output=f"Timed out after {timeout}s",
                duration=round(elapsed, 2),
            )

        elapsed = __import__("time").monotonic() - start
        raw_output = (stdout.decode("utf-8", errors="replace") or "").strip()
        raw_error = (stderr.decode("utf-8", errors="replace") or "").strip()

        # Combine output and error for the snippet
        parts: list[str] = []
        if raw_output:
            parts.append(raw_output)
        if raw_error:
            parts.append(raw_error)

        full_text = "\n".join(parts)
        # Truncate to last 500 characters for brevity
        if len(full_text) > 500:
            output_snippet = "...\n" + full_text[-500:]
        else:
            output_snippet = full_text

        if proc.returncode == 0:
            status = "pass"
        else:
            status = "fail"

        logger.debug(
            "Validation check '{}' {} in {:.1f}s (exit code {})",
            check_name,
            status,
            elapsed,
            proc.returncode,
        )

        return GateCheckResult(
            name=check_name,
            status=status,
            output=output_snippet,
            duration=round(elapsed, 2),
        )

    except FileNotFoundError:
        elapsed = __import__("time").monotonic() - start
        logger.warning(
            "Validation check '{}' — command not found: {}",
            check_name,
            command.split()[0] if command.split() else command,
        )
        return GateCheckResult(
            name=check_name,
            status="error",
            output=f"Command not found: {command.split()[0] if command.split() else command}",
            duration=round(elapsed, 2),
        )
    except OSError as exc:
        elapsed = __import__("time").monotonic() - start
        logger.warning(
            "Validation check '{}' — OS error: {}",
            check_name,
            exc,
        )
        return GateCheckResult(
            name=check_name,
            status="error",
            output=f"OS error: {exc}",
            duration=round(elapsed, 2),
        )


# ---------------------------------------------------------------------------
# Gate runner
# ---------------------------------------------------------------------------


async def run_validation_gate(
    project_root: Path,
    config: ValidationGateConfig,
) -> ValidationGateResult:
    """Execute the configured validation checks and return structured results.

    Runs each enabled check sequentially.  If ``fail_fast`` is ``True``,
    execution stops at the first failed check.  A check that errors
    (command not found, timeout, OS error) counts as a failure for
    ``fail_fast`` purposes.

    Args:
        project_root: The project root directory where commands execute.
        config: Validation gate configuration controlling which checks run.

    Returns:
        ValidationGateResult with per-check results and overall pass/fail.
    """
    if not config.enabled:
        logger.debug("Validation gate disabled — skipping")
        return ValidationGateResult(passed=True, checks=[])

    commands = _discover_commands(project_root, config.custom_commands)
    check_results: list[GateCheckResult] = []

    for check_name in config.checks:
        command = commands.get(check_name)
        if command is None:
            logger.warning("Validation gate — unknown check '{}', skipping", check_name)
            continue

        result = await _run_single_check(
            check_name=check_name,
            command=command,
            project_root=project_root,
            timeout=config.timeout,
        )
        check_results.append(result)

        # fail_fast: stop on first non-pass
        if config.fail_fast and result.status != "pass":
            logger.info(
                "Validation gate — '{}' {} (fail_fast=True, stopping)",
                check_name,
                result.status,
            )
            break

    passed = all(r.status == "pass" for r in check_results)

    if passed:
        logger.info("Validation gate — all {} check(s) passed", len(check_results))
    else:
        failed = [r.name for r in check_results if r.status != "pass"]
        logger.warning("Validation gate — FAILED: {}", ", ".join(failed))

    return ValidationGateResult(passed=passed, checks=check_results)


# ---------------------------------------------------------------------------
# Sync wrapper for non-async callers
# ---------------------------------------------------------------------------


def run_validation_gate_sync(
    project_root: Path,
    config: ValidationGateConfig,
) -> ValidationGateResult:
    """Synchronous wrapper around :func:`run_validation_gate`.

    Spins up a new event loop to run the async checks.  Use this when
    calling from synchronous code that does not have an active event loop.

    Args:
        project_root: The project root directory.
        config: Validation gate configuration.

    Returns:
        ValidationGateResult with per-check results.
    """
    return asyncio.run(run_validation_gate(project_root, config))


# ---------------------------------------------------------------------------
# Fix-up instruction builder
# ---------------------------------------------------------------------------


def _build_fixup_instruction(
    gate_result: ValidationGateResult,
    task_prefix: str,
) -> str:
    """Build a focused fix-up instruction from validation gate failure.

    The instruction is intentionally small (under 4KB) to avoid E2BIG
    when passed as a command-line argument. It references the specific
    failures and asks the agent to fix them.

    Args:
        gate_result: The failed validation gate result.
        task_prefix: The task prefix (e.g. "T04") for context.

    Returns:
        A focused instruction string for the fix-up run.
    """
    lines: list[str] = []
    lines.append(f"=== VALIDATION GATE FIX-UP for {task_prefix} ===")
    lines.append("")
    lines.append(
        "The validation gate failed after completing this task. "
        "Fix the following issues so the gate passes:"
    )
    lines.append("")

    for check in gate_result.checks:
        if check.status != "pass":
            lines.append(f"  [{check.status.upper()}] {check.name} (took {check.duration}s)")
            # Include only the last ~200 chars of output to keep instruction small
            output_snippet = check.output[-200:] if len(check.output) > 200 else check.output
            lines.append(f"    Output: {output_snippet}")
            lines.append("")

    lines.append("INSTRUCTIONS:")
    lines.append("1. Diagnose the root cause of each failing check")
    lines.append("2. Fix the code, tests, or configuration to resolve the failures")
    lines.append(
        "3. If a test is flaky or the timeout is too short, fix the test or adjust the timeout"
    )
    lines.append("4. Do NOT modify unrelated code — only fix what the gate reported")
    lines.append("5. When done, the validation gate (lint, test, typecheck) must pass")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Gate runner with fix-up
# ---------------------------------------------------------------------------


async def run_validation_gate_with_fixup(
    project_root: Path,
    config: ValidationGateConfig,
    *,
    provider: ArchitectProvider | None = None,
    task_prefix: str = "",
    model_override: str | None = None,
    agent_override: str | None = None,
    renderer: StreamRenderer | None = None,
    config_override: Path | None = None,
    log_dir: Path | None = None,
) -> ValidationGateResult:
    """Run the validation gate with automatic fix-up on failure.

    When the gate fails and config.fixup_attempts > 0, invokes the
    provider agent with a targeted fix-up instruction derived from
    the gate output. Re-runs the gate after each fix-up attempt.

    Only gives up after fixup_attempts consecutive failures.

    Args:
        project_root: Project root directory.
        config: Validation gate configuration.
        provider: Provider instance for fix-up runs. Required when
            fixup_attempts > 0.
        task_prefix: Task prefix for fix-up instruction context.
        model_override: Model override for fix-up runs.
        agent_override: Agent override for fix-up runs.
        renderer: Renderer for fix-up output.
        config_override: Config override for fix-up runs.
        log_dir: Directory for fix-up log files.

    Returns:
        The final ValidationGateResult (passed or failed).
    """
    # Run the gate initially
    gate_result = await run_validation_gate(project_root, config)

    if gate_result.passed or config.fixup_attempts <= 0:
        return gate_result

    logger.warning(
        f"Validation gate failed — starting fix-up flow (max {config.fixup_attempts} attempt(s))"
    )

    if provider is None:
        logger.warning(
            "Validation gate fix-up requires a provider — skipping fix-up, returning failed result"
        )
        return gate_result

    # Import stream_provider for fix-up runs
    from the_architect.core.runner import stream_provider

    for attempt in range(1, config.fixup_attempts + 1):
        failed_checks = [r.name for r in gate_result.checks if r.status != "pass"]
        logger.info(
            "Validation gate fix-up attempt {}/{} for failed check(s): {}",
            attempt,
            config.fixup_attempts,
            ", ".join(failed_checks),
        )

        # Build focused fix-up instruction
        instruction = _build_fixup_instruction(gate_result, task_prefix)

        # Log file for fix-up
        fixup_log: Path | None = None
        if log_dir is not None:
            fixup_log = log_dir / f"fixup_{task_prefix}_{attempt}.log"

        try:
            stream_result = await stream_provider(
                instruction=instruction,
                project_dir=project_root,
                provider=provider,
                model_override=model_override,
                agent_override=agent_override,
                log_path=fixup_log,
                config_override=config_override,
                renderer=renderer,
                task_timeout_seconds=0,  # Use provider default
            )

            if stream_result.exit_code != 0:
                logger.warning(
                    "Fix-up attempt {} exited with code {}",
                    attempt,
                    stream_result.exit_code,
                )
            else:
                logger.info("Fix-up attempt {} completed (exit code 0)", attempt)

        except Exception as exc:
            logger.error("Fix-up attempt {} raised: {!r}", attempt, exc)

        # Re-run the gate to check if fix-up worked
        gate_result = await run_validation_gate(project_root, config)

        if gate_result.passed:
            logger.info("Validation gate PASSED after fix-up attempt {}", attempt)
            return gate_result

        still_failed = [r.name for r in gate_result.checks if r.status != "pass"]
        logger.warning(
            "Validation gate still failed after fix-up attempt {} — failed check(s): {}",
            attempt,
            ", ".join(still_failed),
        )

    # All fix-up attempts exhausted
    logger.error(
        "Validation gate failed after {} fix-up attempt(s) — task will be reclassified as failed",
        config.fixup_attempts,
    )
    return gate_result
