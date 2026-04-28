"""Tests for free model rotation and rate-limit detection."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from the_architect.core.free_models import (
    FreeModelInfo,
    FreeModelRotator,
    _get_openrouter_models_url,
    is_model_not_found_error,
    is_model_not_found_event,
    is_rate_limit_error,
    is_rate_limit_event,
)

# ---------------------------------------------------------------------------
# FreeModelRotator — model access
# ---------------------------------------------------------------------------


class TestFreeModelRotatorAccess:
    """Tests for FreeModelRotator current_model / remaining_count."""

    def test_empty_rotator_returns_none(self) -> None:
        """Rotator with no models should return None for current_model."""
        rotator = FreeModelRotator()
        assert rotator.current_model is None
        assert rotator.remaining_count == 0
        assert rotator.total_count == 0

    def test_current_model_returns_first(self) -> None:
        """Rotator should return the first model initially."""
        rotator = FreeModelRotator(
            models=[
                FreeModelInfo(id="openrouter/model-a", name="Model A", context_length=32000),
                FreeModelInfo(id="openrouter/model-b", name="Model B", context_length=128000),
            ]
        )
        assert rotator.current_model == "openrouter/model-a"
        assert rotator.remaining_count == 2

    def test_current_model_info(self) -> None:
        """current_model_info should return metadata for the current model."""
        rotator = FreeModelRotator(
            models=[FreeModelInfo(id="openrouter/model-a", name="Model A", context_length=32000)]
        )
        info = rotator.current_model_info
        assert info is not None
        assert info.id == "openrouter/model-a"
        assert info.name == "Model A"
        assert info.context_length == 32000

    def test_current_model_info_advances_past_exhausted(self) -> None:
        """Exhausted models must be skipped — returning the next valid info."""
        rotator = FreeModelRotator(
            models=[
                FreeModelInfo(id="openrouter/model-a", context_length=32000),
                FreeModelInfo(id="openrouter/model-b", context_length=16000),
            ]
        )
        rotator.mark_rate_limited("openrouter/model-a")
        info = rotator.current_model_info
        assert info is not None
        assert info.id == "openrouter/model-b"

    def test_current_model_info_none_when_all_exhausted(self) -> None:
        rotator = FreeModelRotator(
            models=[FreeModelInfo(id="openrouter/model-a", context_length=32000)]
        )
        rotator.mark_rate_limited("openrouter/model-a")
        assert rotator.current_model_info is None

    def test_has_models_available_false_when_empty(self) -> None:
        """An empty rotator must report no models available."""
        rotator = FreeModelRotator()
        assert rotator.has_models_available is False

    def test_has_models_available_tracks_exhaustion(self) -> None:
        rotator = FreeModelRotator(
            models=[
                FreeModelInfo(id="openrouter/model-a", context_length=32000),
                FreeModelInfo(id="openrouter/model-b", context_length=16000),
            ]
        )
        assert rotator.has_models_available is True
        rotator.mark_rate_limited("openrouter/model-a")
        assert rotator.has_models_available is True
        rotator.mark_rate_limited("openrouter/model-b")
        assert rotator.has_models_available is False


# ---------------------------------------------------------------------------
# FreeModelRotator — rate-limit rotation
# ---------------------------------------------------------------------------


class TestFreeModelRotatorRotation:
    """Tests for FreeModelRotator model rotation on rate limits."""

    def test_mark_rate_limited_advances(self) -> None:
        """Marking a model as rate-limited should advance to the next."""
        rotator = FreeModelRotator(
            models=[
                FreeModelInfo(id="openrouter/model-a", context_length=32000),
                FreeModelInfo(id="openrouter/model-b", context_length=128000),
            ]
        )
        assert rotator.current_model == "openrouter/model-a"

        next_model = rotator.mark_rate_limited("openrouter/model-a")
        assert next_model == "openrouter/model-b"
        assert rotator.current_model == "openrouter/model-b"
        assert rotator.remaining_count == 1

    def test_mark_rate_limited_exhausted(self) -> None:
        """When all models are rate-limited, current_model should return None."""
        rotator = FreeModelRotator(
            models=[
                FreeModelInfo(id="openrouter/model-a", context_length=32000),
                FreeModelInfo(id="openrouter/model-b", context_length=128000),
            ]
        )
        rotator.mark_rate_limited("openrouter/model-a")
        result = rotator.mark_rate_limited("openrouter/model-b")
        assert result is None
        assert rotator.current_model is None
        assert rotator.remaining_count == 0

    def test_mark_rate_limited_unknown_model(self) -> None:
        """Marking an unknown model as rate-limited should add it to exhausted."""
        rotator = FreeModelRotator(
            models=[FreeModelInfo(id="openrouter/model-a", context_length=32000)]
        )
        rotator.mark_rate_limited("openrouter/unknown-model")
        # The current model should still be available
        assert rotator.current_model == "openrouter/model-a"
        assert rotator.remaining_count == 1

    def test_rotation_through_all_models(self) -> None:
        """Should rotate through all models in order."""
        rotator = FreeModelRotator(
            models=[
                FreeModelInfo(id="openrouter/model-a", context_length=32000),
                FreeModelInfo(id="openrouter/model-b", context_length=64000),
                FreeModelInfo(id="openrouter/model-c", context_length=128000),
            ]
        )
        assert rotator.current_model == "openrouter/model-a"
        rotator.mark_rate_limited("openrouter/model-a")
        assert rotator.current_model == "openrouter/model-b"
        rotator.mark_rate_limited("openrouter/model-b")
        assert rotator.current_model == "openrouter/model-c"
        rotator.mark_rate_limited("openrouter/model-c")
        assert rotator.current_model is None


# ---------------------------------------------------------------------------
# FreeModelRotator — fetch_free_models
# ---------------------------------------------------------------------------


class TestFreeModelRotatorFetch:
    """Tests for FreeModelRotator.fetch_free_models()."""

    @pytest.mark.asyncio
    async def test_fetch_filters_free_models(self) -> None:
        """Should only include models with pricing.prompt=0 and pricing.completion=0."""
        api_response = {
            "data": [
                {
                    "id": "free-model-a",
                    "name": "Free Model A",
                    "context_length": 32000,
                    "pricing": {"prompt": "0", "completion": "0"},
                },
                {
                    "id": "paid-model-b",
                    "name": "Paid Model B",
                    "context_length": 128000,
                    "pricing": {"prompt": "0.001", "completion": "0.002"},
                },
                {
                    "id": "free-model-c",
                    "name": "Free Model C",
                    "context_length": 64000,
                    "pricing": {"prompt": "0", "completion": "0"},
                },
            ]
        }

        # httpx response.json() is synchronous, not async
        mock_response = AsyncMock()
        mock_response.json = lambda: api_response
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("the_architect.core.free_models.httpx.AsyncClient", return_value=mock_client):
            rotator = FreeModelRotator()
            await rotator.fetch_free_models()

        assert rotator.total_count == 2
        # Sorted by context_length descending
        assert rotator.models[0].id == "openrouter/free-model-c"  # 64000
        assert rotator.models[1].id == "openrouter/free-model-a"  # 32000

    @pytest.mark.asyncio
    async def test_fetch_handles_api_failure(self) -> None:
        """Should handle API failures gracefully, leaving models empty."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Network error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("the_architect.core.free_models.httpx.AsyncClient", return_value=mock_client):
            rotator = FreeModelRotator()
            await rotator.fetch_free_models()

        assert rotator.total_count == 0

    @pytest.mark.asyncio
    async def test_fetch_prefixes_openrouter(self) -> None:
        """Should prefix model IDs with 'openrouter/' if not already present."""
        api_response = {
            "data": [
                {
                    "id": "some-provider/free-model",
                    "name": "Free Model",
                    "context_length": 32000,
                    "pricing": {"prompt": "0", "completion": "0"},
                },
            ]
        }

        mock_response = AsyncMock()
        mock_response.json = lambda: api_response
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("the_architect.core.free_models.httpx.AsyncClient", return_value=mock_client):
            rotator = FreeModelRotator()
            await rotator.fetch_free_models()

        assert rotator.models[0].id == "openrouter/some-provider/free-model"

    @pytest.mark.asyncio
    async def test_fetch_returns_silently_on_http_error(self) -> None:
        """An httpx.HTTPError must be caught and leave ``models`` empty."""
        import httpx

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("network down"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "the_architect.core.free_models.httpx.AsyncClient",
            return_value=mock_client,
        ):
            rotator = FreeModelRotator()
            await rotator.fetch_free_models()

        assert rotator.total_count == 0
        assert rotator.models == []

    @pytest.mark.asyncio
    async def test_fetch_ignores_response_without_data_list(self) -> None:
        """A response whose ``data`` is not a list yields an empty model list."""
        api_response = {"data": "not a list"}
        mock_response = AsyncMock()
        mock_response.json = lambda: api_response
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "the_architect.core.free_models.httpx.AsyncClient",
            return_value=mock_client,
        ):
            rotator = FreeModelRotator()
            await rotator.fetch_free_models()

        assert rotator.total_count == 0

    @pytest.mark.asyncio
    async def test_fetch_skips_malformed_entries(self) -> None:
        """Non-dict entries, non-dict pricing, and missing IDs must be skipped."""
        api_response = {
            "data": [
                "string-not-dict",
                {"id": "bad-pricing", "pricing": "not-a-dict"},
                {"id": "", "pricing": {"prompt": "0", "completion": "0"}},
                # One valid free model so we confirm the good path still runs.
                {
                    "id": "good/free",
                    "name": "Good",
                    "context_length": 32000,
                    "pricing": {"prompt": "0", "completion": "0"},
                },
            ]
        }
        mock_response = AsyncMock()
        mock_response.json = lambda: api_response
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "the_architect.core.free_models.httpx.AsyncClient",
            return_value=mock_client,
        ):
            rotator = FreeModelRotator()
            await rotator.fetch_free_models()

        # Only the one well-formed free model survives.
        assert rotator.total_count == 1
        assert rotator.models[0].id == "openrouter/good/free"

    @pytest.mark.asyncio
    async def test_fetch_no_double_prefix(self) -> None:
        """Should not double-prefix if model ID already starts with 'openrouter/'."""
        api_response = {
            "data": [
                {
                    "id": "openrouter/some-provider/free-model",
                    "name": "Free Model",
                    "context_length": 32000,
                    "pricing": {"prompt": "0", "completion": "0"},
                },
            ]
        }

        mock_response = AsyncMock()
        mock_response.json = lambda: api_response
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("the_architect.core.free_models.httpx.AsyncClient", return_value=mock_client):
            rotator = FreeModelRotator()
            await rotator.fetch_free_models()

        assert rotator.models[0].id == "openrouter/some-provider/free-model"


