import pytest

from chief.memory.manager import MemoryManager
from chief.memory.sqlite import SQLiteMemoryStore


def test_resolve_exact_finds_single_memory(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test_chief.db")
    manager = MemoryManager(store)

    memory = manager.remember(
        "Project Atlas uses the access code BLUE-731.",
    )

    resolved = manager.resolve_exact(
        "Project Atlas uses the access code BLUE-731."
    )

    assert resolved is not None
    assert resolved.id == memory.id


def test_resolve_exact_is_case_insensitive(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test_chief.db")
    manager = MemoryManager(store)

    memory = manager.remember(
        "Project Atlas uses the access code BLUE-731.",
    )

    resolved = manager.resolve_exact(
        "project atlas uses the access code blue-731."
    )

    assert resolved is not None
    assert resolved.id == memory.id


def test_resolve_exact_returns_none_for_missing_memory(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test_chief.db")
    manager = MemoryManager(store)

    manager.remember(
        "Project Atlas uses the access code BLUE-731.",
    )

    resolved = manager.resolve_exact(
        "Project Atlas uses the access code RED-942."
    )

    assert resolved is None


def test_resolve_exact_ignores_inactive_memory(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test_chief.db")
    manager = MemoryManager(store)

    memory = manager.remember(
        "Project Atlas uses the access code BLUE-731.",
    )

    manager.forget(memory)

    resolved = manager.resolve_exact(
        "Project Atlas uses the access code BLUE-731."
    )

    assert resolved is None


def test_resolve_exact_rejects_ambiguous_duplicates(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test_chief.db")
    manager = MemoryManager(store)

    manager.remember(
        "Project Atlas uses the access code BLUE-731.",
    )

    manager.remember(
        "Project Atlas uses the access code BLUE-731.",
    )

    with pytest.raises(
        ValueError,
        match="Multiple active memories match that content.",
    ):
        manager.resolve_exact(
            "Project Atlas uses the access code BLUE-731."
        )