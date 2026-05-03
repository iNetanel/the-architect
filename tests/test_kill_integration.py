"""End-to-end probes that prove Ctrl+C and ESC -> Exit really kill the
backend provider subprocess. These go further than the unit tests in
:mod:`test_runner` because they spawn real processes (no mocks) and
confirm the OS has actually reaped them — which is the only honest way
to verify the bug the user reported ("Ctrl+C just exits the UI,
backend keeps going") stays fixed.

If any of these fail, the fix has regressed and users will again be
left with orphan ``opencode`` / ``claude`` processes running after
they quit The Architect.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import threading
import time

import pytest

import the_architect.core.runner as runner_mod


def _pid_alive(pid: int) -> bool:
    """True iff ``pid`` still exists and has not been reaped."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Process-group kill uses killpg, which is POSIX-only",
)


class TestKillActiveSubprocessesReallyKills:
    """The shutdown helpers must actually reach the OS level, not
    just clear internal state. Uses a live ``sleep`` child.
    """

    @pytest.mark.asyncio
    async def test_kill_active_subprocesses_reaps_real_child(self) -> None:
        """kill_active_subprocesses() -> child gone within 1s."""
        kwargs = {"start_new_session": True} if os.name == "posix" else {}
        proc = await asyncio.create_subprocess_exec(
            "sleep",
            "30",
            stdin=None,
            stdout=asyncio.subprocess.PIPE,
            **kwargs,  # type: ignore[arg-type]
        )
        runner_mod._register_process(proc)
        pid = proc.pid
        try:
            assert _pid_alive(pid)

            n = runner_mod.kill_active_subprocesses()
            assert n >= 1

            # Give the kernel a beat to deliver SIGKILL and reap.
            for _ in range(20):
                await asyncio.sleep(0.05)
                if not _pid_alive(pid):
                    break
            else:
                pytest.fail("sleep subprocess survived kill_active_subprocesses")
        finally:
            # Reap so pytest doesn't see a zombie.
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                pass
            runner_mod._unregister_process(proc)

    @pytest.mark.asyncio
    async def test_kill_process_tree_kills_grandchildren_too(self) -> None:
        """The provider is usually node/npm calling opencode calling
        helpers. Killing only the direct child leaks the grandchild —
        ``_kill_process_tree`` must SIGKILL the entire session.
        """
        # ``sh -c "sleep 30 & echo $! >&2; wait"`` — forks a grandchild
        # whose PID it reports on stderr, then waits.
        kwargs = {"start_new_session": True} if os.name == "posix" else {}
        proc = await asyncio.create_subprocess_exec(
            "sh",
            "-c",
            "sleep 30 & echo $! >&2; wait",
            stderr=asyncio.subprocess.PIPE,
            **kwargs,  # type: ignore[arg-type]
        )
        assert proc.stderr is not None
        line = await proc.stderr.readline()
        grandchild_pid = int(line.strip())
        direct_pid = proc.pid
        try:
            assert _pid_alive(grandchild_pid), "grandchild should be alive"
            assert _pid_alive(direct_pid)

            runner_mod._kill_process_tree(proc)

            for _ in range(20):
                await asyncio.sleep(0.05)
                if not _pid_alive(grandchild_pid) and not _pid_alive(direct_pid):
                    break
            else:
                pytest.fail(
                    f"process tree not fully killed "
                    f"(direct_alive={_pid_alive(direct_pid)}, "
                    f"grand_alive={_pid_alive(grandchild_pid)})"
                )
        finally:
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                pass


class TestSigintHandlerKillsBeforeRaising:
    """The SIGINT handler installed by :class:`ArchitectAppRunner` must
    yank any registered subprocess before re-raising KeyboardInterrupt.
    Without that, Ctrl+C during Textual startup (before the event
    loop fully takes over) could drop a KeyboardInterrupt without
    cleaning up the child.
    """

    @pytest.mark.asyncio
    async def test_handler_kills_then_raises_keyboard_interrupt(self) -> None:
        from the_architect.tui.runner import _sigint_kill_handler

        kwargs = {"start_new_session": True} if os.name == "posix" else {}
        proc = await asyncio.create_subprocess_exec(
            "sleep",
            "30",
            stdin=None,
            stdout=asyncio.subprocess.PIPE,
            **kwargs,  # type: ignore[arg-type]
        )
        runner_mod._register_process(proc)
        pid = proc.pid
        try:
            assert _pid_alive(pid)

            with pytest.raises(KeyboardInterrupt):
                _sigint_kill_handler(signal.SIGINT, None)

            for _ in range(20):
                await asyncio.sleep(0.05)
                if not _pid_alive(pid):
                    break
            else:
                pytest.fail("subprocess survived SIGINT handler")
        finally:
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                pass
            runner_mod._unregister_process(proc)


class TestArchitectAppRunnerSigintKillsChild:
    """Full integration: spawn ``sleep`` from inside an
    ArchitectAppRunner flow, send SIGINT to ourselves, and verify the
    child process is dead after the runner returns. This is the path
    the user actually experiences when they hit Ctrl+C.
    """

    def test_sigint_to_self_kills_spawned_subprocess(self) -> None:
        from the_architect.tui.runner import ArchitectAppRunner

        spawned: dict[str, int] = {}
        flow_ready = threading.Event()

        def flow() -> None:
            async def run() -> None:
                kwargs = {"start_new_session": True} if os.name == "posix" else {}
                proc = await asyncio.create_subprocess_exec(
                    "sleep",
                    "30",
                    stdin=None,
                    stdout=asyncio.subprocess.PIPE,
                    **kwargs,  # type: ignore[arg-type]
                )
                runner_mod._register_process(proc)
                spawned["pid"] = proc.pid
                flow_ready.set()
                try:
                    await proc.wait()
                finally:
                    runner_mod._unregister_process(proc)

            asyncio.run(run())

        def delayed_sigint() -> None:
            # Wait until the flow has actually spawned the child, then
            # send SIGINT to ourselves to mimic Ctrl+C.
            if not flow_ready.wait(timeout=10.0):
                return
            time.sleep(0.5)
            os.kill(os.getpid(), signal.SIGINT)

        t = threading.Thread(target=delayed_sigint, daemon=True)
        t.start()

        runner = ArchitectAppRunner(flow=flow)
        with pytest.raises(KeyboardInterrupt):
            runner.run()

        # Give the kernel a moment; the finally block may still be
        # mid-reap when runner.run() returns.
        pid = spawned.get("pid")
        assert pid is not None, "flow never spawned the subprocess"
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if not _pid_alive(pid):
                return
            time.sleep(0.05)
        # Still alive — the fix has regressed. Clean up to avoid
        # leaking out of the test run.
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        pytest.fail(
            f"sleep pid={pid} survived ArchitectAppRunner.run() return — "
            "Ctrl+C is not actually killing the backend"
        )
