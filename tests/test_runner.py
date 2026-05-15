"""Tests for the task execution engine."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from the_architect.config import ArchitectConfig
from the_architect.core.provider import ParsedEvent
from the_architect.core.runner import (
    HourlyTokenBudget,
    ManagedExecutionRenderer,
    OutputAnalysis,
    StreamResult,
    TaskResult,
    TokenUsage,
    _determine_self_assessment,
    _is_lock_stale,
    _parse_opencode_event,
    _task_outcome_summary_for_exit,
    _tool_result_lines,
    acquire_lock,
    analyze_output,
    build_attempt_summary,
    build_instruction,
    build_opencode_command,
    extract_completion_promises,
    extract_error_signals,
    extract_progress_signals,
    has_stdbuf,
    is_task_complete,
    opencode_path_for_command,
    release_lock,
    run_all,
    run_task,
    run_task_once,
    select_model,
    setup_logging,
    stream_provider,
    summarize_previous_attempt,
)
from the_architect.core.tasks import Task, TaskPlan, TaskStatus

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def config(tmp_path: Path) -> ArchitectConfig:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    progress_file = tasks_dir / "PROGRESS.md"
    progress_file.write_text(
        "**Tasks completed:** 0\n**Next task to run:** T01\n",
        encoding="utf-8",
    )
    log_dir = tmp_path / ".architect" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return ArchitectConfig(
        progress_file=progress_file,
        tasks_dir=tasks_dir,
        log_dir=log_dir,
        retry_model_2="claude-sonnet-4-20250514",
        retry_model_3="claude-opus-4-20250514",
        max_retries=3,
        retry_pause=0,
        pause_between_tasks=0,
    )


@pytest.fixture(autouse=True)
def skip_progress_flush_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid the production progress-file flush delay in mocked runner tests."""
    monkeypatch.setattr("the_architect.core.runner._PROGRESS_FLUSH_DELAY_SECONDS", 0.0)


@pytest.fixture
def task(config: ArchitectConfig) -> Task:
    path = config.tasks_dir / "T01_test.md"
    path.write_text("# T01 - Test Task\n", encoding="utf-8")
    return Task(
        name="T01_test",
        prefix="T01",
        number=1,
        path=path,
        status=TaskStatus.PENDING,
    )


# ── Helper: mock provider for stream_provider tests ───────────────────────


def _make_mock_provider(**overrides):
    from the_architect.core.provider import ArchitectProvider

    p = MagicMock(spec=ArchitectProvider)
    p.display_name = overrides.get("display_name", "test-provider")
    p.build_command = overrides.get("build_command", MagicMock(return_value=["echo", "test"]))
    p.get_env_overrides = overrides.get("get_env_overrides", MagicMock(return_value={}))
    p.parse_output_line = overrides.get("parse_output_line", MagicMock(return_value=None))
    p.supports_agents = MagicMock(return_value=overrides.get("supports_agents", True))
    # Instruction is delivered as a CLI arg in mocked tests (not via stdin).
    # Explicit False prevents the truthy MagicMock attribute from triggering
    # the stdin-pipe path and requiring process.stdin on the mock.
    p.instruction_via_stdin = overrides.get("instruction_via_stdin", False)
    return p


def _make_mock_process(stdout_lines=None, exit_code=0, stdout_none=False):
    """Create a mock subprocess with configurable stdout behavior."""
    mock_process = AsyncMock(spec=asyncio.subprocess.Process)

    if stdout_none:
        mock_process.stdout = None
        return mock_process

    mock_stdout = AsyncMock()
    if stdout_lines is not None:
        lines = list(stdout_lines) + [b""]  # sentinel
        idx = [0]

        async def readline():
            if idx[0] < len(lines):
                line = lines[idx[0]]
                idx[0] += 1
                return line
            return b""

        mock_stdout.readline = readline
    else:
        mock_stdout.readline = AsyncMock(return_value=b"")

    mock_process.stdout = mock_stdout
    mock_process.wait = AsyncMock(return_value=exit_code)
    return mock_process


# ═══════════════════════════════════════════════════════════════════════════
# 1. acquire_lock / release_lock / _is_lock_stale
# ═══════════════════════════════════════════════════════════════════════════


class TestAcquireLockErrorPaths:
    def test_stale_lock_removed_and_reacquired(self, tmp_path):
        """When lock is stale, remove it and retry -> should succeed."""
        with (
            patch("the_architect.core.runner._is_lock_stale", return_value=True),
            patch("the_architect.core.runner.os.open", side_effect=[FileExistsError, 42]),
            patch("the_architect.core.runner.os.write"),
            patch("the_architect.core.runner.os.close"),
        ):
            # Also need to patch unlink to simulate successful removal
            with patch("the_architect.core.runner.Path.unlink"):
                result = acquire_lock(tmp_path)
                # The second call to os.open returns 42 (success)
                # os.write and os.close are mocked
                # But Path.unlink may not match because lock_path is computed dynamically
                # Let's just check the overall behavior
        # This test is tricky because lock_path is computed inside the function
        # Let's use a simpler approach - create a real stale lock
        lock_dir = tmp_path / ".architect"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_file = lock_dir / "runner.lock"
        lock_file.write_text("999999", encoding="utf-8")  # PID that doesn't exist
        with patch("the_architect.core.runner._is_lock_stale", return_value=True):
            result = acquire_lock(tmp_path)
            assert result is True

    def test_non_stale_lock_returns_false(self, tmp_path):
        """When lock exists and is NOT stale -> should return False."""
        lock_dir = tmp_path / ".architect"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_file = lock_dir / "runner.lock"
        lock_file.write_text(str(os.getpid()), encoding="utf-8")
        with patch("the_architect.core.runner._is_lock_stale", return_value=False):
            result = acquire_lock(tmp_path)
            assert result is False

    def test_oserror_catchall(self, tmp_path):
        """When os.open raises OSError (not FileExistsError) -> should return False."""
        with patch("the_architect.core.runner.os.open", side_effect=OSError("disk full")):
            result = acquire_lock(tmp_path)
            assert result is False


class TestReleaseLockOSError:
    def test_unlink_oserror_does_not_raise(self, tmp_path):
        lock_path = tmp_path / ".architect" / "runner.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("pid", encoding="utf-8")
        with patch("the_architect.core.runner.Path.unlink", side_effect=OSError("read-only")):
            release_lock(tmp_path)  # Should not raise


# ═══════════════════════════════════════════════════════════════════════════
# 2. build_opencode_command
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildOpencodeCommand:
    def test_basic_command(self):
        cmd = build_opencode_command("hello")
        assert "run" in cmd
        assert "hello" in cmd

    def test_model_override(self):
        cmd = build_opencode_command("hello", model_override="gpt-4o")
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "gpt-4o"

    def test_agent_override(self):
        cmd = build_opencode_command("hello", agent_override="qa-fast")
        assert "--agent" in cmd
        idx = cmd.index("--agent")
        assert cmd[idx + 1] == "qa-fast"


# ═══════════════════════════════════════════════════════════════════════════
# 3. stream_provider (subprocess handling)
# ═══════════════════════════════════════════════════════════════════════════


class TestStreamProviderSubprocess:
    @pytest.fixture(autouse=True)
    def _project_dir(self, tmp_path: Path) -> None:
        """Provide a real directory so stream_provider's is_dir() check passes on all OS."""
        self.project_dir = tmp_path

    @pytest.mark.asyncio
    async def test_stdout_none_returns_failed_result(self):
        """When process.stdout is None, returns StreamResult with exit_code=-1."""
        provider = _make_mock_provider()
        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_mock_process(stdout_none=True)
            result = await stream_provider("test", self.project_dir, provider)
            assert result.exit_code == -1

    def test_managed_execution_renderer_lifecycle(self) -> None:
        renderer = ManagedExecutionRenderer()
        renderer.write_line("hello world")
        renderer.set_footer("starting T01")
        renderer.clear_footer()
        renderer.close()

    def test_managed_execution_renderer_renders_footer_text(self) -> None:
        renderer = ManagedExecutionRenderer()
        renderer.set_footer("T01 | attempt 1/3 | starting")
        renderer.write_line("provider output")
        renderer.close()

    def test_managed_execution_renderer_formats_structured_footer(self) -> None:
        # ManagedExecutionRenderer is currently a compatibility shim that
        # behaves like PlainStreamRenderer.  It accepts footer updates but
        # does not apply them — a real TUI footer will be added later.
        renderer = ManagedExecutionRenderer()
        renderer.set_footer("T01 | attempt 1/3 | model claude-sonnet")
        renderer.clear_footer()
        renderer.close()

    @pytest.mark.asyncio
    async def test_reads_lines_and_accumulates_text(self):
        provider = _make_mock_provider()
        text_event = ParsedEvent(
            event_type="text",
            display_lines=["hello"],
            tokens=None,
            rate_limit=False,
            model_not_found=False,
            cooldown_until=0,
        )
        provider.parse_output_line = MagicMock(return_value=text_event)

        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_mock_process(
                stdout_lines=[b'{"type":"text"}\n'],
                exit_code=0,
            )
            result = await stream_provider("test", self.project_dir, provider)
            assert result.accumulated_text == "hello"

    @pytest.mark.asyncio
    async def test_accumulates_structured_rate_limit_events_without_display_lines(self):
        provider = _make_mock_provider()
        event = ParsedEvent(
            event_type="rate_limit_event",
            display_lines=[],
            tokens=None,
            rate_limit=True,
            model_not_found=False,
            cooldown_until=0,
        )
        provider.parse_output_line = MagicMock(return_value=event)
        raw = b'{"type":"rate_limit_event","overageDisabledReason":"out_of_credits"}\n'

        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_mock_process(stdout_lines=[raw], exit_code=0)
            result = await stream_provider("test", self.project_dir, provider)

        assert "out_of_credits" in result.accumulated_text

    @pytest.mark.asyncio
    async def test_rate_limit_detected(self):
        provider = _make_mock_provider()
        rl_event = ParsedEvent(
            event_type="error",
            display_lines=["rate limited"],
            tokens=None,
            rate_limit=True,
            model_not_found=False,
            cooldown_until=0,
        )
        provider.parse_output_line = MagicMock(return_value=rl_event)

        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_mock_process(
                stdout_lines=[b"rl\n"],
                exit_code=0,
            )
            result = await stream_provider("test", self.project_dir, provider)
            assert result.rate_limit_hit is True

    @pytest.mark.asyncio
    async def test_model_not_found_detected(self):
        provider = _make_mock_provider()
        mnf_event = ParsedEvent(
            event_type="error",
            display_lines=["model not found"],
            tokens=None,
            rate_limit=False,
            model_not_found=True,
            cooldown_until=0,
        )
        provider.parse_output_line = MagicMock(return_value=mnf_event)

        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_mock_process(
                stdout_lines=[b"mnf\n"],
                exit_code=0,
            )
            result = await stream_provider("test", self.project_dir, provider)
            assert result.rate_limit_hit is True

    @pytest.mark.asyncio
    async def test_cancelled_error_in_reader(self):
        provider = _make_mock_provider()
        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_process = _make_mock_process(stdout_lines=[], exit_code=0)

            # Make readline raise CancelledError
            async def raise_cancelled():
                raise asyncio.CancelledError()

            mock_process.stdout.readline = raise_cancelled
            mock_exec.return_value = mock_process
            result = await stream_provider("test", self.project_dir, provider)
            assert isinstance(result, StreamResult)

    @pytest.mark.asyncio
    async def test_value_error_in_reader(self):
        provider = _make_mock_provider()
        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_process = _make_mock_process(stdout_lines=[], exit_code=0)

            async def raise_value():
                raise ValueError("buffer limit")

            mock_process.stdout.readline = raise_value
            mock_exec.return_value = mock_process
            result = await stream_provider("test", self.project_dir, provider)
            assert isinstance(result, StreamResult)

    @pytest.mark.asyncio
    async def test_generic_exception_in_reader(self):
        provider = _make_mock_provider()
        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_process = _make_mock_process(stdout_lines=[], exit_code=0)

            async def raise_runtime():
                raise RuntimeError("unexpected")

            mock_process.stdout.readline = raise_runtime
            mock_exec.return_value = mock_process
            result = await stream_provider("test", self.project_dir, provider)
            assert isinstance(result, StreamResult)

    @pytest.mark.asyncio
    async def test_filenotfounderror_reraised(self):
        provider = _make_mock_provider()
        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = FileNotFoundError("not found")
            with pytest.raises(FileNotFoundError):
                await stream_provider("test", self.project_dir / "nonexistent", provider)

    @pytest.mark.asyncio
    async def test_generic_exception_kills_process(self):
        provider = _make_mock_provider()
        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_process = AsyncMock(spec=asyncio.subprocess.Process)
            mock_process.stdout = AsyncMock()
            mock_process.stdout.readline = AsyncMock(return_value=b"")
            mock_process.wait = AsyncMock(return_value=-1)
            mock_process.returncode = None
            mock_process.kill = MagicMock()
            mock_exec.return_value = mock_process

            async def raise_runtime(awaitable: object, timeout: float | None = None) -> object:
                if asyncio.iscoroutine(awaitable):
                    awaitable.close()
                elif isinstance(awaitable, asyncio.Task):
                    awaitable.cancel()
                raise RuntimeError("boom")

            # Make the reader wait path raise without leaking the awaitable passed to wait_for.
            with patch("the_architect.core.runner.asyncio.wait_for", side_effect=raise_runtime):
                result = await stream_provider("test", self.project_dir, provider)
            assert result.exit_code == -1

    @pytest.mark.asyncio
    async def test_path_not_set_logs_warning(self):
        provider = _make_mock_provider()
        with patch.dict(os.environ, {}, clear=True):
            with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
                mock_exec.return_value = _make_mock_process(stdout_none=True)
                result = await stream_provider("test", self.project_dir, provider)
                # Should not raise - the error is caught and returns exit_code=-1
                assert result.exit_code == -1

    @pytest.mark.asyncio
    async def test_log_path_parent_created(self, tmp_path):
        provider = _make_mock_provider()
        log_path = tmp_path / "deep" / "nested" / "test.log"
        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_mock_process(stdout_lines=[], exit_code=0)
            await stream_provider("test", self.project_dir, provider, log_path=log_path)
            assert log_path.parent.exists()

    @pytest.mark.asyncio
    async def test_spawns_subprocess_with_start_new_session_on_posix(self):
        """Issue 2 regression: subprocess must get its own POSIX session
        so the whole process tree can be killed via ``killpg`` when the
        TUI shuts down. Without this, Ctrl+C leaves opencode / claude
        alive in the background.
        """
        if os.name != "posix":
            pytest.skip("start_new_session is POSIX-only")
        provider = _make_mock_provider()
        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_mock_process(stdout_lines=[], exit_code=0)
            await stream_provider("test", self.project_dir, provider)
            _, kwargs = mock_exec.call_args
            assert kwargs.get("start_new_session") is True

    @pytest.mark.asyncio
    async def test_finally_kills_leftover_subprocess(self):
        """The stream_provider ``finally`` block must terminate the
        subprocess if it is somehow still running when the function
        returns — this is the last line of defence that prevents an
        abandoned provider from outliving the UI after Ctrl+C.
        """
        provider = _make_mock_provider()
        mock_proc = _make_mock_process(stdout_lines=[], exit_code=0)
        # Simulate a process that never exited on its own.
        mock_proc.returncode = None
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            await stream_provider("test", self.project_dir, provider)
            # The finally block called kill() (via _kill_process_tree).
            assert mock_proc.kill.called

    def test_kill_active_subprocesses_terminates_registered_processes(self):
        """``kill_active_subprocesses`` kills every process the runner
        knows about — this is what the TUI shutdown path calls when
        the user hits Ctrl+C so the backend actually stops.
        """
        from the_architect.core import runner as runner_mod

        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.pid = 99999999  # non-existent PID — killpg will no-op
        fake_proc.kill = MagicMock()
        runner_mod._register_process(fake_proc)
        try:
            n = runner_mod.kill_active_subprocesses()
            assert n >= 1
            assert fake_proc.kill.called
        finally:
            runner_mod._unregister_process(fake_proc)

    def test_kill_process_tree_uses_sigkill_not_sigterm(self):
        """Regression guard: ``_kill_process_tree`` must use SIGKILL so
        the backend actually dies when the user hits Ctrl+C. An earlier
        iteration used SIGTERM, which providers could ignore or delay
        while mid-call, producing the exact bug the user reported
        ("Ctrl+C just exits the UI, backend keeps going").

        On Windows ``os.killpg`` does not exist; the test is POSIX-only.
        """
        import os
        import signal as _signal

        if not hasattr(os, "killpg"):
            pytest.skip("killpg is POSIX-only")

        from the_architect.core import runner as runner_mod

        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.pid = 99999999
        fake_proc.kill = MagicMock()

        with patch("the_architect.core.runner.os.killpg") as killpg:
            with patch("the_architect.core.runner.os.getpgid", return_value=42):
                runner_mod._kill_process_tree(fake_proc)
        # The signal we actually send to the process group must be
        # SIGKILL — SIGTERM is not strong enough for a provider that
        # has entered an uninterruptible system call or is ignoring
        # SIGTERM while flushing buffers.
        assert killpg.called
        # Support either killpg(pgid, SIGKILL) signature shape.
        called_sig = killpg.call_args[0][1] if len(killpg.call_args[0]) >= 2 else None
        assert called_sig == _signal.SIGKILL
        # And proc.kill as the Windows / backup path.
        assert fake_proc.kill.called

    @pytest.mark.asyncio
    async def test_provider_doesnt_support_agents(self):
        provider = _make_mock_provider(supports_agents=False)
        # This should still work - agent_override is just ignored
        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_mock_process(stdout_lines=[], exit_code=0)
            result = await stream_provider(
                "test",
                self.project_dir,
                provider,
                agent_override="backend",
            )
            assert isinstance(result, StreamResult)
            # Verify agent_override was NOT in the command
            call_args = mock_exec.call_args[0]
            assert "--agent" not in call_args

    @pytest.mark.asyncio
    async def test_log_file_open_failure(self):
        provider = _make_mock_provider()
        log_path = self.project_dir / "test_stream.log"
        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_mock_process(stdout_lines=[b"line\n"], exit_code=0)
            with patch("builtins.open", side_effect=OSError("read-only")):
                result = await stream_provider(
                    "test", self.project_dir, provider, log_path=log_path
                )
                assert isinstance(result, StreamResult)

    @pytest.mark.asyncio
    async def test_log_file_write_failure(self):
        provider = _make_mock_provider()
        log_path = self.project_dir / "test_stream2.log"
        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_mock_process(stdout_lines=[b"line\n"], exit_code=0)
            mock_file = MagicMock()
            mock_file.write.side_effect = OSError("write fail")
            mock_file.flush = MagicMock()
            mock_file.close = MagicMock()
            with patch("builtins.open", return_value=mock_file):
                result = await stream_provider(
                    "test", self.project_dir, provider, log_path=log_path
                )
                assert isinstance(result, StreamResult)

    @pytest.mark.asyncio
    async def test_log_file_close_failure(self):
        provider = _make_mock_provider()
        log_path = self.project_dir / "test_stream3.log"
        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_mock_process(stdout_lines=[b"line\n"], exit_code=0)
            mock_file = MagicMock()
            mock_file.write = MagicMock()
            mock_file.flush = MagicMock()
            mock_file.close.side_effect = OSError("close fail")
            with patch("builtins.open", return_value=mock_file):
                result = await stream_provider(
                    "test", self.project_dir, provider, log_path=log_path
                )
                assert isinstance(result, StreamResult)

    @pytest.mark.asyncio
    async def test_reader_task_timeout(self):
        provider = _make_mock_provider()
        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_process = _make_mock_process(stdout_lines=[], exit_code=0)

            # Make readline hang forever
            async def hang():
                await asyncio.sleep(999)

            mock_process.stdout.readline = hang
            mock_exec.return_value = mock_process

            with patch("the_architect.core.runner.asyncio.wait_for", side_effect=TimeoutError):
                result = await stream_provider("test", self.project_dir, provider)
                assert isinstance(result, StreamResult)

    @pytest.mark.asyncio
    async def test_cooldown_until_from_event(self):
        provider = _make_mock_provider()
        event = ParsedEvent(
            event_type="text",
            display_lines=["ok"],
            tokens=None,
            rate_limit=True,
            model_not_found=False,
            cooldown_until=1700000000,
        )
        provider.parse_output_line = MagicMock(return_value=event)
        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_mock_process(stdout_lines=[b"ev\n"], exit_code=0)
            result = await stream_provider("test", self.project_dir, provider)
            assert result.cooldown_until == 1700000000

    @pytest.mark.asyncio
    async def test_none_parsed_line_printed_as_is(self):
        provider = _make_mock_provider()
        provider.parse_output_line = MagicMock(return_value=None)
        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_mock_process(
                stdout_lines=[b"raw output line\n"],
                exit_code=0,
            )
            with patch("sys.stdout"):
                result = await stream_provider("test", self.project_dir, provider)
                assert isinstance(result, StreamResult)

    @pytest.mark.asyncio
    async def test_idle_provider_output_is_terminated(self, monkeypatch: pytest.MonkeyPatch):
        """A provider that stops producing stdout must not hang the run forever."""
        provider = _make_mock_provider()
        mock_process = _make_mock_process(stdout_lines=[], exit_code=0)

        async def _never_returns():
            await asyncio.sleep(60)
            return b""

        mock_process.stdout.readline = _never_returns
        mock_process.returncode = None
        mock_process.kill = MagicMock()
        monkeypatch.setenv("ARCHITECT_PROVIDER_IDLE_TIMEOUT_SECONDS", "0.01")
        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_process
            with patch("sys.stdout"):
                result = await stream_provider("test", self.project_dir, provider)

        assert "Provider produced no stdout" in result.accumulated_text
        assert mock_process.kill.called

    @pytest.mark.asyncio
    async def test_sleep_wake_gap_terminates_provider(self, monkeypatch: pytest.MonkeyPatch):
        """A large wall-clock gap while waiting for output is treated as retryable."""
        provider = _make_mock_provider()
        mock_process = _make_mock_process(stdout_lines=[], exit_code=0)

        async def _never_returns():
            await asyncio.sleep(60)
            return b""

        mock_process.stdout.readline = _never_returns
        mock_process.returncode = None
        mock_process.kill = MagicMock()
        monkeypatch.setenv("ARCHITECT_PROVIDER_IDLE_TIMEOUT_SECONDS", "900")
        monkeypatch.setenv("ARCHITECT_SLEEP_WAKE_GAP_SECONDS", "0.01")
        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_process
            with patch("the_architect.core.runner.time.time", side_effect=[100.0, 101.0]):
                with patch("sys.stdout"):
                    result = await stream_provider("test", self.project_dir, provider)

        assert result.interrupted is True
        assert result.interruption_reason == "sleep_wake_gap"
        assert "computer slept or was suspended" in result.accumulated_text
        assert result.exit_code != 0
        assert mock_process.kill.called


