"""The Architect version — single source of truth from pyproject.toml.

Reads ``pyproject.toml`` in development mode and falls back to
``importlib.metadata`` when running from an installed package.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path


def get_version() -> str:
    """Return The Architect version string.

    Resolution order:
        1. Read ``pyproject.toml`` from the package root — works in dev mode.
        2. ``importlib.metadata.version("the-architect")`` — works when installed.
        3. Return ``"0.0.0-unknown"`` if neither source is available.

    Returns:
        The version string (e.g. ``"0.1.0"``).
    """
    # 1. Prefer the checked-out pyproject.toml when present. Editable installs can
    # leave stale package metadata after a local version bump.
    version = _read_version_from_pyproject()
    if version is not None:
        return version

    # 2. Fall back to installed metadata for packaged environments.
    try:
        return importlib.metadata.version("the-architect")
    except importlib.metadata.PackageNotFoundError:
        pass

    # 3. Last resort
    return "0.0.0-unknown"


def _read_version_from_pyproject() -> str | None:
    """Read the version field from ``pyproject.toml`` on disk.

    Walks up from this file's location to find the project root
    (the directory containing ``pyproject.toml``).

    Returns:
        The version string, or ``None`` if the file cannot be found or parsed.
    """
    # Walk up from the_architect/version.py → the_architect/ → project root
    package_dir = Path(__file__).resolve().parent  # the_architect/
    project_root = package_dir.parent  # project root/

    pyproject_path = project_root / "pyproject.toml"
    if not pyproject_path.is_file():
        return None

    try:
        content = pyproject_path.read_text(encoding="utf-8")
    except OSError:
        return None

    # Minimal TOML parsing: find version = "..." under [project]
    return _extract_version_from_toml(content)


def _extract_version_from_toml(content: str) -> str | None:
    """Extract the ``version`` value from a ``[project]`` TOML section.

    This is a lightweight parser that avoids requiring a full TOML library
    for the common case of reading a single string value.

    Args:
        content: The full text of ``pyproject.toml``.

    Returns:
        The version string, or ``None`` if not found.
    """
    in_project_section = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "[project]":
            in_project_section = True
            continue
        # A new section header ends the [project] section
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project_section = False
            continue
        if in_project_section and stripped.startswith("version"):
            # Expect: version = "X.Y.Z"
            parts = stripped.split("=", 1)
            if len(parts) == 2:
                value = parts[1].strip().strip('"').strip("'")
                return value
    return None


__version__: str = get_version()
