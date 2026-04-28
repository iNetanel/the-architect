"""Context injection for The Architect's planning flow.

Reads files and directories specified via ``--context`` flags and
formats them for injection into the architect agent's planning prompt.

Each context file is labelled clearly so the architect agent can
distinguish between different sources.  Oversized files are truncated
with a note rather than causing a failure.

Supported file extensions for directory reads:
    .md, .txt, .rst, .json, .yaml, .yml, .toml, .py, .ts, .js, .go, .rs

Binary files are skipped silently.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum characters per context file before truncation
MAX_CONTEXT_FILE_CHARS = 50_000

# Text-readable extensions for directory scanning
TEXT_EXTENSIONS: set[str] = {
    ".md",
    ".txt",
    ".rst",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
    ".rb",
    ".java",
    ".kt",
    ".cs",
    ".php",
    ".cfg",
    ".ini",
    ".env",
    ".sh",
    ".bash",
    ".zsh",
    ".sql",
    ".graphql",
    ".proto",
    ".tf",
    ".hcl",
}

# Directories to skip when scanning recursively
SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".architect",
    ".pytest_cache",
    "dist",
    "build",
    ".next",
    ".cache",
}


# ---------------------------------------------------------------------------
# Context file reading
# ---------------------------------------------------------------------------


def read_context_file(path: Path, max_chars: int = MAX_CONTEXT_FILE_CHARS) -> tuple[str, bool]:
    """Read a single context file, truncating if oversized.

    Args:
        path: Path to the file to read.
        max_chars: Maximum characters before truncation.

    Returns:
        Tuple of (content, was_truncated).

    Raises:
        FileNotFoundError: If the file does not exist.
        IsADirectoryError: If the path is a directory.
    """
    if not path.exists():
        raise FileNotFoundError(f"Context file not found: {path}")

    if path.is_dir():
        raise IsADirectoryError(f"Context path is a directory (use read_context_directory): {path}")

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Try with a more permissive encoding
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise OSError(f"Cannot read context file: {exc}") from exc
    except OSError as exc:
        raise OSError(f"Cannot read context file: {exc}") from exc

    was_truncated = len(content) > max_chars
    if was_truncated:
        content = content[:max_chars]
        content += (
            f"\n\n--- TRUNCATED: file exceeds {max_chars:,} characters. "
            f"Showing first {max_chars:,} characters. ---\n"
        )

    return content, was_truncated


def read_context_directory(
    dir_path: Path,
    max_chars: int = MAX_CONTEXT_FILE_CHARS,
) -> list[tuple[str, str, bool]]:
    """Read all text-readable files from a directory recursively.

    Skips binary files and directories in the skip list.

    Args:
        dir_path: Path to the directory to scan.
        max_chars: Maximum characters per file before truncation.

    Returns:
        List of (relative_path, content, was_truncated) tuples.

    Raises:
        FileNotFoundError: If the directory does not exist.
    """
    if not dir_path.exists():
        raise FileNotFoundError(f"Context directory not found: {dir_path}")

    if not dir_path.is_dir():
        raise NotADirectoryError(f"Context path is not a directory: {dir_path}")

    results: list[tuple[str, str, bool]] = []

    for path in sorted(dir_path.rglob("*")):
        # Skip blacklisted directories
        if any(skip in path.parts for skip in SKIP_DIRS):
            continue

        # Only read files with text-readable extensions
        if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
            continue

        # Skip symlinks that resolve outside the directory
        if path.is_symlink():
            try:
                if not path.resolve().is_relative_to(dir_path.resolve()):
                    continue
            except (OSError, ValueError):
                continue

        rel_path = str(path.relative_to(dir_path))
        try:
            content, truncated = read_context_file(path, max_chars=max_chars)
            results.append((rel_path, content, truncated))
        except (OSError, UnicodeDecodeError) as exc:
            logger.debug(f"Skipping context file {rel_path}: {exc!r}")
            continue

    return results


# ---------------------------------------------------------------------------
# Context formatting for prompt injection
# ---------------------------------------------------------------------------


def format_context_for_prompt(
    context_files: list[tuple[str, str]],
) -> str:
    """Format context files for injection into the architect prompt.

    Each file is labelled with its path so the architect agent can
    distinguish between different sources.

    Args:
        context_files: List of (label, content) tuples.

    Returns:
        Formatted string ready for prompt injection.
    """
    if not context_files:
        return ""

    parts: list[str] = []
    for label, content in context_files:
        parts.append(f"--- CONTEXT: {label} ---")
        parts.append(content)
        parts.append("")  # blank line separator

    return "\n".join(parts)


def load_context_paths(
    paths: list[Path],
    max_chars: int = MAX_CONTEXT_FILE_CHARS,
) -> list[tuple[str, str]]:
    """Load context from a list of file and directory paths.

    Handles both files and directories.  Returns labelled content
    ready for ``format_context_for_prompt``.

    Args:
        paths: List of file or directory paths.
        max_chars: Maximum characters per file before truncation.

    Returns:
        List of (label, content) tuples.

    Raises:
        FileNotFoundError: If any path does not exist.
    """
    results: list[tuple[str, str]] = []

    for path in paths:
        resolved = path.resolve()

        if resolved.is_dir():
            dir_results = read_context_directory(resolved, max_chars=max_chars)
            for rel_path, content, _truncated in dir_results:
                label = f"{path}/{rel_path}"
                results.append((label, content))
        elif resolved.is_file():
            try:
                content, _truncated = read_context_file(resolved, max_chars=max_chars)
                results.append((str(path), content))
            except OSError as exc:
                logger.warning(f"Failed to read context file {path}: {exc!r}")
        else:
            raise FileNotFoundError(f"Context path not found: {path}")

    return results


def extract_goal_from_context(context_content: str) -> str | None:
    """Attempt to extract a goal from context file content.

    Looks for common goal patterns in the content.  Returns the first
    plausible goal found, or None if no goal can be extracted.

    Args:
        context_content: Combined content from all context files.

    Returns:
        Extracted goal string, or None.
    """
    import re

    # Look for explicit goal sections
    goal_patterns = [
        # "## Goal" section
        re.compile(r"##\s*Goal\s*\n\s*(.+?)(?:\n\s*##|\n\s*$)", re.DOTALL),
        # "## Objective" section
        re.compile(r"##\s*Objective\s*\n\s*(.+?)(?:\n\s*##|\n\s*$)", re.DOTALL),
        # "## Requirements" section
        re.compile(r"##\s*Requirements?\s*\n\s*(.+?)(?:\n\s*##|\n\s*$)", re.DOTALL),
    ]

    for pattern in goal_patterns:
        match = pattern.search(context_content)
        if match:
            goal = match.group(1).strip()
            # Clean up — take first paragraph only
            first_para = goal.split("\n\n")[0].strip()
            if first_para and len(first_para) > 10:
                return first_para

    return None
