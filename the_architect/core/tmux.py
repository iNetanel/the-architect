"""Session and window management for The Architect.

When tmux is available and the user is not already inside a tmux session,
The Architect automatically wraps itself in a split-pane tmux session:

- Left pane (70%): live execution output from the runner
- Right pane (30%): the dashboard process, updating every 2 seconds

When tmux is NOT installed, The Architect offers to install it automatically
using the system package manager (apt, brew, pacman, dnf, etc.).  If the
install succeeds the run continues inside tmux.  If it fails, a one-time
hint is shown and the run continues in the current terminal.

The ``--no-monitor`` flag bypasses all tmux logic.

This module never crashes the run — all operations are wrapped in
try/except and fall back to running without monitoring.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from loguru import logger
from rich.console import Console

# Sentinel so we only show the "tmux not found" prompt once per process
_tmux_install_attempted = False


def is_tmux_available() -> bool:
    """Check whether tmux is installed and on PATH.

    Uses the same pattern as ``check_opencode_installed`` in opencode_config.py.

    Returns:
        True if tmux is found in PATH, False otherwise.
    """
    return shutil.which("tmux") is not None


def is_inside_tmux() -> bool:
    """Check whether the current process is already running inside a tmux session.

    Reads the ``TMUX`` environment variable, which tmux sets for all child
    processes.  If it is set, we are inside tmux and must not create a
    nested session.

    Returns:
        True if the ``TMUX`` environment variable is set.
    """
    return bool(os.environ.get("TMUX"))


def get_session_name(project_dir: Path) -> str:
    """Return the tmux session name for a project directory.

    The session is named ``architect-<project-dir-name>``, e.g.
    ``architect-my-api`` for a project at ``/home/user/my-api``.

    Args:
        project_dir: The project root directory (should be resolved/absolute).

    Returns:
        Session name string.
    """
    return f"architect-{project_dir.name}"


def session_exists(session_name: str) -> bool:
    """Check whether a tmux session with the given name exists.

    Args:
        session_name: The tmux session name to check.

    Returns:
        True if the session exists.
    """
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return False


def list_architect_sessions() -> list[str]:
    """List all active tmux sessions whose names start with ``architect-``.

    Returns:
        List of session name strings.
    """
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        sessions = [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip().startswith("architect-")
        ]
        return sessions
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return []


def kill_session(session_name: str) -> bool:
    """Kill a tmux session by name.

    Args:
        session_name: The tmux session name to kill.

    Returns:
        True if the session was killed successfully.
    """
    try:
        result = subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return False


def attach_session(session_name: str) -> None:
    """Attach to an existing tmux session, replacing the current process.

    Uses ``os.execvp`` so the current process becomes the tmux client —
    no subprocess overhead and the terminal is properly handed over.

    Args:
        session_name: The tmux session name to attach to.
    """
    os.execvp("tmux", ["tmux", "attach-session", "-t", session_name])


def _get_portable_shell() -> str | None:
    """Return the absolute path of the best available POSIX shell.

    Prefers ``bash`` if it is on PATH, falling back to ``sh``.
    Returns ``None`` if neither is found (e.g. native Windows without
    Git Bash), which callers should treat as a signal to skip the
    shell-wrapped launch path.

    Returns:
        Absolute path to ``bash`` or ``sh``, or ``None``.
    """
    shell = shutil.which("bash") or shutil.which("sh")
    return shell


# ---------------------------------------------------------------------------
# Padded Console — right gap so Rich output doesn't touch the side panel
# ---------------------------------------------------------------------------


# How much to reduce the console width by when inside a tmux split pane.
# This creates a visual gap between the left pane's Rich-rendered output
# and the right pane (dashboard side panel).
_SIDE_PANEL_GAP = 2


class PaddedConsole(Console):
    """Rich Console that reduces its width when inside a tmux split pane.

    When the architect runs inside a tmux split, Rich-rendered output
    (console.print, rules, tables) extends all the way to the pane
    border — right next to the dashboard side panel.  By reporting a
    slightly smaller width, Rich renders everything narrower, creating
    a visual gap on the right side.

    When NOT inside tmux, behaves identically to a regular Console
    (no padding since there's no side panel to gap from).

    The gap size is controlled by the ``_SIDE_PANEL_GAP`` constant.

    On Windows, ``legacy_windows=False`` is passed explicitly so that
    Rich uses VT/ANSI escape sequences instead of the old Win32 console
    API.  Windows Terminal and PowerShell 5.1+ both support VT natively.
    Without this flag, Rich falls back to its legacy Windows rendering
    path which produces visibly degraded output (no colour, no box
    characters, no cursor positioning).
    """

    def __init__(self, **kwargs: object) -> None:
        """Initialise with Windows-safe defaults."""
        import sys as _sys

        if _sys.platform == "win32":
            kwargs.setdefault("legacy_windows", False)
        super().__init__(**kwargs)  # type: ignore[arg-type]

    @property
    def width(self) -> int:
        """Return the effective console width, reduced when inside tmux."""
        base = super().width
        if is_inside_tmux():
            return max(base - _SIDE_PANEL_GAP, 20)
        return base

    @width.setter
    def width(self, value: int) -> None:
        """Delegate width setting to the parent Console."""
        Console.width.fset(self, value)  # type: ignore[attr-defined]


def _configure_pane_borders(session_name: str) -> None:
    """Make tmux pane borders invisible for a clean split-pane look.

    Tries ``pane-border-lines none`` (tmux 3.4+) which removes the border
    entirely.  Falls back to setting the border colour to a very dim gray
    (brightblack / colour 8) which is nearly invisible on most terminals.

    This is a best-effort, non-fatal operation — failures are silently
    logged at debug level.

    Args:
        session_name: The tmux session name.
    """
    try:
        # Try modern tmux 3.4+ option: no border at all
        result = subprocess.run(
            [
                "tmux",
                "set-window-option",
                "-t",
                session_name,
                "pane-border-lines",
                "none",
            ],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            logger.debug("Set pane-border-lines none (tmux 3.4+)")
            return
        logger.debug("pane-border-lines none not supported, falling back to subtle colour")
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass

    # Fallback for tmux < 3.4: set border colour to brightblack (colour 8)
    # which is dark gray on most terminals — very subtle, nearly invisible.
    try:
        subprocess.run(
            [
                "tmux",
                "set-window-option",
                "-t",
                session_name,
                "pane-border-style",
                "fg=brightblack",
            ],
            capture_output=True,
            timeout=5,
        )
        subprocess.run(
            [
                "tmux",
                "set-window-option",
                "-t",
                session_name,
                "pane-active-border-style",
                "fg=brightblack",
            ],
            capture_output=True,
            timeout=5,
        )
        logger.debug("Set pane border style to brightblack (subtle)")
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
        logger.debug(f"Failed to set pane border style (non-fatal): {exc!r}")


def launch_in_tmux(
    session_name: str,
    project_dir: Path,
    argv: list[str],
    single_pane: bool = False,
) -> bool:
    """Create a new tmux session with the split-pane layout.

    Layout:
        Left pane (~70%): the architect runner command
        Right pane (~30%): the dashboard process

    When ``single_pane=True`` the right pane (dashboard) is skipped
    entirely — the runner gets the full window.  This is the mode
    used when the Textual TUI is active: the TUI already renders
    every piece of information the dashboard would show, so the
    side panel only competes for screen space.  We still wrap the
    run in a tmux session so the user keeps the ``Ctrl+B D`` detach
    and ``tmux attach`` reattach flow.

    Pane borders are set to invisible (tmux 3.4+) or very subtle
    (brightblack fallback) so there is no visible line between panes —
    just a clean visual gap.  No-op in ``single_pane`` mode where
    there are no borders to configure.

    After creating the session, this function attaches to it (replacing
    the current process via ``os.execvp``).  It never returns on success.

    Critical environment variables (``OPENCODE_CONFIG``,
    ``OPENCODE_CONFIG_DIR``, ``ARCHITECT_*``) are explicitly forwarded
    into the tmux session because tmux does not inherit all parent
    env vars when creating new sessions.

    Args:
        session_name: The tmux session name to create.
        project_dir: The project root directory.
        argv: The full command-line arguments to run in the left pane.
        single_pane: When True, skip the dashboard split.  The runner
            command gets the whole window.  Used when the TUI owns
            rendering and the side panel would be redundant.

    Returns:
        False if session creation failed (so caller can fall back to
        running without tmux).  Never returns True — on success the
        process is replaced by tmux attach.
    """
    # Build the runner command (same argv as current process)
    # We add --no-monitor to prevent recursive tmux wrapping
    runner_cmd = _build_runner_cmd(argv)

    # Build the dashboard command.  Only needed in split-pane mode —
    # in single_pane mode the dashboard is skipped entirely so the
    # TUI can use the whole window.
    dashboard_cmd = "" if single_pane else _build_dashboard_cmd(project_dir)

    # Resolve the shell once — prefer bash, fall back to sh.
    shell = _get_portable_shell()
    if shell is None:
        logger.warning("No POSIX shell found (neither bash nor sh) — cannot launch tmux panes")
        return False
    logger.debug(f"Using shell for tmux pane commands: {shell}")

    try:
        # 1. Create a new detached session with the runner in the left pane
        #    Commands are passed through the portable shell so that shell
        #    quoting and multi-word commands are handled correctly.
        result = subprocess.run(
            [
                "tmux",
                "new-session",
                "-d",  # detached
                "-s",
                session_name,
                "-x",
                "220",  # initial width
                "-y",
                "50",  # initial height
                shell,
                "-c",
                runner_cmd,
            ],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.warning(
                f"tmux new-session failed (rc={result.returncode}): "
                f"{result.stderr.decode(errors='replace').strip()}"
            )
            return False

        # 1b. Forward critical env vars into the tmux session.
        #     tmux new-session does NOT inherit all parent env vars
        #     (especially custom ones like OPENCODE_CONFIG), so we must
        #     explicitly set them.  Using set-environment with -s applies
        #     to the session (inherited by all panes).
        _forward_env_vars(session_name)

        # 1c. Configure true-color terminal support for the session.
        #     tmux overrides TERM to its own default-terminal value
        #     (screen-256color) regardless of what the parent shell had.
        #     Without this, Textual detects a non-true-color terminal and
        #     renders the animated WaitScreen with near-invisible colours.
        _configure_terminal_colors(session_name)

        if not single_pane:
            # 2. Split the window vertically — right pane gets 30% width.
            #    Skipped in single_pane mode where the TUI owns the
            #    whole window; the dashboard would only overlap its
            #    already-rendered status info.
            result = subprocess.run(
                [
                    "tmux",
                    "split-window",
                    "-t",
                    session_name,
                    "-h",  # horizontal split (side by side)
                    "-p",
                    "30",  # right pane = 30% of width
                    shell,
                    "-c",
                    dashboard_cmd,
                ],
                capture_output=True,
                timeout=10,
            )
            if result.returncode != 0:
                logger.warning(
                    f"tmux split-window failed (rc={result.returncode}): "
                    f"{result.stderr.decode(errors='replace').strip()}"
                )
                # Session was created but split failed — kill it and fall back
                kill_session(session_name)
                return False

            # 3. Make pane borders invisible for a clean look (no visible lines
            #    between the left screen and the dashboard side panel).
            _configure_pane_borders(session_name)

            # 4. Select the left pane (pane 0) so the user sees the runner
            subprocess.run(
                ["tmux", "select-pane", "-t", f"{session_name}:0.0"],
                capture_output=True,
                timeout=5,
            )

        # 5. Attach to the session — this replaces the current process
        logger.info(f"Attaching to tmux session: {session_name}")
        attach_session(session_name)

        # Never reached on success
        return True  # pragma: no cover

    except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
        logger.warning(f"tmux launch failed: {exc!r}")
        # Clean up any partial session
        if session_exists(session_name):
            kill_session(session_name)
        return False


def _build_runner_cmd(argv: list[str]) -> str:
    """Build the shell command string for the runner pane.

    Adds ``--no-monitor`` to prevent recursive tmux wrapping.
    The command runs the same Python executable and module as the current
    process so it picks up the same virtualenv.

    Args:
        argv: The original sys.argv.

    Returns:
        Shell command string for tmux.
    """
    # Use the same Python executable / entry-point
    # argv[0] is the architect script path
    cmd_parts = [shlex_quote(arg) for arg in argv]

    # Insert --no-monitor after the script name (argv[0])
    # but before any subcommand or flags
    if "--no-monitor" not in argv:
        cmd_parts.insert(1, "--no-monitor")

    return " ".join(cmd_parts)


def _build_dashboard_cmd(project_dir: Path) -> str:
    """Build the shell command string for the dashboard pane.

    The dashboard is a Python module invoked directly.

    Args:
        project_dir: The project root directory.

    Returns:
        Shell command string for tmux.
    """
    python = shlex_quote(sys.executable)
    module = "the_architect.core.dashboard"
    proj = shlex_quote(str(project_dir))
    return f"{python} -m {module} {proj}"


def shlex_quote(s: str) -> str:
    """Quote a string for safe use in a shell command.

    Args:
        s: The string to quote.

    Returns:
        Shell-safe quoted string.
    """
    import shlex

    return shlex.quote(s)


# ---------------------------------------------------------------------------
# Environment variable forwarding for tmux sessions
# ---------------------------------------------------------------------------

# Env vars that must be forwarded into tmux sessions.
# tmux new-session does NOT inherit all parent env vars — custom ones
# like OPENCODE_CONFIG are dropped.  We explicitly set them so the
# left pane (runner) and right pane (dashboard) can find opencode
# and other tools the same way the parent process does.
#
# The list is intentionally broad: unset variables are silently skipped
# by _forward_env_vars(), so adding extras here is always safe.
_FORWARD_ENV_VARS: list[str] = [
    # opencode config — the most common ones users set
    "OPENCODE_CONFIG",
    "OPENCODE_CONFIG_DIR",
    # The Architect operational flags
    "ARCHITECT_ARCHITECT_MODEL",
    "ARCHITECT_EXECUTION_MODEL",
    "ARCHITECT_HEADLESS",
    "ARCHITECT_GOAL",
    "ARCHITECT_SCOPE",
    "ARCHITECT_CONTEXT",
    "ARCHITECT_LAUNCHED",
    "ARCHITECT_PROVIDER",  # selected provider name ("opencode" / "claude-code")
    # Provider API keys — opencode supports all of these
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "MISTRAL_API_KEY",
    "COHERE_API_KEY",
    "VERTEX_AI_PROJECT",
    "VERTEX_AI_LOCATION",
    # Shell / user environment — tmux commonly drops these in non-login sessions
    "PATH",
    "HOME",
    "SHELL",
    # Terminal capability vars — tmux creates panes with TERM=screen-256color
    # by default, which strips COLORTERM and TERM_PROGRAM entirely.  Without
    # these, Textual cannot detect true-color support and falls back to a
    # limited palette — causing the animated WaitScreen to render blank or
    # with near-invisible colours.  Forwarding the parent values preserves
    # whatever colour depth the user's outer terminal actually supports.
    "TERM",
    "COLORTERM",
    "TERM_PROGRAM",
    "TERM_PROGRAM_VERSION",
    # XDG base dirs — opencode and other tools use these for config discovery
    "XDG_CONFIG_HOME",
    # Node.js / npm — required when opencode was installed globally via npm/nvm
    "NODE_PATH",
    "npm_config_prefix",
    "NVM_DIR",
    "NVM_BIN",
    # Proxy configuration — required on corporate networks
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
]


def _forward_env_vars(session_name: str) -> None:
    """Forward critical environment variables into a tmux session.

    Uses ``tmux set-environment -s`` to set each variable at the session
    level so all panes inherit it.  Only variables that are actually set
    in the current process are forwarded — unset ones are skipped silently.

    The ``_FORWARD_ENV_VARS`` list is intentionally broad (provider keys,
    shell environment, Node.js/npm paths, Architect flags, proxy settings).
    Forwarding an unset variable is a no-op, so adding more entries to the
    list is always safe — it never causes errors on machines that do not
    use a particular variable.

    Errors are logged at debug level and silently swallowed — env
    forwarding is best-effort and must never crash the run.

    Args:
        session_name: The tmux session name to set env vars in.
    """
    for var_name in _FORWARD_ENV_VARS:
        value = os.environ.get(var_name)
        if value is None:
            continue
        try:
            subprocess.run(
                ["tmux", "set-environment", "-s", "-t", session_name, var_name, value],
                capture_output=True,
                timeout=5,
            )
            logger.debug(f"Forwarded env var to tmux session: {var_name}")
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
            logger.debug(f"Failed to forward env var {var_name} to tmux: {exc!r}")


def _configure_terminal_colors(session_name: str) -> None:
    """Set true-color terminal options on a newly-created tmux session.

    tmux always overrides the ``TERM`` env var for its panes to its own
    ``default-terminal`` value (``screen-256color`` unless the user has
    customised ``~/.tmux.conf``).  This strips ``COLORTERM=truecolor``
    and drops ``TERM`` back to 256-colour mode, which makes Textual
    fall back to a limited palette — causing the animated loading screen
    and other Rich/Textual colours to render incorrectly or invisibly.

    This function applies two session-level tmux options:

    1. ``default-terminal xterm-256color`` — makes every new pane start
       with a proper xterm-compatible TERM value.
    2. ``terminal-overrides *:Tc`` — enables the ``Tc`` capability flag
       (true-color / 24-bit RGB) for all terminals matching ``*``,
       so Textual's colour rendering is correct even inside tmux.

    Both options are applied with ``-s`` (session scope) and ``-t SESSION``
    so they only affect the session we just created, not the user's global
    tmux config.  Errors are logged at debug level and silently swallowed —
    color configuration is best-effort and must never abort the run.

    Args:
        session_name: The tmux session name to configure.
    """
    options: list[tuple[str, str]] = [
        ("default-terminal", "xterm-256color"),
        ("terminal-overrides", "*:Tc"),
    ]
    for option, value in options:
        try:
            subprocess.run(
                ["tmux", "set-option", "-s", "-t", session_name, option, value],
                capture_output=True,
                timeout=5,
            )
            logger.debug(f"Set tmux session option: {option}={value}")
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
            logger.debug(f"Failed to set tmux option {option}: {exc!r}")


def prompt_existing_session(session_name: str) -> str:
    """Prompt the user what to do when a session already exists.

    Shows a prompt with three options:
        [A] Attach — connect to the existing session
        [K] Kill and restart — stop the existing session and start fresh
        [R] Run without monitor — start without tmux

    Defaults to A after 10 seconds with no input.

    Args:
        session_name: The existing session name.

    Returns:
        One of "attach", "kill", or "run" based on user input.
    """
    import select
    import sys

    prompt = (
        f"\nA session for this project is already running ({session_name}).\n"
        "  [A] Attach — connect to the existing session\n"
        "  [K] Kill and restart — stop the existing session and start fresh\n"
        "  [R] Run without monitor — start without tmux\n"
        "\nDefaulting to [A] in 10 seconds... "
    )
    sys.stdout.write(prompt)
    sys.stdout.flush()

    # Try to read a keypress with a 10-second timeout
    # Only works on Unix-like systems where select() works on stdin
    try:
        if sys.stdin.isatty():
            try:
                import termios
                import tty
            except ImportError:
                # Not available on Windows — fall through to non-interactive default
                sys.stdout.write("\n")
                sys.stdout.flush()
                return "attach"

            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                ready, _, _ = select.select([sys.stdin], [], [], 10.0)
                if ready:
                    ch = sys.stdin.read(1).upper()
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    if ch == "K":
                        return "kill"
                    elif ch == "R":
                        return "run"
                    else:
                        return "attach"
                else:
                    # Timeout — default to attach
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return "attach"
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        else:
            # Non-interactive — default to attach
            sys.stdout.write("\n")
            sys.stdout.flush()
            return "attach"
    except Exception:
        sys.stdout.write("\n")
        sys.stdout.flush()
        return "attach"


# ---------------------------------------------------------------------------
# tmux auto-install
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# OS detection
# ---------------------------------------------------------------------------


def is_windows() -> bool:
    """Return True when running on native Windows (not WSL).

    tmux does not run on native Windows — it is only available inside WSL.
    We detect native Windows so we can skip the install prompt entirely
    rather than confusing the user with a Linux package manager command.

    Returns:
        True on native Windows (``sys.platform == "win32"``).
    """
    return sys.platform == "win32"


# ---------------------------------------------------------------------------
# Package manager install specs
# ---------------------------------------------------------------------------

# Each entry: (manager_binary, update_cmd_or_None, install_cmd, needs_sudo)
#
# update_cmd is run first when not None.  apt/apt-get require a cache
# refresh or the package list may be stale ("no installation candidate").
# brew, dnf, yum resolve dependencies on-the-fly so no update step needed.
#
# needs_sudo=True means we try ``sudo -n <cmd>`` first (passwordless, works
# in most devcontainers and CI) then fall back to plain ``sudo <cmd>``.
# brew and nix-env manage their own permissions so needs_sudo=False.
_INSTALL_METHODS: list[tuple[str, list[str] | None, list[str], bool]] = [
    # macOS / Linux — Homebrew (no sudo, manages its own prefix)
    ("brew", None, ["brew", "install", "tmux"], False),
    # Debian / Ubuntu / devcontainers
    ("apt-get", ["apt-get", "update", "-qq"], ["apt-get", "install", "-y", "tmux"], True),
    ("apt", ["apt", "update", "-qq"], ["apt", "install", "-y", "tmux"], True),
    # Arch / Manjaro
    ("pacman", ["pacman", "-Sy", "--noconfirm"], ["pacman", "-S", "--noconfirm", "tmux"], True),
    # Fedora / RHEL 8+ / Rocky / Alma
    ("dnf", None, ["dnf", "install", "-y", "tmux"], True),
    # CentOS / RHEL 7
    ("yum", None, ["yum", "install", "-y", "tmux"], True),
    # openSUSE
    ("zypper", ["zypper", "refresh"], ["zypper", "install", "-y", "tmux"], True),
    # Alpine (common in Docker images)
    ("apk", ["apk", "update"], ["apk", "add", "tmux"], True),
    # NixOS / nix-env (declarative — user manages their own profile)
    ("nix-env", None, ["nix-env", "-iA", "nixpkgs.tmux"], False),
    # macOS — MacPorts (less common, needs sudo)
    ("port", ["port", "selfupdate"], ["port", "install", "tmux"], True),
    # Windows — Chocolatey (runs inside PowerShell / cmd, elevated)
    ("choco", None, ["choco", "install", "tmux", "-y"], False),
    # Windows — winget
    ("winget", None, ["winget", "install", "tmux"], False),
    # Windows — Scoop (user-level, no elevation needed)
    ("scoop", None, ["scoop", "install", "tmux"], False),
]


def detect_install_method() -> tuple[list[str] | None, list[str], bool] | None:
    """Detect the available system package manager for installing tmux.

    Skips the search entirely on native Windows since tmux is not
    available there (WSL is detected as Linux so it works normally).

    Returns:
        Tuple of ``(update_cmd_or_None, install_cmd, needs_sudo)`` for the
        first detected package manager, or None if none is found.
    """
    if is_windows():
        # tmux does not run on native Windows — skip silently
        return None
    for binary, update_cmd, install_cmd, needs_sudo in _INSTALL_METHODS:
        if shutil.which(binary):
            return update_cmd, install_cmd, needs_sudo
    return None


def _install_hint() -> str:
    """Return a human-readable hint showing every command that will run.

    When a cache-refresh step is required (e.g. ``apt-get update``) it is
    shown first so the user sees the full picture before confirming.

    Returns:
        A string like ``"brew install tmux"`` or
        ``"sudo apt-get update -qq && sudo apt-get install -y tmux"``,
        or a generic message when no package manager is detected.
    """
    method = detect_install_method()
    if method is None:
        if is_windows():
            return "install tmux inside WSL, or use Windows Subsystem for Linux"
        return "install tmux via your system package manager"
    update_cmd, install_cmd, needs_sudo = method
    prefix = "sudo " if needs_sudo else ""
    install_str = prefix + " ".join(install_cmd)
    if update_cmd is not None:
        update_str = prefix + " ".join(update_cmd)
        return f"{update_str} && {install_str}"
    return install_str


def try_install_tmux() -> bool:
    """Attempt to install tmux using the detected system package manager.

    For package managers that require a cache refresh (apt, apk, zypper,
    pacman) the update command is run first.  This prevents the common
    "Package tmux is not available" error caused by a stale package list.

    If ``sudo`` is needed, tries ``sudo -n`` first (passwordless) then
    falls back to plain ``sudo`` which may prompt for a password.

    Returns:
        True if tmux is now available after the install attempt.
        False if the install failed or no package manager was found.
    """
    method = detect_install_method()
    if method is None:
        return False

    update_cmd, install_cmd, needs_sudo = method

    def _run(command: list[str]) -> bool:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=120,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            return False

    def _run_maybe_sudo(command: list[str]) -> bool:
        if needs_sudo:
            # Try passwordless sudo first (works in most CI / devcontainer setups)
            ok = _run(["sudo", "-n"] + command)
            if not ok:
                ok = _run(["sudo"] + command)
            return ok
        return _run(command)

    # Refresh package cache first when required (apt, apk, etc.)
    if update_cmd is not None:
        _run_maybe_sudo(update_cmd)  # best-effort — ignore failure

    ok = _run_maybe_sudo(install_cmd)

    if ok:
        return shutil.which("tmux") is not None

    return False


def prompt_install_tmux() -> bool:
    """Ask the user whether to install tmux now.

    Shows a single yes/no prompt.  Defaults to yes after 10 seconds.
    In non-interactive environments, defaults to yes silently.

    Returns:
        True if the user said yes (or timed out), False if they said no.
    """
    import select

    msg = (
        "\n  tmux is not installed — The Architect uses it for a split-pane\n"
        "  dashboard (live output + task list side by side).\n\n"
        f"  Install now?  [{_install_hint()}]  [Y/n] "
    )
    sys.stdout.write(msg)
    sys.stdout.flush()

    try:
        if sys.stdin.isatty():
            try:
                import termios
                import tty
            except ImportError:
                # Not available on Windows — fall through to non-interactive default
                sys.stdout.write("\n")
                sys.stdout.flush()
                return True

            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                ready, _, _ = select.select([sys.stdin], [], [], 10.0)
                if ready:
                    ch = sys.stdin.read(1)
                    sys.stdout.write(ch + "\n")
                    sys.stdout.flush()
                    return ch.lower() != "n"
                else:
                    # Timed out — default yes
                    sys.stdout.write("y\n")
                    sys.stdout.flush()
                    return True
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        else:
            # Non-interactive — default yes
            sys.stdout.write("y\n")
            sys.stdout.flush()
            return True
    except Exception:
        sys.stdout.write("\n")
        sys.stdout.flush()
        return True


def maybe_launch_tmux(
    project_dir: Path,
    argv: list[str],
    no_monitor: bool = False,
    single_pane: bool = False,
) -> bool:
    """Check tmux availability and auto-launch if appropriate.

    Called at the very start of ``_run_main``.  Returns True if tmux was
    launched (meaning the current process should exit — tmux has taken
    over).  Returns False if the caller should continue running normally.

    Decision tree:
        1. ``no_monitor=True`` → return False (skip all tmux logic)
        2. tmux not installed → log once, return False
        3. Already inside tmux → return False (no nesting)
        4. Session exists → prompt user (attach / kill+restart / run)
        5. Session does not exist → create session, attach, return True

    Args:
        project_dir: The project root directory.
        argv: The current sys.argv (used to reconstruct the runner command).
        no_monitor: If True, skip all tmux logic.
        single_pane: If True, wrap the runner in tmux WITHOUT the
            dashboard split — the runner gets the full window.  Used
            when the TUI is active: wrapping in tmux still gives the
            user ``Ctrl+B D`` detach + ``tmux attach`` reattach, but
            without the side panel that would otherwise compete with
            the TUI's own Progress/Diagnostics tabs for screen space.

    Returns:
        True if tmux was launched and the caller should exit.
        False if the caller should continue running normally.
    """
    global _tmux_install_attempted

    if no_monitor:
        logger.debug("--no-monitor flag set — skipping tmux")
        return False

    # tmux does not run on native Windows — skip entirely.
    # WSL reports sys.platform == "linux" so it works normally.
    if is_windows():
        logger.debug("Native Windows detected — tmux not supported, skipping")
        return False

    if not is_tmux_available():
        if not _tmux_install_attempted:
            _tmux_install_attempted = True
            # Only offer install when running interactively
            if sys.stdout.isatty() and sys.stdin.isatty():
                wants_install = prompt_install_tmux()
                if wants_install:
                    sys.stdout.write("  Installing tmux...")
                    sys.stdout.flush()
                    ok = try_install_tmux()
                    if ok:
                        sys.stdout.write(" done.\n\n")
                        sys.stdout.flush()
                        # tmux is now available — fall through to launch it
                    else:
                        sys.stdout.write(" failed.\n")
                        sys.stdout.write(f"  Run manually:  {_install_hint()}\n\n")
                        sys.stdout.flush()
                        return False
                else:
                    sys.stdout.write("\n  Running without tmux dashboard.\n\n")
                    sys.stdout.flush()
                    return False
            else:
                logger.debug("tmux not found and non-interactive — skipping install prompt")
                return False

        if not is_tmux_available():
            # Install was attempted but failed or skipped
            return False

    if is_inside_tmux():
        logger.debug("Already inside tmux — skipping auto-launch to avoid nesting")
        return False

    # Skip tmux in non-interactive piped/CI contexts where there is genuinely
    # no terminal at all (e.g. Click CliRunner, GitHub Actions, cron jobs).
    # We check stderr rather than stdout because some environments (VS Code
    # integrated terminal, devcontainers) pipe stdout but still have a
    # controlling terminal accessible via stderr / the process group.
    # We also skip when neither stdout nor stderr is a TTY AND /dev/tty is
    # unavailable — that combination reliably means "no terminal at all".
    _has_tty = sys.stdout.isatty() or sys.stderr.isatty()
    if not _has_tty and sys.platform != "win32":
        # /dev/tty is a Linux/macOS path — never attempt on Windows where it
        # does not exist and would raise FileNotFoundError unconditionally.
        # The except OSError is still needed for Linux/macOS cases where
        # /dev/tty exists but is unavailable (daemon processes, Docker without
        # a controlling terminal).
        try:
            import os as _os

            fd = _os.open("/dev/tty", _os.O_RDWR)
            _os.close(fd)
            _has_tty = True
        except OSError:
            pass

    if not _has_tty:
        logger.debug("No terminal detected — skipping tmux launch")
        return False

    session_name = get_session_name(project_dir)

    if session_exists(session_name):
        choice = prompt_existing_session(session_name)
        if choice == "attach":
            attach_session(session_name)
            return True  # pragma: no cover (attach replaces process)
        elif choice == "kill":
            kill_session(session_name)
            # Fall through to create a new session below
        else:
            # "run" — continue without tmux
            return False

    # Launch in a new tmux session
    launched = launch_in_tmux(session_name, project_dir, argv, single_pane=single_pane)
    if launched:
        return True  # pragma: no cover (attach replaces process)

    # tmux launch failed — fall back to running without monitoring
    logger.warning("tmux launch failed — running without live monitoring")
    return False


# ---------------------------------------------------------------------------
# Own-window launch (fallback when tmux is not available)
# ---------------------------------------------------------------------------


# Terminal emulators on Linux, in order of preference.
# Each entry is (command_name, launch_prefix) where launch_prefix is the
# arguments before the actual command.
_LINUX_TERMINALS: list[tuple[str, list[str]]] = [
    ("gnome-terminal", ["gnome-terminal", "--"]),
    ("konsole", ["konsole", "-e"]),
    ("kitty", ["kitty"]),
    ("alacritty", ["alacritty", "-e"]),
    ("xfce4-terminal", ["xfce4-terminal", "-e"]),
    ("mate-terminal", ["mate-terminal", "-e"]),
    ("xterm", ["xterm", "-e"]),
]

# Env var set when architect is launched in a new window/session.
# Prevents recursive window launching.
_ARCHITECT_LAUNCHED = "ARCHITECT_LAUNCHED"


def is_gui_available() -> bool:
    """Check whether a graphical environment is available.

    On macOS and Windows a GUI is always available.  On Linux we check
    for the ``DISPLAY`` (X11) or ``WAYLAND_DISPLAY`` environment variables.

    Returns:
        True if a GUI terminal window can likely be opened.
    """
    if sys.platform == "darwin":
        return True
    if sys.platform == "win32":
        return True
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return True
    return False


def detect_terminal_emulator() -> str | None:
    """Detect the terminal emulator available on the system.

    Returns:
        Terminal emulator identifier string (e.g. ``"gnome-terminal"``,
        ``"macos-terminal"``, ``"windows-terminal"``), or None if none found.
    """
    if sys.platform == "darwin":
        # macOS — Terminal.app is always available.
        # Check for iTerm2 first (better tab/colour support).
        # Apps may be installed system-wide (/Applications/) or per-user
        # (~/Applications/) — check both locations.
        # Only iTerm2 requires an existence check; all other macOS terminals
        # are detected via shutil.which() or are always present (Terminal.app).
        _iterm_paths = [
            Path("/Applications/iTerm.app"),
            Path.home() / "Applications" / "iTerm.app",
        ]
        if any(p.exists() for p in _iterm_paths):
            return "iterm"
        return "macos-terminal"

    if sys.platform == "win32":
        # Windows — try Windows Terminal first, then cmd.exe fallback.
        if shutil.which("wt.exe") or shutil.which("wt"):
            return "windows-terminal"
        # cmd.exe / powershell are always present on Windows.
        return "windows-cmd"

    # Linux — check common terminal emulators in order of preference.
    for name, _prefix in _LINUX_TERMINALS:
        if shutil.which(name):
            return name

    # Try freedesktop standard terminal launcher
    if shutil.which("xdg-terminal-exec"):
        return "xdg-terminal-exec"

    return None


def _build_window_command(argv: list[str], terminal: str) -> list[str] | None:
    """Build the subprocess command to open The Architect in a new terminal window.

    The launched command includes ``--no-monitor`` to prevent tmux from
    trying to launch inside the new window.  A shell wrapper keeps the
    window open on error so the user can read the message.

    Args:
        argv: The original sys.argv.
        terminal: Detected terminal emulator identifier.

    Returns:
        Command list for ``subprocess.Popen``, or None if the terminal
        is not supported.
    """
    # Build the architect command, adding --no-monitor to prevent tmux
    # recursion inside the new window.
    architect_cmd = list(argv)
    if "--no-monitor" not in architect_cmd:
        architect_cmd.insert(1, "--no-monitor")

    # Wrap in a shell command that keeps the window open on error
    shell_cmd = " ".join(shlex_quote(a) for a in architect_cmd)
    shell_cmd = (
        f"{shell_cmd}; _ec=$?; "
        f'if [ $_ec -ne 0 ]; then echo; echo "The Architect exited with error code $_ec."; '
        f'echo "Press Enter to close this window..."; read; fi'
    )

    if sys.platform == "darwin":
        # macOS — use AppleScript to open Terminal.app / iTerm2.
        # Escape backslashes and double-quotes for AppleScript string literal.
        escaped = shell_cmd.replace("\\", "\\\\").replace('"', '\\"')
        if terminal == "iterm":
            return [
                "osascript",
                "-e",
                'tell application "iTerm" to create window '
                f'with default profile command "{escaped}"',
            ]
        else:
            return [
                "osascript",
                "-e",
                f'tell application "Terminal" to do script "{escaped}"',
            ]

    if sys.platform == "win32":
        # Build the architect command string for use in a Windows shell.
        # We use subprocess.list2cmdline to properly quote the arguments.
        import subprocess as _sp

        win_cmd = _sp.list2cmdline(architect_cmd)
        if terminal == "windows-terminal":
            # Windows Terminal: wt.exe new-tab <command>
            return ["wt.exe", "new-tab", "cmd.exe", "/k", win_cmd]
        else:
            # Fallback: cmd.exe /c start — opens in a new console window.
            # /wait is omitted so the original terminal is freed immediately.
            return ["cmd.exe", "/c", "start", "cmd.exe", "/k", win_cmd]

    # Linux terminal emulators — resolve shell portably; None means no launch.
    sh = _get_portable_shell()
    if sh is None:
        logger.debug("No POSIX shell found — cannot build terminal window command")
        return None

    if terminal == "gnome-terminal":
        return ["gnome-terminal", "--", sh, "-c", shell_cmd]
    elif terminal == "konsole":
        return ["konsole", "-e", sh, "-c", shell_cmd]
    elif terminal == "kitty":
        return ["kitty", sh, "-c", shell_cmd]
    elif terminal == "alacritty":
        return ["alacritty", "-e", sh, "-c", shell_cmd]
    elif terminal in ("xfce4-terminal", "mate-terminal"):
        return [terminal, "-e", f"{shlex_quote(sh)} -c {shlex_quote(shell_cmd)}"]
    elif terminal == "xterm":
        return ["xterm", "-e", sh, "-c", shell_cmd]
    elif terminal == "xdg-terminal-exec":
        return ["xdg-terminal-exec", sh, "-c", shell_cmd]

    return None


def maybe_launch_own_window(
    project_dir: Path,
    argv: list[str],
    no_monitor: bool = False,
) -> bool:
    """Try to open The Architect in its own terminal window.

    Used as a fallback when tmux is not available.  Gives the user an
    "own window" experience where the architect runs in a separate
    terminal and the user's original terminal is immediately free.

    The ``ARCHITECT_LAUNCHED`` environment variable is set in the new
    window to prevent recursive window / tmux launching.

    Never crashes the run — all operations are wrapped in try/except
    and fall back to running in the current terminal.

    Args:
        project_dir: The project root directory.
        argv: The current sys.argv.
        no_monitor: If True, skip (used when already in a launched context).

    Returns:
        True if a new window was launched and the caller should exit.
        False if the caller should continue running normally.
    """
    # Don't launch if already in a launched window
    if os.environ.get(_ARCHITECT_LAUNCHED):
        logger.debug("Already in a launched window — skipping own-window launch")
        return False

    # Don't launch in non-interactive / headless contexts
    if not sys.stdout.isatty():
        return False

    if no_monitor:
        return False

    if not is_gui_available():
        logger.debug("No GUI available — skipping own-window launch")
        return False

    terminal = detect_terminal_emulator()
    if not terminal:
        logger.debug("No terminal emulator detected — skipping own-window launch")
        return False

    launch_cmd = _build_window_command(argv, terminal)
    if launch_cmd is None:
        return False

    try:
        env = os.environ.copy()
        env[_ARCHITECT_LAUNCHED] = "1"

        proc = subprocess.Popen(
            launch_cmd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        # Brief pause to let the terminal window start up.
        # If the process dies immediately, the launch likely failed.
        import time

        time.sleep(0.3)

        poll_result = proc.poll()
        if poll_result is None or poll_result == 0:
            logger.info(f"Launched The Architect in a new {terminal} window")
            return True
        else:
            logger.debug(f"Terminal window process exited immediately (rc={poll_result})")
            return False

    except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
        logger.debug(f"Terminal window launch failed: {exc!r}")
        return False


def print_reattach_hint(project_dir: Path) -> None:
    """Print the reattach command to stdout.

    Called when the user detaches from the tmux session so they know
    how to reconnect.

    Args:
        project_dir: The project root directory.
    """
    from rich.console import Console

    session_name = get_session_name(project_dir)
    console = Console()
    console.print(f"\n[dim]Session running as:[/dim] {session_name}")
    console.print("[dim]Reattach with:[/dim] architect monitor")
