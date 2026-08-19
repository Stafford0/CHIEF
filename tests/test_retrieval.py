from chief.memory.retrieval import MemoryRetriever
from chief.memory.schema import MemoryRecord, MemorySource, MemoryType
from chief.memory.sqlite import SQLiteMemoryStore


def add_memory(
    store: SQLiteMemoryStore,
    content: str,
    tags: list[str],
    importance: float = 0.5,
) -> MemoryRecord:
    memory = MemoryRecord(
        memory_type=MemoryType.SEMANTIC,
        content=content,
        source=MemorySource(source_type="test"),
        confidence=1.0,
        importance=importance,
        tags=tags,
    )

    store.save(memory)
    return memory


def test_retrieval_finds_relevant_memory(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test_chief.db")

    model_memory = add_memory(
        store,
        "CHIEF uses provider-independent model routing.",
        ["chief", "architecture", "models"],
        importance=0.9,
    )

    add_memory(
        store,
        "CHIEF persistent memory uses SQLite during CHIEF ZERO.",
        ["chief", "memory", "sqlite"],
        importance=0.8,
    )

    add_memory(
        store,
        "The CHIEF API exposes a health endpoint.",
        ["chief", "api", "health"],
        importance=0.4,
    )

    retriever = MemoryRetriever(store)

    results = retriever.retrieve(
        "What model architecture does CHIEF use?"
    )

    assert results
    assert results[0].memory.id == model_memory.id


def test_retrieval_respects_limit(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test_chief.db")

    for number in range(10):
        add_memory(
            store,
            f"CHIEF memory architecture record {number}.",
            ["chief", "memory", "architecture"],
        )

    retriever = MemoryRetriever(store)

    results = retriever.retrieve(
        "CHIEF memory architecture",
        limit=3,
    )

    assert len(results) == 3


def test_retrieval_ignores_irrelevant_memory(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test_chief.db")

    add_memory(
        store,
        "A completely unrelated record about gardening.",
        ["plants", "garden"],
        importance=0.0,
    )

    retriever = MemoryRetriever(store)

    results = retriever.retrieve(
        "What model architecture does CHIEF use?"
    )

    assert results == []


def test_retrieval_ignores_inactive_memory(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test_chief.db")

    memory = add_memory(
        store,
        "CHIEF uses provider-independent model routing.",
        ["chief", "models"],
        importance=1.0,
    )

    store.deactivate(memory.id)

    retriever = MemoryRetriever(store)

    results = retriever.retrieve(
        "What model architecture does CHIEF use?"
    )

    assert results == []