from __future__ import annotations

import os
from collections.abc import Callable

from chief.core.config import Settings
from chief.models.anthropic_messages import AnthropicMessagesProvider
from chief.models.openai_responses import OpenAIResponsesProvider
from chief.models.router import ModelRouter

SecretGetter = Callable[[str], str | None]


def _environment_secret(name: str) -> str | None:
    return os.getenv(name, "").strip() or None


def build_cloud_model_router(
    settings: Settings | None = None,
    *,
    secret_getter: SecretGetter = _environment_secret,
) -> ModelRouter | None:
    """Build configured cloud providers without authorizing any transmission."""

    settings = settings or Settings.from_env()
    providers = []
    if settings.openai_model is not None and secret_getter("OPENAI_API_KEY") is not None:
        providers.append(
            OpenAIResponsesProvider(
                model=settings.openai_model,
                api_key_provider=lambda: secret_getter("OPENAI_API_KEY"),
                timeout_seconds=settings.model_timeout_seconds,
                max_response_bytes=settings.max_model_response_bytes,
            )
        )
    if settings.anthropic_model is not None and secret_getter("ANTHROPIC_API_KEY") is not None:
        providers.append(
            AnthropicMessagesProvider(
                model=settings.anthropic_model,
                api_key_provider=lambda: secret_getter("ANTHROPIC_API_KEY"),
                max_tokens=settings.anthropic_max_tokens,
                timeout_seconds=settings.model_timeout_seconds,
                max_response_bytes=settings.max_model_response_bytes,
            )
        )
    return ModelRouter(providers) if providers else None
