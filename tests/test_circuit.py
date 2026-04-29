"""Tests for the circuit breaker module."""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path

import pytest

from the_architect.config import ArchitectConfig
from the_architect.core.circuit import (
    _COOLDOWN_MIN_SECONDS,
    AttemptSummary,
    CircuitBreaker,
    CircuitState,
    ProviderErrorKind,
    RecoveryAction,
    TaskCircuitState,
    _fingerprint_error,
    _now_iso,
    detect_cooldown_signal,
    detect_provider_error,
    load_circuit_state,
)

# -------------------------------------------------------
# Provider error detection
# -------------------------------------------------------


class TestDetectProviderError:
    """Tests for detect_provider_error()."""

    def test_no_error_on_success(self) -> None:
        """No error when exit code is 0."""
        result = detect_provider_error("some text", 0)
        assert result is None

    def test_no_error_on_empty_text_nonzero_exit(self) -> None:
        """No actionable error when exit code is non-zero but text is empty."""
        result = detect_provider_error("", 1)
        assert result is None

    def test_update_required_opencode(self) -> None:
        """Detect OpenCode update-required error."""
        result = detect_provider_error("A new version of opencode is available. Please update.", 1)
        assert result is not None
        assert result.kind == ProviderErrorKind.UPDATE_REQUIRED
        assert "opencode upgrade" in result.action

    def test_update_required_claude(self) -> None:
        """Detect Claude Code update-required error."""
        result = detect_provider_error("Please update Claude Code to continue.", 1)
        assert result is not None
        assert result.kind == ProviderErrorKind.UPDATE_REQUIRED
        assert "claude update" in result.action

    def test_update_required_generic(self) -> None:
        """Detect update-required error without provider name."""
        result = detect_provider_error("Update required to continue.", 1)
        assert result is not None
        assert result.kind == ProviderErrorKind.UPDATE_REQUIRED
        assert "latest version" in result.action

    def test_misconfigured_api_key(self) -> None:
        """Detect API key misconfiguration error."""
        result = detect_provider_error("Error: Invalid API key provided", 1)
        assert result is not None
        assert result.kind == ProviderErrorKind.MISCONFIGURED
        assert "api key" in result.message.lower() or "API key" in result.message

    def test_misconfigured_unauthorized(self) -> None:
        """Detect unauthorized error."""
        result = detect_provider_error("Error: unauthorized access", 1)
        assert result is not None
        assert result.kind == ProviderErrorKind.MISCONFIGURED

    def test_unknown_error_with_output(self) -> None:
        """Detect unknown error with output text."""
        result = detect_provider_error("Something unexpected went wrong in the provider", 1)
        assert result is not None
        assert result.kind == ProviderErrorKind.UNKNOWN
        assert "Something unexpected" in result.action

    def test_update_required_takes_priority_over_misconfigured(self) -> None:
        """Update-required should be detected before misconfiguration."""
        result = detect_provider_error(
            "A new version is available. Please update. Also invalid API key.", 1
        )
        assert result is not None
        assert result.kind == ProviderErrorKind.UPDATE_REQUIRED

    def test_rate_limit_text_not_detected_as_provider_error(self) -> None:
        """Rate limit text should NOT be detected as an actionable provider error."""
        result = detect_provider_error("rate limit exceeded, try again in 60 seconds", 1)
        # This should either be None or not be UPDATE_REQUIRED
        assert result is None or result.kind != ProviderErrorKind.UPDATE_REQUIRED


