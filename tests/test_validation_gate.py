"""Tests for the post-task validation gate.

Covers the core module (validation_gate.py + validation_gate_models.py) and
the runner integration (runner.py gate logic around lines 4092-4168).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from the_architect.core.runner import TaskResult, TokenUsage
from the_architect.core.validation_gate import (
    _DEFAULT_COMMANDS,
    _GO_DEFAULTS,
    _JAVASCRIPT_DEFAULTS,
    _PYTHON_DEFAULTS,
    _RUST_DEFAULTS,
    _detect_project_type,
    _discover_commands,
    _run_single_check,
    run_validation_gate,
    run_validation_gate_sync,
)
from the_architect.core.validation_gate_models import (
    GateCheckResult,
    ValidationGateConfig,
    ValidationGateResult,
)

# ============================================================================
# T03.1 — Core module tests
# ============================================================================


# ---------------------------------------------------------------------------
# ValidationGateConfig model tests
# ---------------------------------------------------------------------------


class TestValidationGateConfigDefaults:
    """ValidationGateConfig uses correct default values."""

    def test_defaults(self) -> None:
        cfg = ValidationGateConfig()
        assert cfg.enabled is True
        assert cfg.checks == ["lint", "test", "typecheck"]
        assert cfg.fail_fast is True
        assert cfg.timeout == 120
        assert cfg.fixup_attempts == 2

    def test_custom_values(self) -> None:
        cfg = ValidationGateConfig(
            enabled=False,
            checks=["test"],
            fail_fast=False,
            timeout=60,
            fixup_attempts=5,
        )
        assert cfg.enabled is False
        assert cfg.checks == ["test"]
        assert cfg.fail_fast is False
        assert cfg.timeout == 60
        assert cfg.fixup_attempts == 5

    def test_timeout_rejects_zero(self) -> None:
        with pytest.raises(Exception):
            ValidationGateConfig(timeout=0)

    def test_timeout_rejects_negative(self) -> None:
        with pytest.raises(Exception):
            ValidationGateConfig(timeout=-5)

    def test_disabled(self) -> None:
        cfg = ValidationGateConfig(enabled=False)
        assert cfg.enabled is False

    def test_empty_checks_list(self) -> None:
        cfg = ValidationGateConfig(checks=[])
        assert cfg.checks == []

    def test_fixup_attempts_zero(self) -> None:
        """fixup_attempts=0 disables fix-up (previous behaviour)."""
        cfg = ValidationGateConfig(fixup_attempts=0)
        assert cfg.fixup_attempts == 0

    def test_fixup_attempts_rejects_negative(self) -> None:
        """fixup_attempts must be >= 0."""
        with pytest.raises(Exception):
            ValidationGateConfig(fixup_attempts=-1)


# ---------------------------------------------------------------------------
# GateCheckResult model tests
# ---------------------------------------------------------------------------


class TestGateCheckResultModel:
    """GateCheckResult stores all required fields."""

    def test_fields(self) -> None:
        result = GateCheckResult(
            name="lint",
            status="pass",
            output="clean",
            duration=0.5,
        )
        assert result.name == "lint"
        assert result.status == "pass"
        assert result.output == "clean"
        assert result.duration == 0.5

    def test_default_output_and_duration(self) -> None:
        result = GateCheckResult(name="test", status="fail")
        assert result.output == ""
        assert result.duration == 0.0


# ---------------------------------------------------------------------------
# ValidationGateResult model tests
# ---------------------------------------------------------------------------


class TestValidationGateResultModel:
    """ValidationGateResult tracks aggregate pass/fail."""

    def test_pass_state(self) -> None:
        res = ValidationGateResult(
            passed=True,
            checks=[GateCheckResult(name="lint", status="pass")],
        )
        assert res.passed is True
        assert len(res.checks) == 1

    def test_fail_state(self) -> None:
        res = ValidationGateResult(
            passed=False,
            checks=[GateCheckResult(name="test", status="fail")],
        )
        assert res.passed is False
        assert len(res.checks) == 1

    def test_default_empty_checks(self) -> None:
        res = ValidationGateResult(passed=True)
        assert res.checks == []


# ---------------------------------------------------------------------------
# _run_single_check tests
# ---------------------------------------------------------------------------


class TestRunSingleCheck:
    """_run_single_check executes a command and returns structured results."""

    async def test_pass(self, tmp_path: Path) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"clean", b""))
        mock_proc.returncode = 0
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()
        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            result = await _run_single_check("lint", "ruff check .", tmp_path, 10)
        assert result.name == "lint"
        assert result.status == "pass"
        assert result.duration >= 0.0

    async def test_fail(self, tmp_path: Path) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"error", b""))
        mock_proc.returncode = 1
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()
        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            result = await _run_single_check("lint", "ruff check .", tmp_path, 10)
        assert result.status == "fail"

    async def test_timeout(self, tmp_path: Path) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError)
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()
        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            result = await _run_single_check("lint", "ruff check .", tmp_path, 10)
        assert result.status == "error"
        assert "Timed out" in result.output
        mock_proc.kill.assert_called_once()

    async def test_file_not_found(self, tmp_path: Path) -> None:
        with patch(
            "asyncio.create_subprocess_shell",
            side_effect=FileNotFoundError("no such command"),
        ):
            result = await _run_single_check("lint", "ruff check .", tmp_path, 10)
        assert result.status == "error"
        assert "not found" in result.output.lower() or "not found" in result.output

    async def test_os_error(self, tmp_path: Path) -> None:
        with patch(
            "asyncio.create_subprocess_shell",
            side_effect=OSError("permission denied"),
        ):
            result = await _run_single_check("lint", "ruff check .", tmp_path, 10)
        assert result.status == "error"
        assert "OS error" in result.output or "os error" in result.output.lower()


# ---------------------------------------------------------------------------
# run_validation_gate tests
# ---------------------------------------------------------------------------


class TestRunValidationGate:
    """run_validation_gate orchestrates checks with fail_fast support."""

    async def test_disabled_returns_passed(self, tmp_path: Path) -> None:
        cfg = ValidationGateConfig(enabled=False)
        result = await run_validation_gate(tmp_path, cfg)
        assert result.passed is True
        assert result.checks == []

    async def test_fail_fast_stops_at_first_failure(self, tmp_path: Path) -> None:
        """With fail_fast=True, second check never runs when first fails."""
        call_order: list[str] = []

        async def fake_check(check_name, command, project_root, timeout):
            call_order.append(check_name)
            if check_name == "lint":
                return GateCheckResult(name=check_name, status="fail", output="lint error")
            return GateCheckResult(name=check_name, status="pass")

        cfg = ValidationGateConfig(
            checks=["lint", "test", "typecheck"],
            custom_commands={
                "lint": "echo lint",
                "test": "echo test",
                "typecheck": "echo typecheck",
            },
            fail_fast=True,
        )
        with patch(
            "the_architect.core.validation_gate._run_single_check",
            new=MagicMock(side_effect=lambda **kw: fake_check(**kw)),
        ):
            result = await run_validation_gate(tmp_path, cfg)

        assert result.passed is False
        assert len(result.checks) == 1
        assert call_order == ["lint"]

    async def test_all_checks_pass(self, tmp_path: Path) -> None:
        call_order: list[str] = []

        async def fake_check(check_name, command, project_root, timeout):
            call_order.append(check_name)
            return GateCheckResult(name=check_name, status="pass")

        cfg = ValidationGateConfig(
            checks=["lint", "test", "typecheck"],
            custom_commands={
                "lint": "echo lint",
                "test": "echo test",
                "typecheck": "echo typecheck",
            },
            fail_fast=True,
        )
        with patch(
            "the_architect.core.validation_gate._run_single_check",
            new=MagicMock(side_effect=lambda **kw: fake_check(**kw)),
        ):
            result = await run_validation_gate(tmp_path, cfg)

        assert result.passed is True
        assert len(result.checks) == 3
        assert call_order == ["lint", "test", "typecheck"]

    async def test_mixed_results_without_fail_fast(self, tmp_path: Path) -> None:
        """Without fail_fast, all checks run even if some fail."""
        call_order: list[str] = []

        async def fake_check(check_name, command, project_root, timeout):
            call_order.append(check_name)
            if check_name == "test":
                return GateCheckResult(name=check_name, status="fail", output="test error")
            return GateCheckResult(name=check_name, status="pass")

        cfg = ValidationGateConfig(
            checks=["lint", "test", "typecheck"],
            custom_commands={
                "lint": "echo lint",
                "test": "echo test",
                "typecheck": "echo typecheck",
            },
            fail_fast=False,
        )
        with patch(
            "the_architect.core.validation_gate._run_single_check",
            new=MagicMock(side_effect=lambda **kw: fake_check(**kw)),
        ):
            result = await run_validation_gate(tmp_path, cfg)

        assert result.passed is False
        assert len(result.checks) == 3
        assert call_order == ["lint", "test", "typecheck"]
        # lint passed, test failed, typecheck passed
        assert result.checks[0].status == "pass"
        assert result.checks[1].status == "fail"
        assert result.checks[2].status == "pass"


# ---------------------------------------------------------------------------
# run_validation_gate_sync tests
# ---------------------------------------------------------------------------


class TestRunValidationGateSync:
    """Synchronous wrapper delegates to async implementation."""

    def test_sync_wrapper(self, tmp_path: Path) -> None:
        cfg = ValidationGateConfig(enabled=False)
        result = run_validation_gate_sync(tmp_path, cfg)
        assert isinstance(result, ValidationGateResult)
        assert result.passed is True
        assert result.checks == []


# ---------------------------------------------------------------------------
# _discover_commands tests
# ---------------------------------------------------------------------------


class TestDiscoverCommands:
    """_discover_commands reads pyproject.toml or returns defaults."""

    def test_no_pyproject_returns_defaults(self, tmp_path: Path) -> None:
        """No manifest files → unknown project type → empty commands."""
        commands = _discover_commands(tmp_path)
        assert commands == {}

    def test_with_pyproject_present(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 88\n")
        commands = _discover_commands(tmp_path)
        assert commands == _DEFAULT_COMMANDS

    def test_unreadable_pyproject_returns_defaults(self, tmp_path: Path) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.ruff]\n")
        # Make file unreadable
        pyproject.chmod(0o000)
        commands = _discover_commands(tmp_path)
        assert commands == _DEFAULT_COMMANDS
        # Restore permissions so cleanup works
        pyproject.chmod(0o644)


# ============================================================================
# T03.2 — Runner integration tests
# ============================================================================


class TestRunnerIntegration:
    """Tests for the runner's validation gate integration logic.

    These tests simulate the gate decision flow from runner.py lines 4092-4168
    rather than calling _execute_one directly (which is deeply nested with
    complex dependencies).
    """

    def _build_task_result(self, status: str = "done") -> TaskResult:
        """Create a minimal TaskResult for gate testing."""
        return TaskResult(
            prefix="T01",
            title="Test task",
            status=status,
            duration_seconds=1.0,
            attempts=1,
            tokens=TokenUsage(),
            model="test-model",
        )

    def test_gate_skips_when_pytest_env_set(self, monkeypatch) -> None:
        """PYTEST_CURRENT_TEST present → gate is skipped (existing behavior)."""
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "some_test.py::test_foo")
        # The runner checks os.environ.get("PYTEST_CURRENT_TEST")
        # If set, it skips the gate entirely
        assert os.environ.get("PYTEST_CURRENT_TEST") == "some_test.py::test_foo"

    def test_gate_skips_when_config_disabled(self) -> None:
        """config.validation_gate_config.enabled=False → gate is skipped."""
        cfg = ValidationGateConfig(enabled=False)
        assert cfg.enabled is False
        # In runner.py: if task_result.status == "done" and config.validation_gate_config.enabled
        # When enabled is False, the gate block is never entered

    def test_gate_runs_when_enabled_not_pytest(self, monkeypatch, tmp_path: Path) -> None:
        """Gate executes when enabled and PYTEST_CURRENT_TEST is absent."""
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        assert os.environ.get("PYTEST_CURRENT_TEST") is None
        cfg = ValidationGateConfig(enabled=True)
        assert cfg.enabled is True

    def test_gate_reclassifies_done_to_failed(self) -> None:
        """When gate returns passed=False, task status changes from done to failed."""
        task_result = self._build_task_result(status="done")
        gate_result = ValidationGateResult(
            passed=False,
            checks=[GateCheckResult(name="lint", status="fail", output="lint error")],
        )

        # Simulate runner gate logic
        if task_result.status == "done":
            task_result.validation_gate = gate_result
            if not gate_result.passed:
                failed_checks = [r.name for r in gate_result.checks if r.status != "pass"]
                skip_msg = f"validation gate failed: {', '.join(failed_checks)}"
                task_result.status = "failed"
                task_result.skip_reason = skip_msg

        assert task_result.status == "failed"
        assert task_result.skip_reason == "validation gate failed: lint"
        assert task_result.validation_gate is gate_result

    def test_gate_preserves_done_on_success(self) -> None:
        """When gate returns passed=True, task status remains done."""
        task_result = self._build_task_result(status="done")
        gate_result = ValidationGateResult(
            passed=True,
            checks=[GateCheckResult(name="lint", status="pass")],
        )

        if task_result.status == "done":
            task_result.validation_gate = gate_result
            if not gate_result.passed:
                task_result.status = "failed"

        assert task_result.status == "done"
        assert task_result.validation_gate is gate_result

    def test_gate_fires_circuit_event_on_failure(self) -> None:
        """on_circuit_event('validation_gate_failed') fires when gate fails."""
        task_result = self._build_task_result(status="done")
        gate_result = ValidationGateResult(
            passed=False,
            checks=[GateCheckResult(name="test", status="fail", output="test error")],
        )

        circuit_events: list[tuple[str, dict]] = []

        def on_circuit_event(event_name: str, data: dict) -> None:
            circuit_events.append((event_name, data))

        # Simulate runner gate logic with circuit event
        if task_result.status == "done":
            task_result.validation_gate = gate_result
            if not gate_result.passed:
                failed_checks = [r.name for r in gate_result.checks if r.status != "pass"]
                skip_msg = f"validation gate failed: {', '.join(failed_checks)}"
                task_result.status = "failed"
                task_result.skip_reason = skip_msg
                if on_circuit_event:
                    try:
                        on_circuit_event(
                            "validation_gate_failed",
                            {
                                "task_id": task_result.prefix,
                                "failed_checks": ", ".join(failed_checks),
                            },
                        )
                    except Exception:
                        pass

        assert len(circuit_events) == 1
        assert circuit_events[0][0] == "validation_gate_failed"
        assert circuit_events[0][1]["task_id"] == "T01"
        assert circuit_events[0][1]["failed_checks"] == "test"

    def test_gate_fires_task_failed_callback_after_reclassification(self) -> None:
        """on_task_failed fires (not on_task_done) after gate reclassification."""
        task_result = self._build_task_result(status="done")
        gate_result = ValidationGateResult(
            passed=False,
            checks=[GateCheckResult(name="lint", status="fail")],
        )

        callbacks_fired: list[str] = []

        def on_task_done(tr: TaskResult) -> None:
            callbacks_fired.append("on_task_done")

        def on_task_failed(tr: TaskResult) -> None:
            callbacks_fired.append("on_task_failed")

        # Simulate gate logic
        if task_result.status == "done":
            task_result.validation_gate = gate_result
            if not gate_result.passed:
                failed_checks = [r.name for r in gate_result.checks if r.status != "pass"]
                skip_msg = f"validation gate failed: {', '.join(failed_checks)}"
                task_result.status = "failed"
                task_result.skip_reason = skip_msg

        # Simulate post-gate callback dispatch (runner.py lines 4157-4168)
        if task_result.status == "done":
            if on_task_done:
                try:
                    on_task_done(task_result)
                except Exception:
                    pass
        else:
            if on_task_failed:
                try:
                    on_task_failed(task_result)
                except Exception:
                    pass

        assert callbacks_fired == ["on_task_failed"]
        assert "on_task_done" not in callbacks_fired

    def test_gate_fires_task_done_callback_on_success(self) -> None:
        """on_task_done fires when gate passes."""
        task_result = self._build_task_result(status="done")
        gate_result = ValidationGateResult(
            passed=True,
            checks=[GateCheckResult(name="lint", status="pass")],
        )

        callbacks_fired: list[str] = []

        def on_task_done(tr: TaskResult) -> None:
            callbacks_fired.append("on_task_done")

        def on_task_failed(tr: TaskResult) -> None:
            callbacks_fired.append("on_task_failed")

        # Simulate gate logic
        if task_result.status == "done":
            task_result.validation_gate = gate_result
            if not gate_result.passed:
                failed_checks = [r.name for r in gate_result.checks if r.status != "pass"]
                skip_msg = f"validation gate failed: {', '.join(failed_checks)}"
                task_result.status = "failed"
                task_result.skip_reason = skip_msg

        # Simulate post-gate callback dispatch
        if task_result.status == "done":
            if on_task_done:
                try:
                    on_task_done(task_result)
                except Exception:
                    pass
        else:
            if on_task_failed:
                try:
                    on_task_failed(task_result)
                except Exception:
                    pass

        assert callbacks_fired == ["on_task_done"]
        assert "on_task_failed" not in callbacks_fired

    def test_task_result_validation_gate_field_set(self) -> None:
        """task_result.validation_gate is populated after gate runs."""
        task_result = self._build_task_result(status="done")
        gate_result = ValidationGateResult(
            passed=True,
            checks=[GateCheckResult(name="lint", status="pass")],
        )

        assert task_result.validation_gate is None
        task_result.validation_gate = gate_result
        assert task_result.validation_gate is gate_result
        assert isinstance(task_result.validation_gate, ValidationGateResult)

    def test_gate_noop_on_non_done_task(self) -> None:
        """Gate is not run when task status is not 'done'."""
        task_result = self._build_task_result(status="failed")

        # The runner only enters gate logic when task_result.status == "done"
        # So a failed task should never trigger the gate
        assert task_result.status == "failed"
        assert task_result.validation_gate is None


# ============================================================================
# T03.3 — Edge case tests
# ============================================================================


class TestEdgeCases:
    """Edge cases and error handling for the validation gate."""

    def _build_task_result(self, status: str = "done") -> TaskResult:
        """Create a minimal TaskResult for gate testing."""
        return TaskResult(
            prefix="T01",
            title="Test task",
            status=status,
            duration_seconds=1.0,
            attempts=1,
            tokens=TokenUsage(),
            model="test-model",
        )

    def test_discover_commands_unreadable_pyproject_oserror(self, tmp_path: Path) -> None:
        """_discover_commands returns defaults when pyproject.toml raises OSError."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.ruff]\n")
        pyproject.chmod(0o000)
        commands = _discover_commands(tmp_path)
        assert commands == _DEFAULT_COMMANDS
        pyproject.chmod(0o644)

    async def test_unknown_check_name_skipped(self, tmp_path: Path) -> None:
        """A check name not in _DEFAULT_COMMANDS is skipped gracefully."""
        # The Literal type only allows "lint", "test", "typecheck", but
        # if an unknown check somehow gets through, it should be skipped
        # via the `commands.get(check_name)` returning None.
        # We test this by providing a config with checks that don't match
        # any discovered commands.
        cfg = ValidationGateConfig(checks=["lint"])

        # Patch _discover_commands to return an empty dict
        with patch(
            "the_architect.core.validation_gate._discover_commands",
            return_value={},
        ):
            result = await run_validation_gate(tmp_path, cfg)

        # All checks were skipped (no commands available)
        assert result.checks == []
        # Empty check list → passed is True (all([]) == True)
        assert result.passed is True

    async def test_gate_zero_checks_passes(self, tmp_path: Path) -> None:
        """Gate with empty checks list returns passed=True."""
        cfg = ValidationGateConfig(checks=[], enabled=True)
        result = await run_validation_gate(tmp_path, cfg)
        assert result.passed is True
        assert result.checks == []

    def test_circuit_event_handler_exception_swallowed(self) -> None:
        """Exceptions in on_circuit_event handler are swallowed silently."""
        task_result = self._build_task_result(status="done")
        gate_result = ValidationGateResult(
            passed=False,
            checks=[GateCheckResult(name="lint", status="fail")],
        )

        def bad_circuit_handler(event_name: str, data: dict) -> None:
            raise RuntimeError("handler crash")

        # Simulate runner gate logic — the try/except should swallow the error
        if task_result.status == "done":
            task_result.validation_gate = gate_result
            if not gate_result.passed:
                failed_checks = [r.name for r in gate_result.checks if r.status != "pass"]
                skip_msg = f"validation gate failed: {', '.join(failed_checks)}"
                task_result.status = "failed"
                task_result.skip_reason = skip_msg
                on_circuit_event = bad_circuit_handler
                if on_circuit_event:
                    try:
                        on_circuit_event(
                            "validation_gate_failed",
                            {
                                "task_id": task_result.prefix,
                                "failed_checks": ", ".join(failed_checks),
                            },
                        )
                    except Exception:
                        pass

        # Task was reclassified despite handler crash
        assert task_result.status == "failed"

    async def test_gate_with_only_error_checks(self, tmp_path: Path) -> None:
        """All checks erroring results in passed=False."""

        async def fake_check(check_name, command, project_root, timeout):
            return GateCheckResult(name=check_name, status="error", output="error")

        cfg = ValidationGateConfig(
            checks=["lint", "test", "typecheck"],
            custom_commands={
                "lint": "echo lint",
                "test": "echo test",
                "typecheck": "echo typecheck",
            },
            fail_fast=False,
        )
        with patch(
            "the_architect.core.validation_gate._run_single_check",
            new=MagicMock(side_effect=lambda **kw: fake_check(**kw)),
        ):
            result = await run_validation_gate(tmp_path, cfg)

        assert result.passed is False
        assert len(result.checks) == 3
        assert all(r.status == "error" for r in result.checks)

    async def test_gate_fail_fast_with_error_stops(self, tmp_path: Path) -> None:
        """An error status triggers fail_fast stop (not just fail)."""
        call_order: list[str] = []

        async def fake_check(check_name, command, project_root, timeout):
            call_order.append(check_name)
            return GateCheckResult(name=check_name, status="error", output="error")

        cfg = ValidationGateConfig(
            checks=["lint", "test", "typecheck"],
            custom_commands={
                "lint": "echo lint",
                "test": "echo test",
                "typecheck": "echo typecheck",
            },
            fail_fast=True,
        )
        with patch(
            "the_architect.core.validation_gate._run_single_check",
            new=MagicMock(side_effect=lambda **kw: fake_check(**kw)),
        ):
            result = await run_validation_gate(tmp_path, cfg)

        assert result.passed is False
        assert len(result.checks) == 1
        assert call_order == ["lint"]

    def test_skip_reason_contains_failed_check_names(self) -> None:
        """skip_reason lists the names of failed checks."""
        task_result = self._build_task_result(status="done")
        gate_result = ValidationGateResult(
            passed=False,
            checks=[
                GateCheckResult(name="lint", status="pass"),
                GateCheckResult(name="test", status="fail"),
                GateCheckResult(name="typecheck", status="error"),
            ],
        )

        if task_result.status == "done":
            task_result.validation_gate = gate_result
            if not gate_result.passed:
                failed_checks = [r.name for r in gate_result.checks if r.status != "pass"]
                skip_msg = f"validation gate failed: {', '.join(failed_checks)}"
                task_result.status = "failed"
                task_result.skip_reason = skip_msg

        assert "test" in task_result.skip_reason
        assert "typecheck" in task_result.skip_reason
        assert "lint" not in task_result.skip_reason