# ---------------------------------------------------------------------------
# Rate-limit detection — is_rate_limit_error
# ---------------------------------------------------------------------------


class TestIsRateLimitError:
    """Tests for is_rate_limit_error()."""

    def test_rate_limit_text(self) -> None:
        """Should detect 'rate limit' in text."""
        assert is_rate_limit_error("Error: rate limit exceeded", 0) is True

    def test_429_text(self) -> None:
        """Should detect '429' in text."""
        assert is_rate_limit_error("HTTP 429 Too Many Requests", 0) is True

    def test_quota_exceeded(self) -> None:
        """Should detect 'quota exceeded' in text."""
        assert is_rate_limit_error("Error: quota exceeded for this model", 0) is True

    def test_too_many_requests(self) -> None:
        """Should detect 'too many requests' in text."""
        assert is_rate_limit_error("too many requests in the last minute", 0) is True

    def test_overloaded(self) -> None:
        """Should detect 'overloaded' in text."""
        assert is_rate_limit_error("Model is overloaded, try again later", 0) is True

    def test_exit_code_429(self) -> None:
        """Should detect exit code 429 as rate limit."""
        assert is_rate_limit_error("some error", 429) is True

    def test_normal_error_not_rate_limit(self) -> None:
        """Should NOT detect normal errors as rate limit."""
        assert is_rate_limit_error("File not found", 1) is False

    def test_case_insensitive(self) -> None:
        """Should detect rate limit patterns case-insensitively."""
        assert is_rate_limit_error("RATE LIMIT exceeded", 0) is True
        assert is_rate_limit_error("Rate_Limit hit", 0) is True

    def test_empty_text(self) -> None:
        """Should return False for empty text and normal exit code."""
        assert is_rate_limit_error("", 0) is False


