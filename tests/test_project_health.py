"""Tests for the project-level health checks module."""

from __future__ import annotations

import json
from pathlib import Path

from the_architect.core.project_health import (
    HealthCheck,
    check_baselines,
    check_circuit_state,
    check_lock_file,
    check_presets,
    check_task_consistency,
    check_token_ledger,
    run_project_checks,
)

# ---------------------------------------------------------------------------
# HealthCheck model
# ---------------------------------------------------------------------------


class TestHealthCheckModel:
    """Tests for the HealthCheck dataclass."""

    def test_healthcheck_fields(self) -> None:
        """HealthCheck should have status, label, and detail."""
        check = HealthCheck(status="ok", label="Test", detail="All good")
        assert check.status == "ok"
        assert check.label == "Test"
        assert check.detail == "All good"

    def test_healthcheck_warn_status(self) -> None:
        """HealthCheck supports warn status."""
        check = HealthCheck(status="warn", label="Test", detail="Minor issue")
        assert check.status == "warn"

    def test_healthcheck_fail_status(self) -> None:
        """HealthCheck supports fail status."""
        check = HealthCheck(status="fail", label="Test", detail="Critical issue")
        assert check.status == "fail"


# ---------------------------------------------------------------------------
# check_lock_file
# ---------------------------------------------------------------------------


class TestCheckLockFile:
    """Tests for check_lock_file()."""

    def test_no_lock_file(self, tmp_path: Path) -> None:
        """Missing lock file is ok."""
        result = check_lock_file(tmp_path)
        assert result.status == "ok"
        assert result.label == "Lock file"
        assert "No runner.lock" in result.detail

    def test_lock_file_exists(self, tmp_path: Path) -> None:
        """Existing lock file is a failure."""
        (tmp_path / ".architect").mkdir()
        (tmp_path / ".architect" / "runner.lock").write_text("locked")
        result = check_lock_file(tmp_path)
        assert result.status == "fail"
        assert "runner.lock exists" in result.detail


# ---------------------------------------------------------------------------
# check_task_consistency
# ---------------------------------------------------------------------------


class TestCheckTaskConsistency:
    """Tests for check_task_consistency()."""

    def test_no_tasks_dir(self, tmp_path: Path) -> None:
        """Missing tasks/ is ok."""
        result = check_task_consistency(tmp_path)
        assert result.status == "ok"
        assert "No tasks/" in result.detail

    def test_no_task_files(self, tmp_path: Path) -> None:
        """Empty tasks/ with PROGRESS.md is ok."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "PROGRESS.md").write_text("some content")
        result = check_task_consistency(tmp_path)
        assert result.status == "ok"
        assert "No task files" in result.detail

    def test_consistent_tasks(self, tmp_path: Path) -> None:
        """Task files matching PROGRESS.md rows is ok."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_foo.md").write_text("# T01")
        (tasks_dir / "T02_bar.md").write_text("# T02")
        progress = "| T01 | Foo | Done | 2026-01-01 |\n| T02 | Bar | Pending | — |\n"
        (tasks_dir / "PROGRESS.md").write_text(progress, encoding="utf-8")
        result = check_task_consistency(tmp_path)
        assert result.status == "ok"
        assert "2 task file(s)" in result.detail

    def test_file_missing_from_progress(self, tmp_path: Path) -> None:
        """Task file without PROGRESS.md row is a warning."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_foo.md").write_text("# T01")
        (tasks_dir / "T02_bar.md").write_text("# T02")
        progress = "| T01 | Foo | Done | 2026-01-01 |\n"
        (tasks_dir / "PROGRESS.md").write_text(progress)
        result = check_task_consistency(tmp_path)
        assert result.status == "warn"
        assert "missing from PROGRESS.md" in result.detail

    def test_row_without_file(self, tmp_path: Path) -> None:
        """PROGRESS.md row without task file is a warning."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_foo.md").write_text("# T01")
        progress = "| T01 | Foo | Done | 2026-01-01 |\n| T02 | Bar | Pending | — |\n"
        (tasks_dir / "PROGRESS.md").write_text(progress, encoding="utf-8")
        result = check_task_consistency(tmp_path)
        assert result.status == "warn"
        assert "without task file" in result.detail


# ---------------------------------------------------------------------------
# check_baselines
# ---------------------------------------------------------------------------


