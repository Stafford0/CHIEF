from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chief.api.models import create_models_router
from chief.core.config import Settings
from chief.models.anthropic_messages import AnthropicMessagesProvider
from chief.models.base import ModelPrivacy
from chief.models.openai_responses import OpenAIResponsesProvider


def test_openai_responses_provider_sends_no_retained_conversation_state() -> None:
    seen: dict[str, object] = {}
    ticks = iter([10.0, 10.25])

    def transport(url, headers, body, timeout, max_bytes):
        seen.update(
            {
                "url": url,
                "headers": headers,
                "body": json.loads(body),
                "timeout": timeout,
                "max_bytes": max_bytes,
            }
        )
        return (
            200,
            {
                "model": "configured-openai-model",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "cloud answer"}],
                    }
                ],
            },
        )

    provider = OpenAIResponsesProvider(
        model="configured-openai-model",
        api_key_provider=lambda: "secret-key",
        transport=transport,
        clock=lambda: next(ticks),
    )
    response = provider.generate("hello", "system rules")

    assert seen["url"] == "https://api.openai.com/v1/responses"
    assert seen["headers"]["Authorization"] == "Bearer secret-key"
    assert seen["body"]["store"] is False
    assert seen["body"]["input"] == [
        {"role": "system", "content": "system rules"},
        {"role": "user", "content": "hello"},
    ]
    assert response.content == "cloud answer"
    assert response.provider == "openai"
    assert response.latency_ms == 250.0
    assert provider.capabilities.privacy == ModelPrivacy.CLOUD


def test_anthropic_messages_provider_uses_messages_contract() -> None:
    seen: dict[str, object] = {}
    ticks = iter([5.0, 5.1])

    def transport(url, headers, body, timeout, max_bytes):
        seen.update({"url": url, "headers": headers, "body": json.loads(body)})
        return (
            200,
            {
                "model": "configured-anthropic-model",
                "content": [{"type": "text", "text": "anthropic answer"}],
            },
        )

    provider = AnthropicMessagesProvider(
        model="configured-anthropic-model",
        api_key_provider=lambda: "anthropic-secret",
        max_tokens=777,
        transport=transport,
        clock=lambda: next(ticks),
    )
    response = provider.generate("hello", "system rules")

    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "anthropic-secret"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["body"] == {
        "model": "configured-anthropic-model",
        "max_tokens": 777,
        "messages": [{"role": "user", "content": "hello"}],
        "system": "system rules",
    }
    assert response.content == "anthropic answer"
    assert response.provider == "anthropic"


def test_cloud_api_requires_global_and_per_call_authorization(monkeypatch) -> None:
    monkeypatch.setenv("CHIEF_OPENAI_MODEL", "configured-openai-model")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    disabled = Settings.from_env()
    app = FastAPI()
    app.include_router(create_models_router(settings=disabled))
    client = TestClient(app)

    blocked = client.post(
        "/models/cloud/generate",
        json={"prompt": "hello", "cloud_authorized": True},
    )
    assert blocked.status_code == 403

    monkeypatch.setenv("CHIEF_CLOUD_MODEL_FALLBACK_ENABLED", "true")
    enabled = Settings.from_env()
    app2 = FastAPI()
    app2.include_router(create_models_router(settings=enabled))
    client2 = TestClient(app2)

    missing_per_call = client2.post(
        "/models/cloud/generate",
        json={"prompt": "hello", "cloud_authorized": False},
    )
    assert missing_per_call.status_code == 403


def test_cloud_status_does_not_expose_credentials(monkeypatch) -> None:
    monkeypatch.setenv("CHIEF_CLOUD_MODEL_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("CHIEF_OPENAI_MODEL", "configured-openai-model")
    monkeypatch.setenv("OPENAI_API_KEY", "never-return-this")
    settings = Settings.from_env()
    app = FastAPI()
    app.include_router(create_models_router(settings=settings))
    body = TestClient(app).get("/models/cloud").json()

    assert body["configured"] is True
    assert body["global_fallback_enabled"] is True
    assert body["per_call_authorization_required"] is True
    assert "never-return-this" not in json.dumps(body)
