from __future__ import annotations

import os

from chief.core.config import Settings
from chief.models.anthropic_messages import AnthropicMessagesProvider
from chief.models.openai_responses import OpenAIResponsesProvider
from chief.models.router import ModelRouter


def _secret(name: str) -> str | None:
    return os.getenv(name, "").strip() or None


def build_cloud_model_router(settings: Settings | None = None) -> ModelRouter | None:
    """Build configured cloud providers without authorizing any transmission."""

    settings = settings or Settings.from_env()
    providers = []
    if settings.openai_model is not None and _secret("OPENAI_API_KEY") is not None:
        providers.append(
            OpenAIResponsesProvider(
                model=settings.openai_model,
                api_key_provider=lambda: _secret("OPENAI_API_KEY"),
                timeout_seconds=settings.model_timeout_seconds,
                max_response_bytes=settings.max_model_response_bytes,
            )
        )
    if settings.anthropic_model is not None and _secret("ANTHROPIC_API_KEY") is not None:
        providers.append(
            AnthropicMessagesProvider(
                model=settings.anthropic_model,
                api_key_provider=lambda: _secret("ANTHROPIC_API_KEY"),
                max_tokens=settings.anthropic_max_tokens,
                timeout_seconds=settings.model_timeout_seconds,
                max_response_bytes=settings.max_model_response_bytes,
            )
        )
    return ModelRouter(providers) if providers else None