class TestCheckBaselines:
    """Tests for check_baselines()."""

    def test_no_baselines_dir(self, tmp_path: Path) -> None:
        """Missing baselines directory is ok."""
        result = check_baselines(tmp_path)
        assert result.status == "ok"
        assert "No .architect/baselines/" in result.detail

    def test_empty_baselines_dir(self, tmp_path: Path) -> None:
        """Empty baselines directory is ok."""
        (tmp_path / ".architect" / "baselines").mkdir(parents=True)
        result = check_baselines(tmp_path)
        assert result.status == "ok"
        assert "No baseline files" in result.detail

    def test_valid_baselines(self, tmp_path: Path) -> None:
        """Valid baseline files are ok."""
        baselines_dir = tmp_path / ".architect" / "baselines"
        baselines_dir.mkdir(parents=True)
        (baselines_dir / "T01.json").write_text(json.dumps({"files": {}}))
        (baselines_dir / "T02.json").write_text(json.dumps({"files": {}}))
        result = check_baselines(tmp_path)
        assert result.status == "ok"
        assert "2 valid" in result.detail

    def test_corrupted_baselines(self, tmp_path: Path) -> None:
        """Corrupted baseline files produce a warning."""
        baselines_dir = tmp_path / ".architect" / "baselines"
        baselines_dir.mkdir(parents=True)
        (baselines_dir / "T01.json").write_text(json.dumps({"files": {}}))
        (baselines_dir / "T02.json").write_text("not-json{{{")
        result = check_baselines(tmp_path)
        assert result.status == "warn"
        assert "1 valid" in result.detail
        assert "1 corrupted" in result.detail

    def test_non_dict_baseline(self, tmp_path: Path) -> None:
        """Baseline that is a JSON array (not dict) is corrupted."""
        baselines_dir = tmp_path / ".architect" / "baselines"
        baselines_dir.mkdir(parents=True)
        (baselines_dir / "T01.json").write_text(json.dumps([1, 2, 3]))
        result = check_baselines(tmp_path)
        assert result.status == "warn"
        assert "corrupted" in result.detail


# ---------------------------------------------------------------------------
# check_circuit_state
# ---------------------------------------------------------------------------


class TestCheckCircuitState:
    """Tests for check_circuit_state()."""

    def test_no_circuit_file(self, tmp_path: Path) -> None:
        """Missing circuit.json is ok."""
        result = check_circuit_state(tmp_path)
        assert result.status == "ok"
        assert "No circuit.json" in result.detail

    def test_all_closed(self, tmp_path: Path) -> None:
        """All CLOSED circuits are ok."""
        (tmp_path / ".architect").mkdir()
        data = {"T01": {"state": "CLOSED"}, "T02": {"state": "CLOSED"}}
        (tmp_path / ".architect" / "circuit.json").write_text(json.dumps(data))
        result = check_circuit_state(tmp_path)
        assert result.status == "ok"
        assert "all CLOSED" in result.detail

    def test_open_circuit(self, tmp_path: Path) -> None:
        """OPEN circuit produces a warning."""
        (tmp_path / ".architect").mkdir()
        data = {"T01": {"state": "CLOSED"}, "T02": {"state": "OPEN"}}
        (tmp_path / ".architect" / "circuit.json").write_text(json.dumps(data))
        result = check_circuit_state(tmp_path)
        assert result.status == "warn"
        assert "1 OPEN" in result.detail

    def test_half_open_circuit(self, tmp_path: Path) -> None:
        """HALF_OPEN circuit produces a warning."""
        (tmp_path / ".architect").mkdir()
        data = {"T01": {"state": "HALF_OPEN"}}
        (tmp_path / ".architect" / "circuit.json").write_text(json.dumps(data))
        result = check_circuit_state(tmp_path)
        assert result.status == "warn"
        assert "1 HALF_OPEN" in result.detail

    def test_corrupted_circuit_file(self, tmp_path: Path) -> None:
        """Corrupted circuit.json produces a warning."""
        (tmp_path / ".architect").mkdir()
        (tmp_path / ".architect" / "circuit.json").write_text("not-json{{{")
        result = check_circuit_state(tmp_path)
        assert result.status == "warn"
        assert "Cannot read" in result.detail


# ---------------------------------------------------------------------------
# check_token_ledger
# ---------------------------------------------------------------------------


