"""Cross-platform file I/O utilities for The Architect.

All helpers here are platform-agnostic — they must behave identically on
Linux, macOS, and Windows without conditional branches in the callers.

Atomic write pattern
--------------------
The standard POSIX idiom of ``write temp → os.replace(tmp, dst)`` is
*almost* atomic on Windows too, but ``os.replace`` raises ``PermissionError``
when another process has the destination file open (e.g. the dashboard process
reading ``monitor_state.json`` while the runner overwrites it).  POSIX
systems permit the rename even if readers have the file open; Windows does not.

The fix is a brief exponential-backoff retry on ``PermissionError``.  The
retry is short (≤ ~0.3 s total across 4 attempts) and the ``PermissionError``
is transient by design — readers open, read, and close the file immediately.
The retry path is compiled and present on all platforms but only ever
*triggered* on Windows.  On POSIX, ``os.replace`` never raises for this reason
and the retry loop exits on the first attempt.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from loguru import logger

# Maximum number of retry attempts when os.replace raises PermissionError.
# Delays:  0.02 s  →  0.04 s  →  0.08 s  →  0.16 s  (total ≈ 0.30 s)
_REPLACE_MAX_RETRIES = 4
_REPLACE_INITIAL_DELAY = 0.02  # seconds


def _replace_with_retry(tmp_path: str, dst_path: Path) -> None:
    """Rename *tmp_path* to *dst_path*, retrying on PermissionError.

    On POSIX this is a single call; the loop never fires.  On Windows a
    reader may transiently hold the destination open, causing PermissionError.
    We retry up to ``_REPLACE_MAX_RETRIES`` times with exponential backoff
    before re-raising so callers still see the error after the grace period.

    Args:
        tmp_path: Absolute path to the source (temp) file as a string.
        dst_path: Target :class:`~pathlib.Path`.

    Raises:
        PermissionError: If all retry attempts are exhausted.
        OSError: For any non-PermissionError rename failure.
    """
    delay = _REPLACE_INITIAL_DELAY
    for attempt in range(_REPLACE_MAX_RETRIES):
        try:
            os.replace(tmp_path, dst_path)
            return
        except PermissionError:
            if attempt == _REPLACE_MAX_RETRIES - 1:
                raise
            time.sleep(delay)
            delay *= 2


def atomic_write_text(path: Path, content: str, prefix: str = ".tmp_") -> None:
    """Write *content* to *path* atomically using a temp file + rename.

    The file is written to a sibling temp file in the same directory as
    *path*, then renamed.  The rename is retried on ``PermissionError``
    so the call is safe on Windows even if a reader has the destination
    open at the same moment.

    On failure the temp file is removed and the exception is re-raised.

    Args:
        path: Target file path.  The parent directory must exist or be
            creatable.
        content: UTF-8 text content to write.
        prefix: Temp-file name prefix (default ``.tmp_``).

    Raises:
        OSError: If the write or rename ultimately fails.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=prefix, suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        fd = -1  # fdopen took ownership and closed it
        _replace_with_retry(tmp, path)
    except Exception:
        if fd != -1:
            # fdopen failed before taking ownership — close the raw fd first
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data: Any, prefix: str = ".tmp_", indent: int = 2) -> None:
    """Serialise *data* to JSON and write it to *path* atomically.

    Delegates to :func:`atomic_write_text` after serialisation so the
    cross-platform retry logic lives in one place.

    Args:
        path: Target file path.
        data: JSON-serialisable value.
        prefix: Temp-file name prefix.
        indent: JSON indentation level.

    Raises:
        OSError: If the write or rename ultimately fails.
        TypeError: If *data* is not JSON-serialisable.
    """
    atomic_write_text(path, json.dumps(data, indent=indent), prefix=prefix)


def safe_atomic_write_text(
    path: Path,
    content: str,
    prefix: str = ".tmp_",
    *,
    log_label: str = "file",
) -> bool:
    """Write *content* to *path* atomically, swallowing all errors.

    Suitable for best-effort infrastructure writes (monitor state, ledger,
    etc.) where a failure must never crash a run.

    Args:
        path: Target file path.
        content: UTF-8 text content to write.
        prefix: Temp-file name prefix.
        log_label: Short label used in the debug-level log message on error.

    Returns:
        ``True`` on success, ``False`` if an exception was swallowed.
    """
    try:
        atomic_write_text(path, content, prefix=prefix)
        return True
    except Exception as exc:
        logger.debug(f"{log_label} atomic write failed (non-fatal): {exc!r}")
        return False


def safe_atomic_write_json(
    path: Path,
    data: Any,
    prefix: str = ".tmp_",
    indent: int = 2,
    *,
    log_label: str = "file",
) -> bool:
    """Serialise *data* to JSON and write atomically, swallowing all errors.

    Args:
        path: Target file path.
        data: JSON-serialisable value.
        prefix: Temp-file name prefix.
        indent: JSON indentation level.
        log_label: Short label used in the debug-level log message on error.

    Returns:
        ``True`` on success, ``False`` if an exception was swallowed.
    """
    try:
        atomic_write_json(path, data, prefix=prefix, indent=indent)
        return True
    except Exception as exc:
        logger.debug(f"{log_label} atomic write failed (non-fatal): {exc!r}")
        return False
