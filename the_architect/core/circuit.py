"""Circuit breaker for The Architect task execution.

Detects three failure patterns that plain max_retries cannot catch:

1. **Silent no-progress** — the agent runs and exits cleanly but writes no files.
2. **Repeated identical errors** — the same bash error appears every attempt.
3. **Provider cooldown** — the provider returns a rate-limit / overload signal.

When a threshold is breached the circuit opens, the task is skipped, and a
recovery action is chosen:

- ``WAIT``        — let normal retry / model-rotation happen first.
- ``REPLAN``      — send the failing task back to the architect to be rewritten.
- ``COOLDOWN_WAIT`` — provider is rate-limited; pause the entire run for 1 hour
                     then retry.  Does NOT open the circuit, does NOT increment
                     any threshold counters.

State is persisted to ``.architect/circuit.json`` so it survives process
restarts, including mid-cooldown-wait restarts.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from the_architect.config import ArchitectConfig
    from the_architect.core.provider import ArchitectProvider


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CIRCUIT_STATE_FILE = Path(".architect/circuit.json")
_TOKEN_HISTORY_CAP = 10


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CircuitState(StrEnum):
    """Possible states of a circuit breaker for a single task."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class RecoveryAction(StrEnum):
    """Recommended recovery action when the circuit opens or a cooldown is hit."""

    WAIT = "WAIT"
    REPLAN = "REPLAN"
    COOLDOWN_WAIT = "COOLDOWN_WAIT"


# ---------------------------------------------------------------------------
# Per-attempt data
# ---------------------------------------------------------------------------


class AttemptSummary(BaseModel):
    """Structured data about a single task attempt, fed to the circuit breaker.

    The runner builds this from the log file and stream result after each
    attempt.  The circuit breaker never does its own log parsing.
    """

    task_id: str = Field(description="Task prefix, e.g. T03")
    attempt_number: int = Field(description="1-based attempt number")
    completion_detected: bool = Field(description="True if the task was marked Done")
    files_written: list[str] = Field(
        default_factory=list,
        description="Paths of files written or edited during this attempt (project-relative)",
    )
    bash_commands_run: int = Field(default=0, description="Number of bash commands executed")
    bash_errors: list[str] = Field(
        default_factory=list,
        description="Error text from failed bash commands",
    )
    total_tokens: int = Field(
        default=0, description="Total tokens used (input + output); 0 if unavailable"
    )
    accumulated_text: str = Field(
        default="",
        description=(
            "Full accumulated text output from the agent (used for cooldown signal detection)"
        ),
    )
    exit_code: int = Field(
        default=0,
        description="opencode subprocess exit code (used for HTTP status code detection)",
    )
    rate_limit_hit: bool = Field(
        default=False,
        description=(
            "True if the provider signalled a rate limit via a structured event "
            "(rate_limit_event, api_error_status 429/529, error='rate_limit') — "
            "reliable even when accumulated_text is empty."
        ),
    )
    cooldown_until: int = Field(
        default=0,
        description=(
            "Unix timestamp when the provider cooldown resets (from rate_limit_event.resetsAt). "
            "0 means not set.  When non-zero, used for precise cooldown timing."
        ),
    )


# ---------------------------------------------------------------------------
# Per-task circuit state (persisted)
# ---------------------------------------------------------------------------


class TaskCircuitState(BaseModel):
    """Persisted circuit breaker state for a single task."""

    state: CircuitState = Field(default=CircuitState.CLOSED)
    consecutive_no_progress: int = Field(default=0)
    consecutive_same_error: int = Field(default=0)
    last_error_fingerprint: str | None = Field(default=None)
    token_history: list[int] = Field(
        default_factory=list, description="Last N attempt token counts"
    )
    opened_at: str | None = Field(default=None, description="ISO timestamp when circuit opened")
    recovery_action: RecoveryAction | None = Field(default=None)
    replan_attempted: bool = Field(default=False)
    any_file_progress: bool = Field(
        default=False,
        description="True if at least one attempt wrote files since the last reset",
    )
    # ── Cooldown wait state ───────────────────────────────────────────────
    cooldown_waiting: bool = Field(
        default=False,
        description="True if this task is currently in a provider cooldown wait",
    )
    cooldown_wait_started_at: str | None = Field(
        default=None,
        description="ISO timestamp when the current cooldown wait began",
    )
    cooldown_wait_count: int = Field(
        default=0,
        description="Total number of cooldown waits that have occurred for this task",
    )

    model_config = {"frozen": False}


# ---------------------------------------------------------------------------
# Error fingerprinting
# ---------------------------------------------------------------------------


_PATH_RE = re.compile(r"/[\w./\-]+\.[\w]+")
_LINE_NUM_RE = re.compile(r":\d+")
_WHITESPACE_RE = re.compile(r"\s+")


