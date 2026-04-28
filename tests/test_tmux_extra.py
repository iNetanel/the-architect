"""Extra tmux tests — target uncovered functions in ``the_architect/core/tmux.py``.

The baseline suite covers the pure helpers.  This module covers the harder
paths that previously sat at 57% coverage:

    - ``session_exists`` / ``list_architect_sessions`` / ``kill_session``
      — both success and subprocess-failure branches.
    - ``launch_in_tmux`` — successful attach, new-session failure, split-window
      failure, and generic subprocess exceptions.
    - ``prompt_existing_session`` — non-TTY default and timeout branch.
    - ``prompt_install_tmux`` — non-TTY default and timeout branch.
    - ``maybe_launch_tmux`` — decision-tree branches that were unexercised.
    - ``_install_hint`` / ``try_install_tmux`` — happy path and failure path.
    - ``maybe_launch_own_window`` — various early-exit branches.
    - ``_build_window_command`` — Linux and macOS emulator branches that the
      baseline suite did not touch.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from the_architect.core import tmux as tmux_mod

# ---------------------------------------------------------------------------
# session_exists / kill_session / list_architect_sessions
# ---------------------------------------------------------------------------


class TestSessionLifecycle:
    """Subprocess-backed helpers around individual tmux sessions."""

    def test_session_exists_true_when_tmux_returns_zero(self) -> None:
        fake = MagicMock(returncode=0)
        with patch("the_architect.core.tmux.subprocess.run", return_value=fake):
            assert tmux_mod.session_exists("architect-proj") is True

    def test_session_exists_false_on_subprocess_error(self) -> None:
        with patch(
            "the_architect.core.tmux.subprocess.run",
            side_effect=subprocess.SubprocessError("boom"),
        ):
            assert tmux_mod.session_exists("architect-proj") is False

    def test_kill_session_true_on_zero_rc(self) -> None:
        with patch(
            "the_architect.core.tmux.subprocess.run",
            return_value=MagicMock(returncode=0),
        ):
            assert tmux_mod.kill_session("architect-proj") is True

    def test_kill_session_false_on_exception(self) -> None:
        with patch(
            "the_architect.core.tmux.subprocess.run",
            side_effect=FileNotFoundError("no tmux"),
        ):
            assert tmux_mod.kill_session("architect-proj") is False

    def test_list_architect_sessions_filters_architect_prefix(self) -> None:
        """Only session names starting with ``architect-`` are returned."""
        fake = MagicMock(
            returncode=0,
            stdout="architect-one\narchitect-two\nsomeone-else\n",
        )
        with patch("the_architect.core.tmux.subprocess.run", return_value=fake):
            result = tmux_mod.list_architect_sessions()
        assert result == ["architect-one", "architect-two"]

    def test_list_architect_sessions_empty_on_nonzero_rc(self) -> None:
        fake = MagicMock(returncode=1, stdout="")
        with patch("the_architect.core.tmux.subprocess.run", return_value=fake):
            assert tmux_mod.list_architect_sessions() == []

    def test_list_architect_sessions_empty_on_exception(self) -> None:
        with patch(
            "the_architect.core.tmux.subprocess.run",
            side_effect=OSError("denied"),
        ):
            assert tmux_mod.list_architect_sessions() == []


# ---------------------------------------------------------------------------
# launch_in_tmux
# ---------------------------------------------------------------------------


class TestLaunchInTmux:
    """Exercise the new-session / split-window / attach orchestration."""

    def test_returns_false_when_no_shell_found(self, tmp_path: Path) -> None:
        with patch("the_architect.core.tmux._get_portable_shell", return_value=None):
            result = tmux_mod.launch_in_tmux("architect-proj", tmp_path, ["architect"])
        assert result is False

    def test_returns_false_when_new_session_fails(self, tmp_path: Path) -> None:
        """If ``tmux new-session`` exits non-zero, we must bail out cleanly."""
        with (
            patch("the_architect.core.tmux._get_portable_shell", return_value="/bin/bash"),
            patch(
                "the_architect.core.tmux.subprocess.run",
                return_value=MagicMock(returncode=1, stderr=b"boom"),
            ),
        ):
            result = tmux_mod.launch_in_tmux("architect-proj", tmp_path, ["architect"])
        assert result is False

    def test_kills_session_when_split_window_fails(self, tmp_path: Path) -> None:
        """Split-window failure must trigger cleanup and return False."""
        calls: list[list[str]] = []

        def _run(cmd: list[str], **_: object) -> MagicMock:
            calls.append(list(cmd))
            # 1st call: new-session (ok). 2nd call: split-window (fail).
            if "new-session" in cmd:
                return MagicMock(returncode=0, stderr=b"")
            if "split-window" in cmd:
                return MagicMock(returncode=1, stderr=b"split failed")
            return MagicMock(returncode=0, stderr=b"")

        killed: list[str] = []

        def _kill(name: str) -> bool:
            killed.append(name)
            return True

        with (
            patch("the_architect.core.tmux._get_portable_shell", return_value="/bin/bash"),
            patch("the_architect.core.tmux._forward_env_vars"),
            patch("the_architect.core.tmux.subprocess.run", side_effect=_run),
            patch("the_architect.core.tmux.kill_session", side_effect=_kill),
        ):
            result = tmux_mod.launch_in_tmux("architect-proj", tmp_path, ["architect"])

        assert result is False
        assert killed == ["architect-proj"]

    def test_generic_subprocess_exception_triggers_cleanup(self, tmp_path: Path) -> None:
        """OSError raised inside the try-block must be caught and cleaned up."""
        killed: list[str] = []

        def _kill(name: str) -> bool:
            killed.append(name)
            return True

        with (
            patch("the_architect.core.tmux._get_portable_shell", return_value="/bin/bash"),
            patch(
                "the_architect.core.tmux.subprocess.run",
                side_effect=OSError("no tty"),
            ),
            patch("the_architect.core.tmux.session_exists", return_value=True),
            patch("the_architect.core.tmux.kill_session", side_effect=_kill),
        ):
            result = tmux_mod.launch_in_tmux("architect-proj", tmp_path, ["architect"])

        assert result is False
        # Cleanup only runs when session_exists returns True
        assert killed == ["architect-proj"]


# ---------------------------------------------------------------------------
# prompt_existing_session
# ---------------------------------------------------------------------------


class TestPromptExistingSession:
    """The TTY is not available inside pytest, so we drive the non-TTY path."""

    def test_non_tty_defaults_to_attach(self) -> None:
        with patch("sys.stdin.isatty", return_value=False):
            assert tmux_mod.prompt_existing_session("architect-proj") == "attach"

    def test_tty_timeout_defaults_to_attach(self) -> None:
        """select() returning an empty ready-list (timeout) defaults to attach."""
        fake_termios = MagicMock()
        fake_termios.tcgetattr.return_value = "orig"
        fake_tty = MagicMock()

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch.dict(
                "sys.modules",
                {"termios": fake_termios, "tty": fake_tty},
            ),
            patch("sys.stdin.fileno", return_value=0),
            patch("select.select", return_value=([], [], [])),
        ):
            assert tmux_mod.prompt_existing_session("architect-proj") == "attach"


# ---------------------------------------------------------------------------
# prompt_install_tmux
# ---------------------------------------------------------------------------


class TestPromptInstallTmux:
    """Same story — drive the non-TTY branch which is the path that runs in CI."""

    def test_non_tty_defaults_to_yes(self) -> None:
        with patch("sys.stdin.isatty", return_value=False):
            assert tmux_mod.prompt_install_tmux() is True

    def test_tty_timeout_defaults_to_yes(self) -> None:
        fake_termios = MagicMock()
        fake_termios.tcgetattr.return_value = "orig"
        fake_tty = MagicMock()

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch.dict(
                "sys.modules",
                {"termios": fake_termios, "tty": fake_tty},
            ),
            patch("sys.stdin.fileno", return_value=0),
            patch("select.select", return_value=([], [], [])),
        ):
            assert tmux_mod.prompt_install_tmux() is True


# ---------------------------------------------------------------------------
# _install_hint / try_install_tmux
# ---------------------------------------------------------------------------


class TestInstallHint:
    """Cover the three return paths in ``_install_hint``."""

    def test_returns_generic_when_no_package_manager(self) -> None:
        with (
            patch("the_architect.core.tmux.detect_install_method", return_value=None),
            patch("the_architect.core.tmux.is_windows", return_value=False),
        ):
            hint = tmux_mod._install_hint()
        assert "package manager" in hint

    def test_returns_windows_hint_on_native_windows(self) -> None:
        with (
            patch("the_architect.core.tmux.detect_install_method", return_value=None),
            patch("the_architect.core.tmux.is_windows", return_value=True),
        ):
            hint = tmux_mod._install_hint()
        assert "WSL" in hint

    def test_returns_update_and_install_when_both_present(self) -> None:
        method = (["apt-get", "update", "-qq"], ["apt-get", "install", "-y", "tmux"], True)
        with patch("the_architect.core.tmux.detect_install_method", return_value=method):
            hint = tmux_mod._install_hint()
        assert "sudo apt-get update" in hint
        assert "sudo apt-get install -y tmux" in hint
        assert "&&" in hint

    def test_returns_install_only_when_no_update_cmd(self) -> None:
        method = (None, ["brew", "install", "tmux"], False)
        with patch("the_architect.core.tmux.detect_install_method", return_value=method):
            hint = tmux_mod._install_hint()
        assert hint == "brew install tmux"


class TestTryInstallTmux:
    """Happy path and failure path of the installer."""

    def test_returns_false_when_no_method(self) -> None:
        with patch("the_architect.core.tmux.detect_install_method", return_value=None):
            assert tmux_mod.try_install_tmux() is False

    def test_returns_true_when_install_succeeds(self) -> None:
        method = (None, ["brew", "install", "tmux"], False)
        fake = MagicMock(returncode=0)
        with (
            patch("the_architect.core.tmux.detect_install_method", return_value=method),
            patch("the_architect.core.tmux.subprocess.run", return_value=fake),
            patch("the_architect.core.tmux.shutil.which", return_value="/usr/local/bin/tmux"),
        ):
            assert tmux_mod.try_install_tmux() is True

    def test_returns_false_when_install_fails(self) -> None:
        method = (None, ["brew", "install", "tmux"], False)
        fake = MagicMock(returncode=1)
        with (
            patch("the_architect.core.tmux.detect_install_method", return_value=method),
            patch("the_architect.core.tmux.subprocess.run", return_value=fake),
        ):
            assert tmux_mod.try_install_tmux() is False

    def test_sudo_fallback_tries_passwordless_first(self) -> None:
        """With needs_sudo=True we try ``sudo -n`` first, then ``sudo``."""
        method = (None, ["apt-get", "install", "-y", "tmux"], True)

        invocations: list[list[str]] = []

        def _run(cmd: list[str], **_: Any) -> MagicMock:
            invocations.append(list(cmd))
            # Passwordless sudo fails (returncode 1), plain sudo succeeds.
            if cmd[:2] == ["sudo", "-n"]:
                return MagicMock(returncode=1)
            return MagicMock(returncode=0)

        with (
            patch("the_architect.core.tmux.detect_install_method", return_value=method),
            patch("the_architect.core.tmux.subprocess.run", side_effect=_run),
            patch("the_architect.core.tmux.shutil.which", return_value="/usr/bin/tmux"),
        ):
            assert tmux_mod.try_install_tmux() is True

        # First call used passwordless sudo, then plain sudo retried.
        assert invocations[0][:2] == ["sudo", "-n"]
        assert invocations[1][0] == "sudo"
        assert invocations[1][1] != "-n"


# ---------------------------------------------------------------------------
# maybe_launch_tmux decision tree
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_install_attempted() -> Any:
    """``maybe_launch_tmux`` caches the install-attempt flag at module scope.

    We reset it before every test here so the decision tree is evaluated
    from a clean slate.
    """
    original = tmux_mod._tmux_install_attempted
    tmux_mod._tmux_install_attempted = False
    yield
    tmux_mod._tmux_install_attempted = original


class TestMaybeLaunchTmux:
    """Verify the orchestrator honours ``no_monitor`` and environment hints."""

    def test_no_monitor_short_circuits(self, tmp_path: Path) -> None:
        assert tmux_mod.maybe_launch_tmux(tmp_path, ["architect"], no_monitor=True) is False

    def test_native_windows_skipped(self, tmp_path: Path) -> None:
        with patch("the_architect.core.tmux.is_windows", return_value=True):
            assert tmux_mod.maybe_launch_tmux(tmp_path, ["architect"]) is False

    def test_skipped_when_already_inside_tmux(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``TMUX`` set => nested launch suppressed."""
        monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")
        with (
            patch("the_architect.core.tmux.is_windows", return_value=False),
            patch("the_architect.core.tmux.is_tmux_available", return_value=True),
        ):
            assert tmux_mod.maybe_launch_tmux(tmp_path, ["architect"]) is False

    def test_skipped_without_tty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """No stdout/stderr TTY and no /dev/tty => don't launch tmux."""
        monkeypatch.delenv("TMUX", raising=False)
        with (
            patch("the_architect.core.tmux.is_windows", return_value=False),
            patch("the_architect.core.tmux.is_tmux_available", return_value=True),
            patch("sys.stdout.isatty", return_value=False),
            patch("sys.stderr.isatty", return_value=False),
            patch("os.open", side_effect=OSError("no tty")),
        ):
            assert tmux_mod.maybe_launch_tmux(tmp_path, ["architect"]) is False

    def test_run_choice_on_existing_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the user picks ``run``, don't attach and don't create a new session."""
        monkeypatch.delenv("TMUX", raising=False)
        with (
            patch("the_architect.core.tmux.is_windows", return_value=False),
            patch("the_architect.core.tmux.is_tmux_available", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch("sys.stderr.isatty", return_value=True),
            patch("the_architect.core.tmux.session_exists", return_value=True),
            patch(
                "the_architect.core.tmux.prompt_existing_session",
                return_value="run",
            ) as p_prompt,
            patch("the_architect.core.tmux.launch_in_tmux") as p_launch,
        ):
            result = tmux_mod.maybe_launch_tmux(tmp_path, ["architect"])

        assert result is False
        p_prompt.assert_called_once()
        p_launch.assert_not_called()

    def test_launch_failure_falls_back_to_running_without_tmux(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``launch_in_tmux`` returning False bubbles up as False."""
        monkeypatch.delenv("TMUX", raising=False)
        with (
            patch("the_architect.core.tmux.is_windows", return_value=False),
            patch("the_architect.core.tmux.is_tmux_available", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch("sys.stderr.isatty", return_value=True),
            patch("the_architect.core.tmux.session_exists", return_value=False),
            patch("the_architect.core.tmux.launch_in_tmux", return_value=False),
        ):
            assert tmux_mod.maybe_launch_tmux(tmp_path, ["architect"]) is False


# ---------------------------------------------------------------------------
# maybe_launch_own_window early-exit branches
# ---------------------------------------------------------------------------


class TestMaybeLaunchOwnWindow:
    """The happy path spawns a terminal — we test the refusal paths."""

    def test_skipped_when_architect_launched_env_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ARCHITECT_LAUNCHED", "1")
        assert tmux_mod.maybe_launch_own_window(tmp_path, ["architect"]) is False

    def test_skipped_when_stdout_not_tty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ARCHITECT_LAUNCHED", raising=False)
        with patch("sys.stdout.isatty", return_value=False):
            assert tmux_mod.maybe_launch_own_window(tmp_path, ["architect"]) is False

    def test_skipped_when_no_monitor(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ARCHITECT_LAUNCHED", raising=False)
        with patch("sys.stdout.isatty", return_value=True):
            assert (
                tmux_mod.maybe_launch_own_window(tmp_path, ["architect"], no_monitor=True) is False
            )

    def test_skipped_when_gui_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ARCHITECT_LAUNCHED", raising=False)
        with (
            patch("sys.stdout.isatty", return_value=True),
            patch("the_architect.core.tmux.is_gui_available", return_value=False),
        ):
            assert tmux_mod.maybe_launch_own_window(tmp_path, ["architect"]) is False

    def test_skipped_when_no_terminal_detected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ARCHITECT_LAUNCHED", raising=False)
        with (
            patch("sys.stdout.isatty", return_value=True),
            patch("the_architect.core.tmux.is_gui_available", return_value=True),
            patch("the_architect.core.tmux.detect_terminal_emulator", return_value=None),
        ):
            assert tmux_mod.maybe_launch_own_window(tmp_path, ["architect"]) is False

    def test_skipped_when_build_window_command_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_build_window_command returning None => short-circuit False."""
        monkeypatch.delenv("ARCHITECT_LAUNCHED", raising=False)
        with (
            patch("sys.stdout.isatty", return_value=True),
            patch("the_architect.core.tmux.is_gui_available", return_value=True),
            patch(
                "the_architect.core.tmux.detect_terminal_emulator",
                return_value="gnome-terminal",
            ),
            patch("the_architect.core.tmux._build_window_command", return_value=None),
        ):
            assert tmux_mod.maybe_launch_own_window(tmp_path, ["architect"]) is False


# ---------------------------------------------------------------------------
# _build_window_command — Linux emulator branches
# ---------------------------------------------------------------------------


class TestBuildWindowCommandLinuxEmulators:
    """Verify each Linux terminal branch picks a working command."""

    @pytest.mark.parametrize(
        "terminal,expected_first",
        [
            ("gnome-terminal", "gnome-terminal"),
            ("konsole", "konsole"),
            ("kitty", "kitty"),
            ("alacritty", "alacritty"),
            ("xterm", "xterm"),
            ("xdg-terminal-exec", "xdg-terminal-exec"),
        ],
    )
    def test_each_linux_terminal_returns_expected_binary(
        self, terminal: str, expected_first: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(tmux_mod.sys, "platform", "linux")
        with patch("the_architect.core.tmux._get_portable_shell", return_value="/bin/bash"):
            cmd = tmux_mod._build_window_command(["architect"], terminal)
        assert cmd is not None
        assert cmd[0] == expected_first

    def test_xfce4_terminal_uses_single_arg_form(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """xfce4-terminal takes a single ``-e`` argument (quoted)."""
        monkeypatch.setattr(tmux_mod.sys, "platform", "linux")
        with patch("the_architect.core.tmux._get_portable_shell", return_value="/bin/bash"):
            cmd = tmux_mod._build_window_command(["architect"], "xfce4-terminal")
        assert cmd is not None
        assert cmd[0] == "xfce4-terminal"
        assert cmd[1] == "-e"

    def test_returns_none_when_no_shell_on_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tmux_mod.sys, "platform", "linux")
        with patch("the_architect.core.tmux._get_portable_shell", return_value=None):
            assert tmux_mod._build_window_command(["architect"], "gnome-terminal") is None

    def test_returns_none_for_unknown_linux_terminal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unknown terminal identifier falls through to None."""
        monkeypatch.setattr(tmux_mod.sys, "platform", "linux")
        with patch("the_architect.core.tmux._get_portable_shell", return_value="/bin/bash"):
            assert tmux_mod._build_window_command(["architect"], "no-such-term") is None


class TestBuildWindowCommandMacOS:
    """Verify the macOS / iTerm AppleScript branches."""

    def test_macos_terminal_uses_osascript(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tmux_mod.sys, "platform", "darwin")
        cmd = tmux_mod._build_window_command(["architect"], "macos-terminal")
        assert cmd is not None
        assert cmd[0] == "osascript"
        assert any("Terminal" in part for part in cmd)

    def test_iterm_uses_osascript_with_iterm_script(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tmux_mod.sys, "platform", "darwin")
        cmd = tmux_mod._build_window_command(["architect"], "iterm")
        assert cmd is not None
        assert cmd[0] == "osascript"
        assert any("iTerm" in part for part in cmd)


# ---------------------------------------------------------------------------
# Smoke test — ensure _tmux_install_attempted round-trip does not leak
# ---------------------------------------------------------------------------


def test_tmux_install_attempted_is_resettable() -> None:
    """Sanity: our autouse fixture preserves the original flag value."""
    assert tmux_mod._tmux_install_attempted is False


# ---------------------------------------------------------------------------
# prompt_existing_session — keypress branches
# ---------------------------------------------------------------------------


class TestPromptExistingSessionKeys:
    """Drive each keypress branch of ``prompt_existing_session``."""

    @pytest.mark.parametrize(
        "keypress,expected",
        [("K", "kill"), ("k", "kill"), ("R", "run"), ("r", "run"), ("A", "attach")],
    )
    def test_keypress_returns_matching_action(self, keypress: str, expected: str) -> None:
        fake_termios = MagicMock()
        fake_termios.tcgetattr.return_value = "orig"
        fake_tty = MagicMock()

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch.dict(
                "sys.modules",
                {"termios": fake_termios, "tty": fake_tty},
            ),
            patch("sys.stdin.fileno", return_value=0),
            patch("sys.stdin.read", return_value=keypress),
            patch("select.select", return_value=([object()], [], [])),
        ):
            assert tmux_mod.prompt_existing_session("architect-proj") == expected

    def test_import_error_falls_back_to_attach(self) -> None:
        """Windows (no termios/tty) => default branch returns attach."""

        import builtins

        real_import = builtins.__import__

        def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "termios":
                raise ImportError("no termios on this platform")
            return real_import(name, *args, **kwargs)

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.__import__", side_effect=_fake_import),
        ):
            assert tmux_mod.prompt_existing_session("architect-proj") == "attach"

    def test_generic_exception_falls_back_to_attach(self) -> None:
        """An unexpected exception in the TTY path must default to attach."""

        with (
            patch("sys.stdin.isatty", side_effect=RuntimeError("sys unhappy")),
        ):
            assert tmux_mod.prompt_existing_session("architect-proj") == "attach"