# ---------------------------------------------------------------------------
# Rate-limit detection — is_rate_limit_event
# ---------------------------------------------------------------------------


class TestIsRateLimitEvent:
    """Tests for is_rate_limit_event()."""

    def test_rate_limit_error_event(self) -> None:
        """Should detect rate-limit error event."""
        event = json.dumps({"type": "error", "message": "rate limit exceeded"})
        assert is_rate_limit_event(event) is True

    def test_429_error_event(self) -> None:
        """Should detect 429 status code in error event."""
        event = json.dumps({"type": "error", "message": "Request failed", "status": 429})
        assert is_rate_limit_event(event) is True

    def test_normal_error_event(self) -> None:
        """Should NOT detect normal error event as rate limit."""
        event = json.dumps({"type": "error", "message": "File not found"})
        assert is_rate_limit_event(event) is False

    def test_non_error_event(self) -> None:
        """Should NOT detect non-error events as rate limit."""
        event = json.dumps({"type": "text", "part": {"text": "rate limit"}})
        assert is_rate_limit_event(event) is False

    def test_non_json_line(self) -> None:
        """Should return False for non-JSON lines."""
        assert is_rate_limit_event("not json") is False

    def test_capacity_error(self) -> None:
        """Should detect 'capacity' in error event."""
        event = json.dumps({"type": "error", "message": "Provider has reached capacity"})
        assert is_rate_limit_event(event) is True

    def test_temporarily_unavailable(self) -> None:
        """Should detect 'temporarily unavailable' in error event."""
        event = json.dumps({"type": "error", "message": "Service temporarily unavailable"})
        assert is_rate_limit_event(event) is True


