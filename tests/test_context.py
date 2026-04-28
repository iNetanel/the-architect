"""Tests for the_architect.core.context — context file loading and formatting."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from the_architect.core.context import (
    extract_goal_from_context,
    format_context_for_prompt,
    load_context_paths,
    read_context_directory,
    read_context_file,
)

# ---------------------------------------------------------------------------
# read_context_file
# ---------------------------------------------------------------------------


class TestReadContextFile:
    """Tests for read_context_file()."""

    def test_reads_file_content(self, tmp_path: Path) -> None:
        """Should return full content of a small file."""
        f = tmp_path / "notes.md"
        f.write_text("# Hello\nworld", encoding="utf-8")
        content, truncated = read_context_file(f)
        assert content == "# Hello\nworld"
        assert truncated is False

    def test_truncates_large_file(self, tmp_path: Path) -> None:
        """Should truncate content exceeding max_chars and set flag."""
        f = tmp_path / "big.md"
        f.write_text("x" * 200, encoding="utf-8")
        content, truncated = read_context_file(f, max_chars=100)
        assert truncated is True
        assert len(content) > 100  # includes truncation notice
        assert "TRUNCATED" in content
        assert content.startswith("x" * 100)

    def test_raises_for_missing_file(self, tmp_path: Path) -> None:
        """Should raise FileNotFoundError for non-existent file."""
        with pytest.raises(FileNotFoundError):
            read_context_file(tmp_path / "ghost.md")

    def test_raises_for_directory(self, tmp_path: Path) -> None:
        """Should raise IsADirectoryError when given a directory."""
        with pytest.raises(IsADirectoryError):
            read_context_file(tmp_path)

    def test_exact_size_not_truncated(self, tmp_path: Path) -> None:
        """File exactly at max_chars should not be truncated."""
        f = tmp_path / "exact.md"
        f.write_text("a" * 100, encoding="utf-8")
        content, truncated = read_context_file(f, max_chars=100)
        assert truncated is False
        assert content == "a" * 100


# ---------------------------------------------------------------------------
# read_context_directory
# ---------------------------------------------------------------------------


class TestReadContextDirectory:
    """Tests for read_context_directory()."""

    def test_reads_text_files(self, tmp_path: Path) -> None:
        """Should read all text-readable files in a directory."""
        (tmp_path / "a.md").write_text("alpha", encoding="utf-8")
        (tmp_path / "b.py").write_text("beta", encoding="utf-8")
        results = read_context_directory(tmp_path)
        labels = [r[0] for r in results]
        assert "a.md" in labels
        assert "b.py" in labels

    def test_skips_binary_extensions(self, tmp_path: Path) -> None:
        """Should skip files with non-text extensions."""
        (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n")
        (tmp_path / "doc.md").write_text("hello", encoding="utf-8")
        results = read_context_directory(tmp_path)
        labels = [r[0] for r in results]
        assert "image.png" not in labels
        assert "doc.md" in labels

    def test_skips_blacklisted_dirs(self, tmp_path: Path) -> None:
        """Should skip files inside blacklisted directories."""
        node_mods = tmp_path / "node_modules"
        node_mods.mkdir()
        (node_mods / "pkg.js").write_text("module", encoding="utf-8")
        (tmp_path / "index.ts").write_text("export {}", encoding="utf-8")
        results = read_context_directory(tmp_path)
        labels = [r[0] for r in results]
        assert not any("node_modules" in lbl for lbl in labels)
        assert "index.ts" in labels

    def test_reads_nested_files(self, tmp_path: Path) -> None:
        """Should read files in subdirectories."""
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "main.py").write_text("print()", encoding="utf-8")
        results = read_context_directory(tmp_path)
        labels = [r[0] for r in results]
        assert any("main.py" in lbl for lbl in labels)

    def test_raises_for_missing_directory(self, tmp_path: Path) -> None:
        """Should raise FileNotFoundError for non-existent directory."""
        with pytest.raises(FileNotFoundError):
            read_context_directory(tmp_path / "ghost")

    def test_raises_for_file_path(self, tmp_path: Path) -> None:
        """Should raise NotADirectoryError when given a file path."""
        f = tmp_path / "file.md"
        f.write_text("x", encoding="utf-8")
        with pytest.raises(NotADirectoryError):
            read_context_directory(f)

    def test_truncation_flag_propagated(self, tmp_path: Path) -> None:
        """Should set truncated flag for oversized files."""
        f = tmp_path / "big.md"
        f.write_text("x" * 200, encoding="utf-8")
        results = read_context_directory(tmp_path, max_chars=100)
        assert len(results) == 1
        _label, _content, truncated = results[0]
        assert truncated is True


# ---------------------------------------------------------------------------
# format_context_for_prompt
# ---------------------------------------------------------------------------


class TestFormatContextForPrompt:
    """Tests for format_context_for_prompt()."""

    def test_empty_list_returns_empty_string(self) -> None:
        """Empty context list should return empty string."""
        assert format_context_for_prompt([]) == ""

    def test_single_file_formatted(self) -> None:
        """Single file should appear with its label."""
        result = format_context_for_prompt([("spec.md", "# Spec\nBuild a thing")])
        assert "--- CONTEXT: spec.md ---" in result
        assert "# Spec" in result
        assert "Build a thing" in result

    def test_multiple_files_all_present(self) -> None:
        """All files should appear in the formatted output."""
        files = [("a.md", "alpha"), ("b.md", "beta"), ("c.py", "gamma")]
        result = format_context_for_prompt(files)
        assert "--- CONTEXT: a.md ---" in result
        assert "--- CONTEXT: b.md ---" in result
        assert "--- CONTEXT: c.py ---" in result
        assert "alpha" in result
        assert "beta" in result
        assert "gamma" in result

    def test_files_separated_by_blank_line(self) -> None:
        """Files should be separated by blank lines."""
        result = format_context_for_prompt([("a.md", "A"), ("b.md", "B")])
        assert "\n\n" in result


# ---------------------------------------------------------------------------
# load_context_paths
# ---------------------------------------------------------------------------


class TestLoadContextPaths:
    """Tests for load_context_paths()."""

    def test_loads_single_file(self, tmp_path: Path) -> None:
        """Should load a single file and return its content."""
        f = tmp_path / "spec.md"
        f.write_text("build this", encoding="utf-8")
        results = load_context_paths([f])
        assert len(results) == 1
        label, content = results[0]
        assert "spec.md" in label
        assert "build this" in content

    def test_loads_directory(self, tmp_path: Path) -> None:
        """Should load all text files from a directory."""
        (tmp_path / "a.md").write_text("alpha", encoding="utf-8")
        (tmp_path / "b.py").write_text("beta", encoding="utf-8")
        results = load_context_paths([tmp_path])
        assert len(results) == 2
        contents = [c for _, c in results]
        assert any("alpha" in c for c in contents)
        assert any("beta" in c for c in contents)

    def test_raises_for_missing_path(self, tmp_path: Path) -> None:
        """Should raise FileNotFoundError for non-existent path."""
        with pytest.raises(FileNotFoundError):
            load_context_paths([tmp_path / "ghost.md"])

    def test_mixed_files_and_dirs(self, tmp_path: Path) -> None:
        """Should handle a mix of files and directories."""
        f = tmp_path / "spec.md"
        f.write_text("spec content", encoding="utf-8")
        sub = tmp_path / "docs"
        sub.mkdir()
        (sub / "guide.md").write_text("guide content", encoding="utf-8")
        results = load_context_paths([f, sub])
        all_content = " ".join(c for _, c in results)
        assert "spec content" in all_content
        assert "guide content" in all_content


# ---------------------------------------------------------------------------
# extract_goal_from_context
# ---------------------------------------------------------------------------


class TestExtractGoalFromContext:
    """Tests for extract_goal_from_context()."""

    def test_extracts_goal_section(self) -> None:
        """Should extract content from a ## Goal section."""
        content = "# Project\n\n## Goal\nBuild a REST API\n\n## Details\nMore info"
        result = extract_goal_from_context(content)
        assert result is not None
        assert "REST API" in result

    def test_extracts_objective_section(self) -> None:
        """Should extract content from a ## Objective section."""
        content = "## Objective\nCreate a CLI tool\n\n## More"
        result = extract_goal_from_context(content)
        assert result is not None
        assert "CLI tool" in result

    def test_returns_none_when_no_goal(self) -> None:
        """Should return None when no goal section is found."""
        content = "# Random document\nJust some text without a goal section."
        result = extract_goal_from_context(content)
        assert result is None

    def test_returns_none_for_short_goal(self) -> None:
        """Should return None when extracted goal is too short."""
        content = "## Goal\nOK\n"
        result = extract_goal_from_context(content)
        assert result is None

    def test_returns_first_paragraph_only(self) -> None:
        """Should return only the first paragraph of the goal section."""
        content = "## Goal\nFirst paragraph here.\n\nSecond paragraph.\n\n## Next"
        result = extract_goal_from_context(content)
        assert result is not None
        assert "First paragraph" in result
        assert "Second paragraph" not in result