# ---------------------------------------------------------------------------
# prompt_install_tmux — keypress branches
# ---------------------------------------------------------------------------


class TestPromptInstallTmuxKeys:
    @pytest.mark.parametrize(
        "keypress,expected",
        [("y", True), ("Y", True), ("n", False), ("N", False)],
    )
    def test_keypress_maps_to_yes_or_no(self, keypress: str, expected: bool) -> None:
        fake_termios = MagicMock()
        fake_termios.tcgetattr.return_value = "orig"
        fake_tty = MagicMock()

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch.dict(
                "sys.modules",
                {"termios": fake_termios, "tty": fake_tty},
            ),
            patch("sys.stdin.fileno", return_value=0),
            patch("sys.stdin.read", return_value=keypress),
            patch("select.select", return_value=([object()], [], [])),
        ):
            assert tmux_mod.prompt_install_tmux() is expected

    def test_import_error_falls_back_to_yes(self) -> None:
        """When termios cannot be imported we default to yes (install)."""

        import builtins

        real_import = builtins.__import__

        def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "termios":
                raise ImportError("no termios here")
            return real_import(name, *args, **kwargs)

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.__import__", side_effect=_fake_import),
        ):
            assert tmux_mod.prompt_install_tmux() is True


# ---------------------------------------------------------------------------
# _forward_env_vars — subprocess exception path
# ---------------------------------------------------------------------------


