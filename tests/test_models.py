from chief.models.base import ModelResponse
from chief.models.ollama import OllamaProvider, visible_response


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


def test_ollama_provider_removes_serialized_reasoning_from_visible_output() -> None:
    assert visible_response(
        "<think>private chain of thought\nwith multiple lines</think>\n\nVisible answer."
    ) == "Visible answer."


def test_ollama_provider_leaves_normal_visible_output_unchanged() -> None:
    assert visible_response("  Normal answer.  ") == "Normal answer."


def test_ollama_provider_handles_orphaned_reasoning_markers() -> None:
    assert visible_response("private reasoning</think>\nVisible answer.") == "Visible answer."
    assert visible_response("Visible prefix.<think>unfinished private reasoning") == "Visible prefix."