class TestCheckTokenLedger:
    """Tests for check_token_ledger()."""

    def test_no_ledger_file(self, tmp_path: Path) -> None:
        """Missing ledger file is ok."""
        result = check_token_ledger(tmp_path)
        assert result.status == "ok"
        assert "No token_ledger.json" in result.detail

    def test_valid_ledger(self, tmp_path: Path) -> None:
        """Valid ledger is ok with record count."""
        (tmp_path / ".architect").mkdir()
        records = [{"run_id": "a", "timestamp": "2026-01-01T00:00:00"}]
        (tmp_path / ".architect" / "token_ledger.json").write_text(json.dumps(records))
        result = check_token_ledger(tmp_path)
        assert result.status == "ok"
        assert "1 run record" in result.detail

    def test_corrupted_ledger(self, tmp_path: Path) -> None:
        """Corrupted ledger produces a warning."""
        (tmp_path / ".architect").mkdir()
        (tmp_path / ".architect" / "token_ledger.json").write_text("not-json{{{")
        result = check_token_ledger(tmp_path)
        assert result.status == "warn"
        assert "Corrupted" in result.detail

    def test_non_array_ledger(self, tmp_path: Path) -> None:
        """Ledger that is a JSON object (not array) is a warning."""
        (tmp_path / ".architect").mkdir()
        (tmp_path / ".architect" / "token_ledger.json").write_text(json.dumps({"key": "val"}))
        result = check_token_ledger(tmp_path)
        assert result.status == "warn"
        assert "unexpected format" in result.detail


# ---------------------------------------------------------------------------
# check_presets
# ---------------------------------------------------------------------------


class TestCheckPresets:
    """Tests for check_presets()."""

    def test_no_presets_file(self, tmp_path: Path) -> None:
        """Missing presets file is ok."""
        result = check_presets(tmp_path)
        assert result.status == "ok"
        assert "No presets.json" in result.detail

    def test_valid_presets(self, tmp_path: Path) -> None:
        """Valid presets are ok with count."""
        (tmp_path / ".architect").mkdir()
        presets = [
            {"name": "sprint", "description": "fast work"},
            {"name": "deep", "description": "deep work"},
        ]
        (tmp_path / ".architect" / "presets.json").write_text(json.dumps(presets))
        result = check_presets(tmp_path)
        assert result.status == "ok"
        assert "2 valid" in result.detail

    def test_invalid_preset_entry(self, tmp_path: Path) -> None:
        """Preset entry without name is invalid."""
        (tmp_path / ".architect").mkdir()
        presets = [
            {"name": "sprint", "description": "fast work"},
            {"description": "missing name"},
        ]
        (tmp_path / ".architect" / "presets.json").write_text(json.dumps(presets))
        result = check_presets(tmp_path)
        assert result.status == "warn"
        assert "1 valid" in result.detail
        assert "1 invalid" in result.detail

    def test_corrupted_presets(self, tmp_path: Path) -> None:
        """Corrupted presets file produces a warning."""
        (tmp_path / ".architect").mkdir()
        (tmp_path / ".architect" / "presets.json").write_text("not-json{{{")
        result = check_presets(tmp_path)
        assert result.status == "warn"
        assert "Corrupted" in result.detail


# ---------------------------------------------------------------------------
# run_project_checks
# ---------------------------------------------------------------------------


class TestRunProjectChecks:
    """Tests for run_project_checks()."""

    def test_returns_all_checks(self, tmp_path: Path) -> None:
        """run_project_checks returns exactly 6 checks."""
        checks = run_project_checks(tmp_path)
        assert len(checks) == 6

    def test_check_order(self, tmp_path: Path) -> None:
        """Checks are returned in deterministic order."""
        checks = run_project_checks(tmp_path)
        labels = [c.label for c in checks]
        assert labels == [
            "Lock file",
            "Task consistency",
            "Baselines",
            "Circuit state",
            "Token ledger",
            "Presets",
        ]

    def test_all_ok_on_clean_project(self, tmp_path: Path) -> None:
        """A clean project with no .architect/ has all ok statuses."""
        checks = run_project_checks(tmp_path)
        statuses = [c.status for c in checks]
        assert all(s == "ok" for s in statuses)

    def test_mixed_statuses(self, tmp_path: Path) -> None:
        """A project with issues shows mixed statuses."""
        # Create lock file (fail) and OPEN circuit (warn)
        (tmp_path / ".architect").mkdir()
        (tmp_path / ".architect" / "runner.lock").write_text("locked")
        data = {"T01": {"state": "OPEN"}}
        (tmp_path / ".architect" / "circuit.json").write_text(json.dumps(data))
        checks = run_project_checks(tmp_path)
        statuses = [c.status for c in checks]
        assert "fail" in statuses
        assert "warn" in statuses
        assert "ok" in statuses
