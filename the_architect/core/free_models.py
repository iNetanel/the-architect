"""Free model rotation for The Architect's --free mode.

When the user runs ``architect --free``, The Architect fetches all free-tier models
from the OpenRouter API, then rotates through them during execution.  When a
rate limit is hit on one model, the rotator switches to the next available
free model.  When all free models are exhausted, it falls back to the user's
default model (whatever opencode.json specifies).

The OpenRouter API endpoint ``https://openrouter.ai/api/v1/models`` returns
a JSON list of all models with their pricing.  Free models have
``pricing.prompt == "0"`` and ``pricing.completion == "0"``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx
from loguru import logger

# Default OpenRouter API base URL (path component added by _get_openrouter_models_url)
_OPENROUTER_BASE_URL_DEFAULT = "https://openrouter.ai/api/v1"


def _get_openrouter_models_url() -> str:
    """Return the OpenRouter models API URL.

    Checks OPENROUTER_BASE_URL env var first so users on corporate networks
    or with custom OpenRouter-compatible endpoints can override the default.

    Returns:
        Full URL string for the models endpoint.
    """
    base = os.environ.get("OPENROUTER_BASE_URL", "").rstrip("/")
    if not base:
        base = _OPENROUTER_BASE_URL_DEFAULT
    return f"{base}/models"


# Rate-limit error patterns detected in opencode output / exit behaviour.
# These are checked against the accumulated text and error events from
# opencode to decide whether the current model hit its rate limit.
_RATE_LIMIT_PATTERNS: list[str] = [
    "rate limit",
    "rate_limit",
    "429",
    "too many requests",
    "quota exceeded",
    "capacity",
    "overloaded",
    "temporarily unavailable",
    "server is busy",
    "try again later",
]

# Model-not-found error patterns — these indicate the model doesn't exist
# or isn't available for the requested use (e.g. non-text models used for
# code generation).  In free mode, the rotator should skip to the next model.
_MODEL_NOT_FOUND_PATTERNS: list[str] = [
    "model not found",
    "providermodelnotfounderror",
    "not available for this provider",
    "invalid model",
    "unknown model",
]


@dataclass
class FreeModelInfo:
    """Metadata about a single free-tier model from OpenRouter.

    Attributes:
        id: The model identifier (e.g. ``"openrouter/quen/qwen3-235b-a22b"``).
        name: Human-readable model name.
        context_length: Maximum context length in tokens.
    """

    id: str
    name: str = ""
    context_length: int = 0


@dataclass
class FreeModelRotator:
    """Rotates through free-tier OpenRouter models, switching on rate limits.

    Usage::

        rotator = FreeModelRotator()
        await rotator.fetch_free_models()
        model = rotator.current_model  # first free model
        rotator.mark_rate_limited(model)  # switch to next
        model = rotator.current_model  # second free model

    When all free models are exhausted, ``current_model`` returns ``None``,
    signalling that the caller should fall back to the default model.

    Attributes:
        models: Ordered list of free models (populated by ``fetch_free_models``).
        exhausted: Set of model IDs that have hit rate limits.
        _current_index: Index into ``models`` for the current model.
    """

    models: list[FreeModelInfo] = field(default_factory=list)
    exhausted: set[str] = field(default_factory=set)
    _current_index: int = 0

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    async def fetch_free_models(self) -> None:
        """Fetch free-tier models from the OpenRouter API.

        Filters for models where both prompt and completion pricing are ``"0"``
        (string zero).  Models are sorted by context length descending so
        larger-context models are tried first (they tend to work better for
        coding tasks).

        If the API call fails, logs a warning and leaves ``models`` empty.
        The caller should treat an empty list as "no free models available,
        fall back to default".
        """
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(_get_openrouter_models_url())
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            logger.warning(f"Failed to fetch OpenRouter models: {exc}")
            return
        except Exception as exc:
            logger.warning(f"Unexpected error fetching OpenRouter models: {exc!r}")
            return

        raw_models = data.get("data", [])
        if not isinstance(raw_models, list):
            logger.warning("OpenRouter API returned unexpected format (no 'data' list)")
            return

        free: list[FreeModelInfo] = []
        for m in raw_models:
            if not isinstance(m, dict):
                continue
            pricing = m.get("pricing", {})
            if not isinstance(pricing, dict):
                continue
            prompt_price = str(pricing.get("prompt", "1"))
            completion_price = str(pricing.get("completion", "1"))
            # Free models have "0" as pricing
            if prompt_price == "0" and completion_price == "0":
                model_id = str(m.get("id", ""))
                if not model_id:
                    continue
                # Skip non-text-output models (audio, image generators, etc.)
                # Only keep models whose output is text-only.
                architecture = m.get("architecture", {})
                modality = str(architecture.get("modality", ""))
                # Accepted modalities: "text->text", "text+image->text",
                # "text+image+video->text", "text+image+file->text", etc.
                # Rejected: anything with "->text+audio", "->text+image", etc.
                if modality and not modality.endswith("->text"):
                    continue
                # Prefix with "openrouter/" for opencode compatibility
                if not model_id.startswith("openrouter/"):
                    model_id = f"openrouter/{model_id}"
                free.append(
                    FreeModelInfo(
                        id=model_id,
                        name=str(m.get("name", "")),
                        context_length=int(m.get("context_length", 0)),
                    )
                )

        # Sort by context length descending — larger context = better for coding
        free.sort(key=lambda m: m.context_length, reverse=True)

        self.models = free
        self._current_index = 0
        self.exhausted.clear()

        logger.info(f"Found {len(free)} free-tier models on OpenRouter")
        for m in free[:10]:
            logger.debug(f"  Free model: {m.id} (context: {m.context_length})")
        if len(free) > 10:
            logger.debug(f"  ... and {len(free) - 10} more")

    # ------------------------------------------------------------------
    # Model access
    # ------------------------------------------------------------------

    @property
    def current_model(self) -> str | None:
        """Return the current free model ID, or None if all are exhausted.

        Returns:
            Model identifier string (e.g. ``"openrouter/quen/qwen3-235b-a22b"``),
            or ``None`` when every free model has been rate-limited.
        """
        # Advance past any exhausted models at the current index
        while self._current_index < len(self.models):
            candidate = self.models[self._current_index]
            if candidate.id not in self.exhausted:
                return candidate.id
            self._current_index += 1
        return None

    @property
    def current_model_info(self) -> FreeModelInfo | None:
        """Return metadata for the current free model, or None if exhausted.

        Returns:
            FreeModelInfo for the current model, or None.
        """
        while self._current_index < len(self.models):
            candidate = self.models[self._current_index]
            if candidate.id not in self.exhausted:
                return candidate
            self._current_index += 1
        return None

    @property
    def has_models_available(self) -> bool:
        """Return True if at least one free model is not yet exhausted.

        Used by the circuit breaker to decide whether to hand a rate-limit
        failure to free-mode rotation or to trigger ``COOLDOWN_WAIT``.
        Free mode can be enabled but fully exhausted — this property
        distinguishes the two cases.

        Returns:
            True if there is at least one non-exhausted free model.
        """
        return any(m.id not in self.exhausted for m in self.models)

    @property
    def remaining_count(self) -> int:
        """Number of free models not yet exhausted.

        Returns:
            Count of models still available.
        """
        return sum(1 for m in self.models if m.id not in self.exhausted)

    @property
    def total_count(self) -> int:
        """Total number of free models discovered.

        Returns:
            Total count of models.
        """
        return len(self.models)

    # ------------------------------------------------------------------
    # Rate-limit handling
    # ------------------------------------------------------------------

    def mark_rate_limited(self, model_id: str) -> str | None:
        """Mark a model as rate-limited and advance to the next one.

        Args:
            model_id: The model that hit a rate limit.

        Returns:
            The next available free model ID, or None if all are exhausted.
        """
        self.exhausted.add(model_id)
        logger.info(
            f"Model {model_id} hit rate limit — switching to next free model "
            f"({self.remaining_count} remaining)"
        )
        # Advance past the exhausted model
        return self.current_model

    # ------------------------------------------------------------------
    # Rate-limit detection
    # ------------------------------------------------------------------


def is_rate_limit_error(accumulated_text: str, exit_code: int) -> bool:
    """Detect whether an opencode run failed due to a rate limit.

    Checks both the accumulated text output and the exit code for
    rate-limit indicators.  This is used by the runner to decide
    whether to rotate to the next free model.

    Args:
        accumulated_text: All text output from the opencode agent.
        exit_code: The subprocess exit code.

    Returns:
        True if a rate limit error is detected.
    """
    text_lower = accumulated_text.lower()

    # Check accumulated text for rate-limit patterns
    for pattern in _RATE_LIMIT_PATTERNS:
        if pattern in text_lower:
            return True

    # Exit code 429 is a direct rate-limit signal (some providers use it)
    if exit_code == 429:
        return True

    return False


def is_rate_limit_event(event_line: str) -> bool:
    """Check whether a single JSON event line from opencode indicates a rate limit.

    This enables mid-stream detection — the runner can detect rate limits
    as they happen rather than waiting for the entire run to finish.

    Args:
        event_line: A single JSON event line from opencode's stdout.

    Returns:
        True if the event indicates a rate limit error.
    """
    import json as _json

    try:
        event = _json.loads(event_line)
    except (ValueError, _json.JSONDecodeError):
        return False

    etype = event.get("type", "")
    if etype != "error":
        return False

    message = str(event.get("message", event.get("error", ""))).lower()
    for pattern in _RATE_LIMIT_PATTERNS:
        if pattern in message:
            return True

    # Check for HTTP status code in the error
    status = event.get("status") or event.get("statusCode")
    if status == 429:
        return True

    return False


def is_model_not_found_error(accumulated_text: str, exit_code: int) -> bool:
    """Detect whether an opencode run failed because the model doesn't exist.

    In free mode, this means the rotator should skip this model and try
    the next one — the model is permanently unusable, not just rate-limited.

    Args:
        accumulated_text: All text output from the opencode agent.
        exit_code: The subprocess exit code.

    Returns:
        True if a model-not-found error is detected.
    """
    text_lower = accumulated_text.lower()
    for pattern in _MODEL_NOT_FOUND_PATTERNS:
        if pattern in text_lower:
            return True
    return False


def is_model_not_found_event(event_line: str) -> bool:
    """Check whether a single JSON event line indicates a model-not-found error.

    This enables mid-stream detection so the rotator can skip unusable
    models immediately rather than waiting for the run to finish.

    Args:
        event_line: A single JSON event line from opencode's stdout.

    Returns:
        True if the event indicates a model-not-found error.
    """
    import json as _json

    try:
        event = _json.loads(event_line)
    except (ValueError, _json.JSONDecodeError):
        return False

    etype = event.get("type", "")
    if etype != "error":
        return False

    message = str(event.get("message", event.get("error", ""))).lower()
    for pattern in _MODEL_NOT_FOUND_PATTERNS:
        if pattern in message:
            return True

    return False
