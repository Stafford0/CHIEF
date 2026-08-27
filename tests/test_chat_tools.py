from uuid import UUID

from fastapi.testclient import TestClient

from chief.core import app as app_module
from chief.core.session_store import SessionStore
from chief.memory.manager import MemoryManager
from chief.memory.sqlite import SQLiteMemoryStore
from chief.tools.base import Tool, ToolDefinition, ToolResult, ToolRisk
from chief.tools.registry import ToolRegistry


class FakeModelResponse:
    content = "ordinary response"
    provider = "fake"
    model = "fake-model"


class FakeModelProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def generate(self, prompt: str, system_prompt: str) -> FakeModelResponse:
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt})
        return FakeModelResponse()


class SilentUltronProvider:
    def generate(self, prompt: str, system_prompt: str) -> FakeModelResponse:
        response = FakeModelResponse()
        response.content = "[[SILENT]]"
        return response


class UnavailableModelProvider:
    def generate(self, prompt: str, system_prompt: str) -> FakeModelResponse:
        raise RuntimeError("CHIEF could not connect to Ollama.")


class RecordingTool(Tool):
    def __init__(self, name: str, risk: ToolRisk, content: str) -> None:
        self._definition = ToolDefinition(
            name=name,
            description=name,
            risk=risk,
            requires_approval=risk == ToolRisk.SENSITIVE,
        )
        self.content = content
        self.calls: list[dict] = []

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def execute(self, arguments: dict) -> ToolResult:
        self.calls.append(arguments)
        return ToolResult(success=True, content=self.content, data={})


def make_client(tmp_path, monkeypatch):
    store = SQLiteMemoryStore(tmp_path / "chat-tools.db")
    sessions = SessionStore()
    provider = FakeModelProvider()
    registry = ToolRegistry()
    safe_tool = RecordingTool("powershell_read", ToolRisk.SAFE, "safe output")
    sensitive_tool = RecordingTool("powershell_command", ToolRisk.SENSITIVE, "17 passed")
    registry.register(safe_tool)
    registry.register(sensitive_tool)
    monkeypatch.setattr(app_module, "memory_store", store)
    monkeypatch.setattr(app_module, "memory_manager", MemoryManager(store))
    monkeypatch.setattr(app_module, "session_store", sessions)
    monkeypatch.setattr(app_module, "model_provider", provider)
    monkeypatch.setattr(app_module, "ultron_provider", SilentUltronProvider())
    monkeypatch.setattr(app_module, "tool_registry", registry)
    return TestClient(app_module.app), sessions, provider, safe_tool, sensitive_tool


def test_safe_intent_executes_automatically(tmp_path, monkeypatch) -> None:
    client, _, provider, safe_tool, _ = make_client(tmp_path, monkeypatch)

    response = client.post("/chat", json={"message": "Chief, show processes."})

    assert response.json()["response"] == "safe output"
    assert response.json()["provider"] == "chief-tools"
    assert response.json()["status"] == "completed"
    assert response.json()["pending_action"] is None
    assert safe_tool.calls == [{"command": "Get-Process"}]
    assert provider.calls == []


def test_sensitive_intent_returns_pending_approval(tmp_path, monkeypatch) -> None:
    client, sessions, _, _, sensitive_tool = make_client(tmp_path, monkeypatch)

    response = client.post("/chat", json={"message": "run the tests"})
    data = response.json()

    assert "Approval required" in data["response"]
    assert data["status"] == "pending_approval"
    assert data["pending_action"] == "approve_or_cancel"
    assert data["tool_description"] == "run the test suite with pytest -q"
    assert sensitive_tool.calls == []
    assert sessions.get_or_create(UUID(data["session_id"])).pending_tool_call is not None


