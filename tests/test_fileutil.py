"""Tests for the cross-platform atomic file I/O helpers in fileutil.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from the_architect.core.fileutil import (
    _REPLACE_MAX_RETRIES,
    atomic_write_json,
    atomic_write_text,
    safe_atomic_write_json,
    safe_atomic_write_text,
)


class TestAtomicWriteText:
    """Tests for atomic_write_text."""

    def test_creates_file_with_correct_content(self, tmp_path: Path) -> None:
        target = tmp_path / "out.txt"
        atomic_write_text(target, "hello world")
        assert target.read_text(encoding="utf-8") == "hello world"

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "c.txt"
        atomic_write_text(target, "deep")
        assert target.read_text(encoding="utf-8") == "deep"

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "f.txt"
        target.write_text("old", encoding="utf-8")
        atomic_write_text(target, "new")
        assert target.read_text(encoding="utf-8") == "new"

    def test_no_temp_file_left_on_success(self, tmp_path: Path) -> None:
        target = tmp_path / "f.txt"
        atomic_write_text(target, "data")
        remaining = list(tmp_path.iterdir())
        assert remaining == [target]

    def test_no_temp_file_left_on_write_failure(self, tmp_path: Path) -> None:
        """Temp file must be cleaned up when the write raises."""
        target = tmp_path / "f.txt"
        with patch("os.fdopen", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                atomic_write_text(target, "data")
        assert not any(p.name.startswith(".tmp_") for p in tmp_path.iterdir())

    def test_permission_error_retried_then_succeeds(self, tmp_path: Path) -> None:
        """PermissionError on os.replace is retried; success on second attempt."""
        target = tmp_path / "f.txt"
        call_count = 0
        real_replace = __import__("os").replace

        def flaky_replace(src: str, dst: object) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise PermissionError("locked")
            real_replace(src, dst)

        with patch("the_architect.core.fileutil.os.replace", side_effect=flaky_replace):
            with patch("the_architect.core.fileutil.time.sleep"):
                atomic_write_text(target, "retried")

        assert target.read_text(encoding="utf-8") == "retried"
        assert call_count == 2

    def test_permission_error_exhausted_raises(self, tmp_path: Path) -> None:
        """All retries exhausted must re-raise PermissionError."""
        target = tmp_path / "f.txt"

        with patch(
            "the_architect.core.fileutil.os.replace",
            side_effect=PermissionError("always locked"),
        ):
            with patch("the_architect.core.fileutil.time.sleep"):
                with pytest.raises(PermissionError):
                    atomic_write_text(target, "data")


class TestAtomicWriteJson:
    """Tests for atomic_write_json."""

    def test_writes_valid_json(self, tmp_path: Path) -> None:
        target = tmp_path / "data.json"
        atomic_write_json(target, {"key": "value", "num": 42})
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload == {"key": "value", "num": 42}

    def test_writes_list(self, tmp_path: Path) -> None:
        target = tmp_path / "list.json"
        atomic_write_json(target, [1, 2, 3])
        assert json.loads(target.read_text(encoding="utf-8")) == [1, 2, 3]

    def test_custom_indent(self, tmp_path: Path) -> None:
        target = tmp_path / "indented.json"
        atomic_write_json(target, {"a": 1}, indent=4)
        raw = target.read_text(encoding="utf-8")
        assert "    " in raw


class TestSafeAtomicWriteText:
    """Tests for safe_atomic_write_text — swallows errors, returns bool."""

    def test_returns_true_on_success(self, tmp_path: Path) -> None:
        assert safe_atomic_write_text(tmp_path / "f.txt", "ok") is True

    def test_returns_false_on_error(self, tmp_path: Path) -> None:
        with patch("the_architect.core.fileutil.atomic_write_text", side_effect=OSError("err")):
            assert safe_atomic_write_text(tmp_path / "f.txt", "data") is False

    def test_does_not_raise_on_error(self, tmp_path: Path) -> None:
        with patch(
            "the_architect.core.fileutil.atomic_write_text",
            side_effect=RuntimeError("boom"),
        ):
            result = safe_atomic_write_text(tmp_path / "f.txt", "data")
        assert result is False


class TestSafeAtomicWriteJson:
    """Tests for safe_atomic_write_json — swallows errors, returns bool."""

    def test_returns_true_on_success(self, tmp_path: Path) -> None:
        assert safe_atomic_write_json(tmp_path / "f.json", {}) is True

    def test_returns_false_on_error(self, tmp_path: Path) -> None:
        with patch("the_architect.core.fileutil.atomic_write_json", side_effect=OSError("err")):
            assert safe_atomic_write_json(tmp_path / "f.json", {}) is False

    def test_log_label_appears_in_debug_log(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from loguru import logger

        # Route loguru DEBUG output to stderr so capsys can capture it.
        logger.remove()
        logger.add(__import__("sys").stderr, level="DEBUG", format="{message}")
        try:
            with patch(
                "the_architect.core.fileutil.atomic_write_json",
                side_effect=OSError("disk full"),
            ):
                safe_atomic_write_json(tmp_path / "f.json", {}, log_label="My ledger")
            captured = capsys.readouterr()
            assert "My ledger" in captured.err
        finally:
            logger.remove()
            logger.add(__import__("sys").stderr, level="WARNING", format="{message}")


class TestRetryConstant:
    """The retry count must be a positive integer — guard against accidental edits."""

    def test_retry_count_is_positive(self) -> None:
        assert isinstance(_REPLACE_MAX_RETRIES, int)
        assert _REPLACE_MAX_RETRIES > 0