# ---------------------------------------------------------------------------
# _get_openrouter_models_url (T14.6)
# ---------------------------------------------------------------------------


class TestGetOpenrouterModelsUrl:
    """Tests for _get_openrouter_models_url() — T11 URL env-var override."""

    def test_returns_default_url_when_env_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return the default OpenRouter models URL when env var is absent."""
        monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
        result = _get_openrouter_models_url()
        assert result == "https://openrouter.ai/api/v1/models"

    def test_returns_custom_url_when_env_is_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should append /models to the custom base URL from OPENROUTER_BASE_URL."""
        monkeypatch.setenv("OPENROUTER_BASE_URL", "https://custom.proxy.example.com/v1")
        result = _get_openrouter_models_url()
        assert result == "https://custom.proxy.example.com/v1/models"

    def test_strips_trailing_slash_from_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should strip a trailing slash before appending /models."""
        monkeypatch.setenv("OPENROUTER_BASE_URL", "https://custom.proxy.example.com/v1/")
        result = _get_openrouter_models_url()
        assert result == "https://custom.proxy.example.com/v1/models"
        # Must not double-slash
        assert "//" not in result.split("://", 1)[1]

    def test_empty_env_var_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty OPENROUTER_BASE_URL should behave like the env var not being set."""
        monkeypatch.setenv("OPENROUTER_BASE_URL", "")
        result = _get_openrouter_models_url()
        assert result == "https://openrouter.ai/api/v1/models"


# ---------------------------------------------------------------------------
# Model-not-found detection
# ---------------------------------------------------------------------------


class TestIsModelNotFoundError:
    """Tests for is_model_not_found_error (post-run text detection)."""

    def test_provider_model_not_found(self) -> None:
        """Should detect ProviderModelNotFoundError in output."""
        text = (
            "ProviderModelNotFoundError: ProviderModelNotFoundError\n"
            "data: {providerID: 'openrouter'}"
        )
        assert is_model_not_found_error(text, 1) is True

    def test_model_not_found_message(self) -> None:
        """Should detect 'Model not found' in output."""
        text = (
            "Error: {'name': 'UnknownError', 'data': {'message': "
            "'Model not found: openrouter/google/lyria-3-pro-preview.'}}"
        )
        assert is_model_not_found_error(text, 1) is True

    def test_model_not_available(self) -> None:
        """Should detect 'not available for this provider' in output."""
        assert is_model_not_found_error("The model is not available for this provider", 1) is True

    def test_invalid_model(self) -> None:
        """Should detect 'invalid model' in output."""
        assert is_model_not_found_error("Error: invalid model specified", 1) is True

    def test_unknown_model(self) -> None:
        """Should detect 'unknown model' in output."""
        assert is_model_not_found_error("Error: unknown model identifier", 1) is True

    def test_normal_error_not_model_not_found(self) -> None:
        """Normal errors should not trigger model-not-found detection."""
        assert is_model_not_found_error("SyntaxError: invalid syntax", 1) is False

    def test_rate_limit_not_model_not_found(self) -> None:
        """Rate limit errors should not trigger model-not-found detection."""
        assert is_model_not_found_error("rate limit exceeded", 429) is False

    def test_case_insensitive(self) -> None:
        """Detection should be case-insensitive."""
        assert is_model_not_found_error("MODEL NOT FOUND", 1) is True
        assert is_model_not_found_error("ProviderModelNotFoundError", 1) is True

    def test_empty_text(self) -> None:
        """Empty text should not trigger detection."""
        assert is_model_not_found_error("", 1) is False


