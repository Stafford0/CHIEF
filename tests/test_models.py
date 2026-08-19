from chief.models.base import ModelResponse
from chief.models.ollama import OllamaProvider


def test_ollama_provider_identity() -> None:
    provider = OllamaProvider()

    assert provider.name == "ollama"
    assert provider.model == "llama3.1:8b"


def test_model_response() -> None:
    response = ModelResponse(
        content="CHIEF MODEL ONLINE",
        provider="ollama",
        model="llama3.1:8b",
    )

    assert response.content == "CHIEF MODEL ONLINE"
    assert response.provider == "ollama"
    assert response.model == "llama3.1:8b"