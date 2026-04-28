"""OpenCode configuration management — backward-compat shim.

All logic has moved to :mod:`the_architect.core.opencode_provider`.
This module re-exports every public symbol so existing imports continue
to work without modification.
"""

from __future__ import annotations

# Re-export everything from the new provider module so callers don't break.
from the_architect.core.opencode_provider import (
    OpenCodeProvider,
    _extract_agents_from_config_output,
    _extract_model_from_config_output,
    check_opencode_installed,
    check_user_opencode_configured,
    ensure_opencode_setup,
    find_user_opencode_config,
    get_opencode_version,
    get_resolved_architect_model,
    list_opencode_agents,
    list_opencode_models,
    opencode_has_any_models,
    read_opencode_info,
    write_architect_config,
    write_architect_prompts,
)

__all__ = [
    "OpenCodeProvider",
    "_extract_agents_from_config_output",
    "_extract_model_from_config_output",
    "check_opencode_installed",
    "check_user_opencode_configured",
    "ensure_opencode_setup",
    "find_user_opencode_config",
    "get_opencode_version",
    "get_resolved_architect_model",
    "list_opencode_agents",
    "list_opencode_models",
    "opencode_has_any_models",
    "read_opencode_info",
    "write_architect_config",
    "write_architect_prompts",
]