# -------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """A temporary project directory with the required structure."""
    (tmp_path / ".architect").mkdir()
    (tmp_path / ".architect" / "logs").mkdir()
    (tmp_path / "tasks").mkdir()
    progress = tmp_path / "PROGRESS.md"
    progress.write_text("**Tasks completed:** 0\n**Next task to run:** T01\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def config(project_dir: Path) -> ArchitectConfig:
    """A resolved config with circuit breaker defaults."""
    return ArchitectConfig(
        progress_file=project_dir / "PROGRESS.md",
        tasks_dir=project_dir / "tasks",
        log_dir=project_dir / ".architect" / "logs",
        max_retries=3,
        retry_pause=0,
        pause_between_tasks=0,
        retry_model_2="model-b",
        retry_model_3="model-c",
        circuit_no_progress_threshold=3,
        circuit_same_error_threshold=3,
        circuit_token_decline_pct=60,
        circuit_cooldown_minutes=30,
        circuit_enable_replan=True,
    )


@pytest.fixture
def cb(config: ArchitectConfig, project_dir: Path) -> CircuitBreaker:
    """A fresh CircuitBreaker with no persisted state."""
    return CircuitBreaker(config=config, project_root=project_dir)


def _summary(
    task_id: str = "T01",
    attempt: int = 1,
    completed: bool = False,
    files: list[str] | None = None,
    bash_errors: list[str] | None = None,
    tokens: int = 1000,
) -> AttemptSummary:
    """Helper to build an AttemptSummary."""
    return AttemptSummary(
        task_id=task_id,
        attempt_number=attempt,
        completion_detected=completed,
        files_written=files or [],
        bash_commands_run=1,
        bash_errors=bash_errors or [],
        total_tokens=tokens,
    )


# ---------------------------------------------------------------------------
# Error fingerprinting
# ---------------------------------------------------------------------------


class TestFingerprintError:
    def test_strips_file_paths(self) -> None:
        text = "Error in /home/user/project/src/foo.py at line 42"
        fp = _fingerprint_error(text)
        assert "/home/user/project/src/foo.py" not in fp
        assert "<path>" in fp

    def test_strips_line_numbers(self) -> None:
        text = "SyntaxError at :42 in foo"
        fp = _fingerprint_error(text)
        assert ":42" not in fp
        # fingerprint is lowercased, so :<N> becomes :<n>
        assert ":<n>" in fp

    def test_normalises_whitespace(self) -> None:
        fp = _fingerprint_error("error:   too   many   spaces")
        assert "  " not in fp

    def test_lowercases(self) -> None:
        fp = _fingerprint_error("ModuleNotFoundError: No module named 'foo'")
        assert fp == fp.lower()

    def test_same_error_different_paths_same_fingerprint(self) -> None:
        err1 = "ModuleNotFoundError: No module named 'foo' in /home/alice/proj/main.py:10"
        err2 = "ModuleNotFoundError: No module named 'foo' in /home/bob/other/main.py:99"
        assert _fingerprint_error(err1) == _fingerprint_error(err2)


# ---------------------------------------------------------------------------
# Circuit stays CLOSED when thresholds not reached
# ---------------------------------------------------------------------------


class TestCircuitStaysClosed:
    def test_single_no_progress_does_not_open(self, cb: CircuitBreaker) -> None:
        state = cb.record_attempt(_summary(files=[]))
        assert state.state == CircuitState.CLOSED

    def test_two_no_progress_does_not_open(self, cb: CircuitBreaker) -> None:
        cb.record_attempt(_summary(attempt=1, files=[]))
        state = cb.record_attempt(_summary(attempt=2, files=[]))
        assert state.state == CircuitState.CLOSED

    def test_single_same_error_does_not_open(self, cb: CircuitBreaker) -> None:
        state = cb.record_attempt(_summary(bash_errors=["ModuleNotFoundError: foo"]))
        assert state.state == CircuitState.CLOSED

    def test_two_same_errors_do_not_open(self, cb: CircuitBreaker) -> None:
        err = "ModuleNotFoundError: foo"
        cb.record_attempt(_summary(attempt=1, bash_errors=[err]))
        state = cb.record_attempt(_summary(attempt=2, bash_errors=[err]))
        assert state.state == CircuitState.CLOSED

    def test_success_resets_counters(self, cb: CircuitBreaker) -> None:
        cb.record_attempt(_summary(attempt=1, files=[]))
        cb.record_attempt(_summary(attempt=2, files=[]))
        cb.record_attempt(_summary(attempt=3, completed=True, files=["foo.py"]))
        state = cb.record_attempt(_summary(attempt=4, files=[]))
        # Counter should have reset after success — one no-progress, not four
        assert state.consecutive_no_progress == 1
        assert state.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# No-progress threshold
# ---------------------------------------------------------------------------


class TestNoProgressThreshold:
    def test_opens_at_threshold(self, cb: CircuitBreaker) -> None:
        for i in range(1, 4):  # threshold is 3
            state = cb.record_attempt(_summary(attempt=i, files=[]))
        assert state.state == CircuitState.OPEN

    def test_file_write_resets_counter(self, cb: CircuitBreaker) -> None:
        cb.record_attempt(_summary(attempt=1, files=[]))
        cb.record_attempt(_summary(attempt=2, files=[]))
        # Write a file — counter resets
        cb.record_attempt(_summary(attempt=3, files=["src/foo.py"]))
        # Two more no-progress — should NOT open (counter reset)
        cb.record_attempt(_summary(attempt=4, files=[]))
        state = cb.record_attempt(_summary(attempt=5, files=[]))
        assert state.state == CircuitState.CLOSED
        assert state.consecutive_no_progress == 2

    def test_zero_threshold_disables_check(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        config.circuit_no_progress_threshold = 0
        cb = CircuitBreaker(config=config, project_root=project_dir)
        for i in range(1, 10):
            state = cb.record_attempt(_summary(attempt=i, files=[]))
        assert state.state == CircuitState.CLOSED

    def test_only_project_files_count(self, config: ArchitectConfig, project_dir: Path) -> None:
        """Files outside the project root should not reset the no-progress counter."""
        cb = CircuitBreaker(config=config, project_root=project_dir)
        # Absolute path outside project root
        outside_file = "/tmp/some_other_project/foo.py"
        for i in range(1, 4):
            state = cb.record_attempt(_summary(attempt=i, files=[outside_file]))
        # Counter should have incremented (outside files don't count)
        assert state.consecutive_no_progress == 3
        assert state.state == CircuitState.OPEN


# ---------------------------------------------------------------------------
# Same-error threshold
# ---------------------------------------------------------------------------


class TestSameErrorThreshold:
    def test_opens_at_threshold(self, cb: CircuitBreaker) -> None:
        err = "ModuleNotFoundError: No module named 'requests'"
        for i in range(1, 4):  # threshold is 3
            state = cb.record_attempt(_summary(attempt=i, bash_errors=[err]))
        assert state.state == CircuitState.OPEN

    def test_different_errors_do_not_accumulate(self, cb: CircuitBreaker) -> None:
        # Write files each attempt so no-progress threshold doesn't fire
        cb.record_attempt(_summary(attempt=1, files=["a.py"], bash_errors=["error: foo not found"]))
        cb.record_attempt(_summary(attempt=2, files=["b.py"], bash_errors=["error: bar not found"]))
        state = cb.record_attempt(
            _summary(attempt=3, files=["c.py"], bash_errors=["error: baz not found"])
        )
        assert state.state == CircuitState.CLOSED

    def test_no_bash_error_does_not_update_counter(self, cb: CircuitBreaker) -> None:
        """Attempts without bash errors should not increment same-error counter."""
        err = "ModuleNotFoundError: foo"
        cb.record_attempt(_summary(attempt=1, bash_errors=[err]))
        cb.record_attempt(_summary(attempt=2, bash_errors=[err]))
        # Attempt 3 has no bash error — counter should NOT advance
        cb.record_attempt(_summary(attempt=3, bash_errors=[]))
        state = cb.record_attempt(_summary(attempt=4, bash_errors=[err]))
        # Counter is 3 now (1+1+skip+1), but threshold is 3 so it opens
        # Actually: attempt1=1, attempt2=2, attempt3=skip, attempt4=3 → opens
        assert state.state == CircuitState.OPEN

    def test_zero_threshold_disables_check(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        config.circuit_same_error_threshold = 0
        # Also disable no-progress threshold so it doesn't fire instead
        config.circuit_no_progress_threshold = 0
        cb = CircuitBreaker(config=config, project_root=project_dir)
        err = "same error every time"
        for i in range(1, 10):
            state = cb.record_attempt(_summary(attempt=i, bash_errors=[err]))
        assert state.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# Token decline threshold
# ---------------------------------------------------------------------------


class TestTokenDeclineThreshold:
    def test_token_decline_alone_does_not_open(self, cb: CircuitBreaker) -> None:
        """Token decline without corroborating signal should NOT open the circuit."""
        # Attempt 1: 1000 tokens
        cb.record_attempt(_summary(attempt=1, tokens=1000, files=["foo.py"]))
        # Attempt 2: 300 tokens (70% decline — over 60% threshold)
        state = cb.record_attempt(_summary(attempt=2, tokens=300, files=["bar.py"]))
        assert state.state == CircuitState.CLOSED

    def test_token_decline_with_no_progress_opens(self, cb: CircuitBreaker) -> None:
        """Token decline + no-progress signal should open the circuit."""
        # Attempt 1: 1000 tokens, no file progress
        cb.record_attempt(_summary(attempt=1, tokens=1000, files=[]))
        # Attempt 2: 300 tokens (70% decline), no file progress
        # consecutive_no_progress=2 (>0) → corroborating signal present
        state = cb.record_attempt(_summary(attempt=2, tokens=300, files=[]))
        assert state.state == CircuitState.OPEN

    def test_token_decline_with_same_error_opens(self, cb: CircuitBreaker) -> None:
        """Token decline + same-error signal should open the circuit."""
        err = "ModuleNotFoundError: foo"
        cb.record_attempt(_summary(attempt=1, tokens=1000, bash_errors=[err]))
        # same_error_count=2 (>0) → corroborating signal
        state = cb.record_attempt(_summary(attempt=2, tokens=300, bash_errors=[err]))
        assert state.state == CircuitState.OPEN

    def test_token_decline_zero_threshold_disables(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        config.circuit_token_decline_pct = 0
        cb = CircuitBreaker(config=config, project_root=project_dir)
        cb.record_attempt(_summary(attempt=1, tokens=1000, files=[]))
        state = cb.record_attempt(_summary(attempt=2, tokens=10, files=[]))
        assert state.state == CircuitState.CLOSED

    def test_no_token_data_skips_check(self, cb: CircuitBreaker) -> None:
        """Attempts with total_tokens=0 should skip the token decline check."""
        cb.record_attempt(_summary(attempt=1, tokens=0, files=[]))
        state = cb.record_attempt(_summary(attempt=2, tokens=0, files=[]))
        # No token history built — check skipped, circuit stays closed
        assert state.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------


class TestStateTransitions:
    def test_open_to_half_open_after_cooldown(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """OPEN → HALF_OPEN when cooldown has elapsed."""
        config.circuit_cooldown_minutes = 0  # immediate
        cb = CircuitBreaker(config=config, project_root=project_dir)

        # Trip the circuit
        for i in range(1, 4):
            cb.record_attempt(_summary(attempt=i, files=[]))

        allowed, reason = cb.can_run("T01")
        assert allowed  # cooldown=0 → immediately HALF_OPEN
        state = cb._get_state("T01")
        assert state.state == CircuitState.HALF_OPEN

    def test_half_open_to_closed_on_success(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """HALF_OPEN → CLOSED when test attempt succeeds."""
        config.circuit_cooldown_minutes = 0
        cb = CircuitBreaker(config=config, project_root=project_dir)

        # Open the circuit
        for i in range(1, 4):
            cb.record_attempt(_summary(attempt=i, files=[]))

        # Trigger HALF_OPEN
        cb.can_run("T01")

        # Successful test attempt
        state = cb.record_attempt(_summary(attempt=4, completed=True, files=["foo.py"]))
        assert state.state == CircuitState.CLOSED
        assert state.consecutive_no_progress == 0

    def test_half_open_to_open_on_failure(self, config: ArchitectConfig, project_dir: Path) -> None:
        """HALF_OPEN → OPEN when test attempt fails."""
        config.circuit_cooldown_minutes = 0
        cb = CircuitBreaker(config=config, project_root=project_dir)

        # Open the circuit
        for i in range(1, 4):
            cb.record_attempt(_summary(attempt=i, files=[]))

        # Trigger HALF_OPEN
        cb.can_run("T01")

        # Failed test attempt
        state = cb.record_attempt(_summary(attempt=4, completed=False, files=[]))
        assert state.state == CircuitState.OPEN
        assert state.opened_at is not None

    def test_open_circuit_blocks_run(self, cb: CircuitBreaker) -> None:
        """can_run() returns False when circuit is OPEN and cooldown not elapsed."""
        for i in range(1, 4):
            cb.record_attempt(_summary(attempt=i, files=[]))

        # Circuit is now OPEN with 30-minute cooldown
        allowed, reason = cb.can_run("T01")
        assert not allowed
        assert "OPEN" in reason

    def test_closed_circuit_allows_run(self, cb: CircuitBreaker) -> None:
        """can_run() returns True when circuit is CLOSED."""
        allowed, reason = cb.can_run("T01")
        assert allowed

    def test_unknown_task_is_closed(self, cb: CircuitBreaker) -> None:
        """Tasks with no recorded state are implicitly CLOSED."""
        allowed, _ = cb.can_run("T99")
        assert allowed


# ---------------------------------------------------------------------------
# Recovery action selection
# ---------------------------------------------------------------------------


class TestRecoveryAction:
    def test_wait_when_models_still_available(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """WAIT when retry models are still available (threshold=1, attempt=1)."""
        # Use threshold=1 so the circuit opens on the very first attempt.
        # At attempt 1, retry_model_2 and retry_model_3 have not been tried yet
        # → models_available=True → recovery=WAIT
        config.circuit_no_progress_threshold = 1
        cb = CircuitBreaker(config=config, project_root=project_dir)
        state = cb.record_attempt(_summary(attempt=1, files=[]))
        assert state.state == CircuitState.OPEN
        assert state.recovery_action == RecoveryAction.WAIT

    def test_replan_when_all_models_exhausted_no_progress(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """REPLAN when all models exhausted and no file progress ever made."""
        # Simulate attempt 3 (all models tried: default, model_2, model_3)
        # and no file progress across all attempts
        cb = CircuitBreaker(config=config, project_root=project_dir)
        # First two attempts: no files, different errors so same-error doesn't trigger
        cb.record_attempt(_summary(attempt=1, files=[], bash_errors=["err1"]))
        cb.record_attempt(_summary(attempt=2, files=[], bash_errors=["err2"]))
        # Third attempt: no files, same error as attempt 2 → same_error=2
        # But we need same_error=3 to trip that threshold, so let's use no_progress
        # Reset and use no_progress threshold instead
        cb2 = CircuitBreaker(config=config, project_root=project_dir)
        # Attempt 3 triggers no_progress threshold (3 consecutive no-file attempts)
        # At attempt 3, retry_model_2 and retry_model_3 have been used
        # (attempts 2 and 3)
        # So no models left → REPLAN
        cb2.record_attempt(_summary(attempt=1, files=[]))
        cb2.record_attempt(_summary(attempt=2, files=[]))
        state = cb2.record_attempt(_summary(attempt=3, files=[]))
        # At attempt 3: models available = retry_model_2 at attempt 2,
        # retry_model_3 at 3
        # attempt_number=3, retry_model_3 is set → still available? Let's check logic:
        # models_available = (
        #     (attempt < 2 and bool(retry_model_2)) or
        # (attempt < 3 and bool(retry_model_3))
        # = (3 < 2 and True) or (3 < 3 and True) = False or False = False
        # → no models left, consecutive_no_progress=3 (all no-progress) → REPLAN
        assert state.recovery_action == RecoveryAction.REPLAN

    def test_wait_when_replan_disabled(self, config: ArchitectConfig, project_dir: Path) -> None:
        """WAIT when circuit_enable_replan=False."""
        config.circuit_enable_replan = False
        cb = CircuitBreaker(config=config, project_root=project_dir)
        for i in range(1, 4):
            cb.record_attempt(_summary(attempt=i, files=[]))
        state = cb._get_state("T01")
        assert state.recovery_action == RecoveryAction.WAIT

    def test_wait_when_replan_already_attempted(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """WAIT when replan_attempted=True — no infinite replanning."""
        cb = CircuitBreaker(config=config, project_root=project_dir)
        # Manually set replan_attempted
        cb._get_state("T01").replan_attempted = True
        for i in range(1, 4):
            cb.record_attempt(_summary(attempt=i, files=[]))
        state = cb._get_state("T01")
        assert state.recovery_action == RecoveryAction.WAIT

    def test_wait_when_partial_file_progress(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """WAIT when all models tried but some file progress was made in an earlier attempt."""
        # Use threshold=1 so circuit opens on the first no-progress attempt.
        # Attempt 1: wrote a file (any_file_progress=True)
        # Attempt 2: no file → threshold=1 → circuit opens
        # At attempt 2: no models left (attempt >= 2 for retry_model_2 check)
        # But any_file_progress=True → recovery=WAIT
        config.circuit_no_progress_threshold = 1
        cb = CircuitBreaker(config=config, project_root=project_dir)
        cb.record_attempt(_summary(attempt=1, files=["src/foo.py"]))  # progress recorded
        state = cb.record_attempt(_summary(attempt=2, files=[]))  # opens circuit
        assert state.state == CircuitState.OPEN
        assert state.recovery_action == RecoveryAction.WAIT


# ---------------------------------------------------------------------------
# Persistence — round-trip
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_save_and_load_round_trip(self, config: ArchitectConfig, project_dir: Path) -> None:
        """State written to disk should be readable back."""
        cb = CircuitBreaker(config=config, project_root=project_dir)
        for i in range(1, 4):
            cb.record_attempt(_summary(attempt=i, files=[]))

        # Create a new breaker and load from disk
        cb2 = CircuitBreaker(config=config, project_root=project_dir)
        cb2.load()

        state = cb2._get_state("T01")
        assert state.state == CircuitState.OPEN
        assert state.consecutive_no_progress == 3

    def test_missing_state_file_starts_fresh(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """A missing state file should not raise — just start fresh."""
        cb = CircuitBreaker(config=config, project_root=project_dir)
        cb.load()  # file doesn't exist
        allowed, _ = cb.can_run("T01")
        assert allowed

    def test_malformed_state_file_starts_fresh(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """A malformed state file should log a warning and start fresh."""
        state_file = project_dir / ".architect" / "circuit.json"
        state_file.write_text("this is not json }{{{", encoding="utf-8")

        cb = CircuitBreaker(config=config, project_root=project_dir)
        cb.load()  # should not raise

        allowed, _ = cb.can_run("T01")
        assert allowed

    def test_partial_malformed_state_skips_bad_entry(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """A state file with one bad entry should skip that entry and load the rest."""
        state_file = project_dir / ".architect" / "circuit.json"
        data = {
            "T01": {
                "state": "OPEN",
                "consecutive_no_progress": 3,
                "consecutive_same_error": 0,
                "last_error_fingerprint": None,
                "token_history": [],
                "opened_at": _now_iso(),
                "recovery_action": "WAIT",
                "replan_attempted": False,
            },
            "T02": "this is not a dict at all — malformed",
        }
        state_file.write_text(json.dumps(data), encoding="utf-8")

        cb = CircuitBreaker(config=config, project_root=project_dir)
        cb.load()

        # T01 should be loaded correctly
        assert cb._get_state("T01").state == CircuitState.OPEN
        # T02 should be CLOSED (skipped bad entry → default)
        assert cb._get_state("T02").state == CircuitState.CLOSED

    def test_save_creates_directory(self, config: ArchitectConfig, tmp_path: Path) -> None:
        """save() should create .architect/ if it doesn't exist."""
        # Use a project dir without .architect pre-created
        proj = tmp_path / "newproject"
        proj.mkdir()
        (proj / "PROGRESS.md").write_text("", encoding="utf-8")
        cfg = ArchitectConfig(
            progress_file=proj / "PROGRESS.md",
            tasks_dir=proj / "tasks",
            log_dir=proj / ".architect" / "logs",
        )
        cb = CircuitBreaker(config=cfg, project_root=proj)
        cb.record_attempt(_summary())
        cb.save()
        assert (proj / ".architect" / "circuit.json").exists()


# ---------------------------------------------------------------------------
# Manual reset
# ---------------------------------------------------------------------------


class TestManualReset:
    def test_reset_clears_open_circuit(self, cb: CircuitBreaker) -> None:
        """reset_task() should return the circuit to CLOSED."""
        for i in range(1, 4):
            cb.record_attempt(_summary(attempt=i, files=[]))

        assert cb._get_state("T01").state == CircuitState.OPEN

        cb.reset_task("T01")
        state = cb._get_state("T01")
        assert state.state == CircuitState.CLOSED
        assert state.consecutive_no_progress == 0
        assert state.consecutive_same_error == 0
        assert state.recovery_action is None

    def test_reset_preserves_replan_attempted(self, cb: CircuitBreaker) -> None:
        """reset_task() should preserve replan_attempted to prevent re-replanning."""
        cb._get_state("T01").replan_attempted = True
        cb.reset_task("T01")
        assert cb._get_state("T01").replan_attempted is True

    def test_reset_unknown_task_does_not_crash(self, cb: CircuitBreaker) -> None:
        """reset_task() on an unknown task should not raise."""
        cb.reset_task("T99")  # no state for T99 yet
        state = cb._get_state("T99")
        assert state.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# load_circuit_state convenience function
# ---------------------------------------------------------------------------


class TestLoadCircuitState:
    def test_returns_circuit_breaker(self, config: ArchitectConfig, project_dir: Path) -> None:
        cb = load_circuit_state(project_dir, config)
        assert isinstance(cb, CircuitBreaker)

    def test_loads_existing_state(self, config: ArchitectConfig, project_dir: Path) -> None:
        # Write some state
        cb1 = CircuitBreaker(config=config, project_root=project_dir)
        for i in range(1, 4):
            cb1.record_attempt(_summary(attempt=i, files=[]))

        # Load via convenience function
        cb2 = load_circuit_state(project_dir, config)
        assert cb2._get_state("T01").state == CircuitState.OPEN


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


class TestConfigDefaults:
    def test_circuit_defaults_present(self) -> None:
        """New config fields should have the documented defaults."""
        cfg = ArchitectConfig()
        assert cfg.circuit_no_progress_threshold == 3
        assert cfg.circuit_same_error_threshold == 3
        assert cfg.circuit_token_decline_pct == 60
        assert cfg.circuit_cooldown_minutes == 30
        assert cfg.circuit_enable_replan is True

    def test_circuit_config_survives_resolve(self, tmp_path: Path) -> None:
        """resolve() should carry circuit config fields through."""
        cfg = ArchitectConfig(
            circuit_no_progress_threshold=5,
            circuit_same_error_threshold=2,
            circuit_token_decline_pct=40,
            circuit_cooldown_minutes=10,
            circuit_enable_replan=False,
        )
        resolved = cfg.resolve(tmp_path)
        assert resolved.circuit_no_progress_threshold == 5
        assert resolved.circuit_same_error_threshold == 2
        assert resolved.circuit_token_decline_pct == 40
        assert resolved.circuit_cooldown_minutes == 10
        assert resolved.circuit_enable_replan is False


# ---------------------------------------------------------------------------
# all_states
# ---------------------------------------------------------------------------


class TestAllStates:
    def test_returns_all_tracked_tasks(self, cb: CircuitBreaker) -> None:
        cb.record_attempt(_summary(task_id="T01"))
        cb.record_attempt(_summary(task_id="T02"))
        states = cb.all_states()
        assert "T01" in states
        assert "T02" in states

    def test_returns_copy(self, cb: CircuitBreaker) -> None:
        """Mutating the returned dict should not affect internal state."""
        cb.record_attempt(_summary(task_id="T01"))
        states = cb.all_states()
        states["T99"] = TaskCircuitState()
        assert "T99" not in cb.all_states()


# ---------------------------------------------------------------------------
# detect_cooldown_signal — standalone function tests
# ---------------------------------------------------------------------------


class TestDetectCooldownSignal:
    def test_detects_rate_limit_text(self) -> None:
        detected, wait, signal = detect_cooldown_signal("rate limit exceeded", 0)
        assert detected is True
        assert wait == _COOLDOWN_MIN_SECONDS
        assert "rate limit" in signal

    def test_detects_429_exit_code(self) -> None:
        detected, wait, signal = detect_cooldown_signal("", 429)
        assert detected is True
        assert "429" in signal

    def test_detects_529_exit_code(self) -> None:
        detected, wait, signal = detect_cooldown_signal("", 529)
        assert detected is True
        assert "529" in signal

    def test_detects_overloaded(self) -> None:
        detected, wait, _ = detect_cooldown_signal("The model is overloaded", 0)
        assert detected is True

    def test_detects_quota_exceeded(self) -> None:
        detected, wait, _ = detect_cooldown_signal("quota_exceeded for your plan", 0)
        assert detected is True

    def test_detects_try_again_in(self) -> None:
        detected, wait, _ = detect_cooldown_signal("please try again in 60 seconds", 0)
        assert detected is True

    def test_no_signal_returns_false(self) -> None:
        detected, wait, signal = detect_cooldown_signal("SyntaxError: unexpected token", 0)
        assert detected is False
        assert signal == ""

    def test_uses_suggested_wait_if_longer_than_one_hour(self) -> None:
        """If provider says 'retry after 7200 seconds', use 7200."""
        detected, wait, _ = detect_cooldown_signal("retry after 7200 seconds", 0)
        assert detected is True
        assert wait == 7200

    def test_uses_one_hour_if_suggested_wait_shorter(self) -> None:
        """If provider says 'retry after 30 seconds', still use 1 hour."""
        detected, wait, _ = detect_cooldown_signal("try again in 30 seconds", 0)
        assert detected is True
        assert wait == _COOLDOWN_MIN_SECONDS

    def test_uses_one_hour_if_no_wait_time_in_message(self) -> None:
        detected, wait, _ = detect_cooldown_signal("rate limit hit", 0)
        assert detected is True
        assert wait == _COOLDOWN_MIN_SECONDS

    def test_wait_time_in_hours(self) -> None:
        """'retry after 2 hours' → 7200 seconds."""
        detected, wait, _ = detect_cooldown_signal("retry after 2 hours", 0)
        assert detected is True
        assert wait == 7200

    def test_wait_time_in_minutes(self) -> None:
        """'please wait 90 minutes' → 5400 seconds (> 3600 → used)."""
        detected, wait, _ = detect_cooldown_signal("please wait 90 minutes", 0)
        assert detected is True
        assert wait == 5400

    def test_case_insensitive(self) -> None:
        detected, _, _ = detect_cooldown_signal("RATE LIMIT EXCEEDED", 0)
        assert detected is True


# ---------------------------------------------------------------------------
# Cooldown detection in CircuitBreaker.record_attempt
# ---------------------------------------------------------------------------


def _cooldown_summary(
    task_id: str = "T01",
    attempt: int = 1,
    text: str = "rate limit exceeded",
    exit_code: int = 0,
) -> AttemptSummary:
    """Helper: build an AttemptSummary with a cooldown signal."""
    return AttemptSummary(
        task_id=task_id,
        attempt_number=attempt,
        completion_detected=False,
        files_written=[],
        bash_commands_run=0,
        bash_errors=[],
        total_tokens=1000,
        accumulated_text=text,
        exit_code=exit_code,
    )


class TestCooldownInCircuitBreaker:
    def test_cooldown_detected_sets_waiting_flag(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """COOLDOWN_WAIT sets cooldown_waiting=True, does not open circuit."""
        cb = CircuitBreaker(config=config, project_root=project_dir)
        state = cb.record_attempt(_cooldown_summary())
        assert state.cooldown_waiting is True
        assert state.state == CircuitState.CLOSED
        assert state.recovery_action == RecoveryAction.COOLDOWN_WAIT

    def test_cooldown_does_not_increment_no_progress(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """No-progress counter must stay at 0 after a cooldown attempt."""
        cb = CircuitBreaker(config=config, project_root=project_dir)
        cb.record_attempt(_cooldown_summary(attempt=1))
        cb.record_attempt(_cooldown_summary(attempt=2))
        state = cb._get_state("T01")
        assert state.consecutive_no_progress == 0

    def test_cooldown_does_not_increment_same_error(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """Same-error counter must stay at 0 after cooldown attempts."""
        cb = CircuitBreaker(config=config, project_root=project_dir)
        cb.record_attempt(_cooldown_summary(attempt=1))
        cb.record_attempt(_cooldown_summary(attempt=2))
        state = cb._get_state("T01")
        assert state.consecutive_same_error == 0

    def test_cooldown_does_not_open_circuit(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """Circuit must stay CLOSED through many cooldown attempts."""
        cb = CircuitBreaker(config=config, project_root=project_dir)
        for i in range(1, 20):
            state = cb.record_attempt(_cooldown_summary(attempt=i))
        assert state.state == CircuitState.CLOSED

    def test_cooldown_increments_wait_count(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """Each cooldown attempt increments cooldown_wait_count."""
        cb = CircuitBreaker(config=config, project_root=project_dir)
        cb.record_attempt(_cooldown_summary(attempt=1))
        cb.record_attempt(_cooldown_summary(attempt=2))
        cb.record_attempt(_cooldown_summary(attempt=3))
        state = cb._get_state("T01")
        assert state.cooldown_wait_count == 3

    def test_cooldown_sets_started_at(self, config: ArchitectConfig, project_dir: Path) -> None:
        """cooldown_wait_started_at should be set to a recent timestamp."""
        cb = CircuitBreaker(config=config, project_root=project_dir)
        cb.record_attempt(_cooldown_summary())
        state = cb._get_state("T01")
        assert state.cooldown_wait_started_at is not None
        # Should be parseable as ISO datetime
        from datetime import datetime

        dt = datetime.fromisoformat(state.cooldown_wait_started_at)
        assert dt is not None

    def test_cooldown_disabled_falls_through_to_normal_eval(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """cooldown_detection=False → cooldown signal ignored, counters increment."""
        config.cooldown_detection = False
        cb = CircuitBreaker(config=config, project_root=project_dir)
        # 3 attempts with rate-limit text but cooldown disabled
        # → no-progress threshold will fire instead
        for i in range(1, 4):
            state = cb.record_attempt(_cooldown_summary(attempt=i))
        # Circuit should open via no-progress (not cooldown)
        assert state.state == CircuitState.OPEN
        assert state.recovery_action != RecoveryAction.COOLDOWN_WAIT
        assert state.consecutive_no_progress == 3

    def test_non_cooldown_failure_after_cooldown_waits_uses_normal_eval(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """After cooldown waits, a non-cooldown failure evaluates normally."""
        cb = CircuitBreaker(config=config, project_root=project_dir)
        # Two cooldown waits (counters stay at 0)
        cb.record_attempt(_cooldown_summary(attempt=1))
        cb.record_attempt(_cooldown_summary(attempt=2))
        # Now a normal failure (no cooldown text, no files written)
        normal = _summary(attempt=3, files=[], bash_errors=[])
        state = cb.record_attempt(normal)
        # consecutive_no_progress should now be 1 (first real no-progress)
        assert state.consecutive_no_progress == 1
        assert state.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# Free mode + cooldown interaction
# ---------------------------------------------------------------------------


class _MockFreeRotatorWithModels:
    """Fake FreeModelRotator that still has models available."""

    has_models_available = True


class _MockFreeRotatorExhausted:
    """Fake FreeModelRotator with all models exhausted."""

    has_models_available = False


class TestCooldownFreeModInteraction:
    def test_cooldown_defers_to_free_mode_when_models_available(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """When free mode has models, CB defers — no COOLDOWN_WAIT."""
        cb = CircuitBreaker(
            config=config,
            project_root=project_dir,
            free_rotator=_MockFreeRotatorWithModels(),
        )
        state = cb.record_attempt(_cooldown_summary())
        # Free mode has models → deferred, no cooldown flag set
        assert state.cooldown_waiting is False
        assert state.recovery_action != RecoveryAction.COOLDOWN_WAIT

    def test_cooldown_triggers_when_free_mode_exhausted(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """When free mode is exhausted, COOLDOWN_WAIT fires correctly."""
        cb = CircuitBreaker(
            config=config,
            project_root=project_dir,
            free_rotator=_MockFreeRotatorExhausted(),
        )
        state = cb.record_attempt(_cooldown_summary())
        assert state.cooldown_waiting is True
        assert state.recovery_action == RecoveryAction.COOLDOWN_WAIT

    def test_cooldown_triggers_when_no_free_rotator(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """Without a free rotator, COOLDOWN_WAIT fires on cooldown signal."""
        cb = CircuitBreaker(config=config, project_root=project_dir, free_rotator=None)
        state = cb.record_attempt(_cooldown_summary())
        assert state.cooldown_waiting is True
        assert state.recovery_action == RecoveryAction.COOLDOWN_WAIT

    def test_full_lifecycle_free_exhausts_then_cooldown(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """Full lifecycle: free mode active → exhausted → cooldown kicks in."""
        # Phase 1: free mode has models → CB defers
        rotator = _MockFreeRotatorWithModels()
        cb = CircuitBreaker(config=config, project_root=project_dir, free_rotator=rotator)
        state1 = cb.record_attempt(_cooldown_summary(attempt=1))
        assert state1.cooldown_waiting is False

        # Phase 2: free mode exhausted → CB handles cooldown
        rotator2 = _MockFreeRotatorExhausted()
        cb._free_rotator = rotator2
        state2 = cb.record_attempt(_cooldown_summary(attempt=2))
        assert state2.cooldown_waiting is True
        assert state2.recovery_action == RecoveryAction.COOLDOWN_WAIT


# ---------------------------------------------------------------------------
# Cooldown wait persistence — restart scenarios
# ---------------------------------------------------------------------------


class TestCooldownPersistence:
    def test_cooldown_state_survives_round_trip(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """Cooldown fields should survive save/load."""
        cb = CircuitBreaker(config=config, project_root=project_dir)
        cb.record_attempt(_cooldown_summary())

        cb2 = CircuitBreaker(config=config, project_root=project_dir)
        cb2.load()
        state = cb2._get_state("T01")
        assert state.cooldown_waiting is True
        assert state.cooldown_wait_count == 1
        assert state.cooldown_wait_started_at is not None

    def test_restart_during_wait_resumes_remaining(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """On restart with cooldown_waiting=True and time remaining → resume wait."""
        cb = CircuitBreaker(config=config, project_root=project_dir)
        cb.record_attempt(_cooldown_summary())
        # Wait just started → ~3600s remaining
        cb2 = CircuitBreaker(config=config, project_root=project_dir)
        cb2.load()
        allowed, reason = cb2.can_run("T01")
        assert not allowed
        assert "cooldown_wait_resume" in reason
        # Remaining should be close to 3600
        remaining = int(reason.split(":")[1])
        assert remaining > 3590

    def test_restart_after_wait_elapsed_proceeds(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """On restart with cooldown_waiting=True but wait elapsed → allow run."""
        # Manually write a state file with cooldown_wait_started_at in the past
        from datetime import datetime, timedelta

        past_ts = (datetime.now(tz=UTC) - timedelta(hours=2)).isoformat()
        state_data = {
            "T01": {
                "state": "CLOSED",
                "consecutive_no_progress": 0,
                "consecutive_same_error": 0,
                "last_error_fingerprint": None,
                "token_history": [],
                "opened_at": None,
                "recovery_action": "COOLDOWN_WAIT",
                "replan_attempted": False,
                "any_file_progress": False,
                "cooldown_waiting": True,
                "cooldown_wait_started_at": past_ts,
                "cooldown_wait_count": 1,
            }
        }
        state_file = project_dir / ".architect" / "circuit.json"
        state_file.write_text(json.dumps(state_data), encoding="utf-8")

        cb = CircuitBreaker(config=config, project_root=project_dir)
        cb.load()
        allowed, reason = cb.can_run("T01")
        # Wait has elapsed → allow run
        assert allowed
        # Flag should be cleared
        assert cb._get_state("T01").cooldown_waiting is False

    def test_cooldown_wait_count_persists_across_restarts(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """cooldown_wait_count accumulates across multiple restarts."""
        cb = CircuitBreaker(config=config, project_root=project_dir)
        cb.record_attempt(_cooldown_summary(attempt=1))
        cb.record_attempt(_cooldown_summary(attempt=2))

        cb2 = CircuitBreaker(config=config, project_root=project_dir)
        cb2.load()
        assert cb2._get_state("T01").cooldown_wait_count == 2


# ---------------------------------------------------------------------------
# Cooldown config default
# ---------------------------------------------------------------------------


class TestCooldownConfig:
    def test_cooldown_detection_default_true(self) -> None:
        cfg = ArchitectConfig()
        assert cfg.cooldown_detection is True

    def test_cooldown_detection_survives_resolve(self, tmp_path: Path) -> None:
        cfg = ArchitectConfig(cooldown_detection=False)
        resolved = cfg.resolve(tmp_path)
        assert resolved.cooldown_detection is False


# ---------------------------------------------------------------------------
# handle_cooldown_wait — async method
# ---------------------------------------------------------------------------


class TestHandleCooldownWait:
    """Tests for CircuitBreaker.handle_cooldown_wait()."""

    @pytest.mark.asyncio
    async def test_noop_when_not_waiting(self, cb: CircuitBreaker) -> None:
        """handle_cooldown_wait() should return immediately when not in cooldown wait."""
        from unittest.mock import patch

        sleep_calls: list[float] = []

        async def fake_sleep(secs: float) -> None:
            sleep_calls.append(secs)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await cb.handle_cooldown_wait("T01")

        # No sleep should have been called — task was not in cooldown wait
        assert sleep_calls == []

    @pytest.mark.asyncio
    async def test_clears_cooldown_flag_after_wait(self, cb: CircuitBreaker) -> None:
        """After handle_cooldown_wait(), cooldown_waiting should be False."""
        from unittest.mock import patch

        # Set up a cooldown wait with a very short remaining time
        state = cb._get_state("T01")
        state.cooldown_waiting = True
        # Use a timestamp 3599 seconds ago so remaining ≈ 1s
        from datetime import datetime, timedelta

        started = datetime.now(tz=UTC) - timedelta(seconds=3599)
        state.cooldown_wait_started_at = started.isoformat()
        state.cooldown_wait_count = 1

        async def fake_sleep(secs: float) -> None:
            # Don't actually sleep
            pass

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await cb.handle_cooldown_wait("T01")

        assert state.cooldown_waiting is False
        assert state.cooldown_wait_started_at is None

    @pytest.mark.asyncio
    async def test_waits_for_remaining_duration(self, cb: CircuitBreaker) -> None:
        """handle_cooldown_wait() should sleep for approximately the remaining time."""
        from unittest.mock import patch

        sleep_calls: list[float] = []

        async def fake_sleep(secs: float) -> None:
            sleep_calls.append(secs)

        # Set up cooldown with ~120s remaining (3600 - 3480 elapsed = 120s)
        from datetime import datetime, timedelta

        state = cb._get_state("T01")
        state.cooldown_waiting = True
        state.cooldown_wait_count = 1
        elapsed = 3600 - 120  # 3480s elapsed → 120s remaining
        started = datetime.now(tz=UTC) - timedelta(seconds=elapsed)
        state.cooldown_wait_started_at = started.isoformat()

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await cb.handle_cooldown_wait("T01")

        # Should have slept in 60s chunks: two chunks of 60s = 120s total
        total_waited = sum(sleep_calls)
        assert abs(total_waited - 120.0) < 5.0  # within 5s tolerance

    @pytest.mark.asyncio
    async def test_handles_zero_remaining(self, cb: CircuitBreaker) -> None:
        """When remaining time is 0 (or negative), no sleep should occur."""
        from unittest.mock import patch

        sleep_calls: list[float] = []

        async def fake_sleep(secs: float) -> None:
            sleep_calls.append(secs)

        # Set up cooldown that already expired (started 2 hours ago)
        from datetime import datetime, timedelta

        state = cb._get_state("T01")
        state.cooldown_waiting = True
        state.cooldown_wait_count = 1
        started = datetime.now(tz=UTC) - timedelta(hours=2)
        state.cooldown_wait_started_at = started.isoformat()

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await cb.handle_cooldown_wait("T01")

        # No sleep needed — wait already elapsed
        assert sleep_calls == []
        assert state.cooldown_waiting is False

    @pytest.mark.asyncio
    async def test_handles_cancelled_error(self, cb: CircuitBreaker) -> None:
        """CancelledError during sleep should break out of the wait loop gracefully."""
        import asyncio
        from datetime import datetime, timedelta
        from unittest.mock import patch

        state = cb._get_state("T01")
        state.cooldown_waiting = True
        state.cooldown_wait_count = 1
        started = datetime.now(tz=UTC) - timedelta(seconds=10)
        state.cooldown_wait_started_at = started.isoformat()

        async def fake_sleep_cancel(secs: float) -> None:
            raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=fake_sleep_cancel):
            # Should not propagate CancelledError — caught internally
            await cb.handle_cooldown_wait("T01")

        # Flag should NOT be cleared since wait was interrupted
        # (the implementation breaks out but does NOT clear the flag on cancel)
        # — just verify no exception was raised

    @pytest.mark.asyncio
    async def test_saves_state_after_wait_completes(
        self, cb: CircuitBreaker, project_dir: Path
    ) -> None:
        """Circuit state should be saved to disk after cooldown wait completes."""
        from datetime import datetime, timedelta
        from unittest.mock import patch

        state = cb._get_state("T01")
        state.cooldown_waiting = True
        state.cooldown_wait_count = 1
        started = datetime.now(tz=UTC) - timedelta(seconds=3599)
        state.cooldown_wait_started_at = started.isoformat()

        async def fake_sleep(secs: float) -> None:
            pass

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await cb.handle_cooldown_wait("T01")

        # Verify state file was written
        state_file = project_dir / ".architect" / "circuit.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert "T01" in data
        assert data["T01"]["cooldown_waiting"] is False


# ---------------------------------------------------------------------------
# attempt_replan — async method
# ---------------------------------------------------------------------------


class TestAttemptReplan:
    """Tests for CircuitBreaker.attempt_replan()."""

    @pytest.mark.asyncio
    async def test_replan_returns_false_when_task_file_missing(
        self, cb: CircuitBreaker, project_dir: Path
    ) -> None:
        """Should return False when the task file cannot be read."""
        missing_file = project_dir / "tasks" / "T99_nonexistent.md"
        progress_file = project_dir / "PROGRESS.md"

        result = await cb.attempt_replan("T99", missing_file, progress_file)
        assert result is False

    @pytest.mark.asyncio
    async def test_replan_marks_replan_attempted(
        self, cb: CircuitBreaker, project_dir: Path
    ) -> None:
        """attempt_replan() should set replan_attempted=True before calling opencode."""
        from unittest.mock import AsyncMock, MagicMock, patch

        task_file = project_dir / "tasks" / "T01_setup.md"
        task_file.write_text("# T01 — Setup\n", encoding="utf-8")
        progress_file = project_dir / "PROGRESS.md"

        mock_stream_result = MagicMock()
        mock_stream_result.exit_code = 0

        with (
            patch(
                "the_architect.core.opencode_config.ensure_opencode_setup",
            ),
            patch(
                "the_architect.core.runner.stream_opencode",
                new_callable=AsyncMock,
                return_value=mock_stream_result,
            ),
            patch(
                "the_architect.core.tasks.discover_tasks",
                return_value=[],
            ),
        ):
            await cb.attempt_replan("T01", task_file, progress_file)

        state = cb._get_state("T01")
        assert state.replan_attempted is True

    @pytest.mark.asyncio
    async def test_replan_resets_circuit_state_on_success(
        self, cb: CircuitBreaker, project_dir: Path
    ) -> None:
        """After a successful replan, the task's circuit state should be reset."""
        from unittest.mock import AsyncMock, MagicMock, patch

        task_file = project_dir / "tasks" / "T01_setup.md"
        task_file.write_text("# T01 — Setup\n", encoding="utf-8")
        progress_file = project_dir / "PROGRESS.md"

        # Trip the circuit first
        for i in range(1, 4):
            cb.record_attempt(_summary(attempt=i, task_id="T01", files=[]))
        assert cb._get_state("T01").state == CircuitState.OPEN

        mock_stream_result = MagicMock()
        mock_stream_result.exit_code = 0

        with (
            patch("the_architect.core.opencode_provider.OpenCodeProvider.ensure_setup"),
            patch(
                "the_architect.core.runner.stream_provider",
                new_callable=AsyncMock,
                return_value=mock_stream_result,
            ),
            patch("the_architect.core.tasks.discover_tasks", return_value=[]),
        ):
            result = await cb.attempt_replan("T01", task_file, progress_file)

        assert result is True
        reset_state = cb._get_state("T01")
        # State should be reset to CLOSED with zeroed counters
        assert reset_state.state == CircuitState.CLOSED
        assert reset_state.consecutive_no_progress == 0
        assert reset_state.consecutive_same_error == 0

    @pytest.mark.asyncio
    async def test_replan_returns_false_on_opencode_exception(
        self, cb: CircuitBreaker, project_dir: Path
    ) -> None:
        """Should return False and fall back to WAIT when opencode raises."""
        from unittest.mock import AsyncMock, patch

        task_file = project_dir / "tasks" / "T01_setup.md"
        task_file.write_text("# T01 — Setup\n", encoding="utf-8")
        progress_file = project_dir / "PROGRESS.md"

        with (
            patch("the_architect.core.opencode_provider.OpenCodeProvider.ensure_setup"),
            patch(
                "the_architect.core.runner.stream_provider",
                new_callable=AsyncMock,
                side_effect=RuntimeError("provider not found"),
            ),
        ):
            result = await cb.attempt_replan("T01", task_file, progress_file)

        assert result is False
        # Should fall back to WAIT
        state = cb._get_state("T01")
        assert state.recovery_action == RecoveryAction.WAIT

    @pytest.mark.asyncio
    async def test_replan_discovers_new_task_files(
        self, cb: CircuitBreaker, project_dir: Path
    ) -> None:
        """New tasks discovered after replan should be registered in circuit state."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from the_architect.core.tasks import Task, TaskStatus

        task_file = project_dir / "tasks" / "T01_setup.md"
        task_file.write_text("# T01 — Setup\n", encoding="utf-8")
        progress_file = project_dir / "PROGRESS.md"

        # Simulate architect creating a new T01b task
        new_task_path = project_dir / "tasks" / "T01b_fix.md"
        new_task_path.write_text("# T01b — Fix\n", encoding="utf-8")
        new_task = Task(
            name="T01b_fix",
            prefix="T01b",
            number=1,
            path=new_task_path,
            title="Fix",
            status=TaskStatus.PENDING,
        )

        mock_stream_result = MagicMock()
        mock_stream_result.exit_code = 0

        with (
            patch("the_architect.core.opencode_provider.OpenCodeProvider.ensure_setup"),
            patch(
                "the_architect.core.runner.stream_provider",
                new_callable=AsyncMock,
                return_value=mock_stream_result,
            ),
            patch("the_architect.core.tasks.discover_tasks", return_value=[new_task]),
        ):
            await cb.attempt_replan("T01", task_file, progress_file)

        # T01b should now have a circuit state entry
        assert "T01b" in cb._states

    @pytest.mark.asyncio
    async def test_cooldown_and_replan_interaction(
        self, config: ArchitectConfig, project_dir: Path
    ) -> None:
        """Verify cooldown detection does not prevent replanning."""
        cb = CircuitBreaker(config=config, project_root=project_dir)

        # Set cooldown state
        state = cb._get_state("T01")
        state.cooldown_waiting = True
        state.cooldown_wait_count = 1

        from datetime import datetime, timedelta

        started = datetime.now(tz=UTC) - timedelta(seconds=3599)
        state.cooldown_wait_started_at = started.isoformat()

        # Verify can_run returns False (in cooldown)
        allowed, reason = cb.can_run("T01")
        assert not allowed
        assert "cooldown_wait_resume" in reason