# ============================================================================
# T02.1 — ValidationGateConfig custom_commands model tests
# ============================================================================


class TestValidationGateConfigCustomCommands:
    """Tests for ValidationGateConfig with custom_commands field."""

    def test_custom_commands_default_empty(self) -> None:
        """custom_commands defaults to empty dict."""
        cfg = ValidationGateConfig()
        assert cfg.custom_commands == {}

    def test_custom_commands_single_entry(self) -> None:
        """custom_commands accepts a single custom check."""
        cfg = ValidationGateConfig(custom_commands={"build": "npm run build"})
        assert cfg.custom_commands == {"build": "npm run build"}

    def test_custom_commands_multiple_entries(self) -> None:
        """custom_commands accepts multiple custom checks."""
        cfg = ValidationGateConfig(
            custom_commands={
                "build": "npm run build",
                "security": "npm audit",
                "format": "prettier --check .",
            }
        )
        assert cfg.custom_commands["build"] == "npm run build"
        assert cfg.custom_commands["security"] == "npm audit"
        assert cfg.custom_commands["format"] == "prettier --check ."

    def test_custom_commands_override_builtin_lint(self) -> None:
        """custom_commands can override a built-in check name like 'lint'."""
        cfg = ValidationGateConfig(custom_commands={"lint": "eslint ."})
        assert cfg.custom_commands == {"lint": "eslint ."}

    def test_custom_commands_override_all_builtins(self) -> None:
        """custom_commands can override all three built-in check names."""
        cfg = ValidationGateConfig(
            custom_commands={
                "lint": "eslint .",
                "test": "jest",
                "typecheck": "tsc --noEmit",
            }
        )
        assert cfg.custom_commands["lint"] == "eslint ."
        assert cfg.custom_commands["test"] == "jest"
        assert cfg.custom_commands["typecheck"] == "tsc --noEmit"

    def test_custom_commands_with_checks_list(self) -> None:
        """custom_commands and checks can be set together."""
        cfg = ValidationGateConfig(
            checks=["lint", "build", "security"],
            custom_commands={
                "build": "cargo build",
                "security": "cargo audit",
            },
        )
        assert cfg.checks == ["lint", "build", "security"]
        assert cfg.custom_commands["build"] == "cargo build"
        assert cfg.custom_commands["security"] == "cargo audit"

    def test_custom_commands_preserved_with_fail_fast(self) -> None:
        """custom_commands coexists with fail_fast setting."""
        cfg = ValidationGateConfig(
            custom_commands={"build": "make"},
            fail_fast=False,
        )
        assert cfg.custom_commands == {"build": "make"}
        assert cfg.fail_fast is False

    def test_checks_accepts_arbitrary_strings(self) -> None:
        """checks field accepts arbitrary check names (not just built-in literals)."""
        cfg = ValidationGateConfig(checks=["lint", "build", "security", "format"])
        assert cfg.checks == ["lint", "build", "security", "format"]


