"""Dedicated unit tests for the_architect/core/tmux.py.

Tests focus on functions whose logic is not otherwise exercised in
test_monitor.py:
  - is_tmux_available() — shutil.which mock
  - is_inside_tmux()    — TMUX env var
  - get_session_name()  — format "architect-<dirname>"
  - is_windows()        — sys.platform mock
  - is_gui_available()  — DISPLAY / WAYLAND_DISPLAY / platform
  - detect_install_method() — shutil.which for various package managers
  - _build_runner_cmd()     — command string format
  - _build_dashboard_cmd()  — command string format
  - _build_window_command() — terminal emulator detection branches
  - print_reattach_hint()   — output without crash
"""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from the_architect.core.tmux import (
    _FORWARD_ENV_VARS,
    _build_dashboard_cmd,
    _build_runner_cmd,
    _build_window_command,
    _forward_env_vars,
    _get_portable_shell,
    detect_install_method,
    get_session_name,
    is_gui_available,
    is_inside_tmux,
    is_tmux_available,
    is_windows,
    print_reattach_hint,
)

# ---------------------------------------------------------------------------
# is_tmux_available
# ---------------------------------------------------------------------------


class TestIsTmuxAvailable:
    """Tests for is_tmux_available()."""

    def test_returns_true_when_tmux_on_path(self) -> None:
        """Should return True when shutil.which finds tmux."""
        with patch("the_architect.core.tmux.shutil.which", return_value="/usr/bin/tmux"):
            assert is_tmux_available() is True

    def test_returns_false_when_tmux_not_found(self) -> None:
        """Should return False when shutil.which returns None."""
        with patch("the_architect.core.tmux.shutil.which", return_value=None):
            assert is_tmux_available() is False


# ---------------------------------------------------------------------------
# is_inside_tmux
# ---------------------------------------------------------------------------


