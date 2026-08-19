from chief.memory.manager import MemoryManager
from chief.memory.sqlite import SQLiteMemoryStore


def test_correct_supersedes_old_memory(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test_chief.db")
    manager = MemoryManager(store)

    old_memory = manager.remember(
        "Project Atlas uses the access code BLUE-731.",
        importance=0.9,
        tags=["project", "atlas", "access", "code"],
    )

    new_memory = manager.correct(
        old_memory,
        "Project Atlas uses the access code RED-942.",
        source_description="User correction",
    )

    stored_old = store.get(old_memory.id)
    stored_new = store.get(new_memory.id)

    assert stored_old is not None
    assert stored_new is not None

    assert stored_old.active is False
    assert stored_new.active is True

    assert stored_new.supersedes == old_memory.id
    assert stored_new.content == (
        "Project Atlas uses the access code RED-942."
    )


def test_recall_uses_corrected_memory(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test_chief.db")
    manager = MemoryManager(store)

    old_memory = manager.remember(
        "Project Atlas uses the access code BLUE-731.",
        importance=1.0,
        tags=["project", "atlas", "access", "code"],
    )

    manager.correct(
        old_memory,
        "Project Atlas uses the access code RED-942.",
    )

    memories = manager.recall(
        "What is the access code for Project Atlas?"
    )

    assert memories
    assert any(
        "RED-942" in memory.content
        for memory in memories
    )

    assert all(
        "BLUE-731" not in memory.content
        for memory in memories
    )


def test_forget_removes_memory_from_recall(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test_chief.db")
    manager = MemoryManager(store)

    memory = manager.remember(
        "Project Atlas uses the access code BLUE-731.",
        importance=1.0,
        tags=["project", "atlas", "access", "code"],
    )

    result = manager.forget(memory)

    assert result is True

    memories = manager.recall(
        "What is the access code for Project Atlas?"
    )

    assert memories == []


def test_forget_preserves_inactive_history(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test_chief.db")
    manager = MemoryManager(store)

    memory = manager.remember(
        "Project Atlas uses the access code BLUE-731.",
    )

    manager.forget(memory)

    stored = store.get(memory.id)

    assert stored is not None
    assert stored.active is False
    assert stored.content == memory.content


def test_cannot_correct_inactive_memory(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test_chief.db")
    manager = MemoryManager(store)

    memory = manager.remember(
        "Project Atlas uses the access code BLUE-731.",
    )

    manager.forget(memory)

    try:
        manager.correct(
            memory,
            "Project Atlas uses the access code RED-942.",
        )
    except ValueError as exc:
        assert str(exc) == "Cannot correct an inactive memory."
    else:
        raise AssertionError(
            "Expected correction of inactive memory to fail."
        )