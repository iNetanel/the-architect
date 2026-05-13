"""Shared provider setup hardening helpers."""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from the_architect.config import ArchitectConfig
from the_architect.core.provider import ArchitectProvider

_REQUIRED_PROMPT_FILES = (
    "architect.md",
    "intelligence.md",
    "reviewer.md",
    "execution-protocol.md",
)


def ensure_provider_setup(
    provider: ArchitectProvider,
    project_dir: Path,
    config: ArchitectConfig,
    *,
    allow_existing_fallback: bool = True,
) -> Path | None:
    """Ensure provider setup, reusing verified setup after resource-loader glitches.

    ``importlib.resources`` can occasionally surface packaged prompt directories
    as a ``MultiplexedPath`` and raise ``NotADirectoryError`` while writing the
    same setup files that already exist on disk. Retrying the task cannot fix
    that class of failure, so all orchestration stages share this verified
    fallback instead of letting execution/replan fail before the provider runs.
    """
    try:
        return provider.ensure_setup(project_dir, config)
    except NotADirectoryError as exc:
        if not allow_existing_fallback or "MultiplexedPath" not in str(exc):
            raise
        if not existing_provider_setup_is_usable(provider, project_dir):
            raise
        logger.warning(
            "Provider setup hit importlib.resources MultiplexedPath issue; "
            "reusing existing .architect provider setup"
        )
        return None


def existing_provider_setup_is_usable(provider: ArchitectProvider, project_dir: Path) -> bool:
    """Return True when existing provider setup files are complete enough to reuse."""
    prompts_dir = project_dir / ".architect" / "prompts"
    if not prompts_dir.is_dir():
        return False
    for filename in _REQUIRED_PROMPT_FILES:
        prompt_file = prompts_dir / filename
        try:
            if not prompt_file.is_file() or not prompt_file.read_text(encoding="utf-8").strip():
                return False
        except OSError:
            return False

    if not provider_uses_architect_config(provider):
        return True

    architect_config = project_dir / ".architect" / "architect.json"
    try:
        data = json.loads(architect_config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    agents = data.get("agent")
    if not isinstance(agents, dict):
        return False
    for agent_name in ("architect", "reviewer"):
        agent_cfg = agents.get(agent_name)
        if not isinstance(agent_cfg, dict):
            return False
        prompt_value = agent_cfg.get("prompt")
        if not isinstance(prompt_value, str) or not prompt_value.strip():
            return False
    return True


def provider_uses_architect_config(provider: ArchitectProvider) -> bool:
    """Return True when review/planning routing depends on .architect/architect.json."""
    return getattr(provider, "name", "") == "opencode"
