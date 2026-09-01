import pytest

from chief.models.anthropic import AnthropicProvider
from chief.models.base import ModelPrivacy, ModelResponse
from chief.models.gemini import GeminiProvider
from chief.models.ollama import OllamaProvider, visible_response
from chief.models.openai import OpenAIProvider
from chief.models.perplexity import PerplexityProvider


def test_ollama_provider_identity() -> None:
    provider = OllamaProvider()

    assert provider.name == "ollama"
    assert provider.model == "llama3.1:8b"
    assert "general" in provider.capabilities.specialties


@pytest.mark.parametrize(
    ("provider_cls", "expected_name", "expected_specialty"),
    [
        (AnthropicProvider, "anthropic", "coding"),
        (GeminiProvider, "gemini", "research"),
        (PerplexityProvider, "perplexity", "signals"),
        (OpenAIProvider, "openai", "voice"),
    ],
)
def test_cloud_provider_identity_and_specialty(
    provider_cls, expected_name, expected_specialty
) -> None:
    provider = provider_cls(api_key="test-key")

    assert provider.name == expected_name
    assert provider.capabilities.privacy == ModelPrivacy.CLOUD
    assert expected_specialty in provider.capabilities.specialties


@pytest.mark.parametrize(
    "provider_cls", [AnthropicProvider, GeminiProvider, PerplexityProvider, OpenAIProvider]
)
def test_cloud_provider_requires_api_key(provider_cls) -> None:
    with pytest.raises(ValueError, match="API key"):
        provider_cls(api_key="")


def test_model_response() -> None:
    response = ModelResponse(
        content="CHIEF MODEL ONLINE",
        provider="ollama",
        model="llama3.1:8b",
    )

    assert response.content == "CHIEF MODEL ONLINE"
    assert response.provider == "ollama"
    assert response.model == "llama3.1:8b"


def test_ollama_provider_removes_serialized_reasoning_from_visible_output() -> None:
    assert visible_response(
        "<think>private chain of thought\nwith multiple lines</think>\n\nVisible answer."
    ) == "Visible answer."


def test_ollama_provider_leaves_normal_visible_output_unchanged() -> None:
    assert visible_response("  Normal answer.  ") == "Normal answer."


def test_ollama_provider_handles_orphaned_reasoning_markers() -> None:
    assert visible_response("private reasoning</think>\nVisible answer.") == "Visible answer."
    assert visible_response("Visible prefix.<think>unfinished private reasoning") == "Visible prefix."