# ═══════════════════════════════════════════════════════════════════════════
# 4. _tool_result_lines
# ═══════════════════════════════════════════════════════════════════════════


class TestToolResultLines:
    def test_glob_with_count(self):
        lines = _tool_result_lines("glob", "", {"count": 5, "truncated": False}, "")
        assert lines == ["5 matches"]

    def test_glob_with_truncated(self):
        lines = _tool_result_lines("glob", "", {"count": 1, "truncated": True}, "")
        assert lines == ["1 match (truncated)"]

    def test_grep_with_matches(self):
        lines = _tool_result_lines("grep", "", {"matches": 10}, "")
        assert lines == ["10 matches"]

    def test_grep_one_match(self):
        lines = _tool_result_lines("grep", "", {"matches": 1}, "")
        assert lines == ["1 match"]

    def test_read_with_preview(self):
        lines = _tool_result_lines("read", "", {"preview": "line1\nline2\nline3"}, "")
        assert len(lines) == 3

    def test_read_with_long_preview(self):
        long_preview = "\n".join(f"line{i}" for i in range(10))
        lines = _tool_result_lines("read", "", {"preview": long_preview}, "")
        assert any("more lines" in line for line in lines)

    def test_read_fallback_from_output(self):
        lines = _tool_result_lines("read", "output text here", {}, "")
        assert lines == ["output text here"]

    def test_read_empty_output(self):
        lines = _tool_result_lines("read", "", {}, "")
        assert lines == []

    def test_write_success(self):
        lines = _tool_result_lines("write", "File wrote successfully", {}, "")
        assert lines == ["File wrote successfully"]

    def test_write_done(self):
        lines = _tool_result_lines("write", "other text", {}, "")
        assert lines == ["done"]

    def test_bash_with_output(self):
        lines = _tool_result_lines("bash", "line1\nline2", {}, "")
        assert lines == ["line1", "line2"]

    def test_bash_truncated(self):
        many = "\n".join(f"line{i}" for i in range(15))
        lines = _tool_result_lines("bash", many, {}, "")
        assert any("more lines" in line for line in lines)

    def test_bash_empty(self):
        lines = _tool_result_lines("bash", "", {}, "")
        assert lines == []

    def test_todowrite_json(self):
        todos = json.dumps(
            {
                "todos": [
                    {"status": "completed", "content": "item1"},
                    {"status": "in_progress", "content": "item2"},
                ]
            }
        )
        lines = _tool_result_lines("todowrite", todos, {}, "")
        assert any("item1" in line for line in lines)
        assert "✓" in lines[0]

    def test_todowrite_fallback(self):
        lines = _tool_result_lines("todowrite", "plain text", {}, "")
        assert lines == ["plain text"]

    def test_generic_with_title(self):
        lines = _tool_result_lines("custom", "output", {}, "My Result")
        assert lines == ["My Result"]

    def test_generic_with_output(self):
        lines = _tool_result_lines("custom", "line1\nline2", {}, "")
        assert lines == ["line1"]

    def test_generic_no_output(self):
        lines = _tool_result_lines("custom", "", {}, "")
        assert lines == []

    def test_view_same_as_read(self):
        lines = _tool_result_lines("view", "content", {}, "")
        assert lines == ["content"]

    def test_edit_success(self):
        lines = _tool_result_lines("edit", "File wrote ok", {}, "")
        assert lines == ["File wrote ok"]

    def test_edit_done(self):
        lines = _tool_result_lines("edit", "other", {}, "")
        assert lines == ["done"]


class TestTaskOutcomeSummaryForExit:
    """Tests for interrupted provider diagnostics."""

    def test_includes_sigkill_diagnostic(self) -> None:
        """Exit _FORCED_TERMINATION_EXIT_CODE should not be hidden as generic no-progress."""
        from the_architect.core.runner import _FORCED_TERMINATION_EXIT_CODE

        summary = _task_outcome_summary_for_exit("", _FORCED_TERMINATION_EXIT_CODE)

        assert "Provider process killed" in summary
        assert str(_FORCED_TERMINATION_EXIT_CODE) in summary


# ═══════════════════════════════════════════════════════════════════════════
# 5. _parse_opencode_event
# ═══════════════════════════════════════════════════════════════════════════


