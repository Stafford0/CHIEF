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


def test_tools_endpoint_exposes_guarded_registry() -> None:
    response = client.get("/tools")

    assert response.status_code == 200
    tools = {tool["name"]: tool for tool in response.json()}

    assert "powershell_read" in tools
    assert "powershell_command" in tools
    assert tools["powershell_read"]["risk"] == "safe"
    assert tools["powershell_read"]["requires_approval"] is False
    assert tools["powershell_command"]["risk"] == "sensitive"
    assert tools["powershell_command"]["requires_approval"] is True


def test_tool_execute_preserves_approval_gate() -> None:
    response = client.post(
        "/tools/execute",
        json={
            "name": "powershell_command",
            "arguments": {
                "command": "pytest",
                "args": ["-q"],
            },
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["success"] is False
    assert result["content"] == "Tool execution requires approval."
    assert "explicit approval" in result["error"]


def test_tool_execute_refuses_unknown_tool() -> None:
    response = client.post(
        "/tools/execute",
        json={
            "name": "not_a_real_tool",
            "arguments": {},
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["success"] is False
    assert result["content"] == "Tool execution refused."
    assert "not registered" in result["error"]
