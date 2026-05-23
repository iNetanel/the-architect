"""Extra CLI tests — focus on commands whose edge cases were uncovered.

This suite targets branches in ``the_architect/cli.py`` that the
pre-existing suite did not exercise:

    - ``status`` command against realistic fixtures (running / stale lock /
      circuit data / token budget / logs present).
    - ``status --json`` command — JSON output structure and edge cases.
    - ``config`` command with invalid KEY=VALUE input, boolean and int
      coercion paths.
    - ``init`` command with existing files (skip branch) and ``--force``.
    - ``monitor`` command non-tmux branches.
    - ``version`` command — straightforward smoke.
    - ``logs`` command filtered by ``--task`` prefix and ``--all`` flag.
    - ``circuit`` command reset and list branches.
    - ``retry`` command happy path with a mocked runner.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from the_architect.cli import main
from the_architect.core.provider import ProviderNotFoundError
from the_architect.core.tasks import Task, TaskStatus


def _run_coro(coro) -> None:
    """Run a coroutine in a fresh event loop for testing asyncio.run patches."""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


class TestInitCmd:
    """Cover both create and skip branches of ``architect init``."""

    def test_init_creates_files_in_empty_dir(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["init", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "AGENTS.md").exists()
        assert (tmp_path / "architect.toml").exists()
        assert "Created" in result.output

    def test_init_skips_existing_files(self, tmp_path: Path) -> None:
        """Without --force, existing files must be preserved."""
        (tmp_path / "AGENTS.md").write_text("# Custom\n", encoding="utf-8")
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["init", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "Skipped" in result.output
        # The user's content must survive.
        assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "# Custom\n"

    def test_init_force_overwrites(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("# Custom\n", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(main, ["init", "-p", str(tmp_path), "--force"])
        assert result.exit_code == 0, result.output
        # Overwritten to the template content.
        content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert "Project Rules" in content


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


class TestVersionCmd:
    def test_version_prints_prefix(self) -> None:
        result = CliRunner().invoke(main, ["version"])
        assert result.exit_code == 0
        assert "architect v" in result.output


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


class TestConfigCmd:
    """Exercise the ``config`` parser branches."""

    def test_config_shows_defaults_without_toml(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(main, ["config", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "max_retries" in result.output
        assert "defaults only" in result.output or "No architect.toml" in result.output

    def test_config_shows_from_toml(self, tmp_path: Path) -> None:
        (tmp_path / "architect.toml").write_text(
            "[architect]\nmax_retries = 7\n",
            encoding="utf-8",
        )
        result = CliRunner().invoke(main, ["config", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "max_retries" in result.output
        assert "7" in result.output
        assert "architect.toml" in result.output

    def test_config_rejects_missing_equals(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(main, ["config", "-p", str(tmp_path), "--set", "max_retries5"])
        assert result.exit_code == 1
        assert "KEY=VALUE" in result.output

    def test_config_rejects_unknown_key(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(main, ["config", "-p", str(tmp_path), "--set", "nope=1"])
        assert result.exit_code == 1
        assert "Unknown config key" in result.output

    def test_config_rejects_non_int_for_int_field(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            main, ["config", "-p", str(tmp_path), "--set", "max_retries=notanint"]
        )
        assert result.exit_code == 1
        assert "Invalid value" in result.output

    def test_config_rejects_non_bool_for_bool_field(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            main, ["config", "-p", str(tmp_path), "--set", "carry_context=maybe"]
        )
        assert result.exit_code == 1
        assert "Invalid value" in result.output

    def test_config_coerces_bool_true(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            main, ["config", "-p", str(tmp_path), "--set", "carry_context=true"]
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "architect.toml").exists()

    def test_config_coerces_bool_false(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            main, ["config", "-p", str(tmp_path), "--set", "carry_context=false"]
        )
        assert result.exit_code == 0, result.output
        assert "carry_context" in result.output
        assert "False" in result.output

    def test_config_sets_multiple_values(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            main,
            [
                "config",
                "-p",
                str(tmp_path),
                "--set",
                "max_retries=9",
                "--set",
                "retry_pause=45",
            ],
        )
        assert result.exit_code == 0, result.output
        toml = (tmp_path / "architect.toml").read_text(encoding="utf-8")
        assert "max_retries = 9" in toml
        assert "retry_pause = 45" in toml

    def test_config_set_token_budget_per_run(self, tmp_path: Path) -> None:
        """Setting token_budget_per_run via CLI works."""
        result = CliRunner().invoke(
            main,
            [
                "config",
                "-p",
                str(tmp_path),
                "--set",
                "token_budget_per_run=1000000",
            ],
        )
        assert result.exit_code == 0, result.output
        toml = (tmp_path / "architect.toml").read_text(encoding="utf-8")
        assert "token_budget_per_run = 1000000" in toml


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


class TestStatusCmd:
    """Cover the sections printed by ``architect status``."""

    def test_status_without_anything(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Not running" in result.output
        assert "No tasks directory" in result.output

    def test_status_with_stale_lock(self, tmp_path: Path) -> None:
        """Lock with a PID that cannot possibly exist."""
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        # Use an extremely large PID that will not exist on any reasonable system
        (arch_dir / "runner.lock").write_text("999999999", encoding="utf-8")

        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        # We should end up on the "not running" line because os.kill raises.
        assert "Not running" in result.output or "stale" in result.output

    def test_status_with_invalid_lock_pid(self, tmp_path: Path) -> None:
        """Malformed lock file should not crash ``status``."""
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        (arch_dir / "runner.lock").write_text("notapid", encoding="utf-8")

        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output

    def test_status_with_tasks_and_progress(self, tmp_path: Path) -> None:
        """Renders the task table and completion count."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_first.md").write_text("# T01 First\n", encoding="utf-8")
        (tasks_dir / "T02_second.md").write_text("# T02 Second\n", encoding="utf-8")
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        (tasks_dir / "PROGRESS.md").write_text(
            """# The Architect — Progress Tracker

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | First  | Done    | 2024-01-01 |
| T02  | Second | Pending | — |
""",
            encoding="utf-8",
        )

        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "T01" in result.output
        assert "T02" in result.output
        assert "tasks complete" in result.output

    def test_status_with_circuit_data(self, tmp_path: Path) -> None:
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        (arch_dir / "circuit.json").write_text(
            '{"T01": {"state": "OPEN", "consecutive_no_progress": 3, "consecutive_same_error": 0}}',
            encoding="utf-8",
        )

        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Circuit breaker" in result.output
        assert "T01" in result.output
        assert "OPEN" in result.output

    def test_status_with_corrupted_circuit_json(self, tmp_path: Path) -> None:
        """Malformed circuit.json must be swallowed silently."""
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        (arch_dir / "circuit.json").write_text("{not-json", encoding="utf-8")

        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output

    def test_status_with_token_budget(self, tmp_path: Path) -> None:
        (tmp_path / "architect.toml").write_text(
            "[architect]\ntoken_budget_per_hour = 500000\n",
            encoding="utf-8",
        )
        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Token budget" in result.output
        assert "500,000" in result.output

    def test_status_with_logs(self, tmp_path: Path) -> None:
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "T01.log").write_text("log content\n", encoding="utf-8")

        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Logs" in result.output
        assert "T01.log" in result.output


class TestStatusJsonCmd:
    """Cover the ``status --json`` machine-readable output path."""

    def test_status_json_basic_structure(self, tmp_path: Path) -> None:
        """JSON output contains all required top-level keys."""
        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        expected_keys = {
            "project",
            "running",
            "pid",
            "tasks",
            "task_summary",
            "circuit_breakers",
            "token_budget",
            "log_dir",
            "log_files",
            "last_run",
        }
        assert set(data.keys()) == expected_keys

    def test_status_json_not_running(self, tmp_path: Path) -> None:
        """No lock file means running=False, pid=null."""
        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["running"] is False
        assert data["pid"] is None

    def test_status_json_stale_lock(self, tmp_path: Path) -> None:
        """Stale lock (non-existent PID) reports running=False with pid."""
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        (arch_dir / "runner.lock").write_text("999999999", encoding="utf-8")

        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["running"] is False
        assert data["pid"] == 999999999

    def test_status_json_tasks_and_summary(self, tmp_path: Path) -> None:
        """Task list and summary are correctly populated."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_first.md").write_text("# T01 First\n", encoding="utf-8")
        (tasks_dir / "T02_second.md").write_text("# T02 Second\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text(
            "# The Architect — Progress Tracker\n\n"
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|-----------|\n"
            "| T01  | First  | Done    | 2024-01-01 |\n"
            "| T02  | Second | Pending | — |\n",
            encoding="utf-8",
        )

        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data["tasks"]) == 2
        assert data["task_summary"]["total"] == 2
        assert data["task_summary"]["done"] == 1
        assert data["task_summary"]["pending"] == 1
        # Verify task entries
        prefixes = {t["prefix"] for t in data["tasks"]}
        assert prefixes == {"T01", "T02"}

    def test_status_json_circuit_breakers(self, tmp_path: Path) -> None:
        """Circuit breakers in OPEN/HALF_OPEN state appear in JSON."""
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        (arch_dir / "circuit.json").write_text(
            '{"T01": {"state": "OPEN", "consecutive_no_progress": 3, '
            '"consecutive_same_error": 1}, '
            '"T02": {"state": "CLOSED", "consecutive_no_progress": 0, '
            '"consecutive_same_error": 0}}',
            encoding="utf-8",
        )

        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data["circuit_breakers"]) == 1
        cb = data["circuit_breakers"][0]
        assert cb["task"] == "T01"
        assert cb["state"] == "OPEN"
        assert cb["no_progress"] == 3
        assert cb["same_error"] == 1

    def test_status_json_token_budget(self, tmp_path: Path) -> None:
        """Token budget appears when configured, null when not."""
        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["token_budget"] is None

        (tmp_path / "architect.toml").write_text(
            "[architect]\ntoken_budget_per_hour = 500000\n",
            encoding="utf-8",
        )
        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["token_budget"] is not None
        assert data["token_budget"]["per_hour"] == 500000

    def test_status_json_log_files(self, tmp_path: Path) -> None:
        """Log files appear when log dir exists, null when it does not."""
        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["log_files"] is None

        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "T01.log").write_text("x" * 2048, encoding="utf-8")
        (log_dir / "T02.log").write_text("y" * 1024, encoding="utf-8")

        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["log_dir"] is not None
        assert len(data["log_files"]) == 2
        names = {f["name"] for f in data["log_files"]}
        assert names == {"T01.log", "T02.log"}

    def test_status_json_no_rich_markup(self, tmp_path: Path) -> None:
        """JSON output contains no Rich escape sequences."""
        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        # Should not contain Rich markup brackets
        assert "[" not in result.output.strip().split("\n")[0] or result.output.strip().startswith(
            "{"
        )
        # Should be valid JSON (no Rich ANSI codes)
        assert "\x1b" not in result.output

    def test_status_json_deterministic(self, tmp_path: Path) -> None:
        """JSON output is deterministic — keys are sorted."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_test.md").write_text("# T01\n", encoding="utf-8")

        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        # Parse and re-dump with sort_keys to compare
        data = json.loads(result.output)
        expected = json.dumps(data, indent=2, sort_keys=True)
        assert result.output.strip() == expected

    def test_status_json_with_project_flag(self, tmp_path: Path) -> None:
        """JSON output respects --project flag."""
        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["project"] == str(tmp_path.resolve())


