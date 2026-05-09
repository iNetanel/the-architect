"""Provider-specific live health checks."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from the_architect.core.circuit import ProviderErrorKind, detect_provider_error

if TYPE_CHECKING:
    from the_architect.core.provider import ArchitectProvider


class ProviderHealthError(Exception):
    """Raised when a provider cannot complete a small live probe."""


_HEALTH_CACHE: set[tuple[str, str, str, str]] = set()
_HEALTH_PROMPT = (
    "Health check only. Reply with exactly ARCHITECT_HEALTH_OK and do not inspect files."
)


async def check_provider_health(
    *,
    provider: ArchitectProvider,
    project_dir: Path,
    model_override: str | None = None,
    agent_override: str | None = None,
    config_override: Path | None = None,
    timeout_seconds: float = 30.0,
) -> None:
    """Run a tiny live provider probe and raise if the account cannot respond.

    The probe is intentionally implemented through the provider abstraction: each
    provider builds its own command, environment, model flag, and agent flag. This
    keeps the check provider-specific without duplicating CLI command details here.

    Args:
        provider: Provider implementation to probe.
        project_dir: Project root to use as subprocess cwd.
        model_override: Optional model to probe.
        agent_override: Optional provider agent to probe.
        config_override: Optional provider-specific config file.
        timeout_seconds: Maximum time to wait for the probe.

    Raises:
        ProviderHealthError: If installation/configuration/quota prevents a tiny
            provider request from completing.
    """
    # Tests frequently pass mocks that implement only the method under test.
    # Real providers live under the_architect.core.*_provider and are checked.
    if not provider.__class__.__module__.startswith("the_architect.core."):
        return

    effective_agent = agent_override if provider.supports_agents() else None
    cache_key = (
        provider.name,
        model_override or "",
        effective_agent or "",
        str(config_override.resolve()) if config_override is not None else "",
    )
    if cache_key in _HEALTH_CACHE:
        return

    if not provider.is_installed():
        raise ProviderHealthError(
            f"{provider.display_name} is not installed. Install it with: {provider.install_hint()}"
        )

    if not provider.has_any_models():
        raise ProviderHealthError(
            f"{provider.display_name} is installed but does not appear configured. "
            "Run the provider CLI once or configure its API key before using The Architect."
        )

    cmd = provider.build_command(_HEALTH_PROMPT, model_override, effective_agent)
    env = {
        **os.environ.copy(),
        "OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS": "900000",
    }
    env.update(provider.get_env_overrides(config_override))

    logger.debug(
        f"Running {provider.display_name} health check "
        f"model={model_override or 'default'} agent={effective_agent or 'default'}"
    )

    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(project_dir.resolve()),
            env=env,
            stdin=None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except TimeoutError as exc:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        raise ProviderHealthError(
            f"{provider.display_name} health check timed out after {int(timeout_seconds)}s"
        ) from exc
    except OSError as exc:
        raise ProviderHealthError(f"{provider.display_name} health check failed: {exc}") from exc

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    accumulated = _accumulate_probe_text(provider, stdout, stderr)
    combined = accumulated or "\n".join(part for part in (stdout, stderr) if part.strip())
    exit_code = int(process.returncode or 0)

    provider_error = detect_provider_error(combined, exit_code)
    if provider_error is not None and provider_error.kind in (
        ProviderErrorKind.UPDATE_REQUIRED,
        ProviderErrorKind.MISCONFIGURED,
        ProviderErrorKind.QUOTA_EXHAUSTED,
    ):
        raise ProviderHealthError(f"{provider_error.message}. {provider_error.action}")

    if exit_code != 0:
        snippet = combined.strip()[:300] or f"exit code {exit_code}"
        raise ProviderHealthError(f"{provider.display_name} health check failed: {snippet}")

    _HEALTH_CACHE.add(cache_key)


def _accumulate_probe_text(provider: ArchitectProvider, stdout: str, stderr: str) -> str:
    """Parse provider probe output into text suitable for error classification."""
    parts: list[str] = []
    for stream_text in (stdout, stderr):
        for raw_line in stream_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parsed = provider.parse_output_line(line)
            if parsed is None:
                parts.append(line)
                continue
            if parsed.display_lines:
                parts.append("\n".join(parsed.display_lines))
            elif parsed.rate_limit or parsed.model_not_found:
                parts.append(line)
    return "\n".join(parts)
