"""Provider abstraction for The Architect.

Defines the ArchitectProvider protocol — a common interface that both
OpenCode and Claude Code CLI implementations satisfy.  All provider-specific
logic lives in the concrete implementations; the rest of The Architect calls
only this interface.

Auto-detection order (when preference is "auto"):
  1. If only OpenCode is installed → use OpenCode
  2. If only Claude Code is installed → use Claude Code
  3. If both are installed → return both so the CLI can prompt the user
  4. If neither is installed → raise ProviderNotFoundError

Preference values (from architect.toml ``provider`` field):
  "auto"        — detect as above
  "opencode"    — require OpenCode, raise if not found
  "claude-code" — require Claude Code, raise if not found
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from the_architect.config import ArchitectConfig


# ---------------------------------------------------------------------------
# Parsed event — unified output representation
# ---------------------------------------------------------------------------


@dataclass
class ParsedEvent:
    """A single parsed event from a provider's output stream.

    Both OpenCode (JSON events) and Claude Code (plain text) are normalised
    into this structure so ``stream_provider`` can handle both uniformly.

    Attributes:
        event_type: Normalised event type string.  Standard values:
            ``"text"``       — agent prose output
            ``"tool_use"``   — tool call (read, write, bash, …)
            ``"step_finish"`` — step completed (may carry token usage)
            ``"error"``      — error message
            ``"raw"``        — unrecognised / pass-through line
        display_lines: Human-readable lines to print to the terminal.
            Empty list for events that produce no visible output.
        tokens: Token usage extracted from this event, or ``None`` when
            the provider does not report token counts per-event.
        rate_limit: ``True`` when this event signals a provider rate limit
            (HTTP 429, "rate_limit", "overloaded", etc.).
        model_not_found: ``True`` when this event signals that the
            requested model is unavailable.
    """

    event_type: str = "raw"
    display_lines: list[str] = field(default_factory=list)
    tokens: object | None = None  # TokenUsage | None — avoid circular import
    rate_limit: bool = False
    model_not_found: bool = False
    cooldown_until: int = 0  # Unix timestamp from rate_limit_event.resetsAt (0 = not set)


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ArchitectProvider(Protocol):
    """Common interface for AI CLI backends (OpenCode, Claude Code).

    Every method must be safe to call even when the provider is not
    installed — ``is_installed()`` must always return a bool, and all
    other methods should return sensible empty/default values rather
    than raising when the binary is absent.
    """

    # ── Identity ──────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        """Short identifier, e.g. ``"opencode"`` or ``"claude-code"``."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name, e.g. ``"OpenCode"`` or ``"Claude Code"``."""
        ...

    @property
    def binary_name(self) -> str:
        """CLI binary name, e.g. ``"opencode"`` or ``"claude"``."""
        ...

    # ── Installation checks ───────────────────────────────────────────────

    def is_installed(self) -> bool:
        """Return True if the provider's binary is on PATH."""
        ...

    def get_version(self) -> str:
        """Return the provider's version string, or ``"unknown"``."""
        ...

    def has_any_models(self) -> bool:
        """Return True if the provider is usable (has at least one model/API key)."""
        ...

    def install_hint(self) -> str:
        """Return a human-readable install command for this platform."""
        ...

    # ── Model / agent discovery ───────────────────────────────────────────

    def list_models(self) -> list[str]:
        """Return available model identifiers.  Empty list on failure."""
        ...

    def list_agents(self, project_dir: Path) -> list[str]:
        """Return primary agent names available for task execution.

        Excludes internal agents (architect, reviewer, compaction, etc.)
        that are not suitable as the main execution agent.
        """
        ...

    def get_resolved_model(self, project_dir: Path, agent_name: str = "architect") -> str:
        """Return the model that will actually be used for ``agent_name``.

        Returns empty string when the model cannot be determined.
        """
        ...

    # ── Config / setup ────────────────────────────────────────────────────

    def find_user_config(self, project_dir: Path) -> Path | None:
        """Find the user's provider config file.

        Returns ``None`` when no config is found (provider may still work
        via built-in defaults / env vars).
        """
        ...

    def ensure_setup(self, project_dir: Path, config: ArchitectConfig) -> Path:
        """Ensure The Architect's planning config and prompts are ready.

        Writes prompts to ``.architect/prompts/`` and any provider-specific
        planning config.  Always safe to call — idempotent.

        Returns the path to the planning config (or prompts dir for
        providers that don't use a separate config file).
        """
        ...

    # ── Command building ──────────────────────────────────────────────────

    def build_command(
        self,
        instruction: str,
        model_override: str | None = None,
        agent_override: str | None = None,
    ) -> list[str]:
        """Build the subprocess command list for running an instruction.

        Args:
            instruction: The full instruction string to pass to the CLI.
            model_override: Optional model to use (overrides config).
            agent_override: Optional agent name (only meaningful for
                providers that support named agents, e.g. OpenCode).

        Returns:
            List of command components ready for ``subprocess`` / ``asyncio``.
        """
        ...

    def get_env_overrides(self, config_override: Path | None = None) -> dict[str, str]:
        """Return extra environment variables to set for this provider run.

        Args:
            config_override: When set, the provider should use this config
                file instead of the user's default.  For OpenCode this
                becomes ``OPENCODE_CONFIG``; Claude Code ignores it.

        Returns:
            Dict of env var name → value.  May be empty.
        """
        ...

    # ── Output parsing ────────────────────────────────────────────────────

    def parse_output_line(self, line: str) -> ParsedEvent | None:
        """Parse a single line from the provider's stdout.

        Args:
            line: A raw line from the provider's stdout (no trailing newline).

        Returns:
            A :class:`ParsedEvent` if the line carries useful information,
            or ``None`` if the line should be printed as-is (raw pass-through).
        """
        ...

    def supports_agents(self) -> bool:
        """Return True if this provider supports named agent selection.

        OpenCode supports ``--agent build``.
        Claude Code does not have a named-agent system.
        """
        ...

    def supports_json_output(self) -> bool:
        """Return True if this provider emits structured JSON events.

        OpenCode: True (``--format json``).
        Claude Code: False (plain text output).
        """
        ...

    def supports_free_tier(self) -> bool:
        """Return True if this provider can use OpenRouter free-tier models.

        Free tier requires OpenCode + an OpenRouter API key (or at least one
        ``openrouter/`` model visible in the user's model list).

        Claude Code talks directly to Anthropic's API and never supports
        OpenRouter free-tier rotation.
        """
        ...


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ProviderNotFoundError(Exception):
    """Raised when no supported provider CLI is installed."""

    pass


