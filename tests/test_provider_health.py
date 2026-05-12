"""Tests for provider live health checks."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from the_architect.core.provider import ParsedEvent
from the_architect.core.provider_health import (
    _HEALTH_CACHE,
    ProviderHealthError,
    _accumulate_probe_text,
    check_provider_health,
)


class _FakeProvider:
    name = "gemini-cli"
    display_name = "Gemini CLI"

    def is_installed(self) -> bool:
        return True

    def has_any_models(self) -> bool:
        return True

    def install_hint(self) -> str:
        return "npm install -g @google/gemini-cli"

    def supports_agents(self) -> bool:
        return False

    def build_command(
        self,
        instruction: str,
        model_override: str | None = None,
        agent_override: str | None = None,
    ) -> list[str]:
        assert "ARCHITECT_HEALTH_OK" in instruction
        assert agent_override is None
        if model_override:
            return ["gemini", "--model", model_override]
        return ["gemini"]

    def get_env_overrides(self, config_override: Path | None = None) -> dict[str, str]:
        return {}

    def parse_output_line(self, line: str):  # noqa: ANN201
        return None


_FakeProvider.__module__ = "the_architect.core.gemini_cli_provider"


class _NotInstalledProvider:
    """Provider variant that reports as not installed."""

    name = "gemini-cli"
    display_name = "Gemini CLI"
    __module__ = "the_architect.core.gemini_cli_provider"

    def is_installed(self) -> bool:
        return False

    def has_any_models(self) -> bool:
        return True

    def install_hint(self) -> str:
        return "npm install -g @google/gemini-cli"

    def supports_agents(self) -> bool:
        return False

    def build_command(
        self,
        instruction: str,
        model_override: str | None = None,
        agent_override: str | None = None,
    ) -> list[str]:
        return ["gemini"]

    def get_env_overrides(self, config_override: Path | None = None) -> dict[str, str]:
        return {}

    def parse_output_line(self, line: str):  # noqa: ANN201
        return None


class _NoModelsProvider:
    """Provider variant that has no configured models."""

    name = "gemini-cli"
    display_name = "Gemini CLI"
    __module__ = "the_architect.core.gemini_cli_provider"

    def is_installed(self) -> bool:
        return True

    def has_any_models(self) -> bool:
        return False

    def install_hint(self) -> str:
        return "npm install -g @google/gemini-cli"

    def supports_agents(self) -> bool:
        return False

    def build_command(
        self,
        instruction: str,
        model_override: str | None = None,
        agent_override: str | None = None,
    ) -> list[str]:
        return ["gemini"]

    def get_env_overrides(self, config_override: Path | None = None) -> dict[str, str]:
        return {}

    def parse_output_line(self, line: str):  # noqa: ANN201
        return None


class _DisplayLinesProvider:
    """Provider that returns ParsedEvent with display_lines."""

    name = "gemini-cli"
    display_name = "Gemini CLI"
    __module__ = "the_architect.core.gemini_cli_provider"

    def is_installed(self) -> bool:
        return True

    def has_any_models(self) -> bool:
        return True

    def install_hint(self) -> str:
        return "npm install -g @google/gemini-cli"

    def supports_agents(self) -> bool:
        return False

    def build_command(
        self,
        instruction: str,
        model_override: str | None = None,
        agent_override: str | None = None,
    ) -> list[str]:
        return ["gemini"]

    def get_env_overrides(self, config_override: Path | None = None) -> dict[str, str]:
        return {}

    def parse_output_line(self, line: str):  # noqa: ANN201
        if line.startswith("EVENT:"):
            return ParsedEvent(
                event_type="text",
                display_lines=["[parsed]", line],
            )
        return None


class _RateLimitProvider:
    """Provider that returns ParsedEvent with rate_limit=True."""

    name = "gemini-cli"
    display_name = "Gemini CLI"
    __module__ = "the_architect.core.gemini_cli_provider"

    def is_installed(self) -> bool:
        return True

    def has_any_models(self) -> bool:
        return True

    def install_hint(self) -> str:
        return "npm install -g @google/gemini-cli"

    def supports_agents(self) -> bool:
        return False

    def build_command(
        self,
        instruction: str,
        model_override: str | None = None,
        agent_override: str | None = None,
    ) -> list[str]:
        return ["gemini"]

    def get_env_overrides(self, config_override: Path | None = None) -> dict[str, str]:
        return {}

    def parse_output_line(self, line: str):  # noqa: ANN201
        if "rate_limit" in line.lower():
            return ParsedEvent(
                event_type="error",
                rate_limit=True,
            )
        return None


class _ModelNotFoundProvider:
    """Provider that returns ParsedEvent with model_not_found=True."""

    name = "gemini-cli"
    display_name = "Gemini CLI"
    __module__ = "the_architect.core.gemini_cli_provider"

    def is_installed(self) -> bool:
        return True

    def has_any_models(self) -> bool:
        return True

    def install_hint(self) -> str:
        return "npm install -g @google/gemini-cli"

    def supports_agents(self) -> bool:
        return False

    def build_command(
        self,
        instruction: str,
        model_override: str | None = None,
        agent_override: str | None = None,
    ) -> list[str]:
        return ["gemini"]

    def get_env_overrides(self, config_override: Path | None = None) -> dict[str, str]:
        return {}

    def parse_output_line(self, line: str):  # noqa: ANN201
        if "not_found" in line.lower():
            return ParsedEvent(
                event_type="error",
                model_not_found=True,
            )
        return None


class _Process:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        timeout: bool = False,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.killed = False
        self._timeout = timeout

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._timeout:
            await asyncio.sleep(999)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return int(self.returncode) if self.returncode is not None else 0


@pytest.fixture(autouse=True)
def clear_health_cache() -> None:
    _HEALTH_CACHE.clear()


class TestProviderHealth:
    @pytest.mark.asyncio
    async def test_successful_probe_passes(self, tmp_path: Path) -> None:
        process = _Process(stdout=b"ARCHITECT_HEALTH_OK\n")
        with patch(
            "the_architect.core.provider_health.asyncio.create_subprocess_exec",
            return_value=process,
        ) as spawn:
            await check_provider_health(
                provider=_FakeProvider(),
                project_dir=tmp_path,
                model_override="gemini-2.5-pro",
            )

        args = spawn.call_args.args
        assert args == ("gemini", "--model", "gemini-2.5-pro")

    @pytest.mark.asyncio
    async def test_quota_error_from_stderr_fails(self, tmp_path: Path) -> None:
        process = _Process(
            stderr=b"RESOURCE_EXHAUSTED: quota exceeded; billing not enabled\n",
            returncode=1,
        )
        with patch(
            "the_architect.core.provider_health.asyncio.create_subprocess_exec",
            return_value=process,
        ):
            with pytest.raises(ProviderHealthError, match="quota"):
                await check_provider_health(provider=_FakeProvider(), project_dir=tmp_path)

    @pytest.mark.asyncio
    async def test_success_is_cached(self, tmp_path: Path) -> None:
        process = _Process(stdout=b"ARCHITECT_HEALTH_OK\n")
        with patch(
            "the_architect.core.provider_health.asyncio.create_subprocess_exec",
            return_value=process,
        ) as spawn:
            await check_provider_health(provider=_FakeProvider(), project_dir=tmp_path)
            await check_provider_health(provider=_FakeProvider(), project_dir=tmp_path)

        assert spawn.call_count == 1

    # --- T01.1: Error-path tests for check_provider_health ---

    @pytest.mark.asyncio
    async def test_not_installed_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ProviderHealthError, match="not installed"):
            await check_provider_health(
                provider=_NotInstalledProvider(),
                project_dir=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_no_models_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ProviderHealthError, match="does not appear configured"):
            await check_provider_health(
                provider=_NoModelsProvider(),
                project_dir=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_timeout_kills_process_and_raises(self, tmp_path: Path) -> None:
        # returncode=None mimics a real still-running subprocess
        process = _Process(stdout=b"", stderr=b"", returncode=None, timeout=True)
        with patch(
            "the_architect.core.provider_health.asyncio.create_subprocess_exec",
            return_value=process,
        ):
            with pytest.raises(ProviderHealthError, match="timed out"):
                await check_provider_health(
                    provider=_FakeProvider(),
                    project_dir=tmp_path,
                    timeout_seconds=0.1,
                )

        assert process.killed

    @pytest.mark.asyncio
    async def test_oserror_on_spawn_raises(self, tmp_path: Path) -> None:
        with patch(
            "the_architect.core.provider_health.asyncio.create_subprocess_exec",
            side_effect=OSError("No such file or directory"),
        ):
            with pytest.raises(ProviderHealthError, match="health check failed"):
                await check_provider_health(provider=_FakeProvider(), project_dir=tmp_path)

    @pytest.mark.asyncio
    async def test_generic_nonzero_exit_raises(self, tmp_path: Path) -> None:
        process = _Process(
            stdout=b"some generic error output here\n",
            returncode=1,
        )
        with patch(
            "the_architect.core.provider_health.asyncio.create_subprocess_exec",
            return_value=process,
        ):
            with pytest.raises(ProviderHealthError, match="health check failed"):
                await check_provider_health(provider=_FakeProvider(), project_dir=tmp_path)

    # --- T01.2: Tests for _accumulate_probe_text ---

    def test_accumulate_skips_empty_lines(self) -> None:
        result = _accumulate_probe_text(_FakeProvider(), "\n\nhello\n\n\n", "\n")
        assert result == "hello"

    def test_accumulate_uses_display_lines(self) -> None:
        result = _accumulate_probe_text(
            _DisplayLinesProvider(),
            "EVENT: some data\nraw line\n",
            "",
        )
        # display_lines branch fires for "EVENT: some data", raw pass-through for "raw line"
        assert "[parsed]" in result
        assert "EVENT: some data" in result
        assert "raw line" in result

    def test_accumulate_captures_rate_limit_raw(self) -> None:
        result = _accumulate_probe_text(
            _RateLimitProvider(),
            "hit rate_limit error\nnormal text\n",
            "",
        )
        # rate_limit branch: raw line captured because display_lines is empty
        assert "hit rate_limit error" in result
        assert "normal text" in result

    def test_accumulate_captures_model_not_found_raw(self) -> None:
        result = _accumulate_probe_text(
            _ModelNotFoundProvider(),
            "model not_found\nother line\n",
            "",
        )
        # model_not_found branch: raw line captured because display_lines is empty
        assert "model not_found" in result
        assert "other line" in result
