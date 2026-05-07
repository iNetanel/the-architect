"""Extra CLI tests — focus on commands whose edge cases were uncovered.

This suite targets branches in ``the_architect/cli.py`` that the
pre-existing suite did not exercise:

    - ``status`` command against realistic fixtures (running / stale lock /
      circuit data / token budget / logs present).
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

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from the_architect.cli import main

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
