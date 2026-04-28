"""Tests for the_architect/exceptions.py.

Covers:
  - All exception classes can be instantiated with a message
  - ArchitectError is the base class for all custom exceptions
  - str() and repr() output behaves correctly
  - Exceptions can be raised and caught correctly
"""

from __future__ import annotations

import pytest

from the_architect.exceptions import ArchitectError, TaskNotFound

# ---------------------------------------------------------------------------
# ArchitectError — base exception
# ---------------------------------------------------------------------------


class TestArchitectError:
    """Tests for the ArchitectError base exception class."""

    def test_can_be_instantiated_with_message(self) -> None:
        """ArchitectError should accept a message string."""
        exc = ArchitectError("something went wrong")
        assert str(exc) == "something went wrong"

    def test_can_be_raised_and_caught(self) -> None:
        """ArchitectError should be raisable and catchable."""
        with pytest.raises(ArchitectError, match="something went wrong"):
            raise ArchitectError("something went wrong")

    def test_is_subclass_of_exception(self) -> None:
        """ArchitectError should inherit from Exception."""
        assert issubclass(ArchitectError, Exception)

    def test_repr_contains_class_name(self) -> None:
        """repr() should include the exception class name."""
        exc = ArchitectError("test error")
        assert "ArchitectError" in repr(exc)

    def test_str_returns_message(self) -> None:
        """str() should return the exception message."""
        exc = ArchitectError("test message")
        assert str(exc) == "test message"

    def test_can_be_instantiated_with_no_args(self) -> None:
        """ArchitectError should be instantiable without arguments."""
        exc = ArchitectError()
        assert isinstance(exc, ArchitectError)

    def test_can_be_caught_as_exception(self) -> None:
        """ArchitectError should be catchable as a generic Exception."""
        with pytest.raises(Exception):
            raise ArchitectError("base exception catch test")

    def test_args_stored_correctly(self) -> None:
        """The exception message should be accessible via args."""
        exc = ArchitectError("my error")
        assert exc.args == ("my error",)


# ---------------------------------------------------------------------------
# TaskNotFound
# ---------------------------------------------------------------------------


class TestTaskNotFound:
    """Tests for the TaskNotFound exception class."""

    def test_is_subclass_of_architect_error(self) -> None:
        """TaskNotFound should be a subclass of ArchitectError."""
        assert issubclass(TaskNotFound, ArchitectError)

    def test_is_subclass_of_exception(self) -> None:
        """TaskNotFound should be a subclass of Exception."""
        assert issubclass(TaskNotFound, Exception)

    def test_message_includes_task_name(self) -> None:
        """str() should include the task name passed to __init__."""
        exc = TaskNotFound("T03")
        assert "T03" in str(exc)

    def test_message_format(self) -> None:
        """Message should follow 'Task not found: <task>' format."""
        exc = TaskNotFound("T07")
        assert str(exc) == "Task not found: T07"

    def test_can_be_raised_and_caught(self) -> None:
        """TaskNotFound should be raisable and catchable."""
        with pytest.raises(TaskNotFound, match="T05"):
            raise TaskNotFound("T05")

    def test_can_be_caught_as_architect_error(self) -> None:
        """TaskNotFound should be catchable as ArchitectError."""
        with pytest.raises(ArchitectError):
            raise TaskNotFound("T01")

    def test_can_be_caught_as_exception(self) -> None:
        """TaskNotFound should be catchable as a generic Exception."""
        with pytest.raises(Exception):
            raise TaskNotFound("T02")

    def test_repr_contains_class_name(self) -> None:
        """repr() should contain the class name."""
        exc = TaskNotFound("T01")
        assert "TaskNotFound" in repr(exc)

    def test_args_contain_formatted_message(self) -> None:
        """args tuple should contain the formatted message."""
        exc = TaskNotFound("T04")
        assert len(exc.args) == 1
        assert "T04" in exc.args[0]

    def test_different_task_names_produce_different_messages(self) -> None:
        """Two TaskNotFound instances with different tasks should have different messages."""
        exc1 = TaskNotFound("T01")
        exc2 = TaskNotFound("T99")
        assert str(exc1) != str(exc2)
        assert "T01" in str(exc1)
        assert "T99" in str(exc2)


# ---------------------------------------------------------------------------
# Inheritance tree verification
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    """Tests for the full exception inheritance hierarchy."""

    def test_all_custom_exceptions_inherit_from_architect_error(self) -> None:
        """Every custom exception should derive from ArchitectError."""
        custom_exceptions = [TaskNotFound]
        for exc_cls in custom_exceptions:
            assert issubclass(exc_cls, ArchitectError), (
                f"{exc_cls.__name__} should be a subclass of ArchitectError"
            )

    def test_architect_error_is_not_base_exception(self) -> None:
        """ArchitectError should NOT inherit from BaseException directly — only Exception."""
        assert not issubclass(ArchitectError, BaseException.__subclasses__()[0])  # SystemExit check

    def test_task_not_found_instance_check(self) -> None:
        """isinstance checks should work correctly through the hierarchy."""
        exc = TaskNotFound("T01")
        assert isinstance(exc, TaskNotFound)
        assert isinstance(exc, ArchitectError)
        assert isinstance(exc, Exception)