class TestIsModelNotFoundEvent:
    """Tests for is_model_not_found_event (mid-stream JSON event detection)."""

    def test_model_not_found_event(self) -> None:
        """Should detect model-not-found in JSON error event."""
        event = (
            '{"type":"error","message":"Model not found: openrouter/google/lyria-3-pro-preview"}'
        )
        assert is_model_not_found_event(event) is True

    def test_provider_model_not_found_event(self) -> None:
        """Should detect ProviderModelNotFoundError in JSON error event."""
        event = (
            '{"type":"error",'
            '"message":"ProviderModelNotFoundError: data: {providerID: openrouter}"}'
        )
        assert is_model_not_found_event(event) is True

    def test_normal_error_event_not_detected(self) -> None:
        """Normal error events should not trigger detection."""
        event = '{"type":"error","message":"Something went wrong"}'
        assert is_model_not_found_event(event) is False

    def test_non_error_event_not_detected(self) -> None:
        """Non-error events should not trigger detection."""
        event = '{"type":"text","message":"Model not found in the list"}'
        assert is_model_not_found_event(event) is False

    def test_non_json_line_not_detected(self) -> None:
        """Non-JSON lines should not trigger detection."""
        assert is_model_not_found_event("Model not found: something") is False


class TestFreeModelModalityFilter:
    """Tests for the modality filter in fetch_free_models."""

    @pytest.mark.asyncio
    async def test_audio_models_filtered_out(self) -> None:
        """Models with audio output should be excluded from free model list."""
        api_response = {
            "data": [
                {
                    "id": "good-model",
                    "name": "Good Text Model",
                    "context_length": 131072,
                    "pricing": {"prompt": "0", "completion": "0"},
                    "architecture": {"modality": "text->text"},
                },
                {
                    "id": "audio-model",
                    "name": "Audio Model",
                    "context_length": 1048576,
                    "pricing": {"prompt": "0", "completion": "0"},
                    "architecture": {"modality": "text+image->text+audio"},
                },
            ]
        }

        mock_response = AsyncMock()
        mock_response.json = lambda: api_response
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("the_architect.core.free_models.httpx.AsyncClient", return_value=mock_client):
            rotator = FreeModelRotator()
            await rotator.fetch_free_models()

        model_ids = [m.id for m in rotator.models]
        assert "openrouter/good-model" in model_ids
        assert "openrouter/audio-model" not in model_ids

    @pytest.mark.asyncio
    async def test_text_only_models_kept(self) -> None:
        """Models with text-only output should be included."""
        api_response = {
            "data": [
                {
                    "id": "text-model",
                    "name": "Text Model",
                    "context_length": 131072,
                    "pricing": {"prompt": "0", "completion": "0"},
                    "architecture": {"modality": "text->text"},
                },
                {
                    "id": "vision-model",
                    "name": "Vision Model",
                    "context_length": 131072,
                    "pricing": {"prompt": "0", "completion": "0"},
                    "architecture": {"modality": "text+image->text"},
                },
            ]
        }

        mock_response = AsyncMock()
        mock_response.json = lambda: api_response
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("the_architect.core.free_models.httpx.AsyncClient", return_value=mock_client):
            rotator = FreeModelRotator()
            await rotator.fetch_free_models()

        model_ids = [m.id for m in rotator.models]
        assert "openrouter/text-model" in model_ids
        assert "openrouter/vision-model" in model_ids
