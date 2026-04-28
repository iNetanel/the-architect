"""Claude Code CLI provider implementation for The Architect.

Wraps all Claude Code-specific logic: binary detection, command building,
plain-text output parsing, and planning setup.

Key differences from OpenCode:
  - Binary: ``claude`` (not ``opencode``)
  - No named-agent system — roles injected as prompt prefixes
  - No JSON event stream — plain text output
  - Model list extracted from the Claude Code binary (instant) or
    via ``claude models`` API call (slow fallback)
  - Agent list via ``claude agents`` (plain text output)
  - No config file equivalent — uses CLAUDE.md for project context
  - Non-interactive mode: ``claude --print`` / ``-p``
  - Permissions: ``--dangerously-skip-permissions`` (same flag name)
  - Token counts: not available per-event (no structured output)
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from the_architect.config import ArchitectConfig
    from the_architect.core.provider import ParsedEvent


# ---------------------------------------------------------------------------
# Fallback model list — only used when ``claude models`` CLI call fails
# ---------------------------------------------------------------------------

_FALLBACK_CLAUDE_MODELS: list[str] = [
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "claude-opus-4-5",
    "claude-sonnet-4-5",
    "claude-haiku-3-5",
    "claude-opus-4",
    "claude-sonnet-4",
    "claude-3-7-sonnet-20250219",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229",
]

# Internal agents that should never appear in the execution dropdown
_INTERNAL_AGENTS = {"architect", "reviewer", "compaction", "summary", "title", "plan"}


# ---------------------------------------------------------------------------
# Claude Code provider
# ---------------------------------------------------------------------------


class ClaudeCodeProvider:
    """Provider implementation for the Claude Code CLI.

    Claude Code is Anthropic's official CLI for Claude models.
    It does not have a named-agent system or JSON event output —
    The Architect handles both differences transparently.
    """

    def __init__(self) -> None:
        self._resolved_model_cache: dict[str, str] = {}
        self._binary_models_cache: list[str] | None = None
        # Cache the resolved version string for the lifetime of this
        # provider instance.  ``get_version()`` spawns ``claude --version``
        # which can take hundreds of milliseconds — callers in hot paths
        # (e.g. prompt_toolkit render callbacks) must never pay that cost
        # on every repaint.  A fresh CLI invocation creates a new instance,
        # so the user sees up-to-date versions after re-running ``architect``.
        self._version_cache: str | None = None

    # ── Identity ──────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        """Short identifier."""
        return "claude-code"

    @property
    def display_name(self) -> str:
        """Human-readable name."""
        return "Claude Code"

    @property
    def binary_name(self) -> str:
        """CLI binary name."""
        return "claude"

    # ── Installation checks ───────────────────────────────────────────────

    def is_installed(self) -> bool:
        """Return True if the claude binary is on PATH."""
        return shutil.which("claude") is not None

    def get_version(self) -> str:
        """Return the claude version string, or ``'unknown'``.

        The result is cached on the instance after the first successful
        call so subsequent calls (e.g. from prompt_toolkit render
        callbacks) do not re-spawn ``claude --version``.
        """
        if self._version_cache is not None:
            return self._version_cache
        try:
            result = subprocess.run(
                ["claude", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                value = result.stdout.strip() or result.stderr.strip() or "unknown"
            else:
                value = "unknown"
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            value = "unknown"
        self._version_cache = value
        return value

    def has_any_models(self) -> bool:
        """Return True if Claude Code is installed and likely usable.

        Checks that the ``claude`` binary is present and exits cleanly
        with ``--version``.  Model availability is determined at runtime
        by the Claude Code CLI itself.
        """
        if not self.is_installed():
            return False
        try:
            result = subprocess.run(
                ["claude", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            return False

    def install_hint(self) -> str:
        """Return the Claude Code install command for this platform."""
        if shutil.which("npm"):
            return "npm install -g @anthropic-ai/claude-code"
        return "see https://docs.anthropic.com/en/docs/claude-code"

    # ── Model / agent discovery ───────────────────────────────────────────

    def list_models(self) -> list[str]:
        """Return model identifiers from the user's Claude Code installation.

        Resolution order (fastest first):
        1. **Binary extraction** — scan the Claude Code executable for
           embedded model IDs (~0.1s, no network call).  The binary ships
           with the full list of known models baked in.
        2. **``claude models`` API call** — queries Anthropic's API to
           return only the models the user's account has access to
           (~8s, network required).  Used only when binary extraction
           fails or returns no results.
        3. **Static fallback list** — hardcoded list of known models.
           Only used when both methods above fail.

        Returns:
            List of model identifier strings, sorted alphabetically.
        """
        # 1. Fast path: extract models from the binary itself
        if self._binary_models_cache is None:
            self._binary_models_cache = _extract_models_from_binary()
        if self._binary_models_cache:
            return list(self._binary_models_cache)

        # 2. Slow path: claude models API call
        try:
            result = subprocess.run(
                ["claude", "models"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                models = _parse_claude_models_output(result.stdout)
                if models:
                    self._binary_models_cache = sorted(models)
                    return list(self._binary_models_cache)

            logger.debug("claude models returned no parseable models, using fallback list")
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError) as exc:
            logger.debug(f"claude models call failed: {exc!r}, using fallback list")

        # 3. Last resort: static fallback
        return list(_FALLBACK_CLAUDE_MODELS)

    def list_agents(self, project_dir: Path) -> list[str]:
        """Return execution agent names from the user's Claude Code CLI.

        Calls ``claude agents`` to discover what agents the user's Claude
        Code installation has configured.  Falls back to reading CLAUDE.md
        for custom agent definitions, then to ``["build"]`` as a default.

        The ``claude agents`` output is plain text::

            4 active agents

            Built-in agents:
              Explore · haiku
              general-purpose · inherit

        We extract agent names from lines matching ``name · model``.

        Args:
            project_dir: The project root directory.

        Returns:
            List of agent name strings (may be empty).
        """
        # Primary: claude agents CLI
        try:
            result = subprocess.run(
                ["claude", "agents"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                agents = _parse_claude_agents_output(result.stdout)
                if agents:
                    return sorted(a for a in agents if a not in _INTERNAL_AGENTS)
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError) as exc:
            logger.debug(f"claude agents call failed: {exc!r}")

        # Fallback: read CLAUDE.md for custom agent hints
        agents = _read_claude_md_agents(project_dir)
        if agents:
            return sorted(a for a in agents if a not in _INTERNAL_AGENTS)

        # Last resort: offer "build" as the standard execution agent name
        # (matches the convention used in AGENTS.md / opencode.json)
        return ["build"]

    def get_resolved_model(self, project_dir: Path, agent_name: str = "architect") -> str:
        """Return the model Claude Code will use.

        Resolution order:
        1. ``ANTHROPIC_MODEL`` env var
        2. ``CLAUDE_MODEL`` env var (alternative)
        3. CLAUDE.md model hint
        4. Binary extraction — pick the newest non-dated model ID from
           the Claude Code executable (instant, no network call)
        5. ``claude models`` API call — parses the default model line
           (slow, ~8s, only used when binary extraction fails)

        Results are cached per project directory so slow lookups
        only happen once per run.

        Args:
            project_dir: The project root directory.
            agent_name: Ignored for Claude Code (no named agents).

        Returns:
            Model identifier string, or empty string if not determinable.
        """
        # Check cache first (avoids repeated lookups).
        # "_stream" is populated by parse_output_line when it sees the
        # stream-json ``system`` init event — use it as the highest-priority
        # source so the model name from an active run is always reflected.
        if "_stream" in self._resolved_model_cache:
            return self._resolved_model_cache["_stream"]

        cache_key = str(project_dir)
        if cache_key in self._resolved_model_cache:
            return self._resolved_model_cache[cache_key]

        import os

        # 1. ANTHROPIC_MODEL env var
        env_model = os.environ.get("ANTHROPIC_MODEL", "").strip()
        if env_model:
            self._resolved_model_cache[cache_key] = env_model
            return env_model

        # 2. CLAUDE_MODEL env var (alternative)
        env_model2 = os.environ.get("CLAUDE_MODEL", "").strip()
        if env_model2:
            self._resolved_model_cache[cache_key] = env_model2
            return env_model2

        # 3. CLAUDE.md model hint
        model = _read_claude_md_model(project_dir)
        if model:
            self._resolved_model_cache[cache_key] = model
            return model

        # 4. Fast path: pick default model from binary-extracted list
        #    Prefer the newest non-dated model ID (e.g. "claude-sonnet-4-6"
        #    over "claude-sonnet-4-6-20251101").  These short IDs are the
        #    aliases Claude Code uses by default.
        if self._binary_models_cache is None:
            self._binary_models_cache = _extract_models_from_binary()
        if self._binary_models_cache:
            default = _pick_default_model(self._binary_models_cache)
            if default:
                self._resolved_model_cache[cache_key] = default
                return default

        # 5. Slow path: claude models API call
        try:
            result = subprocess.run(
                ["claude", "models"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                default_model = _parse_claude_default_model(result.stdout)
                if default_model:
                    self._resolved_model_cache[cache_key] = default_model
                    return default_model
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            pass

        return ""

    # ── Config / setup ────────────────────────────────────────────────────

    def find_user_config(self, project_dir: Path) -> Path | None:
        """Find the user's Claude Code config (CLAUDE.md).

        Checks project-local CLAUDE.md first, then global ~/.claude/CLAUDE.md.

        Args:
            project_dir: The project root directory.

        Returns:
            Path to CLAUDE.md, or None if not found.
        """
        # Project-local CLAUDE.md
        local = project_dir / "CLAUDE.md"
        if local.exists():
            logger.debug(f"Found project-local CLAUDE.md: {local}")
            return local

        # Global ~/.claude/CLAUDE.md
        global_claude = Path.home() / ".claude" / "CLAUDE.md"
        if global_claude.exists():
            logger.debug(f"Found global CLAUDE.md: {global_claude}")
            return global_claude

        return None

    def ensure_setup(self, project_dir: Path, config: ArchitectConfig) -> Path:
        """Ensure The Architect's prompts are ready for Claude Code.

        Writes prompts to ``.architect/prompts/``.  Claude Code does not
        use a separate planning config file — the architect/reviewer
        prompts are injected directly into the instruction string at
        runtime.

        Args:
            project_dir: The project root directory.
            config: The ArchitectConfig instance.

        Returns:
            Path to ``.architect/prompts/`` directory.
        """
        if isinstance(project_dir, str):
            project_dir = Path(project_dir)

        project_dir = project_dir.resolve()
        self._write_architect_prompts(project_dir)

        return project_dir / ".architect" / "prompts"

    def _write_architect_prompts(self, project_dir: Path) -> None:
        """Write architect.md, reviewer.md, and execution-protocol.md to .architect/prompts/."""
        import importlib.resources as resources

        prompts_dir = project_dir / ".architect" / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)

        package_prompts = resources.files("the_architect.resources.prompts")

        for filename in ("architect.md", "reviewer.md", "execution-protocol.md"):
            source = package_prompts / filename
            target = prompts_dir / filename
            content = source.read_text(encoding="utf-8")
            target.write_text(content, encoding="utf-8")
            logger.debug(f"Written prompt: {target}")

    def get_architect_prompt(self) -> str:
        """Return the architect agent prompt text.

        Used to prepend the architect role to planning instructions
        since Claude Code has no named-agent system.

        Returns:
            Full architect.md prompt text.
        """
        import importlib.resources as resources

        package_prompts = resources.files("the_architect.resources.prompts")
        source = package_prompts / "architect.md"
        return source.read_text(encoding="utf-8")

    def get_reviewer_prompt(self) -> str:
        """Return the reviewer agent prompt text.

        Used to prepend the reviewer role to retrospective instructions.

        Returns:
            Full reviewer.md prompt text.
        """
        import importlib.resources as resources

        package_prompts = resources.files("the_architect.resources.prompts")
        source = package_prompts / "reviewer.md"
        return source.read_text(encoding="utf-8")

    # ── Command building ──────────────────────────────────────────────────

    def build_command(
        self,
        instruction: str,
        model_override: str | None = None,
        agent_override: str | None = None,
    ) -> list[str]:
        """Build the claude CLI command for non-interactive execution.

        Uses ``--print`` with ``--output-format stream-json --verbose`` so
        Claude Code emits a structured JSON event stream.  This enables
        The Architect to capture token usage and model name from the
        ``result`` and ``system`` events respectively.

        ``--verbose`` is required by Claude Code when using stream-json
        output format in ``--print`` mode.

        Note: ``agent_override`` is ignored for Claude Code — agent roles
        are injected as prompt prefixes in the instruction string itself.

        Args:
            instruction: The full instruction string to pass to claude.
            model_override: Optional model name to pass via --model flag.
            agent_override: Ignored (Claude Code has no named agents).

        Returns:
            List of command components ready for subprocess execution.
        """
        claude_bin = shutil.which("claude") or "claude"
        cmd: list[str] = [
            claude_bin,
            "--dangerously-skip-permissions",
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
        ]

        if model_override:
            cmd.extend(["--model", model_override])

        # Pass instruction as the final positional argument
        cmd.append(instruction)

        return cmd

    def get_env_overrides(self, config_override: Path | None = None) -> dict[str, str]:
        """Return extra environment variables for this Claude Code run.

        ``config_override`` is ignored — Claude Code does not use a
        config file path env var like OpenCode's ``OPENCODE_CONFIG``.

        Returns:
            Empty dict (Claude Code uses ANTHROPIC_API_KEY from the
            environment, which is already inherited from the parent process).
        """
        return {}

    # ── Output parsing ────────────────────────────────────────────────────

    def parse_output_line(self, line: str) -> ParsedEvent | None:
        """Parse a single line from Claude Code's stream-json output.

        Claude Code is invoked with ``--output-format stream-json --verbose``
        which emits one JSON object per line.  We handle four event types:

        - ``system`` (subtype ``init``) — carries the model name; no display
        - ``assistant`` — carries the agent's text content for display
        - ``result`` (subtype ``success``/``error``) — carries cumulative
          token usage; no display text
        - ``rate_limit_event`` — signals provider rate limiting

        Any line that is not valid JSON is treated as plain text (fallback
        for unexpected output).

        Args:
            line: A single raw line from claude's stdout.

        Returns:
            A ParsedEvent, or None for empty/silent lines.
        """
        import json as _json

        from the_architect.core.provider import ParsedEvent
        from the_architect.core.runner import TokenUsage

        stripped = line.strip()
        if not stripped:
            return None

        # ── Try to parse as JSON (stream-json mode) ─────────────────────
        try:
            event = _json.loads(stripped)
        except (ValueError, _json.JSONDecodeError):
            # Not JSON — treat as plain text (fallback / legacy)
            rate_limit = _is_rate_limit_text(stripped)
            model_not_found = _is_model_not_found_text(stripped)
            return ParsedEvent(
                event_type="text",
                display_lines=[stripped],
                tokens=None,
                rate_limit=rate_limit,
                model_not_found=model_not_found,
            )

        etype = event.get("type", "")
        display_lines: list[str] = []
        tokens: TokenUsage | None = None
        rate_limit = False
        model_not_found = False

        if etype == "system":
            # Init event — carries model name; cache it for get_resolved_model
            # No display text needed.
            model = event.get("model", "")
            if model:
                # Update the resolved model cache so TaskResult.model is
                # populated even when no explicit model override was set.
                self._resolved_model_cache["_stream"] = model
            return ParsedEvent(event_type=etype, display_lines=[], tokens=None)

        elif etype == "assistant":
            # Agent message — may contain text, thinking, or tool_use content parts.
            # Only "text" parts are displayed; "thinking" and "tool_use" are silent.
            message = event.get("message", {})
            if isinstance(message, dict):
                for part in message.get("content", []):
                    if isinstance(part, dict) and part.get("type") == "text":
                        text = (part.get("text") or "").strip()
                        if text:
                            display_lines.extend(text.split("\n"))
                        break
                    # "thinking" and "tool_use" content parts: skip silently
            # Claude Code sets error="rate_limit" on the assistant event when
            # the request is rejected before the model runs.
            err_field = str(event.get("error") or "")
            if err_field and _is_rate_limit_text(err_field):
                rate_limit = True
            # Return here so assistant events with no text (thinking/tool_use only)
            # are silently consumed — never fall through to the raw-print path.
            if not display_lines:
                return ParsedEvent(
                    event_type=etype, display_lines=[], tokens=None, rate_limit=rate_limit
                )

        elif etype == "result":
            # Final result event — carries cumulative token usage
            usage = event.get("usage", {})
            if isinstance(usage, dict):
                tokens = TokenUsage(
                    input_tokens=int(usage.get("input_tokens", 0) or 0),
                    output_tokens=int(usage.get("output_tokens", 0) or 0),
                    cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
                    cache_write_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
                )
            # HTTP 429/529 in api_error_status means rate-limited at the API level
            api_status = event.get("api_error_status")
            if api_status in (429, 529):
                rate_limit = True
            # Check for error subtype or is_error flag
            if event.get("subtype") == "error" or event.get("is_error"):
                error_msg = event.get("result", "") or event.get("error", "")
                if error_msg:
                    display_lines.append(f"Error: {error_msg}")
                    if not rate_limit:
                        rate_limit = _is_rate_limit_text(str(error_msg))
                    model_not_found = _is_model_not_found_text(str(error_msg))
            # No display text for successful result events
            return ParsedEvent(
                event_type=etype,
                display_lines=display_lines,
                tokens=tokens,
                rate_limit=rate_limit,
                model_not_found=model_not_found,
            )

        elif etype == "rate_limit_event":
            # Claude Code emits this event at the start of every request to report
            # the current rate-limit state.  Only treat it as a hard rate limit when
            # status == "rejected" — "allowed" is purely informational.
            rate_limit_info = event.get("rate_limit_info", {})
            status = rate_limit_info.get("status", "") if isinstance(rate_limit_info, dict) else ""
            is_rejected = status == "rejected"
            resets_at = 0
            if isinstance(rate_limit_info, dict):
                try:
                    resets_at = int(rate_limit_info.get("resetsAt") or 0)
                except (TypeError, ValueError):
                    resets_at = 0
            return ParsedEvent(
                event_type=etype,
                display_lines=[],
                tokens=None,
                rate_limit=is_rejected,
                cooldown_until=resets_at if is_rejected else 0,
            )

        elif etype == "tool_use":
            # Tool call events — display the tool name
            tool_name = event.get("name", "")
            if tool_name:
                display_lines.append(f"→ {tool_name}")

        elif etype in ("tool_result", "user"):
            # tool_result: tool output fed back to the model — silent.
            # user: the user turn (injected tool results) — silent.
            return ParsedEvent(event_type=etype, display_lines=[])

        else:
            # Unknown/future event type — consume silently, never print raw JSON.
            return ParsedEvent(event_type=etype, display_lines=[])

        # Detect rate-limit / model-not-found in any remaining display text
        if not rate_limit and display_lines:
            combined = " ".join(display_lines)
            rate_limit = _is_rate_limit_text(combined)
            model_not_found = _is_model_not_found_text(combined)

        if not display_lines:
            return ParsedEvent(event_type=etype, display_lines=[], tokens=tokens)

        return ParsedEvent(
            event_type=etype,
            display_lines=display_lines,
            tokens=tokens,
            rate_limit=rate_limit,
            model_not_found=model_not_found,
        )

    def supports_agents(self) -> bool:
        """Claude Code does not support named agent selection."""
        return False

    def supports_json_output(self) -> bool:
        """Claude Code outputs plain text, not JSON events."""
        return False

    def supports_free_tier(self) -> bool:
        """Claude Code never supports OpenRouter free-tier rotation.

        Claude Code talks directly to Anthropic's API.  There is no
        OpenRouter integration and no free-tier model rotation available.

        Returns:
            Always False.
        """
        return False


# ---------------------------------------------------------------------------
# CLAUDE.md helpers
# ---------------------------------------------------------------------------


def _read_claude_md_agents(project_dir: Path) -> list[str]:
    """Extract agent names from CLAUDE.md if present.

    Looks for a section like:
        ## Agents
        - build
        - frontend

    Args:
        project_dir: The project root directory.

    Returns:
        List of agent names, or empty list.
    """
    import re

    claude_md = project_dir / "CLAUDE.md"
    if not claude_md.exists():
        return []

    try:
        content = claude_md.read_text(encoding="utf-8")
    except OSError:
        return []

    # Look for an "## Agents" section
    match = re.search(
        r"##\s+Agents\s*\n((?:[-*]\s+\S+\s*\n)+)",
        content,
        re.IGNORECASE,
    )
    if not match:
        return []

    agents = []
    for line in match.group(1).splitlines():
        line = line.strip().lstrip("-*").strip()
        if line:
            agents.append(line)

    return agents


def _read_claude_md_model(project_dir: Path) -> str:
    """Extract a model hint from CLAUDE.md if present.

    Looks for a line like:
        Model: claude-sonnet-4-5

    Args:
        project_dir: The project root directory.

    Returns:
        Model string, or empty string.
    """
    import re

    claude_md = project_dir / "CLAUDE.md"
    if not claude_md.exists():
        return ""

    try:
        content = claude_md.read_text(encoding="utf-8")
    except OSError:
        return ""

    match = re.search(r"^Model:\s*(\S+)", content, re.MULTILINE | re.IGNORECASE)
    if match:
        return match.group(1).strip()

    return ""


# ---------------------------------------------------------------------------
# CLI output parsing — ``claude models`` and ``claude agents``
# ---------------------------------------------------------------------------


def _parse_claude_models_output(stdout: str) -> list[str]:
    """Extract model IDs from ``claude models`` markdown table output.

    The output looks like::

        The current Claude models available:

        | Model | ID |
        |---|---|
        | **Opus 4.7** | `claude-opus-4-7` |
        | **Sonnet 4.6** | `claude-sonnet-4-6` |

    We extract backtick-wrapped strings from the ID column.

    Args:
        stdout: Raw stdout from ``claude models``.

    Returns:
        List of model ID strings (may be empty if parsing fails).
    """
    import re

    # Match backtick-wrapped model IDs in the table rows
    # Pattern: | ... | `model-id` |
    models: list[str] = []
    for line in stdout.splitlines():
        # Look for lines that contain backtick-wrapped IDs in a table row
        matches = re.findall(r"`([^`]+)`", line)
        for m in matches:
            # Only include strings that look like model IDs
            # (contain "claude" or follow Anthropic's naming convention)
            if "claude" in m.lower():
                models.append(m)

    return models


def _parse_claude_agents_output(stdout: str) -> list[str]:
    """Extract agent names from ``claude agents`` plain text output.

    The output looks like::

        4 active agents

        Built-in agents:
          Explore · haiku
          general-purpose · inherit

    Agent lines are indented with spaces and contain a unicode middle dot
    (·) separating the name from the model.  We only match indented lines
    to avoid false positives from header lines like "Built-in agents:".

    Args:
        stdout: Raw stdout from ``claude agents``.

    Returns:
        List of agent name strings (may be empty if parsing fails).
    """
    import re

    agents: list[str] = []
    for line in stdout.splitlines():
        # Agent lines are indented (start with whitespace) and contain
        # a middle dot separator: "  Explore · haiku"
        match = re.match(r"^\s+(\S+(?:-\S+)*)\s*·\s*\S+", line)
        if match:
            agents.append(match.group(1))

    return agents


def _parse_claude_default_model(stdout: str) -> str:
    """Extract the current default model from ``claude models`` output.

    The output may include lines like::

        You're currently running on **Sonnet 4.6**.
        Default to Sonnet 4.6 or Opus 4.7 for most tasks.

    We map the display name back to its model ID using the table rows
    in the same output.  If the mapping fails, we return the first
    model ID from the table as a reasonable default.

    Args:
        stdout: Raw stdout from ``claude models``.

    Returns:
        Model identifier string, or empty string if parsing fails.
    """
    import re

    # Parse all model IDs from the table (same logic as _parse_claude_models_output)
    model_ids: list[str] = []
    for line in stdout.splitlines():
        matches = re.findall(r"`([^`]+)`", line)
        for m in matches:
            if "claude" in m.lower():
                model_ids.append(m)

    # Try to find the current/default model from the prose line.
    # Format 1: "You're currently running on **Sonnet 4.6**."
    # Format 2: "Default to Sonnet 4.6 or Opus 4.7 for most tasks."
    display_name = ""

    # Format 1: bold-wrapped name after "currently running on"
    current_match = re.search(
        r"currently (?:running|on)(?:\s+model)?\s+(?:on\s+)?\*\*(.+?)\*\*", stdout
    )
    if current_match:
        display_name = current_match.group(1).strip()

    # Format 2: "Default to Sonnet 4.6" (first name after "Default to")
    if not display_name:
        default_match = re.search(r"Default\s+to\s+(\S+(?:\s+\d[\d.]*)?)", stdout, re.IGNORECASE)
        if default_match:
            display_name = default_match.group(1).strip()

    # Map display name to model ID
    if display_name:
        # Convert "Sonnet 4.6" → "sonnet-4-6" pattern to search in model IDs
        name_lower = display_name.lower()
        # "sonnet 4.6" → "sonnet-4-6"
        pattern = name_lower.replace(" ", "-").replace(".", "-")
        # Collapse multiple dashes: "sonnet--4--6" → "sonnet-4-6"
        while "--" in pattern:
            pattern = pattern.replace("--", "-")
        for mid in model_ids:
            if pattern in mid.lower():
                return mid

    # Fallback: return the first model ID from the table
    if model_ids:
        return model_ids[0]

    return ""


# ---------------------------------------------------------------------------
# Binary model extraction — scan the Claude Code executable for model IDs
# ---------------------------------------------------------------------------


def _find_claude_binary() -> Path | None:
    """Locate the Claude Code binary on the system.

    Uses ``shutil.which`` to find ``claude`` on PATH, then resolves
    any symlinks to find the actual executable.  Returns ``None`` if
    the binary cannot be found or is not a regular file.

    This is fully platform-agnostic — no hardcoded paths.

    Returns:
        Path to the Claude Code binary, or None.
    """
    binary_name = shutil.which("claude")
    if not binary_name:
        return None
    try:
        resolved = Path(binary_name).resolve()
        if resolved.is_file():
            return resolved
    except OSError:
        pass
    return None


def _extract_models_from_binary() -> list[str]:
    """Extract model IDs from the Claude Code binary.

    The Claude Code executable ships with the full list of known model
    IDs embedded as ASCII strings.  We scan the binary in 1MB chunks
    with overlap to find all ``claude-{family}-{version}`` patterns.

    This is ~80x faster than calling ``claude models`` (~0.1s vs ~8s)
    because it reads a local file instead of making a network API call.

    The approach is platform-agnostic:
    - Binary path resolved dynamically via ``shutil.which`` + symlink
      resolution — no hardcoded paths
    - Pure Python binary scan — no dependency on ``strings`` or other
      platform tools
    - Works on Linux, macOS, and any OS where the binary is readable

    Returns:
        Sorted list of model identifier strings (may be empty).
    """
    import re

    binary_path = _find_claude_binary()
    if binary_path is None:
        logger.debug("claude binary not found, cannot extract models from binary")
        return []

    # Pattern matches: claude-opus-4-7, claude-sonnet-4-6, claude-haiku-4-5-20251001
    # Family names: opus, sonnet, haiku, instant, code
    pattern = re.compile(rb"claude-(?:opus|sonnet|haiku|instant|code)-[0-9][a-z0-9._-]*")

    try:
        models: set[str] = set()
        overlap = 60  # max model ID length — ensures boundary matches are caught
        with open(binary_path, "rb") as f:
            chunk = f.read(1024 * 1024)
            while chunk:
                for match in pattern.finditer(chunk):
                    try:
                        raw = match.group().decode("ascii")
                        # Strip trailing non-model chars
                        model_id = raw.rstrip(".,;:!?-_ ")
                        # Must have at least family + major version
                        # e.g. "claude-opus-4" is 14 chars
                        # Skip display-name artifacts that contain dots
                        # (e.g. "claude-sonnet-4.6" is a display label,
                        # not a valid API model ID — the real ID uses dashes)
                        # Also skip "claude-code-*" internal identifiers.
                        if (
                            len(model_id) >= 13
                            and "." not in model_id
                            and not model_id.startswith("claude-code-")
                        ):
                            models.add(model_id)
                    except UnicodeDecodeError:
                        pass
                next_chunk = f.read(1024 * 1024)
                if not next_chunk:
                    break
                chunk = chunk[-overlap:] + next_chunk

        if models:
            logger.debug(f"Extracted {len(models)} model IDs from claude binary")
            return sorted(models)

        logger.debug("No model IDs found in claude binary")
        return []
    except OSError as exc:
        logger.debug(f"Cannot read claude binary for model extraction: {exc!r}")
        return []


def _pick_default_model(models: list[str]) -> str:
    """Pick the best default model from a list of extracted model IDs.

    Claude Code's default is typically the latest Sonnet.  We select
    the newest non-dated short alias (e.g. ``claude-sonnet-4-6`` over
    ``claude-sonnet-4-6-20251101``) because these are the aliases the
    CLI uses by default.

    Priority: Sonnet > Opus > Haiku (matching Claude Code's default
    behaviour of using Sonnet for most tasks).

    Args:
        models: List of model identifier strings.

    Returns:
        Best default model ID, or empty string if list is empty.
    """
    if not models:
        return ""

    # Separate short aliases from dated versions.
    # Short alias: claude-sonnet-4-6 (no date suffix)
    # Dated version: claude-sonnet-4-6-20251101
    import re

    short_aliases: list[str] = []
    for m in models:
        # A "short alias" has no 8-digit date suffix (YYYYMMDD)
        if not re.search(r"-\d{8}", m):
            short_aliases.append(m)

    # Pick from short aliases if available, otherwise use all
    candidates = short_aliases if short_aliases else models

    # Priority order: prefer sonnet (default), then opus, then haiku
    for family in ("sonnet", "opus", "haiku"):
        family_models = [m for m in candidates if f"-{family}-" in m]
        if family_models:
            # Sort reverse-alphabetically to get the newest version first
            # e.g. "claude-sonnet-4-6" > "claude-sonnet-4-5" > "claude-sonnet-4"
            family_models.sort(reverse=True)
            return family_models[0]

    # Fallback: return the last (highest-sorted) model
    return candidates[-1] if candidates else ""


# ---------------------------------------------------------------------------
# Rate-limit / model-not-found detection for plain text
# ---------------------------------------------------------------------------

_RATE_LIMIT_PHRASES = [
    "rate limit",
    "rate_limit",
    "429",
    "too many requests",
    "overloaded",
    "529",
    "quota exceeded",
    "usage limit",
]

_MODEL_NOT_FOUND_PHRASES = [
    "model not found",
    "model_not_found",
    "no such model",
    "unknown model",
    "invalid model",
    "does not exist",
]


def _is_rate_limit_text(text: str) -> bool:
    """Return True if the text signals a provider rate limit.

    Args:
        text: A plain-text line from Claude Code output.

    Returns:
        True if a rate-limit phrase is found.
    """
    lower = text.lower()
    return any(phrase in lower for phrase in _RATE_LIMIT_PHRASES)


def _is_model_not_found_text(text: str) -> bool:
    """Return True if the text signals that the requested model is unavailable.

    Args:
        text: A plain-text line from Claude Code output.

    Returns:
        True if a model-not-found phrase is found.
    """
    lower = text.lower()
    return any(phrase in lower for phrase in _MODEL_NOT_FOUND_PHRASES)
