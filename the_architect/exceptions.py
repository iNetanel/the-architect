"""Custom exceptions raised by The Architect."""

from __future__ import annotations


class ArchitectError(Exception):
    """Base exception for all The Architect errors."""

    pass


class TaskNotFound(ArchitectError):
    """Raised when a requested task cannot be found."""

    def __init__(self, task: str) -> None:
        super().__init__(f"Task not found: {task}")