class TestIsInsideTmux:
    """Tests for is_inside_tmux()."""

    def test_returns_true_when_tmux_env_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return True when the TMUX environment variable is set."""
        monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")
        assert is_inside_tmux() is True

    def test_returns_false_when_tmux_env_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return False when the TMUX environment variable is not set."""
        monkeypatch.delenv("TMUX", raising=False)
        assert is_inside_tmux() is False

    def test_returns_false_when_tmux_env_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return False when the TMUX variable is an empty string."""
        monkeypatch.setenv("TMUX", "")
        assert is_inside_tmux() is False


# ---------------------------------------------------------------------------
# get_session_name
# ---------------------------------------------------------------------------


class TestGetSessionName:
    """Tests for get_session_name()."""

    def test_format_is_architect_prefix_plus_dirname(self, tmp_path: Path) -> None:
        """Session name should be 'architect-<dirname>'."""
        project = tmp_path / "my-api"
        project.mkdir()
        assert get_session_name(project) == f"architect-{project.name}"

    def test_starts_with_architect_dash(self, tmp_path: Path) -> None:
        """Session name must start with 'architect-'."""
        project = tmp_path / "some-project"
        project.mkdir()
        assert get_session_name(project).startswith("architect-")

    def test_different_projects_give_different_names(self, tmp_path: Path) -> None:
        """Two different project directories must produce different session names."""
        proj_a = tmp_path / "alpha"
        proj_b = tmp_path / "beta"
        proj_a.mkdir()
        proj_b.mkdir()
        assert get_session_name(proj_a) != get_session_name(proj_b)

    def test_name_uses_only_dirname_not_full_path(self, tmp_path: Path) -> None:
        """Only the final directory name (not the full path) should appear."""
        project = tmp_path / "nested" / "deep" / "project-x"
        project.mkdir(parents=True)
        name = get_session_name(project)
        assert name == "architect-project-x"
        # The parent path components must not appear in the session name
        assert "nested" not in name
        assert "deep" not in name


# ---------------------------------------------------------------------------
# is_windows
# ---------------------------------------------------------------------------


class TestIsWindows:
    """Tests for is_windows()."""

    def test_returns_true_on_win32(self) -> None:
        """Should return True when sys.platform is 'win32'."""
        original = sys.platform
        sys.platform = "win32"
        try:
            assert is_windows() is True
        finally:
            sys.platform = original

    def test_returns_false_on_linux(self) -> None:
        """Should return False when sys.platform is 'linux'."""
        original = sys.platform
        sys.platform = "linux"
        try:
            assert is_windows() is False
        finally:
            sys.platform = original

    def test_returns_false_on_darwin(self) -> None:
        """Should return False on macOS."""
        original = sys.platform
        sys.platform = "darwin"
        try:
            assert is_windows() is False
        finally:
            sys.platform = original


# ---------------------------------------------------------------------------
# is_gui_available
# ---------------------------------------------------------------------------


class TestIsGuiAvailable:
    """Tests for is_gui_available()."""

    def test_always_true_on_darwin(self) -> None:
        """macOS always has a GUI."""
        original = sys.platform
        sys.platform = "darwin"
        try:
            assert is_gui_available() is True
        finally:
            sys.platform = original

    def test_always_true_on_win32(self) -> None:
        """Windows always has a GUI."""
        original = sys.platform
        sys.platform = "win32"
        try:
            assert is_gui_available() is True
        finally:
            sys.platform = original

    def test_linux_true_with_display(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Linux is GUI-available when DISPLAY is set."""
        original = sys.platform
        sys.platform = "linux"
        try:
            monkeypatch.setenv("DISPLAY", ":0")
            monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
            assert is_gui_available() is True
        finally:
            sys.platform = original

    def test_linux_true_with_wayland(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Linux is GUI-available when WAYLAND_DISPLAY is set."""
        original = sys.platform
        sys.platform = "linux"
        try:
            monkeypatch.delenv("DISPLAY", raising=False)
            monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
            assert is_gui_available() is True
        finally:
            sys.platform = original

    def test_linux_false_without_display_or_wayland(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Linux with no DISPLAY or WAYLAND_DISPLAY is not GUI-available."""
        original = sys.platform
        sys.platform = "linux"
        try:
            monkeypatch.delenv("DISPLAY", raising=False)
            monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
            assert is_gui_available() is False
        finally:
            sys.platform = original


# ---------------------------------------------------------------------------
# detect_install_method
# ---------------------------------------------------------------------------


class TestDetectInstallMethod:
    """Tests for detect_install_method()."""

    def test_returns_none_on_windows(self) -> None:
        """Should skip detection and return None on native Windows."""
        with patch("the_architect.core.tmux.is_windows", return_value=True):
            assert detect_install_method() is None

    def test_returns_none_when_no_manager_found(self) -> None:
        """Should return None when no known package manager is on PATH."""
        with (
            patch("the_architect.core.tmux.is_windows", return_value=False),
            patch("the_architect.core.tmux.shutil.which", return_value=None),
        ):
            assert detect_install_method() is None

    def test_detects_brew(self) -> None:
        """Should detect Homebrew with no update step and no sudo."""

        def _which(name: str) -> str | None:
            return "/usr/local/bin/brew" if name == "brew" else None

        with (
            patch("the_architect.core.tmux.is_windows", return_value=False),
            patch("the_architect.core.tmux.shutil.which", side_effect=_which),
        ):
            result = detect_install_method()
        assert result is not None
        update_cmd, install_cmd, needs_sudo = result
        assert update_cmd is None
        assert "brew" in install_cmd
        assert needs_sudo is False

    def test_detects_apt_get(self) -> None:
        """Should detect apt-get with an update step and sudo requirement."""

        def _which(name: str) -> str | None:
            return "/usr/bin/apt-get" if name == "apt-get" else None

        with (
            patch("the_architect.core.tmux.is_windows", return_value=False),
            patch("the_architect.core.tmux.shutil.which", side_effect=_which),
        ):
            result = detect_install_method()
        assert result is not None
        update_cmd, install_cmd, needs_sudo = result
        assert update_cmd is not None
        assert "apt-get" in update_cmd
        assert "apt-get" in install_cmd
        assert needs_sudo is True

    def test_detects_pacman(self) -> None:
        """Should detect pacman on Arch Linux."""

        def _which(name: str) -> str | None:
            # brew not present, apt-get not present, apt not present, then pacman
            if name == "pacman":
                return "/usr/bin/pacman"
            return None

        with (
            patch("the_architect.core.tmux.is_windows", return_value=False),
            patch("the_architect.core.tmux.shutil.which", side_effect=_which),
        ):
            result = detect_install_method()
        assert result is not None
        _, install_cmd, needs_sudo = result
        assert "pacman" in install_cmd
        assert needs_sudo is True

    def test_detects_nix_env(self) -> None:
        """Should detect nix-env — no update step, no sudo."""

        def _which(name: str) -> str | None:
            return "/run/sw/bin/nix-env" if name == "nix-env" else None

        with (
            patch("the_architect.core.tmux.is_windows", return_value=False),
            patch("the_architect.core.tmux.shutil.which", side_effect=_which),
        ):
            result = detect_install_method()
        assert result is not None
        update_cmd, install_cmd, needs_sudo = result
        assert update_cmd is None
        assert "nix-env" in install_cmd
        assert needs_sudo is False


# ---------------------------------------------------------------------------
# _build_runner_cmd
# ---------------------------------------------------------------------------


class TestBuildRunnerCmd:
    """Tests for _build_runner_cmd()."""

    def test_inserts_no_monitor_flag(self) -> None:
        """--no-monitor should be injected after the script name."""
        argv = ["/usr/local/bin/architect", "--project", "/tmp/proj"]
        cmd = _build_runner_cmd(argv)
        assert "--no-monitor" in cmd

    def test_no_monitor_not_duplicated(self) -> None:
        """--no-monitor should not be inserted when already present."""
        argv = ["/usr/local/bin/architect", "--no-monitor", "--project", "/tmp/proj"]
        cmd = _build_runner_cmd(argv)
        # Count occurrences — should appear exactly once
        assert cmd.count("--no-monitor") == 1

    def test_preserves_other_flags(self) -> None:
        """Other arguments should be preserved in the output."""
        argv = ["/usr/local/bin/architect", "--project", "/tmp/proj", "--max-retries", "5"]
        cmd = _build_runner_cmd(argv)
        assert "architect" in cmd
        assert "--project" in cmd
        assert "--max-retries" in cmd
        assert "5" in cmd

    def test_returns_string(self) -> None:
        """Should return a string (not a list)."""
        result = _build_runner_cmd(["/usr/bin/architect"])
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _build_dashboard_cmd
# ---------------------------------------------------------------------------


class TestBuildDashboardCmd:
    """Tests for _build_dashboard_cmd()."""

    def test_contains_module_path(self, tmp_path: Path) -> None:
        """Command should reference the dashboard module."""
        cmd = _build_dashboard_cmd(tmp_path)
        assert "the_architect.core.dashboard" in cmd

    def test_contains_project_dir(self, tmp_path: Path) -> None:
        """Command should include the project directory path."""
        cmd = _build_dashboard_cmd(tmp_path)
        assert str(tmp_path) in cmd

    def test_contains_python_executable(self, tmp_path: Path) -> None:
        """Command should reference the Python executable."""
        cmd = _build_dashboard_cmd(tmp_path)
        # The python executable path should appear (possibly quoted)
        python = sys.executable
        # Strip quotes for comparison
        assert python.replace("'", "") in cmd.replace("'", "")

    def test_returns_string(self, tmp_path: Path) -> None:
        """Should return a string."""
        result = _build_dashboard_cmd(tmp_path)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _build_window_command
# ---------------------------------------------------------------------------


class TestBuildWindowCommand:
    """Tests for _build_window_command()."""

    def test_macos_terminal_returns_osascript_list(self) -> None:
        """macOS Terminal.app should return an osascript command list."""
        original = sys.platform
        sys.platform = "darwin"
        try:
            result = _build_window_command(["/usr/bin/architect"], "macos-terminal")
            assert result is not None
            assert result[0] == "osascript"
            assert any("Terminal" in arg for arg in result)
        finally:
            sys.platform = original

    def test_iterm_returns_osascript_list(self) -> None:
        """iTerm2 should return an osascript command list referencing iTerm."""
        original = sys.platform
        sys.platform = "darwin"
        try:
            result = _build_window_command(["/usr/bin/architect"], "iterm")
            assert result is not None
            assert result[0] == "osascript"
            assert any("iTerm" in arg for arg in result)
        finally:
            sys.platform = original

    def test_windows_terminal_returns_wt_command(self) -> None:
        """Windows Terminal should return a wt.exe command list."""
        original = sys.platform
        sys.platform = "win32"
        try:
            result = _build_window_command(["/usr/bin/architect"], "windows-terminal")
            assert result is not None
            assert "wt.exe" in result
        finally:
            sys.platform = original

    def test_windows_cmd_returns_cmd_command(self) -> None:
        """Windows cmd fallback should return a cmd.exe command list."""
        original = sys.platform
        sys.platform = "win32"
        try:
            result = _build_window_command(["/usr/bin/architect"], "windows-cmd")
            assert result is not None
            assert "cmd.exe" in result
        finally:
            sys.platform = original

    def test_linux_gnome_terminal(self) -> None:
        """gnome-terminal should return an appropriate command list."""
        original = sys.platform
        sys.platform = "linux"
        try:
            with patch("the_architect.core.tmux._get_portable_shell", return_value="/bin/sh"):
                result = _build_window_command(["/usr/bin/architect"], "gnome-terminal")
            assert result is not None
            assert "gnome-terminal" in result
        finally:
            sys.platform = original

    def test_linux_xterm(self) -> None:
        """xterm should return an appropriate command list."""
        original = sys.platform
        sys.platform = "linux"
        try:
            with patch("the_architect.core.tmux._get_portable_shell", return_value="/bin/sh"):
                result = _build_window_command(["/usr/bin/architect"], "xterm")
            assert result is not None
            assert "xterm" in result
        finally:
            sys.platform = original

    def test_unknown_terminal_returns_none(self) -> None:
        """An unrecognised terminal identifier should return None."""
        original = sys.platform
        sys.platform = "linux"
        try:
            with patch("the_architect.core.tmux._get_portable_shell", return_value="/bin/sh"):
                result = _build_window_command(["/usr/bin/architect"], "unknown-terminal-xyz")
            assert result is None
        finally:
            sys.platform = original

    def test_inserts_no_monitor_flag(self) -> None:
        """The generated command should include --no-monitor to prevent recursion."""
        original = sys.platform
        sys.platform = "linux"
        try:
            with patch("the_architect.core.tmux._get_portable_shell", return_value="/bin/sh"):
                result = _build_window_command(
                    ["/usr/bin/architect", "--project", "/tmp/x"], "xterm"
                )
            assert result is not None
            assert "--no-monitor" in " ".join(result)
        finally:
            sys.platform = original


# ---------------------------------------------------------------------------
# print_reattach_hint
# ---------------------------------------------------------------------------


class TestPrintReattachHint:
    """Tests for print_reattach_hint()."""

    def test_does_not_crash(self, tmp_path: Path) -> None:
        """print_reattach_hint() should produce output without raising."""
        project = tmp_path / "my-project"
        project.mkdir()
        captured = StringIO()
        with patch("sys.stdout", captured):
            print_reattach_hint(project)
        output = captured.getvalue()
        assert len(output) > 0

    def test_output_contains_session_name(self, tmp_path: Path) -> None:
        """Output should contain the session name."""
        project = tmp_path / "my-project"
        project.mkdir()
        captured = StringIO()
        with patch("sys.stdout", captured):
            print_reattach_hint(project)
        output = captured.getvalue()
        assert "architect-my-project" in output

    def test_output_contains_reattach_command(self, tmp_path: Path) -> None:
        """Output should contain the reattach hint."""
        project = tmp_path / "my-project"
        project.mkdir()
        captured = StringIO()
        with patch("sys.stdout", captured):
            print_reattach_hint(project)
        output = captured.getvalue()
        assert "monitor" in output or "architect" in output.lower()


# ---------------------------------------------------------------------------
# _get_portable_shell (T14.1)
# ---------------------------------------------------------------------------


class TestGetPortableShell:
    """Tests for _get_portable_shell() — T01 agnostic shell resolution."""

    def test_returns_string_on_current_platform(self) -> None:
        """Should return a non-None string when bash or sh is available in the test env."""
        result = _get_portable_shell()
        # In any CI/Linux/macOS environment bash or sh must be present
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_none_when_neither_shell_found(self) -> None:
        """Should return None when shutil.which returns None for both bash and sh."""
        with patch("the_architect.core.tmux.shutil.which", return_value=None):
            result = _get_portable_shell()
        assert result is None

    def test_returns_bash_path_when_bash_available(self) -> None:
        """Should return the bash path when bash is found by shutil.which."""

        def _which(name: str) -> str | None:
            return "/bin/bash" if name == "bash" else None

        with patch("the_architect.core.tmux.shutil.which", side_effect=_which):
            result = _get_portable_shell()
        assert result == "/bin/bash"

    def test_returns_sh_when_only_sh_available(self) -> None:
        """Should fall back to sh path when bash is not found."""

        def _which(name: str) -> str | None:
            return "/bin/sh" if name == "sh" else None

        with patch("the_architect.core.tmux.shutil.which", side_effect=_which):
            result = _get_portable_shell()
        assert result == "/bin/sh"

    def test_prefers_bash_over_sh_when_both_available(self) -> None:
        """Should prefer bash even when sh is also available."""

        def _which(name: str) -> str | None:
            if name == "bash":
                return "/usr/bin/bash"
            if name == "sh":
                return "/bin/sh"
            return None

        with patch("the_architect.core.tmux.shutil.which", side_effect=_which):
            result = _get_portable_shell()
        assert result == "/usr/bin/bash"


# ---------------------------------------------------------------------------
# _FORWARD_ENV_VARS and _forward_env_vars (T14.2)
# ---------------------------------------------------------------------------


class TestForwardEnvVars:
    """Tests for _FORWARD_ENV_VARS list coverage and _forward_env_vars() behaviour."""

    def test_forward_env_vars_contains_required_entries(self) -> None:
        """_FORWARD_ENV_VARS must include the minimum required environment variables."""
        required = {
            "PATH",
            "HOME",
            "SHELL",
            "OPENCODE_CONFIG",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "GROQ_API_KEY",
        }
        for var in required:
            assert var in _FORWARD_ENV_VARS, f"_FORWARD_ENV_VARS is missing '{var}'"

    def test_forward_env_vars_skips_unset_variables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_forward_env_vars() must NOT call subprocess for variables not in os.environ."""
        # Remove all vars in _FORWARD_ENV_VARS from the environment
        for var in _FORWARD_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

        call_count: list[int] = [0]

        def mock_run(cmd: list[str], **kwargs: object) -> object:
            call_count[0] += 1
            return None

        with patch("the_architect.core.tmux.subprocess.run", side_effect=mock_run):
            _forward_env_vars("test-session")

        assert call_count[0] == 0, "_forward_env_vars must not call subprocess when no vars are set"

    def test_forward_env_vars_calls_subprocess_for_set_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_forward_env_vars() must call subprocess once per variable that is set."""
        # Remove all vars first
        for var in _FORWARD_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

        # Set two known variables
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        monkeypatch.setenv("HOME", "/home/testuser")

        captured_calls: list[list[str]] = []

        def mock_run(cmd: list[str], **kwargs: object) -> object:
            captured_calls.append(list(cmd))
            return None

        with patch("the_architect.core.tmux.subprocess.run", side_effect=mock_run):
            _forward_env_vars("test-session")

        assert len(captured_calls) == 2, "Expected exactly 2 subprocess calls for the 2 set vars"
        # Both calls should be tmux set-environment commands
        for call in captured_calls:
            assert call[0] == "tmux"
            assert "set-environment" in call


class TestLaunchInTmuxSinglePane:
    """Tests for ``launch_in_tmux`` single-pane mode.

    When the Textual TUI is active, wrapping the run in tmux is
    useful purely for the detach/reattach flow — the side-panel
    dashboard would just fight with the TUI for screen space. These
    tests make sure ``single_pane=True`` really skips the split.
    """

    def test_single_pane_mode_skips_split_window(self, tmp_path: Path) -> None:
        """When ``single_pane=True``, no ``tmux split-window`` call is made."""
        from unittest.mock import MagicMock, patch

        from the_architect.core.tmux import launch_in_tmux

        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            captured.append(list(cmd))
            result = MagicMock()
            result.returncode = 0
            result.stderr = b""
            return result

        with (
            patch("the_architect.core.tmux.subprocess.run", side_effect=fake_run),
            patch("the_architect.core.tmux._get_portable_shell", return_value="/bin/bash"),
            patch("the_architect.core.tmux.attach_session") as mock_attach,
        ):
            launch_in_tmux(
                "architect-test",
                tmp_path,
                ["architect"],
                single_pane=True,
            )

        # No split-window call should appear in the captured command list.
        split_calls = [c for c in captured if "split-window" in c]
        assert split_calls == []
        # The new-session call is still present — we still want the
        # tmux wrapper, just without the split.
        new_session_calls = [c for c in captured if "new-session" in c]
        assert len(new_session_calls) == 1
        # attach_session was called at the end like the normal path.
        mock_attach.assert_called_once_with("architect-test")

    def test_split_pane_mode_still_splits(self, tmp_path: Path) -> None:
        """Default (``single_pane=False``) behaviour is unchanged —
        regression guard so the dashboard still shows up when the TUI
        is off.
        """
        from unittest.mock import MagicMock, patch

        from the_architect.core.tmux import launch_in_tmux

        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            captured.append(list(cmd))
            result = MagicMock()
            result.returncode = 0
            result.stderr = b""
            return result

        with (
            patch("the_architect.core.tmux.subprocess.run", side_effect=fake_run),
            patch("the_architect.core.tmux._get_portable_shell", return_value="/bin/bash"),
            patch("the_architect.core.tmux.attach_session"),
        ):
            launch_in_tmux(
                "architect-test",
                tmp_path,
                ["architect"],
                # single_pane defaults to False — keep the dashboard.
            )

        split_calls = [c for c in captured if "split-window" in c]
        assert len(split_calls) == 1
