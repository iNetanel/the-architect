"""Google Gemini CLI provider implementation for The Architect.

Wraps all Gemini-specific logic: binary detection, command building,
JSONL output parsing, and model resolution from settings.json.

Key differences from Codex and Claude Code:
  - Binary: ``gemini`` (installed via @google/gemini-cli npm package)
  - JSONL event stream via ``--output-format stream-json`` flag
  - Model resolution: ``GEMINI_MODEL`` env var → ``~/.gemini/settings.json`` → fallback list
  - No named-agent system or config file equivalent
  - Auth: ``GEMINI_API_KEY`` env var
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from the_architect.config import ArchitectConfig
    from the_architect.core.provider import ParsedEvent


# ---------------------------------------------------------------------------
# Fallback model list — only used when settings.json parsing fails
# ---------------------------------------------------------------------------

_FALLBACK_GEMINI_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
]


# Rate-limit and model-not-found phrase detection (same as CodexCliProvider)
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
# Gemini CLI provider
# ---------------------------------------------------------------------------


class GeminiCliProvider:
    """Provider implementation for the Google Gemini CLI.

    Gemini is Google's official CLI for their Gemini models. Key characteristics:
    - Emits structured JSONL events via ``--output-format stream-json`` flag
    - No named-agent system
    - Model resolution from environment or settings file
    - Config stored as JSON (``~/.gemini/settings.json``)
    """

    def __init__(self) -> None:
        self._resolved_model_cache: dict[str, str] = {}
        # Cache the resolved version string for the lifetime of this
        # provider instance. ``get_version()`` spawns ``gemini --version``
        # which can take time — callers in hot paths must never pay that cost
        # on every invocation. A fresh CLI invocation creates a new instance.
        self._version_cache: str | None = None

    # ── Identity

    @property
    def name(self) -> str:
        """Short identifier."""
        return "gemini-cli"

    @property
    def display_name(self) -> str:
        """Human-readable name."""
        return "Gemini CLI"

    @property
    def binary_name(self) -> str:
        """CLI binary name."""
        return "gemini"

    # ── Installation checks

    def is_installed(self) -> bool:
        """Return True if the gemini binary is on PATH."""
        return shutil.which("gemini") is not None

    def get_version(self) -> str:
        """Return the gemini version string, or ``'unknown'``.

        The result is cached on the instance after the first successful
        call so subsequent calls (e.g. from prompt_toolkit render
        callbacks) do not re-spawn ``gemini --version``.
        """
        if self._version_cache is not None:
            return self._version_cache
        try:
            result = subprocess.run(
                ["gemini", "--version"],
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
        """Return True if Gemini CLI is installed and likely usable.

        Checks that the ``gemini`` binary is present and exits cleanly
        with ``--version``. Model availability is determined at runtime.
        """
        if not self.is_installed():
            return False
        try:
            result = subprocess.run(
                ["gemini", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            return False

    def install_hint(self) -> str:
        """Return the Gemini CLI install command for this platform."""
        if shutil.which("npm"):
            return "npm install -g @google/gemini-cli"
        return "see https://github.com/google-gemini/gemini-cli"

    def check_update_available(self) -> str:
        """Check if a Gemini CLI update is available.

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

        try:
            req = urllib.request.Request(
                "https://registry.npmjs.org/@google/gemini-cli/latest",
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
                f"Gemini CLI {installed_ver} is installed, but {latest} is available. "
                f"Update with: npm i -g @google/gemini-cli@latest"
            )

        return ""

    # ── Model / agent discovery

    def list_models(self) -> list[str]:
        """Return available model identifiers from the user's local Gemini CLI installation.

        Resolution order — all local, no external network calls:
        1. ``GEMINI_MODEL`` env var — single model override.
        2. **Bundle extraction** — locate the installed ``gemini`` binary via
           ``shutil.which``, resolve the real path (following symlinks), then
           scan the JS bundle chunks in the same directory for
           ``gemini-*`` model name strings.  This is the set of models the
           installed version of the CLI actually knows about, reflecting whatever
           the user has installed (including custom or enterprise builds).
        3. ``~/.gemini/settings.json`` ``model`` field — single model from the
           user's local config.
        4. Static fallback list — last resort when the binary is not found.

        Returns:
            List of model name strings (e.g. ``"gemini-2.5-pro"``), sorted.
        """
        # 1. Env var override
        env_model = os.environ.get("GEMINI_MODEL", "").strip()
        if env_model:
            return [env_model]

        # 2. Bundle extraction from the installed binary
        models = _extract_models_from_gemini_bundle()
        if models:
            return models

        # 3. Config file
        model = _read_gemini_settings_model()
        if model:
            return [model]

        # 4. Static fallback
        return list(_FALLBACK_GEMINI_MODELS)

    def list_agents(self, project_dir: Path) -> list[str]:
        """Return execution agent names (no-op for Gemini CLI).

        Gemini CLI has no named-agent system. Always returns empty list.

        Args:
            project_dir: The project root directory (unused).

        Returns:
            Empty list always.
        """
        return []

    def get_resolved_model(self, project_dir: Path, agent_name: str = "architect") -> str:
        """Return the model Gemini CLI will use.

        Resolution order:
        1. ``GEMINI_MODEL`` env var
        2. ``~/.gemini/settings.json`` model.name field
        3. First model from live API list (via list_models)
        4. First model from static fallback list

        Results are cached per project directory.

        Args:
            project_dir: The project root directory (unused for model resolution).
            agent_name: Ignored (Gemini CLI has no named agents).

        Returns:
            Model identifier string, or empty string if not determinable.
        """
        # Fast check for streaming resolution
        if "_stream" in self._resolved_model_cache:
            return self._resolved_model_cache["_stream"]

        cache_key = str(project_dir)
        if cache_key in self._resolved_model_cache:
            return self._resolved_model_cache[cache_key]

        # 1. GEMINI_MODEL env var
        env_model = os.environ.get("GEMINI_MODEL", "").strip()
        if env_model:
            self._resolved_model_cache[cache_key] = env_model
            return env_model

        # 2. Config file
        model = _read_gemini_settings_model()
        if model:
            self._resolved_model_cache[cache_key] = model
            return model

        # 3. First model from live list (API or fallback)
        live = self.list_models()
        if live:
            self._resolved_model_cache[cache_key] = live[0]
            return live[0]

        return ""

    # ── Config / setup

    def find_user_config(self, project_dir: Path) -> Path | None:
        """Find the user's Gemini CLI config file.

        Checks project-local ``.gemini/settings.json`` first, then falls
        back to global ``~/.gemini/settings.json``.

        Args:
            project_dir: The project root directory.

        Returns:
            Path to settings.json, or None if not found.
        """
        project_config = project_dir / ".gemini" / "settings.json"
        if project_config.exists():
            return project_config

        global_config = Path.home() / ".gemini" / "settings.json"
        if global_config.exists():
            return global_config

        return None

    def ensure_setup(self, project_dir: Path, config: ArchitectConfig) -> Path:
        """Ensure The Architect's prompts are ready for Gemini CLI.

        Writes prompts to ``.architect/prompts/``. Gemini CLI does not
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
        since Gemini CLI has no named-agent system.

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

    # ── Command building

    @property
    def instruction_via_stdin(self) -> bool:
        """Gemini CLI receives its instruction as a command-line argument."""
        return False

    def build_command(
        self,
        instruction: str,
        model_override: str | None = None,
        agent_override: str | None = None,
    ) -> list[str]:
        """Build the Gemini CLI command for non-interactive execution.

        Uses ``gemini -p <instruction> --output-format stream-json --yolo``
        to emit JSONL events.

        Note: ``agent_override`` is ignored (Gemini CLI has no named agents).

        Args:
            instruction: The full instruction string to pass to gemini.
            model_override: Optional model name to pass via --model flag.
            agent_override: Ignored (Gemini CLI has no named agents).

        Returns:
            List of command components ready for subprocess execution.
        """
        gemini_bin = shutil.which("gemini") or "gemini"

        cmd: list[str] = [gemini_bin]

        if model_override:
            cmd.extend(["--model", model_override])

        cmd.extend(["-p", instruction, "--output-format", "stream-json", "--yolo"])

        return cmd

    def get_env_overrides(self, config_override: Path | None = None) -> dict[str, str]:
        """Return extra environment variables for this Gemini CLI run.

        If ``GEMINI_API_KEY`` is set in the environment, pass it through
        as an override. Otherwise return empty dict.

        Args:
            config_override: Ignored (Gemini CLI has no config file path env var).

        Returns:
            Dict of env var name → value (may be empty).
        """
        env = {}
        api_key = os.environ.get("GEMINI_API_KEY")
        if api_key:
            env["GEMINI_API_KEY"] = api_key
        return env

    # ── Output parsing

    def parse_output_line(self, line: str) -> ParsedEvent | None:
        """Parse a single line from Gemini CLI's JSONL event stream.

        Gemini CLI is invoked with ``--output-format stream-json`` which
        emits one JSON object per line. We handle six event types:

        - ``init`` — extract model name, silent display
        - ``message`` — extract text from model messages for display
        - ``tool_use`` — display tool name
        - ``tool_result`` — silent (no display lines needed)
        - ``error`` — extract error message
        - ``result`` — extract token usage

        Any line that is not valid JSON is treated as plain text (fallback).

        Args:
            line: A single raw line from gemini's stdout.

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

        if etype == "init":
            # Extract model name from init event and cache it
            model = event.get("model", "")
            if not model and isinstance(event.get("session"), dict):
                model = event.get("session", {}).get("model", "")
            if model:
                self._resolved_model_cache["_stream"] = str(model)
            return ParsedEvent(event_type=etype, display_lines=[], tokens=None)

        elif etype == "message":
            # Only display model messages, not user echoes
            role = event.get("role", "")
            if role == "model":
                raw_content = event.get("content", "") or event.get("text", "")
                # Gemini CLI may emit content as a plain string or as a list of
                # parts: [{"text": "..."}, ...].  Handle both shapes.
                if isinstance(raw_content, str):
                    text = raw_content.strip()
                    if text:
                        display_lines.extend(text.split("\n"))
                elif isinstance(raw_content, list):
                    for part in raw_content:
                        if isinstance(part, dict):
                            part_text = (part.get("text") or "").strip()
                            if part_text:
                                display_lines.extend(part_text.split("\n"))
            # role == "user" → silent (no display lines)

        elif etype == "tool_use":
            # Display tool name and a short summary of inputs so the user can
            # see what the model is doing without reading raw JSON.
            tool_name = event.get("name", "") or event.get("tool_name", "")
            if tool_name:
                inp = event.get("input", {}) or {}
                # Build a brief description from the most informative input field
                detail = ""
                if isinstance(inp, dict):
                    for key in (
                        "path",
                        "filePath",
                        "file_path",
                        "command",
                        "pattern",
                        "query",
                        "url",
                    ):
                        val = inp.get(key, "")
                        if val:
                            detail = str(val)[:80]
                            break
                    if not detail and inp:
                        # fallback: first non-empty value
                        for v in inp.values():
                            if v:
                                detail = str(v)[:80]
                                break
                if detail:
                    display_lines.append(f"→ {tool_name} {detail}")
                else:
                    display_lines.append(f"→ {tool_name}")

        elif etype == "tool_result":
            # Silent — no display lines needed
            pass

        elif etype == "error":
            # Extract error message
            error_msg = event.get("message", "") or event.get("error", "")
            if isinstance(error_msg, str) and error_msg.strip():
                display_lines.append(f"Error: {error_msg.strip()}")
                rate_limit = _is_rate_limit_text(error_msg)
                model_not_found = _is_model_not_found_text(error_msg)

        elif etype == "result":
            # Extract token usage from result event
            usage = event.get("usage", {})
            if isinstance(usage, dict):
                tokens = TokenUsage(
                    input_tokens=int(usage.get("input_tokens", 0) or 0),
                    output_tokens=int(usage.get("output_tokens", 0) or 0),
                    cache_read_tokens=int(usage.get("cached_input_tokens", 0) or 0),
                    cache_write_tokens=0,  # Gemini doesn't report cache_write_tokens
                )

        else:
            # Unrecognised event type — return with event_type and empty display
            if not display_lines:
                return ParsedEvent(event_type=etype, display_lines=[], tokens=None)

        # Detect rate-limit / model-not-found in display text
        if not rate_limit and display_lines:
            combined = " ".join(display_lines)
            rate_limit = _is_rate_limit_text(combined)
            model_not_found = _is_model_not_found_text(combined)

        if not display_lines and tokens is None:
            return ParsedEvent(event_type=etype, display_lines=[], tokens=None)

        return ParsedEvent(
            event_type=etype,
            display_lines=display_lines,
            tokens=tokens,
            rate_limit=rate_limit,
            model_not_found=model_not_found,
        )

    def supports_agents(self) -> bool:
        """Gemini CLI does not support named agent selection."""
        return False

    def supports_json_output(self) -> bool:
        """Gemini CLI supports JSONL output via --output-format stream-json."""
        return True

    def supports_free_tier(self) -> bool:
        """Gemini CLI never supports OpenRouter free-tier rotation.

        Gemini CLI talks directly to Google's API and has no OpenRouter integration.

        Returns:
            Always False.
        """
        return False


