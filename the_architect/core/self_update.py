"""Self-update utilities for The Architect.

Checks PyPI for a newer version and, when requested by the user, runs
``pip install --upgrade the-architect`` then re-executes the original
command so the user lands back in the updated version seamlessly.

Design notes
------------
- The PyPI check is a single HTTPS request to the JSON API with a short
  timeout (5 s).  Network errors are always silenced — a failed check
  never prevents the tool from running.
- Version comparison uses :mod:`packaging.version` when available and
  falls back to a simple ``tuple`` comparison on dotted integers so there
  is no hard dependency on ``packaging`` just for this feature.
- The re-exec after update uses :func:`os.execvp` which *replaces* the
  current process image — the shell history and working directory are
  preserved exactly as the user typed them.
"""

from __future__ import annotations

import os
import subprocess
import sys

import httpx
from loguru import logger

from the_architect.version import __version__ as _CURRENT_VERSION  # noqa: N812

# PyPI JSON API endpoint for the package.
_PYPI_URL = "https://pypi.org/pypi/the-architect/json"

# Request timeout in seconds — short so slow networks don't stall startup.
_TIMEOUT = 5


def check_self_update() -> tuple[str, str]:
    """Check PyPI for a newer version of The Architect.

    Returns a ``(current_version, latest_version)`` tuple.  When no
    update is available, or when the check fails for any reason, both
    values are empty strings — callers should treat ``("", "")`` as
    "no action needed".

    The check is intentionally fire-and-forget: every exception is
    caught and logged at DEBUG level so the startup path is never
    interrupted by network issues.

    Returns:
        ``(current, latest)`` where both are non-empty strings only
        when a newer version exists on PyPI.
    """
    try:
        resp = httpx.get(_PYPI_URL, timeout=_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
        latest_version: str = data["info"]["version"]
    except Exception as exc:
        logger.debug(f"Self-update check failed (non-fatal): {exc!r}")
        return "", ""

    current_version = _CURRENT_VERSION
    if _is_newer(latest_version, current_version):
        return current_version, latest_version

    return "", ""


def _is_newer(candidate: str, current: str) -> bool:
    """Return True when *candidate* is strictly newer than *current*.

    Uses :mod:`packaging.version` when available for correct PEP 440
    comparison; falls back to a tuple-of-ints comparison for simple
    ``MAJOR.MINOR.PATCH`` versions.

    Args:
        candidate: The version string from PyPI.
        current: The installed version string.

    Returns:
        True if ``candidate > current``.
    """
    try:
        from packaging.version import Version

        return Version(candidate) > Version(current)
    except Exception:
        pass

    # Fallback: compare dotted integer tuples.
    def _to_tuple(v: str) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in v.split(".") if x.isdigit())
        except Exception:
            return (0,)

    return _to_tuple(candidate) > _to_tuple(current)


def run_self_update() -> None:
    """Install the latest version of The Architect and re-exec the command.

    Runs ``pip install --upgrade the-architect`` using the same Python
    interpreter that is currently running, then replaces the current
    process with a fresh invocation of the original command via
    :func:`os.execvp`.

    This function does **not** return on success — the process image is
    replaced by ``os.execvp``.  On failure it raises :class:`SystemExit`
    with a non-zero code after printing a human-readable error.

    Raises:
        SystemExit: If the pip install step fails.
    """
    pip_cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "the-architect"]

    print(f"\nRunning: {' '.join(pip_cmd)}\n", flush=True)

    result = subprocess.run(pip_cmd, check=False)  # noqa: S603 — intentional subprocess call

    if result.returncode != 0:
        print(
            "\n[error] pip install failed — please update manually:\n"
            "  pip install --upgrade the-architect\n",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(result.returncode)

    # Re-exec the original command.  sys.argv[0] is the script/entry-point
    # path; os.execvp replaces this process image so the user seamlessly
    # continues in the updated version.
    print("\nUpdate complete — restarting The Architect…\n", flush=True)
    try:
        os.execvp(sys.argv[0], sys.argv)  # noqa: S606 — intentional re-exec
    except Exception as exc:
        # execvp failed (e.g. path changed after install).  Fall back to a
        # clean exit so the user can re-run manually.
        print(
            f"\n[warning] Could not restart automatically ({exc}). Please run 'architect' again.\n",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(0)