# ============================================================================
# T02.2 — _discover_commands() merge behavior tests
# ============================================================================


class TestDiscoverCommandsCustomMerge:
    """Tests for _discover_commands() merging custom commands with defaults."""

    def test_no_custom_commands_returns_defaults(self, tmp_path: Path) -> None:
        """Without custom_commands, _discover_commands returns Python defaults."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        commands = _discover_commands(tmp_path)
        assert commands == _DEFAULT_COMMANDS

    def test_empty_custom_commands_returns_defaults(self, tmp_path: Path) -> None:
        """Empty custom_commands dict returns defaults unchanged."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        commands = _discover_commands(tmp_path, custom_commands={})
        assert commands == _DEFAULT_COMMANDS

    def test_none_custom_commands_returns_defaults(self, tmp_path: Path) -> None:
        """None custom_commands returns defaults unchanged."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        commands = _discover_commands(tmp_path, custom_commands=None)
        assert commands == _DEFAULT_COMMANDS

    def test_custom_command_overrides_builtin_lint(self, tmp_path: Path) -> None:
        """Custom 'lint' command overrides the built-in default."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        commands = _discover_commands(
            tmp_path,
            custom_commands={"lint": "eslint ."},
        )
        assert commands["lint"] == "eslint ."
        assert commands["test"] == _DEFAULT_COMMANDS["test"]
        assert commands["typecheck"] == _DEFAULT_COMMANDS["typecheck"]

    def test_custom_command_overrides_builtin_test(self, tmp_path: Path) -> None:
        """Custom 'test' command overrides the built-in default."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        commands = _discover_commands(
            tmp_path,
            custom_commands={"test": "jest"},
        )
        assert commands["test"] == "jest"
        assert commands["lint"] == _DEFAULT_COMMANDS["lint"]
        assert commands["typecheck"] == _DEFAULT_COMMANDS["typecheck"]

    def test_custom_command_overrides_builtin_typecheck(self, tmp_path: Path) -> None:
        """Custom 'typecheck' command overrides the built-in default."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        commands = _discover_commands(
            tmp_path,
            custom_commands={"typecheck": "tsc --noEmit"},
        )
        assert commands["typecheck"] == "tsc --noEmit"
        assert commands["lint"] == _DEFAULT_COMMANDS["lint"]
        assert commands["test"] == _DEFAULT_COMMANDS["test"]

    def test_custom_command_overrides_all_builtins(self, tmp_path: Path) -> None:
        """Custom commands can override all three built-in checks."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        commands = _discover_commands(
            tmp_path,
            custom_commands={
                "lint": "eslint .",
                "test": "jest",
                "typecheck": "tsc --noEmit",
            },
        )
        assert commands["lint"] == "eslint ."
        assert commands["test"] == "jest"
        assert commands["typecheck"] == "tsc --noEmit"

    def test_custom_check_name_added_as_is(self, tmp_path: Path) -> None:
        """A custom check name not in defaults is added alongside defaults."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        commands = _discover_commands(
            tmp_path,
            custom_commands={"build": "npm run build"},
        )
        assert commands["build"] == "npm run build"
        assert commands["lint"] == _DEFAULT_COMMANDS["lint"]
        assert commands["test"] == _DEFAULT_COMMANDS["test"]
        assert commands["typecheck"] == _DEFAULT_COMMANDS["typecheck"]

    def test_custom_check_names_added_with_builtin_override(self, tmp_path: Path) -> None:
        """Custom commands can both override builtins and add new names."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        commands = _discover_commands(
            tmp_path,
            custom_commands={
                "lint": "eslint .",
                "build": "npm run build",
                "security": "npm audit",
            },
        )
        assert commands["lint"] == "eslint ."
        assert commands["build"] == "npm run build"
        assert commands["security"] == "npm audit"
        assert commands["test"] == _DEFAULT_COMMANDS["test"]
        assert commands["typecheck"] == _DEFAULT_COMMANDS["typecheck"]

    def test_custom_commands_without_pyproject(self, tmp_path: Path) -> None:
        """Custom commands are applied even when pyproject.toml is absent.

        With no manifest, the project type is 'unknown' so defaults are empty.
        Custom commands are still merged into the empty base.
        """
        commands = _discover_commands(
            tmp_path,
            custom_commands={"build": "cargo build"},
        )
        assert commands["build"] == "cargo build"
        # No Python defaults for unknown project type
        assert "lint" not in commands

    def test_custom_commands_with_unreadable_pyproject(self, tmp_path: Path) -> None:
        """Custom commands are applied even when pyproject.toml is unreadable."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.ruff]\n")
        pyproject.chmod(0o000)
        commands = _discover_commands(
            tmp_path,
            custom_commands={"build": "make"},
        )
        assert commands["build"] == "make"
        assert commands["lint"] == _DEFAULT_COMMANDS["lint"]
        pyproject.chmod(0o644)

    def test_custom_commands_preserve_default_keys(self, tmp_path: Path) -> None:
        """Adding a custom check does not remove default keys."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        commands = _discover_commands(
            tmp_path,
            custom_commands={"format": "prettier --check ."},
        )
        assert "lint" in commands
        assert "test" in commands
        assert "typecheck" in commands
        assert "format" in commands
        assert len(commands) == 4


# ============================================================================
# T02.3 — ArchitectConfig.validation_gate_config property tests
# ============================================================================


class TestArchitectConfigValidationGateProperty:
    """Tests for ArchitectConfig.validation_gate_config passing custom_commands."""

    def test_default_config_returns_default_gate(self) -> None:
        """Default ArchitectConfig produces default ValidationGateConfig."""
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig()
        gate = config.validation_gate_config
        assert gate.enabled is True
        assert gate.checks == ["lint", "test", "typecheck"]
        assert gate.custom_commands == {}
        assert gate.fail_fast is True
        assert gate.timeout == 120

    def test_config_with_custom_commands(self) -> None:
        """ArchitectConfig passes custom_commands to ValidationGateConfig."""
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig(
            validation_gate={
                "enabled": True,
                "checks": ["lint", "build"],
                "custom_commands": {
                    "build": "npm run build",
                },
            }
        )
        gate = config.validation_gate_config
        assert gate.custom_commands == {"build": "npm run build"}
        assert gate.checks == ["lint", "build"]

    def test_config_with_custom_commands_override_builtin(self) -> None:
        """custom_commands can override built-in checks via config."""
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig(
            validation_gate={
                "enabled": True,
                "custom_commands": {
                    "lint": "eslint .",
                    "test": "jest",
                },
            }
        )
        gate = config.validation_gate_config
        assert gate.custom_commands["lint"] == "eslint ."
        assert gate.custom_commands["test"] == "jest"

    def test_config_empty_custom_commands(self) -> None:
        """Empty custom_commands dict in config produces empty dict in gate."""
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig(
            validation_gate={
                "enabled": True,
                "custom_commands": {},
            }
        )
        gate = config.validation_gate_config
        assert gate.custom_commands == {}

    def test_config_no_custom_commands_key(self) -> None:
        """Missing custom_commands key defaults to empty dict."""
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig(
            validation_gate={
                "enabled": True,
                "checks": ["test"],
            }
        )
        gate = config.validation_gate_config
        assert gate.custom_commands == {}
        assert gate.checks == ["test"]

    def test_config_empty_validation_gate_dict(self) -> None:
        """Empty validation_gate dict returns default ValidationGateConfig."""
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig(validation_gate={})
        gate = config.validation_gate_config
        assert gate.enabled is True
        assert gate.checks == ["lint", "test", "typecheck"]
        assert gate.custom_commands == {}

    def test_config_all_fields_together(self) -> None:
        """All validation_gate fields work together including custom_commands."""
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig(
            validation_gate={
                "enabled": False,
                "checks": ["build", "security"],
                "custom_commands": {
                    "build": "cargo build",
                    "security": "cargo audit",
                    "lint": "clippy",
                },
                "fail_fast": False,
                "timeout": 300,
            }
        )
        gate = config.validation_gate_config
        assert gate.enabled is False
        assert gate.checks == ["build", "security"]
        assert gate.custom_commands == {
            "build": "cargo build",
            "security": "cargo audit",
            "lint": "clippy",
        }
        assert gate.fail_fast is False
        assert gate.timeout == 300

    def test_config_backward_compatible_no_custom_commands(self) -> None:
        """Existing config without custom_commands still works (backward compat)."""
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig(
            validation_gate={
                "enabled": True,
                "checks": ["lint", "test"],
                "fail_fast": True,
                "timeout": 60,
            }
        )
        gate = config.validation_gate_config
        assert gate.enabled is True
        assert gate.checks == ["lint", "test"]
        assert gate.custom_commands == {}
        assert gate.fail_fast is True
        assert gate.timeout == 60

    def test_config_custom_commands_survive_resolve(self, tmp_path: Path) -> None:
        """custom_commands in validation_gate survives resolve()."""
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig(
            validation_gate={
                "custom_commands": {
                    "build": "npm run build",
                },
            }
        )
        resolved = config.resolve(tmp_path)
        gate = resolved.validation_gate_config
        assert gate.custom_commands == {"build": "npm run build"}


# ============================================================================
# T02.4 — End-to-end validation gate with custom commands
# ============================================================================


class TestValidationGateWithCustomCommands:
    """Integration tests for custom commands flowing through the gate."""

    async def test_custom_command_runs_via_gate(self, tmp_path: Path) -> None:
        """A custom command is actually executed by run_validation_gate."""
        cfg = ValidationGateConfig(
            checks=["build"],
            custom_commands={"build": "echo hello"},
            fail_fast=False,
        )
        result = await run_validation_gate(tmp_path, cfg)
        assert result.passed is True
        assert len(result.checks) == 1
        assert result.checks[0].name == "build"
        assert result.checks[0].status == "pass"

    async def test_custom_command_override_lint_runs(self, tmp_path: Path) -> None:
        """Overriding 'lint' with a custom command executes the custom command."""
        cfg = ValidationGateConfig(
            checks=["lint"],
            custom_commands={"lint": "echo linting"},
        )
        result = await run_validation_gate(tmp_path, cfg)
        assert result.passed is True
        assert len(result.checks) == 1
        assert result.checks[0].name == "lint"
        assert result.checks[0].status == "pass"

    async def test_custom_command_failure_detected(self, tmp_path: Path) -> None:
        """A failing custom command is detected by the gate."""
        cfg = ValidationGateConfig(
            checks=["build"],
            custom_commands={"build": "exit 1"},
        )
        result = await run_validation_gate(tmp_path, cfg)
        assert result.passed is False
        assert len(result.checks) == 1
        assert result.checks[0].status == "fail"

    async def test_mixed_builtin_and_custom_checks(self, tmp_path: Path) -> None:
        """Built-in and custom checks run together."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        cfg = ValidationGateConfig(
            checks=["lint", "build"],
            custom_commands={"build": "echo build_ok"},
            fail_fast=False,
        )

        # Patch _run_single_check for lint to avoid needing ruff installed
        async def fake_check(check_name, command, project_root, timeout):
            return GateCheckResult(name=check_name, status="pass", output="ok")

        with patch(
            "the_architect.core.validation_gate._run_single_check",
            new=MagicMock(side_effect=lambda **kw: fake_check(**kw)),
        ):
            result = await run_validation_gate(tmp_path, cfg)

        assert result.passed is True
        assert len(result.checks) == 2
        names = [r.name for r in result.checks]
        assert "lint" in names
        assert "build" in names

    async def test_custom_command_fail_fast_stops(self, tmp_path: Path) -> None:
        """fail_fast stops at first failing custom command."""
        cfg = ValidationGateConfig(
            checks=["build", "security"],
            custom_commands={
                "build": "exit 1",
                "security": "echo secure",
            },
            fail_fast=True,
        )
        result = await run_validation_gate(tmp_path, cfg)
        assert result.passed is False
        assert len(result.checks) == 1
        assert result.checks[0].name == "build"
        assert result.checks[0].status == "fail"