def test_forward_env_vars_swallows_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A subprocess failure while forwarding an env var must be swallowed."""
    # Clear everything, then set just one variable.
    for v in tmux_mod._FORWARD_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    with patch(
        "the_architect.core.tmux.subprocess.run",
        side_effect=subprocess.SubprocessError("boom"),
    ):
        # Must not raise.
        tmux_mod._forward_env_vars("architect-proj")


# ---------------------------------------------------------------------------
# maybe_launch_tmux — install prompt branches
# ---------------------------------------------------------------------------


class TestMaybeLaunchTmuxInstallPrompt:
    """Drive the install-offer path when tmux is not on PATH."""

    def test_install_declined_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TMUX", raising=False)
        with (
            patch("the_architect.core.tmux.is_windows", return_value=False),
            patch("the_architect.core.tmux.is_tmux_available", return_value=False),
            patch("sys.stdout.isatty", return_value=True),
            patch("sys.stdin.isatty", return_value=True),
            patch("the_architect.core.tmux.prompt_install_tmux", return_value=False),
        ):
            assert tmux_mod.maybe_launch_tmux(tmp_path, ["architect"]) is False

    def test_install_accepted_then_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TMUX", raising=False)
        with (
            patch("the_architect.core.tmux.is_windows", return_value=False),
            patch("the_architect.core.tmux.is_tmux_available", return_value=False),
            patch("sys.stdout.isatty", return_value=True),
            patch("sys.stdin.isatty", return_value=True),
            patch("the_architect.core.tmux.prompt_install_tmux", return_value=True),
            patch("the_architect.core.tmux.try_install_tmux", return_value=False),
        ):
            assert tmux_mod.maybe_launch_tmux(tmp_path, ["architect"]) is False

    def test_non_interactive_skips_install_prompt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TMUX", raising=False)
        with (
            patch("the_architect.core.tmux.is_windows", return_value=False),
            patch("the_architect.core.tmux.is_tmux_available", return_value=False),
            patch("sys.stdout.isatty", return_value=False),
            patch("sys.stdin.isatty", return_value=False),
        ):
            assert tmux_mod.maybe_launch_tmux(tmp_path, ["architect"]) is False