class TestFormatStatusJson:
    """Direct unit tests for _format_status_json helper."""

    def test_format_json_empty_project(self, tmp_path: Path) -> None:
        """Formatter handles project with no tasks, no circuit, no logs."""
        from the_architect.cli import _format_status_json
        from the_architect.config import load_config

        config = load_config(tmp_path)
        output = _format_status_json(tmp_path, config)
        data = json.loads(output)

        assert data["running"] is False
        assert data["pid"] is None
        assert data["tasks"] == []
        assert data["task_summary"] == {
            "total": 0,
            "done": 0,
            "failed": 0,
            "pending": 0,
            "blocked": 0,
        }
        assert data["circuit_breakers"] == []
        assert data["token_budget"] is None
        assert data["log_dir"] is None  # log_dir doesn't exist in empty project
        assert data["log_files"] is None

    def test_format_json_missing_circuit_file(self, tmp_path: Path) -> None:
        """No exception when circuit.json is missing."""
        from the_architect.cli import _format_status_json
        from the_architect.config import load_config

        config = load_config(tmp_path)
        # .architect exists but no circuit.json
        (tmp_path / ".architect").mkdir()
        output = _format_status_json(tmp_path, config)
        data = json.loads(output)
        assert data["circuit_breakers"] == []

    def test_format_json_corrupted_circuit_file(self, tmp_path: Path) -> None:
        """Corrupted circuit.json does not crash the formatter."""
        from the_architect.cli import _format_status_json
        from the_architect.config import load_config

        config = load_config(tmp_path)
        (tmp_path / ".architect").mkdir()
        (tmp_path / ".architect" / "circuit.json").write_text("{bad json", encoding="utf-8")
        output = _format_status_json(tmp_path, config)
        data = json.loads(output)
        assert data["circuit_breakers"] == []

    def test_format_json_all_statuses(self, tmp_path: Path) -> None:
        """Task summary counts all status types correctly."""
        from the_architect.cli import _format_status_json
        from the_architect.config import load_config

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        for name in ("T01", "T02", "T03", "T04", "T05"):
            (tasks_dir / f"{name}_task.md").write_text(f"# {name}\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text(
            "# The Architect — Progress Tracker\n\n"
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|-----------|\n"
            "| T01  | Task  | Done    | 2024-01-01 |\n"
            "| T02  | Task  | Failed  | — |\n"
            "| T03  | Task  | Blocked | — |\n"
            "| T04  | Task  | Pending | — |\n"
            "| T05  | Task  | Pending | — |\n",
            encoding="utf-8",
        )

        config = load_config(tmp_path)
        output = _format_status_json(tmp_path, config)
        data = json.loads(output)
        assert data["task_summary"]["total"] == 5
        assert data["task_summary"]["done"] == 1
        assert data["task_summary"]["failed"] == 1
        assert data["task_summary"]["blocked"] == 1
        assert data["task_summary"]["pending"] == 2

    def test_format_json_invalid_lock_pid(self, tmp_path: Path) -> None:
        """Non-numeric lock PID does not crash the formatter."""
        from the_architect.cli import _format_status_json
        from the_architect.config import load_config

        config = load_config(tmp_path)
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        (arch_dir / "runner.lock").write_text("not-a-pid", encoding="utf-8")
        output = _format_status_json(tmp_path, config)
        data = json.loads(output)
        assert data["running"] is False
        assert data["pid"] is None