# ---------------------------------------------------------------------------
# T02.1 — UnicodeDecodeError fallback path (lines 105–112)
# ---------------------------------------------------------------------------


class TestReadContextFileUnicodeFallback:
    """Tests for UnicodeDecodeError / OSError handling in read_context_file()."""

    def test_reads_non_utf8_file_with_replacement_chars(self, tmp_path: Path) -> None:
        """Should fall back to errors='replace' when UTF-8 decoding fails."""
        f = tmp_path / "binary.md"
        f.write_bytes(b"\xff\xfe invalid utf-8 \x80\x81")
        content, truncated = read_context_file(f)
        assert truncated is False
        # Replacement character U+FFFD should appear for each invalid byte
        assert "\ufffd" in content

    def test_raises_oserror_when_fallback_read_fails(self, tmp_path: Path) -> None:
        """Should raise OSError when both primary and fallback reads fail."""
        f = tmp_path / "bad.md"
        f.write_text("content", encoding="utf-8")

        call_count = 0

        def mock_read_text(self_path: Path, *args: object, **kwargs: object) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise UnicodeDecodeError("utf-8", b"", 0, 1, "invalid start byte")
            raise OSError("I/O error during fallback read")

        with patch.object(Path, "read_text", mock_read_text):
            with pytest.raises(OSError, match="Cannot read context file"):
                read_context_file(f)

    def test_raises_oserror_when_read_text_raises_oserror(self, tmp_path: Path) -> None:
        """Should raise OSError when initial read_text raises OSError."""
        f = tmp_path / "bad.md"
        f.write_text("content", encoding="utf-8")

        with patch.object(Path, "read_text", side_effect=OSError("read error")):
            with pytest.raises(OSError, match="Cannot read context file"):
                read_context_file(f)


