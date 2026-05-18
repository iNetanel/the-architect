"""Workspace context detection for The Architect.

Detects the current git workspace state (branch, uncommitted changes, staged
files, recent commits) so the planner can make smarter decomposition decisions
based on existing work-in-progress, feature branches, and interrupted runs.

Detection always runs fresh — never cached.  Non-git projects and git errors
are handled gracefully (partial data, no exceptions).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Subprocess timeout in seconds — prevents hanging on large or corrupted repos.
_GIT_TIMEOUT: int = 5

# Maximum output bytes before truncation — prevents memory pressure on huge repos.
_GIT_MAX_OUTPUT: int = 64 * 1024  # 64 KB

# Number of recent commits to include in context.
_RECENT_COMMITS_COUNT: int = 5


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class WorkspaceContext(BaseModel):
    """Detected workspace state for planning context injection.

    Attributes:
        is_git: Whether the project directory is a git repository.
        branch: Current branch name, or None if not a git repo.
        uncommitted_count: Number of untracked or unstaged modified files.
        staged_count: Number of staged (indexed) files.
        recent_commits: Up to 5 recent commits as dicts with ``hash`` and ``message`` keys.
    """

    is_git: bool = False
    branch: str | None = None
    uncommitted_count: int = 0
    staged_count: int = 0
    recent_commits: list[dict[str, str]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Git subprocess helpers
# ---------------------------------------------------------------------------


def _run_git(
    project_dir: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    """Run a git command with safety bounds.

    Args:
        project_dir: The project root (used as working directory).
        *args: Git sub-command and flags.

    Returns:
        CompletedProcess with text output.

    Raises:
        subprocess.TimeoutExpired: If the command exceeds ``_GIT_TIMEOUT``.
        subprocess.SubprocessError: On other subprocess failures.
        FileNotFoundError: If ``git`` is not on ``PATH``.
    """
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
        cwd=str(project_dir),
    )


def _safe_run_git(
    project_dir: Path,
    *args: str,
) -> str | None:
    """Run a git command and return stdout, or None on any failure.

    All exceptions (timeout, permission denied, not a repo, git not found)
    are caught and logged as debug-level messages.

    Args:
        project_dir: The project root.
        *args: Git sub-command and flags.

    Returns:
        Standard output string, or None if the command failed.
    """
    try:
        result = _run_git(project_dir, *args)
        if result.returncode != 0:
            logger.debug(
                "git command failed (exit {}): {}",
                result.returncode,
                result.stderr.strip()[:200],
            )
            return None
        output = result.stdout
        if len(output.encode("utf-8")) > _GIT_MAX_OUTPUT:
            logger.debug("git output truncated to {} bytes", _GIT_MAX_OUTPUT)
            output = output.encode("utf-8")[:_GIT_MAX_OUTPUT].decode("utf-8", errors="replace")
        return output
    except subprocess.TimeoutExpired:
        logger.debug("git command timed out after {} seconds", _GIT_TIMEOUT)
        return None
    except subprocess.SubprocessError as exc:
        logger.debug("git subprocess error: {}", exc)
        return None
    except FileNotFoundError:
        logger.debug("git executable not found on PATH")
        return None


# ---------------------------------------------------------------------------
# Detection functions
# ---------------------------------------------------------------------------


def _is_git_repo(project_dir: Path) -> bool:
    """Check whether *project_dir* is inside a git repository.

    Args:
        project_dir: The directory to check.

    Returns:
        True if ``.git`` exists or ``git rev-parse --git-dir`` succeeds.
    """
    if (project_dir / ".git").exists():
        return True
    output = _safe_run_git(project_dir, "rev-parse", "--git-dir")
    return output is not None


def _get_branch(project_dir: Path) -> str | None:
    """Return the current branch name.

    Args:
        project_dir: The project root.

    Returns:
        Branch name string, or None if detached HEAD, not a repo, or on error.
    """
    output = _safe_run_git(project_dir, "branch", "--show-current")
    if output:
        branch = output.strip()
        if branch:
            return branch
    return None


def _get_status_counts(project_dir: Path) -> tuple[int, int]:
    """Count uncommitted and staged files via ``git status --porcelain``.

    The porcelain format uses a two-character prefix per line:
    - First character: staging area (A/M/D/R/C/?)
    - Second character: working tree (A/M/D/R/C/? or space)
    - Lines starting with ``?`` are untracked (uncommitted).
    - Lines starting with a letter (not ``?``) are staged or modified tracked files.

    Args:
        project_dir: The project root.

    Returns:
        Tuple of (uncommitted_count, staged_count).
    """
    uncommitted = 0
    staged = 0

    output = _safe_run_git(project_dir, "status", "--porcelain")
    if not output:
        return 0, 0

    for line in output.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("?"):
            uncommitted += 1
        else:
            staged += 1

    return uncommitted, staged


def _get_recent_commits(project_dir: Path) -> list[dict[str, str]]:
    """Return the most recent commits as a list of ``{hash, message}`` dicts.

    Args:
        project_dir: The project root.

    Returns:
        List of dicts, each with ``hash`` (short SHA) and ``message`` keys.
    """
    commits: list[dict[str, str]] = []

    output = _safe_run_git(
        project_dir,
        "log",
        "--oneline",
        "-n",
        str(_RECENT_COMMITS_COUNT),
        "--format=%H %s",
    )
    if not output:
        return commits

    for line in output.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2:
            full_hash, message = parts
            short_hash = full_hash[:7]
            commits.append({"hash": short_hash, "message": message})

    return commits


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def gather_workspace_context(project_dir: Path) -> WorkspaceContext:
    """Detect workspace state for planning context injection.

    Runs git commands to determine branch, uncommitted/staged file counts,
    and recent commit history.  Non-git directories and git errors are handled
    gracefully — the returned model will have ``is_git=False`` with zeroed
    fields rather than raising exceptions.

    Args:
        project_dir: The project root directory.

    Returns:
        A ``WorkspaceContext`` with detected git state.  For non-git projects,
        returns ``is_git=False`` with empty/zero fields.
    """
    if not project_dir.is_dir():
        logger.debug("project_dir is not a directory: {}", project_dir)
        return WorkspaceContext()

    is_git = _is_git_repo(project_dir)
    if not is_git:
        logger.debug("not a git repository: {}", project_dir)
        return WorkspaceContext(is_git=False)

    branch = _get_branch(project_dir)
    uncommitted, staged = _get_status_counts(project_dir)
    recent = _get_recent_commits(project_dir)

    ctx = WorkspaceContext(
        is_git=True,
        branch=branch,
        uncommitted_count=uncommitted,
        staged_count=staged,
        recent_commits=recent,
    )

    logger.debug(
        "workspace context: branch={}, uncommitted={}, staged={}, commits={}",
        branch,
        uncommitted,
        staged,
        len(recent),
    )

    return ctx


def format_workspace_context(ctx: WorkspaceContext) -> str:
    """Format a ``WorkspaceContext`` into a planning-instruction section.

    Produces a markdown-style block suitable for injection into the architect
    agent's planning prompt under the ``=== WORKSPACE STATE ===`` header.

    For non-git repos, returns an empty string (silence when no info).

    Args:
        ctx: The workspace context to format.

    Returns:
        Formatted string, or empty string if not a git repo.
    """
    if not ctx.is_git:
        return ""

    lines: list[str] = []

    if ctx.branch:
        lines.append(f"Current branch: {ctx.branch}")

    if ctx.uncommitted_count > 0:
        lines.append(
            f"Uncommitted changes: {ctx.uncommitted_count} file(s) "
            "(untracked or unstaged modifications)"
        )
    if ctx.staged_count > 0:
        lines.append(f"Staged changes: {ctx.staged_count} file(s) (in index)")

    if ctx.recent_commits:
        lines.append("Recent commits:")
        for commit in ctx.recent_commits:
            lines.append(f"  - {commit['hash']}: {commit['message']}")

    return "\n".join(lines)
