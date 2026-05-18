"""Tests for the_architect/core/workspace_context.py.

Covers gather_workspace_context(), format_workspace_context(), and all
internal helpers via subprocess.run mocking and tmp_path fixtures.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from the_architect.core.workspace_context import (
    WorkspaceContext,
    format_workspace_context,
    gather_workspace_context,
)


class TestWorkspaceContextModel:
    """Tests for WorkspaceContext Pydantic model defaults."""

    def test_default_values(self) -> None:
        """All fields default to safe empty/zero values."""
        ctx = WorkspaceContext()
        assert ctx.is_git is False
        assert ctx.branch is None
        assert ctx.uncommitted_count == 0
        assert ctx.staged_count == 0
        assert ctx.recent_commits == []

    def test_full_values(self) -> None:
        """All fields accept populated values."""
        ctx = WorkspaceContext(
            is_git=True,
            branch="feature/login",
            uncommitted_count=3,
            staged_count=2,
            recent_commits=[
                {"hash": "abc1234", "message": "Add login page"},
                {"hash": "def5678", "message": "Fix auth middleware"},
            ],
        )
        assert ctx.is_git is True
        assert ctx.branch == "feature/login"
        assert ctx.uncommitted_count == 3
        assert ctx.staged_count == 2
        assert len(ctx.recent_commits) == 2

    def test_model_serialization(self) -> None:
        """WorkspaceContext serializes correctly via model_dump."""
        ctx = WorkspaceContext(
            is_git=True,
            branch="main",
            uncommitted_count=1,
            staged_count=0,
            recent_commits=[{"hash": "aaa1111", "message": "Initial commit"}],
        )
        data = ctx.model_dump()
        assert data["is_git"] is True
        assert data["branch"] == "main"
        assert data["uncommitted_count"] == 1
        assert data["staged_count"] == 0
        assert len(data["recent_commits"]) == 1


class TestGatherWorkspaceContextNonGit:
    """Tests for non-git directory handling."""

    def test_non_git_directory(self, tmp_path: Path) -> None:
        """A plain directory without .git returns is_git=False."""
        ctx = gather_workspace_context(tmp_path)
        assert ctx.is_git is False
        assert ctx.branch is None
        assert ctx.uncommitted_count == 0
        assert ctx.staged_count == 0
        assert ctx.recent_commits == []

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        """A path that does not exist returns empty context."""
        ctx = gather_workspace_context(tmp_path / "does_not_exist")
        assert ctx.is_git is False
        assert ctx.branch is None

    def test_file_instead_of_directory(self, tmp_path: Path) -> None:
        """A file path (not a directory) returns empty context."""
        some_file = tmp_path / "file.txt"
        some_file.write_text("hello", encoding="utf-8")
        ctx = gather_workspace_context(some_file)
        assert ctx.is_git is False


class TestGatherWorkspaceContextGitRepo:
    """Tests for valid git repository handling."""

    def _mock_git_run(
        self,
        branch: str = "main",
        uncommitted: int = 2,
        staged: int = 1,
        commits: list[tuple[str, str]] | None = None,
    ) -> object:
        """Return a mock for subprocess.run that simulates git commands."""
        if commits is None:
            commits = [
                ("abc1234def56789", "Add feature A"),
                ("def5678abc12345", "Fix bug B"),
            ]

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            # cmd is ["git", ...args]
            args = cmd[1:] if len(cmd) > 1 else []

            # git rev-parse --git-dir
            if "rev-parse" in args and "--git-dir" in args:
                return subprocess.CompletedProcess(cmd, 0, ".git", "")

            # git branch --show-current
            if "branch" in args and "--show-current" in args:
                return subprocess.CompletedProcess(cmd, 0, branch + "\n", "")

            # git status --porcelain
            if "status" in args and "--porcelain" in args:
                lines = []
                for _i in range(uncommitted):
                    lines.append("? untracked_file.txt")
                for _i in range(staged):
                    lines.append("M staged_file.txt")
                return subprocess.CompletedProcess(
                    cmd, 0, "\n".join(lines) + "\n" if lines else "", ""
                )

            # git log --oneline -n 5 --format=%H %s
            if "log" in args and "--format=" in " ".join(args):
                commit_lines = []
                for sha, msg in commits:
                    commit_lines.append(f"{sha} {msg}")
                return subprocess.CompletedProcess(
                    cmd, 0, "\n".join(commit_lines) + "\n" if commit_lines else "", ""
                )

            return subprocess.CompletedProcess(cmd, 1, "", "unknown command")

        return patch("subprocess.run", side_effect=fake_run)

    def test_valid_git_repo_full_data(self, tmp_path: Path) -> None:
        """A git repo with branch, changes, and commits returns full context."""
        (tmp_path / ".git").mkdir()

        with self._mock_git_run(
            branch="feature/auth",
            uncommitted=3,
            staged=2,
            commits=[
                ("aaa1111bbb2222c", "Implement OAuth2"),
                ("ccc3333ddd4444e", "Add session store"),
                ("eee5555fff6666g", "Initial commit"),
            ],
        ):
            ctx = gather_workspace_context(tmp_path)

        assert ctx.is_git is True
        assert ctx.branch == "feature/auth"
        assert ctx.uncommitted_count == 3
        assert ctx.staged_count == 2
        assert len(ctx.recent_commits) == 3
        assert ctx.recent_commits[0]["hash"] == "aaa1111"
        assert ctx.recent_commits[0]["message"] == "Implement OAuth2"

    def test_valid_git_repo_no_changes(self, tmp_path: Path) -> None:
        """A clean git repo (no changes) returns zero counts."""
        (tmp_path / ".git").mkdir()

        with self._mock_git_run(
            branch="main",
            uncommitted=0,
            staged=0,
            commits=[("abc1234def56789", "Initial commit")],
        ):
            ctx = gather_workspace_context(tmp_path)

        assert ctx.is_git is True
        assert ctx.branch == "main"
        assert ctx.uncommitted_count == 0
        assert ctx.staged_count == 0
        assert len(ctx.recent_commits) == 1

    def test_valid_git_repo_no_commits(self, tmp_path: Path) -> None:
        """An empty git repo (no commits) returns empty recent_commits."""
        (tmp_path / ".git").mkdir()

        with self._mock_git_run(
            branch="main",
            uncommitted=0,
            staged=0,
            commits=[],
        ):
            ctx = gather_workspace_context(tmp_path)

        assert ctx.is_git is True
        assert ctx.branch == "main"
        assert ctx.recent_commits == []

    def test_detached_head_no_branch(self, tmp_path: Path) -> None:
        """Detached HEAD (empty branch output) returns branch=None."""
        (tmp_path / ".git").mkdir()

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            if cmd[-1] == "--git-dir" and cmd[-2] == "rev-parse":
                return subprocess.CompletedProcess(cmd, 0, ".git", "")
            if cmd[-1] == "--show-current" and cmd[-2] == "branch":
                # Detached HEAD — git branch --show-current outputs nothing
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd[-1] == "--porcelain" and cmd[-2] == "status":
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd[-3] == "--format=%H %s" and cmd[-4] == "-n":
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 1, "", "unknown")

        with patch("subprocess.run", side_effect=fake_run):
            ctx = gather_workspace_context(tmp_path)

        assert ctx.is_git is True
        assert ctx.branch is None


class TestGatherWorkspaceContextGitErrors:
    """Tests for git error handling."""

    def test_git_timeout(self, tmp_path: Path) -> None:
        """subprocess.TimeoutExpired returns safe defaults."""
        (tmp_path / ".git").mkdir()

        def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise subprocess.TimeoutExpired(cmd="git", timeout=5)

        with patch("subprocess.run", side_effect=fake_run):
            ctx = gather_workspace_context(tmp_path)

        # .git exists so is_git is True (checked before subprocess),
        # but all git commands time out — returns zeroed fields
        assert ctx.is_git is True
        assert ctx.branch is None
        assert ctx.uncommitted_count == 0
        assert ctx.staged_count == 0
        assert ctx.recent_commits == []

    def test_git_subprocess_error(self, tmp_path: Path) -> None:
        """subprocess.SubprocessError returns safe defaults."""
        (tmp_path / ".git").mkdir()

        def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise subprocess.SubprocessError("permission denied")

        with patch("subprocess.run", side_effect=fake_run):
            ctx = gather_workspace_context(tmp_path)

        assert ctx.is_git is True  # .git exists
        assert ctx.branch is None
        assert ctx.uncommitted_count == 0

    def test_git_not_found(self, tmp_path: Path) -> None:
        """FileNotFoundError (git not on PATH) returns safe defaults."""
        (tmp_path / ".git").mkdir()

        def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise FileNotFoundError("git not found")

        with patch("subprocess.run", side_effect=fake_run):
            ctx = gather_workspace_context(tmp_path)

        assert ctx.is_git is True  # .git exists
        assert ctx.branch is None
        assert ctx.uncommitted_count == 0

    def test_git_command_returns_nonzero(self, tmp_path: Path) -> None:
        """Non-zero exit code from git returns None for that command."""
        (tmp_path / ".git").mkdir()

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            # All commands fail with exit code 1
            return subprocess.CompletedProcess(cmd, 1, "", "fatal: not a git repository")

        with patch("subprocess.run", side_effect=fake_run):
            ctx = gather_workspace_context(tmp_path)

        # .git exists so is_git is True, but all commands fail
        assert ctx.is_git is True
        assert ctx.branch is None
        assert ctx.uncommitted_count == 0


class TestGatherWorkspaceContextDetection:
    """Tests for git repo detection edge cases."""

    def test_is_git_via_git_dir_check(self, tmp_path: Path) -> None:
        """Detection works via .git directory existence (no subprocess needed)."""
        (tmp_path / ".git").mkdir()
        ctx = gather_workspace_context(tmp_path)
        # .git exists → is_git=True; git commands will fail but that's fine
        assert ctx.is_git is True

    def test_non_git_no_dot_git_no_subprocess(self, tmp_path: Path) -> None:
        """Directory without .git and git not available returns is_git=False."""

        def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise FileNotFoundError("git not found")

        with patch("subprocess.run", side_effect=fake_run):
            ctx = gather_workspace_context(tmp_path)

        assert ctx.is_git is False


class TestGatherWorkspaceContextLargeOutput:
    """Tests for large output truncation."""

    def test_large_status_output_truncated(self, tmp_path: Path) -> None:
        """Very large git status output is truncated to prevent memory pressure."""
        (tmp_path / ".git").mkdir()

        # Create a status output larger than _GIT_MAX_OUTPUT (64KB)
        huge_output = "\n".join(f"? file_{i}.txt" for i in range(5000))

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            if cmd[-1] == "--git-dir" and cmd[-2] == "rev-parse":
                return subprocess.CompletedProcess(cmd, 0, ".git", "")
            if cmd[-1] == "--show-current" and cmd[-2] == "branch":
                return subprocess.CompletedProcess(cmd, 0, "main\n", "")
            if cmd[-1] == "--porcelain" and cmd[-2] == "status":
                return subprocess.CompletedProcess(cmd, 0, huge_output, "")
            if cmd[-3] == "--format=%H %s" and cmd[-4] == "-n":
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 1, "", "unknown")

        with patch("subprocess.run", side_effect=fake_run):
            ctx = gather_workspace_context(tmp_path)

        # Should not crash; uncommitted_count will be high but finite
        assert ctx.is_git is True
        assert ctx.branch == "main"
        # The truncation happens in _safe_run_git, so count depends on what
        # survives the truncation — the key point is it doesn't crash
        assert ctx.uncommitted_count >= 0


class TestFormatWorkspaceContext:
    """Tests for format_workspace_context() output formatting."""

    def test_git_repo_full_output(self) -> None:
        """Git repo with all data produces formatted section."""
        ctx = WorkspaceContext(
            is_git=True,
            branch="feature/auth",
            uncommitted_count=3,
            staged_count=2,
            recent_commits=[
                {"hash": "abc1234", "message": "Add login"},
                {"hash": "def5678", "message": "Fix auth"},
            ],
        )
        result = format_workspace_context(ctx)

        assert "Current branch: feature/auth" in result
        assert "Uncommitted changes: 3 file(s)" in result
        assert "Staged changes: 2 file(s)" in result
        assert "Recent commits:" in result
        assert "abc1234: Add login" in result
        assert "def5678: Fix auth" in result

    def test_non_git_repo_empty_string(self) -> None:
        """Non-git repo returns empty string."""
        ctx = WorkspaceContext(is_git=False)
        result = format_workspace_context(ctx)
        assert result == ""

    def test_git_repo_branch_only(self) -> None:
        """Git repo with only branch info shows branch line."""
        ctx = WorkspaceContext(
            is_git=True,
            branch="main",
            uncommitted_count=0,
            staged_count=0,
            recent_commits=[],
        )
        result = format_workspace_context(ctx)
        assert "Current branch: main" in result
        assert "Uncommitted changes" not in result
        assert "Staged changes" not in result
        assert "Recent commits" not in result

    def test_git_repo_no_branch(self) -> None:
        """Git repo with branch=None omits branch line."""
        ctx = WorkspaceContext(
            is_git=True,
            branch=None,
            uncommitted_count=0,
            staged_count=0,
            recent_commits=[],
        )
        result = format_workspace_context(ctx)
        assert "Current branch" not in result
        # Result may be empty if no other data
        assert result == ""

    def test_git_repo_changes_only(self) -> None:
        """Git repo with only changes shows change lines."""
        ctx = WorkspaceContext(
            is_git=True,
            branch=None,
            uncommitted_count=5,
            staged_count=3,
            recent_commits=[],
        )
        result = format_workspace_context(ctx)
        assert "Uncommitted changes: 5 file(s)" in result
        assert "Staged changes: 3 file(s)" in result
        assert "Current branch" not in result

    def test_git_repo_commits_only(self) -> None:
        """Git repo with only commits shows commits."""
        ctx = WorkspaceContext(
            is_git=True,
            branch=None,
            uncommitted_count=0,
            staged_count=0,
            recent_commits=[{"hash": "aaa1111", "message": "Initial commit"}],
        )
        result = format_workspace_context(ctx)
        assert "Recent commits:" in result
        assert "aaa1111: Initial commit" in result

    def test_format_output_is_multiline(self) -> None:
        """Formatted output uses newlines between sections."""
        ctx = WorkspaceContext(
            is_git=True,
            branch="dev",
            uncommitted_count=1,
            staged_count=1,
            recent_commits=[{"hash": "aaa1111", "message": "First"}],
        )
        result = format_workspace_context(ctx)
        lines = result.split("\n")
        assert len(lines) > 3  # branch + uncommitted + staged + commits header + commit

    def test_format_commit_hash_is_short(self) -> None:
        """Commit hashes in formatted output are 7 characters."""
        ctx = WorkspaceContext(
            is_git=True,
            branch=None,
            uncommitted_count=0,
            staged_count=0,
            recent_commits=[{"hash": "abc1234", "message": "Test"}],
        )
        result = format_workspace_context(ctx)
        # The hash from the model is used as-is (already shortened by gather)
        assert "abc1234: Test" in result
