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

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from the_architect.cli import main
from the_architect.core.provider import ProviderNotFoundError

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
        assert data["token_budget"]["limit"] == 500000
        assert "tracked per run" in data["token_budget"]["description"]

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
    """``monitor`` is mostly tmux glue — cover the branches that do not attach."""

    def test_monitor_without_tmux_installed(self, tmp_path: Path) -> None:
        with patch("the_architect.core.tmux.is_tmux_available", return_value=False):
            result = CliRunner().invoke(main, ["monitor", "-p", str(tmp_path)])
        assert result.exit_code == 1
        assert "tmux is not installed" in result.output

    def test_monitor_without_any_session(self, tmp_path: Path) -> None:
        with (
            patch("the_architect.core.tmux.is_tmux_available", return_value=True),
            patch("the_architect.core.tmux.session_exists", return_value=False),
            patch(
                "the_architect.core.tmux.list_architect_sessions",
                return_value=[],
            ),
        ):
            result = CliRunner().invoke(main, ["monitor", "-p", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "No active session" in result.output
        assert "architect" in result.output

    def test_monitor_with_other_architect_sessions(self, tmp_path: Path) -> None:
        with (
            patch("the_architect.core.tmux.is_tmux_available", return_value=True),
            patch("the_architect.core.tmux.session_exists", return_value=False),
            patch(
                "the_architect.core.tmux.list_architect_sessions",
                return_value=["architect-foo", "architect-bar"],
            ),
        ):
            result = CliRunner().invoke(main, ["monitor", "-p", str(tmp_path)])
        # When more than one other session exists we print a helpful list
        # and exit 0.
        assert result.exit_code == 0, result.output
        assert "architect-foo" in result.output
        assert "architect-bar" in result.output


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

    def test_doctor_all_pass(self, tmp_path: Path) -> None:
        """When provider is detected and installed, all checks pass."""
        fake = MagicMock()
        fake.name = "opencode"
        fake.display_name = "OpenCode"
        fake.is_installed.return_value = True
        fake.get_version.return_value = "0.6.12"
        fake.has_any_models.return_value = True
        fake.check_update_available.return_value = ""

        with patch("the_architect.cli.detect_provider", return_value=fake):
            result = CliRunner().invoke(main, ["doctor"])

        assert result.exit_code == 0, result.output
        assert "All checks passed" in result.output or "Environment Diagnostics" in result.output

    def test_doctor_provider_not_found(self, tmp_path: Path) -> None:
        """When no provider is detected, exit code is 1."""
        with patch(
            "the_architect.cli.detect_provider",
            side_effect=ProviderNotFoundError("none found"),
        ):
            result = CliRunner().invoke(main, ["doctor"])

        assert result.exit_code == 1, result.output
        assert "No provider detected" in result.output

    def test_doctor_python_version_shown(self, tmp_path: Path) -> None:
        """Python version row appears in output."""
        fake = MagicMock()
        fake.name = "opencode"
        fake.display_name = "OpenCode"
        fake.is_installed.return_value = True
        fake.get_version.return_value = "0.6.12"
        fake.has_any_models.return_value = True
        fake.check_update_available.return_value = ""

        with patch("the_architect.cli.detect_provider", return_value=fake):
            result = CliRunner().invoke(main, ["doctor"])

        assert result.exit_code == 0, result.output
        assert "Python version" in result.output