# ============================================================================
# T03.1 — Cross-project type detection tests
# ============================================================================


class TestDetectProjectType:
    """_detect_project_type identifies project type from manifest files."""

    def test_python_detected(self, tmp_path: Path) -> None:
        """pyproject.toml present → python."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        assert _detect_project_type(tmp_path) == "python"

    def test_javascript_detected(self, tmp_path: Path) -> None:
        """package.json present (no pyproject.toml) → javascript."""
        (tmp_path / "package.json").write_text("{}")
        assert _detect_project_type(tmp_path) == "javascript"

    def test_go_detected(self, tmp_path: Path) -> None:
        """go.mod present (no pyproject.toml or package.json) → go."""
        (tmp_path / "go.mod").write_text("module example.com/foo\n")
        assert _detect_project_type(tmp_path) == "go"

    def test_rust_detected(self, tmp_path: Path) -> None:
        """Cargo.toml present (no other manifests) → rust."""
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        assert _detect_project_type(tmp_path) == "rust"

    def test_unknown_no_manifests(self, tmp_path: Path) -> None:
        """No recognized manifest files → unknown."""
        assert _detect_project_type(tmp_path) == "unknown"

    def test_unknown_unrecognized_manifest(self, tmp_path: Path) -> None:
        """Only unrecognized manifest files → unknown."""
        (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\n")
        assert _detect_project_type(tmp_path) == "unknown"

    def test_priority_python_over_javascript(self, tmp_path: Path) -> None:
        """pyproject.toml takes priority over package.json."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "package.json").write_text("{}")
        assert _detect_project_type(tmp_path) == "python"

    def test_priority_javascript_over_go(self, tmp_path: Path) -> None:
        """package.json takes priority over go.mod."""
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "go.mod").write_text("module example.com/foo\n")
        assert _detect_project_type(tmp_path) == "javascript"

    def test_priority_go_over_rust(self, tmp_path: Path) -> None:
        """go.mod takes priority over Cargo.toml."""
        (tmp_path / "go.mod").write_text("module example.com/foo\n")
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        assert _detect_project_type(tmp_path) == "go"

    def test_priority_all_manifests_present(self, tmp_path: Path) -> None:
        """When all manifests exist, python wins (highest priority)."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "go.mod").write_text("module example.com/foo\n")
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        assert _detect_project_type(tmp_path) == "python"


# ============================================================================
# T03.2 — Cross-project command discovery tests
# ============================================================================


class TestDiscoverCommandsCrossProject:
    """_discover_commands returns correct defaults per project type."""

    def test_python_defaults(self, tmp_path: Path) -> None:
        """Python project gets Python-specific defaults."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        commands = _discover_commands(tmp_path)
        assert commands == _PYTHON_DEFAULTS
        assert commands["lint"] == "ruff check ."
        assert commands["test"] == "pytest tests/ -q"
        assert commands["typecheck"] == "mypy the_architect/"

    def test_javascript_defaults(self, tmp_path: Path) -> None:
        """JavaScript project gets JS-specific defaults."""
        (tmp_path / "package.json").write_text("{}")
        commands = _discover_commands(tmp_path)
        assert commands == _JAVASCRIPT_DEFAULTS
        assert commands["lint"] == "npx eslint ."
        assert commands["test"] == "npm test"
        assert "typecheck" not in commands

    def test_go_defaults(self, tmp_path: Path) -> None:
        """Go project gets Go-specific defaults."""
        (tmp_path / "go.mod").write_text("module example.com/foo\n")
        commands = _discover_commands(tmp_path)
        assert commands == _GO_DEFAULTS
        assert commands["lint"] == "go vet ./..."
        assert commands["test"] == "go test ./..."
        assert "typecheck" not in commands

    def test_rust_defaults(self, tmp_path: Path) -> None:
        """Rust project gets Rust-specific defaults."""
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        commands = _discover_commands(tmp_path)
        assert commands == _RUST_DEFAULTS
        assert commands["lint"] == "cargo clippy"
        assert commands["test"] == "cargo test"
        assert "typecheck" not in commands

    def test_unknown_project_empty_commands(self, tmp_path: Path) -> None:
        """Unknown project type returns empty commands (gate disabled)."""
        commands = _discover_commands(tmp_path)
        assert commands == {}

    def test_custom_commands_override_javascript_defaults(self, tmp_path: Path) -> None:
        """Custom commands override JavaScript defaults."""
        (tmp_path / "package.json").write_text("{}")
        commands = _discover_commands(
            tmp_path,
            custom_commands={"lint": "eslint --config .eslintrc.json ."},
        )
        assert commands["lint"] == "eslint --config .eslintrc.json ."
        assert commands["test"] == "npm test"

    def test_custom_commands_override_go_defaults(self, tmp_path: Path) -> None:
        """Custom commands override Go defaults."""
        (tmp_path / "go.mod").write_text("module example.com/foo\n")
        commands = _discover_commands(
            tmp_path,
            custom_commands={"test": "go test -race ./..."},
        )
        assert commands["lint"] == "go vet ./..."
        assert commands["test"] == "go test -race ./..."

    def test_custom_commands_override_rust_defaults(self, tmp_path: Path) -> None:
        """Custom commands override Rust defaults."""
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        commands = _discover_commands(
            tmp_path,
            custom_commands={
                "lint": "cargo clippy --all-targets",
                "test": "cargo test --all-features",
            },
        )
        assert commands["lint"] == "cargo clippy --all-targets"
        assert commands["test"] == "cargo test --all-features"

    def test_custom_commands_on_unknown_project(self, tmp_path: Path) -> None:
        """Custom commands work on unknown project type (empty base)."""
        commands = _discover_commands(
            tmp_path,
            custom_commands={"check": "custom-check"},
        )
        assert commands == {"check": "custom-check"}

    def test_javascript_with_custom_typecheck(self, tmp_path: Path) -> None:
        """JavaScript project can add typecheck via custom commands."""
        (tmp_path / "package.json").write_text("{}")
        commands = _discover_commands(
            tmp_path,
            custom_commands={"typecheck": "tsc --noEmit"},
        )
        assert commands["lint"] == "npx eslint ."
        assert commands["test"] == "npm test"
        assert commands["typecheck"] == "tsc --noEmit"

    def test_go_with_custom_typecheck(self, tmp_path: Path) -> None:
        """Go project can add typecheck via custom commands."""
        (tmp_path / "go.mod").write_text("module example.com/foo\n")
        commands = _discover_commands(
            tmp_path,
            custom_commands={"typecheck": "staticcheck ./..."},
        )
        assert commands["lint"] == "go vet ./..."
        assert commands["test"] == "go test ./..."
        assert commands["typecheck"] == "staticcheck ./..."

    def test_rust_with_custom_build(self, tmp_path: Path) -> None:
        """Rust project can add build check via custom commands."""
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        commands = _discover_commands(
            tmp_path,
            custom_commands={"build": "cargo build --release"},
        )
        assert commands["lint"] == "cargo clippy"
        assert commands["test"] == "cargo test"
        assert commands["build"] == "cargo build --release"


