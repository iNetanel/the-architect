"""User feedback storage for The Architect.

The feedback system stores a single user message that gets injected into
the next task's execution prompt.  It follows the established JSON storage
pattern (like `monitor_state.json`, `circuit.json`) using atomic writes.

The file lives at ``<project>/.architect/feedback.json``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from the_architect.core.fileutil import safe_atomic_write_json

# Feedback file location relative to project root
FEEDBACK_FILE = Path(".architect/feedback.json")


class FeedbackState(BaseModel):
    """User feedback that will be injected into the next task's prompt.

    Attributes:
        message: The feedback text written by the user.
        written_at: ISO 8601 timestamp when the feedback was written.
        target_task: Optional task prefix to target (e.g. ``"T03"``).
            ``None`` means the next pending task.
    """

    message: str = Field(description="The user's feedback message")
    written_at: str = Field(description="ISO 8601 timestamp when written")
    target_task: str | None = Field(
        default=None,
        description="Target task prefix (e.g. 'T03'), or None for next pending task",
    )


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Returns:
        ISO timestamp string with timezone info.
    """
    return datetime.now(tz=UTC).isoformat()


def load_feedback(project: Path) -> FeedbackState | None:
    """Read stored feedback from disk.

    Args:
        project: The project root directory.

    Returns:
        A ``FeedbackState`` instance if feedback exists, or ``None`` if the
        file doesn't exist or contains invalid data.
    """
    feedback_path = project / FEEDBACK_FILE
    try:
        raw = feedback_path.read_text(encoding="utf-8")
        data: dict[str, Any] = json.loads(raw)
        return FeedbackState.model_validate(data)
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None


def save_feedback(
    project: Path,
    message: str,
    target_task: str | None = None,
) -> FeedbackState:
    """Write user feedback to disk atomically.

    Creates the ``.architect/`` directory if it doesn't exist.  Uses atomic
    write (temp file + rename) so a reader never sees a partial write.

    Args:
        project: The project root directory.
        message: The feedback text.
        target_task: Optional task prefix to target.

    Returns:
        The ``FeedbackState`` that was written.
    """
    state = FeedbackState(
        message=message,
        written_at=_now_iso(),
        target_task=target_task,
    )
    feedback_path = project / FEEDBACK_FILE
    safe_atomic_write_json(
        feedback_path,
        state.model_dump(),
        prefix=".feedback_tmp_",
        log_label="Feedback state",
    )
    return state


def clear_feedback(project: Path) -> None:
    """Remove stored feedback from disk.

    Silently succeeds if the file doesn't exist.

    Args:
        project: The project root directory.
    """
    feedback_path = project / FEEDBACK_FILE
    try:
        feedback_path.unlink(missing_ok=True)
    except OSError:
        pass
