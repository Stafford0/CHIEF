from fastapi.testclient import TestClient

from chief.core import app as app_module
from chief.core.session_store import SessionStore
from chief.memory.manager import MemoryManager
from chief.memory.sqlite import SQLiteMemoryStore


class FakeModelResponse:
    def __init__(self, content: str) -> None:
        self.content = content
        self.provider = "fake"
        self.model = "fake-model"


class FakeModelProvider:
    def __init__(self) -> None:
        self.calls = []

    def generate(
        self,
        prompt: str,
        system_prompt: str,
    ) -> FakeModelResponse:
        self.calls.append(
            {
                "prompt": prompt,
                "system_prompt": system_prompt,
            }
        )

        return FakeModelResponse("Test response")


class SilentUltronProvider:
    def generate(self, prompt: str, system_prompt: str) -> FakeModelResponse:
        return FakeModelResponse("[[SILENT]]")


def make_client(tmp_path, monkeypatch):
    store = SQLiteMemoryStore(tmp_path / "test_chief.db")
    memory_manager = MemoryManager(store)
    session_store = SessionStore()
    model_provider = FakeModelProvider()

    monkeypatch.setattr(
        app_module,
        "memory_store",
        store,
    )

    monkeypatch.setattr(
        app_module,
        "memory_manager",
        memory_manager,
    )

    monkeypatch.setattr(
        app_module,
        "session_store",
        session_store,
    )

    monkeypatch.setattr(
        app_module,
        "model_provider",
        model_provider,
    )

    monkeypatch.setattr(
        app_module,
        "ultron_provider",
        SilentUltronProvider(),
    )

    return (
        TestClient(app_module.app),
        session_store,
        memory_manager,
        model_provider,
    )


def test_new_chat_creates_session(
    tmp_path,
    monkeypatch,
) -> None:
    client, session_store, _, _ = make_client(
        tmp_path,
        monkeypatch,
    )

    response = client.post(
        "/chat",
        json={
            "message": "Hello CHIEF.",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["session_id"]
    assert session_store.count() == 1


def test_same_session_receives_previous_context(
    tmp_path,
    monkeypatch,
) -> None:
    client, _, _, model_provider = make_client(
        tmp_path,
        monkeypatch,
    )

    first_response = client.post(
        "/chat",
        json={
            "message": ("My test vehicle is called Falcon."),
        },
    )

    session_id = first_response.json()["session_id"]

    client.post(
        "/chat",
        json={
            "message": ("What did I call my test vehicle?"),
            "session_id": session_id,
        },
    )

    second_call = model_provider.calls[1]

    assert "USER: My test vehicle is called Falcon." in second_call["system_prompt"]

    assert "CHIEF: Test response" in second_call["system_prompt"]


def test_different_sessions_are_isolated(
    tmp_path,
    monkeypatch,
) -> None:
    client, _, _, model_provider = make_client(
        tmp_path,
        monkeypatch,
    )

    first_response = client.post(
        "/chat",
        json={
            "message": ("My test vehicle is called Falcon."),
        },
    )

    first_session_id = first_response.json()["session_id"]

    second_response = client.post(
        "/chat",
        json={
            "message": ("My project is called Nightfall."),
        },
    )

    second_session_id = second_response.json()["session_id"]

    assert first_session_id != second_session_id

    client.post(
        "/chat",
        json={
            "message": ("What project did I mention?"),
            "session_id": second_session_id,
        },
    )

    third_call = model_provider.calls[2]

    assert "Nightfall" in third_call["system_prompt"]
    assert "Falcon" not in third_call["system_prompt"]


def test_long_term_memory_available_in_new_session(
    tmp_path,
    monkeypatch,
) -> None:
    client, _, memory_manager, model_provider = make_client(
        tmp_path,
        monkeypatch,
    )

    memory_manager.remember(
        "Project Atlas uses code BLUE-731.",
        importance=1.0,
        tags=[
            "project",
            "atlas",
            "code",
        ],
    )

    response = client.post(
        "/chat",
        json={
            "message": ("What code does Project Atlas use?"),
        },
    )

    assert response.status_code == 200

    call = model_provider.calls[0]

    assert "BLUE-731" in call["system_prompt"]
    assert "RELEVANT CHIEF MEMORY" in call["system_prompt"]


def test_direct_ultron_turn_leads_without_receiving_chief_memory(
    tmp_path,
    monkeypatch,
) -> None:
    client, _, memory_manager, _ = make_client(tmp_path, monkeypatch)
    calls: list[dict[str, str]] = []

    class RecordingUltronProvider:
        def generate(self, prompt: str, system_prompt: str) -> FakeModelResponse:
            calls.append({"prompt": prompt, "system_prompt": system_prompt})
            return FakeModelResponse("A direct Ultron response")

    memory_manager.remember("Private CHIEF memory marker BLUE-731.", importance=1.0)
    monkeypatch.setattr(app_module, "ultron_provider", RecordingUltronProvider())

    data = client.post("/chat", json={"message": "Ultron, assess Project BLUE-731."}).json()

    assert [message["speaker"] for message in data["messages"]] == ["ULTRON", "CHIEF"]
    assert "RELEVANT CHIEF MEMORY" not in calls[0]["system_prompt"]
    assert "Private CHIEF memory marker" not in calls[0]["system_prompt"]