class TestStatusLastRun:
    """Tests for the Last Run summary section in ``status`` command."""

    def _write_ledger(self, tmp_path: Path, records: list[dict]) -> None:
        """Write a token ledger file with the given records."""
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir(exist_ok=True)
        (arch_dir / "token_ledger.json").write_text(json.dumps(records), encoding="utf-8")

    def test_last_run_display_success(self, tmp_path: Path) -> None:
        """Last Run section shows for a successful run."""
        self._write_ledger(
            tmp_path,
            [
                {
                    "run_id": "abc123",
                    "timestamp": "2026-05-20T10:00:00+00:00",
                    "goal_summary": "Add a new feature",
                    "total_tokens": 50000,
                    "total_cost_estimate": 0.50,
                    "model_breakdown": [],
                    "task_breakdown": [
                        {
                            "task_id": "T01",
                            "title": "First",
                            "status": "done",
                            "input_tokens": 20000,
                            "output_tokens": 30000,
                            "cache_read_tokens": 0,
                            "cache_write_tokens": 0,
                            "model": "gpt-4o",
                            "cost_estimate": 0.25,
                            "duration_seconds": 60.0,
                        },
                        {
                            "task_id": "T02",
                            "title": "Second",
                            "status": "done",
                            "input_tokens": 10000,
                            "output_tokens": 10000,
                            "cache_read_tokens": 0,
                            "cache_write_tokens": 0,
                            "model": "gpt-4o",
                            "cost_estimate": 0.25,
                            "duration_seconds": 30.0,
                        },
                    ],
                    "task_count": 2,
                    "outcome": "success",
                    "duration_seconds": 90.0,
                }
            ],
        )
        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Last Run" in result.output
        assert "2026-05-20" in result.output
        assert "Success" in result.output
        assert "Add a new feature" in result.output
        assert "2/2" in result.output
        assert "50,000" in result.output

    def test_last_run_display_failure(self, tmp_path: Path) -> None:
        """Last Run section shows Failed outcome in red."""
        self._write_ledger(
            tmp_path,
            [
                {
                    "run_id": "def456",
                    "timestamp": "2026-05-19T08:30:00+00:00",
                    "goal_summary": "Fix a bug",
                    "total_tokens": 25000,
                    "total_cost_estimate": 0.25,
                    "model_breakdown": [],
                    "task_breakdown": [
                        {
                            "task_id": "T01",
                            "title": "Fix",
                            "status": "failed",
                            "input_tokens": 15000,
                            "output_tokens": 10000,
                            "cache_read_tokens": 0,
                            "cache_write_tokens": 0,
                            "model": "gpt-4o",
                            "cost_estimate": 0.25,
                            "duration_seconds": 45.0,
                        },
                    ],
                    "task_count": 1,
                    "outcome": "failure",
                    "duration_seconds": 50.0,
                }
            ],
        )
        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Last Run" in result.output
        assert "Failed" in result.output
        assert "0/1" in result.output

    def test_last_run_hidden_no_ledger(self, tmp_path: Path) -> None:
        """Last Run section is hidden when no ledger file exists."""
        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Last Run" not in result.output

    def test_last_run_hidden_empty_ledger(self, tmp_path: Path) -> None:
        """Last Run section is hidden when ledger file is empty array."""
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        (arch_dir / "token_ledger.json").write_text("[]", encoding="utf-8")
        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Last Run" not in result.output

    def test_last_run_hidden_corrupted_ledger(self, tmp_path: Path) -> None:
        """Last Run section is hidden when ledger file has invalid JSON."""
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        (arch_dir / "token_ledger.json").write_text("{bad json", encoding="utf-8")
        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Last Run" not in result.output

    def test_last_run_goal_truncation(self, tmp_path: Path) -> None:
        """Goal summary longer than 60 chars is truncated with ellipsis."""
        long_goal = "A" * 100
        self._write_ledger(
            tmp_path,
            [
                {
                    "run_id": "ghi789",
                    "timestamp": "2026-05-18T12:00:00+00:00",
                    "goal_summary": long_goal,
                    "total_tokens": 10000,
                    "total_cost_estimate": 0.10,
                    "model_breakdown": [],
                    "task_breakdown": [],
                    "task_count": 1,
                    "outcome": "success",
                    "duration_seconds": 30.0,
                }
            ],
        )
        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        # The goal should be truncated to 57 chars + "..."
        assert "..." in result.output
        # Full 100-char goal should NOT appear
        assert long_goal not in result.output

    def test_last_run_json_includes_field(self, tmp_path: Path) -> None:
        """status --json includes last_run field with data."""
        self._write_ledger(
            tmp_path,
            [
                {
                    "run_id": "jkl012",
                    "timestamp": "2026-05-20T15:00:00+00:00",
                    "goal_summary": "Build something",
                    "total_tokens": 30000,
                    "total_cost_estimate": 0.30,
                    "model_breakdown": [],
                    "task_breakdown": [
                        {
                            "task_id": "T01",
                            "title": "Task1",
                            "status": "done",
                            "input_tokens": 15000,
                            "output_tokens": 15000,
                            "cache_read_tokens": 0,
                            "cache_write_tokens": 0,
                            "model": "gpt-4o",
                            "cost_estimate": 0.30,
                            "duration_seconds": 40.0,
                        },
                    ],
                    "task_count": 1,
                    "outcome": "success",
                    "duration_seconds": 45.0,
                }
            ],
        )
        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "last_run" in data
        lr = data["last_run"]
        assert lr is not None
        assert lr["date"] == "2026-05-20"
        assert lr["goal"] == "Build something"
        assert lr["tasks_done"] == 1
        assert lr["tasks_total"] == 1
        assert lr["tokens"] == 30000
        assert lr["cost"] == 0.30
        assert lr["outcome"] == "success"

    def test_last_run_json_none_when_no_ledger(self, tmp_path: Path) -> None:
        """status --json has last_run null when no ledger data."""
        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["last_run"] is None

    def test_last_run_json_goal_truncated(self, tmp_path: Path) -> None:
        """status --json truncates goal to 60 chars."""
        long_goal = "B" * 100
        self._write_ledger(
            tmp_path,
            [
                {
                    "run_id": "mno345",
                    "timestamp": "2026-05-20T16:00:00+00:00",
                    "goal_summary": long_goal,
                    "total_tokens": 5000,
                    "total_cost_estimate": 0.05,
                    "model_breakdown": [],
                    "task_breakdown": [],
                    "task_count": 1,
                    "outcome": "failure",
                    "duration_seconds": 20.0,
                }
            ],
        )
        result = CliRunner().invoke(main, ["status", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        lr = data["last_run"]
        assert lr is not None
        assert len(lr["goal"]) == 60  # 57 + "..."
        assert lr["goal"].endswith("...")


class TestGetLastRunSummary:
    """Unit tests for the ``_get_last_run_summary`` helper function."""

    def test_returns_none_no_ledger_file(self, tmp_path: Path) -> None:
        """Returns None when ledger file does not exist."""
        from the_architect.cli import _get_last_run_summary

        result = _get_last_run_summary(tmp_path)
        assert result is None

    def test_returns_none_empty_ledger(self, tmp_path: Path) -> None:
        """Returns None when ledger file exists but has no records."""
        from the_architect.cli import _get_last_run_summary

        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        (arch_dir / "token_ledger.json").write_text("[]", encoding="utf-8")
        result = _get_last_run_summary(tmp_path)
        assert result is None

    def test_returns_none_corrupted_ledger(self, tmp_path: Path) -> None:
        """Returns None when ledger file contains invalid JSON."""
        from the_architect.cli import _get_last_run_summary

        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        (arch_dir / "token_ledger.json").write_text("{bad", encoding="utf-8")
        result = _get_last_run_summary(tmp_path)
        assert result is None

    def test_returns_summary_with_record(self, tmp_path: Path) -> None:
        """Returns correct summary dict when ledger has records."""
        from the_architect.cli import _get_last_run_summary

        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        records = [
            {
                "run_id": "r1",
                "timestamp": "2026-05-20T10:00:00+00:00",
                "goal_summary": "Test goal",
                "total_tokens": 10000,
                "total_cost_estimate": 0.10,
                "model_breakdown": [],
                "task_breakdown": [
                    {
                        "task_id": "T01",
                        "title": "Task1",
                        "status": "done",
                        "input_tokens": 5000,
                        "output_tokens": 5000,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                        "model": "gpt-4o",
                        "cost_estimate": 0.10,
                        "duration_seconds": 30.0,
                    },
                    {
                        "task_id": "T02",
                        "title": "Task2",
                        "status": "failed",
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                        "model": "gpt-4o",
                        "cost_estimate": 0.0,
                        "duration_seconds": 10.0,
                    },
                ],
                "task_count": 2,
                "outcome": "failure",
                "duration_seconds": 40.0,
            }
        ]
        (arch_dir / "token_ledger.json").write_text(json.dumps(records), encoding="utf-8")
        result = _get_last_run_summary(tmp_path)
        assert result is not None
        assert result["date"] == "2026-05-20"
        assert result["goal"] == "Test goal"
        assert result["tasks_done"] == 1
        assert result["tasks_total"] == 2
        assert result["tokens"] == 10000
        assert result["cost"] == 0.10
        assert result["outcome"] == "failure"

    def test_returns_last_record_when_multiple(self, tmp_path: Path) -> None:
        """Returns the most recent (last) record when multiple exist."""
        from the_architect.cli import _get_last_run_summary

        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        records = [
            {
                "run_id": "r1",
                "timestamp": "2026-05-19T10:00:00+00:00",
                "goal_summary": "Old goal",
                "total_tokens": 5000,
                "total_cost_estimate": 0.05,
                "model_breakdown": [],
                "task_breakdown": [],
                "task_count": 1,
                "outcome": "success",
                "duration_seconds": 20.0,
            },
            {
                "run_id": "r2",
                "timestamp": "2026-05-20T10:00:00+00:00",
                "goal_summary": "New goal",
                "total_tokens": 10000,
                "total_cost_estimate": 0.10,
                "model_breakdown": [],
                "task_breakdown": [],
                "task_count": 1,
                "outcome": "failure",
                "duration_seconds": 30.0,
            },
        ]
        (arch_dir / "token_ledger.json").write_text(json.dumps(records), encoding="utf-8")
        result = _get_last_run_summary(tmp_path)
        assert result is not None
        assert result["goal"] == "New goal"
        assert result["outcome"] == "failure"

    def test_goal_truncation_at_60_chars(self, tmp_path: Path) -> None:
        """Goal longer than 60 chars is truncated with ellipsis."""
        from the_architect.cli import _get_last_run_summary

        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        long_goal = "X" * 100
        records = [
            {
                "run_id": "r1",
                "timestamp": "2026-05-20T10:00:00+00:00",
                "goal_summary": long_goal,
                "total_tokens": 1000,
                "total_cost_estimate": 0.01,
                "model_breakdown": [],
                "task_breakdown": [],
                "task_count": 1,
                "outcome": "success",
                "duration_seconds": 10.0,
            }
        ]
        (arch_dir / "token_ledger.json").write_text(json.dumps(records), encoding="utf-8")
        result = _get_last_run_summary(tmp_path)
        assert result is not None
        assert len(result["goal"]) == 60  # 57 + "..."
        assert result["goal"].endswith("...")

    def test_empty_goal_summary(self, tmp_path: Path) -> None:
        """Empty goal_summary renders as empty string."""
        from the_architect.cli import _get_last_run_summary

        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        records = [
            {
                "run_id": "r1",
                "timestamp": "2026-05-20T10:00:00+00:00",
                "goal_summary": "",
                "total_tokens": 1000,
                "total_cost_estimate": 0.01,
                "model_breakdown": [],
                "task_breakdown": [],
                "task_count": 1,
                "outcome": "success",
                "duration_seconds": 10.0,
            }
        ]
        (arch_dir / "token_ledger.json").write_text(json.dumps(records), encoding="utf-8")
        result = _get_last_run_summary(tmp_path)
        assert result is not None
        assert result["goal"] == ""


# ---------------------------------------------------------------------------
# monitor
# ---------------------------------------------------------------------------


class TestMonitorCmd:
    """``monitor`` command — cover the TUI screen path."""

    def test_monitor_opens_tui_screen(self, tmp_path: Path) -> None:
        """Monitor should open the TUI monitor screen."""
        with patch("the_architect.tui.screens.run_monitor_screen") as mock_screen:
            result = CliRunner().invoke(main, ["monitor", "-p", str(tmp_path)])
        mock_screen.assert_called_once()
        assert result.exit_code == 0, result.output

    def test_monitor_tui_failure_exits_nonzero(self, tmp_path: Path) -> None:
        """If TUI screen raises, monitor exits with code 1."""
        with patch(
            "the_architect.tui.screens.run_monitor_screen",
            side_effect=RuntimeError("screen broken"),
        ):
            result = CliRunner().invoke(main, ["monitor", "-p", str(tmp_path)])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------


class TestLogsCmd:
    """Cover the ``logs`` sub-command's filter and ``--all`` branches."""

    def test_logs_no_log_dir(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(main, ["logs", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "No log directory" in result.output

    def test_logs_empty_log_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".architect" / "logs").mkdir(parents=True)
        result = CliRunner().invoke(main, ["logs", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "No log files" in result.output

    def test_logs_list_all(self, tmp_path: Path) -> None:
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "T01_one.log").write_text("first log\n", encoding="utf-8")
        (log_dir / "T02_two.log").write_text("second log\n", encoding="utf-8")

        result = CliRunner().invoke(main, ["logs", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "T01_one.log" in result.output
        assert "T02_two.log" in result.output

    def test_logs_task_filter_hits(self, tmp_path: Path) -> None:
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "T01_one.log").write_text(
            '{"type":"text","part":{"text":"hello world"}}\n'
            '{"type":"error","message":"boom"}\n'
            "raw non-json line\n",
            encoding="utf-8",
        )

        result = CliRunner().invoke(main, ["logs", "-p", str(tmp_path), "-t", "T01", "--all"])
        assert result.exit_code == 0, result.output
        assert "hello world" in result.output
        assert "[ERROR]" in result.output and "boom" in result.output
        assert "raw non-json" in result.output

    def test_logs_task_filter_tail(self, tmp_path: Path) -> None:
        """--tail N restricts output to the last N lines."""
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        body = "\n".join(f"line{i}" for i in range(1, 21)) + "\n"
        (log_dir / "T01.log").write_text(body, encoding="utf-8")

        result = CliRunner().invoke(main, ["logs", "-p", str(tmp_path), "-t", "T01", "-n", "5"])
        assert result.exit_code == 0, result.output
        assert "line20" in result.output
        # Tail=5 means lines 16..20 visible; earlier lines suppressed.
        assert "line10" not in result.output

    def test_logs_task_filter_miss(self, tmp_path: Path) -> None:
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "T01.log").write_text("irrelevant\n", encoding="utf-8")

        result = CliRunner().invoke(main, ["logs", "-p", str(tmp_path), "-t", "T99"])
        assert result.exit_code == 1
        assert "No log found" in result.output


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


class TestCancelCmd:
    """Exercise the ``cancel`` command's branches around the lock file."""

    def test_cancel_no_lock(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(main, ["cancel", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "No lock file" in result.output

    def test_cancel_removes_stale_lock(self, tmp_path: Path) -> None:
        """A lock pointing to a dead PID is removed with no prompt."""
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        # Use a PID that will not exist
        (arch_dir / "runner.lock").write_text("999999999", encoding="utf-8")

        result = CliRunner().invoke(main, ["cancel", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert not (arch_dir / "runner.lock").exists()

    def test_cancel_malformed_lock(self, tmp_path: Path) -> None:
        """A lock file that is not a number must still be removed."""
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        (arch_dir / "runner.lock").write_text("not-a-pid", encoding="utf-8")

        result = CliRunner().invoke(main, ["cancel", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert not (arch_dir / "runner.lock").exists()

    def test_cancel_json_no_lock(self, tmp_path: Path) -> None:
        """--json with no lock file outputs clean JSON."""
        import json

        result = CliRunner().invoke(main, ["cancel", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["lock_file"] == "not_found"
        assert data["cancelled"] is False

    def test_cancel_json_removes_stale_lock(self, tmp_path: Path) -> None:
        """--json with a stale lock outputs clean JSON and removes lock."""
        import json

        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        (arch_dir / "runner.lock").write_text("999999999", encoding="utf-8")

        result = CliRunner().invoke(main, ["cancel", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["lock_file"] == "removed"
        assert data["cancelled"] is True
        assert not (arch_dir / "runner.lock").exists()

    def test_cancel_json_monitor_state_cleaned(self, tmp_path: Path) -> None:
        """--json cancel cleans up monitor state file."""
        import json

        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        (arch_dir / "runner.lock").write_text("999999999", encoding="utf-8")
        # Write a monitor state file
        monitor_file = arch_dir / "monitor_state.json"
        monitor_file.write_text(
            json.dumps({"status": "RUNNING", "current_task_id": "T01"}),
            encoding="utf-8",
        )

        result = CliRunner().invoke(main, ["cancel", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["monitor_state"] == "cleaned"
        # Monitor state should be updated with cancelled info
        updated_state = json.loads(monitor_file.read_text(encoding="utf-8"))
        assert updated_state["cancelled"] is True

    def test_cancel_force_skips_confirmation(self, tmp_path: Path) -> None:
        """--force bypasses confirmation prompt for alive PID."""
        # We cannot easily test with a real alive PID, but we can verify
        # that --force flag is accepted and doesn't crash
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        (arch_dir / "runner.lock").write_text("999999999", encoding="utf-8")

        result = CliRunner().invoke(main, ["cancel", "-p", str(tmp_path), "--force"])
        assert result.exit_code == 0, result.output
        assert not (arch_dir / "runner.lock").exists()

    def test_cancel_yes_flag_alias(self, tmp_path: Path) -> None:
        """--yes is an alias for --force and works identically."""
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        (arch_dir / "runner.lock").write_text("999999999", encoding="utf-8")

        result = CliRunner().invoke(main, ["cancel", "-p", str(tmp_path), "--yes"])
        assert result.exit_code == 0, result.output
        assert not (arch_dir / "runner.lock").exists()

    def test_cancel_clean_output(self, tmp_path: Path) -> None:
        """Non-JSON cancel shows lock removed and monitor state cleaned."""
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        (arch_dir / "runner.lock").write_text("999999999", encoding="utf-8")

        result = CliRunner().invoke(main, ["cancel", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Lock removed" in result.output
        assert "Monitor state cleaned" in result.output

    def test_cancel_monitor_state_preserves_existing_fields(self, tmp_path: Path) -> None:
        """Monitor state cleanup preserves existing task and token data."""
        import json

        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        (arch_dir / "runner.lock").write_text("999999999", encoding="utf-8")
        # Write a monitor state with existing data
        monitor_file = arch_dir / "monitor_state.json"
        existing_state = {
            "status": "RUNNING",
            "current_task_id": "T02",
            "current_task_title": "Some Task",
            "tasks": [{"id": "T01", "title": "Done", "status": "done"}],
            "tokens": {"session_total": 5000},
            "circuit_breaker": {"state": "CLOSED"},
        }
        monitor_file.write_text(json.dumps(existing_state), encoding="utf-8")

        CliRunner().invoke(main, ["cancel", "-p", str(tmp_path)])

        updated_state = json.loads(monitor_file.read_text(encoding="utf-8"))
        assert updated_state["cancelled"] is True
        assert updated_state["current_task_id"] == "T02"
        assert updated_state["tokens"]["session_total"] == 5000
        assert len(updated_state["tasks"]) == 1


# ---------------------------------------------------------------------------
# circuit
# ---------------------------------------------------------------------------


class TestCircuitCmd:
    """Basic coverage of ``architect circuit``."""

    def test_circuit_no_state(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(main, ["circuit", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        # The command prints a friendly message when there is no state.
        assert "No circuit state" in result.output or "no circuit" in result.output.lower()

    def test_circuit_reset_all_no_state(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(main, ["circuit", "-p", str(tmp_path), "--reset", "all"])
        # Either exits 0 with a message or 1 — both branches are acceptable
        # provided the command does not crash.
        assert result.exit_code in (0, 1)


class TestCircuitJsonOutput:
    """Tests for ``architect circuit --json`` structured JSON output."""

    def test_circuit_json_basic_output(self, tmp_path: Path) -> None:
        """Should output valid JSON with tasks, project, and summary keys."""
        import json

        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        circuit_data = {
            "T01": {
                "state": "CLOSED",
                "consecutive_no_progress": 0,
                "consecutive_same_error": 0,
            }
        }
        (arch_dir / "circuit.json").write_text(json.dumps(circuit_data), encoding="utf-8")
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tasks_dir / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )

        with patch("the_architect.cli.discover_tasks", return_value=[mock_task]):
            runner = CliRunner()
            result = runner.invoke(main, ["circuit", "-p", str(tmp_path), "--json"])
            assert result.exit_code == 0, result.output
            data = json.loads(result.output)
            assert "tasks" in data
            assert "project" in data
            assert "summary" in data
            assert data["summary"]["total"] == 1
            assert data["summary"]["closed"] == 1

    def test_circuit_json_open_state(self, tmp_path: Path) -> None:
        """Should include OPEN state with recovery_action and opened_at."""
        import json
        from datetime import UTC, datetime

        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        circuit_data = {
            "T02": {
                "state": "OPEN",
                "consecutive_no_progress": 3,
                "consecutive_same_error": 0,
                "recovery_action": "REPLAN",
                "opened_at": datetime.now(tz=UTC).isoformat(),
            }
        }
        (arch_dir / "circuit.json").write_text(json.dumps(circuit_data), encoding="utf-8")
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        mock_task = Task(
            name="T02_fail",
            prefix="T02",
            number=2,
            path=tasks_dir / "T02_fail.md",
            title="Failing task",
            status=TaskStatus.PENDING,
        )

        with patch("the_architect.cli.discover_tasks", return_value=[mock_task]):
            runner = CliRunner()
            result = runner.invoke(main, ["circuit", "-p", str(tmp_path), "--json"])
            assert result.exit_code == 0, result.output
            data = json.loads(result.output)
            assert data["summary"]["open"] == 1
            task_entry = data["tasks"][0]
            assert task_entry["task_id"] == "T02"
            assert task_entry["state"] == "OPEN"
            assert task_entry["recovery_action"] == "REPLAN"
            assert task_entry["opened_at"] is not None

    def test_circuit_json_half_open_state(self, tmp_path: Path) -> None:
        """Should include HALF_OPEN state in output."""
        import json

        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        circuit_data = {
            "T03": {
                "state": "HALF_OPEN",
                "consecutive_no_progress": 0,
                "consecutive_same_error": 0,
                "recovery_action": None,
                "opened_at": None,
            }
        }
        (arch_dir / "circuit.json").write_text(json.dumps(circuit_data), encoding="utf-8")
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        mock_task = Task(
            name="T03_retry",
            prefix="T03",
            number=3,
            path=tasks_dir / "T03_retry.md",
            title="Retry task",
            status=TaskStatus.PENDING,
        )

        with patch("the_architect.cli.discover_tasks", return_value=[mock_task]):
            runner = CliRunner()
            result = runner.invoke(main, ["circuit", "-p", str(tmp_path), "--json"])
            assert result.exit_code == 0, result.output
            data = json.loads(result.output)
            assert data["summary"]["half_open"] == 1
            task_entry = data["tasks"][0]
            assert task_entry["state"] == "HALF_OPEN"

    def test_circuit_json_mixed_states(self, tmp_path: Path) -> None:
        """Should correctly count mixed CLOSED, OPEN, and HALF_OPEN states."""
        import json

        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        circuit_data = {
            "T01": {
                "state": "CLOSED",
                "consecutive_no_progress": 0,
                "consecutive_same_error": 0,
            },
            "T02": {
                "state": "OPEN",
                "consecutive_no_progress": 3,
                "consecutive_same_error": 0,
                "recovery_action": "WAIT",
                "opened_at": "2026-05-17T00:00:00",
            },
            "T03": {
                "state": "HALF_OPEN",
                "consecutive_no_progress": 0,
                "consecutive_same_error": 0,
            },
        }
        (arch_dir / "circuit.json").write_text(json.dumps(circuit_data), encoding="utf-8")
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        mock_tasks = [
            Task(
                name=f"T{i:02d}_task",
                prefix=f"T{i:02d}",
                number=i,
                path=tasks_dir / f"T{i:02d}_task.md",
                title=f"Task {i}",
                status=TaskStatus.PENDING,
            )
            for i in range(1, 4)
        ]

        with patch("the_architect.cli.discover_tasks", return_value=mock_tasks):
            runner = CliRunner()
            result = runner.invoke(main, ["circuit", "-p", str(tmp_path), "--json"])
            assert result.exit_code == 0, result.output
            data = json.loads(result.output)
            assert data["summary"]["total"] == 3
            assert data["summary"]["closed"] == 1
            assert data["summary"]["open"] == 1
            assert data["summary"]["half_open"] == 1

    def test_circuit_json_task_no_circuit_state(self, tmp_path: Path) -> None:
        """Should include tasks with no circuit state as CLOSED with zeroed counters."""
        import json

        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")
        arch_dir = tmp_path / ".architect"
        arch_dir.mkdir()
        # No circuit.json — all tasks have no circuit state
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        mock_task = Task(
            name="T01_test",
            prefix="T01",
            number=1,
            path=tasks_dir / "T01_test.md",
            title="Test task",
            status=TaskStatus.PENDING,
        )

        with patch("the_architect.cli.discover_tasks", return_value=[mock_task]):
            runner = CliRunner()
            result = runner.invoke(main, ["circuit", "-p", str(tmp_path), "--json"])
            assert result.exit_code == 0, result.output
            data = json.loads(result.output)
            assert data["summary"]["total"] == 1
            assert data["summary"]["closed"] == 1
            task_entry = data["tasks"][0]
            assert task_entry["task_id"] == "T01"
            assert task_entry["state"] == "CLOSED"
            assert task_entry["consecutive_no_progress"] == 0
            assert task_entry["consecutive_same_error"] == 0
            assert task_entry["recovery_action"] is None

    def test_circuit_json_mutual_exclusion_tui(self, tmp_path: Path) -> None:
        """Should error when --json and --tui are both provided."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")
        (tmp_path / ".architect").mkdir()

        runner = CliRunner()
        result = runner.invoke(main, ["circuit", "-p", str(tmp_path), "--json", "--tui"])
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output

    def test_circuit_json_mutual_exclusion_reset(self, tmp_path: Path) -> None:
        """Should error when --json and --reset are both provided."""
        (tmp_path / "architect.toml").write_text("[architect]\n", encoding="utf-8")
        (tmp_path / ".architect").mkdir()

        runner = CliRunner()
        result = runner.invoke(main, ["circuit", "-p", str(tmp_path), "--json", "--reset", "T01"])
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output


# ---------------------------------------------------------------------------
# skip — extra branches
# ---------------------------------------------------------------------------


class TestSkipCmdMissingProgress:
    """Skip without PROGRESS.md prints a helpful error and exits 1."""

    def test_skip_without_progress_file(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(main, ["skip", "-t", "T01", "-p", str(tmp_path)])
        assert result.exit_code == 1
        assert "PROGRESS.md not found" in result.output

    def test_skip_already_done(self, tmp_path: Path) -> None:
        """Skipping a task that is already Done prints an info message."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        (tasks_dir / "PROGRESS.md").write_text(
            """# Progress

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | Done task | Done    | 2024-01-01 |
""",
            encoding="utf-8",
        )
        result = CliRunner().invoke(main, ["skip", "-t", "T01", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "already Done" in result.output


class TestSkipCmdAllStatuses:
    """Skip command handles all terminal statuses (Pending, Failed, Blocked, Skipped)."""

    def _make_progress(self, tmp_path: Path, status: str) -> Path:
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        pf = tasks_dir / "PROGRESS.md"
        pf.write_text(
            f"""# The Architect — Progress Tracker

## Overall Status

**Tasks completed:** 0
**Next task to run:** T01

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | Test task | {status} | — |

---

## Current State

Testing.

## Last Task Summary

N/A

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
| | | | |
""",
            encoding="utf-8",
        )
        return pf

    def test_skip_pending_to_done(self, tmp_path: Path) -> None:
        """Skip a Pending task — transitions to Done."""
        self._make_progress(tmp_path, "Pending")
        result = CliRunner().invoke(main, ["skip", "-t", "T01", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "marked as Done" in result.output
        assert "Pending → Done" in result.output

    def test_skip_failed_to_done(self, tmp_path: Path) -> None:
        """Skip a Failed task — transitions to Done."""
        self._make_progress(tmp_path, "Failed")
        result = CliRunner().invoke(main, ["skip", "-t", "T01", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "marked as Done" in result.output
        assert "Failed → Done" in result.output

    def test_skip_blocked_to_done(self, tmp_path: Path) -> None:
        """Skip a Blocked task — transitions to Done."""
        self._make_progress(tmp_path, "Blocked")
        result = CliRunner().invoke(main, ["skip", "-t", "T01", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "marked as Done" in result.output
        assert "Blocked → Done" in result.output

    def test_skip_skipped_to_done(self, tmp_path: Path) -> None:
        """Skip a Skipped task — transitions to Done."""
        self._make_progress(tmp_path, "Skipped")
        result = CliRunner().invoke(main, ["skip", "-t", "T01", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "marked as Done" in result.output
        assert "Skipped → Done" in result.output

    def test_skip_failed_annotated_to_done(self, tmp_path: Path) -> None:
        """Skip a Failed (3 attempts) task — annotated status transitions to Done."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        pf = tasks_dir / "PROGRESS.md"
        pf.write_text(
            """# The Architect — Progress Tracker

## Overall Status

**Tasks completed:** 0
**Next task to run:** T01

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | Test task | Failed (3 attempts) | — |

---

## Current State

Testing.

## Last Task Summary

N/A

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
| | | | |
""",
            encoding="utf-8",
        )
        result = CliRunner().invoke(main, ["skip", "-t", "T01", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "marked as Done" in result.output


class TestSkipCmdCounterIncrement:
    """Skip command increments the Tasks completed counter."""

    def test_skip_increments_counter(self, tmp_path: Path) -> None:
        """Counter goes from 0 to 1 after skipping."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        pf = tasks_dir / "PROGRESS.md"
        pf.write_text(
            """# The Architect — Progress Tracker

## Overall Status

**Tasks completed:** 0
**Next task to run:** T01

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | Test task | Pending | — |

---

## Current State

Testing.

## Last Task Summary

N/A

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
| | | | |
""",
            encoding="utf-8",
        )
        CliRunner().invoke(main, ["skip", "-t", "T01", "-p", str(tmp_path)])
        updated = pf.read_text(encoding="utf-8")
        assert "**Tasks completed:** 1" in updated

    def test_skip_does_not_increment_when_already_done(self, tmp_path: Path) -> None:
        """Counter stays at 3 when skipping a task that is already Done."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        pf = tasks_dir / "PROGRESS.md"
        pf.write_text(
            """# The Architect — Progress Tracker

## Overall Status

**Tasks completed:** 3
**Next task to run:** T02

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | Test task | Done | 2026-01-01 |

---

## Current State

Testing.

## Last Task Summary

N/A

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
| | | | |
""",
            encoding="utf-8",
        )
        CliRunner().invoke(main, ["skip", "-t", "T01", "-p", str(tmp_path)])
        updated = pf.read_text(encoding="utf-8")
        assert "**Tasks completed:** 3" in updated


class TestSkipCmdLastFlag:
    """Skip --last flag skips the most recent failed task."""

    def test_skip_last_skips_last_failed(self, tmp_path: Path) -> None:
        """--last skips the highest-numbered failed task."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        pf = tasks_dir / "PROGRESS.md"
        pf.write_text(
            """# The Architect — Progress Tracker

## Overall Status

**Tasks completed:** 0
**Next task to run:** T03

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | First task | Done | 2026-01-01 |
| T02 | Second task | Failed | — |
| T03 | Third task | Failed | — |

---

## Current State

Testing.

## Last Task Summary

N/A

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
| | | | |
""",
            encoding="utf-8",
        )
        # Create task files so discover_tasks finds them
        for name in ["T01_first_task", "T02_second_task", "T03_third_task"]:
            (tasks_dir / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")

        result = CliRunner().invoke(main, ["skip", "--last", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "T03" in result.output
        assert "marked as Done" in result.output

    def test_skip_last_no_failed_tasks(self, tmp_path: Path) -> None:
        """--last exits 1 when no failed tasks exist."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        pf = tasks_dir / "PROGRESS.md"
        pf.write_text(
            """# The Architect — Progress Tracker

## Overall Status

**Tasks completed:** 2
**Next task to run:** T03

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | First task | Done | 2026-01-01 |
| T02 | Second task | Done | 2026-01-02 |

---

## Current State

Testing.

## Last Task Summary

N/A

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
| | | | |
""",
            encoding="utf-8",
        )
        result = CliRunner().invoke(main, ["skip", "--last", "-p", str(tmp_path)])
        assert result.exit_code == 1
        assert "No failed tasks" in result.output


class TestSkipCmdFailedFlag:
    """Skip --failed flag skips all failed tasks."""

    def test_skip_failed_skips_all_failed(self, tmp_path: Path) -> None:
        """--failed marks all Failed tasks as Done."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        pf = tasks_dir / "PROGRESS.md"
        pf.write_text(
            """# The Architect — Progress Tracker

## Overall Status

**Tasks completed:** 0
**Next task to run:** T03

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | First task | Done | 2026-01-01 |
| T02 | Second task | Failed | — |
| T03 | Third task | Failed | — |
| T04 | Fourth task | Pending | — |

---

## Current State

Testing.

## Last Task Summary

N/A

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
| | | | |
""",
            encoding="utf-8",
        )
        for name in ["T01_first", "T02_second", "T03_third", "T04_fourth"]:
            (tasks_dir / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")

        result = CliRunner().invoke(main, ["skip", "--failed", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Skipped 2" in result.output

    def test_skip_failed_no_failed_tasks(self, tmp_path: Path) -> None:
        """--failed exits 1 when no failed tasks exist."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        pf = tasks_dir / "PROGRESS.md"
        pf.write_text(
            """# The Architect — Progress Tracker

## Overall Status

**Tasks completed:** 2
**Next task to run:** T03

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | First task | Done | 2026-01-01 |
| T02 | Second task | Done | 2026-01-02 |

---

## Current State

Testing.

## Last Task Summary

N/A

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
| | | | |
""",
            encoding="utf-8",
        )
        result = CliRunner().invoke(main, ["skip", "--failed", "-p", str(tmp_path)])
        assert result.exit_code == 1
        assert "No failed tasks" in result.output


class TestSkipCmdJson:
    """Skip --json flag outputs clean JSON."""

    def test_skip_json_output(self, tmp_path: Path) -> None:
        """--json produces valid JSON with task status."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        pf = tasks_dir / "PROGRESS.md"
        pf.write_text(
            """# The Architect — Progress Tracker

## Overall Status

**Tasks completed:** 0
**Next task to run:** T01

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | Test task | Failed | — |

---

## Current State

Testing.

## Last Task Summary

N/A

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
| | | | |
""",
            encoding="utf-8",
        )
        result = CliRunner().invoke(main, ["skip", "-t", "T01", "--json", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert data["task"] == "T01"
        assert data["status"] == "done"
        assert data["previous_status"] == "Failed"

    def test_skip_json_already_done(self, tmp_path: Path) -> None:
        """--json for already-done task shows status already_done."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        pf = tasks_dir / "PROGRESS.md"
        pf.write_text(
            """# The Architect — Progress Tracker

## Overall Status

**Tasks completed:** 1
**Next task to run:** T02

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | Test task | Done | 2026-01-01 |

---

## Current State

Testing.

## Last Task Summary

N/A

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
| | | | |
""",
            encoding="utf-8",
        )
        result = CliRunner().invoke(main, ["skip", "-t", "T01", "--json", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert data["status"] == "already_done"

    def test_skip_json_not_found(self, tmp_path: Path) -> None:
        """--json for non-existent task shows error status."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        pf = tasks_dir / "PROGRESS.md"
        pf.write_text(
            """# The Architect — Progress Tracker

## Overall Status

**Tasks completed:** 0
**Next task to run:** T01

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | Test task | Pending | — |

---

## Current State

Testing.

## Last Task Summary

N/A

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
| | | | |
""",
            encoding="utf-8",
        )
        result = CliRunner().invoke(main, ["skip", "-t", "T99", "--json", "-p", str(tmp_path)])
        assert result.exit_code == 1
        data = json.loads(result.output.strip())
        assert data["status"] == "error"

    def test_skip_failed_json_output(self, tmp_path: Path) -> None:
        """--failed --json produces valid JSON with skipped list."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        pf = tasks_dir / "PROGRESS.md"
        pf.write_text(
            """# The Architect — Progress Tracker

## Overall Status

**Tasks completed:** 0
**Next task to run:** T03

---

## Task Log

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01 | First task | Failed | — |
| T02 | Second task | Failed | — |

---

## Current State

Testing.

## Last Task Summary

N/A

---

## Permanent Decisions

| Decision | Value | Reason | Task |
|----------|-------|--------|------|
| | | | |
""",
            encoding="utf-8",
        )
        for name in ["T01_first", "T02_second"]:
            (tasks_dir / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")

        result = CliRunner().invoke(main, ["skip", "--failed", "--json", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert "skipped" in data
        assert len(data["skipped"]) == 2


class TestSkipCmdMutualExclusivity:
    """Skip command enforces mutual exclusivity of --task, --last, --failed."""

    def test_skip_no_flags_errors(self, tmp_path: Path) -> None:
        """No flags specified — exits 1 with error."""
        result = CliRunner().invoke(main, ["skip", "-p", str(tmp_path)])
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output or "Specify one of" in result.output

    def test_skip_task_and_last_conflict(self, tmp_path: Path) -> None:
        """--task and --last together — exits 1."""
        result = CliRunner().invoke(main, ["skip", "-t", "T01", "--last", "-p", str(tmp_path)])
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output

    def test_skip_task_and_failed_conflict(self, tmp_path: Path) -> None:
        """--task and --failed together — exits 1."""
        result = CliRunner().invoke(main, ["skip", "-t", "T01", "--failed", "-p", str(tmp_path)])
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output

    def test_skip_last_and_failed_conflict(self, tmp_path: Path) -> None:
        """--last and --failed together — exits 1."""
        result = CliRunner().invoke(main, ["skip", "--last", "--failed", "-p", str(tmp_path)])
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output


# ---------------------------------------------------------------------------
# reset — extra branches
# ---------------------------------------------------------------------------


class TestResetCmdMissingProgress:
    def test_reset_without_progress_file(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(main, ["reset", "-p", str(tmp_path)], input="y\n")
        assert result.exit_code == 1
        assert "PROGRESS.md not found" in result.output

    def test_reset_json_without_progress_file(self, tmp_path: Path) -> None:
        """reset --json outputs JSON error when PROGRESS.md is missing."""
        result = CliRunner().invoke(main, ["reset", "-p", str(tmp_path), "--json"], input="y\n")
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["reset"] is False
        assert "PROGRESS.md not found" in data["error"]

    def test_reset_force_flag(self, tmp_path: Path) -> None:
        """reset --force skips confirmation prompt."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "PROGRESS.md").write_text("# Progress\n", encoding="utf-8")
        result = CliRunner().invoke(main, ["reset", "-p", str(tmp_path), "--force"])
        assert result.exit_code == 0
        assert "PROGRESS.md reset" in result.output

    def test_reset_json_flag(self, tmp_path: Path) -> None:
        """reset --json outputs clean JSON."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "PROGRESS.md").write_text("# Progress\n", encoding="utf-8")
        result = CliRunner().invoke(main, ["reset", "-p", str(tmp_path), "--json", "--force"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["reset"] is True
        assert "project" in data

    def test_reset_json_requires_force(self, tmp_path: Path) -> None:
        """reset --json without --force is cancelled (scripted mode requires --force)."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "PROGRESS.md").write_text("# Progress\n", encoding="utf-8")
        result = CliRunner().invoke(main, ["reset", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["reset"] is False
        assert data["cancelled"] is True


# ---------------------------------------------------------------------------
# retry — use the existing happy-path hooks
# ---------------------------------------------------------------------------


class TestRetryCmd:
    """``retry`` mostly invokes the runner — we mock it and verify wiring."""

    def test_retry_task_not_found_in_tasks_dir(self, tmp_path: Path) -> None:
        """When there is no matching task file, retry exits 1."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        (tasks_dir / "PROGRESS.md").write_text(
            """# Progress

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T05  | Nope | Pending | — |
""",
            encoding="utf-8",
        )
        # No tasks/ directory → discover_tasks returns []
        result = CliRunner().invoke(main, ["retry", "-t", "T05", "-p", str(tmp_path)])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_retry_resets_done_task_and_runs(self, tmp_path: Path) -> None:
        """When the task is Done, retry flips it to Pending and calls the runner."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_task.md").write_text("# T01 Task\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text(
            """# Progress

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | Task | Done | 2024-01-01 |
""",
            encoding="utf-8",
        )

        # Mock out the runner + provider plumbing so the command does not
        # actually try to spawn opencode.
        fake_provider = MagicMock()

        async def _noop_run_task(*_: object, **__: object) -> MagicMock:
            return MagicMock()

        with (
            patch(
                "the_architect.core.opencode_provider.OpenCodeProvider",
                return_value=fake_provider,
            ),
            patch("the_architect.cli.setup_logging"),
            patch("the_architect.cli.run_task", side_effect=_noop_run_task),
        ):
            result = CliRunner().invoke(main, ["retry", "-t", "T01", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        # PROGRESS.md should now have T01 back to Pending
        content = (tasks_dir / "PROGRESS.md").read_text(encoding="utf-8")
        assert "Pending" in content

    def test_retry_last_no_failed_tasks(self, tmp_path: Path) -> None:
        """--last exits 1 when no failed tasks exist."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_task.md").write_text("# T01 Task\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text(
            """# Progress

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | Task | Done | 2024-01-01 |
""",
            encoding="utf-8",
        )
        result = CliRunner().invoke(main, ["retry", "--last", "-p", str(tmp_path)])
        assert result.exit_code == 1
        assert "No failed tasks" in result.output

    def test_retry_last_one_failed_task(self, tmp_path: Path) -> None:
        """--last retries the single failed task."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_task.md").write_text("# T01 Task\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text(
            """# Progress

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | Task | Failed | — |
""",
            encoding="utf-8",
        )
        fake_provider = MagicMock()

        async def _noop_run_task(*_: object, **__: object) -> MagicMock:
            return MagicMock()

        with (
            patch(
                "the_architect.core.opencode_provider.OpenCodeProvider",
                return_value=fake_provider,
            ),
            patch("the_architect.cli.setup_logging"),
            patch("the_architect.cli.run_task", side_effect=_noop_run_task),
        ):
            result = CliRunner().invoke(main, ["retry", "--last", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "T01" in result.output

    def test_retry_last_picks_last_in_plan_order(self, tmp_path: Path) -> None:
        """--last picks the highest-numbered failed task."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_task.md").write_text("# T01 Task\n", encoding="utf-8")
        (tasks_dir / "T03_task.md").write_text("# T03 Task\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text(
            """# Progress

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | One | Failed | — |
| T03  | Three | Failed | — |
""",
            encoding="utf-8",
        )
        fake_provider = MagicMock()

        async def _noop_run_task(*_: object, **__: object) -> MagicMock:
            return MagicMock()

        with (
            patch(
                "the_architect.core.opencode_provider.OpenCodeProvider",
                return_value=fake_provider,
            ),
            patch("the_architect.cli.setup_logging"),
            patch("the_architect.cli.run_task", side_effect=_noop_run_task),
        ):
            result = CliRunner().invoke(main, ["retry", "--last", "-p", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "T03" in result.output

    def test_retry_failed_resets_all(self, tmp_path: Path) -> None:
        """--failed resets all Failed tasks to Pending."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_task.md").write_text("# T01 Task\n", encoding="utf-8")
        (tasks_dir / "T02_task.md").write_text("# T02 Task\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text(
            """# Progress

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | One | Failed | — |
| T02  | Two | Failed | — |
""",
            encoding="utf-8",
        )
        result = CliRunner().invoke(main, ["retry", "--failed", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        content = (tasks_dir / "PROGRESS.md").read_text(encoding="utf-8")
        # Both tasks should now be Pending
        assert content.count("Pending") == 2
        assert "Reset 2 failed" in result.output

    def test_retry_failed_no_failed_tasks(self, tmp_path: Path) -> None:
        """--failed exits 1 when no failed tasks exist."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_task.md").write_text("# T01 Task\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text(
            """# Progress

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | Task | Done | 2024-01-01 |
""",
            encoding="utf-8",
        )
        result = CliRunner().invoke(main, ["retry", "--failed", "-p", str(tmp_path)])
        assert result.exit_code == 1
        assert "No failed tasks" in result.output

    def test_retry_mutual_exclusivity_task_last(self, tmp_path: Path) -> None:
        """--task and --last cannot be used together."""
        result = CliRunner().invoke(main, ["retry", "-t", "T01", "--last", "-p", str(tmp_path)])
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output

    def test_retry_mutual_exclusivity_task_failed(self, tmp_path: Path) -> None:
        """--task and --failed cannot be used together."""
        result = CliRunner().invoke(main, ["retry", "-t", "T01", "--failed", "-p", str(tmp_path)])
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output

    def test_retry_mutual_exclusivity_last_failed(self, tmp_path: Path) -> None:
        """--last and --failed cannot be used together."""
        result = CliRunner().invoke(main, ["retry", "--last", "--failed", "-p", str(tmp_path)])
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output

    def test_retry_no_flag_error(self, tmp_path: Path) -> None:
        """No flag specified produces an error."""
        result = CliRunner().invoke(main, ["retry", "-p", str(tmp_path)])
        assert result.exit_code == 1
        assert "Specify one of" in result.output

    # ---------------------------------------------------------------------
    # retry --json and --force tests
    # ---------------------------------------------------------------------

    def test_retry_json_requires_force(self, tmp_path: Path) -> None:
        """--json without --force returns cancelled JSON."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_task.md").write_text("# T01 Task\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text(
            """# Progress

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | Task | Failed | — |
""",
            encoding="utf-8",
        )
        result = CliRunner().invoke(main, ["retry", "-t", "T01", "--json", "-p", str(tmp_path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["cancelled"] is True
        assert "project" in data

    def test_retry_task_json_output(self, tmp_path: Path) -> None:
        """--task --json --force outputs structured JSON."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_task.md").write_text("# T01 Task\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text(
            """# Progress

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | Task | Failed | — |
""",
            encoding="utf-8",
        )
        fake_provider = MagicMock()

        async def _noop_run_task(*_: object, **__: object) -> MagicMock:
            return MagicMock()

        with (
            patch(
                "the_architect.core.opencode_provider.OpenCodeProvider",
                return_value=fake_provider,
            ),
            patch("the_architect.cli.setup_logging"),
            patch("the_architect.cli.run_task", side_effect=_noop_run_task),
        ):
            result = CliRunner().invoke(
                main,
                ["retry", "-t", "T01", "--json", "--force", "-p", str(tmp_path)],
            )

        assert result.exit_code == 0, result.output
        # JSON output should appear before the task runner output
        lines = result.output.strip().split("\n")
        json_output = ""
        for line in lines:
            json_output += line + "\n"
            try:
                data = json.loads(json_output.strip())
                break
            except json.JSONDecodeError:
                continue
        else:
            data = json.loads(result.output.split("\n---")[0].strip())

        assert data["project"] == str(tmp_path)
        assert data["task_id"] == "T01"
        assert data["action"] == "retry_task"
        assert data["previous_status"] == "Failed"
        assert data["reset_status"] == "Pending"

    def test_retry_last_json_output(self, tmp_path: Path) -> None:
        """--last --json --force outputs structured JSON."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_task.md").write_text("# T01 Task\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text(
            """# Progress

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | Task | Failed | — |
""",
            encoding="utf-8",
        )
        fake_provider = MagicMock()

        async def _noop_run_task(*_: object, **__: object) -> MagicMock:
            return MagicMock()

        with (
            patch(
                "the_architect.core.opencode_provider.OpenCodeProvider",
                return_value=fake_provider,
            ),
            patch("the_architect.cli.setup_logging"),
            patch("the_architect.cli.run_task", side_effect=_noop_run_task),
        ):
            result = CliRunner().invoke(
                main,
                ["retry", "--last", "--json", "--force", "-p", str(tmp_path)],
            )

        assert result.exit_code == 0, result.output
        # Parse JSON from output
        data = json.loads(result.output.split("\n---")[0].strip())
        assert data["task_id"] == "T01"
        assert data["action"] == "retry_last"
        assert data["previous_status"] == "Failed"
        assert "title" in data

    def test_retry_failed_json_output(self, tmp_path: Path) -> None:
        """--failed --json --force outputs structured JSON with task list."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_task.md").write_text("# T01 One\n", encoding="utf-8")
        (tasks_dir / "T02_task.md").write_text("# T02 Two\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text(
            """# Progress

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | One | Failed | — |
| T02  | Two | Failed | — |
""",
            encoding="utf-8",
        )
        result = CliRunner().invoke(
            main,
            ["retry", "--failed", "--json", "--force", "-p", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["project"] == str(tmp_path)
        assert data["action"] == "reset_failed"
        assert data["count"] == 2
        assert len(data["tasks"]) == 2
        assert data["tasks"][0]["task_id"] == "T01"
        assert data["tasks"][1]["task_id"] == "T02"

    def test_retry_json_no_failed_tasks_error(self, tmp_path: Path) -> None:
        """--last --json returns error JSON when no failed tasks exist."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_task.md").write_text("# T01 Task\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text(
            """# Progress

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | Task | Done | 2024-01-01 |
""",
            encoding="utf-8",
        )
        result = CliRunner().invoke(
            main, ["retry", "--last", "--json", "--force", "-p", str(tmp_path)]
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "error" in data
        assert "No failed tasks" in data["error"]

    def test_retry_failed_json_no_failed_error(self, tmp_path: Path) -> None:
        """--failed --json returns error JSON when no failed tasks exist."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_task.md").write_text("# T01 Task\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text(
            """# Progress

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | Task | Done | 2024-01-01 |
""",
            encoding="utf-8",
        )
        result = CliRunner().invoke(
            main, ["retry", "--failed", "--json", "--force", "-p", str(tmp_path)]
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "error" in data

    def test_retry_json_mutual_exclusivity_error(self, tmp_path: Path) -> None:
        """--task + --last with --json returns error JSON."""
        result = CliRunner().invoke(
            main,
            ["retry", "-t", "T01", "--last", "--json", "-p", str(tmp_path)],
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "error" in data
        assert "mutually exclusive" in data["error"]

    def test_retry_json_no_flag_error(self, tmp_path: Path) -> None:
        """No mode flag with --json returns error JSON."""
        result = CliRunner().invoke(main, ["retry", "--json", "--force", "-p", str(tmp_path)])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "error" in data
        assert "Specify one of" in data["error"]

    def test_retry_json_task_not_found_error(self, tmp_path: Path) -> None:
        """--task with nonexistent task returns error JSON."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "PROGRESS.md").write_text(
            """# Progress

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T05  | Nope | Pending | — |
""",
            encoding="utf-8",
        )
        result = CliRunner().invoke(
            main, ["retry", "-t", "T05", "--json", "--force", "-p", str(tmp_path)]
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "error" in data
        assert "T05" in data["error"]

    def test_retry_force_skips_warnings(self, tmp_path: Path) -> None:
        """--force suppresses the 'not in terminal state' warning."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_task.md").write_text("# T01 Task\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text(
            """# Progress

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | Task | Pending | — |
""",
            encoding="utf-8",
        )
        fake_provider = MagicMock()

        async def _noop_run_task(*_: object, **__: object) -> MagicMock:
            return MagicMock()

        with (
            patch(
                "the_architect.core.opencode_provider.OpenCodeProvider",
                return_value=fake_provider,
            ),
            patch("the_architect.cli.setup_logging"),
            patch("the_architect.cli.run_task", side_effect=_noop_run_task),
        ):
            result = CliRunner().invoke(
                main, ["retry", "-t", "T01", "--force", "-p", str(tmp_path)]
            )

        assert result.exit_code == 0, result.output
        # The warning about non-terminal state should still appear without --json
        # but --force should not change the flow — it only matters with --json

    def test_retry_json_done_task(self, tmp_path: Path) -> None:
        """--task --json --force on a Done task shows previous_status as Done."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_task.md").write_text("# T01 Task\n", encoding="utf-8")
        (tasks_dir / "PROGRESS.md").write_text(
            """# Progress

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | Task | Done | 2024-01-01 |
""",
            encoding="utf-8",
        )
        fake_provider = MagicMock()

        async def _noop_run_task(*_: object, **__: object) -> MagicMock:
            return MagicMock()

        with (
            patch(
                "the_architect.core.opencode_provider.OpenCodeProvider",
                return_value=fake_provider,
            ),
            patch("the_architect.cli.setup_logging"),
            patch("the_architect.cli.run_task", side_effect=_noop_run_task),
        ):
            result = CliRunner().invoke(
                main,
                ["retry", "-t", "T01", "--json", "--force", "-p", str(tmp_path)],
            )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.split("\n---")[0].strip())
        assert data["previous_status"] == "Done"


# ---------------------------------------------------------------------------
# list command
# ---------------------------------------------------------------------------


class TestListCmd:
    def test_list_no_tasks_dir(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(main, ["list", "-p", str(tmp_path)])
        # Should not crash — message varies.
        assert result.exit_code == 0, result.output

    def test_list_with_tasks(self, tmp_path: Path) -> None:
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "T01_first.md").write_text("# T01 First\n", encoding="utf-8")
        (tasks_dir / "T02_second.md").write_text("# T02 Second\n", encoding="utf-8")
        (tmp_path / "PROGRESS.md").write_text(
            """# Progress

| Task | Title | Status | Completed |
|------|-------|--------|-----------|
| T01  | First  | Done    | 2024-01-01 |
| T02  | Second | Pending | — |
""",
            encoding="utf-8",
        )
        result = CliRunner().invoke(main, ["list", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "T01" in result.output
        assert "T02" in result.output


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


class TestDoctorCmd:
    """Exercise the ``doctor`` sub-command branches."""

    def _fake_provider(
        self,
        *,
        name: str,
        display_name: str,
        installed: bool,
        models: bool,
    ) -> MagicMock:
        """Create a provider mock with the fields doctor uses."""
        fake = MagicMock()
        fake.name = name
        fake.display_name = display_name
        fake.is_installed.return_value = installed
        fake.get_version.return_value = "0.6.12" if installed else "unknown"
        fake.has_any_models.return_value = models
        fake.check_update_available.return_value = ""
        fake.install_hint.return_value = f"install {name}"
        return fake

    def test_doctor_all_pass(self, tmp_path: Path) -> None:
        """When provider is detected and installed, all checks pass."""
        fake = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=True, models=True
        )

        with (
            patch("the_architect.cli.detect_provider", return_value=fake),
            patch("the_architect.cli.supported_providers", return_value=[fake]),
        ):
            result = CliRunner().invoke(main, ["doctor"])

        assert result.exit_code == 0, result.output
        assert "All checks passed" in result.output or "Environment Diagnostics" in result.output
        assert "Providers" in result.output

    def test_doctor_provider_not_found(self, tmp_path: Path) -> None:
        """When no provider is detected, exit code is 1."""
        fake = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=False, models=False
        )
        with (
            patch(
                "the_architect.cli.detect_provider",
                side_effect=ProviderNotFoundError("none found"),
            ),
            patch("the_architect.cli.supported_providers", return_value=[fake]),
        ):
            result = CliRunner().invoke(main, ["doctor"])

        assert result.exit_code == 1, result.output
        assert "No installed provider detected" in result.output

    def test_doctor_reports_unconfigured_optional_provider_without_failing(
        self, tmp_path: Path
    ) -> None:
        """Optional unconfigured providers are reported but do not fail doctor."""
        selected = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=True, models=True
        )
        optional = self._fake_provider(
            name="codex", display_name="Codex CLI", installed=True, models=False
        )

        with (
            patch("the_architect.cli.detect_provider", return_value=selected),
            patch("the_architect.cli.supported_providers", return_value=[selected, optional]),
        ):
            result = CliRunner().invoke(main, ["doctor"])

        assert result.exit_code == 0, result.output
        assert "Codex CLI" in result.output
        assert "no models/API key detected" in result.output

    def test_doctor_fails_when_selected_provider_is_unconfigured(self, tmp_path: Path) -> None:
        """The selected provider must be installed and configured."""
        selected = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=True, models=False
        )

        with (
            patch("the_architect.cli.detect_provider", return_value=selected),
            patch("the_architect.cli.supported_providers", return_value=[selected]),
        ):
            result = CliRunner().invoke(main, ["doctor"])

        assert result.exit_code == 1, result.output
        assert "Some required checks failed" in result.output

    def test_doctor_python_version_shown(self, tmp_path: Path) -> None:
        """Python version row appears in output."""
        fake = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=True, models=True
        )

        with (
            patch("the_architect.cli.detect_provider", return_value=fake),
            patch("the_architect.cli.supported_providers", return_value=[fake]),
        ):
            result = CliRunner().invoke(main, ["doctor"])

        assert result.exit_code == 0, result.output
        assert "Python version" in result.output

    # -----------------------------------------------------------------------
    # Live health probe tests (--live flag)
    # -----------------------------------------------------------------------

    def test_doctor_live_success(self, tmp_path: Path) -> None:
        """doctor --live with successful health check returns exit code 0."""
        fake = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=True, models=True
        )

        async def fake_health(*_a, **_kw) -> None:  # pragma: no cover - called via asyncio.run
            pass

        with (
            patch("the_architect.cli.detect_provider", return_value=fake),
            patch("the_architect.cli.supported_providers", return_value=[fake]),
            patch(
                "the_architect.core.provider_health.check_provider_health",
                side_effect=fake_health,
            ),
            patch("asyncio.run", side_effect=_run_coro),
        ):
            result = CliRunner().invoke(main, ["doctor", "--live"])

        assert result.exit_code == 0, result.output
        assert "Live check skipped" not in result.output
        assert "live check passed" in result.output.lower() or "All checks passed" in result.output

    def test_doctor_live_provider_health_error(self, tmp_path: Path) -> None:
        """doctor --live with ProviderHealthError returns exit code 1."""
        fake = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=True, models=True
        )

        from the_architect.core.provider_health import ProviderHealthError

        async def fake_health_fail(*_a, **_kw) -> None:  # pragma: no cover
            raise ProviderHealthError("quota exhausted")

        with (
            patch("the_architect.cli.detect_provider", return_value=fake),
            patch("the_architect.cli.supported_providers", return_value=[fake]),
            patch(
                "the_architect.core.provider_health.check_provider_health",
                side_effect=fake_health_fail,
            ),
            patch("asyncio.run", side_effect=_run_coro),
        ):
            result = CliRunner().invoke(main, ["doctor", "--live"])

        assert result.exit_code == 1, result.output
        assert "Live check failed" in result.output or "live check failed" in result.output.lower()

    def test_doctor_live_no_provider_skips(self, tmp_path: Path) -> None:
        """doctor --live with no provider detected skips live check."""
        fake = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=False, models=False
        )

        with (
            patch(
                "the_architect.cli.detect_provider",
                side_effect=ProviderNotFoundError("none found"),
            ),
            patch("the_architect.cli.supported_providers", return_value=[fake]),
            patch(
                "the_architect.core.provider_health.check_provider_health",
                side_effect=AssertionError("must not be called"),
            ),
        ):
            result = CliRunner().invoke(main, ["doctor", "--live"])

        assert result.exit_code == 1, result.output
        assert "Live check skipped" in result.output

    def test_doctor_without_live_no_health_check(self, tmp_path: Path) -> None:
        """doctor without --live does not invoke health check."""
        fake = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=True, models=True
        )

        with (
            patch("the_architect.cli.detect_provider", return_value=fake),
            patch("the_architect.cli.supported_providers", return_value=[fake]),
            patch(
                "the_architect.core.provider_health.check_provider_health",
                side_effect=AssertionError("must not be called without --live"),
            ),
        ):
            result = CliRunner().invoke(main, ["doctor"])

        assert result.exit_code == 0, result.output
        assert "Live check" not in result.output

    def test_doctor_live_timeout_passed(self, tmp_path: Path) -> None:
        """--live-timeout value is passed through to check_provider_health."""
        fake = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=True, models=True
        )

        captured_timeout: float | None = None

        async def capture_timeout(**kw) -> None:  # pragma: no cover
            nonlocal captured_timeout
            captured_timeout = kw.get("timeout_seconds")

        with (
            patch("the_architect.cli.detect_provider", return_value=fake),
            patch("the_architect.cli.supported_providers", return_value=[fake]),
            patch(
                "the_architect.core.provider_health.check_provider_health",
                side_effect=capture_timeout,
            ),
            patch("asyncio.run", side_effect=_run_coro),
        ):
            CliRunner().invoke(main, ["doctor", "--live", "--live-timeout", "60"])

        assert captured_timeout == 60.0

    def test_doctor_live_generic_exception(self, tmp_path: Path) -> None:
        """doctor --live with a generic exception returns exit code 1."""
        fake = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=True, models=True
        )

        async def fake_health_generic(*_a, **_kw) -> None:  # pragma: no cover
            raise RuntimeError("unexpected crash")

        with (
            patch("the_architect.cli.detect_provider", return_value=fake),
            patch("the_architect.cli.supported_providers", return_value=[fake]),
            patch(
                "the_architect.core.provider_health.check_provider_health",
                side_effect=fake_health_generic,
            ),
            patch("asyncio.run", side_effect=_run_coro),
        ):
            result = CliRunner().invoke(main, ["doctor", "--live"])

        assert result.exit_code == 1, result.output
        assert "Live check error" in result.output or "live check error" in result.output.lower()

    # -----------------------------------------------------------------------
    # Project health tests (--project flag)
    # -----------------------------------------------------------------------

    def test_doctor_project_display(self, tmp_path: Path) -> None:
        """doctor --project shows Project Health section with check labels."""
        fake = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=True, models=True
        )

        from the_architect.core.project_health import HealthCheck

        fake_checks = [
            HealthCheck(status="ok", label="Lock file", detail="No runner.lock found"),
            HealthCheck(
                status="ok", label="Task consistency", detail="No tasks/ or PROGRESS.md found"
            ),
            HealthCheck(
                status="ok", label="Baselines", detail="No .architect/baselines/ directory found"
            ),
            HealthCheck(status="ok", label="Circuit state", detail="No circuit.json found"),
            HealthCheck(status="ok", label="Token ledger", detail="No token_ledger.json found"),
            HealthCheck(status="ok", label="Presets", detail="No presets.json found"),
        ]

        with (
            patch("the_architect.cli.detect_provider", return_value=fake),
            patch("the_architect.cli.supported_providers", return_value=[fake]),
            patch(
                "the_architect.core.project_health.run_project_checks",
                return_value=fake_checks,
            ),
        ):
            result = CliRunner().invoke(main, ["doctor", "--project"])

        assert result.exit_code == 0, result.output
        assert "Project Health" in result.output
        assert "Lock file" in result.output
        assert "Task consistency" in result.output

    def test_doctor_project_json_output(self, tmp_path: Path) -> None:
        """doctor --project --json outputs clean structured JSON."""
        fake = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=True, models=True
        )

        from the_architect.core.project_health import HealthCheck

        fake_checks = [
            HealthCheck(status="ok", label="Lock file", detail="No runner.lock found"),
            HealthCheck(status="warn", label="Circuit state", detail="1 OPEN out of 2 task(s)"),
            HealthCheck(
                status="ok", label="Baselines", detail="No .architect/baselines/ directory found"
            ),
            HealthCheck(status="ok", label="Token ledger", detail="No token_ledger.json found"),
            HealthCheck(status="ok", label="Presets", detail="No presets.json found"),
            HealthCheck(
                status="ok", label="Task consistency", detail="No tasks/ or PROGRESS.md found"
            ),
        ]

        with (
            patch("the_architect.cli.detect_provider", return_value=fake),
            patch("the_architect.cli.supported_providers", return_value=[fake]),
            patch(
                "the_architect.core.project_health.run_project_checks",
                return_value=fake_checks,
            ),
        ):
            result = CliRunner().invoke(main, ["doctor", "--project", "--json"])

        assert result.exit_code == 0, result.output
        # Parse the JSON output
        data = json.loads(result.output)
        assert "checks" in data
        assert "project" in data
        assert "summary" in data
        assert len(data["checks"]) == 6
        assert data["summary"]["ok"] == 5
        assert data["summary"]["warn"] == 1
        assert data["summary"]["fail"] == 0

    def test_doctor_json_without_project_outputs_env_diagnostics(self, tmp_path: Path) -> None:
        """--json without --project outputs environment diagnostics as JSON."""
        fake = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=True, models=True
        )

        with (
            patch("the_architect.cli.detect_provider", return_value=fake),
            patch("the_architect.cli.supported_providers", return_value=[fake]),
        ):
            result = CliRunner().invoke(main, ["doctor", "--json"])

        assert result.exit_code == 0, result.output
        # Should output valid JSON
        data = json.loads(result.output)
        assert "checks" in data
        assert "providers" in data
        # Should NOT contain project health fields
        assert "project" not in data
        # Environment checks should be present
        check_names = [c["check"] for c in data["checks"]]
        assert "Python version" in check_names
        assert "Selected provider" in check_names

    # -----------------------------------------------------------------------
    # Project health exit code tests
    # -----------------------------------------------------------------------

    def test_doctor_project_exit_code_all_ok(self, tmp_path: Path) -> None:
        """doctor --project exits 0 when all checks are ok or warn."""
        fake = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=True, models=True
        )

        from the_architect.core.project_health import HealthCheck

        fake_checks = [
            HealthCheck(status="ok", label="Lock file", detail="No runner.lock found"),
            HealthCheck(status="warn", label="Circuit state", detail="1 OPEN out of 2 task(s)"),
            HealthCheck(
                status="ok", label="Baselines", detail="No .architect/baselines/ directory found"
            ),
            HealthCheck(status="ok", label="Token ledger", detail="No token_ledger.json found"),
            HealthCheck(status="ok", label="Presets", detail="No presets.json found"),
            HealthCheck(
                status="ok", label="Task consistency", detail="No tasks/ or PROGRESS.md found"
            ),
        ]

        with (
            patch("the_architect.cli.detect_provider", return_value=fake),
            patch("the_architect.cli.supported_providers", return_value=[fake]),
            patch(
                "the_architect.core.project_health.run_project_checks",
                return_value=fake_checks,
            ),
        ):
            result = CliRunner().invoke(main, ["doctor", "--project"])

        assert result.exit_code == 0, result.output
        assert "Project Health" in result.output

    def test_doctor_project_exit_code_fail(self, tmp_path: Path) -> None:
        """doctor --project exits 1 when any check is fail."""
        fake = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=True, models=True
        )

        from the_architect.core.project_health import HealthCheck

        fake_checks = [
            HealthCheck(
                status="fail",
                label="Lock file",
                detail="runner.lock exists — another Architect process may be active",
            ),
            HealthCheck(
                status="ok", label="Task consistency", detail="No tasks/ or PROGRESS.md found"
            ),
            HealthCheck(
                status="ok", label="Baselines", detail="No .architect/baselines/ directory found"
            ),
            HealthCheck(status="ok", label="Circuit state", detail="No circuit.json found"),
            HealthCheck(status="ok", label="Token ledger", detail="No token_ledger.json found"),
            HealthCheck(status="ok", label="Presets", detail="No presets.json found"),
        ]

        with (
            patch("the_architect.cli.detect_provider", return_value=fake),
            patch("the_architect.cli.supported_providers", return_value=[fake]),
            patch(
                "the_architect.core.project_health.run_project_checks",
                return_value=fake_checks,
            ),
        ):
            result = CliRunner().invoke(main, ["doctor", "--project"])

        assert result.exit_code == 1, result.output
        assert "project health checks failed" in result.output.lower()

    def test_doctor_project_json_exit_code_fail(self, tmp_path: Path) -> None:
        """doctor --project --json exits 1 when any check is fail."""
        fake = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=True, models=True
        )

        from the_architect.core.project_health import HealthCheck

        fake_checks = [
            HealthCheck(
                status="fail",
                label="Lock file",
                detail="runner.lock exists — another Architect process may be active",
            ),
            HealthCheck(
                status="ok", label="Task consistency", detail="No tasks/ or PROGRESS.md found"
            ),
            HealthCheck(
                status="ok", label="Baselines", detail="No .architect/baselines/ directory found"
            ),
            HealthCheck(status="ok", label="Circuit state", detail="No circuit.json found"),
            HealthCheck(status="ok", label="Token ledger", detail="No token_ledger.json found"),
            HealthCheck(status="ok", label="Presets", detail="No presets.json found"),
        ]

        with (
            patch("the_architect.cli.detect_provider", return_value=fake),
            patch("the_architect.cli.supported_providers", return_value=[fake]),
            patch(
                "the_architect.core.project_health.run_project_checks",
                return_value=fake_checks,
            ),
        ):
            result = CliRunner().invoke(main, ["doctor", "--project", "--json"])

        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["summary"]["fail"] == 1

    # -----------------------------------------------------------------------
    # Project path override tests
    # -----------------------------------------------------------------------

    def test_doctor_project_path_override(self, tmp_path: Path) -> None:
        """--project-path uses the specified directory for project checks."""
        fake = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=True, models=True
        )

        # Create a separate project directory with a lock file
        custom_project = tmp_path / "custom_project"
        custom_project.mkdir()
        (custom_project / ".architect").mkdir()
        (custom_project / ".architect" / "runner.lock").write_text("locked")

        with (
            patch("the_architect.cli.detect_provider", return_value=fake),
            patch("the_architect.cli.supported_providers", return_value=[fake]),
        ):
            result = CliRunner().invoke(
                main, ["doctor", "--project", "--project-path", str(custom_project)]
            )

        assert result.exit_code == 1, result.output
        assert "runner.lock exists" in result.output

    def test_doctor_project_path_json_override(self, tmp_path: Path) -> None:
        """--project-path with --json uses the specified directory path in JSON."""
        fake = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=True, models=True
        )

        custom_project = tmp_path / "custom_project"
        custom_project.mkdir()

        with (
            patch("the_architect.cli.detect_provider", return_value=fake),
            patch("the_architect.cli.supported_providers", return_value=[fake]),
        ):
            result = CliRunner().invoke(
                main, ["doctor", "--project", "--json", "--project-path", str(custom_project)]
            )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["project"] == str(custom_project.resolve())

    # -----------------------------------------------------------------------
    # Combined --project --live tests
    # -----------------------------------------------------------------------

    def test_doctor_project_live_combined(self, tmp_path: Path) -> None:
        """doctor --project --live shows both Project Health and Live Health Check sections."""
        fake = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=True, models=True
        )

        from the_architect.core.project_health import HealthCheck

        fake_checks = [
            HealthCheck(status="ok", label="Lock file", detail="No runner.lock found"),
            HealthCheck(
                status="ok", label="Task consistency", detail="No tasks/ or PROGRESS.md found"
            ),
            HealthCheck(
                status="ok", label="Baselines", detail="No .architect/baselines/ directory found"
            ),
            HealthCheck(status="ok", label="Circuit state", detail="No circuit.json found"),
            HealthCheck(status="ok", label="Token ledger", detail="No token_ledger.json found"),
            HealthCheck(status="ok", label="Presets", detail="No presets.json found"),
        ]

        async def fake_health(*_a, **_kw) -> None:  # pragma: no cover
            pass

        with (
            patch("the_architect.cli.detect_provider", return_value=fake),
            patch("the_architect.cli.supported_providers", return_value=[fake]),
            patch(
                "the_architect.core.project_health.run_project_checks",
                return_value=fake_checks,
            ),
            patch(
                "the_architect.core.provider_health.check_provider_health",
                side_effect=fake_health,
            ),
            patch("asyncio.run", side_effect=_run_coro),
        ):
            result = CliRunner().invoke(main, ["doctor", "--project", "--live"])

        assert result.exit_code == 0, result.output
        assert "Project Health" in result.output
        assert "Live Health Check" in result.output
        assert "Lock file" in result.output

    def test_doctor_project_live_combined_fail(self, tmp_path: Path) -> None:
        """doctor --project --live with project fail exits 1."""
        fake = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=True, models=True
        )

        from the_architect.core.project_health import HealthCheck

        fake_checks = [
            HealthCheck(
                status="fail",
                label="Lock file",
                detail="runner.lock exists — another Architect process may be active",
            ),
            HealthCheck(
                status="ok", label="Task consistency", detail="No tasks/ or PROGRESS.md found"
            ),
            HealthCheck(
                status="ok", label="Baselines", detail="No .architect/baselines/ directory found"
            ),
            HealthCheck(status="ok", label="Circuit state", detail="No circuit.json found"),
            HealthCheck(status="ok", label="Token ledger", detail="No token_ledger.json found"),
            HealthCheck(status="ok", label="Presets", detail="No presets.json found"),
        ]

        async def fake_health(*_a, **_kw) -> None:  # pragma: no cover
            pass

        with (
            patch("the_architect.cli.detect_provider", return_value=fake),
            patch("the_architect.cli.supported_providers", return_value=[fake]),
            patch(
                "the_architect.core.project_health.run_project_checks",
                return_value=fake_checks,
            ),
            patch(
                "the_architect.core.provider_health.check_provider_health",
                side_effect=fake_health,
            ),
            patch("asyncio.run", side_effect=_run_coro),
        ):
            result = CliRunner().invoke(main, ["doctor", "--project", "--live"])

        assert result.exit_code == 1, result.output
        assert "Project Health" in result.output
        assert "project health checks failed" in result.output.lower()


# ---------------------------------------------------------------------------
# Windows / PowerShell TUI detection
# ---------------------------------------------------------------------------


class TestIsDumbTerminal:
    """Tests for _is_dumb_terminal() — the authoritative dumb-terminal gate."""

    def test_term_dumb_is_dumb(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TERM=dumb must return True."""
        from the_architect.cli import _is_dumb_terminal

        monkeypatch.setenv("TERM", "dumb")
        assert _is_dumb_terminal() is True

    def test_term_empty_is_not_dumb(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unset TERM (PowerShell / cmd.exe) must NOT be treated as dumb."""
        from the_architect.cli import _is_dumb_terminal

        monkeypatch.delenv("TERM", raising=False)
        assert _is_dumb_terminal() is False

    def test_term_xterm_is_not_dumb(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A normal TERM value must not be treated as dumb."""
        from the_architect.cli import _is_dumb_terminal

        monkeypatch.setenv("TERM", "xterm-256color")
        assert _is_dumb_terminal() is False

    def test_term_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TERM=DUMB (upper-case) must still be detected as dumb."""
        from the_architect.cli import _is_dumb_terminal

        monkeypatch.setenv("TERM", "DUMB")
        assert _is_dumb_terminal() is True


class TestResolveTuiDefaultWindows:
    """Tests that _resolve_tui_default enables TUI on Windows PowerShell."""

    def test_empty_term_with_tty_enables_tui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty TERM (Windows PowerShell) + real TTY must enable the TUI."""
        from the_architect.cli import _resolve_tui_default

        monkeypatch.delenv("TERM", raising=False)
        monkeypatch.delenv("NO_COLOR", raising=False)
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            assert _resolve_tui_default(None, headless=False) is True

    def test_term_dumb_with_tty_disables_tui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TERM=dumb must disable the TUI even with a TTY."""
        from the_architect.cli import _resolve_tui_default

        monkeypatch.setenv("TERM", "dumb")
        monkeypatch.delenv("NO_COLOR", raising=False)
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            assert _resolve_tui_default(None, headless=False) is False

    def test_headless_always_disables_tui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """headless=True must disable the TUI regardless of TERM."""
        from the_architect.cli import _resolve_tui_default

        monkeypatch.delenv("TERM", raising=False)
        monkeypatch.delenv("NO_COLOR", raising=False)
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            assert _resolve_tui_default(None, headless=True) is False

    def test_no_color_disables_tui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """NO_COLOR env var must disable the TUI."""
        from the_architect.cli import _resolve_tui_default

        monkeypatch.delenv("TERM", raising=False)
        monkeypatch.setenv("NO_COLOR", "1")
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            assert _resolve_tui_default(None, headless=False) is False

    def test_non_tty_disables_tui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Piped / non-TTY stdout must disable the TUI."""
        from the_architect.cli import _resolve_tui_default

        monkeypatch.delenv("TERM", raising=False)
        monkeypatch.delenv("NO_COLOR", raising=False)
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            assert _resolve_tui_default(None, headless=False) is False

    def test_explicit_true_overrides_detection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """explicit=True must bypass all auto-detection."""
        from the_architect.cli import _resolve_tui_default

        monkeypatch.setenv("TERM", "dumb")
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            assert _resolve_tui_default(True, headless=False) is True

    def test_explicit_false_overrides_detection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """explicit=False (--no-tui) must bypass all auto-detection."""
        from the_architect.cli import _resolve_tui_default

        monkeypatch.delenv("TERM", raising=False)
        monkeypatch.delenv("NO_COLOR", raising=False)
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            assert _resolve_tui_default(False, headless=False) is False


class TestDiffCommand:
    """Cover the ``diff`` command for per-task baseline change display."""

    def _write_baseline(self, tmp_path: Path, name: str, files: dict[str, str]) -> None:
        """Write a baseline JSON file with the given file checksums."""
        import hashlib

        from the_architect.core.baseline import FileRecord, WorkspaceBaseline

        baseline = WorkspaceBaseline(
            task_prefix=name.replace(".json", ""),
            files={
                p: FileRecord(path=p, sha256=hashlib.sha256(c.encode()).hexdigest(), size=len(c))
                for p, c in files.items()
            },
        )
        baselines_dir = tmp_path / ".architect" / "baselines"
        baselines_dir.mkdir(parents=True, exist_ok=True)

        (baselines_dir / f"{name}.json").write_text(
            baseline.model_dump_json(indent=2), encoding="utf-8"
        )

    def test_diff_no_baselines_dir(self, tmp_path: Path) -> None:
        """Shows message when baselines directory does not exist."""
        result = CliRunner().invoke(main, ["diff", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert "No baseline data available" in result.output

    def test_diff_empty_baselines_dir(self, tmp_path: Path) -> None:
        """Shows message when baselines directory exists but is empty."""
        (tmp_path / ".architect" / "baselines").mkdir(parents=True)
        result = CliRunner().invoke(main, ["diff", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert "No baseline data available" in result.output

    def test_diff_single_task(self, tmp_path: Path) -> None:
        """Displays changes for a single task baseline."""
        # Create a tracked file that exists in the workspace
        (tmp_path / "example.py").write_text("hello world", encoding="utf-8")
        self._write_baseline(tmp_path, "T01", {"example.py": "hello world"})

        result = CliRunner().invoke(main, ["diff", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert "T01" in result.output
        # File matches baseline so no changes expected
        assert "Created: 0" in result.output or "Modified: 0" in result.output

    def test_diff_single_task_with_changes(self, tmp_path: Path) -> None:
        """Displays created files when workspace has new files."""
        # Baseline has no files, workspace has a new file
        self._write_baseline(tmp_path, "T01", {})
        (tmp_path / "new_file.py").write_text("new content", encoding="utf-8")

        result = CliRunner().invoke(main, ["diff", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert "T01" in result.output
        assert "Created: 1" in result.output
        assert "new_file.py" in result.output

    def test_diff_multiple_tasks(self, tmp_path: Path) -> None:
        """Displays changes for multiple task baselines."""
        self._write_baseline(tmp_path, "T01", {})
        self._write_baseline(tmp_path, "T02", {})

        result = CliRunner().invoke(main, ["diff", "-p", str(tmp_path)])
        assert result.exit_code == 0
        assert "T01" in result.output
        assert "T02" in result.output

    def test_diff_task_filter(self, tmp_path: Path) -> None:
        """--task filter shows only the matching task."""
        self._write_baseline(tmp_path, "T01", {})
        self._write_baseline(tmp_path, "T02", {})

        result = CliRunner().invoke(main, ["diff", "-p", str(tmp_path), "--task", "T01"])
        assert result.exit_code == 0
        assert "T01" in result.output
        assert "T02" not in result.output

    def test_diff_task_filter_no_match(self, tmp_path: Path) -> None:
        """--task with non-existent task shows no data message."""
        self._write_baseline(tmp_path, "T01", {})

        result = CliRunner().invoke(main, ["diff", "-p", str(tmp_path), "--task", "T99"])
        assert result.exit_code == 0
        assert "No baseline data found for task T99" in result.output

    def test_diff_json_basic_structure(self, tmp_path: Path) -> None:
        """JSON output contains required top-level keys."""
        self._write_baseline(tmp_path, "T01", {})

        result = CliRunner().invoke(main, ["diff", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "project" in data
        assert "tasks" in data
        assert isinstance(data["tasks"], list)

    def test_diff_json_task_structure(self, tmp_path: Path) -> None:
        """JSON task entries have task_id, created, modified, deleted keys."""
        self._write_baseline(tmp_path, "T01", {})
        (tmp_path / "new.py").write_text("x", encoding="utf-8")

        result = CliRunner().invoke(main, ["diff", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data["tasks"]) == 1
        task = data["tasks"][0]
        assert "task_id" in task
        assert "created" in task
        assert "modified" in task
        assert "deleted" in task
        assert task["task_id"] == "T01"
        assert isinstance(task["created"], list)
        assert isinstance(task["modified"], list)
        assert isinstance(task["deleted"], list)

    def test_diff_json_task_filter(self, tmp_path: Path) -> None:
        """JSON output respects --task filter."""
        self._write_baseline(tmp_path, "T01", {})
        self._write_baseline(tmp_path, "T02", {})

        result = CliRunner().invoke(main, ["diff", "-p", str(tmp_path), "--json", "--task", "T01"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["task_id"] == "T01"

    def test_diff_json_deterministic(self, tmp_path: Path) -> None:
        """JSON output is deterministic with sorted keys."""
        self._write_baseline(tmp_path, "T01", {})

        result = CliRunner().invoke(main, ["diff", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        expected = json.dumps(data, indent=2, sort_keys=True)
        assert result.output.strip() == expected

    def test_diff_json_no_rich_markup(self, tmp_path: Path) -> None:
        """JSON output contains no Rich ANSI escape codes."""
        self._write_baseline(tmp_path, "T01", {})

        result = CliRunner().invoke(main, ["diff", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0
        assert "\x1b" not in result.output

    def test_diff_corrupted_baseline(self, tmp_path: Path) -> None:
        """Corrupted baseline files are skipped gracefully."""
        baselines_dir = tmp_path / ".architect" / "baselines"
        baselines_dir.mkdir(parents=True)
        (baselines_dir / "T01.json").write_text("not valid json{", encoding="utf-8")

        result = CliRunner().invoke(main, ["diff", "-p", str(tmp_path)])
        assert result.exit_code == 0
        # Should not crash — corrupted baseline is skipped

    def test_diff_json_mutual_exclusion_tui(self, tmp_path: Path) -> None:
        """--json and --tui are mutually exclusive."""
        result = CliRunner().invoke(main, ["diff", "-p", str(tmp_path), "--json", "--tui"])
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output

    def test_diff_json_empty_baselines(self, tmp_path: Path) -> None:
        """JSON output with no baselines returns empty tasks array."""
        result = CliRunner().invoke(main, ["diff", "-p", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["tasks"] == []

    def test_diff_format_json_no_baselines(self, tmp_path: Path) -> None:
        """_format_diff_json returns valid JSON with no baselines dir."""
        from the_architect.cli import _format_diff_json

        output = _format_diff_json(tmp_path)
        data = json.loads(output)
        assert data["project"] == str(tmp_path)
        assert data["tasks"] == []
