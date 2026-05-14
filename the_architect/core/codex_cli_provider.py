"""OpenAI Codex CLI provider implementation for The Architect.

Wraps all Codex-specific logic: binary detection, command building,
JSONL output parsing, and model resolution from config.toml.

Key differences from OpenCode and Claude Code:
  - Binary: ``codex`` (installed via @openai/codex npm package)
  - JSONL event stream via ``--json`` flag
  - Model resolution: ``CODEX_MODEL`` env var → ``~/.codex/config.toml`` → fallback list
  - No named-agent system or config file equivalent
  - Auth: ``CODEX_API_KEY`` env var
  - Git repo required for ``codex exec``
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from the_architect.config import ArchitectConfig
    from the_architect.core.provider import ParsedEvent


# ---------------------------------------------------------------------------
# Fallback model list — only used when config.toml parsing fails
# ---------------------------------------------------------------------------

_FALLBACK_CODEX_MODELS = [
    "gpt-5.4",
    "gpt-5.5",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5.2",
    "o3",
]


# Rate-limit and model-not-found phrase detection (same as ClaudeCodeProvider)
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


# ---------------------------------------------------------------------------
# Codex CLI provider
# ---------------------------------------------------------------------------


class CodexCliProvider:
    """Provider implementation for the OpenAI Codex CLI.

    Codex is OpenAI's official CLI for their models. Key characteristics:
    - Emits structured JSONL events via ``--json`` flag
    - No named-agent system
    - Model resolution from environment or config file
    - Requires Git repository for ``codex exec``
    """

    def __init__(self) -> None:
        self._resolved_model_cache: dict[str, str] = {}
        # Cache the resolved version string for the lifetime of this
        # provider instance. ``get_version()`` spawns ``codex --version``
        # which can take time — callers in hot paths must never pay that cost
        # on every invocation. A fresh CLI invocation creates a new instance.
        self._version_cache: str | None = None
        # Tracks whether any item.delta text events were received in the
        # current turn.  When True we suppress the item.completed agent_message
        # text to avoid duplicating output that was already streamed live.
        # Reset to False on every turn.started event.
        self._delta_text_seen: bool = False

    # ── Identity.truncated

    @property
    def name(self) -> str:
        """Short identifier."""
        return "codex"

    @property
    def display_name(self) -> str:
        """Human-readable name."""
        return "Codex CLI"

    @property
    def binary_name(self) -> str:
        """CLI binary name."""
        return "codex"

    # ── Installation checks.truncated

    def is_installed(self) -> bool:
        """Return True if the codex binary is on PATH."""
        return shutil.which("codex") is not None

    def get_version(self) -> str:
        """Return the codex version string, or ``'unknown'``.

        The result is cached on the instance after the first successful
        call so subsequent calls (e.g. from prompt_toolkit render
        callbacks) do not re-spawn ``codex --version``.
        """
        if self._version_cache is not None:
            return self._version_cache
        try:
            result = subprocess.run(
                ["codex", "--version"],
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
        """Return True if Codex is installed and likely usable.

        Checks that the ``codex`` binary is present and exits cleanly
        with ``--version``. Model availability is determined at runtime.
        """
        if not self.is_installed():
            return False
        try:
            result = subprocess.run(
                ["codex", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            return False

    def install_hint(self) -> str:
        """Return the Codex install command for this platform."""
        if shutil.which("npm"):
            return "npm install -g @openai/codex"
        return "see https://github.com/openai/codex"

    def check_update_available(self) -> str:
        """Check if a Codex update is available.

        Returns:
            Empty string if up-to-date or check fails; otherwise an
            actionable message with the update command.
        """
        import urllib.request

        if not self.is_installed():
            return ""

        installed = self.get_version()
        if not installed or installed == "unknown":
            return ""

        m = re.search(r"(\d+\.\d+\.\d+)", installed)
        if not m:
            return ""
        installed_ver = m.group(1)

        # Codex uses npm too
        try:
            req = urllib.request.Request(
                "https://registry.npmjs.org/@openai/codex/latest",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                latest = data.get("version", "")
                if not latest:
                    return ""
        except Exception:
            return ""

        try:
            inst_tuple = tuple(int(x) for x in installed_ver.split("."))
            latest_tuple = tuple(int(x) for x in latest.split("."))
        except (ValueError, AttributeError):
            return ""

        if inst_tuple < latest_tuple:
            return (
                f"Codex CLI {installed_ver} is installed, but {latest} is available. "
                f"Update with: npm i -g @openai/codex@latest"
            )

        return ""

    # ── Model / agent discovery.truncated

    def list_models(self) -> list[str]:
        """Return available model identifiers from the Codex CLI.

        Resolution order:
        1. ``CODEX_MODEL`` env var — single model override.
        2. ``codex debug models`` — live catalog from the installed binary,
           filtered to visible models (``visibility != "hide"``), sorted by
           ``priority`` (lower = more prominent).  This is the correct source
           because OpenAI controls the catalog and it changes with each
           Codex CLI release — hardcoded lists go stale immediately.
        3. ``~/.codex/config.toml`` ``model`` field — single model from config.
        4. Static fallback list — only used when the binary call fails.

        Returns:
            List of model identifier strings in display order.
        """
        import json as _json

        # 1. Fast path: CODEX_MODEL env var
        env_model = os.environ.get("CODEX_MODEL", "").strip()
        if env_model:
            return [env_model]

        # 2. Live catalog from the binary
        try:
            result = subprocess.run(
                ["codex", "debug", "models"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                catalog = _json.loads(result.stdout)
                raw_models = catalog.get("models", [])
                # Filter out hidden entries; sort by priority (ascending = most
                # prominent first) so the dropdown order matches the Codex UI.
                visible = [
                    m for m in raw_models if isinstance(m, dict) and m.get("visibility") != "hide"
                ]
                visible.sort(key=lambda m: m.get("priority", 999))
                slugs = [m["slug"] for m in visible if m.get("slug")]
                if slugs:
                    logger.debug(f"Codex: loaded {len(slugs)} models from debug catalog")
                    return slugs
        except (
            subprocess.TimeoutExpired,
            subprocess.SubprocessError,
            FileNotFoundError,
            _json.JSONDecodeError,
            KeyError,
        ) as exc:
            logger.debug(f"codex debug models failed: {exc!r}, falling back")

        # 3. Config file
        model = _read_codex_config_model()
        if model:
            return [model]

        # 4. Static fallback — last resort
        return list(_FALLBACK_CODEX_MODELS)

    def list_agents(self, project_dir: Path) -> list[str]:
        """Return execution agent names (no-op for Codex).

        Codex has no named-agent system. Always returns empty list.

        Args:
            project_dir: The project root directory (unused).

        Returns:
            Empty list always.
        """
        return []

    def get_resolved_model(self, project_dir: Path, agent_name: str = "architect") -> str:
        """Return the model Codex will use.

        Resolution order:
        1. ``CODEX_MODEL`` env var
        2. ``~/.codex/config.toml`` model field
        3. First visible model from ``codex debug models`` catalog
        4. First model from static fallback list

        Results are cached per project directory.

        Args:
            project_dir: The project root directory (unused for model resolution).
            agent_name: Ignored (Codex has no named agents).

        Returns:
            Model identifier string, or empty string if not determinable.
        """
        # Fast check for streaming resolution
        if "_stream" in self._resolved_model_cache:
            return self._resolved_model_cache["_stream"]

        cache_key = str(project_dir)
        if cache_key in self._resolved_model_cache:
            return self._resolved_model_cache[cache_key]

        # 1. CODEX_MODEL env var
        env_model = os.environ.get("CODEX_MODEL", "").strip()
        if env_model:
            self._resolved_model_cache[cache_key] = env_model
            return env_model

        # 2. Config file
        model = _read_codex_config_model()
        if model:
            self._resolved_model_cache[cache_key] = model
            return model

        # 3. First visible model from live catalog (same call as list_models)
        live = self.list_models()
        if live:
            self._resolved_model_cache[cache_key] = live[0]
            return live[0]

        return ""

    # ── Config / setup.truncated

    def find_user_config(self, project_dir: Path) -> Path | None:
        """Find the user's Codex config file.

        Looks for the global ``~/.codex/config.toml`` file.

        Args:
            project_dir: The project root directory (unused for Codex).

        Returns:
            Path to config.toml, or None if not found.
        """
        config_path = Path.home() / ".codex" / "config.toml"
        if config_path.exists():
            return config_path
        return None

    def ensure_setup(self, project_dir: Path, config: ArchitectConfig) -> Path:
        """Ensure The Architect's prompts are ready for Codex.

        Writes prompts to ``.architect/prompts/``. Codex does not
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
        """Write packaged Architect prompts to .architect/prompts/."""
        import importlib.resources as resources

        prompts_dir = project_dir / ".architect" / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)

        package_prompts = resources.files("the_architect.resources.prompts")

        for filename in (
            "architect.md",
            "intelligence.md",
            "reviewer.md",
            "execution-protocol.md",
        ):
            source = package_prompts / filename
            target = prompts_dir / filename
            content = source.read_text(encoding="utf-8")
            target.write_text(content, encoding="utf-8")
            logger.debug(f"Written prompt: {target}")

    def get_architect_prompt(self) -> str:
        """Return the architect agent prompt text.

        Used to prepend the architect role to planning instructions
        since Codex has no named-agent system.

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

    # ── Command building.truncated

    @property
    def instruction_via_stdin(self) -> bool:
        """Codex CLI receives its instruction as a command-line argument."""
        return False

    def build_command(
        self,
        instruction: str,
        model_override: str | None = None,
        agent_override: str | None = None,
    ) -> list[str]:
        """Build the codex CLI command for non-interactive execution.

        Uses ``codex exec --dangerously-bypass-approvals-and-sandbox --json``
        to emit JSONL events. ``--yolo`` is an alternative to the bypass flag.

        Note: ``agent_override`` is ignored (Codex has no named agents).

        Args:
            instruction: The full instruction string to pass to codex.
            model_override: Optional model name to pass via --model flag.
            agent_override: Ignored (Codex has no named agents).

        Returns:
            List of command components ready for subprocess execution.
        """
        codex_bin = shutil.which("codex") or "codex"
        cmd: list[str] = [
            codex_bin,
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--json",
        ]
        if model_override:
            cmd.extend(["--model", model_override])
        cmd.extend(["--", instruction])

        return cmd

    def get_env_overrides(self, config_override: Path | None = None) -> dict[str, str]:
        """Return extra environment variables for this Codex run.

        If ``CODEX_API_KEY`` is set in the environment, pass it through
        as an override. Otherwise return empty dict.

        Args:
            config_override: Ignored (Codex has no config file path env var).

        Returns:
            Dict of env var name → value (may be empty).
        """
        env = {}
        api_key = os.environ.get("CODEX_API_KEY")
        if api_key:
            env["CODEX_API_KEY"] = api_key
        return env

    # ── Output parsing.truncated

    def parse_output_line(self, line: str) -> ParsedEvent | None:
        """Parse a single line from Codex's JSONL event stream.

        Codex is invoked with ``--json`` which emits one JSON object per line.
        We handle several event types:

        - ``thread.started`` — silent, no display
        - ``turn.started`` — silent, no display
        - ``item.completed`` with ``item.type == "agent_message"`` — extract text for display
        - ``item.completed`` with ``item.type == "command_execution"`` — display tool name
        - ``turn.completed`` — extract token usage from ``usage`` dict
        - ``turn.failed`` — extract error message
        - ``error`` — extract error message

        Any line that is not valid JSON is treated as plain text (fallback).

        Args:
            line: A single raw line from codex's stdout.

        Returns:
            A ParsedEvent, or None for empty/silent lines.
        """
        from the_architect.core.provider import ParsedEvent
        from the_architect.core.runner import TokenUsage

        stripped = line.strip()
        if not stripped:
            return None

        # Try to parse as JSON (JSONL mode)
        try:
            event = json.loads(stripped)
        except (ValueError, json.JSONDecodeError):
            # Not JSON — treat as plain text (fallback)
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

        if etype == "thread.started":
            # Silent event (thread_id not displayed)
            return ParsedEvent(event_type=etype, display_lines=[], tokens=None)

        elif etype == "turn.started":
            # Silent event (turn about to start). Reset delta-seen flag so
            # item.completed agent_message text is emitted fresh for this turn.
            self._delta_text_seen = False
            return ParsedEvent(event_type=etype, display_lines=[], tokens=None)

        elif etype == "item.started":
            # Silent event (item about to start)
            return ParsedEvent(event_type=etype, display_lines=[])

        elif etype == "item.delta":
            # Streaming delta from the agent — emitted token-by-token while the
            # model is generating.  Extract text from content_delta parts so the
            # TUI shows live output rather than waiting for item.completed.
            # Codex delta shapes observed in the wild:
            #   {"type":"item.delta","delta":{"type":"text_delta","text":"..."}}
            #   {"type":"item.delta","delta":{"type":"content_delta",
            #     "delta":{"type":"text","text":"..."}}}
            delta = event.get("delta", {})
            if isinstance(delta, dict):
                delta_type = delta.get("type", "")
                if delta_type == "text_delta":
                    text = delta.get("text", "")
                    if isinstance(text, str) and text:
                        # Emit non-empty text chunks; split on newlines so each
                        # line is its own RichLog entry.
                        for chunk_line in text.split("\n"):
                            if chunk_line:
                                display_lines.append(chunk_line)
                elif delta_type == "content_delta":
                    inner = delta.get("delta", {})
                    if isinstance(inner, dict) and inner.get("type") == "text":
                        text = inner.get("text", "")
                        if isinstance(text, str) and text:
                            for chunk_line in text.split("\n"):
                                if chunk_line:
                                    display_lines.append(chunk_line)
            # Mark that this turn received streamed delta text so that the
            # subsequent item.completed agent_message event is suppressed.
            if display_lines:
                self._delta_text_seen = True

        elif etype == "item.completed":
            # Extract display content based on item type.
            item = event.get("item", {})
            item_type = item.get("type") if isinstance(item, dict) else None

            if item_type == "agent_message":
                text = item.get("text", "") if isinstance(item, dict) else ""
                if isinstance(text, str) and text.strip():
                    if self._delta_text_seen:
                        # This turn already streamed text via item.delta events;
                        # item.completed would duplicate it — suppress.
                        pass
                    else:
                        # No deltas were streamed (common with older Codex builds
                        # or when the response arrives as a single completed event).
                        # Emit the full text now so it's not silently dropped.
                        for completed_line in text.strip().split("\n"):
                            display_lines.append(completed_line)
            elif item_type == "command_execution":
                # Show the command that was executed
                cmd = ""
                if isinstance(item, dict):
                    cmd = item.get("command", "") or item.get("id", "") or "command_execution"
                if cmd and len(cmd) > 80:
                    cmd = cmd[:80] + "…"
                display_lines.append(f"$ {cmd}")

        elif etype == "turn.completed":
            # Extract token usage from turn completion
            usage = event.get("usage", {})
            if isinstance(usage, dict):
                tokens = TokenUsage(
                    input_tokens=int(usage.get("input_tokens", 0) or 0),
                    output_tokens=int(usage.get("output_tokens", 0) or 0),
                    cache_read_tokens=int(usage.get("cached_input_tokens", 0) or 0),
                    cache_write_tokens=0,  # Codex doesn't report cache_write_tokens
                )

        elif etype == "turn.failed":
            # Extract error message
            error_msg = event.get("error", "")
            if isinstance(error_msg, str) and error_msg.strip():
                display_lines.append(f"Error: {error_msg.strip()}")
                if not rate_limit:
                    rate_limit = _is_rate_limit_text(error_msg)
                model_not_found = _is_model_not_found_text(error_msg)

        elif etype == "error":
            # API-level error
            error_msg = event.get("message", "")
            if isinstance(error_msg, str) and error_msg.strip():
                display_lines.append(f"Error: {error_msg.strip()}")
                if not rate_limit:
                    rate_limit = _is_rate_limit_text(error_msg)
                model_not_found = _is_model_not_found_text(error_msg)

        # Detect rate-limit / model-not-found in display text
        if not rate_limit and display_lines:
            combined = " ".join(display_lines)
            rate_limit = _is_rate_limit_text(combined)
            model_not_found = _is_model_not_found_text(combined)

        # Cache the model name from agent_message for get_resolved_model
        if etype == "item.completed":
            item = event.get("item", {})
            if isinstance(item, dict) and item.get("type") == "agent_message":
                model = item.get("model", "")
                if model:
                    self._resolved_model_cache["_stream"] = str(model)

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
        """Codex does not support named agent selection."""
        return False

    def supports_json_output(self) -> bool:
        """Codex supports JSONL output via --json flag."""
        return True

    def supports_free_tier(self) -> bool:
        """Codex never supports OpenRouter free-tier rotation.

        Codex talks directly to OpenAI's API and has no OpenRouter integration.

        Returns:
            Always False.
        """
        return False


# ---------------------------------------------------------------------------
# Config.toml parsing helper
# ---------------------------------------------------------------------------


def _read_codex_config_model() -> str:
    """Read the model from ~/.codex/config.toml.

    Parses the TOML file and extracts the "model" field if present.

    Returns:
        Model string, or empty string if parsing fails or file not found.
    """
    config_path = Path.home() / ".codex" / "config.toml"
    if not config_path.exists():
        return ""
    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        return str(data.get("model", "")).strip()
    except (OSError, tomllib.TOMLDecodeError):
        return ""


# ---------------------------------------------------------------------------
# Rate-limit / model-not-found detection for plain text and JSON
# ---------------------------------------------------------------------------


def _is_rate_limit_text(text: str) -> bool:
    """Return True if the text signals a provider rate limit.

    Args:
        text: A plain-text line or error message.

    Returns:
        True if a rate-limit phrase is found.
    """
    lower = text.lower()
    return any(phrase in lower for phrase in _RATE_LIMIT_PHRASES)


def _is_model_not_found_text(text: str) -> bool:
    """Return True if the text signals that the requested model is unavailable.

    Args:
        text: A plain-text line or error message.

    Returns:
        True if a model-not-found phrase is found.
    """
    lower = text.lower()
    return any(phrase in lower for phrase in _MODEL_NOT_FOUND_PHRASES)
