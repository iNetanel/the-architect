"""Tests for provider live health checks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from the_architect.core.provider_health import (
    _HEALTH_CACHE,
    ProviderHealthError,
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


class _Process:
    def __init__(self, *, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return int(self.returncode or 0)


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
