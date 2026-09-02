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


def test_dashboard_embeds_the_owner_scoped_portfolio_contract() -> None:
    dashboard = client.get("/dashboard")
    portfolio = client.get("/portfolio")

    assert dashboard.status_code == 200
    assert portfolio.status_code == 200
    runtime = dashboard.json()["runtime"]
    assert runtime["portfolio_summary"] == portfolio.json()["summary"]
    assert runtime["portfolio_onboarding"] == portfolio.json()["onboarding"]


def test_dashboard_degrades_when_host_telemetry_fails(monkeypatch) -> None:
    from chief.core import dashboard as dashboard_module

    def unavailable(_project_root):
        raise PermissionError("service identity cannot read a host metric")

    monkeypatch.setattr(dashboard_module, "collect_dashboard_snapshot", unavailable)

    response = client.get("/dashboard")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime"]["api_status"] == "online"
    assert payload["runtime"]["degraded_components"] == ["host_telemetry"]
    assert payload["host"]["hostname"] == "unavailable"


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
    assert tools["powershell_command"]["side_effects"] is True
    assert tools["powershell_command"]["idempotent"] is False
    assert tools["powershell_read"]["input_schema"]["additionalProperties"] is False


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
    assert "approval" in result["error"].lower()


def test_tool_execute_rejects_client_supplied_approval() -> None:
    response = client.post(
        "/tools/execute",
        json={
            "name": "powershell_command",
            "arguments": {"command": "pytest", "args": ["-q"]},
            "approved": True,
        },
    )

    assert response.status_code == 422


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


def test_schema_driven_plan_executes_bounded_safe_step() -> None:
    plan = {
        "objective": "Check CHIEF runtime",
        "steps": [
            {
                "id": "status",
                "tool_name": "system_status",
                "arguments": {},
                "rationale": "Collect current local runtime evidence.",
                "expected_outcome": "Runtime status is returned.",
            }
        ],
    }

    assert client.post("/plans/validate", json=plan).json()["valid"] is True
    executed = client.post("/plans/execute", json=plan)
    assert executed.status_code == 200
    assert executed.json()["status"] == "succeeded"


def test_schema_driven_plan_never_auto_approves_sensitive_step() -> None:
    response = client.post(
        "/plans/execute",
        json={
            "objective": "Run tests",
            "steps": [
                {
                    "id": "tests",
                    "tool_name": "powershell_command",
                    "arguments": {"command": "pytest", "args": ["-q"]},
                    "rationale": "Verify the repository.",
                    "expected_outcome": "Test results are returned.",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "awaiting_approval"
