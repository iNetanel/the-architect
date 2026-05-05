"""Tests for the_architect.core.self_update module."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch


class TestIsNewer:
    """Tests for the _is_newer version comparison helper."""

    def test_newer_patch(self) -> None:
        from the_architect.core.self_update import _is_newer

        assert _is_newer("1.2.1", "1.0.0") is True

    def test_newer_minor(self) -> None:
        from the_architect.core.self_update import _is_newer

        assert _is_newer("1.3.0", "1.0.0") is True

    def test_newer_major(self) -> None:
        from the_architect.core.self_update import _is_newer

        assert _is_newer("2.0.0", "1.9.9") is True

    def test_same_version(self) -> None:
        from the_architect.core.self_update import _is_newer

        assert _is_newer("1.0.0", "1.0.0") is False

    def test_older(self) -> None:
        from the_architect.core.self_update import _is_newer

        assert _is_newer("0.9.0", "1.0.0") is False


class TestCheckSelfUpdate:
    """Tests for check_self_update()."""

    def test_returns_empty_when_up_to_date(self) -> None:
        from the_architect.core.self_update import check_self_update

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"info": {"version": "0.0.1"}}

        with patch("the_architect.core.self_update._CURRENT_VERSION", "1.0.0"):
            with patch("httpx.get", return_value=mock_resp):
                current, latest = check_self_update()

        assert current == ""
        assert latest == ""

    def test_returns_versions_when_update_available(self) -> None:
        from the_architect.core.self_update import check_self_update

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"info": {"version": "9.9.9"}}

        with patch("the_architect.core.self_update._CURRENT_VERSION", "1.0.0"):
            with patch("httpx.get", return_value=mock_resp):
                current, latest = check_self_update()

        assert current == "1.0.0"
        assert latest == "9.9.9"

    def test_network_error_returns_empty(self) -> None:
        from the_architect.core.self_update import check_self_update

        with patch("httpx.get", side_effect=Exception("network error")):
            current, latest = check_self_update()

        assert current == ""
        assert latest == ""

    def test_malformed_response_returns_empty(self) -> None:
        from the_architect.core.self_update import check_self_update

        mock_resp = MagicMock()
        mock_resp.json.return_value = {}  # missing "info" key

        with patch("httpx.get", return_value=mock_resp):
            current, latest = check_self_update()

        assert current == ""
        assert latest == ""


class TestRunSelfUpdate:
    """Tests for run_self_update()."""

    def test_calls_pip_and_reexecs_on_success(self) -> None:
        from the_architect.core.self_update import run_self_update

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            with patch("os.execvp") as mock_exec:
                run_self_update()

        # pip install was called with the right args
        call_args = mock_run.call_args[0][0]
        assert sys.executable in call_args
        assert "pip" in call_args
        assert "install" in call_args
        assert "--upgrade" in call_args
        assert "the-architect" in call_args

        # re-exec was attempted
        assert mock_exec.called

    def test_raises_system_exit_on_pip_failure(self) -> None:
        import pytest

        from the_architect.core.self_update import run_self_update

        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(SystemExit) as exc_info:
                run_self_update()

        assert exc_info.value.code == 1

    def test_raises_system_exit_when_execvp_fails(self) -> None:
        import pytest

        from the_architect.core.self_update import run_self_update

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            with patch("os.execvp", side_effect=OSError("no such file")):
                with pytest.raises(SystemExit) as exc_info:
                    run_self_update()

        assert exc_info.value.code == 0