# ============================================================================
# T03.3 — Cross-project gate execution tests
# ============================================================================


class TestValidationGateCrossProjectExecution:
    """Validation gate executes correct commands per project type."""

    async def test_gate_runs_javascript_commands(self, tmp_path: Path) -> None:
        """Gate runs JS commands when package.json is present."""
        (tmp_path / "package.json").write_text("{}")
        cfg = ValidationGateConfig(
            checks=["lint"],
            custom_commands={"lint": "echo js_lint_ok"},
            fail_fast=False,
        )
        result = await run_validation_gate(tmp_path, cfg)
        assert result.passed is True
        assert len(result.checks) == 1
        assert result.checks[0].name == "lint"
        assert result.checks[0].status == "pass"

    async def test_gate_runs_go_commands(self, tmp_path: Path) -> None:
        """Gate runs Go commands when go.mod is present."""
        (tmp_path / "go.mod").write_text("module example.com/foo\n")
        cfg = ValidationGateConfig(
            checks=["test"],
            custom_commands={"test": "echo go_test_ok"},
            fail_fast=False,
        )
        result = await run_validation_gate(tmp_path, cfg)
        assert result.passed is True
        assert len(result.checks) == 1
        assert result.checks[0].name == "test"
        assert result.checks[0].status == "pass"

    async def test_gate_runs_rust_commands(self, tmp_path: Path) -> None:
        """Gate runs Rust commands when Cargo.toml is present."""
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        cfg = ValidationGateConfig(
            checks=["lint"],
            custom_commands={"lint": "echo rust_lint_ok"},
            fail_fast=False,
        )
        result = await run_validation_gate(tmp_path, cfg)
        assert result.passed is True
        assert len(result.checks) == 1
        assert result.checks[0].name == "lint"
        assert result.checks[0].status == "pass"

    async def test_gate_unknown_project_no_checks_run(self, tmp_path: Path) -> None:
        """Gate runs no checks on unknown project type (empty defaults)."""
        cfg = ValidationGateConfig(
            checks=["lint", "test", "typecheck"],
            fail_fast=False,
        )
        result = await run_validation_gate(tmp_path, cfg)
        # No commands available for unknown project → all checks skipped
        assert result.checks == []
        assert result.passed is True

    async def test_gate_unknown_project_custom_commands_work(self, tmp_path: Path) -> None:
        """Custom commands enable checks on unknown project type."""
        cfg = ValidationGateConfig(
            checks=["check"],
            custom_commands={"check": "echo custom_check_ok"},
            fail_fast=False,
        )
        result = await run_validation_gate(tmp_path, cfg)
        assert result.passed is True
        assert len(result.checks) == 1
        assert result.checks[0].name == "check"
        assert result.checks[0].status == "pass"

    async def test_gate_python_priority_over_javascript(self, tmp_path: Path) -> None:
        """When both pyproject.toml and package.json exist, Python defaults apply."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "package.json").write_text("{}")
        cfg = ValidationGateConfig(
            checks=["lint"],
            custom_commands={"lint": "echo lint_ok"},
            fail_fast=False,
        )
        result = await run_validation_gate(tmp_path, cfg)
        assert result.passed is True
        assert len(result.checks) == 1

    async def test_gate_multiple_checks_javascript(self, tmp_path: Path) -> None:
        """Multiple checks run on JavaScript project."""
        (tmp_path / "package.json").write_text("{}")
        cfg = ValidationGateConfig(
            checks=["lint", "test"],
            custom_commands={
                "lint": "echo lint_ok",
                "test": "echo test_ok",
            },
            fail_fast=False,
        )
        result = await run_validation_gate(tmp_path, cfg)
        assert result.passed is True
        assert len(result.checks) == 2
        names = [r.name for r in result.checks]
        assert names == ["lint", "test"]

    async def test_gate_multiple_checks_rust(self, tmp_path: Path) -> None:
        """Multiple checks run on Rust project."""
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        cfg = ValidationGateConfig(
            checks=["lint", "test"],
            custom_commands={
                "lint": "echo clippy_ok",
                "test": "echo cargo_test_ok",
            },
            fail_fast=False,
        )
        result = await run_validation_gate(tmp_path, cfg)
        assert result.passed is True
        assert len(result.checks) == 2
        names = [r.name for r in result.checks]
        assert names == ["lint", "test"]


# ============================================================================
# T03.5 — Fix-up instruction builder tests
# ============================================================================


class TestBuildFixupInstruction:
    """_build_fixup_instruction builds focused fix-up instructions."""

    def test_basic_structure(self) -> None:
        from the_architect.core.validation_gate import _build_fixup_instruction

        gate_result = ValidationGateResult(
            passed=False,
            checks=[
                GateCheckResult(
                    name="lint", status="fail", output="E501 line too long", duration=1.2
                ),
            ],
        )
        instruction = _build_fixup_instruction(gate_result, "T04")

        assert "VALIDATION GATE FIX-UP for T04" in instruction
        assert "[FAIL] lint" in instruction
        assert "took 1.2s" in instruction
        assert "E501 line too long" in instruction
        assert "INSTRUCTIONS:" in instruction

    def test_multiple_failed_checks(self) -> None:
        from the_architect.core.validation_gate import _build_fixup_instruction

        gate_result = ValidationGateResult(
            passed=False,
            checks=[
                GateCheckResult(name="lint", status="fail", output="lint error", duration=1.0),
                GateCheckResult(name="test", status="fail", output="test error", duration=2.0),
            ],
        )
        instruction = _build_fixup_instruction(gate_result, "T01")

        assert "[FAIL] lint" in instruction
        assert "[FAIL] test" in instruction

    def test_error_status(self) -> None:
        from the_architect.core.validation_gate import _build_fixup_instruction

        gate_result = ValidationGateResult(
            passed=False,
            checks=[
                GateCheckResult(
                    name="typecheck",
                    status="error",
                    output="Command not found: mypy",
                    duration=0.1,
                ),
            ],
        )
        instruction = _build_fixup_instruction(gate_result, "T02")

        assert "[ERROR] typecheck" in instruction

    def test_pass_checks_excluded(self) -> None:
        from the_architect.core.validation_gate import _build_fixup_instruction

        gate_result = ValidationGateResult(
            passed=False,
            checks=[
                GateCheckResult(name="lint", status="pass", output="clean", duration=0.5),
                GateCheckResult(name="test", status="fail", output="test error", duration=2.0),
            ],
        )
        instruction = _build_fixup_instruction(gate_result, "T03")

        # Pass checks should not appear as [PASS] entries
        assert "[PASS]" not in instruction
        assert "[FAIL] test" in instruction
        # Exactly one failed check in the instruction
        assert instruction.count("[FAIL]") == 1

    def test_output_truncated(self) -> None:
        from the_architect.core.validation_gate import _build_fixup_instruction

        long_output = "x" * 500
        gate_result = ValidationGateResult(
            passed=False,
            checks=[
                GateCheckResult(
                    name="test",
                    status="fail",
                    output=long_output,
                    duration=1.0,
                ),
            ],
        )
        instruction = _build_fixup_instruction(gate_result, "T05")

        # The output snippet should be truncated to last 200 chars
        assert "x" * 200 in instruction
        assert len(instruction) < 4000

    def test_instruction_under_4kb(self) -> None:
        from the_architect.core.validation_gate import _build_fixup_instruction

        """Fix-up instruction must stay under 4KB to avoid E2BIG."""
        gate_result = ValidationGateResult(
            passed=False,
            checks=[
                GateCheckResult(
                    name="lint",
                    status="fail",
                    output="a" * 300,
                    duration=1.0,
                ),
                GateCheckResult(
                    name="test",
                    status="fail",
                    output="b" * 300,
                    duration=2.0,
                ),
                GateCheckResult(
                    name="typecheck",
                    status="error",
                    output="c" * 300,
                    duration=0.5,
                ),
            ],
        )
        instruction = _build_fixup_instruction(gate_result, "T99")
        assert len(instruction.encode("utf-8")) < 4096


# ============================================================================
# T03.6 — Fix-up gate runner tests
# ============================================================================


class TestRunValidationGateWithFixup:
    """run_validation_gate_with_fixup handles fix-up flow correctly."""

    async def test_passes_without_fixup(self, tmp_path: Path) -> None:
        """When gate passes initially, no fix-up is attempted."""
        from the_architect.core.validation_gate import (
            run_validation_gate_with_fixup,
        )

        cfg = ValidationGateConfig(
            checks=["lint"],
            custom_commands={"lint": "echo ok"},
            fixup_attempts=2,
        )
        result = await run_validation_gate_with_fixup(
            project_root=tmp_path,
            config=cfg,
        )
        assert result.passed is True

    async def test_no_fixup_when_zero_attempts(self, tmp_path: Path) -> None:
        """When fixup_attempts=0, failed gate returns immediately."""
        from the_architect.core.validation_gate import (
            run_validation_gate_with_fixup,
        )

        cfg = ValidationGateConfig(
            checks=["lint"],
            custom_commands={"lint": "exit 1"},
            fixup_attempts=0,
        )
        result = await run_validation_gate_with_fixup(
            project_root=tmp_path,
            config=cfg,
        )
        assert result.passed is False

    async def test_no_fixup_when_no_provider(self, tmp_path: Path) -> None:
        """When provider is None and gate fails, returns failed result."""
        from the_architect.core.validation_gate import (
            run_validation_gate_with_fixup,
        )

        cfg = ValidationGateConfig(
            checks=["lint"],
            custom_commands={"lint": "exit 1"},
            fixup_attempts=2,
        )
        result = await run_validation_gate_with_fixup(
            project_root=tmp_path,
            config=cfg,
            provider=None,
        )
        assert result.passed is False

    async def test_fixup_succeeds_on_first_attempt(self, tmp_path: Path) -> None:
        """Fix-up can turn a failing gate into a passing one."""
        from the_architect.core.validation_gate import (
            run_validation_gate_with_fixup,
        )

        # Use a marker file to simulate fix-up success
        marker = tmp_path / ".fixup_done"

        # First the lint command fails, then after fix-up it passes
        fail_cmd = f"test ! -f {marker} && exit 1 || echo ok"

        cfg = ValidationGateConfig(
            checks=["lint"],
            custom_commands={"lint": fail_cmd},
            fixup_attempts=2,
        )

        # Mock the stream_provider to create the marker file
        async def mock_stream_provider(*_args, **_kwargs):
            from the_architect.core.runner import StreamResult, TokenUsage

            marker.touch()
            return StreamResult(
                exit_code=0,
                tokens=TokenUsage(),
                accumulated_text="Fixed the lint error",
            )

        # stream_provider is imported inside the function, so patch at
        # the runner module level where the import resolves
        with patch(
            "the_architect.core.runner.stream_provider",
            new=mock_stream_provider,
        ):
            # Need a mock provider object
            mock_provider = MagicMock()
            mock_provider.supports_agents.return_value = False

            result = await run_validation_gate_with_fixup(
                project_root=tmp_path,
                config=cfg,
                provider=mock_provider,
                task_prefix="T01",
            )

        assert result.passed is True

    async def test_fixup_exhausted_attempts(self, tmp_path: Path) -> None:
        """After all fix-up attempts fail, gate returns failed."""
        from the_architect.core.validation_gate import (
            run_validation_gate_with_fixup,
        )

        cfg = ValidationGateConfig(
            checks=["lint"],
            custom_commands={"lint": "exit 1"},
            fixup_attempts=1,
        )

        async def mock_stream_provider(*_args, **_kwargs):
            from the_architect.core.runner import StreamResult, TokenUsage

            return StreamResult(
                exit_code=1,
                tokens=TokenUsage(),
                accumulated_text="Could not fix",
            )

        # stream_provider is imported inside the function, so patch at
        # the runner module level where the import resolves
        with patch(
            "the_architect.core.runner.stream_provider",
            new=mock_stream_provider,
        ):
            mock_provider = MagicMock()
            mock_provider.supports_agents.return_value = False

            result = await run_validation_gate_with_fixup(
                project_root=tmp_path,
                config=cfg,
                provider=mock_provider,
                task_prefix="T02",
            )

        assert result.passed is False


# ============================================================================
# T03.7 — Config validation_gate_config with fixup_attempts
# ============================================================================


class TestValidationGateConfigPropertyFixup:
    """ArchitectConfig.validation_gate_config includes fixup_attempts."""

    def test_default_fixup_attempts(self, tmp_path: Path) -> None:
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig().resolve(tmp_path)
        gate_config = config.validation_gate_config
        assert gate_config.fixup_attempts == 2

    def test_custom_fixup_attempts(self, tmp_path: Path) -> None:
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig(
            validation_gate={
                "fixup_attempts": 5,
            },
        ).resolve(tmp_path)
        gate_config = config.validation_gate_config
        assert gate_config.fixup_attempts == 5

    def test_fixup_attempts_zero(self, tmp_path: Path) -> None:
        from the_architect.config import ArchitectConfig

        config = ArchitectConfig(
            validation_gate={
                "fixup_attempts": 0,
            },
        ).resolve(tmp_path)
        gate_config = config.validation_gate_config
        assert gate_config.fixup_attempts == 0
