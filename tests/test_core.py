from fastapi.testclient import TestClient

from chief.core.app import app


client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "online",
        "system": "CHIEF",
        "version": "0.0.1",
    }


def test_system_info() -> None:
    response = client.get("/system")

    assert response.status_code == 200
    assert response.json()["name"] == "CHIEF"
    assert response.json()["version"] == "0.0.1"
    assert response.json()["milestone"] == "CHIEF ZERO"