"""Tests for the_architect.core.hooks — lifecycle hooks module.

Covers:
- HookEvent StrEnum values
- HookConfig Pydantic model validation and defaults
- HookResult Pydantic model validation and fields
- execute_hook() — success, failure, timeout, FileNotFoundError, generic exception
- execute_hooks_for_event() — filtering by event, enabled/disabled, ordering
- load_hooks() — file exists, missing, corrupted, invalid JSON, validation error
- save_hooks() — creates directory, writes valid JSON, atomic write
- add_hook() — append to existing, create new
- remove_hook() — valid index, out of range, empty list
- list_hooks() — all hooks, empty store, missing store
- Edge cases — shell commands with args, context env vars, output truncation
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from the_architect.core.hooks import (
    _DEFAULT_HOOK_TIMEOUT,
    _OUTPUT_TRUNCATE,
    HOOKS_FILE,
    HookConfig,
    HookEvent,
    HookResult,
    _build_env,
    _hooks_path,
    _now_iso,
    _truncate_output,
    add_hook,
    execute_hook,
    execute_hooks_for_event,
    list_hooks,
    load_hooks,
    remove_hook,
    save_hooks,
)

# ---------------------------------------------------------------------------
# HookEvent enum
# ---------------------------------------------------------------------------


class TestHookEvent:
    """Tests for the HookEvent StrEnum."""

    def test_has_pre_run(self) -> None:
        assert HookEvent.pre_run == "pre_run"

    def test_has_post_task(self) -> None:
        assert HookEvent.post_task == "post_task"

    def test_has_post_run_success(self) -> None:
        assert HookEvent.post_run_success == "post_run_success"

    def test_has_post_run_failure(self) -> None:
        assert HookEvent.post_run_failure == "post_run_failure"

    def test_all_four_events_exist(self) -> None:
        events = list(HookEvent)
        assert len(events) == 4
        values = {e.value for e in events}
        assert values == {"pre_run", "post_task", "post_run_success", "post_run_failure"}

    def test_str_comparison(self) -> None:
        assert HookEvent.pre_run == "pre_run"

    def test_from_string(self) -> None:
        event = HookEvent("post_task")
        assert event == HookEvent.post_task


# ---------------------------------------------------------------------------
# HookConfig model
# ---------------------------------------------------------------------------


class TestHookConfigModel:
    """Tests for the HookConfig Pydantic model."""

    def test_create_with_all_fields(self) -> None:
        hook = HookConfig(
            event=HookEvent.pre_run,
            command="echo hello",
            enabled=True,
            timeout=60,
        )
        assert hook.event == HookEvent.pre_run
        assert hook.command == "echo hello"
        assert hook.enabled is True
        assert hook.timeout == 60

    def test_default_enabled_is_true(self) -> None:
        hook = HookConfig(event=HookEvent.post_task, command="echo done")
        assert hook.enabled is True

    def test_default_timeout_is_30(self) -> None:
        hook = HookConfig(event=HookEvent.pre_run, command="echo start")
        assert hook.timeout == 30

    def test_disabled_hook(self) -> None:
        hook = HookConfig(
            event=HookEvent.pre_run,
            command="echo hello",
            enabled=False,
        )
        assert hook.enabled is False

    def test_custom_timeout(self) -> None:
        hook = HookConfig(
            event=HookEvent.post_run_success,
            command="deploy.sh",
            timeout=120,
        )
        assert hook.timeout == 120

    def test_timeout_minimum_is_1(self) -> None:
        with pytest.raises(Exception):
            HookConfig(
                event=HookEvent.pre_run,
                command="echo hello",
                timeout=0,
            )

    def test_model_dump_roundtrip(self) -> None:
        original = HookConfig(
            event=HookEvent.post_task,
            command="echo task_done",
            enabled=True,
            timeout=45,
        )
        dump = original.model_dump()
        restored = HookConfig.model_validate(dump)
        assert restored.event == original.event
        assert restored.command == original.command
        assert restored.enabled == original.enabled
        assert restored.timeout == original.timeout

    def test_model_validate_from_dict(self) -> None:
        data = {
            "event": "post_run_failure",
            "command": "alert.sh",
        }
        hook = HookConfig.model_validate(data)
        assert hook.event == HookEvent.post_run_failure
        assert hook.command == "alert.sh"
        assert hook.enabled is True
        assert hook.timeout == _DEFAULT_HOOK_TIMEOUT

    def test_command_can_contain_shell_syntax(self) -> None:
        hook = HookConfig(
            event=HookEvent.pre_run,
            command="echo 'hello world' && date",
        )
        assert hook.command == "echo 'hello world' && date"


# ---------------------------------------------------------------------------
# HookResult model
# ---------------------------------------------------------------------------


class TestHookResultModel:
    """Tests for the HookResult Pydantic model."""

    def test_create_with_all_fields(self) -> None:
        result = HookResult(
            event=HookEvent.pre_run,
            command="echo hello",
            exit_code=0,
            stdout="hello\n",
            stderr="",
            duration_seconds=0.05,
            timestamp="2026-05-20T10:00:00+00:00",
            error="",
        )
        assert result.exit_code == 0
        assert result.stdout == "hello\n"
        assert result.duration_seconds == 0.05
        assert result.error == ""

    def test_default_exit_code_is_none(self) -> None:
        result = HookResult(
            event=HookEvent.pre_run,
            command="echo hello",
            timestamp="2026-05-20T10:00:00+00:00",
        )
        assert result.exit_code is None

    def test_default_stdout_stderr_empty(self) -> None:
        result = HookResult(
            event=HookEvent.pre_run,
            command="echo hello",
            timestamp="2026-05-20T10:00:00+00:00",
        )
        assert result.stdout == ""
        assert result.stderr == ""

    def test_default_duration_zero(self) -> None:
        result = HookResult(
            event=HookEvent.pre_run,
            command="echo hello",
            timestamp="2026-05-20T10:00:00+00:00",
        )
        assert result.duration_seconds == 0.0

    def test_default_error_empty(self) -> None:
        result = HookResult(
            event=HookEvent.pre_run,
            command="echo hello",
            timestamp="2026-05-20T10:00:00+00:00",
        )
        assert result.error == ""

    def test_model_dump_roundtrip(self) -> None:
        original = HookResult(
            event=HookEvent.post_task,
            command="echo done",
            exit_code=1,
            stdout="output",
            stderr="err",
            duration_seconds=1.5,
            timestamp="2026-05-20T10:00:00+00:00",
            error="",
        )
        dump = original.model_dump()
        restored = HookResult.model_validate(dump)
        assert restored.event == original.event
        assert restored.exit_code == original.exit_code
        assert restored.stdout == original.stdout


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    """Tests for internal helper functions."""

    def test_now_iso_returns_iso_string(self) -> None:
        from datetime import datetime

        result = _now_iso()
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None

    def test_now_iso_contains_timezone(self) -> None:
        result = _now_iso()
        assert "+00:00" in result or "Z" in result

    def test_hooks_path_returns_correct_path(self, tmp_path: Path) -> None:
        result = _hooks_path(tmp_path)
        assert result == tmp_path / HOOKS_FILE

    def test_truncate_output_no_truncation_needed(self) -> None:
        text = "short output"
        assert _truncate_output(text) == text

    def test_truncate_output_at_boundary(self) -> None:
        text = "x" * _OUTPUT_TRUNCATE
        assert _truncate_output(text) == text

    def test_truncate_output_exceeds_max(self) -> None:
        text = "x" * (_OUTPUT_TRUNCATE + 100)
        result = _truncate_output(text)
        assert "... [truncated]" in result
        assert len(result) == _OUTPUT_TRUNCATE + len("... [truncated]")

    def test_truncate_output_custom_max(self) -> None:
        text = "x" * 200
        result = _truncate_output(text, max_len=50)
        assert "... [truncated]" in result
        assert len(result) == 50 + len("... [truncated]")

    def test_build_env_none_context(self) -> None:
        assert _build_env(None) is None

    def test_build_env_empty_context(self) -> None:
        assert _build_env({}) is None

    def test_build_env_with_context(self) -> None:
        result = _build_env({"KEY": "value"})
        assert result is not None
        assert result["KEY"] == "value"
        # Should also contain parent env vars
        assert "PATH" in result

    def test_build_env_context_overrides_existing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KEY", "original")
        result = _build_env({"KEY": "overridden"})
        assert result is not None
        assert result["KEY"] == "overridden"


# ---------------------------------------------------------------------------
# execute_hook()
# ---------------------------------------------------------------------------


class TestExecuteHook:
    """Tests for execute_hook()."""

    @pytest.mark.asyncio
    async def test_successful_hook(self) -> None:
        hook = HookConfig(
            event=HookEvent.pre_run,
            command="echo hello",
        )
        result = await execute_hook(hook)
        assert result.event == HookEvent.pre_run
        assert result.command == "echo hello"
        assert result.exit_code == 0
        assert "hello" in result.stdout
        assert result.error == ""
        assert result.duration_seconds > 0

    @pytest.mark.asyncio
    async def test_hook_with_nonzero_exit(self) -> None:
        hook = HookConfig(
            event=HookEvent.post_task,
            command="exit 42",
        )
        result = await execute_hook(hook)
        assert result.exit_code == 42
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_hook_with_stderr(self) -> None:
        hook = HookConfig(
            event=HookEvent.pre_run,
            command="echo error_msg >&2",
        )
        result = await execute_hook(hook)
        assert result.exit_code == 0
        assert "error_msg" in result.stderr

    @pytest.mark.asyncio
    async def test_hook_with_context_env_vars(self) -> None:
        hook = HookConfig(
            event=HookEvent.post_task,
            command="echo $TASK_ID",
        )
        result = await execute_hook(hook, context={"TASK_ID": "T01"})
        assert result.exit_code == 0
        assert "T01" in result.stdout

    @pytest.mark.asyncio
    async def test_hook_timeout(self) -> None:
        hook = HookConfig(
            event=HookEvent.pre_run,
            command="sleep 10",
            timeout=1,
        )
        result = await execute_hook(hook)
        assert result.exit_code is None
        assert "timed out" in result.error.lower()
        assert result.duration_seconds >= 1.0

    @pytest.mark.asyncio
    async def test_hook_command_not_found(self) -> None:
        """With create_subprocess_shell, a nonexistent command returns exit 127."""
        hook = HookConfig(
            event=HookEvent.pre_run,
            command="nonexistent_command_xyz_12345",
        )
        result = await execute_hook(hook)
        assert result.exit_code == 127
        assert "not found" in result.stderr.lower()
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_hook_result_has_timestamp(self) -> None:
        from datetime import datetime

        hook = HookConfig(
            event=HookEvent.pre_run,
            command="echo hello",
        )
        result = await execute_hook(hook)
        dt = datetime.fromisoformat(result.timestamp)
        assert dt.tzinfo is not None

    @pytest.mark.asyncio
    async def test_hook_output_truncation(self) -> None:
        """Large output is truncated in the result."""
        hook = HookConfig(
            event=HookEvent.pre_run,
            command="python3 -c \"print('x' * 2000)\"",
        )
        result = await execute_hook(hook)
        assert result.exit_code == 0
        # Output should be truncated
        assert len(result.stdout) <= _OUTPUT_TRUNCATE + len("... [truncated]")

    @pytest.mark.asyncio
    async def test_disabled_hook_can_still_be_executed(self) -> None:
        """execute_hook does not check the enabled flag — that's the caller's job."""
        hook = HookConfig(
            event=HookEvent.pre_run,
            command="echo hello",
            enabled=False,
        )
        result = await execute_hook(hook)
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# execute_hooks_for_event()
# ---------------------------------------------------------------------------


class TestExecuteHooksForEvent:
    """Tests for execute_hooks_for_event()."""

    @pytest.mark.asyncio
    async def test_executes_matching_hooks(self) -> None:
        hooks = [
            HookConfig(event=HookEvent.pre_run, command="echo first"),
            HookConfig(event=HookEvent.pre_run, command="echo second"),
        ]
        results = await execute_hooks_for_event(hooks, HookEvent.pre_run)
        assert len(results) == 2
        assert all(r.exit_code == 0 for r in results)

    @pytest.mark.asyncio
    async def test_skips_disabled_hooks(self) -> None:
        hooks = [
            HookConfig(event=HookEvent.pre_run, command="echo first", enabled=True),
            HookConfig(event=HookEvent.pre_run, command="echo second", enabled=False),
        ]
        results = await execute_hooks_for_event(hooks, HookEvent.pre_run)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_filters_by_event(self) -> None:
        hooks = [
            HookConfig(event=HookEvent.pre_run, command="echo pre"),
            HookConfig(event=HookEvent.post_task, command="echo post"),
            HookConfig(event=HookEvent.post_run_success, command="echo success"),
        ]
        results = await execute_hooks_for_event(hooks, HookEvent.post_task)
        assert len(results) == 1
        assert results[0].event == HookEvent.post_task

    @pytest.mark.asyncio
    async def test_returns_empty_for_no_matching_hooks(self) -> None:
        hooks = [
            HookConfig(event=HookEvent.pre_run, command="echo hello"),
        ]
        results = await execute_hooks_for_event(hooks, HookEvent.post_task)
        assert results == []

    @pytest.mark.asyncio
    async def test_returns_empty_for_empty_hooks_list(self) -> None:
        results = await execute_hooks_for_event([], HookEvent.pre_run)
        assert results == []

    @pytest.mark.asyncio
    async def test_preserves_registration_order(self) -> None:
        hooks = [
            HookConfig(event=HookEvent.pre_run, command="echo third"),
            HookConfig(event=HookEvent.pre_run, command="echo first"),
            HookConfig(event=HookEvent.pre_run, command="echo second"),
        ]
        results = await execute_hooks_for_event(hooks, HookEvent.pre_run)
        assert len(results) == 3
        assert results[0].command == "echo third"
        assert results[1].command == "echo first"
        assert results[2].command == "echo second"

    @pytest.mark.asyncio
    async def test_passes_context_to_hooks(self) -> None:
        hooks = [
            HookConfig(event=HookEvent.post_task, command="echo $TASK_ID"),
        ]
        results = await execute_hooks_for_event(
            hooks, HookEvent.post_task, context={"TASK_ID": "T02"}
        )
        assert len(results) == 1
        assert "T02" in results[0].stdout

    @pytest.mark.asyncio
    async def test_hook_failure_does_not_stop_others(self) -> None:
        """A failing hook does not prevent subsequent hooks from running."""
        hooks = [
            HookConfig(event=HookEvent.pre_run, command="exit 1"),
            HookConfig(event=HookEvent.pre_run, command="echo survived"),
        ]
        results = await execute_hooks_for_event(hooks, HookEvent.pre_run)
        assert len(results) == 2
        assert results[0].exit_code == 1
        assert results[1].exit_code == 0


# ---------------------------------------------------------------------------
# load_hooks()
# ---------------------------------------------------------------------------


class TestLoadHooks:
    """Tests for load_hooks()."""

    def test_returns_empty_when_file_missing(self, tmp_path: Path) -> None:
        result = load_hooks(tmp_path)
        assert result == []

    def test_returns_empty_when_dir_missing(self, tmp_path: Path) -> None:
        assert not (tmp_path / ".architect").exists()
        result = load_hooks(tmp_path)
        assert result == []

    def test_loads_valid_hooks(self, tmp_path: Path) -> None:
        hooks_path = _hooks_path(tmp_path)
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_path.write_text(
            json.dumps(
                [
                    {
                        "event": "pre_run",
                        "command": "echo start",
                        "enabled": True,
                        "timeout": 30,
                    }
                ]
            ),
            encoding="utf-8",
        )
        result = load_hooks(tmp_path)
        assert len(result) == 1
        assert result[0].event == HookEvent.pre_run
        assert result[0].command == "echo start"

    def test_loads_multiple_hooks(self, tmp_path: Path) -> None:
        hooks_path = _hooks_path(tmp_path)
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_path.write_text(
            json.dumps(
                [
                    {"event": "pre_run", "command": "echo start"},
                    {"event": "post_task", "command": "echo done"},
                    {"event": "post_run_success", "command": "echo success"},
                ]
            ),
            encoding="utf-8",
        )
        result = load_hooks(tmp_path)
        assert len(result) == 3

    def test_returns_empty_on_invalid_json(self, tmp_path: Path) -> None:
        hooks_path = _hooks_path(tmp_path)
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_path.write_text("not valid json {{{{", encoding="utf-8")
        result = load_hooks(tmp_path)
        assert result == []

    def test_returns_empty_on_corrupted_data(self, tmp_path: Path) -> None:
        hooks_path = _hooks_path(tmp_path)
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_path.write_text("CORRUPTED", encoding="utf-8")
        result = load_hooks(tmp_path)
        assert result == []

    def test_returns_empty_on_empty_file(self, tmp_path: Path) -> None:
        hooks_path = _hooks_path(tmp_path)
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_path.write_text("", encoding="utf-8")
        result = load_hooks(tmp_path)
        assert result == []

    def test_returns_empty_on_os_error(self, tmp_path: Path) -> None:
        hooks_path = _hooks_path(tmp_path)
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_path.write_text("[]", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
            result = load_hooks(tmp_path)
        assert result == []

    def test_returns_empty_on_validation_error(self, tmp_path: Path) -> None:
        """JSON that is valid but fails Pydantic validation returns empty list."""
        hooks_path = _hooks_path(tmp_path)
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_path.write_text(
            json.dumps([{"event": "invalid_event", "command": "echo hi"}]),
            encoding="utf-8",
        )
        result = load_hooks(tmp_path)
        assert result == []

    def test_loads_hooks_with_defaults(self, tmp_path: Path) -> None:
        """Hooks without enabled/timeout fields get defaults."""
        hooks_path = _hooks_path(tmp_path)
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_path.write_text(
            json.dumps([{"event": "pre_run", "command": "echo hi"}]),
            encoding="utf-8",
        )
        result = load_hooks(tmp_path)
        assert len(result) == 1
        assert result[0].enabled is True
        assert result[0].timeout == _DEFAULT_HOOK_TIMEOUT


# ---------------------------------------------------------------------------
# save_hooks()
# ---------------------------------------------------------------------------


class TestSaveHooks:
    """Tests for save_hooks()."""

    def test_creates_directory_if_missing(self, tmp_path: Path) -> None:
        assert not (tmp_path / ".architect").exists()
        save_hooks(tmp_path, [])
        assert (tmp_path / ".architect").exists()

    def test_writes_valid_json(self, tmp_path: Path) -> None:
        hooks = [
            HookConfig(event=HookEvent.pre_run, command="echo start"),
        ]
        save_hooks(tmp_path, hooks)
        raw = _hooks_path(tmp_path).read_text(encoding="utf-8")
        data = json.loads(raw)
        assert len(data) == 1
        assert data[0]["event"] == "pre_run"

    def test_empty_hooks_writes_empty_array(self, tmp_path: Path) -> None:
        save_hooks(tmp_path, [])
        raw = _hooks_path(tmp_path).read_text(encoding="utf-8")
        data = json.loads(raw)
        assert data == []

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        first = [HookConfig(event=HookEvent.pre_run, command="echo first")]
        save_hooks(tmp_path, first)
        second = [HookConfig(event=HookEvent.post_task, command="echo second")]
        save_hooks(tmp_path, second)
        loaded = load_hooks(tmp_path)
        assert len(loaded) == 1
        assert loaded[0].event == HookEvent.post_task

    def test_preserves_hook_fields(self, tmp_path: Path) -> None:
        hooks = [
            HookConfig(
                event=HookEvent.post_run_success,
                command="deploy.sh",
                enabled=False,
                timeout=120,
            ),
        ]
        save_hooks(tmp_path, hooks)
        loaded = load_hooks(tmp_path)
        assert len(loaded) == 1
        assert loaded[0].event == HookEvent.post_run_success
        assert loaded[0].command == "deploy.sh"
        assert loaded[0].enabled is False
        assert loaded[0].timeout == 120


# ---------------------------------------------------------------------------
# add_hook()
# ---------------------------------------------------------------------------


class TestAddHook:
    """Tests for add_hook()."""

    def test_adds_to_empty_store(self, tmp_path: Path) -> None:
        hook = HookConfig(event=HookEvent.pre_run, command="echo start")
        result = add_hook(tmp_path, hook)
        assert result.command == "echo start"
        loaded = list_hooks(tmp_path)
        assert len(loaded) == 1
        assert loaded[0].event == HookEvent.pre_run

    def test_appends_to_existing(self, tmp_path: Path) -> None:
        save_hooks(
            tmp_path,
            [
                HookConfig(event=HookEvent.pre_run, command="echo first"),
            ],
        )
        add_hook(tmp_path, HookConfig(event=HookEvent.post_task, command="echo second"))
        loaded = list_hooks(tmp_path)
        assert len(loaded) == 2
        assert loaded[0].command == "echo first"
        assert loaded[1].command == "echo second"

    def test_returns_added_hook(self, tmp_path: Path) -> None:
        hook = HookConfig(
            event=HookEvent.post_run_failure,
            command="alert.sh",
            timeout=60,
        )
        result = add_hook(tmp_path, hook)
        assert result is hook


# ---------------------------------------------------------------------------
# remove_hook()
# ---------------------------------------------------------------------------


class TestRemoveHook:
    """Tests for remove_hook()."""

    def test_removes_by_index(self, tmp_path: Path) -> None:
        save_hooks(
            tmp_path,
            [
                HookConfig(event=HookEvent.pre_run, command="echo first"),
                HookConfig(event=HookEvent.post_task, command="echo second"),
            ],
        )
        result = remove_hook(tmp_path, 0)
        assert result is True
        loaded = list_hooks(tmp_path)
        assert len(loaded) == 1
        assert loaded[0].command == "echo second"

    def test_removes_last_hook(self, tmp_path: Path) -> None:
        save_hooks(
            tmp_path,
            [
                HookConfig(event=HookEvent.pre_run, command="echo first"),
                HookConfig(event=HookEvent.post_task, command="echo second"),
            ],
        )
        result = remove_hook(tmp_path, 1)
        assert result is True
        loaded = list_hooks(tmp_path)
        assert len(loaded) == 1
        assert loaded[0].command == "echo first"

    def test_out_of_range_returns_false(self, tmp_path: Path) -> None:
        save_hooks(
            tmp_path,
            [
                HookConfig(event=HookEvent.pre_run, command="echo first"),
            ],
        )
        result = remove_hook(tmp_path, 5)
        assert result is False
        assert len(list_hooks(tmp_path)) == 1

    def test_negative_index_returns_false(self, tmp_path: Path) -> None:
        save_hooks(
            tmp_path,
            [
                HookConfig(event=HookEvent.pre_run, command="echo first"),
            ],
        )
        result = remove_hook(tmp_path, -1)
        assert result is False

    def test_remove_from_empty_returns_false(self, tmp_path: Path) -> None:
        result = remove_hook(tmp_path, 0)
        assert result is False

    def test_remove_from_missing_store_returns_false(self, tmp_path: Path) -> None:
        result = remove_hook(tmp_path, 0)
        assert result is False


# ---------------------------------------------------------------------------
# list_hooks()
# ---------------------------------------------------------------------------


class TestListHooks:
    """Tests for list_hooks()."""

    def test_returns_all_hooks(self, tmp_path: Path) -> None:
        save_hooks(
            tmp_path,
            [
                HookConfig(event=HookEvent.pre_run, command="echo start"),
                HookConfig(event=HookEvent.post_task, command="echo done"),
            ],
        )
        result = list_hooks(tmp_path)
        assert len(result) == 2

    def test_returns_empty_for_missing_store(self, tmp_path: Path) -> None:
        result = list_hooks(tmp_path)
        assert result == []

    def test_returns_copy_of_hooks(self, tmp_path: Path) -> None:
        save_hooks(
            tmp_path,
            [
                HookConfig(event=HookEvent.pre_run, command="echo start"),
            ],
        )
        result = list_hooks(tmp_path)
        result.clear()
        assert len(list_hooks(tmp_path)) == 1


# ---------------------------------------------------------------------------
# Integration and edge cases
# ---------------------------------------------------------------------------


class TestIntegration:
    """Integration tests for the hooks module."""

    def test_full_crud_roundtrip(self, tmp_path: Path) -> None:
        """Add -> List -> Remove -> List roundtrip."""
        add_hook(tmp_path, HookConfig(event=HookEvent.pre_run, command="echo a"))
        add_hook(tmp_path, HookConfig(event=HookEvent.post_task, command="echo b"))
        hooks = list_hooks(tmp_path)
        assert len(hooks) == 2

        remove_hook(tmp_path, 0)
        hooks = list_hooks(tmp_path)
        assert len(hooks) == 1
        assert hooks[0].event == HookEvent.post_task

    def test_save_and_load_preserves_all_events(self, tmp_path: Path) -> None:
        hooks = [
            HookConfig(event=HookEvent.pre_run, command="echo pre"),
            HookConfig(event=HookEvent.post_task, command="echo task"),
            HookConfig(event=HookEvent.post_run_success, command="echo success"),
            HookConfig(event=HookEvent.post_run_failure, command="echo fail"),
        ]
        save_hooks(tmp_path, hooks)
        loaded = load_hooks(tmp_path)
        assert len(loaded) == 4
        events = [h.event for h in loaded]
        assert HookEvent.pre_run in events
        assert HookEvent.post_task in events
        assert HookEvent.post_run_success in events
        assert HookEvent.post_run_failure in events

    def test_hook_with_complex_shell_command(self, tmp_path: Path) -> None:
        """A hook with pipes, redirects, and chaining survives round-trip."""
        cmd = "echo hello | grep hello && echo done || echo failed"
        hook = HookConfig(event=HookEvent.pre_run, command=cmd)
        add_hook(tmp_path, hook)
        loaded = list_hooks(tmp_path)
        assert loaded[0].command == cmd

    @pytest.mark.asyncio
    async def test_execute_hook_with_multiple_context_vars(self) -> None:
        hook = HookConfig(
            event=HookEvent.post_task,
            command="echo $TASK_ID $TASK_STATUS",
        )
        result = await execute_hook(
            hook,
            context={"TASK_ID": "T03", "TASK_STATUS": "done"},
        )
        assert result.exit_code == 0
        assert "T03" in result.stdout
        assert "done" in result.stdout

    def test_hooks_file_is_json_array_not_object(self, tmp_path: Path) -> None:
        """Hooks are stored as a JSON array, not a wrapped object."""
        save_hooks(
            tmp_path,
            [
                HookConfig(event=HookEvent.pre_run, command="echo hi"),
            ],
        )
        raw = _hooks_path(tmp_path).read_text(encoding="utf-8")
        data = json.loads(raw)
        assert isinstance(data, list)
        assert not isinstance(data, dict)

    def test_default_timeout_constant(self) -> None:
        assert _DEFAULT_HOOK_TIMEOUT == 30

    def test_output_truncate_constant(self) -> None:
        assert _OUTPUT_TRUNCATE == 1000

    def test_hooks_file_path_constant(self) -> None:
        assert HOOKS_FILE == Path(".architect/hooks.json")
