"""Tests for the ``architect hooks`` CLI command group.

Covers:
- hooks command group registration in help
- hooks list — empty, with hooks, --json output
- hooks add — basic, custom timeout, multiple adds
- hooks remove — valid index, out of range, empty store
- hooks run — no hooks, with hooks, --json output, event filtering
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from the_architect.cli import main
from the_architect.core.hooks import (
    HOOKS_FILE,
    HookConfig,
    HookEvent,
    add_hook,
    list_hooks,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def _write_raw_hooks(project: Path, data: object) -> None:
    """Write raw hooks data directly to disk."""
    hooks_path = project / HOOKS_FILE
    hooks_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        hooks_path.write_text(data, encoding="utf-8")
    else:
        hooks_path.write_text(json.dumps(data), encoding="utf-8")


def _make_hook_dict(
    event: str = "pre_run",
    command: str = "echo hello",
    enabled: bool = True,
    timeout: int = 30,
) -> dict[str, object]:
    """Build a valid hook dict for test data."""
    return {
        "event": event,
        "command": command,
        "enabled": enabled,
        "timeout": timeout,
    }


# ---------------------------------------------------------------------------
# CLI — architect hooks command group
# ---------------------------------------------------------------------------


class TestHooksCLI:
    """Tests for the ``architect hooks`` CLI command group.

    Note: The ``-p`` / ``--project`` option lives on each sub-command, not
    on the ``hooks`` group.  So the correct invocation is:
    ``hooks <sub-cmd> -p <path> ...``
    """

    def test_hooks_in_help(self, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "hooks" in result.output

    # -- hooks list ------------------------------------------------------------

    def test_list_empty(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["hooks", "list", "-p", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "No hooks configured" in result.output

    def test_list_with_hooks(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        add_hook(tmp_path, HookConfig(event=HookEvent.pre_run, command="echo start"))
        add_hook(tmp_path, HookConfig(event=HookEvent.post_task, command="echo done"))
        result = cli_runner.invoke(
            main,
            ["hooks", "list", "-p", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "pre_run" in result.output
        assert "post_task" in result.output
        assert "echo start" in result.output
        assert "echo done" in result.output
        assert "0" in result.output
        assert "1" in result.output

    def test_list_json_empty(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["hooks", "list", "-p", str(tmp_path), "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["hooks"] == []
        assert "project" in payload

    def test_list_json_with_hooks(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        add_hook(tmp_path, HookConfig(event=HookEvent.pre_run, command="echo start"))
        result = cli_runner.invoke(
            main,
            ["hooks", "list", "-p", str(tmp_path), "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload["hooks"]) == 1
        assert payload["hooks"][0]["event"] == "pre_run"
        assert payload["hooks"][0]["command"] == "echo start"

    # -- hooks add -------------------------------------------------------------

    def test_add_basic(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["hooks", "add", "-p", str(tmp_path), "-e", "pre_run", "-c", "echo start"],
        )
        assert result.exit_code == 0, result.output
        hooks = list_hooks(tmp_path)
        assert len(hooks) == 1
        assert hooks[0].event == HookEvent.pre_run
        assert hooks[0].command == "echo start"
        assert hooks[0].timeout == 30

    def test_add_with_timeout(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            [
                "hooks",
                "add",
                "-p",
                str(tmp_path),
                "-e",
                "post_run_success",
                "-c",
                "deploy.sh",
                "-t",
                "120",
            ],
        )
        assert result.exit_code == 0, result.output
        hooks = list_hooks(tmp_path)
        assert len(hooks) == 1
        assert hooks[0].event == HookEvent.post_run_success
        assert hooks[0].command == "deploy.sh"
        assert hooks[0].timeout == 120

    def test_add_multiple_hooks(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        cli_runner.invoke(
            main,
            ["hooks", "add", "-p", str(tmp_path), "-e", "pre_run", "-c", "echo start"],
        )
        cli_runner.invoke(
            main,
            ["hooks", "add", "-p", str(tmp_path), "-e", "post_task", "-c", "echo done"],
        )
        hooks = list_hooks(tmp_path)
        assert len(hooks) == 2
        assert hooks[0].event == HookEvent.pre_run
        assert hooks[1].event == HookEvent.post_task

    def test_add_shows_index(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["hooks", "add", "-p", str(tmp_path), "-e", "pre_run", "-c", "echo hi"],
        )
        assert result.exit_code == 0, result.output
        assert "Hook added" in result.output
        assert "0" in result.output

    def test_add_all_event_types(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        for event in ["pre_run", "post_task", "post_run_success", "post_run_failure"]:
            result = cli_runner.invoke(
                main,
                ["hooks", "add", "-p", str(tmp_path), "-e", event, "-c", f"echo {event}"],
            )
            assert result.exit_code == 0, result.output
        hooks = list_hooks(tmp_path)
        assert len(hooks) == 4

    def test_add_invalid_event_rejected(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["hooks", "add", "-p", str(tmp_path), "-e", "invalid_event", "-c", "echo hi"],
        )
        assert result.exit_code != 0

    def test_add_missing_event_required(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["hooks", "add", "-p", str(tmp_path), "-c", "echo hi"],
        )
        assert result.exit_code != 0

    def test_add_missing_command_required(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["hooks", "add", "-p", str(tmp_path), "-e", "pre_run"],
        )
        assert result.exit_code != 0

    def test_add_json_output(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            [
                "hooks",
                "add",
                "-p",
                str(tmp_path),
                "-e",
                "pre_run",
                "-c",
                "echo start",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["action"] == "added"
        assert payload["index"] == 0
        assert "project" in payload
        assert payload["hook"]["event"] == "pre_run"
        assert payload["hook"]["command"] == "echo start"
        assert payload["hook"]["timeout"] == 30

    def test_add_json_suppresses_rich(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            [
                "hooks",
                "add",
                "-p",
                str(tmp_path),
                "-e",
                "post_task",
                "-c",
                "echo done",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        # JSON output must not contain Rich markup
        assert "[green]" not in result.output
        assert "[cyan]" not in result.output
        # Should be valid JSON
        json.loads(result.output)

    def test_add_json_with_timeout(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            [
                "hooks",
                "add",
                "-p",
                str(tmp_path),
                "-e",
                "post_run_success",
                "-c",
                "deploy.sh",
                "-t",
                "120",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["hook"]["timeout"] == 120
        assert payload["hook"]["event"] == "post_run_success"

    # -- hooks remove ----------------------------------------------------------

    def test_remove_valid_index(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        add_hook(tmp_path, HookConfig(event=HookEvent.pre_run, command="echo first"))
        add_hook(tmp_path, HookConfig(event=HookEvent.post_task, command="echo second"))
        result = cli_runner.invoke(
            main,
            ["hooks", "remove", "-p", str(tmp_path), "-i", "0"],
        )
        assert result.exit_code == 0, result.output
        assert "Hook removed" in result.output
        hooks = list_hooks(tmp_path)
        assert len(hooks) == 1
        assert hooks[0].command == "echo second"

    def test_remove_last_hook(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        add_hook(tmp_path, HookConfig(event=HookEvent.pre_run, command="echo first"))
        add_hook(tmp_path, HookConfig(event=HookEvent.post_task, command="echo second"))
        result = cli_runner.invoke(
            main,
            ["hooks", "remove", "-p", str(tmp_path), "-i", "1"],
        )
        assert result.exit_code == 0, result.output
        hooks = list_hooks(tmp_path)
        assert len(hooks) == 1
        assert hooks[0].command == "echo first"

    def test_remove_out_of_range(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        add_hook(tmp_path, HookConfig(event=HookEvent.pre_run, command="echo hi"))
        result = cli_runner.invoke(
            main,
            ["hooks", "remove", "-p", str(tmp_path), "-i", "5"],
        )
        assert result.exit_code == 1
        assert "out of range" in result.output.lower()

    def test_remove_from_empty_store(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["hooks", "remove", "-p", str(tmp_path), "-i", "0"],
        )
        assert result.exit_code == 1
        assert "out of range" in result.output.lower()

    def test_remove_missing_index_required(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["hooks", "remove", "-p", str(tmp_path)],
        )
        assert result.exit_code != 0

    def test_remove_json_output(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        add_hook(tmp_path, HookConfig(event=HookEvent.pre_run, command="echo first"))
        add_hook(tmp_path, HookConfig(event=HookEvent.post_task, command="echo second"))
        result = cli_runner.invoke(
            main,
            ["hooks", "remove", "-p", str(tmp_path), "-i", "0", "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["action"] == "removed"
        assert payload["index"] == 0
        assert "project" in payload
        assert payload["hook"]["event"] == "pre_run"
        assert payload["hook"]["command"] == "echo first"

    def test_remove_json_suppresses_rich(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        add_hook(tmp_path, HookConfig(event=HookEvent.pre_run, command="echo hi"))
        result = cli_runner.invoke(
            main,
            ["hooks", "remove", "-p", str(tmp_path), "-i", "0", "--json"],
        )
        assert result.exit_code == 0, result.output
        # JSON output must not contain Rich markup
        assert "[green]" not in result.output
        assert "[cyan]" not in result.output
        # Should be valid JSON
        json.loads(result.output)

    def test_remove_json_out_of_range(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        add_hook(tmp_path, HookConfig(event=HookEvent.pre_run, command="echo hi"))
        result = cli_runner.invoke(
            main,
            ["hooks", "remove", "-p", str(tmp_path), "-i", "5", "--json"],
        )
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert "error" in payload
        assert "out of range" in payload["error"]
        assert "project" in payload

    def test_remove_json_empty_store(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["hooks", "remove", "-p", str(tmp_path), "-i", "0", "--json"],
        )
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert "error" in payload
        assert "out of range" in payload["error"]

    # -- hooks run -------------------------------------------------------------

    def test_run_no_hooks(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["hooks", "run", "-p", str(tmp_path), "-e", "pre_run"],
        )
        assert result.exit_code == 0, result.output
        assert "No enabled hooks" in result.output

    def test_run_with_hooks(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        add_hook(tmp_path, HookConfig(event=HookEvent.pre_run, command="echo hello"))
        result = cli_runner.invoke(
            main,
            ["hooks", "run", "-p", str(tmp_path), "-e", "pre_run"],
        )
        assert result.exit_code == 0, result.output
        assert "Ran 1 hook" in result.output
        assert "echo hello" in result.output

    def test_run_filters_by_event(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        add_hook(tmp_path, HookConfig(event=HookEvent.pre_run, command="echo pre"))
        add_hook(tmp_path, HookConfig(event=HookEvent.post_task, command="echo post"))
        result = cli_runner.invoke(
            main,
            ["hooks", "run", "-p", str(tmp_path), "-e", "pre_run"],
        )
        assert result.exit_code == 0, result.output
        assert "Ran 1 hook" in result.output

    def test_run_skips_disabled_hooks(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        add_hook(tmp_path, HookConfig(event=HookEvent.pre_run, command="echo enabled"))
        add_hook(
            tmp_path,
            HookConfig(event=HookEvent.pre_run, command="echo disabled", enabled=False),
        )
        result = cli_runner.invoke(
            main,
            ["hooks", "run", "-p", str(tmp_path), "-e", "pre_run"],
        )
        assert result.exit_code == 0, result.output
        assert "Ran 1 hook" in result.output

    def test_run_json_empty(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["hooks", "run", "-p", str(tmp_path), "-e", "pre_run", "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["results"] == []
        assert payload["event"] == "pre_run"
        assert "project" in payload

    def test_run_json_with_results(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        add_hook(tmp_path, HookConfig(event=HookEvent.pre_run, command="echo test"))
        result = cli_runner.invoke(
            main,
            ["hooks", "run", "-p", str(tmp_path), "-e", "pre_run", "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload["results"]) == 1
        assert payload["results"][0]["command"] == "echo test"
        assert payload["results"][0]["exit_code"] == 0

    def test_run_shows_output(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        add_hook(tmp_path, HookConfig(event=HookEvent.pre_run, command="echo visible_output"))
        result = cli_runner.invoke(
            main,
            ["hooks", "run", "-p", str(tmp_path), "-e", "pre_run"],
        )
        assert result.exit_code == 0, result.output
        assert "visible_output" in result.output

    def test_run_shows_stderr(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        add_hook(
            tmp_path,
            HookConfig(event=HookEvent.pre_run, command="echo err_msg >&2"),
        )
        result = cli_runner.invoke(
            main,
            ["hooks", "run", "-p", str(tmp_path), "-e", "pre_run"],
        )
        assert result.exit_code == 0, result.output
        assert "err_msg" in result.output

    def test_run_shows_nonzero_exit(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        add_hook(tmp_path, HookConfig(event=HookEvent.pre_run, command="exit 42"))
        result = cli_runner.invoke(
            main,
            ["hooks", "run", "-p", str(tmp_path), "-e", "pre_run"],
        )
        assert result.exit_code == 0, result.output
        assert "exit 42" in result.output

    def test_run_missing_event_required(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["hooks", "run", "-p", str(tmp_path)],
        )
        assert result.exit_code != 0

    def test_run_invalid_event_rejected(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(
            main,
            ["hooks", "run", "-p", str(tmp_path), "-e", "invalid_event"],
        )
        assert result.exit_code != 0

    # -- integration ------------------------------------------------------------

    def test_full_crud_cli_roundtrip(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Add -> List -> Remove -> List roundtrip via CLI."""
        # Add
        r = cli_runner.invoke(
            main,
            ["hooks", "add", "-p", str(tmp_path), "-e", "pre_run", "-c", "echo a"],
        )
        assert r.exit_code == 0, r.output

        r = cli_runner.invoke(
            main,
            ["hooks", "add", "-p", str(tmp_path), "-e", "post_task", "-c", "echo b"],
        )
        assert r.exit_code == 0, r.output

        # List
        r = cli_runner.invoke(
            main,
            ["hooks", "list", "-p", str(tmp_path)],
        )
        assert r.exit_code == 0, r.output
        assert "echo a" in r.output
        assert "echo b" in r.output

        # Remove
        r = cli_runner.invoke(
            main,
            ["hooks", "remove", "-p", str(tmp_path), "-i", "0"],
        )
        assert r.exit_code == 0, r.output

        # List again
        r = cli_runner.invoke(
            main,
            ["hooks", "list", "-p", str(tmp_path)],
        )
        assert r.exit_code == 0, r.output
        assert "echo a" not in r.output
        assert "echo b" in r.output

    def test_list_shows_enabled_status(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Enabled hooks show 'on', disabled hooks show 'off'."""
        add_hook(tmp_path, HookConfig(event=HookEvent.pre_run, command="echo on", enabled=True))
        add_hook(
            tmp_path,
            HookConfig(event=HookEvent.post_task, command="echo off", enabled=False),
        )
        result = cli_runner.invoke(
            main,
            ["hooks", "list", "-p", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "on" in result.output.lower()
        assert "off" in result.output.lower()

    def test_list_shows_timeout(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        add_hook(
            tmp_path,
            HookConfig(event=HookEvent.pre_run, command="echo hi", timeout=60),
        )
        result = cli_runner.invoke(
            main,
            ["hooks", "list", "-p", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "60s" in result.output
