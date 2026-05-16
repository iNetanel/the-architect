"""OpenCode provider implementation for The Architect.

Wraps all OpenCode-specific logic: binary detection, config discovery,
command building, JSON event parsing, and planning config management.

This is the original provider — all existing OpenCode behaviour is
preserved here unchanged.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from the_architect.config import ArchitectConfig
    from the_architect.core.provider import ParsedEvent
    from the_architect.core.runner import TokenUsage


# ---------------------------------------------------------------------------
# Internal helpers (extracted from opencode_config.py — unchanged logic)
# ---------------------------------------------------------------------------


def _extract_model_from_config_output(raw: str, agent_name: str) -> str:
    """Extract a specific agent's model from (possibly truncated) debug config output."""
    import re

    pattern = rf'^\s{{4}}"{re.escape(agent_name)}"\s*:\s*\{{'
    match = re.search(pattern, raw, re.MULTILINE)
    if not match:
        return ""

    after = raw[match.start() :]
    model_match = re.search(r'^\s+"model"\s*:\s*"([^"]+)"', after, re.MULTILINE)
    if model_match:
        return model_match.group(1)

    return ""


def _extract_agents_from_config_output(raw: str) -> dict[str, str]:
    """Extract agent names and modes from (possibly truncated) debug config output."""
    import re

    agents: dict[str, str] = {}
    current_agent: str | None = None

    for line in raw.split("\n"):
        m = re.match(r'^\s{4}"(\w+)"\s*:\s*\{', line)
        if m:
            current_agent = m.group(1)
            agents[current_agent] = "unknown"
            continue
        if current_agent is not None:
            m2 = re.match(r'^\s+"mode"\s*:\s*"(\w+)"', line)
            if m2:
                agents[current_agent] = m2.group(1)

    return agents