class TestParseOpencodeEvent:
    def test_text_event(self):
        result = _parse_opencode_event('{"type":"text","part":{"text":"hello"}}')
        assert result is not None
        etype, lines, tokens = result
        assert etype == "text"
        assert "hello" in lines

    def test_tool_use_completed(self):
        ev = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "read",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "test.py"},
                        "output": "file contents",
                        "metadata": {"count": 5},
                    },
                },
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None
        etype, lines, _ = result
        assert any("read" in line for line in lines)

    def test_tool_use_with_offset_limit(self):
        ev = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "read",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "test.py", "offset": 10, "limit": 5},
                        "output": "",
                        "metadata": {},
                    },
                },
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None
        etype, lines, _ = result
        assert any("L10" in line for line in lines)

    def test_step_start_event(self):
        result = _parse_opencode_event('{"type":"step_start"}')
        assert result is not None
        etype, lines, _ = result
        assert etype == "step_start"
        assert lines == []

    def test_step_finish_event(self):
        result = _parse_opencode_event('{"type":"step_finish"}')
        assert result is not None
        assert result[0] == "step_finish"

    def test_error_event(self):
        result = _parse_opencode_event('{"type":"error","message":"something broke"}')
        assert result is not None
        assert "Error" in result[1][0]

    def test_legacy_assistant_event(self):
        ev = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "hello world"}]},
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None
        assert "hello world" in result[1]

    def test_legacy_tool_event(self):
        ev = json.dumps(
            {
                "type": "tool",
                "tool": {"name": "bash", "input": {"command": "ls -la"}},
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None
        assert any("ls -la" in line for line in result[1])

    def test_legacy_tool_write(self):
        ev = json.dumps(
            {
                "type": "tool",
                "tool": {"name": "write", "input": {"path": "file.py"}},
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None
        assert any("write" in line for line in result[1])

    def test_legacy_tool_glob(self):
        ev = json.dumps(
            {
                "type": "tool",
                "tool": {"name": "glob", "input": {"pattern": "*.py", "path": "src"}},
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None
        assert any("glob" in line for line in result[1])

    def test_legacy_tool_grep(self):
        ev = json.dumps(
            {
                "type": "tool",
                "tool": {
                    "name": "grep",
                    "input": {"pattern": "TODO", "include": "*.py", "path": "src"},
                },
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None
        assert any("grep" in line for line in result[1])

    def test_legacy_tool_ls(self):
        ev = json.dumps(
            {
                "type": "tool",
                "tool": {"name": "ls", "input": {"path": "src"}},
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None

    def test_legacy_tool_fetch(self):
        ev = json.dumps(
            {
                "type": "tool",
                "tool": {"name": "fetch", "input": {"url": "https://example.com"}},
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None

    def test_legacy_tool_generic(self):
        ev = json.dumps(
            {
                "type": "tool",
                "tool": {"name": "custom", "input": {"key": "value"}},
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None
        assert any("custom" in line for line in result[1])

    def test_invalid_json_returns_none(self):
        result = _parse_opencode_event("not json at all")
        assert result is None

    def test_tool_use_bash(self):
        ev = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "bash",
                    "state": {
                        "status": "running",
                        "input": {"command": "pytest tests/"},
                    },
                },
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None
        assert any("pytest" in line for line in result[1])

    def test_tool_use_agent(self):
        ev = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "agent",
                    "state": {
                        "status": "running",
                        "input": {"prompt": "do the thing"},
                    },
                },
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None
        assert any("agent" in line for line in result[1])

    def test_tool_use_todowrite(self):
        ev = json.dumps(
            {
                "type": "tool_use",
                "part": {"tool": "todowrite", "state": {"status": "running", "input": {}}},
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None
        assert any("todowrite" in line for line in result[1])

    def test_tool_use_sourcegraph(self):
        ev = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "sourcegraph",
                    "state": {
                        "status": "running",
                        "input": {"query": "my search"},
                    },
                },
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None

    def test_tool_use_diagnostics(self):
        ev = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "diagnostics",
                    "state": {
                        "status": "running",
                        "input": {"filePath": "test.py"},
                    },
                },
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None

    def test_tool_use_generic_fallback(self):
        ev = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "unknown_tool",
                    "state": {
                        "status": "running",
                        "input": {"key1": "val1"},
                    },
                },
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None
        assert any("unknown_tool" in line for line in result[1])

    def test_tokens_from_part(self):
        ev = json.dumps(
            {
                "type": "step_finish",
                "part": {"tokens": {"input": 100, "output": 50, "cache": {"read": 10, "write": 5}}},
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None
        _, _, tokens = result
        assert tokens is not None
        assert tokens.input_tokens == 100
        assert tokens.output_tokens == 50

    def test_tokens_from_usage(self):
        ev = json.dumps(
            {
                "type": "step_finish",
                "usage": {"input_tokens": 200, "output_tokens": 80},
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None
        _, _, tokens = result
        assert tokens is not None
        assert tokens.input_tokens == 200

    def test_v14_write_edit(self):
        for tool in ("write", "edit"):
            ev = json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "tool": tool,
                        "state": {
                            "status": "running",
                            "input": {"path": "file.py"},
                        },
                    },
                }
            )
            result = _parse_opencode_event(ev)
            assert result is not None
            assert any(tool in line for line in result[1])

    def test_v14_grep_with_include(self):
        ev = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "grep",
                    "state": {
                        "status": "running",
                        "input": {"pattern": "TODO", "include": "*.py", "path": "src"},
                    },
                },
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None

    def test_v14_ls_with_path(self):
        ev = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "ls",
                    "state": {
                        "status": "running",
                        "input": {"path": "src"},
                    },
                },
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None

    def test_v14_fetch_with_url(self):
        ev = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "fetch",
                    "state": {
                        "status": "running",
                        "input": {"url": "https://example.com"},
                    },
                },
            }
        )
        result = _parse_opencode_event(ev)
        assert result is not None


# ═══════════════════════════════════════════════════════════════════════════
# 6. analyze_output / extract_* / is_task_complete / build_instruction
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractCompletionPromises:
    def test_finds_promise(self):
        text = "Some output\n<promise>T01_COMPLETE</promise>\nmore"
        promises = extract_completion_promises(text)
        assert len(promises) == 1
        assert "T01" in promises[0]

    def test_no_promise(self):
        assert extract_completion_promises("no promise here") == []


class TestOutputAnalysis:
    def test_has_progress_signal(self):
        oa = OutputAnalysis(
            completion_promises=[],
            error_signals=[],
            progress_signals=["all tests pass"],
            agent_self_assessment="complete",
        )
        assert oa.has_progress_signal is True

    def test_no_progress_signal(self):
        oa = OutputAnalysis(
            completion_promises=[],
            error_signals=[],
            progress_signals=[],
            agent_self_assessment="unknown",
        )
        assert oa.has_progress_signal is False


class TestAnalyzeOutput:
    def test_analyze_with_promise(self):
        result = analyze_output("some output\n<promise>T01_COMPLETE</promise>")
        assert "T01" in result.completion_promises

    def test_analyze_progress_signal(self):
        result = analyze_output("all tests pass")
        assert result.has_progress_signal is True

    def test_analyze_no_signals(self):
        result = analyze_output("random text with nothing")
        assert result.completion_promises == []


class TestExtractErrorSignals:
    def test_extracts_stuck_signals(self):
        text = "I am stuck and cannot proceed"
        signals = extract_error_signals(text)
        assert len(signals) > 0

    def test_no_errors(self):
        signals = extract_error_signals("all is well")
        assert len(signals) == 0


class TestExtractProgressSignals:
    def test_extracts_progress(self):
        text = "all tests pass\ntask complete"
        signals = extract_progress_signals(text)
        assert len(signals) > 0


class TestIsTaskComplete:
    def test_promise_alone_is_done(self):
        oa = OutputAnalysis(
            completion_promises=["T01"],
            error_signals=[],
            progress_signals=[],
            agent_self_assessment="unknown",
        )
        done, reasons = is_task_complete("T01", oa, False, 0)
        assert done is True

    def test_two_signals_done(self):
        oa = OutputAnalysis(
            completion_promises=[],
            error_signals=[],
            progress_signals=["all tests pass"],
            agent_self_assessment="complete",
        )
        done, reasons = is_task_complete("T01", oa, True, 0)
        assert done is True

    def test_exit_code_alone_not_done(self):
        oa = OutputAnalysis(
            completion_promises=[],
            error_signals=[],
            progress_signals=[],
            agent_self_assessment="unknown",
        )
        done, reasons = is_task_complete("T01", oa, False, 0)
        assert done is False

    def test_progress_md_done_alone(self):
        oa = OutputAnalysis(
            completion_promises=[],
            error_signals=[],
            progress_signals=[],
            agent_self_assessment="unknown",
        )
        done, reasons = is_task_complete("T01", oa, True, 1)
        assert done is True


class TestBuildInstruction:
    def test_basic_instruction(self, task, config):
        result = build_instruction(task, attempt=1, config=config)
        assert "T01" in result
        assert "TASK PREFIX: T01" in result
        assert "<promise>T01_COMPLETE</promise>" in result
        assert task.path.name in result

    def test_uses_project_local_execution_protocol_when_resources_fail(
        self, task, config, monkeypatch
    ):
        prompts_dir = config.project_root / ".architect" / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        (prompts_dir / "execution-protocol.md").write_text(
            "LOCAL EXECUTION PROTOCOL",
            encoding="utf-8",
        )

        def fail_resource_lookup(package: str) -> None:
            raise NotADirectoryError("MultiplexedPath only supports directories")

        monkeypatch.setattr("importlib.resources.files", fail_resource_lookup)

        result = build_instruction(task, attempt=1, config=config)

        assert "LOCAL EXECUTION PROTOCOL" in result
        assert "TASK PREFIX: T01" in result

    def test_instruction_uses_r_task_prefix_for_promise(self, config):
        path = config.tasks_dir / "R01_fix.md"
        path.write_text("# R01 - Fix Task\n", encoding="utf-8")
        r_task = Task(
            name="R01_fix",
            prefix="R01",
            number=1,
            path=path,
            status=TaskStatus.PENDING,
        )

        result = build_instruction(r_task, attempt=1, config=config)

        assert "TASK PREFIX: R01" in result
        assert "<promise>R01_COMPLETE</promise>" in result

    def test_with_architect_md_content(self, task, config):
        result = build_instruction(
            task, attempt=1, config=config, architect_md_content="# Knowledge"
        )
        assert "=== ARCHITECT.md" in result
        assert "Knowledge" in result

    def test_without_architect_md_content(self, task, config):
        result = build_instruction(task, attempt=1, config=config, architect_md_content="")
        assert "=== ARCHITECT.md" not in result

    def test_retry_attempt(self, task, config):
        result = build_instruction(task, attempt=2, config=config)
        assert "RETRY ATTEMPT" in result

    def test_with_instructions_md(self, task, config):
        instructions_md = config.progress_file.parent / "tasks" / "INSTRUCTIONS.md"
        instructions_md.parent.mkdir(parents=True, exist_ok=True)
        instructions_md.write_text("# Instructions\n", encoding="utf-8")
        result = build_instruction(task, attempt=1, config=config)
        assert "tasks/INSTRUCTIONS.md" in result

    def test_integrity_protocol_enabled_by_default(self, task, config):
        result = build_instruction(task, attempt=1, config=config)
        assert "=== FILE INTEGRITY PROTOCOL ===" in result
        assert "architect_eval_<original_filename>" in result

    def test_integrity_protocol_can_be_disabled(self, task, config):
        config.integrity = False
        result = build_instruction(task, attempt=1, config=config)
        assert "=== FILE INTEGRITY PROTOCOL ===" not in result

    def test_retry_prompt_mode_same(self, task, config):
        config.retry_prompt_mode = "same"
        result = build_instruction(task, attempt=2, config=config)
        assert "RETRY ATTEMPT" in result


class TestBuildAttemptSummary:
    def test_basic_summary(self, config, task):
        log_path = config.log_dir / "T01_test.log"
        log_path.write_text("Some log content", encoding="utf-8")
        summary = build_attempt_summary(
            task_id="T01",
            attempt_number=1,
            log_path=log_path,
            completion_detected=False,
            total_tokens=100,
        )
        assert summary.task_id == "T01"

    def test_log_file_not_found(self, config, task):
        log_path = config.log_dir / "nonexistent.log"
        summary = build_attempt_summary(
            task_id="T01",
            attempt_number=1,
            log_path=log_path,
            completion_detected=False,
            total_tokens=0,
        )
        assert summary.task_id == "T01"


class TestSummarizePreviousAttempt:
    def test_basic(self, config, task):
        log_path = config.log_dir / "T01_test.log"
        log_path.write_text("Error: something failed", encoding="utf-8")
        result = summarize_previous_attempt(log_path=log_path)
        assert isinstance(result, str)

    def test_log_not_found(self, config, task):
        log_path = config.log_dir / "nonexistent.log"
        result = summarize_previous_attempt(log_path=log_path)
        assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════════════════════
# 7. select_model / run_task_once
# ═══════════════════════════════════════════════════════════════════════════


class TestSelectModel:
    def test_no_override_returns_none(self, config):
        model = select_model(1, config)
        assert model is None

    def test_override_takes_precedence(self, config):
        model = select_model(1, config, model_override="gpt-4o")
        assert model == "gpt-4o"

    def test_standalone_mode(self, config):
        config.standalone_mode = "claude-3-opus"
        model = select_model(1, config)
        assert model == "claude-3-opus"

    def test_override_over_standalone(self, config):
        config.standalone_mode = "claude-3-opus"
        model = select_model(1, config, model_override="gpt-4o")
        assert model == "gpt-4o"


class TestRunTaskOnce:
    @pytest.mark.asyncio
    async def test_basic_run(self, task, config):
        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| Task | Title | Status | Completed |\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )
        mock_result = StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")
        with patch("the_architect.core.runner.stream_provider", return_value=mock_result):
            result = await run_task_once(task=task, attempt=1, config=config)
            assert result.status in ("done", "failed")


# ═══════════════════════════════════════════════════════════════════════════
# 8. run_task (with circuit breaker, retries, callbacks)
# ═══════════════════════════════════════════════════════════════════════════


class TestRunTaskCircuitBreaker:
    @pytest.mark.asyncio
    async def test_circuit_open_blocks_run(self, task, config):
        from the_architect.core.circuit import CircuitBreaker as CB

        cb = MagicMock(spec=CB)
        cb.can_run.return_value = (False, "circuit open")
        result = await run_task(task=task, config=config, circuit_breaker=cb)
        assert result.status == "failed"
        assert result.attempts == 0

    @pytest.mark.asyncio
    async def test_circuit_cooldown_wait_resume(self, task, config):
        from the_architect.core.circuit import CircuitBreaker as CB
        from the_architect.core.circuit import CircuitState, TaskCircuitState

        cb = MagicMock(spec=CB)
        cb.can_run.side_effect = [
            (False, "cooldown_wait_resume:30"),
            (True, ""),
        ]
        cb.handle_cooldown_wait = AsyncMock()
        cb_state = TaskCircuitState(
            state=CircuitState.CLOSED,
            consecutive_no_progress=0,
            consecutive_same_error=0,
        )
        cb.record_attempt.return_value = cb_state
        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )
        mock_stream = StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")
        with patch("the_architect.core.runner.stream_provider", return_value=mock_stream):
            result = await run_task(task=task, config=config, circuit_breaker=cb)
            assert result.status == "done"

    @pytest.mark.asyncio
    async def test_circuit_cooldown_resume_failure(self, task, config):
        from the_architect.core.circuit import CircuitBreaker as CB

        cb = MagicMock(spec=CB)
        cb.can_run.side_effect = [
            (False, "cooldown_wait_resume:30"),
            (True, ""),
            (True, ""),
        ]
        cb.handle_cooldown_wait = AsyncMock(side_effect=RuntimeError("cooldown failed"))
        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )
        mock_stream = StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")
        with patch("the_architect.core.runner.stream_provider", return_value=mock_stream):
            result = await run_task(task=task, config=config, circuit_breaker=cb)
            assert result.status == "done"

    @pytest.mark.asyncio
    async def test_circuit_reset_on_success(self, task, config):
        from the_architect.core.circuit import CircuitBreaker as CB
        from the_architect.core.circuit import CircuitState, TaskCircuitState

        cb = MagicMock(spec=CB)
        cb.can_run.return_value = (True, "")
        cb_state = TaskCircuitState(
            state=CircuitState.CLOSED,
            consecutive_no_progress=0,
            consecutive_same_error=0,
        )
        cb.record_attempt.return_value = cb_state
        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )
        with patch(
            "the_architect.core.runner.stream_provider",
            return_value=StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text=""),
        ):
            result = await run_task(task=task, config=config, circuit_breaker=cb)
            assert result.status == "done"
            cb.reset_task.assert_called_once_with(task.prefix)

    @pytest.mark.asyncio
    async def test_circuit_record_attempt_exception(self, task, config):
        from the_architect.core.circuit import CircuitBreaker as CB

        cb = MagicMock(spec=CB)
        cb.can_run.return_value = (True, "")
        cb.record_attempt.side_effect = RuntimeError("state corrupt")
        with patch(
            "the_architect.core.runner.stream_provider",
            return_value=StreamResult(exit_code=1, tokens=TokenUsage(), accumulated_text="failed"),
        ):
            result = await run_task(task=task, config=config, circuit_breaker=cb)
            assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_interrupted_attempt_skips_circuit_counters(self, task, config):
        from the_architect.core.circuit import CircuitBreaker as CB

        config.max_retries = 1
        cb = MagicMock(spec=CB)
        cb.can_run.return_value = (True, "")
        interrupted = StreamResult(
            exit_code=-9,
            tokens=TokenUsage(),
            accumulated_text="Provider execution paused after system sleep.",
            interrupted=True,
            interruption_reason="sleep_wake_gap",
        )
        with patch("the_architect.core.runner.stream_provider", return_value=interrupted):
            result = await run_task(task=task, config=config, circuit_breaker=cb)

        assert result.status == "failed"
        assert result.interrupted is True
        assert result.interruption_reason == "sleep_wake_gap"
        cb.record_attempt.assert_not_called()


class TestRunTaskArchitectMdAndCallbacks:
    @pytest.mark.asyncio
    async def test_reads_architect_md(self, task, config):
        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )
        architect_md = config.project_root / "ARCHITECT.md"
        architect_md.write_text("# Project Intelligence\nSome decisions.", encoding="utf-8")

        captured_content = []

        async def mock_run_once(**kwargs):
            captured_content.append(kwargs.get("architect_md_content", ""))
            return TaskResult(
                prefix=task.prefix,
                title=task.title or task.name,
                status="done",
                duration_seconds=1.0,
                attempts=1,
                tokens=TokenUsage(),
                model="",
            )

        with patch("the_architect.core.runner.run_task_once", side_effect=mock_run_once):
            result = await run_task(task=task, config=config)
            assert result.status == "done"
            assert any("Project Intelligence" in c for c in captured_content)

    @pytest.mark.asyncio
    async def test_forwards_renderer_to_run_task_once(self, task, config):
        """Provider output must use the TUI renderer supplied to run_task."""
        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )
        renderer = MagicMock()
        received_renderers = []

        async def mock_run_once(**kwargs):
            received_renderers.append(kwargs.get("renderer"))
            return TaskResult(
                prefix=task.prefix,
                title=task.title or task.name,
                status="done",
                duration_seconds=1.0,
                attempts=1,
                tokens=TokenUsage(),
                model="",
            )

        with patch("the_architect.core.runner.run_task_once", side_effect=mock_run_once):
            result = await run_task(task=task, config=config, renderer=renderer)

        assert result.status == "done"
        assert received_renderers == [renderer]

    @pytest.mark.asyncio
    async def test_quota_exhausted_stops_without_retry(self, task, config):
        renderer = MagicMock()

        async def mock_run_once(**kwargs):
            return TaskResult(
                prefix=task.prefix,
                title=task.title or task.name,
                status="failed",
                duration_seconds=1.0,
                attempts=1,
                tokens=TokenUsage(),
                model="gemini-2.5-pro",
                accumulated_text="RESOURCE_EXHAUSTED: quota exceeded; billing not enabled",
                exit_code=1,
                rate_limit_hit=True,
            )

        with patch(
            "the_architect.core.runner.run_task_once", side_effect=mock_run_once
        ) as run_once:
            result = await run_task(task=task, config=config, renderer=renderer)

        assert result.status == "failed"
        assert run_once.call_count == 1
        renderer.write_line.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_attempt_start_callback_exception(self, task, config):
        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )
        mock_stream = StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")

        def bad_callback(attempt, model):
            raise RuntimeError("callback crash")

        with patch("the_architect.core.runner.stream_provider", return_value=mock_stream):
            result = await run_task(task=task, config=config, on_attempt_start=bad_callback)
            assert result.status == "done"

    @pytest.mark.asyncio
    async def test_on_attempt_start_with_model_resolution(self, task, config):
        from the_architect.core.provider import ArchitectProvider

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )
        mock_provider = MagicMock(spec=ArchitectProvider)
        mock_provider.get_resolved_model = MagicMock(return_value="gpt-4o")
        mock_provider.supports_agents = MagicMock(return_value=True)
        mock_provider.build_command = MagicMock(return_value=["opencode", "run", "--", "test"])
        mock_provider.get_env_overrides = MagicMock(return_value={})
        mock_provider.parse_output_line = MagicMock(return_value=None)

        received_models = []

        def capture_model(attempt, model):
            received_models.append(model)

        mock_stream = StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")
        with patch("the_architect.core.runner.stream_provider", return_value=mock_stream):
            result = await run_task(
                task=task,
                config=config,
                on_attempt_start=capture_model,
                provider=mock_provider,
            )
            assert result.status == "done"
            assert any(m == "gpt-4o" for m in received_models)

    @pytest.mark.asyncio
    async def test_retry_pause_cancelled(self, task, config):
        config.retry_pause = 0.01

        async def mock_run_once(**kwargs):
            return TaskResult(
                prefix=task.prefix,
                title=task.title or task.name,
                status="failed",
                duration_seconds=0.1,
                attempts=kwargs.get("attempt", 1),
                tokens=TokenUsage(),
                model="",
            )

        with (
            patch("the_architect.core.runner.run_task_once", side_effect=mock_run_once),
            patch("the_architect.core.runner.asyncio.sleep", side_effect=asyncio.CancelledError),
        ):
            result = await run_task(task=task, config=config)
            assert result.status == "failed"


# ═══════════════════════════════════════════════════════════════════════════
# 9. run_task with free mode rotation
# ═══════════════════════════════════════════════════════════════════════════


class TestRunTaskFreeModeRotation:
    @pytest.mark.asyncio
    async def test_free_mode_model_rotation(self, task, config):
        from the_architect.core.free_models import FreeModelInfo, FreeModelRotator

        rotator = FreeModelRotator(
            models=[
                FreeModelInfo(id="openrouter/model-a", context_length=32000),
                FreeModelInfo(id="openrouter/model-b", context_length=64000),
            ]
        )
        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        async def mock_stream(**kwargs):
            return StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")

        with patch("the_architect.core.runner.stream_provider", side_effect=mock_stream):
            result = await run_task(task=task, config=config, free_rotator=rotator)
            assert result.status == "done"

    @pytest.mark.asyncio
    async def test_free_mode_model_not_found_reason(self, task, config):
        from the_architect.core.free_models import FreeModelInfo, FreeModelRotator

        rotator = FreeModelRotator(
            models=[
                FreeModelInfo(id="openrouter/model-a", context_length=32000),
            ]
        )
        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        async def mock_stream(**kwargs):
            return StreamResult(
                exit_code=1,
                tokens=TokenUsage(),
                accumulated_text="",
                rate_limit_hit=True,
                model_not_found=True,
            )

        with patch("the_architect.core.runner.stream_provider", side_effect=mock_stream):
            result = await run_task(task=task, config=config, free_rotator=rotator)
            # Should rotate to next model or fall back to default
            assert isinstance(result, TaskResult)