def test_approval_follow_up_executes_pending_call(tmp_path, monkeypatch) -> None:
    client, sessions, _, _, sensitive_tool = make_client(tmp_path, monkeypatch)
    first = client.post("/chat", json={"message": "run the tests"}).json()

    response = client.post("/chat", json={"message": "approve", "session_id": first["session_id"]})

    assert response.json()["response"] == "17 passed"
    assert response.json()["status"] == "completed"
    assert response.json()["pending_action"] is None
    assert sensitive_tool.calls == [{"command": "pytest", "args": ["-q"]}]
    assert sessions.get_or_create(UUID(first["session_id"])).pending_tool_call is None


def test_rejection_cancels_without_execution(tmp_path, monkeypatch) -> None:
    client, sessions, _, _, sensitive_tool = make_client(tmp_path, monkeypatch)
    first = client.post("/chat", json={"message": "run tests"}).json()

    response = client.post("/chat", json={"message": "cancel", "session_id": first["session_id"]})

    assert response.json()["response"].startswith("Cancelled")
    assert response.json()["status"] == "cancelled"
    assert sensitive_tool.calls == []
    assert sessions.get_or_create(UUID(first["session_id"])).pending_tool_call is None


def test_ordinary_chat_falls_back_to_model(tmp_path, monkeypatch) -> None:
    client, _, provider, safe_tool, sensitive_tool = make_client(tmp_path, monkeypatch)

    response = client.post(
        "/chat", json={"message": "Write a PowerShell command that lists processes"}
    )

    assert response.json()["response"] == "ordinary response"
    assert len(provider.calls) == 1
    assert safe_tool.calls == []
    assert sensitive_tool.calls == []


def test_chat_returns_separately_attributed_chief_and_ultron_messages(
    tmp_path, monkeypatch
) -> None:
    client, _, _, _, _ = make_client(tmp_path, monkeypatch)

    class SpeakingUltronProvider:
        def generate(self, prompt: str, system_prompt: str) -> FakeModelResponse:
            response = FakeModelResponse()
            response.content = "The contradiction is the interesting part."
            response.model = "ultron-test"
            return response

    monkeypatch.setattr(app_module, "ultron_provider", SpeakingUltronProvider())
    data = client.post("/chat", json={"message": "Assess this plan."}).json()

    assert [message["speaker"] for message in data["messages"]] == ["CHIEF", "ULTRON"]
    assert data["messages"][0]["content"] == "ordinary response"
    assert data["messages"][1]["model"] == "ultron-test"


def test_ultron_evaluates_deterministic_tool_exchanges(tmp_path, monkeypatch) -> None:
    client, _, _, _, _ = make_client(tmp_path, monkeypatch)

    class SpeakingUltronProvider:
        def generate(self, prompt: str, system_prompt: str) -> FakeModelResponse:
            response = FakeModelResponse()
            response.content = "A result without interpretation is merely debris."
            return response

    monkeypatch.setattr(app_module, "ultron_provider", SpeakingUltronProvider())
    data = client.post("/chat", json={"message": "Chief, show processes."}).json()

    assert [message["speaker"] for message in data["messages"]] == ["CHIEF", "ULTRON"]
    assert data["messages"][0]["provider"] == "chief-tools"


def test_approval_does_not_execute_without_pending_call(tmp_path, monkeypatch) -> None:
    client, _, provider, _, sensitive_tool = make_client(tmp_path, monkeypatch)

    response = client.post("/chat", json={"message": "approve"})

    assert response.json()["response"] == "ordinary response"
    assert len(provider.calls) == 1
    assert sensitive_tool.calls == []


def test_root_serves_chat_interface(tmp_path, monkeypatch) -> None:
    client, _, _, _, _ = make_client(tmp_path, monkeypatch)

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "CHIEF is online" in response.text
    assert "approve_or_cancel" in response.text
    assert "contentType.includes('application/json')" in response.text


def test_model_unavailable_returns_structured_chat_response(tmp_path, monkeypatch) -> None:
    client, _, _, _, _ = make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module, "model_provider", UnavailableModelProvider())

    response = client.post("/chat", json={"message": "How is the weather?"})

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["provider"] == "chief-system"
    assert response.json()["model"] == "unavailable"
    assert response.json()["response"] == "CHIEF could not connect to Ollama."
