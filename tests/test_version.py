"""Tests for the_architect.version — centralized version source."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from the_architect import __version__ as init_version
from the_architect.version import __version__ as module_version
from the_architect.version import (
    _extract_version_from_toml,
    _read_version_from_pyproject,
    get_version,
)

# ---------------------------------------------------------------------------
# T01.5 — Tests
# ---------------------------------------------------------------------------


class TestGetVersion:
    """Tests for ``get_version()``."""

    def test_returns_non_empty_string(self) -> None:
        """``get_version()`` should return a non-empty version string."""
        version = get_version()
        assert isinstance(version, str)
        assert len(version) > 0

    def test_returns_valid_semver_format(self) -> None:
        """Version should look like a semver string (X.Y.Z)."""
        version = get_version()
        parts = version.split(".")
        assert len(parts) >= 2, f"Version '{version}' does not look like semver"
        assert all(p.isdigit() for p in parts[:3]), f"Version '{version}' has non-numeric parts"


class TestVersionImportable:
    """Tests that ``__version__`` is importable from the right places."""

    def test_importable_from_the_architect_init(self) -> None:
        """``__version__`` should be importable from ``the_architect``."""
        assert isinstance(init_version, str)
        assert len(init_version) > 0

    def test_importable_from_the_architect_version(self) -> None:
        """``__version__`` should be importable from ``the_architect.version``."""
        assert isinstance(module_version, str)
        assert len(module_version) > 0

    def test_both_imports_match(self) -> None:
        """``the_architect.__version__`` must equal ``the_architect.version.__version__``."""
        assert init_version == module_version


class TestVersionMatchesPyproject:
    """Tests that the runtime version matches ``pyproject.toml``."""

    def test_matches_pyproject_toml(self) -> None:
        """The version returned by ``get_version()`` must match ``pyproject.toml``."""
        pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        assert pyproject_path.is_file(), f"pyproject.toml not found at {pyproject_path}"

        content = pyproject_path.read_text(encoding="utf-8")
        extracted = _extract_version_from_toml(content)
        assert extracted is not None, "Could not extract version from pyproject.toml"

        assert get_version() == extracted

    def test_read_version_from_pyproject_returns_value(self) -> None:
        """``_read_version_from_pyproject()`` should return a version string."""
        version = _read_version_from_pyproject()
        assert version is not None
        assert isinstance(version, str)
        assert len(version) > 0


class TestExtractVersionFromToml:
    """Tests for the lightweight TOML version extractor."""

    def test_extracts_version_from_project_section(self) -> None:
        """Should extract version from a ``[project]`` section."""
        content = '[project]\nname = "the-architect"\nversion = "1.2.3"\n'
        assert _extract_version_from_toml(content) == "1.2.3"

    def test_returns_none_when_no_project_section(self) -> None:
        """Should return ``None`` if there is no ``[project]`` section."""
        content = "[tool.ruff]\nline-length = 120\n"
        assert _extract_version_from_toml(content) is None

    def test_returns_none_when_no_version_in_project(self) -> None:
        """Should return ``None`` if ``[project]`` exists but has no ``version``."""
        content = '[project]\nname = "the-architect"\n'
        assert _extract_version_from_toml(content) is None

    def test_stops_at_next_section(self) -> None:
        """Should not read ``version`` from a section after ``[project]``."""
        content = '[project]\nname = "the-architect"\n\n[tool.other]\nversion = "9.9.9"\n'
        assert _extract_version_from_toml(content) is None

    def test_handles_single_quoted_version(self) -> None:
        """Should handle single-quoted version strings."""
        content = "[project]\nversion = '3.0.0'\n"
        assert _extract_version_from_toml(content) == "3.0.0"


class TestFallbackBehavior:
    """Tests for fallback when ``importlib.metadata`` fails."""

    def test_fallback_to_pyproject_on_package_not_found(self) -> None:
        """When ``importlib.metadata`` raises PackageNotFoundError, fall back to pyproject.toml."""
        with patch(
            "the_architect.version.importlib.metadata.version",
            side_effect=__import__("importlib.metadata").metadata.PackageNotFoundError(
                "the-architect"
            ),
        ):
            version = get_version()
            # Should still return a valid version from pyproject.toml
            assert version != "0.0.0-unknown"
            assert len(version) > 0

    def test_fallback_to_unknown_when_pyproject_missing(self) -> None:
        """When both importlib.metadata AND pyproject.toml fail, return the sentinel."""
        with (
            patch(
                "the_architect.version.importlib.metadata.version",
                side_effect=__import__("importlib.metadata").metadata.PackageNotFoundError(
                    "the-architect"
                ),
            ),
            patch(
                "the_architect.version._read_version_from_pyproject",
                return_value=None,
            ),
        ):
            assert get_version() == "0.0.0-unknown"

    def test_read_version_returns_none_when_pyproject_not_file(self) -> None:
        """Missing pyproject.toml must yield ``None`` from the low-level reader."""
        with patch("the_architect.version.Path.is_file", return_value=False):
            assert _read_version_from_pyproject() is None

    def test_read_version_returns_none_on_os_error(self) -> None:
        """An OSError while reading pyproject.toml must be swallowed and return ``None``."""
        with (
            patch("the_architect.version.Path.is_file", return_value=True),
            patch("the_architect.version.Path.read_text", side_effect=OSError("locked")),
        ):
            assert _read_version_from_pyproject() is None