# ---------------------------------------------------------------------------
# T02.2 — Symlink skip in directory scan (lines 162–166)
# ---------------------------------------------------------------------------


class TestReadContextDirectorySymlinkSkip:
    """Tests for symlink handling in read_context_directory()."""

    def test_skips_symlink_pointing_outside_directory(self, tmp_path: Path) -> None:
        """Should skip symlinks that resolve outside the scanned directory."""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "target.md").write_text("outside content", encoding="utf-8")

        inside = tmp_path / "inside"
        inside.mkdir()
        (inside / "link.md").symlink_to(outside / "target.md")
        (inside / "real.md").write_text("real content", encoding="utf-8")

        results = read_context_directory(inside)
        labels = [r[0] for r in results]
        assert "link.md" not in labels
        assert "real.md" in labels

    def test_skips_symlink_when_resolve_raises_oserror(self, tmp_path: Path) -> None:
        """Should skip symlinks when resolve() raises OSError."""
        inside = tmp_path / "inside"
        inside.mkdir()
        (inside / "file.md").write_text("content", encoding="utf-8")

        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "target.md").write_text("outside content", encoding="utf-8")
        (inside / "link.md").symlink_to(outside / "target.md")

        original_resolve = Path.resolve

        def mock_resolve(self_path: Path, strict: bool = False) -> Path:
            if self_path.is_symlink():
                raise OSError("resolve error")
            return original_resolve(self_path, strict=strict)

        with patch.object(Path, "resolve", mock_resolve):
            results = read_context_directory(inside)

        labels = [r[0] for r in results]
        assert "file.md" in labels
        assert "link.md" not in labels


# ---------------------------------------------------------------------------
# T02.3 — Unreadable file skip in directory scan (lines 172–174)
# ---------------------------------------------------------------------------


class TestReadContextDirectoryUnreadableFile:
    """Tests for OSError / UnicodeDecodeError skip in directory scan."""

    def test_skips_file_that_raises_oserror(self, tmp_path: Path) -> None:
        """Should skip files that raise OSError during directory scan."""
        good = tmp_path / "good.md"
        good.write_text("readable", encoding="utf-8")
        bad = tmp_path / "bad.md"
        bad.write_text("unreadable", encoding="utf-8")

        original_read_text = Path.read_text

        def mock_read_text(self_path: Path, *args: object, **kwargs: object) -> str:
            if "bad.md" in str(self_path):
                raise OSError("Permission denied")
            return original_read_text(self_path, *args, **kwargs)

        with patch.object(Path, "read_text", mock_read_text):
            results = read_context_directory(tmp_path)

        labels = [r[0] for r in results]
        assert "good.md" in labels
        assert "bad.md" not in labels


# ---------------------------------------------------------------------------
# T02.4 — OSError warning in load_context_paths (lines 243–244)
# ---------------------------------------------------------------------------


class TestLoadContextPathsOSError:
    """Tests for OSError warning path in load_context_paths()."""

    def test_skips_unreadable_file_with_warning(self, tmp_path: Path) -> None:
        """Should log warning and skip when a file cannot be read."""
        f = tmp_path / "unreadable.md"
        f.write_text("content", encoding="utf-8")

        with patch.object(Path, "read_text", side_effect=OSError("Permission denied")):
            results = load_context_paths([f])

        assert len(results) == 0
