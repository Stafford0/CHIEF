from chief.memory.schema import MemoryRecord, MemorySource, MemoryType
from chief.memory.sqlite import SQLiteMemoryStore


def make_memory() -> MemoryRecord:
    return MemoryRecord(
        memory_type=MemoryType.SEMANTIC,
        content="CHIEF uses provider-independent model routing.",
        source=MemorySource(
            source_type="user",
            description="Direct user statement",
        ),
        confidence=1.0,
        importance=0.8,
        tags=["chief", "architecture", "models"],
    )


def test_memory_save_and_retrieve(tmp_path) -> None:
    database = tmp_path / "test_chief.db"
    store = SQLiteMemoryStore(database)

    memory = make_memory()
    store.save(memory)

    retrieved = store.get(memory.id)

    assert retrieved is not None
    assert retrieved.id == memory.id
    assert retrieved.content == memory.content
    assert retrieved.memory_type == MemoryType.SEMANTIC
    assert retrieved.source.source_type == "user"


def test_memory_persists_across_store_instances(tmp_path) -> None:
    database = tmp_path / "test_chief.db"

    first_store = SQLiteMemoryStore(database)
    memory = make_memory()
    first_store.save(memory)

    second_store = SQLiteMemoryStore(database)
    retrieved = second_store.get(memory.id)

    assert retrieved is not None
    assert retrieved.content == memory.content


def test_list_active_memories(tmp_path) -> None:
    database = tmp_path / "test_chief.db"
    store = SQLiteMemoryStore(database)

    memory = make_memory()
    store.save(memory)

    active_memories = store.list_active()

    assert len(active_memories) == 1
    assert active_memories[0].id == memory.id


def test_deactivate_memory(tmp_path) -> None:
    database = tmp_path / "test_chief.db"
    store = SQLiteMemoryStore(database)

    memory = make_memory()
    store.save(memory)

    assert store.deactivate(memory.id) is True
    assert store.list_active() == []

    retrieved = store.get(memory.id)

    assert retrieved is not None
    assert retrieved.active is False


def test_delete_memory(tmp_path) -> None:
    database = tmp_path / "test_chief.db"
    store = SQLiteMemoryStore(database)

    memory = make_memory()
    store.save(memory)

    assert store.delete(memory.id) is True
    assert store.get(memory.id) is None