def _get_default_agent_from_debug_config(project_dir: Path) -> str:
    """Get the default_agent from opencode's merged config."""
    import re

    try:
        result = subprocess.run(
            ["opencode", "debug", "config"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(project_dir),
        )
        if result.returncode != 0:
            return ""
        try:
            data = json.loads(result.stdout)
            return str(data.get("default_agent", ""))
        except json.JSONDecodeError:
            pass
        m = re.search(r'"default_agent"\s*:\s*"([^"]+)"', result.stdout)
        return m.group(1) if m else ""
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
        return ""


# ---------------------------------------------------------------------------
# OpenCode provider
# ---------------------------------------------------------------------------


class OpenCodeProvider:
    """Provider implementation for the OpenCode CLI.

    All OpenCode-specific logic lives here.  The rest of The Architect
    calls only the :class:`~the_architect.core.provider.ArchitectProvider`
    interface.
    """

    def __init__(self) -> None:
        # Cache the resolved version string for the lifetime of this
        # provider instance.  ``get_version()`` spawns ``opencode --version``
        # which can take hundreds of milliseconds — callers in hot paths
        # (e.g. prompt_toolkit render callbacks) must never pay that cost
        # on every repaint.  A fresh CLI invocation creates a new instance,
        # so the user sees up-to-date versions after re-running ``architect``.
        self._version_cache: str | None = None

    # ── Identity ──────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        """Short identifier."""
        return "opencode"

    @property
    def display_name(self) -> str:
        """Human-readable name."""
        return "OpenCode"

    @property
    def binary_name(self) -> str:
        """CLI binary name."""
        return "opencode"

    # ── Installation checks ───────────────────────────────────────────────

    def is_installed(self) -> bool:
        """Return True if the opencode binary is on PATH."""
        return shutil.which("opencode") is not None

    def get_version(self) -> str:
        """Return the opencode version string, or ``'unknown'``.

        The result is cached on the instance after the first successful
        call so subsequent calls (e.g. from prompt_toolkit render
        callbacks) do not re-spawn ``opencode --version``.
        """
        if self._version_cache is not None:
            return self._version_cache
        try:
            result = subprocess.run(
                ["opencode", "--version"],
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

    def _agent_flag_broken(self) -> bool:
        """Return True when the --agent CLI flag is known to be broken.

        The flag raises "InstanceRef not provided" in OpenCode 1.15.0 and
        1.15.1 due to an Effect-based event system regression.  When broken,
        agent selection falls back to ``default_agent`` in the injected config.
        On older versions the flag works correctly and should still be used so
        that ``execution_agent`` is honoured.

        If the installed version cannot be determined we assume broken (safe
        default — avoids a crash; the warning in build_command tells the user).

        Returns:
            True when version >= 1.15.0 or version is unknown.
        """
        version_str = self.get_version()
        import re as _re

        m = _re.search(r"(\d+)\.(\d+)\.(\d+)", version_str)
        if not m:
            return True  # unknown version — play it safe
        major, minor = int(m.group(1)), int(m.group(2))
        return (major, minor) >= (1, 15)

    def has_any_models(self) -> bool:
        """Return True if opencode can list at least one model."""
        if not self.is_installed():
            return False
        try:
            result = subprocess.run(
                ["opencode", "models"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                return False
            return bool(result.stdout.strip())
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            return False

    def install_hint(self) -> str:
        """Return the most likely opencode install command for this platform."""
        if shutil.which("brew"):
            return "brew install opencode"
        if shutil.which("npm"):
            return "npm i -g opencode"
        return "see https://opencode.ai/docs/installation"

    # ── Model / agent discovery ───────────────────────────────────────────

    def list_models(self) -> list[str]:
        """Return all models available in the user's OpenCode setup."""
        try:
            result = subprocess.run(
                ["opencode", "models"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                return []
            models = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            return sorted(models)
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            return []

    def list_agents(self, project_dir: Path) -> list[str]:
        """Return primary agent names available in the user's OpenCode setup."""
        import re

        _INTERNAL_AGENTS = {
            "architect",
            "intelligence",
            "reviewer",
            "compaction",
            "summary",
            "title",
            "plan",
        }

        def _filter_primary(agents: dict[str, dict[str, Any]]) -> list[str]:
            return sorted(
                name
                for name, cfg in agents.items()
                if name not in _INTERNAL_AGENTS
                and isinstance(cfg, dict)
                and cfg.get("mode") == "primary"
            )

        # Primary: opencode agent list
        try:
            result = subprocess.run(
                ["opencode", "agent", "list"],
                capture_output=True,
                text=True,
                timeout=15,
                cwd=str(project_dir),
            )
            if result.returncode == 0:
                agents: list[str] = []
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    m = re.match(r"^(\S+)\s*\((\w+)\)", line)
                    if m:
                        name, mode = m.group(1), m.group(2)
                        if mode == "primary" and name not in _INTERNAL_AGENTS:
                            agents.append(name)
                if agents:
                    return sorted(agents)
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            pass

        # Fallback 1: opencode debug config
        try:
            result = subprocess.run(
                ["opencode", "debug", "config"],
                capture_output=True,
                text=True,
                timeout=15,
                cwd=str(project_dir),
            )
            if result.returncode == 0:
                try:
                    data = json.loads(result.stdout)
                    agent_dict = data.get("agent", {})
                    return _filter_primary(agent_dict)
                except json.JSONDecodeError:
                    pass
                found_agents = _extract_agents_from_config_output(result.stdout)
                primary = sorted(
                    name
                    for name, mode in found_agents.items()
                    if mode == "primary" and name not in _INTERNAL_AGENTS
                )
                if primary:
                    return primary
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            pass

        # Fallback 2: read project opencode.json directly
        oc_json = self.find_user_config(project_dir)
        if oc_json is not None:
            try:
                data = json.loads(oc_json.read_text(encoding="utf-8"))
                agent_dict = data.get("agent", {})
                return _filter_primary(agent_dict)
            except (OSError, json.JSONDecodeError):
                pass

        return []

    def get_resolved_model(self, project_dir: Path, agent_name: str = "") -> str:
        """Return the model OpenCode will actually use.

        Resolution order:
        1. Direct read of the user's config file — fastest, no subprocess.
           ``find_opencode_json`` checks ``OPENCODE_CONFIG`` env var first,
           then project-local ``opencode.json``, then global XDG path.
           When ``agent_name`` is empty the config's ``default_agent`` is used.
        2. ``opencode debug config`` with the current environment preserved —
           JSON parse first, regex fallback when the output is truncated.
           The environment is passed as-is so opencode reads the same config
           the user has active in their shell (including ``OPENCODE_CONFIG``).

        Args:
            project_dir: The project root directory.
            agent_name: Agent name to look up.  Empty string means use the
                config's ``default_agent`` (the agent OpenCode picks by default).

        Returns:
            Model identifier string, or empty string if not determinable.
        """
        import re as _re

        # ── Primary: read config file directly ──────────────────────────────
        from the_architect.config import find_opencode_json

        oc_json = find_opencode_json(project_dir)
        if oc_json is not None:
            try:
                data = json.loads(oc_json.read_text(encoding="utf-8"))
                agents = data.get("agent", {})

                # Resolve the effective agent name
                effective_agent = agent_name or str(data.get("default_agent", ""))

                if effective_agent and effective_agent in agents:
                    model = agents[effective_agent].get("model", "")
                    if model:
                        return str(model)

                # Fall back to top-level model field
                top_model = data.get("model", "")
                if top_model:
                    return str(top_model)
            except (OSError, json.JSONDecodeError, KeyError):
                pass

        # ── Fallback: opencode debug config subprocess ───────────────────────
        # Inherit the full parent environment so opencode reads the same config
        # the user has active (including OPENCODE_CONFIG when set).
        try:
            result = subprocess.run(
                ["opencode", "debug", "config"],
                capture_output=True,
                text=True,
                timeout=15,
                cwd=str(project_dir),
            )
            if result.returncode == 0:
                raw = result.stdout

                # Try JSON parse first
                try:
                    data = json.loads(raw)
                    agents = data.get("agent", {})
                    effective_agent = agent_name or str(data.get("default_agent", ""))
                    model = (
                        (agents.get(effective_agent, {}).get("model") if effective_agent else None)
                        or data.get("model")
                        or ""
                    )
                    if model:
                        return str(model)
                except json.JSONDecodeError:
                    pass

                # Regex fallback when JSON is truncated
                # Resolve effective agent name from the raw output
                if not agent_name:
                    m = _re.search(r'"default_agent"\s*:\s*"([^"]+)"', raw)
                    effective_agent = m.group(1) if m else ""
                else:
                    effective_agent = agent_name

                if effective_agent:
                    model = _extract_model_from_config_output(raw, effective_agent)
                    if model:
                        return model

                # Last resort: any top-level "model" field
                m2 = _re.search(r'^\s{2}"model"\s*:\s*"([^"]+)"', raw, _re.MULTILINE)
                if m2:
                    return m2.group(1)

        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError, KeyError):
            pass

        return ""

    # ── Config / setup ────────────────────────────────────────────────────

    def find_user_config(self, project_dir: Path) -> Path | None:
        """Find the user's opencode config using opencode's own resolution order."""
        import os

        # 1. Explicit config file via env var
        env_config = os.environ.get("OPENCODE_CONFIG", "").strip()
        if env_config:
            p = Path(env_config)
            if p.exists():
                logger.debug(f"Found opencode config via OPENCODE_CONFIG: {p}")
                return p

        # 2. Config directory via env var
        env_config_dir = os.environ.get("OPENCODE_CONFIG_DIR", "").strip()
        if env_config_dir:
            for name in ("opencode.json", "opencode.jsonc", "config.json"):
                p = Path(env_config_dir) / name
                if p.exists():
                    logger.debug(f"Found opencode config via OPENCODE_CONFIG_DIR: {p}")
                    return p

        # 3. Project-local config
        project_dir = project_dir.resolve()
        for name in ("opencode.json", "opencode.jsonc"):
            p = project_dir / name
            if p.exists():
                logger.debug(f"Found project-local opencode config: {p}")
                return p

        # 4. Global XDG config
        xdg_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
        config_base = Path(xdg_home) if xdg_home else Path.home() / ".config"
        for name in ("opencode.json", "opencode.jsonc", "config.json"):
            p = config_base / "opencode" / name
            if p.exists():
                logger.debug(f"Found global opencode config: {p}")
                return p

        # 5. Windows-standard config locations
        if sys.platform == "win32":
            for env_var in ("APPDATA", "LOCALAPPDATA"):
                win_base = os.environ.get(env_var, "").strip()
                if win_base:
                    for name in ("opencode.json", "opencode.jsonc", "config.json"):
                        p = Path(win_base) / "opencode" / name
                        if p.exists():
                            logger.debug(f"Found opencode config via {env_var}: {p}")
                            return p

        return None

    def ensure_setup(self, project_dir: Path, config: ArchitectConfig) -> Path:
        """Ensure The Architect's own planning config and prompts are ready.

        Writes prompts to ``.architect/prompts/`` and The Architect's
        planning config to ``.architect/architect.json``.

        Never reads, writes, or modifies the user's opencode config.

        Args:
            project_dir: The project root directory.
            config: The ArchitectConfig instance.

        Returns:
            Path to ``.architect/architect.json``.
        """
        if isinstance(project_dir, str):
            project_dir = Path(project_dir)

        project_dir = project_dir.resolve()

        # Always write The Architect prompts (versioned with the package)
        self._write_architect_prompts(project_dir)

        # Always write The Architect's own planning config
        architect_cfg = self._write_architect_config(project_dir)

        return architect_cfg

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
            "execution.md",
        ):
            source = package_prompts / filename
            target = prompts_dir / filename
            content = source.read_text(encoding="utf-8")
            target.write_text(content, encoding="utf-8")
            logger.debug(f"Written prompt: {target}")

    def _write_architect_config(self, project_dir: Path) -> Path:
        """Write The Architect's own planning config to .architect/architect.json."""
        import importlib.resources as resources

        project_dir = project_dir.resolve()
        architect_dir = project_dir / ".architect"
        architect_dir.mkdir(parents=True, exist_ok=True)

        package_resources = resources.files("the_architect.resources")
        template_source = package_resources / "opencode_template.json"
        content = template_source.read_text(encoding="utf-8")
        data = json.loads(content)

        # Rewrite prompt paths to absolute
        prompts_dir = architect_dir / "prompts"
        relative_prefix = ".architect/prompts/"
        agents: dict[str, object] = data.get("agent", {}) or {}
        for agent_cfg in agents.values():
            if isinstance(agent_cfg, dict):
                prompt_val = agent_cfg.get("prompt", "")
                if isinstance(prompt_val, str) and relative_prefix in prompt_val:
                    agent_cfg["prompt"] = prompt_val.replace(
                        relative_prefix,
                        str(prompts_dir) + "/",
                    )
        content_out = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

        target = architect_dir / "architect.json"
        target.write_text(content_out, encoding="utf-8")

        logger.info(f"Written The Architect planning config: {target}")
        return target

    def read_info(self, project_dir: Path, config: ArchitectConfig) -> dict[str, str]:
        """Read agent name, model and version from opencode.json and config.

        Returns a dict with keys: agent, model, version, mode.

        Args:
            project_dir: The project root directory.
            config: The ArchitectConfig instance.

        Returns:
            Dict with display info about the active OpenCode setup.
        """
        info: dict[str, str] = {
            "agent": "",
            "model": "",
            "version": self.get_version(),
            "mode": "opencode",
        }

        if config.standalone_mode:
            info["model"] = config.standalone_mode
            info["mode"] = "standalone"
            return info

        resolved = self.get_resolved_model(project_dir)
        if resolved:
            info["model"] = resolved

        oc_json = self.find_user_config(project_dir)
        if oc_json is not None:
            try:
                data = json.loads(oc_json.read_text(encoding="utf-8"))
                info["agent"] = data.get("default_agent", "")
            except (OSError, json.JSONDecodeError, KeyError):
                pass

        if not info["agent"]:
            info["agent"] = _get_default_agent_from_debug_config(project_dir)

        return info

    # ── Command building ──────────────────────────────────────────────────

    @property
    def instruction_via_stdin(self) -> bool:
        """OpenCode receives its instruction as a command-line argument."""
        return False

    def build_command(
        self,
        instruction: str,
        model_override: str | None = None,
        agent_override: str | None = None,
    ) -> list[str]:
        """Build the opencode run command.

        Uses ``--format json`` so opencode emits structured JSON events
        that The Architect can parse for token usage and display rendering.

        Args:
            instruction: The instruction string to pass to opencode run.
            model_override: Optional model name to pass via --model flag.
            agent_override: Optional agent name to pass via --agent flag.

        Returns:
            List of command components ready for subprocess execution.
        """
        opencode_bin = shutil.which("opencode") or "opencode"
        cmd: list[str] = [
            opencode_bin,
            "run",
            "--format",
            "json",
            "--dangerously-skip-permissions",
        ]

        if model_override:
            cmd.extend(["--model", model_override])

        if agent_override:
            if self._agent_flag_broken():
                # --agent is broken in OpenCode ≥ 1.15 (raises "InstanceRef not
                # provided").  Planning is unaffected — architect.json sets
                # default_agent.  For execution_agent, warn the user.
                logger.warning(
                    f"execution_agent='{agent_override}' cannot be applied: "
                    f"the --agent flag is broken in OpenCode ≥ 1.15 "
                    f"(see COMPATIBILITY.md [OC-1]). "
                    f"Set default_agent in your opencode.json to select your agent."
                )
            else:
                cmd.extend(["--agent", agent_override])

        cmd.extend(["--", instruction])

        return cmd

    def get_env_overrides(self, config_override: Path | None = None) -> dict[str, str]:
        """Return extra environment variables for this OpenCode run.

        Args:
            config_override: When set, passed as ``OPENCODE_CONFIG`` so
                opencode uses The Architect's planning config instead of
                the user's config.

        Returns:
            Dict of env var name → value.
        """
        env: dict[str, str] = {}
        if config_override is not None:
            env["OPENCODE_CONFIG"] = str(config_override.resolve())
        return env

    # ── Output parsing ────────────────────────────────────────────────────

    def parse_output_line(self, line: str) -> ParsedEvent | None:
        """Parse a single opencode JSON event line into a ParsedEvent.

        Handles both the current opencode v1.4+ format and legacy format.

        Args:
            line: A single raw line from opencode's stdout.

        Returns:
            A ParsedEvent, or None if the line is not valid JSON.
        """
        import json as _json

        from the_architect.core.provider import ParsedEvent
        from the_architect.core.runner import TokenUsage

        try:
            event = _json.loads(line)
        except (ValueError, _json.JSONDecodeError):
            return None

        etype = event.get("type", "")
        display_lines: list[str] = []
        tokens: TokenUsage | None = None
        rate_limit = False
        model_not_found = False

        # ── Token usage extraction ──────────────────────────────────────
        part = event.get("part", {})
        if isinstance(part, dict):
            part_tokens = part.get("tokens")
            if isinstance(part_tokens, dict):
                cache = part_tokens.get("cache", {})
                tokens = TokenUsage(
                    input_tokens=part_tokens.get("input", 0),
                    output_tokens=part_tokens.get("output", 0),
                    cache_read_tokens=cache.get("read", 0) if isinstance(cache, dict) else 0,
                    cache_write_tokens=cache.get("write", 0) if isinstance(cache, dict) else 0,
                )

        if tokens is None:
            usage = event.get("usage")
            if isinstance(usage, dict):
                tokens = TokenUsage(
                    input_tokens=int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
                    output_tokens=int(
                        usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
                    ),
                    cache_read_tokens=usage.get("cache_read_input_tokens", 0),
                    cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
                )

        # ── Display text extraction ─────────────────────────────────────
        if etype == "text":
            t = (part.get("text") or "").strip() if isinstance(part, dict) else ""
            if t:
                display_lines.extend(t.split("\n"))

        elif etype == "tool_use":
            tool_name = (part.get("tool") or "") if isinstance(part, dict) else ""
            state = part.get("state", {}) if isinstance(part, dict) else {}
            inp = state.get("input", {}) if isinstance(state, dict) else {}
            status = state.get("status", "") if isinstance(state, dict) else ""
            output = state.get("output", "") if isinstance(state, dict) else ""
            tool_title = state.get("title", "") if isinstance(state, dict) else ""
            metadata = state.get("metadata", {}) if isinstance(state, dict) else {}

            def _inp(key: str, alt: str = "") -> str:
                val = inp.get(key, "")
                if not val and alt:
                    val = inp.get(alt, "")
                return str(val) if val else ""

            call_line = _build_tool_call_line(tool_name, inp, _inp)

            if not call_line:
                return ParsedEvent(event_type=etype, display_lines=[], tokens=tokens)

            if status == "completed":
                from the_architect.core.runner import _tool_result_lines

                result_lines = _tool_result_lines(tool_name, output, metadata, tool_title)
                if result_lines:
                    display_lines.append(f"{call_line}  ← {result_lines[0]}")
                    for rl in result_lines[1:]:
                        display_lines.append(f"  {rl}")
                else:
                    display_lines.append(call_line)
            else:
                display_lines.append(call_line)

        elif etype in ("step_start", "step_finish"):
            pass  # No display text needed

        # Legacy format
        elif etype == "assistant":
            for content_part in event.get("message", {}).get("content", []):
                if content_part.get("type") == "text":
                    t = content_part.get("text", "").strip()
                    if t:
                        display_lines.extend(t.split("\n"))
                        break

        elif etype == "tool":
            tool = event.get("tool", {})
            name = tool.get("name", "")
            inp = tool.get("input", {})

            def _leg_inp(key: str, alt: str = "") -> str:
                val = inp.get(key, "")
                if not val and alt:
                    val = inp.get(alt, "")
                return str(val) if val else ""

            call_line = _build_tool_call_line(name, inp, _leg_inp)
            if call_line:
                display_lines.append(call_line)

        # Error handling
        if etype == "error":
            msg = event.get("message", event.get("error", ""))
            display_lines.append(f"Error: {msg}")
            # Rate-limit detection
            from the_architect.core.free_models import is_model_not_found_event, is_rate_limit_event

            if is_rate_limit_event(line):
                rate_limit = True
            if is_model_not_found_event(line):
                model_not_found = True

        return ParsedEvent(
            event_type=etype,
            display_lines=display_lines,
            tokens=tokens,
            rate_limit=rate_limit,
            model_not_found=model_not_found,
        )

    def supports_agents(self) -> bool:
        """OpenCode supports named agent selection."""
        return True

    def supports_json_output(self) -> bool:
        """OpenCode emits structured JSON events."""
        return True

    def supports_free_tier(self) -> bool:
        """Return True if OpenRouter free-tier rotation is available.

        Checks for an ``OPENROUTER_API_KEY`` env var first (fast), then
        falls back to scanning the user's model list for any model prefixed
        with ``openrouter/``.  Either signal is sufficient — the user may
        have OpenRouter configured in opencode.json without a separate env var.

        Returns:
            True if at least one OpenRouter signal is detected.
        """
        import os

        # Fast path: explicit API key
        if os.environ.get("OPENROUTER_API_KEY", "").strip():
            return True

        # Slower path: scan model list for openrouter/ prefix
        try:
            models = self.list_models()
            return any(m.startswith("openrouter/") for m in models)
        except Exception:
            return False

    def check_update_available(self) -> str:
        """Check if an opencode update is available.

        Runs ``opencode --version`` to get the installed version, then
        checks the npm registry for the latest version.  If a newer
        version exists, returns a message with the update command.

        Returns:
            Empty string if up-to-date or check fails; otherwise an
            actionable message like "OpenCode 1.14.28 is installed, but
            1.14.30 is available. Update with: opencode upgrade"
        """
        import re
        import urllib.request

        if not self.is_installed():
            return ""

        # Get installed version
        installed = self.get_version()
        if not installed or installed == "unknown":
            return ""

        # Extract semver from the version string (may include extra text)
        m = re.search(r"(\d+\.\d+\.\d+)", installed)
        if not m:
            return ""
        installed_ver = m.group(1)

        # Check npm registry for latest version
        try:
            req = urllib.request.Request(
                "https://registry.npmjs.org/opencode-ai/latest",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                latest = data.get("version", "")
                if not latest:
                    return ""
        except Exception:
            # Network error, DNS failure, etc. — can't check, don't block
            return ""

        # Compare versions using tuple comparison
        try:
            inst_tuple = tuple(int(x) for x in installed_ver.split("."))
            latest_tuple = tuple(int(x) for x in latest.split("."))
        except (ValueError, AttributeError):
            return ""

        if inst_tuple < latest_tuple:
            return (
                f"OpenCode {installed_ver} is installed, but {latest} is available. "
                f"Update with: opencode upgrade"
            )

        return ""


# ---------------------------------------------------------------------------
# Tool call line builder (shared between v1.4+ and legacy formats)
# ---------------------------------------------------------------------------


def _build_tool_call_line(
    tool_name: str,
    inp: dict[str, Any],
    get_inp: Any,
) -> str:
    """Build a single-line display string for a tool call.

    Args:
        tool_name: The tool name (read, write, bash, etc.).
        inp: The raw input dict from the event.
        get_inp: Callable(key, alt="") -> str for safe key access.

    Returns:
        A display string, or empty string if nothing to show.
    """
    if tool_name in ("write", "edit"):
        path = get_inp("path", "filePath") or get_inp("file_path")
        return f"→ {tool_name} {path}"

    if tool_name == "bash":
        cmd_str = get_inp("command")[:80]
        return f"$ {cmd_str}"

    if tool_name in ("read", "view"):
        path = get_inp("filePath", "file_path") or get_inp("path")
        offset = inp.get("offset")
        limit = inp.get("limit")
        detail = str(path)
        if offset is not None or limit is not None:
            detail += f" (L{offset or 0}"
            if limit:
                detail += f"-{int(offset or 0) + int(limit)}"
            detail += ")"
        return f"→ {tool_name} {detail}"

    if tool_name == "glob":
        pattern = get_inp("pattern")
        path = get_inp("path")
        detail = str(pattern)
        if path:
            detail += f" in {path}"
        return f"→ {tool_name} {detail}"

    if tool_name == "grep":
        pattern = get_inp("pattern")
        include = get_inp("include")
        path = get_inp("path")
        detail = f'"{pattern}"'
        if include:
            detail += f" ({include})"
        if path:
            detail += f" in {path}"
        return f"→ {tool_name} {detail}"

    if tool_name == "ls":
        path = get_inp("path")
        return f"→ ls {path}" if path else "→ ls"

    if tool_name == "fetch":
        url = get_inp("url")
        return f"→ fetch {url}" if url else "→ fetch"

    if tool_name == "diagnostics":
        fpath = get_inp("filePath", "file_path")
        return f"→ diagnostics {fpath}" if fpath else "→ diagnostics"

    if tool_name == "sourcegraph":
        query = get_inp("query")
        return f'→ sourcegraph "{query}"' if query else "→ sourcegraph"

    if tool_name == "todowrite":
        return "→ todowrite"

    if tool_name == "agent":
        prompt_preview = get_inp("prompt")[:60]
        return f"→ agent {prompt_preview}" if prompt_preview else "→ agent"

    if tool_name:
        first_val = ""
        if isinstance(inp, dict):
            for _k, v in inp.items():
                if v and str(v).strip():
                    first_val = str(v)[:60]
                    break
        return f"→ {tool_name} {first_val}" if first_val else f"→ {tool_name}"

    return ""


# ---------------------------------------------------------------------------
# Backward-compat shims (keep old opencode_config.py public API working)
# ---------------------------------------------------------------------------

_provider = OpenCodeProvider()


def check_opencode_installed() -> bool:
    """Check if opencode is on PATH.

    Returns:
        True if opencode is found, False otherwise.
    """
    return _provider.is_installed()


def get_opencode_version() -> str:
    """Get opencode version string.

    Returns:
        Version string, or 'unknown' if version could not be determined.
    """
    return _provider.get_version()


def opencode_has_any_models() -> bool:
    """Check whether opencode can list at least one model.

    Returns:
        True if ``opencode models`` returns at least one model.
    """
    return _provider.has_any_models()


def write_architect_prompts(project_dir: Path) -> None:
    """Write architect.md and execution.md to project_dir/.architect/prompts/.

    Args:
        project_dir: The project root directory.
    """
    _provider._write_architect_prompts(project_dir)


def find_user_opencode_config(project_dir: Path) -> Path | None:
    """Find the user's opencode config.

    Args:
        project_dir: The project root directory.

    Returns:
        Path to the user's opencode config file, or None if not found.
    """
    return _provider.find_user_config(project_dir)


def write_architect_config(project_dir: Path) -> Path:
    """Write The Architect's own planning config to .architect/architect.json.

    Args:
        project_dir: The project root directory.

    Returns:
        Path to the written .architect/architect.json file.
    """
    return _provider._write_architect_config(project_dir)


def list_opencode_models() -> list[str]:
    """Return all models available in the user's OpenCode setup.

    Returns:
        Sorted list of model identifiers.
    """
    return _provider.list_models()


def get_resolved_architect_model(project_dir: Path) -> str:
    """Return the model OpenCode will actually use for the architect agent.

    Args:
        project_dir: The project root directory.

    Returns:
        Model identifier string, or empty string if not determinable.
    """
    return _provider.get_resolved_model(project_dir, "architect")


def list_opencode_agents(project_dir: Path) -> list[str]:
    """Return primary agent names available in the user's OpenCode setup.

    Args:
        project_dir: The project root directory.

    Returns:
        Sorted list of primary agent name strings.
    """
    return _provider.list_agents(project_dir)


def read_opencode_info(project_dir: Path, config: ArchitectConfig) -> dict[str, str]:
    """Read agent name, model and version from opencode.json and config.

    Args:
        project_dir: The project root directory.
        config: The ArchitectConfig instance.

    Returns:
        Dict with display info about the active OpenCode setup.
    """
    return _provider.read_info(project_dir, config)


def check_user_opencode_configured(project_dir: Path) -> bool:
    """Return True if the user has opencode configured (any config found).

    Args:
        project_dir: The project root directory.

    Returns:
        True if a user opencode config exists, False otherwise.
    """
    return _provider.find_user_config(project_dir) is not None


def ensure_opencode_setup(project_dir: Path, config: ArchitectConfig) -> Path:
    """Ensure The Architect's own planning config and prompts are ready.

    Args:
        project_dir: The project root directory.
        config: The ArchitectConfig instance.

    Returns:
        Path to the The Architect planning config at .architect/architect.json.
    """
    return _provider.ensure_setup(project_dir, config)
