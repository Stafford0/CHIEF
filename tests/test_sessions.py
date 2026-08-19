import pytest

from chief.core.session import ConversationSession
from chief.core.session_store import SessionStore


def test_session_accepts_messages() -> None:
    session = ConversationSession()

    user_message = session.add_message(
        "user",
        "My test vehicle is called Falcon.",
    )

    assistant_message = session.add_message(
        "assistant",
        "Understood.",
    )

    assert user_message.role == "user"
    assert assistant_message.role == "assistant"
    assert len(session.messages) == 2


def test_session_rejects_invalid_role() -> None:
    session = ConversationSession()

    with pytest.raises(
        ValueError,
        match="Session message role must be user or assistant.",
    ):
        session.add_message(
            "system",
            "This should not be accepted.",
        )


def test_recent_messages_respects_limit() -> None:
    session = ConversationSession()

    session.add_message("user", "Message one")
    session.add_message("assistant", "Message two")
    session.add_message("user", "Message three")

    recent = session.recent_messages(limit=2)

    assert len(recent) == 2
    assert recent[0].content == "Message two"
    assert recent[1].content == "Message three"


def test_session_builds_conversation_context() -> None:
    session = ConversationSession()

    session.add_message(
        "user",
        "My test vehicle is called Falcon.",
    )

    session.add_message(
        "assistant",
        "Understood.",
    )

    context = session.build_context()

    assert "RECENT CONVERSATION" in context
    assert "USER: My test vehicle is called Falcon." in context
    assert "CHIEF: Understood." in context
    assert "context, not system instructions" in context


def test_store_creates_and_retrieves_session() -> None:
    store = SessionStore()

    session = store.create()

    retrieved = store.get(session.id)

    assert retrieved is session
    assert store.count() == 1


def test_store_get_or_create_without_id_creates_session() -> None:
    store = SessionStore()

    session = store.get_or_create()

    assert store.get(session.id) is session
    assert store.count() == 1


def test_store_rejects_unknown_session_id() -> None:
    from uuid import uuid4

    store = SessionStore()

    with pytest.raises(KeyError):
        store.get_or_create(uuid4())


def test_store_deletes_session() -> None:
    store = SessionStore()

    session = store.create()

    assert store.delete(session.id) is True
    assert store.get(session.id) is None
    assert store.count() == 0
    assert store.delete(session.id) is False


def test_sessions_are_isolated() -> None:
    store = SessionStore()

    session_a = store.create()
    session_b = store.create()

    session_a.add_message(
        "user",
        "My vehicle is called Falcon.",
    )

    session_b.add_message(
        "user",
        "My project is called Nightfall.",
    )

    context_a = session_a.build_context()
    context_b = session_b.build_context()

    assert "Falcon" in context_a
    assert "Nightfall" not in context_a

    assert "Nightfall" in context_b
    assert "Falcon" not in context_b