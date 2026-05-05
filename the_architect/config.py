"""Config loading and validation for The Architect."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class ArchitectConfig(BaseModel):
    """Configuration for The Architect with sensible defaults."""

    tasks_dir: Path = Field(default=Path("tasks"), description="Directory containing task files")
    progress_file: Path = Field(default=Path("PROGRESS.md"), description="Path to progress tracker")
    log_dir: Path = Field(default=Path(".architect/logs"), description="Directory for log files")
    agents_path: Path = Field(
        default=Path("the_architect/prompts"), description="Path to agent prompts"
    )
    docs_path: Path = Field(default=Path("docs"), description="Path to documentation")

    max_retries: int = Field(default=3, ge=0, description="Maximum retry attempts for failed tasks")
    retry_pause: int = Field(default=30, ge=0, description="Seconds to wait before retrying")
    pause_between_tasks: int = Field(default=10, ge=0, description="Seconds to wait between tasks")

    retry_model_2: str = Field(
        default="",
        description=(
            "Fallback model for retry attempt 2. "
            "Empty string means keep using the same model. "
            "Use openrouter/provider/model format, e.g. openrouter/anthropic/claude-sonnet-4.5"
        ),
    )
    retry_model_3: str = Field(
        default="",
        description=(
            "Fallback model for retry attempt 3. "
            "Empty string means keep using the same model. "
            "Use openrouter/provider/model format, e.g. openrouter/google/gemini-2.5-pro"
        ),
    )

    provider: str = Field(
        default="auto",
        description=(
            "Which AI CLI provider to use for planning and execution. "
            "'auto' — detect: prefer OpenCode if installed, then Codex CLI, "
            "then Claude Code, then fall back to Gemini CLI. "
            "'opencode' — require OpenCode, error if not found. "
            "'codex' — require Codex CLI, error if not found. "
            "'claude-code' — require Claude Code, error if not found. "
            "'gemini-cli' — require Gemini CLI, error if not found."
        ),
    )

    standalone_mode: str = Field(
        default="", description="When set, bypass opencode.json and use this model for all runs"
    )

    execution_agent: str = Field(
        default="",
        description="Agent name from user's opencode.json to use for task execution. "
        "Empty string means use opencode's default_agent.",
    )

    architect_model: str = Field(
        default="",
        description=(
            "Last-used architect model override.  Empty string means use the "
            "provider default.  Pre-fills the architect-model picker on the "
            "next run so the user doesn't have to re-select."
        ),
    )

    last_scope: str = Field(
        default="",
        description=(
            "Last-used task scope ('simple', 'standard', or 'complex').  "
            "Empty string means no preference — defaults to 'standard'.  "
            "Pre-fills the scope picker on the next run."
        ),
    )

    retrospective_rounds: int = Field(
        default=1,
        ge=0,
        description=(
            "Number of retrospective review rounds to run after execution. "
            "Each round runs the reviewer agent, which may create fix-up tasks (R-prefixed) "
            "that are then executed before the next round. Set to 0 to disable. "
            "Default is 1, giving: Execution → Retrospective → Execution → Done. "
            "Use --persistent for 2 rounds with higher retry limits."
        ),
    )

    carry_context: bool = Field(
        default=True,
        description=(
            "When True, inject a summary of the previous attempt's work into retry "
            "instructions — files written, errors detected, bash commands run. "
            "Helps the agent pick up where it left off without re-discovering the "
            "problem from scratch. Set to False to use identical prompts each retry "
            "(Ralph-style)."
        ),
    )

    retry_prompt_mode: str = Field(
        default="focused",
        description=(
            "How to vary the prompt on retry attempts. "
            "'focused' — add structured retry guidance (read PROGRESS.md first, "
            "run tests, fix only what's broken). "
            "'same' — use the identical base prompt each retry (Ralph-style, "
            "relies on files on disk for state). "
        ),
    )

    free_mode: bool = Field(
        default=False,
        description=(
            "When True, use free-tier OpenRouter models for execution. "
            "The Architect fetches all free models from the OpenRouter API, then "
            "rotates through them during execution — switching to the next "
            "free model whenever a rate limit is detected. When all free "
            "models are exhausted, falls back to the user's default model "
            "from opencode.json."
        ),
    )

    persistent: bool = Field(
        default=False,
        description=(
            "When True, enables persistent mode: max_retries is set to 30 "
            "and retrospective_rounds is set to 2. Designed for long-running "
            "autonomous sessions where you want The Architect to keep trying "
            "until the work is genuinely complete."
        ),
    )

    integrity: bool = Field(
        default=True,
        description=(
            "When True, The Architect instructs executors to snapshot existing files "
            "as architect_eval_* before editing, validate rewritten output against "
            "those snapshots, and treat leftover snapshots as corruption signals "
            "during reassessment and retrospective review."
        ),
    )

    # ── Circuit breaker settings ──────────────────────────────────────────

    circuit_no_progress_threshold: int = Field(
        default=3,
        ge=0,
        description=(
            "Number of consecutive attempts with zero file writes before the "
            "circuit opens.  Set to 0 to disable this check."
        ),
    )
    circuit_same_error_threshold: int = Field(
        default=3,
        ge=0,
        description=(
            "Number of consecutive attempts with the same bash error fingerprint "
            "before the circuit opens.  Set to 0 to disable this check."
        ),
    )
    circuit_token_decline_pct: int = Field(
        default=60,
        ge=0,
        le=100,
        description=(
            "Percentage decline in token usage from the first attempt that, "
            "combined with another elevated signal, opens the circuit. "
            "E.g. 60 means: if the latest attempt used <40%% of attempt-1 tokens "
            "AND another counter is elevated, open the circuit.  Set to 0 to disable."
        ),
    )
    circuit_cooldown_minutes: int = Field(
        default=30,
        ge=0,
        description=(
            "Minutes to wait after the circuit opens before allowing a single "
            "test attempt (HALF_OPEN state).  Set to 0 for immediate retry."
        ),
    )
    circuit_enable_replan: bool = Field(
        default=True,
        description=(
            "When True, The Architect may send a failing task back to the architect "
            "agent to be rewritten when all retry models are exhausted and no "
            "file progress was made.  Set to False to always fall back to WAIT."
        ),
    )
    cooldown_detection: bool = Field(
        default=True,
        description=(
            "When True, The Architect detects provider cooldown / rate-limit signals "
            "(HTTP 429, 529, 'rate limit', 'overloaded', etc.) and pauses the "
            "entire run for 1 hour before retrying.  Cooldown waits do not "
            "consume retry slots and do not increment circuit breaker counters. "
            "Set to False to skip cooldown detection and fall through to normal "
            "circuit breaker evaluation."
        ),
    )

    # ── Token budget ──────────────────────────────────────────────────────

    token_budget_per_hour: int = Field(
        default=0,
        ge=0,
        description=(
            "Maximum tokens to spend per rolling hour across the entire run. "
            "0 (default) means no limit — the run continues regardless of token "
            "usage.  When the budget is exceeded, The Architect pauses the run "
            "for the remainder of the current hour (same behaviour as a provider "
            "cooldown wait — no retry slots are consumed, the run resumes "
            "automatically).  A single Claude call can use 100k+ tokens; set "
            "this to e.g. 500000 to cap hourly spend.  The budget resets at the "
            "start of each new hour window."
        ),
    )

    model_config = {"frozen": False, "extra": "ignore"}

    @property
    def project_root(self) -> Path:
        """Return the project root directory derived from progress_file.

        The progress file is always at the project root, so its parent
        is the project root.  Only meaningful after ``resolve()`` has
        been called (which makes all paths absolute).

        Returns:
            The resolved project root directory.
        """
        return self.progress_file.parent

    def resolve(self, project_dir: Path | str) -> ArchitectConfig:
        """Make all paths absolute relative to project_dir.

        Args:
            project_dir: The project root directory

        Returns:
            A new config with resolved absolute paths
        """
        if isinstance(project_dir, str):
            project_dir = Path(project_dir)

        project_dir = project_dir.resolve()

        return ArchitectConfig(
            tasks_dir=(project_dir / self.tasks_dir).resolve(),
            progress_file=(project_dir / self.progress_file).resolve(),
            log_dir=(project_dir / self.log_dir).resolve(),
            agents_path=self.agents_path,
            docs_path=self.docs_path,
            max_retries=self.max_retries,
            retry_pause=self.retry_pause,
            pause_between_tasks=self.pause_between_tasks,
            retry_model_2=self.retry_model_2,
            retry_model_3=self.retry_model_3,
            provider=self.provider,
            standalone_mode=self.standalone_mode,
            execution_agent=self.execution_agent,
            architect_model=self.architect_model,
            last_scope=self.last_scope,
            retrospective_rounds=self.retrospective_rounds,
            carry_context=self.carry_context,
            retry_prompt_mode=self.retry_prompt_mode,
            free_mode=self.free_mode,
            persistent=self.persistent,
            integrity=self.integrity,
            circuit_no_progress_threshold=self.circuit_no_progress_threshold,
            circuit_same_error_threshold=self.circuit_same_error_threshold,
            circuit_token_decline_pct=self.circuit_token_decline_pct,
            circuit_cooldown_minutes=self.circuit_cooldown_minutes,
            circuit_enable_replan=self.circuit_enable_replan,
            cooldown_detection=self.cooldown_detection,
            token_budget_per_hour=self.token_budget_per_hour,
        )


def load_config(project_dir: Path | str) -> ArchitectConfig:
    """Load The Architect config from architect.toml if it exists, else return defaults.

    Args:
        project_dir: The project root directory

    Returns:
        ArchitectConfig with loaded or default values
    """
    if isinstance(project_dir, str):
        project_dir = Path(project_dir)

    config_file = project_dir / "architect.toml"

    if not config_file.exists():
        return ArchitectConfig().resolve(project_dir)

    import tomllib

    with open(config_file, "rb") as f:
        data = tomllib.load(f)

    config_data = data.get("architect", {})
    return ArchitectConfig(**config_data).resolve(project_dir)


def write_config(project_dir: Path | str, updates: dict[str, object]) -> Path:
    """Write or update architect.toml with the given key-value pairs.

    Reads the existing ``architect.toml`` if present, merges in ``updates``
    under the ``[architect]`` section, and writes the result back.  Only
    scalar values (str, int, bool, float) are written — path fields are
    intentionally excluded since they are resolved at runtime.

    Args:
        project_dir: The project root directory.
        updates: Dict of config field names to new values.

    Returns:
        Path to the written ``architect.toml`` file.

    Raises:
        ValueError: If an unknown field name is passed in ``updates``.
        TypeError: If a value type is not supported (not str/int/bool/float).
    """
    if isinstance(project_dir, str):
        project_dir = Path(project_dir)

    # Validate field names against the model
    valid_fields = set(ArchitectConfig.model_fields.keys())
    # Exclude path fields — they are resolved at runtime, not stored in toml
    path_fields = {"tasks_dir", "progress_file", "log_dir", "agents_path", "docs_path"}
    # provider is a string field and IS writable
    writable_fields = valid_fields - path_fields

    for key in updates:
        if key not in writable_fields:
            if key in path_fields:
                raise ValueError(f"'{key}' is a path field — set it directly in architect.toml")
            raise ValueError(
                f"Unknown config field: '{key}'. Valid fields: {sorted(writable_fields)}"
            )

    for key, val in updates.items():
        if not isinstance(val, (str, int, bool, float)):
            raise TypeError(
                f"Config value for '{key}' must be str, int, bool, or float — "
                f"got {type(val).__name__}"
            )

    config_file = project_dir / "architect.toml"

    # Load existing toml data
    existing: dict[str, object] = {}
    if config_file.exists():
        import tomllib

        with open(config_file, "rb") as f:
            existing = tomllib.load(f).get("architect", {})

    # Merge updates
    merged = {**existing, **updates}

    # Serialise to TOML manually — avoids adding a TOML writer dependency
    lines = ["[architect]"]
    for key, val in sorted(merged.items()):
        if isinstance(val, bool):
            lines.append(f"{key} = {'true' if val else 'false'}")
        elif isinstance(val, str):
            escaped = val.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key} = "{escaped}"')
        else:
            lines.append(f"{key} = {val}")

    content = "\n".join(lines) + "\n"
    config_file.write_text(content, encoding="utf-8")
    return config_file


def find_opencode_json(project_dir: Path | str) -> Path | None:
    """Find the user's opencode config using opencode's own resolution order.

    Delegates to ``find_user_opencode_config`` which checks env vars and
    all standard locations.  This is used as a fallback by model/agent
    discovery when ``opencode debug config`` is unavailable.

    Args:
        project_dir: The project root directory.

    Returns:
        Path to the user's opencode config file, or None if not found.
    """
    from the_architect.core.opencode_config import find_user_opencode_config

    if isinstance(project_dir, str):
        project_dir = Path(project_dir)

    return find_user_opencode_config(project_dir)
