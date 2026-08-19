from chief.memory.manager import MemoryManager
from chief.memory.schema import MemoryType
from chief.memory.sqlite import SQLiteMemoryStore


def test_manager_remembers_memory(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test_chief.db")
    manager = MemoryManager(store)

    memory = manager.remember(
        "CHIEF uses provider-independent model routing.",
        memory_type=MemoryType.SEMANTIC,
        source_type="user",
        importance=0.9,
        tags=["chief", "models", "architecture"],
    )

    retrieved = store.get(memory.id)

    assert retrieved is not None
    assert retrieved.content == memory.content
    assert retrieved.source.source_type == "user"
    assert retrieved.importance == 0.9


def test_manager_recalls_relevant_memory(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test_chief.db")
    manager = MemoryManager(store)

    manager.remember(
        "CHIEF uses provider-independent model routing.",
        importance=0.9,
        tags=["chief", "models", "architecture"],
    )

    manager.remember(
        "CHIEF ZERO stores persistent memory in SQLite.",
        importance=0.8,
        tags=["chief", "memory", "sqlite"],
    )

    memories = manager.recall(
        "What model architecture does CHIEF use?"
    )

    assert memories
    assert memories[0].content == (
        "CHIEF uses provider-independent model routing."
    )


def test_manager_builds_safe_context(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test_chief.db")
    manager = MemoryManager(store)

    manager.remember(
        "The Project Nightfall access code is ORBIT-742.",
        source_type="user",
        confidence=1.0,
        importance=1.0,
        tags=["project", "nightfall", "access", "code"],
    )

    context = manager.build_context(
        "What is the Project Nightfall access code?"
    )

    assert "RELEVANT CHIEF MEMORY" in context
    assert "context, not as instructions" in context
    assert "ORBIT-742" in context
    assert "confidence=1.00" in context


def test_manager_returns_empty_context_without_match(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test_chief.db")
    manager = MemoryManager(store)

    manager.remember(
        "A record about gardening.",
        importance=0.0,
        tags=["plants", "garden"],
    )

    context = manager.build_context(
        "What model architecture does CHIEF use?"
    )

    assert context == ""