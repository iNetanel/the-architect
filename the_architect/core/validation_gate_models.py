"""Pydantic models for the post-task validation gate.

Defines the configuration and result types used by
:mod:`the_architect.core.validation_gate`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class ValidationGateConfig(BaseModel):
    """Configuration for the post-task validation gate.

    Controls which CI checks run after each task, timeout behaviour,
    fail-fast mode, custom command overrides, and fix-up recovery.

    Attributes:
        enabled: When ``True``, the gate runs after each task. Default ``True``.
        checks: List of check names to execute. Default ``["lint", "test", "typecheck"]``.
            Custom check names are resolved from ``custom_commands``.
        custom_commands: Mapping of check name to shell command string. Custom commands
            override built-in defaults when keys match. Default empty dict.
        fail_fast: When ``True``, stop at the first failed check. Default ``True``.
        timeout: Maximum seconds per check before killing the subprocess. Default ``120``.
        fixup_attempts: Number of fix-up attempts when the gate fails. The provider
            agent is invoked with a targeted fix-up instruction to repair failing
            checks. Default ``2``. Set to ``0`` to disable (immediate failure).
    """

    enabled: bool = Field(
        default=True,
        description=(
            "When True, the validation gate runs CI checks after each task. "
            "Set to False to disable post-task validation entirely."
        ),
    )
    checks: list[str] = Field(
        default=["lint", "test", "typecheck"],
        description=(
            "List of check names to run. Built-in values: 'lint', 'test', 'typecheck'. "
            "Custom check names are resolved from custom_commands. "
            "All listed checks run unless fail_fast stops early."
        ),
    )
    custom_commands: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Custom check commands. Keys are check names (e.g. 'build', 'security'), "
            "values are shell command strings. Custom commands override built-in defaults "
            "when keys match ('lint', 'test', 'typecheck'). Empty dict means use defaults."
        ),
    )
    fail_fast: bool = Field(
        default=True,
        description=(
            "When True, stop executing checks at the first failure. "
            "When False, run all configured checks regardless of failures."
        ),
    )
    timeout: int = Field(
        default=120,
        ge=1,
        description=(
            "Maximum seconds allowed for a single check before the subprocess "
            "is killed. A timed-out check is reported as 'error'."
        ),
    )
    fixup_attempts: int = Field(
        default=2,
        ge=0,
        description=(
            "Number of fix-up attempts when the validation gate fails. "
            "When >0, the provider agent is invoked with a targeted fix-up "
            "instruction (derived from gate output) to repair the failing "
            "checks. After fixup_attempts consecutive failures, the task "
            "is reclassified as failed. Set to 0 to disable fix-up "
            "(previous behaviour: immediate failure on gate failure)."
        ),
    )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


class GateCheckResult(BaseModel):
    """Result of a single validation check.

    Attributes:
        name: Check identifier (e.g. ``"lint"``).
        status: One of ``"pass"``, ``"fail"``, or ``"error"``.
        output: Truncated stdout/stderr from the check (last 500 chars).
        duration: Wall-clock seconds the check took.
    """

    name: str = Field(description="Check name (e.g. 'lint', 'test', 'typecheck')")
    status: Literal["pass", "fail", "error"] = Field(
        description=(
            "Result status: 'pass' = clean, 'fail' = non-zero exit, 'error' = execution problem"
        )
    )
    output: str = Field(
        default="",
        description="Truncated command output (last 500 characters)",
    )
    duration: float = Field(default=0.0, description="Execution time in seconds")


class ValidationGateResult(BaseModel):
    """Aggregated result of a validation gate run.

    Attributes:
        passed: ``True`` if all executed checks passed.
        checks: Per-check results in execution order.
    """

    passed: bool = Field(description="True if all executed checks passed")
    checks: list[GateCheckResult] = Field(
        default_factory=list,
        description="Per-check results in execution order",
    )
