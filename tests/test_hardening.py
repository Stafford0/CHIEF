import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from chief.core.app import app
from chief.core.session import ConversationSession
from chief.memory.schema import MemoryRecord, MemorySource, MemoryType


def test_operational_headers_and_request_id():
    response = TestClient(app).get("/health", headers={"x-request-id": "audit-123"})
    assert response.headers["x-request-id"] == "audit-123"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_readiness_is_separate_from_liveness():
    assert TestClient(app).get("/ready").json()["status"] == "ready"


def test_session_rejects_empty_and_large_messages():
    session = ConversationSession(max_message_chars=3)
    with pytest.raises(ValueError):
        session.add_message("user", "   ")
    with pytest.raises(ValueError):
        session.add_message("user", "four")


def test_session_bounds_history():
    session = ConversationSession(max_messages=2)
    for value in ("one", "two", "three"):
        session.add_message("user", value)
    assert [message.content for message in session.messages] == ["two", "three"]


def test_memory_normalizes_content_and_tags():
    memory = MemoryRecord(
        memory_type=MemoryType.SEMANTIC,
        content=" fact ",
        source=MemorySource(source_type="user"),
        tags=[" Work ", "work"],
    )
    assert memory.content == "fact"
    assert memory.tags == ["work"]


def test_memory_rejects_oversized_content():
    with pytest.raises(ValidationError):
        MemoryRecord(
            memory_type=MemoryType.SEMANTIC,
            content="x" * 20_001,
            source=MemorySource(source_type="user"),
        )
