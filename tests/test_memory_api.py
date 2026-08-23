from fastapi.testclient import TestClient

from chief.core import app as app_module
from chief.memory.manager import MemoryManager
from chief.memory.sqlite import SQLiteMemoryStore


def make_client(tmp_path, monkeypatch):
    store = SQLiteMemoryStore(tmp_path / "test_chief.db")
    manager = MemoryManager(store)

    monkeypatch.setattr(
        app_module,
        "memory_store",
        store,
    )

    monkeypatch.setattr(
        app_module,
        "memory_manager",
        manager,
    )

    return TestClient(app_module.app), store


def test_chat_remember_command(tmp_path, monkeypatch) -> None:
    client, store = make_client(tmp_path, monkeypatch)

    response = client.post(
        "/chat",
        json={"message": ("Chief, remember that Project Atlas uses BLUE-731.")},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["provider"] == "chief-memory"
    assert data["model"] == "deterministic"
    assert "Memory saved" in data["response"]

    memories = store.list_active()

    assert len(memories) == 1
    assert memories[0].content == ("Project Atlas uses BLUE-731.")


def test_chat_correct_command(tmp_path, monkeypatch) -> None:
    client, store = make_client(tmp_path, monkeypatch)

    client.post(
        "/chat",
        json={"message": ("Chief, remember that Project Atlas uses BLUE-731.")},
    )

    response = client.post(
        "/chat",
        json={
            "message": (
                'Chief, correct "Project Atlas uses BLUE-731." to "Project Atlas uses RED-942."'
            )
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["provider"] == "chief-memory"
    assert "Memory corrected" in data["response"]
    assert "RED-942" in data["response"]

    active = store.list_active()

    assert len(active) == 1
    assert active[0].content == ("Project Atlas uses RED-942.")
    assert active[0].supersedes is not None


def test_chat_forget_command(tmp_path, monkeypatch) -> None:
    client, store = make_client(tmp_path, monkeypatch)

    client.post(
        "/chat",
        json={"message": ("Chief, remember that Project Atlas uses BLUE-731.")},
    )

    response = client.post(
        "/chat",
        json={"message": ('Chief, forget "Project Atlas uses BLUE-731."')},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["provider"] == "chief-memory"
    assert "Memory forgotten" in data["response"]

    assert store.list_active() == []


def test_chat_refuses_missing_correction(
    tmp_path,
    monkeypatch,
) -> None:
    client, store = make_client(tmp_path, monkeypatch)

    response = client.post(
        "/chat",
        json={"message": ('Chief, correct "This memory does not exist." to "Something else."')},
    )

    assert response.status_code == 200
    assert "did not change anything" in response.json()["response"]
    assert store.list_active() == []


def test_chat_refuses_missing_forget(
    tmp_path,
    monkeypatch,
) -> None:
    client, store = make_client(tmp_path, monkeypatch)

    response = client.post(
        "/chat",
        json={"message": ('Chief, forget "This memory does not exist."')},
    )

    assert response.status_code == 200
    assert "did not forget anything" in response.json()["response"]
    assert store.list_active() == []