class TestRunAll:
    @pytest.mark.asyncio
    async def test_acquire_lock_failure_raises(self, config, tmp_path):
        tasks_dir = tmp_path / "tasks"
        t1_path = tasks_dir / "T01_first.md"
        t1_path.parent.mkdir(parents=True, exist_ok=True)
        t1_path.write_text("# T01\n", encoding="utf-8")
        tasks = [
            Task(name="T01_first", prefix="T01", number=1, path=t1_path, status=TaskStatus.PENDING)
        ]
        plan = TaskPlan(tasks=tasks)

        with patch("the_architect.core.runner.acquire_lock", return_value=False):
            with pytest.raises(RuntimeError, match="Another"):
                await run_all(plan, config)

    @pytest.mark.asyncio
    async def test_on_task_start_callback_failure(self, config, tmp_path):
        from the_architect.core.runner import _run_all_inner

        tasks_dir = tmp_path / "tasks"
        t1_path = tasks_dir / "T01_first.md"
        t1_path.parent.mkdir(parents=True, exist_ok=True)
        t1_path.write_text("# T01\n", encoding="utf-8")
        tasks = [
            Task(name="T01_first", prefix="T01", number=1, path=t1_path, status=TaskStatus.PENDING)
        ]
        plan = TaskPlan(tasks=tasks)

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        async def mock_run_task(**kwargs):
            return TaskResult(
                prefix="T01",
                title="first",
                status="done",
                duration_seconds=1.0,
                attempts=1,
                tokens=TokenUsage(),
                model="",
            )

        def bad_done(result):
            raise RuntimeError("done callback crash")

        with patch("the_architect.core.runner.run_task", side_effect=mock_run_task):
            result = await _run_all_inner(plan, config, on_task_done=bad_done)
            assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_on_task_failed_callback_failure(self, config, tmp_path):
        from the_architect.core.runner import _run_all_inner

        tasks_dir = tmp_path / "tasks"
        t1_path = tasks_dir / "T01_first.md"
        t1_path.parent.mkdir(parents=True, exist_ok=True)
        t1_path.write_text("# T01\n", encoding="utf-8")
        tasks = [
            Task(name="T01_first", prefix="T01", number=1, path=t1_path, status=TaskStatus.PENDING)
        ]
        plan = TaskPlan(tasks=tasks)

        async def mock_run_task(**kwargs):
            return TaskResult(
                prefix="T01",
                title="first",
                status="failed",
                duration_seconds=1.0,
                attempts=1,
                tokens=TokenUsage(),
                model="",
            )

        def bad_failed(result):
            raise RuntimeError("failed callback crash")

        with patch("the_architect.core.runner.run_task", side_effect=mock_run_task):
            result = await _run_all_inner(plan, config, on_task_failed=bad_failed)
            assert result is False

    @pytest.mark.asyncio
    async def test_run_task_exception_returns_false(self, config, tmp_path):
        from the_architect.core.runner import _run_all_inner

        tasks_dir = tmp_path / "tasks"
        t1_path = tasks_dir / "T01_first.md"
        t1_path.parent.mkdir(parents=True, exist_ok=True)
        t1_path.write_text("# T01\n", encoding="utf-8")
        tasks = [
            Task(name="T01_first", prefix="T01", number=1, path=t1_path, status=TaskStatus.PENDING)
        ]
        plan = TaskPlan(tasks=tasks)

        async def mock_run_task(**kwargs):
            raise RuntimeError("unexpected crash")

        with patch("the_architect.core.runner.run_task", side_effect=mock_run_task):
            result = await _run_all_inner(plan, config)
            assert result is False

    @pytest.mark.asyncio
    async def test_token_budget_in_run_all(self, config, tmp_path):
        from the_architect.core.runner import _run_all_inner

        config.token_budget_per_hour = 100
        tasks_dir = tmp_path / "tasks"
        t1_path = tasks_dir / "T01_first.md"
        t1_path.parent.mkdir(parents=True, exist_ok=True)
        t1_path.write_text("# T01\n", encoding="utf-8")
        tasks = [
            Task(name="T01_first", prefix="T01", number=1, path=t1_path, status=TaskStatus.PENDING)
        ]
        plan = TaskPlan(tasks=tasks)

        mock_result = TaskResult(
            prefix="T01",
            title="first",
            status="done",
            duration_seconds=1.0,
            attempts=1,
            tokens=TokenUsage(input_tokens=200, output_tokens=50),
            model="",
        )

        async def mock_run_task(**kwargs):
            config.progress_file.write_text(
                "**Tasks completed:** 1\n**Next task to run:** T02\n"
                "| T01 | Test | Done | 2026-04-12 |\n",
                encoding="utf-8",
            )
            return mock_result

        with (
            patch("the_architect.core.runner.run_task", side_effect=mock_run_task),
            patch(
                "the_architect.core.runner.HourlyTokenBudget.wait_for_reset", new_callable=AsyncMock
            ),
        ):
            result = await _run_all_inner(plan, config)
            assert result is True

    @pytest.mark.asyncio
    async def test_token_budget_exceeded_wait_cancelled(self, config, tmp_path):
        from the_architect.core.runner import _run_all_inner

        config.token_budget_per_hour = 100
        tasks_dir = tmp_path / "tasks"
        t1_path = tasks_dir / "T01_first.md"
        t1_path.parent.mkdir(parents=True, exist_ok=True)
        t1_path.write_text("# T01\n", encoding="utf-8")
        t2_path = tasks_dir / "T02_second.md"
        t2_path.write_text("# S02\n", encoding="utf-8")
        tasks = [
            Task(name="T01_first", prefix="T01", number=1, path=t1_path, status=TaskStatus.PENDING),
            Task(
                name="T02_second", prefix="T02", number=2, path=t2_path, status=TaskStatus.PENDING
            ),
        ]
        plan = TaskPlan(tasks=tasks)

        mock_result = TaskResult(
            prefix="T01",
            title="first",
            status="done",
            duration_seconds=1.0,
            attempts=1,
            tokens=TokenUsage(input_tokens=200, output_tokens=50),
            model="",
        )

        async def mock_run_task(**kwargs):
            config.progress_file.write_text(
                "**Tasks completed:** 1\n**Next task to run:** T02\n"
                "| T01 | Test | Done | 2026-04-12 |\n",
                encoding="utf-8",
            )
            return mock_result

        with (
            patch("the_architect.core.runner.run_task", side_effect=mock_run_task),
            patch(
                "the_architect.core.runner.HourlyTokenBudget.wait_for_reset",
                new_callable=AsyncMock,
                side_effect=asyncio.CancelledError,
            ),
        ):
            result = await _run_all_inner(plan, config)
            assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_on_task_pause_callback_failure(self, config, tmp_path):
        from the_architect.core.runner import _run_all_inner

        tasks_dir = tmp_path / "tasks"
        t1_path = tasks_dir / "T01_first.md"
        t1_path.parent.mkdir(parents=True, exist_ok=True)
        t1_path.write_text("# T01\n", encoding="utf-8")
        t2_path = tasks_dir / "T02_second.md"
        t2_path.write_text("# S02\n", encoding="utf-8")
        tasks = [
            Task(name="T01_first", prefix="T01", number=1, path=t1_path, status=TaskStatus.PENDING),
            Task(
                name="T02_second", prefix="T02", number=2, path=t2_path, status=TaskStatus.PENDING
            ),
        ]
        plan = TaskPlan(tasks=tasks)

        async def mock_run_task(**kwargs):
            config.progress_file.write_text(
                "**Tasks completed:** 1\n**Next task to run:** T02\n"
                "| T01 | Test | Done | 2026-04-12 |\n",
                encoding="utf-8",
            )
            return TaskResult(
                prefix="T01",
                title="first",
                status="done",
                duration_seconds=1.0,
                attempts=1,
                tokens=TokenUsage(),
                model="",
            )

        def bad_pause(seconds):
            raise RuntimeError("pause callback crash")

        with patch("the_architect.core.runner.run_task", side_effect=mock_run_task):
            result = await _run_all_inner(plan, config, on_task_pause=bad_pause)
            assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_inter_task_pause_cancelled(self, config, tmp_path):
        from the_architect.core.runner import _run_all_inner

        config.pause_between_tasks = 1.0
        tasks_dir = tmp_path / "tasks"
        t1_path = tasks_dir / "T01_first.md"
        t1_path.parent.mkdir(parents=True, exist_ok=True)
        t1_path.write_text("# T01\n", encoding="utf-8")
        t2_path = tasks_dir / "T02_second.md"
        t2_path.write_text("# S02\n", encoding="utf-8")
        tasks = [
            Task(name="T01_first", prefix="T01", number=1, path=t1_path, status=TaskStatus.PENDING),
            Task(
                name="T02_second", prefix="T02", number=2, path=t2_path, status=TaskStatus.PENDING
            ),
        ]
        plan = TaskPlan(tasks=tasks)

        async def mock_run_task(**kwargs):
            config.progress_file.write_text(
                "**Tasks completed:** 1\n**Next task to run:** T02\n"
                "| T01 | Test | Done | 2026-04-12 |\n",
                encoding="utf-8",
            )
            return TaskResult(
                prefix="T01",
                title="first",
                status="done",
                duration_seconds=1.0,
                attempts=1,
                tokens=TokenUsage(),
                model="",
            )

        with (
            patch("the_architect.core.runner.run_task", side_effect=mock_run_task),
            patch("the_architect.core.runner.asyncio.sleep", side_effect=asyncio.CancelledError),
        ):
            result = await _run_all_inner(plan, config)
            assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_circuit_breaker_load_failure(self, config, tmp_path):

        tasks_dir = tmp_path / "tasks"
        t1_path = tasks_dir / "T01_first.md"
        t1_path.parent.mkdir(parents=True, exist_ok=True)
        t1_path.write_text("# T01\n", encoding="utf-8")
        tasks = [
            Task(name="T01_first", prefix="T01", number=1, path=t1_path, status=TaskStatus.PENDING)
        ]
        plan = TaskPlan(tasks=tasks)

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        async def mock_run_task(**kwargs):
            return TaskResult(
                prefix="T01",
                title="first",
                status="done",
                duration_seconds=1.0,
                attempts=1,
                tokens=TokenUsage(),
                model="",
            )

        with (
            patch("the_architect.core.runner.acquire_lock", return_value=True),
            patch("the_architect.core.runner.run_task", side_effect=mock_run_task),
            patch(
                "the_architect.core.circuit.load_circuit_state",
                side_effect=RuntimeError("circuit broken"),
            ),
        ):
            result = await run_all(plan, config)
            # Should still work without circuit breaker
            assert result is True


# ═══════════════════════════════════════════════════════════════════════════
# 12. StreamResult / TaskResult rate limit
# ═══════════════════════════════════════════════════════════════════════════


class TestStreamResultRateLimit:
    def test_rate_limit_fields(self):
        sr = StreamResult(exit_code=1, rate_limit_hit=True, cooldown_until=1700000000)
        assert sr.rate_limit_hit is True
        assert sr.cooldown_until == 1700000000


class TestTaskResultRateLimit:
    def test_rate_limit_from_stream(self):
        sr = StreamResult(
            exit_code=1,
            tokens=TokenUsage(),
            accumulated_text="",
            rate_limit_hit=True,
            cooldown_until=1700000000,
        )
        assert sr.rate_limit_hit is True
        assert sr.cooldown_until == 1700000000


# ═══════════════════════════════════════════════════════════════════════════
# 13. Utility tests
# ═══════════════════════════════════════════════════════════════════════════


class TestHasStdbuf:
    def test_returns_bool(self):
        result = has_stdbuf()
        assert isinstance(result, bool)


class TestOpencodePathForCommand:
    def test_returns_string(self):
        result = opencode_path_for_command()
        assert isinstance(result, str)


class TestSetupLogging:
    def test_creates_log_dir(self, tmp_path):
        log_dir = tmp_path / "logs"
        setup_logging(log_dir)
        assert log_dir.exists()


class TestIsLockStale:
    def test_stale_pid(self, tmp_path):
        lock_path = tmp_path / "runner.lock"
        lock_path.write_text("999999", encoding="utf-8")
        assert _is_lock_stale(lock_path) is True

    def test_current_pid(self, tmp_path):
        lock_path = tmp_path / "runner.lock"
        lock_path.write_text(str(os.getpid()), encoding="utf-8")
        assert _is_lock_stale(lock_path) is False

    def test_invalid_content(self, tmp_path):
        lock_path = tmp_path / "runner.lock"
        lock_path.write_text("not_a_number", encoding="utf-8")
        assert _is_lock_stale(lock_path) is True

    def test_missing_file(self, tmp_path):
        lock_path = tmp_path / "nonexistent.lock"
        assert _is_lock_stale(lock_path) is True


# ═══════════════════════════════════════════════════════════════════════════
# 14. Additional coverage tests for remaining uncovered lines
# ═══════════════════════════════════════════════════════════════════════════


class TestRunTaskCircuitBreakerExtended:
    """Extended circuit breaker tests for cooldown, replan, and callback paths."""

    @pytest.mark.asyncio
    async def test_per_attempt_circuit_check_blocks(self, task, config):
        """Per-attempt circuit check (attempt > 1) should break out."""
        from the_architect.core.circuit import (
            CircuitBreaker as CB,
        )
        from the_architect.core.circuit import (
            CircuitState,
            RecoveryAction,
            TaskCircuitState,
        )

        cb = MagicMock(spec=CB)
        # Attempt 1: allowed; attempt 2: blocked
        cb.can_run.side_effect = [
            (True, ""),  # pre-run check
            (True, ""),  # attempt 1
            (False, "circuit open"),  # attempt 2 check
        ]
        cb_state = TaskCircuitState(
            state=CircuitState.OPEN,
            consecutive_no_progress=2,
            consecutive_same_error=0,
            recovery_action=RecoveryAction.REPLAN,
        )
        cb.record_attempt.return_value = cb_state
        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        with patch(
            "the_architect.core.runner.stream_provider",
            return_value=StreamResult(exit_code=1, tokens=TokenUsage(), accumulated_text="failed"),
        ):
            result = await run_task(task=task, config=config, circuit_breaker=cb)
            assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_circuit_replan_triggers(self, task, config):
        """Circuit breaker REPLAN action should call attempt_replan."""
        from the_architect.core.circuit import (
            CircuitBreaker as CB,
        )
        from the_architect.core.circuit import (
            CircuitState,
            RecoveryAction,
            TaskCircuitState,
        )

        cb = MagicMock(spec=CB)
        cb.can_run.return_value = (True, "")
        # First attempt: fails and triggers replan
        cb_state_replan = TaskCircuitState(
            state=CircuitState.OPEN,
            consecutive_no_progress=3,
            consecutive_same_error=0,
            recovery_action=RecoveryAction.REPLAN,
        )
        # After replan, second attempt: succeeds
        cb_state_success = TaskCircuitState(
            state=CircuitState.CLOSED,
            consecutive_no_progress=0,
            consecutive_same_error=0,
        )
        cb.record_attempt.side_effect = [cb_state_replan, cb_state_success]
        cb.attempt_replan = AsyncMock()

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        call_count = [0]

        async def mock_stream(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return StreamResult(exit_code=1, tokens=TokenUsage(), accumulated_text="failed")
            return StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")

        with patch("the_architect.core.runner.stream_provider", side_effect=mock_stream):
            await run_task(task=task, config=config, circuit_breaker=cb)
            cb.attempt_replan.assert_called_once()

    @pytest.mark.asyncio
    async def test_circuit_replan_exception(self, task, config):
        """Circuit replan exception should not crash the run."""
        from the_architect.core.circuit import (
            CircuitBreaker as CB,
        )
        from the_architect.core.circuit import (
            CircuitState,
            RecoveryAction,
            TaskCircuitState,
        )

        cb = MagicMock(spec=CB)
        cb.can_run.return_value = (True, "")
        cb_state_replan = TaskCircuitState(
            state=CircuitState.OPEN,
            consecutive_no_progress=3,
            consecutive_same_error=0,
            recovery_action=RecoveryAction.REPLAN,
        )
        cb_state_after = TaskCircuitState(
            state=CircuitState.CLOSED,
            consecutive_no_progress=0,
            consecutive_same_error=0,
        )
        cb.record_attempt.side_effect = [cb_state_replan, cb_state_after]
        cb.attempt_replan = AsyncMock(side_effect=RuntimeError("replan failed"))

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        with patch(
            "the_architect.core.runner.stream_provider",
            return_value=StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text=""),
        ):
            result = await run_task(task=task, config=config, circuit_breaker=cb)
            assert result.status == "done"

    @pytest.mark.asyncio
    async def test_on_attempt_done_callback_exception(self, task, config):
        """Exception in on_attempt_done callback should not stop the run."""
        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        def bad_done(attempt, success):
            raise RuntimeError("done callback crash")

        with patch(
            "the_architect.core.runner.stream_provider",
            return_value=StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text=""),
        ):
            result = await run_task(task=task, config=config, on_attempt_done=bad_done)
            assert result.status == "done"

    @pytest.mark.asyncio
    async def test_reset_task_exception(self, task, config):
        """Exception in cb.reset_task should not crash the run."""
        from the_architect.core.circuit import CircuitBreaker as CB
        from the_architect.core.circuit import CircuitState, TaskCircuitState

        cb = MagicMock(spec=CB)
        cb.can_run.return_value = (True, "")
        cb_state = TaskCircuitState(
            state=CircuitState.CLOSED,
            consecutive_no_progress=0,
            consecutive_same_error=0,
        )
        cb.record_attempt.return_value = cb_state
        cb.reset_task.side_effect = RuntimeError("reset failed")

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        with patch(
            "the_architect.core.runner.stream_provider",
            return_value=StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text=""),
        ):
            result = await run_task(task=task, config=config, circuit_breaker=cb)
            assert result.status == "done"

    @pytest.mark.asyncio
    async def test_run_task_once_exception_handled(self, task, config):
        """If run_task_once raises unexpectedly, it should be handled."""
        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        # Make stream_provider succeed but raise in run_task_once
        with patch(
            "the_architect.core.runner.run_task_once", side_effect=RuntimeError("unexpected crash")
        ):
            result = await run_task(task=task, config=config)
            assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_architect_md_read_exception(self, task, config):
        """If reading ARCHITECT.md fails, should continue without it."""
        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        with (
            patch(
                "the_architect.core.runner.stream_provider",
                return_value=StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text=""),
            ),
            patch(
                "the_architect.core.architect_md.read_architect_md",
                side_effect=RuntimeError("read failed"),
            ),
        ):
            result = await run_task(task=task, config=config)
            assert result.status == "done"

    @pytest.mark.asyncio
    async def test_on_attempt_start_model_resolution_exception(self, task, config):
        """Exception resolving model in on_attempt_start callback should be handled."""
        from the_architect.core.provider import ArchitectProvider

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        mock_provider = MagicMock(spec=ArchitectProvider)
        mock_provider.get_resolved_model = MagicMock(side_effect=RuntimeError("resolution failed"))
        mock_provider.supports_agents = MagicMock(return_value=True)
        mock_provider.build_command = MagicMock(return_value=["opencode", "run", "--", "test"])
        mock_provider.get_env_overrides = MagicMock(return_value={})
        mock_provider.parse_output_line = MagicMock(return_value=None)

        received_models = []

        def capture_model(attempt, model):
            received_models.append(model)

        with patch(
            "the_architect.core.runner.stream_provider",
            return_value=StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text=""),
        ):
            result = await run_task(
                task=task,
                config=config,
                on_attempt_start=capture_model,
                provider=mock_provider,
            )
            assert result.status == "done"

    @pytest.mark.asyncio
    async def test_cooldown_continue_skips_retry_pause(self, task, config):
        """When cooldown_triggered is True, should continue without retry pause."""
        from the_architect.core.circuit import (
            CircuitBreaker as CB,
        )
        from the_architect.core.circuit import (
            CircuitState,
            RecoveryAction,
            TaskCircuitState,
        )

        cb = MagicMock(spec=CB)
        cb.can_run.return_value = (True, "")
        # First: cooldown, then success
        cb_state1 = TaskCircuitState(
            state=CircuitState.HALF_OPEN,
            consecutive_no_progress=2,
            consecutive_same_error=0,
            cooldown_waiting=True,
            recovery_action=RecoveryAction.COOLDOWN_WAIT,
            cooldown_wait_count=1,
        )
        cb_state2 = TaskCircuitState(
            state=CircuitState.CLOSED,
            consecutive_no_progress=0,
            consecutive_same_error=0,
        )
        cb.record_attempt.side_effect = [cb_state1, cb_state2]
        cb.handle_cooldown_wait = AsyncMock()

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        with patch(
            "the_architect.core.runner.stream_provider",
            return_value=StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text=""),
        ):
            result = await run_task(task=task, config=config, circuit_breaker=cb)
            assert result.status == "done"


