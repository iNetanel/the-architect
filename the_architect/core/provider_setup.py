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
    "execution.md",
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
    for agent_name in ("architect", "intelligence", "reviewer"):
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


def uses_architect_agent_routing(provider: ArchitectProvider) -> bool:
    """Return True when architect/intelligence/reviewer meta-roles should use
    OpenCode's named-agent routing (``--agent <role>`` + ``.architect/architect.json``).

    ``provider.supports_agents()`` alone is not a sufficient check: Claude Code
    (and potentially other future providers) also supports a ``--agent`` flag,
    but only for real execution-agent selection with its own agent names
    (``claude``, ``Explore``, ``general-purpose``, ``Plan``,
    ``statusline-setup``) — never OpenCode's ``"architect"``/``"intelligence"``/
    ``"reviewer"``. Only OpenCode's ``.architect/architect.json`` config makes
    those specific names resolvable, so provider identity — not agent-flag
    support — is what must gate this decision.

    Every call site that decides whether to pass ``agent_override="architect"``
    (or ``"intelligence"`` / ``"reviewer"``) must use this single check so the
    behaviour stays consistent across ``core/planner.py``,
    ``core/intelligence.py``, ``core/retrospective.py``, and ``core/circuit.py``
    — and automatically stays correct for any future provider.
    """
    return provider.supports_agents() and provider_uses_architect_config(provider)


def prepend_provider_role_prompt(
    provider: ArchitectProvider,
    instruction: str,
    prompt_getter_name: str,
) -> str:
    """Prepend a packaged provider role prompt when named-agent routing isn't used.

    Used for the architect/intelligence/reviewer meta-roles on any provider
    that doesn't route through OpenCode's ``.architect/architect.json``
    named-agent config (Claude Code, Codex CLI, Gemini CLI, and any future
    provider). Duck-typed via ``prompt_getter_name`` (e.g.
    ``"get_architect_prompt"``, ``"get_reviewer_prompt"``) so this works with
    any provider implementing the corresponding method — never hardcoded to
    one provider class via ``isinstance``.

    Args:
        provider: Active provider instance.
        instruction: The instruction text to prepend the role prompt to.
        prompt_getter_name: Name of the zero-arg provider method returning
            the packaged role prompt text (e.g. ``"get_architect_prompt"``).

    Returns:
        ``instruction`` unchanged if the provider has no such method, the
        method isn't callable, or it returns an empty/blank prompt.
        Otherwise, the role prompt followed by ``instruction``, separated by
        a ``---`` divider.
    """
    if prompt_getter_name not in dir(provider):
        return instruction
    getter = getattr(provider, prompt_getter_name, None)
    if not callable(getter):
        return instruction
    prompt = str(getter()).strip()
    if not prompt:
        return instruction
    return f"{prompt}\n\n---\n\n{instruction}"
