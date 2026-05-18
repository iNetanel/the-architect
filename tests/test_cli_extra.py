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


# ---------------------------------------------------------------------------
# reset — extra branches
# ---------------------------------------------------------------------------


class TestResetCmdMissingProgress:
    def test_reset_without_progress_file(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(main, ["reset", "-p", str(tmp_path)], input="y\n")
        assert result.exit_code == 1
        assert "PROGRESS.md not found" in result.output


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

    def test_doctor_json_without_project_ignored(self, tmp_path: Path) -> None:
        """--json without --project is silently ignored — environment diagnostics still run."""
        fake = self._fake_provider(
            name="opencode", display_name="OpenCode", installed=True, models=True
        )

        with (
            patch("the_architect.cli.detect_provider", return_value=fake),
            patch("the_architect.cli.supported_providers", return_value=[fake]),
        ):
            result = CliRunner().invoke(main, ["doctor", "--json"])

        assert result.exit_code == 0, result.output
        # Should show environment diagnostics, not JSON
        assert "Environment Diagnostics" in result.output
        assert "Providers" in result.output
        # Should NOT have the project health JSON structure
        data = None
        try:
            data = json.loads(result.output)
        except (json.JSONDecodeError, ValueError):
            pass
        if data is not None:
            assert "checks" not in data

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