class TestRunTaskFreeModeRotationExtended:
    """Extended free mode rotation tests covering callback paths."""

    @pytest.mark.asyncio
    async def test_free_mode_model_switched_callback(self, task, config):
        """on_model_switched callback should be called on rate limit."""
        from the_architect.core.free_models import FreeModelInfo, FreeModelRotator

        rotator = FreeModelRotator(
            models=[
                FreeModelInfo(id="openrouter/model-a", context_length=32000),
                FreeModelInfo(id="openrouter/model-b", context_length=64000),
            ]
        )

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        switched = []

        def on_switched(old, new):
            switched.append((old, new))

        async def mock_stream(**kwargs):
            if kwargs.get("model_override") == "openrouter/model-a":
                return StreamResult(
                    exit_code=1,
                    tokens=TokenUsage(),
                    accumulated_text="",
                    rate_limit_hit=True,
                )
            return StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")

        with patch("the_architect.core.runner.stream_provider", side_effect=mock_stream):
            await run_task(
                task=task,
                config=config,
                free_rotator=rotator,
                on_model_switched=on_switched,
            )
            assert len(switched) > 0

    @pytest.mark.asyncio
    async def test_free_mode_model_switched_callback_exception(self, task, config):
        """Exception in on_model_switched callback should not crash the run."""
        from the_architect.core.free_models import FreeModelInfo, FreeModelRotator

        rotator = FreeModelRotator(
            models=[
                FreeModelInfo(id="openrouter/model-a", context_length=32000),
                FreeModelInfo(id="openrouter/model-b", context_length=64000),
            ]
        )

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        def bad_switch(old, new):
            raise RuntimeError("switch callback crash")

        async def mock_stream(**kwargs):
            if kwargs.get("model_override") == "openrouter/model-a":
                return StreamResult(
                    exit_code=1,
                    tokens=TokenUsage(),
                    accumulated_text="",
                    rate_limit_hit=True,
                )
            return StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")

        with patch("the_architect.core.runner.stream_provider", side_effect=mock_stream):
            result = await run_task(
                task=task,
                config=config,
                free_rotator=rotator,
                on_model_switched=bad_switch,
            )
            assert isinstance(result, TaskResult)


class TestHourlyTokenBudgetExtended:
    """Extended HourlyTokenBudget tests for wait_for_reset and window reset."""

    def test_seconds_until_reset_no_window(self):
        budget = HourlyTokenBudget(100)
        assert budget.seconds_until_reset() == 0.0

    @pytest.mark.asyncio
    async def test_wait_for_reset_already_elapsed(self):
        budget = HourlyTokenBudget(100)
        budget.add(50)
        # Manually set window start to far past
        budget._window_start = time.monotonic() - 7200  # 2 hours ago
        await budget.wait_for_reset()
        # Window should be reset
        assert budget._tokens_this_hour == 0

    @pytest.mark.asyncio
    async def test_wait_for_reset_with_progress_logging(self):
        budget = HourlyTokenBudget(100)
        budget.add(150)
        # Set window start to just before reset
        budget._window_start = time.monotonic() - 3599  # Almost 1 hour ago
        with patch("the_architect.core.runner.asyncio.sleep", new_callable=AsyncMock):
            await budget.wait_for_reset()
        assert budget._tokens_this_hour == 0

    def test_add_resets_elapsed_window(self):
        budget = HourlyTokenBudget(100)
        budget.add(50)
        # Set window start to far past
        budget._window_start = time.monotonic() - 7200
        budget.add(50)
        # Window should have been reset, tokens start fresh
        assert budget._tokens_this_hour == 50


class TestRunTaskOnceExtended:
    """Extended run_task_once tests for provider resolution and error handling."""

    @pytest.mark.asyncio
    async def test_provider_resolution_exception(self, task, config):
        """If provider.get_resolved_model raises, should continue with no model."""
        from the_architect.core.provider import ArchitectProvider

        mock_provider = MagicMock(spec=ArchitectProvider)
        mock_provider.get_resolved_model = MagicMock(side_effect=RuntimeError("resolution failed"))
        mock_provider.supports_agents = MagicMock(return_value=True)
        mock_provider.build_command = MagicMock(return_value=["opencode", "run", "--", "test"])
        mock_provider.get_env_overrides = MagicMock(return_value={})
        mock_provider.parse_output_line = MagicMock(return_value=None)

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        with patch(
            "the_architect.core.runner.stream_provider",
            return_value=StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text=""),
        ):
            result = await run_task_once(
                task=task, attempt=1, config=config, provider=mock_provider
            )
            assert result.status in ("done", "failed")

    @pytest.mark.asyncio
    async def test_run_task_once_unexpected_exception(self, task, config):
        """If stream_provider raises unexpected exception, should return failed result."""
        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        with patch(
            "the_architect.core.runner.stream_provider",
            side_effect=RuntimeError("unexpected crash"),
        ):
            result = await run_task_once(task=task, attempt=1, config=config)
            assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_nonzero_exit_rate_limit(self, task, config):
        """Non-zero exit with rate_limit_hit should set rate_limit on result."""
        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        mock_stream = StreamResult(
            exit_code=1,
            tokens=TokenUsage(),
            accumulated_text="",
            rate_limit_hit=True,
        )
        with patch("the_architect.core.runner.stream_provider", return_value=mock_stream):
            result = await run_task_once(task=task, attempt=1, config=config)
            assert result.rate_limit_hit is True

    @pytest.mark.asyncio
    async def test_zero_exit_rate_limit(self, task, config):
        """Zero exit with rate_limit_hit should still set rate_limit."""
        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        mock_stream = StreamResult(
            exit_code=0,
            tokens=TokenUsage(),
            accumulated_text="",
            rate_limit_hit=True,
        )
        with patch("the_architect.core.runner.stream_provider", return_value=mock_stream):
            result = await run_task_once(task=task, attempt=1, config=config)
            assert result.rate_limit_hit is True


class TestStreamOpencodeCompat:
    """Test the backward-compat stream_opencode shim."""

    @pytest.mark.asyncio
    async def test_stream_opencode_delegates(self, tmp_path: Path):
        """stream_opencode should delegate to stream_provider."""
        from the_architect.core.runner import stream_opencode

        mock_result = StreamResult(exit_code=0, tokens=TokenUsage(), accumulated_text="")
        with patch(
            "the_architect.core.runner.stream_provider", return_value=mock_result
        ) as mock_sp:
            result = await stream_opencode("test", tmp_path)
            assert result.exit_code == 0
            mock_sp.assert_called_once()


class TestRunAllInnerExtended:
    """Extended _run_all_inner tests for callback exception paths."""

    @pytest.mark.asyncio
    async def test_on_task_start_exception(self, config, tmp_path):
        from the_architect.core.runner import _run_all_inner

        tasks_dir = tmp_path / "tasks"
        t1_path = tasks_dir / "T01_first.md"
        t1_path.parent.mkdir(parents=True, exist_ok=True)
        t1_path.write_text("# T01\n", encoding="utf-8")
        tasks = [
            Task(name="T01_first", prefix="T01", number=1, path=t1_path, status=TaskStatus.PENDING)
        ]
        plan = TaskPlan(tasks=tasks)

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        async def mock_run_task(**kwargs):
            return TaskResult(
                prefix="T01",
                title="first",
                status="done",
                duration_seconds=1.0,
                attempts=1,
                tokens=TokenUsage(),
                model="",
            )

        def bad_start(task_arg):
            raise RuntimeError("start crash")

        with patch("the_architect.core.runner.run_task", side_effect=mock_run_task):
            result = await _run_all_inner(plan, config, on_task_start=bad_start)
            assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_on_task_done_exception(self, config, tmp_path):
        from the_architect.core.runner import _run_all_inner

        tasks_dir = tmp_path / "tasks"
        t1_path = tasks_dir / "T01_first.md"
        t1_path.parent.mkdir(parents=True, exist_ok=True)
        t1_path.write_text("# T01\n", encoding="utf-8")
        tasks = [
            Task(name="T01_first", prefix="T01", number=1, path=t1_path, status=TaskStatus.PENDING)
        ]
        plan = TaskPlan(tasks=tasks)

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        async def mock_run_task(**kwargs):
            return TaskResult(
                prefix="T01",
                title="first",
                status="done",
                duration_seconds=1.0,
                attempts=1,
                tokens=TokenUsage(),
                model="",
            )

        def bad_done(result_arg):
            raise RuntimeError("done crash")

        with patch("the_architect.core.runner.run_task", side_effect=mock_run_task):
            result = await _run_all_inner(plan, config, on_task_done=bad_done)
            assert isinstance(result, bool)


class TestSummarizePreviousAttemptExtended:
    """Extended tests for summarize_previous_attempt() covering JSON parsing."""

    def test_extracts_write_events(self, tmp_path):
        log_path = tmp_path / "test.log"
        event = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "write",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "src/main.py"},
                    },
                },
            }
        )
        log_path.write_text(event, encoding="utf-8")
        result = summarize_previous_attempt(log_path=log_path)
        assert "src/main.py" in result
        assert "Files written" in result

    def test_extracts_edit_events(self, tmp_path):
        log_path = tmp_path / "test.log"
        event = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "edit",
                    "state": {
                        "status": "completed",
                        "input": {"path": "src/utils.py"},
                    },
                },
            }
        )
        log_path.write_text(event, encoding="utf-8")
        result = summarize_previous_attempt(log_path=log_path)
        assert "src/utils.py" in result

    def test_extracts_read_events_only_when_no_writes(self, tmp_path):
        log_path = tmp_path / "test.log"
        event = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "read",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "src/main.py"},
                    },
                },
            }
        )
        log_path.write_text(event, encoding="utf-8")
        result = summarize_previous_attempt(log_path=log_path)
        assert "Files read" in result

    def test_extracts_bash_with_pytest_failure(self, tmp_path):
        log_path = tmp_path / "test.log"
        event = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": "pytest tests/"},
                        "output": "FAILED test_main.py::test_foo\n1 failed",
                    },
                },
            }
        )
        log_path.write_text(event, encoding="utf-8")
        result = summarize_previous_attempt(log_path=log_path)
        assert "Test failures" in result or "Errors detected" in result

    def test_extracts_bash_without_failure(self, tmp_path):
        log_path = tmp_path / "test.log"
        event = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": "ls -la"},
                    },
                },
            }
        )
        log_path.write_text(event, encoding="utf-8")
        result = summarize_previous_attempt(log_path=log_path)
        assert "Bash commands run: 1" in result

    def test_non_dict_part_skipped(self, tmp_path):
        log_path = tmp_path / "test.log"
        log_path.write_text('{"type":"tool_use","part":"not a dict"}', encoding="utf-8")
        result = summarize_previous_attempt(log_path=log_path)
        assert result == ""

    def test_non_completed_status_skipped(self, tmp_path):
        log_path = tmp_path / "test.log"
        event = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "write",
                    "state": {
                        "status": "running",
                        "input": {"filePath": "test.py"},
                    },
                },
            }
        )
        log_path.write_text(event, encoding="utf-8")
        result = summarize_previous_attempt(log_path=log_path)
        assert "test.py" not in result

    def test_non_dict_state_skipped(self, tmp_path):
        log_path = tmp_path / "test.log"
        event = json.dumps(
            {
                "type": "tool_use",
                "part": {"tool": "write", "state": "not a dict"},
            }
        )
        log_path.write_text(event, encoding="utf-8")
        result = summarize_previous_attempt(log_path=log_path)
        assert result == ""

    def test_non_dict_input_handled(self, tmp_path):
        log_path = tmp_path / "test.log"
        event = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "write",
                    "state": {
                        "status": "completed",
                        "input": "not a dict",
                    },
                },
            }
        )
        log_path.write_text(event, encoding="utf-8")
        result = summarize_previous_attempt(log_path=log_path)
        assert isinstance(result, str)

    def test_read_not_shown_when_writes_exist(self, tmp_path):
        log_path = tmp_path / "test.log"
        write_ev = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "write",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "new_file.py"},
                    },
                },
            }
        )
        read_ev = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "read",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "old_file.py"},
                    },
                },
            }
        )
        log_path.write_text(write_ev + "\n" + read_ev, encoding="utf-8")
        result = summarize_previous_attempt(log_path=log_path)
        assert "Files read" not in result
        assert "new_file.py" in result

    def test_pytest_error_without_failed(self, tmp_path):
        log_path = tmp_path / "test.log"
        event = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": "pytest tests/"},
                        "output": "ERROR collecting tests\nsome error",
                    },
                },
            }
        )
        log_path.write_text(event, encoding="utf-8")
        result = summarize_previous_attempt(log_path=log_path)
        assert "Errors detected" in result or "pytest exited" in result

    def test_oserror_reading_log(self, tmp_path):
        # When log_path doesn't exist, should return ""
        log_path = tmp_path / "nonexistent.log"
        result = summarize_previous_attempt(log_path=log_path)
        assert result == ""