# ---------------------------------------------------------------------------
# Settings.json parsing helper
# ---------------------------------------------------------------------------


def _read_gemini_settings_model() -> str:
    """Read the model from ~/.gemini/settings.json.

    Parses the JSON file and extracts the model.name field if present.

    Returns:
        Model string, or empty string if parsing fails or file not found.
    """
    config_path = Path.home() / ".gemini" / "settings.json"
    if not config_path.exists():
        return ""
    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
        return str(data.get("model", {}).get("name", "")).strip()
    except (OSError, json.JSONDecodeError, AttributeError):
        return ""


def _extract_models_from_gemini_bundle() -> list[str]:
    """Extract ``gemini-*`` model identifiers from the installed Gemini CLI bundle.

    Locates the ``gemini`` binary via ``shutil.which``, resolves its real path
    (following symlinks), then scans the sibling JS bundle chunks for model
    name string literals.  This approach is OS-agnostic: it follows whatever
    ``gemini`` resolves to on the current machine, so it works with system
    installs, nvm, Homebrew, Windows ``%APPDATA%\\npm``, etc.

    Only models whose names match the ``gemini-<version>`` pattern are
    returned; internal identifiers (``gemini-cli``, ``gemini-api-key``, …)
    are filtered out by requiring at least one digit in the version part.

    Returns:
        Sorted list of model name strings, or empty list if the binary is not
        found or the bundle cannot be read.
    """
    import re as _re

    gemini_bin = shutil.which("gemini")
    if not gemini_bin:
        return []

    try:
        bundle_dir = Path(gemini_bin).resolve().parent
        # The resolved path may point directly inside the bundle directory
        # (e.g. <prefix>/lib/node_modules/@google/gemini-cli/bundle/gemini.js)
        # or one level above it.  Try both.
        candidate_dirs = [bundle_dir, bundle_dir / "bundle"]
        js_files: list[Path] = []
        for d in candidate_dirs:
            if d.is_dir():
                js_files.extend(d.glob("*.js"))
                if js_files:
                    break

        if not js_files:
            return []

        models: set[str] = set()
        # Pattern: a quoted gemini-<major>.<minor> or gemini-<major>-<name>
        # The version part must start with a digit to exclude internal names.
        pattern = _re.compile(r'["\']gemini-([0-9][a-zA-Z0-9._-]*)["\']')
        for js_file in js_files:
            try:
                content = js_file.read_text(encoding="utf-8", errors="ignore")
                for m in pattern.finditer(content):
                    models.add(f"gemini-{m.group(1)}")
            except OSError:
                continue

        if models:
            logger.debug(f"Gemini: extracted {len(models)} models from bundle at {bundle_dir}")
        return sorted(models)

    except Exception as exc:
        logger.debug(f"Gemini bundle extraction failed: {exc!r}")
        return []


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