def _fingerprint_error(error_text: str) -> str:
    """Create a normalised fingerprint from bash error output.

    Strips file paths and line numbers so the same underlying error
    produces the same fingerprint even when surface details vary.

    Args:
        error_text: Raw error text from a failed bash command.

    Returns:
        A normalised, lower-cased fingerprint string.
    """
    text = _PATH_RE.sub("<path>", error_text)
    text = _LINE_NUM_RE.sub(":<N>", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip().lower()[:300]


# ---------------------------------------------------------------------------
# Cooldown signal detection
# ---------------------------------------------------------------------------

# Minimum wait for a provider cooldown — never wait less than this.
_COOLDOWN_MIN_SECONDS = 3600  # 1 hour

# Text patterns that indicate the provider is rate-limiting or overloaded.
# Case-insensitive substring matching — covers Anthropic, OpenAI, OpenRouter,
# Claude Code CLI, and most common providers.
#
# Claude Code CLI specific patterns:
#   "you're out of extra usage"   — daily/monthly quota exhausted
#   "out of extra usage"          — same, without the contraction
#   "usage limit reached"         — alternative phrasing
#   "credit balance is too low"   — Anthropic API credit exhausted
#   "your account has run out"    — account-level quota
#   "resets"                      — appears alongside quota messages ("resets 11pm UTC")
#                                   NOT added alone — too broad; paired with quota check
_COOLDOWN_TEXT_PATTERNS: list[str] = [
    # Generic / OpenAI / OpenRouter
    "rate limit",
    "rate_limit",
    "too many requests",
    "usage limit",
    "please wait",
    "try again in",
    "retry after",
    "overloaded",
    "capacity",
    "quota exceeded",
    "quota_exceeded",
    # Anthropic / Claude Code CLI specific
    "out of extra usage",
    "usage limit reached",
    "credit balance is too low",
    "your account has run out",
    "exceeded your current quota",
    "billing hard limit",
]

# HTTP status codes that indicate cooldown / rate-limiting.
_COOLDOWN_STATUS_CODES: set[int] = {429, 529}

# Regex to extract a suggested wait time in seconds from provider messages.
# Matches patterns like "retry after 3600 seconds", "please wait 1 hour",
# "try again in 30 minutes", "retry after 1h", etc.
_WAIT_TIME_RE = re.compile(
    r"(?:retry after|try again in|please wait|wait)\s+"
    r"(\d+(?:\.\d+)?)\s*(second|sec|minute|min|hour|hr|h|s|m)\w*",
    re.IGNORECASE,
)


def detect_cooldown_signal(text: str, exit_code: int) -> tuple[bool, int, str]:
    """Detect whether a provider cooldown / rate-limit signal is present.

    Checks accumulated text output and exit code for known cooldown
    indicators.  If a suggested wait time is found in the text and it
    exceeds 1 hour, that wait time is used; otherwise 1 hour is used.

    Args:
        text: Accumulated text output from the agent run.
        exit_code: The opencode subprocess exit code.

    Returns:
        ``(detected, wait_seconds, matched_signal)`` where:

        - ``detected`` — True if a cooldown signal was found.
        - ``wait_seconds`` — How long to wait (minimum 3600, i.e. 1 hour).
        - ``matched_signal`` — The specific pattern or status code that matched,
          for logging.
    """
    text_lower = text.lower()

    matched_signal = ""

    # Check HTTP status codes first (highest confidence)
    if exit_code in _COOLDOWN_STATUS_CODES:
        matched_signal = f"HTTP {exit_code}"

    # Check text patterns
    if not matched_signal:
        for pattern in _COOLDOWN_TEXT_PATTERNS:
            if pattern in text_lower:
                matched_signal = f'"{pattern}"'
                break

    if not matched_signal:
        return False, _COOLDOWN_MIN_SECONDS, ""

    # Try to extract a suggested wait time from the text
    wait_seconds = _COOLDOWN_MIN_SECONDS
    match = _WAIT_TIME_RE.search(text)
    if match:
        try:
            value = float(match.group(1))
            unit = match.group(2).lower()
            if unit in ("h", "hr", "hour"):
                suggested = int(value * 3600)
            elif unit in ("m", "min", "minute"):
                suggested = int(value * 60)
            else:
                # seconds
                suggested = int(value)
            # Only use the suggested time if it is longer than 1 hour
            if suggested > _COOLDOWN_MIN_SECONDS:
                wait_seconds = suggested
        except (ValueError, IndexError):
            pass

    return True, wait_seconds, matched_signal


# ---------------------------------------------------------------------------
# Provider error detection — actionable errors that require user intervention
# ----------------------------------------------------------------- ----------


class ProviderErrorKind(StrEnum):
    """Categories of actionable provider errors."""

    UPDATE_REQUIRED = "UPDATE_REQUIRED"
    """Provider needs an update before it can run."""

    MISCONFIGURED = "MISCONFIGURED"
    """Provider is installed but not properly configured."""

    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    """Provider account has no usable quota, credits, or billing budget."""

    UNKNOWN = "UNKNOWN"
    """Provider exited with an error but the cause is unclear."""


class ProviderError(BaseModel):
    """An actionable error detected from provider output."""

    kind: ProviderErrorKind = Field(description="Category of the error")
    message: str = Field(description="Human-readable error description")
    action: str = Field(description="Suggested action the user should take")
    raw_text: str = Field(default="", description="Raw text that triggered detection")


# Text patterns that indicate the provider needs an update.
# OpenCode patterns:
#   "A new version of opencode is available"
#   "please update opencode"
#   "update required"
#   "you must update"
#   "version is outdated"
# Claude Code patterns:
#   "please update claude code"
#   "A new version of Claude Code is available"
_UPDATE_REQUIRED_PATTERNS: list[str] = [
    "new version",
    "update required",
    "please update",
    "must update",
    "version is outdated",
    "outdated version",
    "needs to be updated",
    "must be updated",
    "upgrade required",
    "please upgrade",
]

# Text patterns that indicate waiting/retrying will not help without user action.
# These are intentionally checked before transient cooldown handling so provider
# budget/billing failures do not turn into a misleading 1-hour wait loop.
_QUOTA_EXHAUSTED_PATTERNS: list[str] = [
    "insufficient quota",
    "insufficient_quota",
    "quota exceeded",
    "quota_exceeded",
    "quota exhausted",
    "free quota exhausted",
    "exceeded your current quota",
    "billing hard limit",
    "billing not enabled",
    "billing account",
    "credit balance is too low",
    "out of credits",
    "out_of_credits",
    "out of extra usage",
    "your account has run out",
    "no budget",
    "budget exhausted",
    "resource_exhausted",
]

# Text patterns that indicate the provider is misconfigured
# (not an update issue, not a rate limit — something else is wrong).
_MISCONFIGURED_PATTERNS: list[str] = [
    "api key",
    "authentication failed",
    "unauthorized",
    "invalid api key",
    "no api key",
    "missing api key",
    "not authenticated",
    "login required",
    "please sign in",
]


def detect_provider_error(text: str, exit_code: int) -> ProviderError | None:
    """Detect actionable provider errors from accumulated output.

    Unlike ``detect_cooldown_signal`` which finds transient rate-limit
    issues that resolve with waiting, this function finds errors that
    require *user action* — the provider will keep failing until the
    user fixes something (updates, reconfigures, etc.).

    Args:
        text: Accumulated text output from the provider run.
        exit_code: The provider subprocess exit code.

    Returns:
        A :class:`ProviderError` if an actionable error is detected, or
        ``None`` if no actionable error is found (the failure may be
        transient or the cause is unknown).
    """
    if exit_code == 0 and not text.strip():
        return None

    text_lower = text.lower()

    # Check for update-required patterns first (most actionable)
    for pattern in _UPDATE_REQUIRED_PATTERNS:
        if pattern in text_lower:
            # Determine which provider based on text content
            if "opencode" in text_lower:
                action = "Run: opencode upgrade"
            elif "claude" in text_lower:
                action = "Run: claude update"
            else:
                action = "Update your AI CLI tool to the latest version"

            return ProviderError(
                kind=ProviderErrorKind.UPDATE_REQUIRED,
                message=f'Provider requires an update (matched: "{pattern}")',
                action=action,
                raw_text=text[:500],
            )

    # Check for account quota / billing exhaustion before generic
    # misconfiguration. These are not transient cooldowns: the user must switch
    # provider/model, add credits, enable billing, or wait for their provider's
    # own quota reset outside The Architect.
    for pattern in _QUOTA_EXHAUSTED_PATTERNS:
        if pattern in text_lower:
            return ProviderError(
                kind=ProviderErrorKind.QUOTA_EXHAUSTED,
                message=f'Provider account has no usable quota or budget (matched: "{pattern}")',
                action=(
                    "Switch provider/model, add credits or enable billing, then rerun The Architect"
                ),
                raw_text=text[:500],
            )

    # Check for misconfiguration patterns
    for pattern in _MISCONFIGURED_PATTERNS:
        if pattern in text_lower:
            return ProviderError(
                kind=ProviderErrorKind.MISCONFIGURED,
                message=f'Provider is misconfigured (matched: "{pattern}")',
                action="Check your API key configuration and provider authentication",
                raw_text=text[:500],
            )

    if exit_code == 0:
        return None

    # Non-zero exit code but no recognized pattern — return a generic error
    # so the caller can at least show *something* useful instead of "no tasks created"
    if text.strip():
        # Show a snippet of the error output so the user isn't blind
        snippet = text.strip()[:200]
        return ProviderError(
            kind=ProviderErrorKind.UNKNOWN,
            message=f"Provider exited with code {exit_code}",
            action=f"Provider output: {snippet}",
            raw_text=text[:500],
        )

    return None


# ---------------------------------------------------------------------------
# Circuit breaker logic
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """Evaluates attempt summaries and manages per-task circuit state.

    Instantiate once per run, load state from disk, then call
    :meth:`record_attempt` after every attempt and :meth:`can_run`
    before every attempt.

    Args:
        config: The resolved The Architect configuration.
        project_root: The project root directory (used to filter file paths
            and to locate the state file).
        free_rotator: Optional :class:`~the_architect.core.free_models.FreeModelRotator`
            instance.  When provided, the cooldown check asks whether free mode
            still has unexhausted models available before triggering
            ``COOLDOWN_WAIT`` — if models are available, the failure is handed
            to free-mode rotation instead.
    """

    def __init__(
        self,
        config: ArchitectConfig,
        project_root: Path,
        free_rotator: object | None = None,
        provider: ArchitectProvider | None = None,
    ) -> None:
        self._config = config
        self._project_root = project_root.resolve()
        self._states: dict[str, TaskCircuitState] = {}
        self._state_file = project_root / CIRCUIT_STATE_FILE
        self._free_rotator = free_rotator
        self._provider: ArchitectProvider | None = provider
        # Track whether we've already logged "cooldown detection disabled"
        self._cooldown_disabled_logged = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load circuit state from disk.

        Safe to call even when the file does not exist (starts fresh).
        If the file is malformed, logs a warning and starts fresh.
        """
        if not self._state_file.exists():
            logger.debug("Circuit state file not found — starting fresh")
            return

        try:
            raw = self._state_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object at top level")
            for task_id, raw_state in data.items():
                try:
                    self._states[task_id] = TaskCircuitState.model_validate(raw_state)
                except Exception as exc:
                    logger.warning(f"Circuit: ignoring malformed state for {task_id}: {exc}")
            logger.debug(f"Circuit: loaded state for {len(self._states)} task(s)")
        except Exception as exc:
            logger.warning(f"Circuit: malformed state file — starting fresh ({exc})")
            self._states = {}

    def save(self) -> None:
        """Persist current circuit state to disk.

        Creates the ``.architect/`` directory if needed.  Errors are logged
        but never raised — a failed save must not crash the run.
        """
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            data = {task_id: state.model_dump() for task_id, state in self._states.items()}
            self._state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            logger.debug(f"Circuit: saved state for {len(self._states)} task(s)")
        except Exception as exc:
            logger.error(f"Circuit: failed to save state file: {exc!r}")

    def can_run(self, task_id: str) -> tuple[bool, str]:
        """Check whether a task is allowed to run.

        Also handles cooldown-wait resume on process restart: if the task
        was in a cooldown wait when the process was killed, checks whether
        1 hour has elapsed.  If yes, clears the flag and allows the run.
        If no, returns ``(False, reason)`` so the caller can resume the wait.

        Args:
            task_id: Task prefix, e.g. ``T03``.

        Returns:
            ``(allowed, reason)`` — if ``allowed`` is False, ``reason``
            explains why the task is being skipped.
        """
        state = self._get_state(task_id)

        # ── Resume cooldown wait from a previous run ────────────────────
        if state.cooldown_waiting and state.cooldown_wait_started_at:
            elapsed = self._seconds_since(state.cooldown_wait_started_at)
            remaining = _COOLDOWN_MIN_SECONDS - elapsed
            if remaining > 0:
                logger.info(
                    f"Circuit {task_id}: resuming cooldown wait from previous run — "
                    f"{int(remaining)}s remaining (wait #{state.cooldown_wait_count})"
                )
                return False, f"cooldown_wait_resume:{int(remaining)}"
            else:
                # Wait has elapsed — clear and allow
                logger.info(
                    f"Circuit {task_id}: cooldown wait elapsed since last run — proceeding to retry"
                )
                state.cooldown_waiting = False
                state.cooldown_wait_started_at = None
                self.save()

        if state.state == CircuitState.OPEN:
            # Check if circuit cooldown has elapsed → transition to HALF_OPEN
            if state.opened_at and self._cooldown_elapsed(state.opened_at):
                logger.info(f"Circuit {task_id}: OPEN → HALF_OPEN (cooldown elapsed)")
                state.state = CircuitState.HALF_OPEN
                self.save()
                return True, "half-open test attempt"

            opened_ago = self._seconds_since(state.opened_at) if state.opened_at else 0
            reason = (
                f"circuit is OPEN (recovery={state.recovery_action}, "
                f"opened {int(opened_ago)}s ago, "
                f"no_progress={state.consecutive_no_progress}, "
                f"same_error={state.consecutive_same_error})"
            )
            logger.warning(f"Circuit {task_id}: skipping — {reason}")
            return False, reason

        return True, "ok"

    def record_attempt(self, summary: AttemptSummary) -> TaskCircuitState:
        """Update circuit state after a completed attempt.

        **Evaluation order (most important first):**

        1. If free mode has unexhausted models → defer to free-mode rotation,
           skip all circuit evaluation.
        2. If cooldown signal detected and ``cooldown_detection=True`` → set
           ``COOLDOWN_WAIT``, do NOT increment any counters, do NOT change
           circuit state.
        3. Normal threshold evaluation (no-progress, same-error, token decline).

        If the circuit was HALF_OPEN:

        - success → reset to CLOSED
        - failure → re-open (reset ``opened_at`` to now)

        Args:
            summary: Structured data about the completed attempt.

        Returns:
            The updated :class:`TaskCircuitState` for this task.
        """
        task_id = summary.task_id
        state = self._get_state(task_id)

        # ── HALF_OPEN resolution ────────────────────────────────────────
        if state.state == CircuitState.HALF_OPEN:
            if summary.completion_detected:
                logger.info(f"Circuit {task_id}: HALF_OPEN → CLOSED (test attempt succeeded)")
                self._reset_state(task_id)
                self.save()
                return self._get_state(task_id)
            else:
                logger.warning(f"Circuit {task_id}: HALF_OPEN → OPEN (test attempt failed)")
                state.opened_at = _now_iso()
                state.state = CircuitState.OPEN
                self.save()
                return state

        # ── Already OPEN — nothing to update ───────────────────────────
        if state.state == CircuitState.OPEN:
            self.save()
            return state

        # ── CLOSED — evaluate in priority order ────────────────────────

        # 1. Free mode with available models → hand off, skip CB evaluation
        if self._free_rotator_has_models():
            logger.debug(
                f"Circuit {task_id}: free mode has models available — "
                "deferring to free-mode rotation, skipping CB evaluation"
            )
            self.save()
            return state

        # 2. Cooldown detection (before any counter increments)
        if not summary.completion_detected:
            cooldown_result = self._check_cooldown(task_id, summary, state)
            if cooldown_result is not None:
                # Cooldown detected — persist state and return immediately.
                # Counters are NOT touched.
                self.save()
                return state

        # 3. Token history (only for non-cooldown attempts)
        if summary.total_tokens > 0:
            state.token_history.append(summary.total_tokens)
            if len(state.token_history) > _TOKEN_HISTORY_CAP:
                state.token_history = state.token_history[-_TOKEN_HISTORY_CAP:]

        # Success resets all counters
        if summary.completion_detected:
            self._reset_state(task_id)
            self.save()
            return self._get_state(task_id)

        # ── No-progress counter ─────────────────────────────────────────
        project_files = self._filter_project_files(summary.files_written)
        if project_files:
            logger.debug(
                f"Circuit {task_id}: {len(project_files)} file(s) written — "
                "resetting no-progress counter"
            )
            state.consecutive_no_progress = 0
            state.any_file_progress = True
        else:
            state.consecutive_no_progress += 1
            logger.debug(
                f"Circuit {task_id}: no file progress — "
                f"consecutive_no_progress={state.consecutive_no_progress}"
            )

        # ── Same-error counter ──────────────────────────────────────────
        if summary.bash_errors:
            combined_error = " ".join(summary.bash_errors)
            fingerprint = _fingerprint_error(combined_error)
            if fingerprint == state.last_error_fingerprint:
                state.consecutive_same_error += 1
                logger.debug(
                    f"Circuit {task_id}: same error fingerprint — "
                    f"consecutive_same_error={state.consecutive_same_error}"
                )
            else:
                state.last_error_fingerprint = fingerprint
                state.consecutive_same_error = 1
                logger.debug(
                    f"Circuit {task_id}: new error fingerprint — resetting same-error counter to 1"
                )
        else:
            # A clean attempt (no bash errors) breaks the same-error streak.
            # Leaving the counter unchanged would let alternating error/clean
            # attempts still trip the threshold — a false positive.
            if state.consecutive_same_error > 0:
                logger.debug(f"Circuit {task_id}: no bash errors — resetting same-error counter")
            state.consecutive_same_error = 0
            state.last_error_fingerprint = None

        # ── Check thresholds ────────────────────────────────────────────
        no_prog_threshold = self._config.circuit_no_progress_threshold
        same_err_threshold = self._config.circuit_same_error_threshold
        token_decline_pct = self._config.circuit_token_decline_pct

        tripped_reason: str | None = None

        if no_prog_threshold > 0 and state.consecutive_no_progress >= no_prog_threshold:
            tripped_reason = (
                f"no-progress threshold reached "
                f"({state.consecutive_no_progress}/{no_prog_threshold})"
            )

        elif same_err_threshold > 0 and state.consecutive_same_error >= same_err_threshold:
            tripped_reason = (
                f"same-error threshold reached "
                f"({state.consecutive_same_error}/{same_err_threshold})"
            )

        elif token_decline_pct > 0 and self._token_decline_tripped(state, token_decline_pct):
            # Token decline is only a trigger when at least one other counter is elevated
            if state.consecutive_no_progress > 0 or state.consecutive_same_error > 0:
                tripped_reason = (
                    f"token decline >{token_decline_pct}% with corroborating signal "
                    f"(no_progress={state.consecutive_no_progress}, "
                    f"same_error={state.consecutive_same_error})"
                )
            else:
                logger.debug(
                    f"Circuit {task_id}: token decline detected but no corroborating signal — "
                    "not tripping"
                )

        if tripped_reason:
            state.state = CircuitState.OPEN
            state.opened_at = _now_iso()
            state.recovery_action = self._choose_recovery(task_id, state, summary)
            logger.warning(
                f"Circuit {task_id}: CLOSED → OPEN — {tripped_reason} — "
                f"recovery={state.recovery_action}"
            )

        self.save()
        return state

    async def handle_cooldown_wait(self, task_id: str) -> None:
        """Pause the run for the provider cooldown wait period.

        Called by the runner when ``record_attempt`` has set
        ``cooldown_waiting=True`` on the task state.  Waits for the
        remaining duration (accounting for time already elapsed since
        ``cooldown_wait_started_at``), logging progress every minute.

        After the wait completes, clears the cooldown flag so the task
        can be retried.

        Args:
            task_id: Task prefix, e.g. ``T03``.
        """
        import asyncio

        state = self._get_state(task_id)
        if not state.cooldown_waiting or not state.cooldown_wait_started_at:
            return

        elapsed = self._seconds_since(state.cooldown_wait_started_at)
        remaining = max(0.0, _COOLDOWN_MIN_SECONDS - elapsed)

        logger.info(
            f"Circuit {task_id}: COOLDOWN_WAIT — pausing run for {int(remaining)}s "
            f"(wait #{state.cooldown_wait_count})"
        )

        # Sleep in 60-second chunks, logging progress each minute
        waited = 0.0
        while waited < remaining:
            chunk = min(60.0, remaining - waited)
            try:
                await asyncio.sleep(chunk)
            except asyncio.CancelledError:
                logger.warning(f"Circuit {task_id}: cooldown wait interrupted")
                break
            waited += chunk
            still_remaining = remaining - waited
            if still_remaining > 0:
                logger.info(
                    f"Circuit {task_id}: cooldown wait in progress — "
                    f"{int(still_remaining)}s remaining (wait #{state.cooldown_wait_count})"
                )

        # Clear cooldown flag — task is ready to retry
        state.cooldown_waiting = False
        state.cooldown_wait_started_at = None
        self.save()
        logger.info(f"Circuit {task_id}: cooldown wait complete — retrying")

    def reset_task(self, task_id: str) -> None:
        """Manually reset a task's circuit state to CLOSED with zeroed counters.

        Args:
            task_id: Task prefix, e.g. ``T03``.
        """
        self._reset_state(task_id)
        self.save()
        logger.info(f"Circuit {task_id}: manually reset to CLOSED")

    def all_states(self) -> dict[str, TaskCircuitState]:
        """Return a snapshot of all tracked circuit states.

        Returns:
            Dict mapping task ID to its :class:`TaskCircuitState`.
        """
        return dict(self._states)

    # ------------------------------------------------------------------
    # Replan path
    # ------------------------------------------------------------------

    async def attempt_replan(
        self,
        task_id: str,
        task_file: Path,
        progress_file: Path,
    ) -> bool:
        """Ask the architect to rewrite a failing task.

        Collects the task file content, attempt history from the circuit
        state, and PROGRESS.md, then calls the architect agent via
        ``stream_opencode``.  After the call, discovers any new or modified
        task files and resets the circuit state for the original task.

        If the architect call fails for any reason, logs the error and
        returns False — the caller should fall back to WAIT.

        Args:
            task_id: The failing task's prefix (e.g. ``T03``).
            task_file: Absolute path to the failing task's ``.md`` file.
            progress_file: Absolute path to PROGRESS.md.

        Returns:
            True if the replan completed (even if no new tasks were created),
            False if the architect call failed.
        """
        from the_architect.core.runner import stream_provider
        from the_architect.core.tasks import discover_tasks

        state = self._get_state(task_id)

        # Mark replan attempted before the call so we never retry even if
        # the call crashes halfway through.
        state.replan_attempted = True
        self.save()

        logger.info(f"Circuit {task_id}: triggering REPLAN via architect agent")

        # ── Resolve provider ────────────────────────────────────────────
        if self._provider is not None:
            provider = self._provider
        else:
            from the_architect.core.opencode_provider import OpenCodeProvider

            provider = OpenCodeProvider()

        # ── Build the replan prompt ─────────────────────────────────────
        try:
            task_content = task_file.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error(f"Circuit replan: cannot read task file {task_file}: {exc!r}")
            return False

        try:
            progress_content = (
                progress_file.read_text(encoding="utf-8") if progress_file.exists() else ""
            )
        except OSError:
            progress_content = ""

        attempt_summary_text = (
            f"no_progress_count={state.consecutive_no_progress}, "
            f"same_error_count={state.consecutive_same_error}, "
            f"last_error_fingerprint={state.last_error_fingerprint or 'none'}"
        )

        instruction = _build_replan_instruction(
            task_id=task_id,
            task_content=task_content,
            attempt_summary=attempt_summary_text,
            progress_content=progress_content,
            project_root=self._project_root,
        )

        # For Claude Code: prepend architect prompt (no named agents)
        if not provider.supports_agents():
            from the_architect.core.claude_code_provider import ClaudeCodeProvider

            if isinstance(provider, ClaudeCodeProvider):
                architect_prompt = provider.get_architect_prompt()
                instruction = f"{architect_prompt}\n\n---\n\n{instruction}"

        # ── Call the architect via the active provider ──────────────────
        try:
            from the_architect.core.provider_setup import ensure_provider_setup

            ensure_provider_setup(provider, self._project_root, self._config)
            model_override = self._config.standalone_mode or None

            # Config override and agent override only apply to OpenCode
            config_override = None
            agent_override = None
            if provider.supports_agents():
                config_override = self._project_root / ".architect" / "architect.json"
                agent_override = "architect"

            stream_result = await stream_provider(
                instruction=instruction,
                project_dir=self._project_root,
                provider=provider,
                model_override=model_override,
                agent_override=agent_override,
                log_path=self._config.log_dir / f"replan_{task_id}.log",
                config_override=config_override,
            )

            if stream_result.exit_code != 0:
                logger.warning(
                    f"Circuit replan: architect exited with code "
                    f"{stream_result.exit_code} for {task_id}"
                )
        except Exception as exc:
            logger.error(f"Circuit replan: architect call failed for {task_id}: {exc!r}")
            # Fall back to WAIT — do not crash the run
            state.recovery_action = RecoveryAction.WAIT
            self.save()
            return False

        # ── Discover new/modified task files ───────────────────────────
        tasks_dir = self._project_root / self._config.tasks_dir.name
        tasks_after = discover_tasks(tasks_dir)
        new_task_ids = [t.prefix for t in tasks_after if t.prefix != task_id]

        # Reset circuit state for the original task and any new tasks
        self._reset_state(task_id)
        for new_id in new_task_ids:
            if new_id not in self._states:
                self._states[new_id] = TaskCircuitState()
        self.save()

        logger.info(
            f"Circuit replan for {task_id} complete — "
            f"reset original, {len(new_task_ids)} other task(s) in plan"
        )
        return True

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _free_rotator_has_models(self) -> bool:
        """Return True if free mode is active and has unexhausted models.

        Returns:
            True when the free rotator is set and ``has_models_available``
            is True.  False in all other cases (no rotator, or all exhausted).
        """
        if self._free_rotator is None:
            return False
        has_attr = hasattr(self._free_rotator, "has_models_available")
        if not has_attr:
            return False
        return bool(getattr(self._free_rotator, "has_models_available"))

    def _check_cooldown(
        self,
        task_id: str,
        summary: AttemptSummary,
        state: TaskCircuitState,
    ) -> str | None:
        """Check for a provider cooldown signal and update state if detected.

        Returns the matched signal string if cooldown was detected (so the
        caller knows to skip counter updates), or ``None`` if no cooldown.

        When cooldown is detected:
        - Sets ``state.cooldown_waiting = True``
        - Sets ``state.cooldown_wait_started_at`` to now
        - Increments ``state.cooldown_wait_count``
        - Sets ``state.recovery_action = COOLDOWN_WAIT``
        - Does NOT change ``state.state`` (circuit stays CLOSED)
        - Does NOT increment any threshold counters

        Args:
            task_id: Task prefix.
            summary: The current attempt summary.
            state: The current (mutable) task circuit state.

        Returns:
            The matched signal string, or ``None`` if no cooldown detected.
        """
        if not self._config.cooldown_detection:
            if not self._cooldown_disabled_logged:
                logger.debug(f"Circuit {task_id}: cooldown detection disabled via config")
                self._cooldown_disabled_logged = True
            return None

        # Priority order (most precise first):
        # 1. cooldown_until timestamp — exact reset time from the provider
        # 2. rate_limit_hit flag — structured event signal (reliable even with empty text)
        # 3. text-pattern matching — plain-text fallback for other providers
        import time as _time

        detected = False
        wait_seconds = _COOLDOWN_MIN_SECONDS
        matched_signal = ""

        if summary.cooldown_until and summary.cooldown_until > 0:
            now_ts = int(_time.time())
            wait = summary.cooldown_until - now_ts
            wait_seconds = max(wait, 60)
            detected = True
            matched_signal = f"resetsAt={summary.cooldown_until}"
        elif summary.rate_limit_hit:
            detected = True
            matched_signal = "rate_limit signal"
        else:
            detected, wait_seconds, matched_signal = detect_cooldown_signal(
                summary.accumulated_text, summary.exit_code
            )

        if not detected:
            return None

        state.cooldown_waiting = True
        state.cooldown_wait_started_at = _now_iso()
        state.cooldown_wait_count += 1
        state.recovery_action = RecoveryAction.COOLDOWN_WAIT

        logger.warning(
            f"Circuit {task_id}: COOLDOWN_WAIT detected — signal={matched_signal}, "
            f"wait={wait_seconds}s, count=#{state.cooldown_wait_count}"
        )
        return matched_signal

    def _get_state(self, task_id: str) -> TaskCircuitState:
        """Return (creating if absent) the state for a task."""
        if task_id not in self._states:
            self._states[task_id] = TaskCircuitState()
        return self._states[task_id]

    def _reset_state(self, task_id: str) -> None:
        """Reset a task's state to CLOSED with zeroed counters."""
        existing = self._states.get(task_id, TaskCircuitState())
        self._states[task_id] = TaskCircuitState(
            # Preserve replan_attempted so we never replan twice
            replan_attempted=existing.replan_attempted,
            # any_file_progress resets on manual reset / success
        )

    def _filter_project_files(self, files: list[str]) -> list[str]:
        """Return only files that are inside the project root.

        Args:
            files: List of file path strings (may be relative or absolute).

        Returns:
            Filtered list containing only project-internal paths.
        """
        result: list[str] = []
        for f in files:
            p = Path(f)
            # Resolve relative paths against the project root so that paths
            # like "../../outside.py" are correctly rejected rather than
            # trusted as project-internal.
            resolved = (self._project_root / p).resolve() if not p.is_absolute() else p.resolve()
            try:
                resolved.relative_to(self._project_root)
                result.append(f)
            except ValueError:
                pass
        return result

    def _cooldown_elapsed(self, opened_at: str) -> bool:
        """Return True if the cooldown period has passed since ``opened_at``.

        Args:
            opened_at: ISO timestamp string.

        Returns:
            True if cooldown has elapsed.
        """
        cooldown_seconds = self._config.circuit_cooldown_minutes * 60
        elapsed = self._seconds_since(opened_at)
        return elapsed >= cooldown_seconds

    @staticmethod
    def _seconds_since(iso_ts: str) -> float:
        """Return seconds elapsed since an ISO timestamp.

        Args:
            iso_ts: ISO 8601 timestamp string.

        Returns:
            Elapsed seconds, or 0 on parse error.
        """
        try:
            then = datetime.fromisoformat(iso_ts)
            now = datetime.now(tz=UTC)
            if then.tzinfo is None:
                then = then.replace(tzinfo=UTC)
            return (now - then).total_seconds()
        except (ValueError, TypeError):
            return 0.0

    def _token_decline_tripped(self, state: TaskCircuitState, decline_pct: int) -> bool:
        """Return True if the most recent attempt used far fewer tokens than the first.

        Args:
            state: Current task circuit state.
            decline_pct: Decline threshold percentage (0–100).

        Returns:
            True if the token decline exceeds the threshold.
        """
        history = state.token_history
        if len(history) < 2:
            return False
        first = history[0]
        latest = history[-1]
        if first == 0:
            return False
        decline = (first - latest) / first * 100
        return decline >= decline_pct

    def _choose_recovery(
        self,
        task_id: str,
        state: TaskCircuitState,
        summary: AttemptSummary,
    ) -> RecoveryAction:
        """Decide the recovery action when the circuit opens.

        Decision logic (in priority order):

        1. If ``circuit_enable_replan`` is False → always WAIT.
        2. If replan was already attempted → WAIT (no infinite replanning).
        3. If retry models are still available → WAIT (let rotation happen first).
        4. If all models tried and no file progress ever → REPLAN.
        5. If all models tried but some file progress existed → WAIT (task may just be hard).

        Args:
            task_id: The task prefix.
            state: Current (pre-open) circuit state.
            summary: The attempt that triggered the open.

        Returns:
            The recommended :class:`RecoveryAction`.
        """
        if not self._config.circuit_enable_replan:
            return RecoveryAction.WAIT

        if state.replan_attempted:
            logger.warning(
                f"Circuit {task_id}: replan already attempted — "
                "falling back to WAIT to avoid infinite replanning"
            )
            return RecoveryAction.WAIT

        # Check if retry models are still available
        attempt = summary.attempt_number
        models_available = (attempt < 2 and bool(self._config.retry_model_2)) or (
            attempt < 3 and bool(self._config.retry_model_3)
        )
        if models_available:
            return RecoveryAction.WAIT

        # All models tried — check if there was any file progress at all
        if state.any_file_progress:
            # There was file progress in at least one attempt — task may just be hard
            return RecoveryAction.WAIT

        return RecoveryAction.REPLAN


# ---------------------------------------------------------------------------
# Replan instruction builder
# ---------------------------------------------------------------------------


def _build_replan_instruction(
    task_id: str,
    task_content: str,
    attempt_summary: str,
    progress_content: str,
    project_root: Path,
) -> str:
    """Build the instruction sent to the architect for a targeted replan.

    This is NOT a full re-plan of the project — it asks the architect to
    fix or split one specific failing task only.

    Args:
        task_id: The failing task prefix.
        task_content: Content of the original task file.
        attempt_summary: Human-readable summary of what was tried.
        progress_content: Current PROGRESS.md content for context.
        project_root: The project root directory.

    Returns:
        Instruction string for opencode run.
    """
    lines = [
        f"PROJECT ROOT: {project_root}",
        "BOUNDARY: You MUST NOT read, write, or modify any file outside this project root.",
        "",
        "=== TARGETED TASK REPLAN ===",
        "",
        f"Task {task_id} has failed repeatedly and the circuit breaker has opened.",
        "Your job is to FIX THIS ONE TASK ONLY — do NOT replan the entire project.",
        "Do NOT modify any other task files.",
        "",
        "=== ORIGINAL TASK FILE CONTENT ===",
        task_content,
        "",
        "=== WHAT WAS TRIED AND WHAT WENT WRONG ===",
        attempt_summary,
        "",
        "=== CURRENT PROGRESS.MD ===",
        progress_content[:3000] if progress_content else "(not available)",
        "",
        "=== YOUR INSTRUCTIONS ===",
        f"1. Analyse why task {task_id} is failing based on the attempt history above.",
        "2. Either:",
        f"   a) Rewrite {task_id}'s task file with corrected assumptions, OR",
        f"   b) Split {task_id} into two smaller task files if it was too large.",
        "3. Write the updated/new task file(s) to the tasks/ directory.",
        "4. Do NOT change any other task files.",
        "5. Do NOT rewrite PROGRESS.md — The Architect will handle that.",
        "",
        "IMPORTANT: Only fix the root cause. Keep the task file(s) focused and actionable.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Persistence helpers (module-level convenience)
# ---------------------------------------------------------------------------


def load_circuit_state(
    project_root: Path,
    config: ArchitectConfig,
    free_rotator: object | None = None,
    provider: ArchitectProvider | None = None,
) -> CircuitBreaker:
    """Create a CircuitBreaker and load its persisted state.

    Args:
        project_root: The project root directory.
        config: The resolved The Architect configuration.
        free_rotator: Optional :class:`~the_architect.core.free_models.FreeModelRotator`
            instance.  When provided, the circuit breaker will defer cooldown
            detection to free-mode rotation while free models are still available.
        provider: Optional :class:`~the_architect.core.provider.ArchitectProvider`
            instance.  Used by the replan path to call the architect via the
            correct provider (OpenCode, Codex CLI, Claude Code, or Gemini CLI).

    Returns:
        A ready-to-use :class:`CircuitBreaker` instance.
    """
    cb = CircuitBreaker(
        config=config, project_root=project_root, free_rotator=free_rotator, provider=provider
    )
    cb.load()
    return cb


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Returns:
        ISO timestamp string with timezone info.
    """
    return datetime.now(tz=UTC).isoformat()