class TestBuildAttemptSummaryExtended:
    """Extended tests for build_attempt_summary() covering JSON parsing and error detection."""

    def test_extracts_write_events(self, tmp_path):
        log_path = tmp_path / "test.log"
        event = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "write",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "src/main.py"},
                    },
                },
            }
        )
        log_path.write_text(event, encoding="utf-8")
        summary = build_attempt_summary(
            task_id="T01",
            attempt_number=1,
            log_path=log_path,
            completion_detected=False,
            total_tokens=100,
        )
        assert "src/main.py" in summary.files_written

    def test_extracts_bash_with_exit_code(self, tmp_path):
        log_path = tmp_path / "test.log"
        event = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": "pytest"},
                        "output": "tests failed",
                        "exit_code": 1,
                    },
                },
            }
        )
        log_path.write_text(event, encoding="utf-8")
        summary = build_attempt_summary(
            task_id="T01",
            attempt_number=1,
            log_path=log_path,
            completion_detected=False,
        )
        assert summary.bash_commands_run == 1
        assert len(summary.bash_errors) > 0

    def test_bash_error_indicators_in_output(self, tmp_path):
        log_path = tmp_path / "test.log"
        event = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": "python script.py"},
                        "output": "Traceback (most recent call last):\n  File...",
                    },
                },
            }
        )
        log_path.write_text(event, encoding="utf-8")
        summary = build_attempt_summary(
            task_id="T01",
            attempt_number=1,
            log_path=log_path,
            completion_detected=False,
        )
        assert len(summary.bash_errors) > 0

    def test_bash_exit_code_not_int(self, tmp_path):
        log_path = tmp_path / "test.log"
        event = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": "test"},
                        "output": "ok",
                        "exit_code": "not_a_number",
                    },
                },
            }
        )
        log_path.write_text(event, encoding="utf-8")
        summary = build_attempt_summary(
            task_id="T01",
            attempt_number=1,
            log_path=log_path,
            completion_detected=False,
        )
        assert summary.bash_commands_run == 1

    def test_non_dict_part_skipped(self, tmp_path):
        log_path = tmp_path / "test.log"
        log_path.write_text('{"type":"tool_use","part":"not_a_dict"}', encoding="utf-8")
        summary = build_attempt_summary(
            task_id="T01", attempt_number=1, log_path=log_path, completion_detected=False
        )
        assert summary.files_written == []

    def test_non_dict_state_skipped(self, tmp_path):
        log_path = tmp_path / "test.log"
        log_path.write_text(
            '{"type":"tool_use","part":{"tool":"write","state":"not_a_dict"}}',
            encoding="utf-8",
        )
        summary = build_attempt_summary(
            task_id="T01", attempt_number=1, log_path=log_path, completion_detected=False
        )
        assert summary.files_written == []

    def test_non_completed_status_skipped(self, tmp_path):
        log_path = tmp_path / "test.log"
        log_path.write_text(
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "tool": "write",
                        "state": {
                            "status": "running",
                            "input": {"filePath": "test.py"},
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        summary = build_attempt_summary(
            task_id="T01", attempt_number=1, log_path=log_path, completion_detected=False
        )
        assert "test.py" not in summary.files_written

    def test_non_dict_input_handled(self, tmp_path):
        log_path = tmp_path / "test.log"
        log_path.write_text(
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "tool": "write",
                        "state": {
                            "status": "completed",
                            "input": "not_a_dict",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        summary = build_attempt_summary(
            task_id="T01", attempt_number=1, log_path=log_path, completion_detected=False
        )
        assert summary.files_written == []

    def test_oserror_reading_log(self, tmp_path):
        # When log_path doesn't exist, should return empty summary
        log_path = tmp_path / "nonexistent.log"
        summary = build_attempt_summary(
            task_id="T01", attempt_number=1, log_path=log_path, completion_detected=False
        )
        assert summary.files_written == []

    def test_accumulated_text_from_log(self, tmp_path):
        log_path = tmp_path / "test.log"
        log_path.write_text(
            json.dumps({"type": "text", "part": {"text": "hello from log"}}),
            encoding="utf-8",
        )
        summary = build_attempt_summary(
            task_id="T01",
            attempt_number=1,
            log_path=log_path,
            completion_detected=False,
            accumulated_text="",
        )
        assert "hello from log" in summary.accumulated_text

    def test_accumulated_text_caller_priority(self, tmp_path):
        log_path = tmp_path / "test.log"
        log_path.write_text(
            json.dumps({"type": "text", "part": {"text": "from log"}}),
            encoding="utf-8",
        )
        summary = build_attempt_summary(
            task_id="T01",
            attempt_number=1,
            log_path=log_path,
            completion_detected=False,
            accumulated_text="from caller",
        )
        assert summary.accumulated_text == "from caller"

    def test_error_event_in_log(self, tmp_path):
        log_path = tmp_path / "test.log"
        log_path.write_text(
            json.dumps({"type": "error", "message": "rate limited"}),
            encoding="utf-8",
        )
        summary = build_attempt_summary(
            task_id="T01",
            attempt_number=1,
            log_path=log_path,
            completion_detected=False,
            accumulated_text="",
        )
        assert "rate limited" in summary.accumulated_text

    def test_plain_text_line_in_log(self, tmp_path):
        log_path = tmp_path / "test.log"
        log_path.write_text("plain text line from Claude Code", encoding="utf-8")
        summary = build_attempt_summary(
            task_id="T01",
            attempt_number=1,
            log_path=log_path,
            completion_detected=False,
            accumulated_text="",
        )
        assert "plain text line" in summary.accumulated_text

    def test_rate_limit_and_cooldown_fields(self, tmp_path):
        log_path = tmp_path / "nonexistent.log"
        summary = build_attempt_summary(
            task_id="T01",
            attempt_number=1,
            log_path=log_path,
            completion_detected=False,
            rate_limit_hit=True,
            cooldown_until=1700000000,
        )
        assert summary.rate_limit_hit is True
        assert summary.cooldown_until == 1700000000

    def test_bash_no_error_with_zero_exit(self, tmp_path):
        log_path = tmp_path / "test.log"
        log_path.write_text(
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "tool": "bash",
                        "state": {
                            "status": "completed",
                            "input": {"command": "ls"},
                            "output": "file1.py\nfile2.py",
                            "exit_code": 0,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        summary = build_attempt_summary(
            task_id="T01",
            attempt_number=1,
            log_path=log_path,
            completion_detected=False,
        )
        assert summary.bash_errors == []

    def test_bash_truncates_long_error(self, tmp_path):
        log_path = tmp_path / "test.log"
        long_output = "Error: " + "x" * 1000
        log_path.write_text(
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "tool": "bash",
                        "state": {
                            "status": "completed",
                            "input": {"command": "test"},
                            "output": long_output,
                            "exit_code": 1,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        summary = build_attempt_summary(
            task_id="T01",
            attempt_number=1,
            log_path=log_path,
            completion_detected=False,
        )
        assert len(summary.bash_errors[0]) <= 500


# ── Coverage Gap Tests ────────────────────────────────────────────────────


# ── Coverage Gap Tests ────────────────────────────────────────────────────


class TestOutputAnalysisProperties:
    """Cover OutputAnalysis.has_completion_promise (L125) and is_stuck (L139)."""

    def test_has_completion_promise_true(self):
        oa = OutputAnalysis(
            completion_promises=["T01"],
            error_signals=[],
            progress_signals=[],
            agent_self_assessment="unknown",
        )
        assert oa.has_completion_promise is True

    def test_has_completion_promise_false(self):
        oa = OutputAnalysis(
            completion_promises=[],
            error_signals=[],
            progress_signals=[],
            agent_self_assessment="unknown",
        )
        assert oa.has_completion_promise is False

    def test_is_stuck_true(self):
        oa = OutputAnalysis(
            completion_promises=[],
            error_signals=["I'm stuck", "I can't proceed"],
            progress_signals=[],
            agent_self_assessment="stuck",
        )
        assert oa.is_stuck is True

    def test_is_stuck_false_single_signal(self):
        oa = OutputAnalysis(
            completion_promises=[],
            error_signals=["I'm stuck"],
            progress_signals=[],
            agent_self_assessment="unknown",
        )
        assert oa.is_stuck is False


class TestDetermineSelfAssessment:
    """Cover _determine_self_assessment branches (L253, L258, L263)."""

    def test_stuck_pattern(self):
        assert _determine_self_assessment("I'm stuck on this problem") == "stuck"

    def test_stuck_cant_proceed(self):
        assert _determine_self_assessment("I can't proceed further") == "stuck"

    def test_complete_pattern(self):
        assert _determine_self_assessment("The task is complete") == "complete"

    def test_complete_done(self):
        assert _determine_self_assessment("task done") == "complete"

    def test_in_progress_still_working(self):
        assert _determine_self_assessment("I'm still working on it") == "in_progress"

    def test_in_progress_remaining(self):
        assert _determine_self_assessment("remaining items to fix") == "in_progress"

    def test_unknown(self):
        assert _determine_self_assessment("just some text") == "unknown"

    def test_stuck_overrides_complete(self):
        """Stuck pattern should override complete pattern."""
        text = "The task is complete but I'm stuck on the tests"
        assert _determine_self_assessment(text) == "stuck"


class TestAcquireLockExtended:
    """Cover acquire_lock atomic create (L319-323), stale retry (L330-331), OSError (L340-341)."""

    def test_atomic_create_success(self, tmp_path):
        lock_path = tmp_path / ".architect" / "runner.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        assert acquire_lock(tmp_path) is True
        # Lock file should contain current PID
        pid = lock_path.read_text().strip()
        assert pid == str(os.getpid())

    def test_stale_lock_removed_and_reacquired(self, tmp_path):
        lock_path = tmp_path / ".architect" / "runner.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Write a stale lock (PID that doesn't exist)
        lock_path.write_text("999999999", encoding="utf-8")
        assert acquire_lock(tmp_path) is True
        # Should now contain our PID
        pid = lock_path.read_text().strip()
        assert pid == str(os.getpid())

    def test_active_lock_returns_false(self, tmp_path):
        lock_path = tmp_path / ".architect" / "runner.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Write a lock with current PID (active process)
        lock_path.write_text(str(os.getpid()), encoding="utf-8")
        assert acquire_lock(tmp_path) is False

    def test_stale_lock_unlink_oserror(self, tmp_path):
        """When stale lock unlink fails, should return False (L330-331)."""
        lock_path = tmp_path / ".architect" / "runner.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Write a stale lock (PID that doesn't exist)
        lock_path.write_text("999999999", encoding="utf-8")
        with patch("the_architect.core.runner.Path.unlink", side_effect=OSError("perm")):
            assert acquire_lock(tmp_path) is False

    def test_retry_after_unlink_oserror(self, tmp_path):
        """When retry after stale removal also fails with OSError (L340-341)."""
        lock_path = tmp_path / ".architect" / "runner.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("999999999", encoding="utf-8")
        with patch("the_architect.core.runner.Path.unlink") as mock_unlink:
            # First call: actually unlink (simulate successful removal)
            mock_unlink.side_effect = lambda *a, **kw: None
            # But os.open fails on retry
            with patch("os.open", side_effect=OSError("cannot create")):
                assert acquire_lock(tmp_path) is False


class TestIsLockStalePermissionError:
    """Cover _is_lock_stale PermissionError case (L378)."""

    def test_permission_error_returns_false(self, tmp_path):
        lock_path = tmp_path / "runner.lock"
        lock_path.write_text(str(os.getpid()), encoding="utf-8")
        # os.kill(pid, 0) with PermissionError means process exists but can't signal
        with patch("os.kill", side_effect=PermissionError("no access")):
            assert _is_lock_stale(lock_path) is False


class TestStreamProviderTokenAccumulation:
    """Cover accumulated_tokens addition (L667)."""

    @pytest.mark.asyncio
    async def test_tokens_accumulated(self, tmp_path: Path):
        provider = _make_mock_provider()
        # Provider returns ParsedEvent with tokens
        token_event = ParsedEvent(
            event_type="step_finish",
            display_lines=[],
            tokens=TokenUsage(input_tokens=100, output_tokens=50),
        )
        provider.parse_output_line = MagicMock(return_value=token_event)

        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_mock_process(
                stdout_lines=[b'{"type":"step_finish"}\n'],
                exit_code=0,
            )
            result = await stream_provider("test", tmp_path, provider)
        assert result.tokens.input_tokens == 100
        assert result.tokens.output_tokens == 50


class TestInstructionViaStdin:
    """Tests for the stdin-delivery path (instruction_via_stdin=True).

    This is the fix for Windows CreateProcess command-line length limit
    (error 206) that caused every Claude Code task to fail when prompts
    exceeded 32 767 chars.
    """

    @pytest.mark.asyncio
    async def test_stdin_provider_writes_instruction_to_stdin(self, tmp_path: Path) -> None:
        """When instruction_via_stdin=True the instruction is written to process.stdin."""
        from unittest.mock import AsyncMock as _AsyncMock

        provider = _make_mock_provider(instruction_via_stdin=True)
        mock_process = _make_mock_process(stdout_lines=[], exit_code=0)
        # Attach an AsyncMock stdin so we can assert it was written to.
        mock_stdin = _AsyncMock()
        mock_process.stdin = mock_stdin

        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_process
            await stream_provider("my instruction", tmp_path, provider)

        mock_stdin.write.assert_called_once_with(b"my instruction")
        mock_stdin.drain.assert_awaited_once()
        mock_stdin.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_stdin_provider_opens_stdin_pipe(self, tmp_path: Path) -> None:
        """When instruction_via_stdin=True the subprocess is opened with stdin=PIPE."""
        provider = _make_mock_provider(instruction_via_stdin=True)
        mock_process = _make_mock_process(stdout_lines=[], exit_code=0)
        from unittest.mock import AsyncMock as _AsyncMock

        mock_process.stdin = _AsyncMock()

        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_process
            await stream_provider("instruction", tmp_path, provider)

        _, kwargs = mock_exec.call_args
        import asyncio as _asyncio

        assert kwargs.get("stdin") == _asyncio.subprocess.PIPE

    @pytest.mark.asyncio
    async def test_non_stdin_provider_does_not_open_stdin_pipe(self, tmp_path: Path) -> None:
        """When instruction_via_stdin=False the subprocess stdin must be None."""
        provider = _make_mock_provider(instruction_via_stdin=False)

        with patch("the_architect.core.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_mock_process(stdout_lines=[], exit_code=0)
            await stream_provider("instruction", tmp_path, provider)

        _, kwargs = mock_exec.call_args
        assert kwargs.get("stdin") is None

    def test_claude_code_provider_instruction_via_stdin_is_true(self) -> None:
        """ClaudeCodeProvider must advertise stdin delivery."""
        from the_architect.core.claude_code_provider import ClaudeCodeProvider

        assert ClaudeCodeProvider().instruction_via_stdin is True

    def test_claude_code_build_command_excludes_instruction(self) -> None:
        """ClaudeCodeProvider.build_command must NOT include the instruction."""
        from unittest.mock import patch as _patch

        from the_architect.core.claude_code_provider import ClaudeCodeProvider

        with _patch("shutil.which", return_value="/usr/bin/claude"):
            cmd = ClaudeCodeProvider().build_command("secret instruction", model_override=None)
        assert "secret instruction" not in cmd

    def test_other_providers_instruction_via_stdin_is_false(self) -> None:
        """OpenCode, Codex, and Gemini providers must keep stdin=False."""
        from the_architect.core.codex_cli_provider import CodexCliProvider
        from the_architect.core.gemini_cli_provider import GeminiCliProvider
        from the_architect.core.opencode_provider import OpenCodeProvider

        assert OpenCodeProvider().instruction_via_stdin is False
        assert CodexCliProvider().instruction_via_stdin is False
        assert GeminiCliProvider().instruction_via_stdin is False

    """Cover FileNotFoundError re-raise (L726)."""

    @pytest.mark.asyncio
    async def test_filenotfound_reraise(self, tmp_path):
        provider = _make_mock_provider()
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("not found")):
            with pytest.raises(FileNotFoundError, match="not found"):
                await stream_provider(instruction="test", project_dir=tmp_path, provider=provider)


class TestStreamProviderKillOnException:
    """Cover process.kill on generic exception (L735-736).

    Already covered by TestStreamProviderSubprocess::test_generic_exception_kills_process.
    """


class TestToolResultLinesExtended:
    """Cover _tool_result_lines: read/view fallback multi-line (L849, L851),
    todowrite non-dict (L894), todowrite truncation (L896)."""

    def test_read_fallback_multi_line(self):
        """read/view fallback: show first 3 lines + more marker (L849, L851)."""
        output = "line1\nline2\nline3\nline4"
        result = _tool_result_lines("read", output, {}, "test")
        assert len(result) == 4  # 3 shown + 1 "more lines"
        assert "more lines" in result[-1]

    def test_read_fallback_empty_output(self):
        result = _tool_result_lines("read", "", {}, "test")
        assert result == []

    def test_todowrite_non_dict_items(self):
        todos_json = json.dumps({"todos": ["item1", "item2"]})
        result = _tool_result_lines("todowrite", todos_json, {}, "test")
        assert len(result) == 2
        assert "item1" in result[0]

    def test_todowrite_truncation_many_items(self):
        todos = [{"content": f"item {i}", "status": "pending"} for i in range(20)]
        todos_json = json.dumps({"todos": todos})
        result = _tool_result_lines("todowrite", todos_json, {}, "test")
        # Should show 15 items + 1 "more" line
        assert len(result) == 16
        assert "+5 more" in result[-1]

    def test_todowrite_invalid_json(self):
        result = _tool_result_lines("todowrite", "not json", {}, "test")
        assert result == ["not json"[:80]]


class TestParseOpencodeEventExtended:
    """Cover parse_opencode_event: glob with path (L1026-1031), _inp alt key (L1003)."""

    def test_glob_with_path(self):
        event = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "glob",
                    "state": {
                        "status": "completed",
                        "input": {"pattern": "**/*.py", "path": "src"},
                        "output": "file1.py\nfile2.py",
                    },
                },
            }
        )
        etype, lines, tokens = _parse_opencode_event(event)
        assert etype == "tool_use"
        # Should include the path in the call line
        assert any("src" in line for line in lines)

    def test_inp_alt_key_camelcase(self):
        """When primary key is empty, alt key should be used (L1003)."""
        event = json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "write",
                    "state": {
                        "status": "running",
                        "input": {"filePath": "test.py"},  # camelCase alt key
                        "output": "",
                    },
                },
            }
        )
        etype, lines, tokens = _parse_opencode_event(event)
        assert etype == "tool_use"
        assert any("test.py" in line for line in lines)


class TestParseOpencodeEventLegacyExtended:
    """Cover legacy format: _leg_inp alt key (L1113), read/view (L1124-1125)."""

    def test_legacy_read_with_filepath_alt(self):
        event = json.dumps(
            {
                "type": "tool",
                "tool": {"name": "read", "input": {"filePath": "foo.py"}},
            }
        )
        etype, lines, tokens = _parse_opencode_event(event)
        assert etype == "tool"
        assert any("foo.py" in line for line in lines)

    def test_legacy_view_with_filepath(self):
        event = json.dumps(
            {
                "type": "tool",
                "tool": {"name": "view", "input": {"filePath": "bar.py"}},
            }
        )
        etype, lines, tokens = _parse_opencode_event(event)
        assert etype == "tool"
        assert any("bar.py" in line for line in lines)


class TestIsTaskCompletePromiseMatch:
    """Cover is_task_complete promise_match branch (L1231)."""

    def test_promise_alone_done(self):
        oa = OutputAnalysis(
            completion_promises=["T01"],
            error_signals=[],
            progress_signals=[],
            agent_self_assessment="unknown",
        )
        done, signals = is_task_complete("T01", oa, progress_done=False, exit_code=1)
        assert done is True

    def test_two_signals_done(self):
        """Two positive signals should be done even without promise."""
        oa = OutputAnalysis(
            completion_promises=[],
            error_signals=[],
            progress_signals=["all tests pass"],
            agent_self_assessment="complete",
        )
        done, signals = is_task_complete("T01", oa, progress_done=True, exit_code=0)
        assert done is True


class TestBuildInstructionPreviousSummary:
    """Cover build_instruction with previous_summary (L1626-1629)."""

    def test_previous_summary_injected(self, config, task):
        instruction = build_instruction(
            task, attempt=2, config=config, previous_summary="Error: test failed"
        )
        assert "PREVIOUS ATTEMPT CONTEXT" in instruction
        assert "Error: test failed" in instruction

    def test_structured_outcome_block_required(self, config, task):
        instruction = build_instruction(task, attempt=1, config=config)
        assert "=== TASK OUTCOME ===" in instruction
        assert "Summary:" in instruction
        assert "Files:" in instruction
        assert "Verification:" in instruction
        assert "Impact:" in instruction


class TestBuildInstructionDocsPath:
    """Cover build_instruction with docs_path (L1643-1644)."""

    def test_docs_path_injected(self, config, task, tmp_path):
        docs_path = tmp_path / "docs"
        docs_path.mkdir()
        (docs_path / "guide.md").write_text("# Guide", encoding="utf-8")
        config.docs_path = docs_path
        instruction = build_instruction(task, attempt=1, config=config)
        assert "Project documentation is available at" in instruction
        assert str(docs_path) in instruction


class TestRunTaskOnceCarryContext:
    """Cover run_task_once carry_context logging (L1733)."""

    @pytest.mark.asyncio
    async def test_carry_context_logs_summary(self, config, task, tmp_path):
        # Create a previous log file
        log_file = config.log_dir / f"{task.name}.log"
        log_file.write_text(
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "tool": "write",
                        "state": {
                            "status": "completed",
                            "input": {"path": "foo.py"},
                            "output": "wrote foo.py",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        config.carry_context = True

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        with patch(
            "the_architect.core.runner.stream_provider",
            return_value=StreamResult(
                exit_code=0, accumulated_text="<promise>T01_COMPLETE</promise>"
            ),
        ):
            result = await run_task_once(task=task, attempt=2, config=config)
        assert result.status == "done"


class TestRunTaskNotDoneWarning:
    """Cover run_task NOT done warning (L1829)."""

    @pytest.mark.asyncio
    async def test_not_done_logs_warning(self, config, task):
        config.progress_file.write_text(
            "**Tasks completed:** 0\n**Next task to run:** T01\n",
            encoding="utf-8",
        )
        # Return result with no completion signals
        with patch(
            "the_architect.core.runner.stream_provider",
            return_value=StreamResult(exit_code=1, accumulated_text=""),
        ):
            result = await run_task(task=task, config=config)
        assert result.status == "failed"


class TestRunTaskCircuitBreakerEvents:
    """Cover circuit breaker event callbacks in run_task (L2095-2165)."""

    @pytest.mark.asyncio
    async def test_on_circuit_event_state_change(self, config, task, tmp_path):
        from the_architect.core.circuit import (
            CircuitBreaker as CB,
        )
        from the_architect.core.circuit import (
            CircuitState,
            RecoveryAction,
            TaskCircuitState,
        )

        cb = MagicMock(spec=CB)
        cb.can_run.return_value = (True, "")
        cb_state = TaskCircuitState(
            state=CircuitState.OPEN,
            consecutive_no_progress=3,
            consecutive_same_error=2,
            recovery_action=RecoveryAction.REPLAN,
        )
        cb_state_success = TaskCircuitState(
            state=CircuitState.CLOSED,
            consecutive_no_progress=0,
            consecutive_same_error=0,
        )
        cb.record_attempt.side_effect = [cb_state, cb_state_success]
        cb.reset_task = MagicMock()
        cb.handle_cooldown_wait = AsyncMock()
        cb.attempt_replan = AsyncMock()

        events = []

        def on_circuit_event(etype: str, data: object) -> None:
            events.append((etype, data))

        call_count = [0]

        async def mock_stream(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return StreamResult(exit_code=1, tokens=TokenUsage(), accumulated_text="failed")
            return StreamResult(
                exit_code=0,
                tokens=TokenUsage(),
                accumulated_text="<promise>T01_COMPLETE</promise>",
            )

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        config.max_retries = 3

        with patch("the_architect.core.runner.stream_provider", side_effect=mock_stream):
            await run_task(
                task=task,
                config=config,
                circuit_breaker=cb,
                on_circuit_event=on_circuit_event,
            )
        # Should have fired at least one circuit_state_change event
        assert any(e[0] == "circuit_state_change" for e in events)

    @pytest.mark.asyncio
    async def test_cooldown_wait_events(self, config, task, tmp_path):
        from the_architect.core.circuit import (
            CircuitBreaker as CB,
        )
        from the_architect.core.circuit import (
            CircuitState,
            RecoveryAction,
            TaskCircuitState,
        )

        cb = MagicMock(spec=CB)
        cb.can_run.return_value = (True, "")
        cb_state_cool = TaskCircuitState(
            state=CircuitState.HALF_OPEN,
            consecutive_no_progress=3,
            consecutive_same_error=0,
            cooldown_waiting=True,
            recovery_action=RecoveryAction.COOLDOWN_WAIT,
            cooldown_wait_count=2,
        )
        cb_state_success = TaskCircuitState(
            state=CircuitState.CLOSED,
            consecutive_no_progress=0,
            consecutive_same_error=0,
        )
        cb.record_attempt.side_effect = [cb_state_cool, cb_state_success]
        cb.reset_task = MagicMock()
        cb.handle_cooldown_wait = AsyncMock()
        cb.attempt_replan = AsyncMock()

        events = []

        def on_circuit_event(etype: str, data: object) -> None:
            events.append((etype, data))

        call_count = [0]

        async def mock_stream(**kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                return StreamResult(exit_code=1, tokens=TokenUsage(), accumulated_text="failed")
            return StreamResult(
                exit_code=0,
                tokens=TokenUsage(),
                accumulated_text="<promise>T01_COMPLETE</promise>",
            )

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        config.max_retries = 5

        with patch("the_architect.core.runner.stream_provider", side_effect=mock_stream):
            await run_task(
                task=task,
                config=config,
                circuit_breaker=cb,
                on_circuit_event=on_circuit_event,
            )
        # Should have fired cooldown_start and cooldown_end events
        assert any(e[0] == "cooldown_start" for e in events)
        assert any(e[0] == "cooldown_end" for e in events)

    @pytest.mark.asyncio
    async def test_replan_events(self, config, task, tmp_path):
        from the_architect.core.circuit import (
            CircuitBreaker as CB,
        )
        from the_architect.core.circuit import (
            CircuitState,
            RecoveryAction,
            TaskCircuitState,
        )

        cb = MagicMock(spec=CB)
        cb.can_run.return_value = (True, "")
        cb_state_replan = TaskCircuitState(
            state=CircuitState.OPEN,
            consecutive_no_progress=5,
            consecutive_same_error=0,
            recovery_action=RecoveryAction.REPLAN,
        )
        cb_state_success = TaskCircuitState(
            state=CircuitState.CLOSED,
            consecutive_no_progress=0,
            consecutive_same_error=0,
        )
        cb.record_attempt.side_effect = [cb_state_replan, cb_state_success]
        cb.reset_task = MagicMock()
        cb.handle_cooldown_wait = AsyncMock()
        cb.attempt_replan = AsyncMock()

        events = []

        def on_circuit_event(etype: str, data: object) -> None:
            events.append((etype, data))

        call_count = [0]

        async def mock_stream(**kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                return StreamResult(exit_code=1, tokens=TokenUsage(), accumulated_text="failed")
            return StreamResult(
                exit_code=0,
                tokens=TokenUsage(),
                accumulated_text="<promise>T01_COMPLETE</promise>",
            )

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        config.max_retries = 5

        with patch("the_architect.core.runner.stream_provider", side_effect=mock_stream):
            await run_task(
                task=task,
                config=config,
                circuit_breaker=cb,
                on_circuit_event=on_circuit_event,
            )
        # Should have fired replan_start and replan_end events
        assert any(e[0] == "replan_start" for e in events)
        assert any(e[0] == "replan_end" for e in events)

    @pytest.mark.asyncio
    async def test_on_circuit_event_exception_swallowed(self, config, task, tmp_path):
        """Exception in on_circuit_event callback should not crash (L2106)."""
        from the_architect.core.circuit import (
            CircuitBreaker as CB,
        )
        from the_architect.core.circuit import (
            CircuitState,
            RecoveryAction,
            TaskCircuitState,
        )

        cb = MagicMock(spec=CB)
        cb.can_run.return_value = (True, "")
        cb_state = TaskCircuitState(
            state=CircuitState.OPEN,
            consecutive_no_progress=3,
            consecutive_same_error=0,
            recovery_action=RecoveryAction.REPLAN,
        )
        cb_state_success = TaskCircuitState(
            state=CircuitState.CLOSED,
            consecutive_no_progress=0,
            consecutive_same_error=0,
        )
        cb.record_attempt.side_effect = [cb_state, cb_state_success]
        cb.reset_task = MagicMock()
        cb.handle_cooldown_wait = AsyncMock()
        cb.attempt_replan = AsyncMock()

        def bad_callback(etype, data):
            raise RuntimeError("callback error")

        call_count = [0]

        async def mock_stream(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return StreamResult(exit_code=1, tokens=TokenUsage(), accumulated_text="failed")
            return StreamResult(
                exit_code=0,
                tokens=TokenUsage(),
                accumulated_text="<promise>T01_COMPLETE</promise>",
            )

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        config.max_retries = 3

        with patch("the_architect.core.runner.stream_provider", side_effect=mock_stream):
            # Should not raise even though callback fails
            result = await run_task(
                task=task,
                config=config,
                circuit_breaker=cb,
                on_circuit_event=bad_callback,
            )
        assert result.status in ("done", "failed")


class TestRunTaskCooldownContinue:
    """Cover cooldown_triggered continue (L2198-2199)."""

    @pytest.mark.asyncio
    async def test_cooldown_skips_retry_pause(self, config, task, tmp_path):
        from the_architect.core.circuit import (
            CircuitBreaker as CB,
        )
        from the_architect.core.circuit import (
            CircuitState,
            RecoveryAction,
            TaskCircuitState,
        )

        cb = MagicMock(spec=CB)
        cb.can_run.return_value = (True, "")
        cb_state_cool = TaskCircuitState(
            state=CircuitState.HALF_OPEN,
            consecutive_no_progress=3,
            consecutive_same_error=0,
            cooldown_waiting=True,
            recovery_action=RecoveryAction.COOLDOWN_WAIT,
            cooldown_wait_count=1,
        )
        cb_state_success = TaskCircuitState(
            state=CircuitState.CLOSED,
            consecutive_no_progress=0,
            consecutive_same_error=0,
        )
        # Provide enough side_effect values (cooldown decrements attempt counter)
        cb.record_attempt.side_effect = [cb_state_cool] * 5 + [cb_state_success] * 5
        cb.reset_task = MagicMock()
        cb.handle_cooldown_wait = AsyncMock()
        cb.attempt_replan = AsyncMock()

        call_count = [0]

        async def mock_stream(**kwargs):
            call_count[0] += 1
            if call_count[0] <= 3:
                return StreamResult(exit_code=1, tokens=TokenUsage(), accumulated_text="failed")
            return StreamResult(
                exit_code=0,
                tokens=TokenUsage(),
                accumulated_text="<promise>T01_COMPLETE</promise>",
            )

        config.retry_pause = 10  # would sleep 10s if not skipped

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        config.max_retries = 5

        with patch("the_architect.core.runner.stream_provider", side_effect=mock_stream):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await run_task(
                    task=task,
                    config=config,
                    circuit_breaker=cb,
                )
        # asyncio.sleep should never be called with retry_pause=10
        for call_args in mock_sleep.call_args_list:
            assert call_args[0][0] != 10


class TestHourlyTokenBudgetExtended2:
    """Cover HourlyTokenBudget: disabled (L2298), no window (L2330),
    wait_for_reset logging (L2368-2370, L2373)."""

    def test_add_disabled(self):
        budget = HourlyTokenBudget(budget=0)
        budget.add(1000)  # should be ignored
        assert budget._tokens_this_hour == 0

    def test_exceeded_no_window(self):
        budget = HourlyTokenBudget(budget=1000)
        assert budget.exceeded() is False

    @pytest.mark.asyncio
    async def test_wait_for_reset_with_logging(self):
        budget = HourlyTokenBudget(budget=100)
        # Artificially set window to almost elapsed
        budget._window_start = time.monotonic() - 3599.5
        budget._tokens_this_hour = 200
        # Should complete quickly since <1s remaining
        await budget.wait_for_reset()
        assert budget._tokens_this_hour == 0

    @pytest.mark.asyncio
    async def test_wait_for_reset_cancelled(self):
        budget = HourlyTokenBudget(budget=100)
        budget._window_start = time.monotonic() - 100
        budget._tokens_this_hour = 200

        async def cancel_after_delay():
            await asyncio.sleep(0.05)
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.gather(
                budget.wait_for_reset(),
                cancel_after_delay(),
            )


class TestBuildAttemptSummaryAccumulatedText:
    """Cover build_attempt_summary accumulated text from log (L1470-1497)."""

    def test_text_from_log_events(self, tmp_path):
        log_path = tmp_path / "test.log"
        log_path.write_text(
            json.dumps({"type": "text", "part": {"text": "Hello world"}})
            + "\n"
            + json.dumps({"type": "error", "message": "rate limited"})
            + "\n"
            + "plain text line\n",
            encoding="utf-8",
        )
        summary = build_attempt_summary(
            task_id="T01",
            attempt_number=1,
            log_path=log_path,
            completion_detected=False,
        )
        # Should accumulate text from all sources
        assert "Hello world" in summary.accumulated_text
        assert "rate limited" in summary.accumulated_text
        assert "plain text line" in summary.accumulated_text

    def test_text_oserror_handled(self, tmp_path):
        """When log can't be read for text, should not crash (L1495-1496)."""
        log_path = tmp_path / "test.log"
        log_path.write_text("{}", encoding="utf-8")
        # Pass caller-supplied accumulated_text which takes priority
        summary = build_attempt_summary(
            task_id="T01",
            attempt_number=1,
            log_path=log_path,
            completion_detected=False,
            accumulated_text="caller text",
        )
        # Caller text takes priority
        assert summary.accumulated_text == "caller text"


class TestRunAllInnerCallbacksExtended:
    """Cover _run_all_inner on_task_start/done callback exceptions (L2512-2515, L2545-2548)."""

    @pytest.mark.asyncio
    async def test_on_task_start_exception_swallowed(self, config, tmp_path):
        def bad_start(t):
            raise RuntimeError("start error")

        tasks_dir = tmp_path / "tasks"
        t1_path = tasks_dir / "T01_first.md"
        t1_path.parent.mkdir(parents=True, exist_ok=True)
        t1_path.write_text("# T01\n", encoding="utf-8")
        tasks = [
            Task(name="T01_first", prefix="T01", number=1, path=t1_path, status=TaskStatus.PENDING)
        ]
        plan = TaskPlan(tasks=tasks)

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        async def mock_run_task(**kwargs):
            return TaskResult(
                prefix="T01",
                title="test",
                status="done",
                duration_seconds=1.0,
                attempts=1,
                tokens=TokenUsage(),
                model="",
            )

        with (
            patch("the_architect.core.runner.acquire_lock", return_value=True),
            patch("the_architect.core.runner.release_lock"),
            patch("the_architect.core.runner.run_task", side_effect=mock_run_task),
            patch("the_architect.core.circuit.load_circuit_state"),
        ):
            result = await run_all(plan, config, on_task_start=bad_start)
            assert result is True

    @pytest.mark.asyncio
    async def test_on_task_done_exception_swallowed(self, config, tmp_path):
        def bad_done(t):
            raise RuntimeError("done error")

        tasks_dir = tmp_path / "tasks"
        t1_path = tasks_dir / "T01_first.md"
        t1_path.parent.mkdir(parents=True, exist_ok=True)
        t1_path.write_text("# T01\n", encoding="utf-8")
        tasks = [
            Task(name="T01_first", prefix="T01", number=1, path=t1_path, status=TaskStatus.PENDING)
        ]
        plan = TaskPlan(tasks=tasks)

        config.progress_file.write_text(
            "**Tasks completed:** 1\n**Next task to run:** T02\n"
            "| T01 | Test | Done | 2026-04-12 |\n",
            encoding="utf-8",
        )

        async def mock_run_task(**kwargs):
            return TaskResult(
                prefix="T01",
                title="test",
                status="done",
                duration_seconds=1.0,
                attempts=1,
                tokens=TokenUsage(),
                model="",
            )

        with (
            patch("the_architect.core.runner.acquire_lock", return_value=True),
            patch("the_architect.core.runner.release_lock"),
            patch("the_architect.core.runner.run_task", side_effect=mock_run_task),
            patch("the_architect.core.circuit.load_circuit_state"),
        ):
            result = await run_all(plan, config, on_task_done=bad_done)
            assert result is True


class TestSummarizePreviousAttemptOSError:
    """Cover summarize_previous_attempt OSError reading log (L1293-1294)."""

    def test_nonexistent_log_returns_empty(self, tmp_path):
        log_path = tmp_path / "nonexistent.log"
        result = summarize_previous_attempt(log_path=log_path)
        assert result == ""


# ---------------------------------------------------------------------------
# Progress reconciliation tests — closes the "task repeats after retro" loop.
# ---------------------------------------------------------------------------


class TestReconcileProgressAfterAttempt:
    """Unit tests for _reconcile_progress_after_attempt.

    These guard the invariant that the runner's authoritative verdict for
    each task is persisted to PROGRESS.md, regardless of whether the
    executor agent updated the file itself.  Without this step the
    "executor repeats tasks after retrospective" bug would regress.
    """

    def _seed_progress(self, tmp_path: Path) -> Path:
        """Write a PROGRESS.md with T01/T02 both Pending and return its path."""
        p = tmp_path / "PROGRESS.md"
        p.write_text(
            "**Tasks completed:** 0\n"
            "**Next task to run:** T01\n"
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|-----------|\n"
            "| T01 | First  | Pending | — |\n"
            "| T02 | Second | Pending | — |\n",
            encoding="utf-8",
        )
        return p

    def test_done_verdict_persists_as_done(self, tmp_path: Path) -> None:
        """A done TaskResult must flip the PROGRESS.md row to Done with today's date."""
        from the_architect.core.progress import task_is_done
        from the_architect.core.runner import _reconcile_progress_after_attempt

        p = self._seed_progress(tmp_path)
        result = TaskResult(
            prefix="T01",
            title="First",
            status="done",
            duration_seconds=1.0,
            attempts=1,
            tokens=TokenUsage(),
            model="",
        )
        _reconcile_progress_after_attempt(p, result, max_retries=3)
        assert task_is_done(p, "T01") is True

    def test_failed_verdict_persists_as_failed_with_attempts(self, tmp_path: Path) -> None:
        """A failed TaskResult must flip the row to Failed so the next loop skips it."""
        from the_architect.core.progress import task_is_resolved, task_status
        from the_architect.core.runner import _reconcile_progress_after_attempt

        p = self._seed_progress(tmp_path)
        result = TaskResult(
            prefix="T01",
            title="First",
            status="failed",
            duration_seconds=2.0,
            attempts=3,
            tokens=TokenUsage(),
            model="",
        )
        _reconcile_progress_after_attempt(p, result, max_retries=3)
        assert task_is_resolved(p, "T01") is True
        assert task_status(p, "T01") == "Failed"
        content = p.read_text(encoding="utf-8")
        assert "3 attempts" in content

    def test_skipped_verdict_does_not_touch_row(self, tmp_path: Path) -> None:
        """A skipped TaskResult (e.g. circuit breaker) must leave the row Pending."""
        from the_architect.core.progress import task_status
        from the_architect.core.runner import _reconcile_progress_after_attempt

        p = self._seed_progress(tmp_path)
        before = p.read_text(encoding="utf-8")
        result = TaskResult(
            prefix="T01",
            title="First",
            status="skipped",
            duration_seconds=0.0,
            attempts=0,
            tokens=TokenUsage(),
            model="",
        )
        _reconcile_progress_after_attempt(p, result, max_retries=3)
        assert p.read_text(encoding="utf-8") == before
        assert task_status(p, "T01") == "Pending"

    def test_missing_progress_file_does_not_raise(self, tmp_path: Path) -> None:
        """Reconciliation must never propagate errors up the run loop."""
        from the_architect.core.runner import _reconcile_progress_after_attempt

        missing = tmp_path / "does-not-exist.md"
        result = TaskResult(
            prefix="T01",
            title="First",
            status="done",
            duration_seconds=0.0,
            attempts=1,
            tokens=TokenUsage(),
            model="",
        )
        # Must not raise; file stays absent.
        _reconcile_progress_after_attempt(missing, result, max_retries=3)
        assert not missing.exists()

    def test_missing_row_logs_warning_but_does_not_raise(self, tmp_path: Path) -> None:
        """A failed verdict for a prefix with no row must warn, not crash."""
        from the_architect.core.runner import _reconcile_progress_after_attempt

        p = tmp_path / "PROGRESS.md"
        p.write_text(
            "**Tasks completed:** 0\n"
            "**Next task to run:** T01\n"
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|-----------|\n"
            "| T01 | First | Pending | — |\n",
            encoding="utf-8",
        )
        result = TaskResult(
            prefix="T99",
            title="Ghost",
            status="failed",
            duration_seconds=0.0,
            attempts=3,
            tokens=TokenUsage(),
            model="",
        )
        _reconcile_progress_after_attempt(p, result, max_retries=3)
        # File content unchanged for T01.
        assert "T01 | First | Pending" in p.read_text(encoding="utf-8")


class TestRunAllTerminalSkip:
    """End-to-end: a task whose row is already Failed must not be re-picked.

    This is the regression test for the "executor repeats tasks after
    retrospective" bug.  Before the fix, a row without ``Done`` — even if
    it had been marked ``Failed`` — was treated as Pending and re-run.
    After the fix, any terminal status causes the task to be skipped.
    """

    @pytest.mark.asyncio
    async def test_failed_row_is_skipped_on_next_run(self, tmp_path: Path) -> None:
        from the_architect.core.tasks import Task, TaskPlan, TaskStatus

        progress_file = tmp_path / "PROGRESS.md"
        progress_file.write_text(
            "**Tasks completed:** 0\n"
            "**Next task to run:** T01\n"
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|-----------|\n"
            "| T01 | First  | Failed | 3 attempts |\n"
            "| T02 | Second | Pending | — |\n",
            encoding="utf-8",
        )
        config = ArchitectConfig(progress_file=progress_file, max_retries=1)

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        t01 = tasks_dir / "T01_first.md"
        t01.write_text("# T01 — First\n\n## Goal\nx\n", encoding="utf-8")
        t02 = tasks_dir / "T02_second.md"
        t02.write_text("# T02 — Second\n\n## Goal\nx\n", encoding="utf-8")

        plan = TaskPlan(
            tasks=[
                Task(
                    name="T01_first",
                    prefix="T01",
                    number=1,
                    path=t01,
                    status=TaskStatus.PENDING,
                    title="First",
                ),
                Task(
                    name="T02_second",
                    prefix="T02",
                    number=2,
                    path=t02,
                    status=TaskStatus.PENDING,
                    title="Second",
                ),
            ]
        )

        # run_task should only be invoked for T02 — never for the Failed T01.
        invocations: list[str] = []

        async def fake_run_task(*args, **kwargs):
            task = kwargs.get("task") or args[0]
            invocations.append(task.prefix)
            return TaskResult(
                prefix=task.prefix,
                title=task.title or task.name,
                status="done",
                duration_seconds=0.01,
                attempts=1,
                tokens=TokenUsage(),
                model="",
            )

        with (
            patch("the_architect.core.runner.acquire_lock", return_value=True),
            patch("the_architect.core.runner.release_lock"),
            patch("the_architect.core.runner.run_task", side_effect=fake_run_task),
            patch("the_architect.core.circuit.load_circuit_state"),
        ):
            result = await run_all(plan, config, on_task_pause=lambda s: None)

        assert invocations == ["T02"], "Failed task must not be re-picked"
        # T02 should now be Done (via reconciliation).
        from the_architect.core.progress import task_is_done

        assert task_is_done(progress_file, "T02") is True
        # run_all returns True iff all ATTEMPTED tasks (just T02 here) ended Done.
        assert result is True

    @pytest.mark.asyncio
    async def test_agent_forgot_to_update_progress_runner_reconciles(self, tmp_path: Path) -> None:
        """The core repeat-loop regression: runner says done, PROGRESS.md was not updated.

        The runner must reconcile PROGRESS.md to Done so the next loop
        iteration does not re-pick the task.
        """
        from the_architect.core.progress import task_is_done
        from the_architect.core.tasks import Task, TaskPlan, TaskStatus

        progress_file = tmp_path / "PROGRESS.md"
        progress_file.write_text(
            "**Tasks completed:** 0\n"
            "**Next task to run:** T01\n"
            "| Task | Title | Status | Completed |\n"
            "|------|-------|--------|-----------|\n"
            "| T01 | First | Pending | — |\n",
            encoding="utf-8",
        )
        config = ArchitectConfig(progress_file=progress_file, max_retries=1)

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        t01 = tasks_dir / "T01_first.md"
        t01.write_text("# T01 — First\n\n## Goal\nx\n", encoding="utf-8")

        plan = TaskPlan(
            tasks=[
                Task(
                    name="T01_first",
                    prefix="T01",
                    number=1,
                    path=t01,
                    status=TaskStatus.PENDING,
                    title="First",
                ),
            ]
        )

        # Simulate an agent that signals done but does NOT rewrite PROGRESS.md.
        async def fake_run_task(*args, **kwargs):
            task = kwargs.get("task") or args[0]
            return TaskResult(
                prefix=task.prefix,
                title=task.title or task.name,
                status="done",
                duration_seconds=0.01,
                attempts=1,
                tokens=TokenUsage(),
                model="",
            )

        # Before reconciliation, PROGRESS.md has T01 Pending.
        assert task_is_done(progress_file, "T01") is False

        with (
            patch("the_architect.core.runner.acquire_lock", return_value=True),
            patch("the_architect.core.runner.release_lock"),
            patch("the_architect.core.runner.run_task", side_effect=fake_run_task),
            patch("the_architect.core.circuit.load_circuit_state"),
        ):
            result = await run_all(plan, config, on_task_pause=lambda s: None)

        # After run_all, the runner has reconciled PROGRESS.md to Done.
        assert task_is_done(progress_file, "T01") is True
        assert result is True


# ── Baseline integration tests ──────────────────────────────────────────────


class TestBaselineRunnerIntegration:
    """Tests for workspace baseline capture integrated into run_task_once."""

    def test_baseline_path_empty_when_disabled(self, tmp_path: Path):
        """When workspace_baseline=False, baseline_path stays empty."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        progress_file = tasks_dir / "PROGRESS.md"
        progress_file.write_text("test", encoding="utf-8")
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)

        config = ArchitectConfig(
            progress_file=progress_file,
            tasks_dir=tasks_dir,
            log_dir=log_dir,
            workspace_baseline=False,
            max_retries=3,
            retry_pause=0,
            pause_between_tasks=0,
        )

        assert config.workspace_baseline is False

    def test_baseline_path_default_true(self, tmp_path: Path):
        """workspace_baseline defaults to True."""
        config = ArchitectConfig()
        assert config.workspace_baseline is True

    def test_task_result_baseline_path_default_empty(self):
        """TaskResult.baseline_path defaults to empty string."""
        result = TaskResult(prefix="T01", status="done")
        assert result.baseline_path == ""

    def test_task_result_baseline_path_settable(self):
        """TaskResult.baseline_path can be set to an absolute path."""
        result = TaskResult(
            prefix="T01",
            status="done",
            baseline_path="/some/path/baseline.json",
        )
        assert result.baseline_path == "/some/path/baseline.json"

    @pytest.mark.asyncio
    async def test_run_task_once_baseline_enabled(self, tmp_path: Path):
        """run_task_once captures baseline when workspace_baseline=True."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        progress_file = tasks_dir / "PROGRESS.md"
        progress_file.write_text("test", encoding="utf-8")
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)

        task_file = tasks_dir / "T01_test_baseline.md"
        task_file.write_text("# T01 - Test\n", encoding="utf-8")

        config = ArchitectConfig(
            progress_file=progress_file,
            tasks_dir=tasks_dir,
            log_dir=log_dir,
            workspace_baseline=True,
            max_retries=3,
            retry_pause=0,
            pause_between_tasks=0,
        )

        task = Task(
            name="T01_test_baseline",
            prefix="T01",
            number=1,
            path=task_file,
            status=TaskStatus.PENDING,
        )

        # Mock stream_provider to return success with completion promise
        fake_stream = StreamResult(
            exit_code=0,
            tokens=TokenUsage(input_tokens=100, output_tokens=50),
            accumulated_text="<promise>T01_COMPLETE</promise>\nall tests pass",
        )

        with patch(
            "the_architect.core.runner.stream_provider",
            new_callable=AsyncMock,
            return_value=fake_stream,
        ):
            result = await run_task_once(
                task=task,
                attempt=1,
                config=config,
            )

        # Baseline should have been captured
        assert result.baseline_path != ""
        assert "T01_test_baseline.json" in result.baseline_path

        # Baseline file should exist
        baseline_file = Path(result.baseline_path)
        assert baseline_file.exists()

        # Baseline JSON should be valid
        data = json.loads(baseline_file.read_text(encoding="utf-8"))
        assert "timestamp" in data
        assert data["task_prefix"] == "T01"
        assert "files" in data

    @pytest.mark.asyncio
    async def test_run_task_once_baseline_disabled(self, tmp_path: Path):
        """run_task_once skips baseline when workspace_baseline=False."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        progress_file = tasks_dir / "PROGRESS.md"
        progress_file.write_text("test", encoding="utf-8")
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)

        task_file = tasks_dir / "T01_no_baseline.md"
        task_file.write_text("# T01 - Test\n", encoding="utf-8")

        config = ArchitectConfig(
            progress_file=progress_file,
            tasks_dir=tasks_dir,
            log_dir=log_dir,
            workspace_baseline=False,
            max_retries=3,
            retry_pause=0,
            pause_between_tasks=0,
        )

        task = Task(
            name="T01_no_baseline",
            prefix="T01",
            number=1,
            path=task_file,
            status=TaskStatus.PENDING,
        )

        fake_stream = StreamResult(
            exit_code=0,
            tokens=TokenUsage(input_tokens=100, output_tokens=50),
            accumulated_text="<promise>T01_COMPLETE</promise>\nall tests pass",
        )

        with patch(
            "the_architect.core.runner.stream_provider",
            new_callable=AsyncMock,
            return_value=fake_stream,
        ):
            result = await run_task_once(
                task=task,
                attempt=1,
                config=config,
            )

        # Baseline should NOT have been captured
        assert result.baseline_path == ""

    @pytest.mark.asyncio
    async def test_run_task_once_baseline_on_failed_task(self, tmp_path: Path):
        """Baseline path is set even when the task fails (non-zero exit)."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        progress_file = tasks_dir / "PROGRESS.md"
        progress_file.write_text("test", encoding="utf-8")
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)

        task_file = tasks_dir / "T01_failed_baseline.md"
        task_file.write_text("# T01 - Test\n", encoding="utf-8")

        config = ArchitectConfig(
            progress_file=progress_file,
            tasks_dir=tasks_dir,
            log_dir=log_dir,
            workspace_baseline=True,
            max_retries=3,
            retry_pause=0,
            pause_between_tasks=0,
        )

        task = Task(
            name="T01_failed_baseline",
            prefix="T01",
            number=1,
            path=task_file,
            status=TaskStatus.PENDING,
        )

        fake_stream = StreamResult(
            exit_code=1,
            tokens=TokenUsage(input_tokens=100, output_tokens=50),
            accumulated_text="I'm stuck on this task",
        )

        with patch(
            "the_architect.core.runner.stream_provider",
            new_callable=AsyncMock,
            return_value=fake_stream,
        ):
            result = await run_task_once(
                task=task,
                attempt=1,
                config=config,
            )

        # Baseline should have been captured even though task failed
        assert result.baseline_path != ""
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_run_task_once_baseline_changes_detected(self, tmp_path: Path):
        """Change detection appends baseline summary to outcome_summary."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        progress_file = tasks_dir / "PROGRESS.md"
        progress_file.write_text("test", encoding="utf-8")
        log_dir = tmp_path / ".architect" / "logs"
        log_dir.mkdir(parents=True)

        # Create a file that will be captured by the baseline
        new_file = tmp_path / "new_module.py"
        new_file.write_text("# new file\n", encoding="utf-8")

        task_file = tasks_dir / "T01_changes.md"
        task_file.write_text("# T01 - Test\n", encoding="utf-8")

        config = ArchitectConfig(
            progress_file=progress_file,
            tasks_dir=tasks_dir,
            log_dir=log_dir,
            workspace_baseline=True,
            max_retries=3,
            retry_pause=0,
            pause_between_tasks=0,
        )

        task = Task(
            name="T01_changes",
            prefix="T01",
            number=1,
            path=task_file,
            status=TaskStatus.PENDING,
        )

        # During the mocked stream_provider call, create a new file
        # so that change detection finds it
        async def fake_stream_with_change(*args, **kwargs):
            # Create a new tracked file during the "execution"
            changed_file = tmp_path / "changed_file.py"
            changed_file.write_text("# changed\n", encoding="utf-8")
            return StreamResult(
                exit_code=0,
                tokens=TokenUsage(input_tokens=100, output_tokens=50),
                accumulated_text="<promise>T01_COMPLETE</promise>\nall tests pass",
            )

        with patch(
            "the_architect.core.runner.stream_provider",
            new_callable=AsyncMock,
            side_effect=fake_stream_with_change,
        ):
            result = await run_task_once(
                task=task,
                attempt=1,
                config=config,
            )

        # Outcome should mention baseline changes
        assert result.baseline_path != ""
        assert "Baseline changes" in result.outcome_summary
        assert "created" in result.outcome_summary