class ProviderMisconfiguredError(Exception):
    """Raised when a provider is installed but not usable (no API key, etc.)."""

    pass


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_provider(preference: str = "auto") -> ArchitectProvider:
    """Detect and return the appropriate provider based on preference.

    This function returns a single provider.  When both are installed and
    preference is ``"auto"``, it returns OpenCode (the primary provider).
    The CLI layer calls :func:`detect_available_providers` when it needs
    to offer the user a choice.

    Args:
        preference: One of ``"auto"``, ``"opencode"``, or ``"claude-code"``.

    Returns:
        An :class:`ArchitectProvider` instance.

    Raises:
        ProviderNotFoundError: When the requested provider is not installed.
        ValueError: When ``preference`` is not a recognised value.
    """
    from the_architect.core.claude_code_provider import ClaudeCodeProvider
    from the_architect.core.opencode_provider import OpenCodeProvider

    oc = OpenCodeProvider()
    cc = ClaudeCodeProvider()

    pref = preference.lower().strip()

    if pref == "opencode":
        if not oc.is_installed():
            raise ProviderNotFoundError(
                f"OpenCode is not installed. Install it with: {oc.install_hint()}"
            )
        return oc

    if pref == "claude-code":
        if not cc.is_installed():
            raise ProviderNotFoundError(
                f"Claude Code is not installed. Install it with: {cc.install_hint()}"
            )
        return cc

    if pref == "auto":
        oc_ok = oc.is_installed()
        cc_ok = cc.is_installed()

        if oc_ok:
            return oc  # OpenCode preferred when both present
        if cc_ok:
            return cc
        raise ProviderNotFoundError(
            "No supported AI CLI found. Install one of:\n"
            f"  OpenCode:    {oc.install_hint()}\n"
            f"  Claude Code: {cc.install_hint()}"
        )

    raise ValueError(
        f"Unknown provider preference: {preference!r}. "
        "Valid values: 'auto', 'opencode', 'claude-code'."
    )


def detect_available_providers() -> list[ArchitectProvider]:
    """Return all installed providers, in preference order.

    Used by the CLI when it needs to offer the user a choice between
    multiple installed providers.

    Returns:
        List of installed :class:`ArchitectProvider` instances.
        OpenCode comes first when both are installed.
        Empty list when nothing is installed.
    """
    from the_architect.core.claude_code_provider import ClaudeCodeProvider
    from the_architect.core.opencode_provider import OpenCodeProvider

    available: list[ArchitectProvider] = []
    oc = OpenCodeProvider()
    cc = ClaudeCodeProvider()

    if oc.is_installed():
        available.append(oc)
    if cc.is_installed():
        available.append(cc)

    return available
