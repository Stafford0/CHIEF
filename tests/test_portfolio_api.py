from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from chief.api import create_portfolio_router
from chief.portfolio import SQLitePortfolioStore


def _client(database_path: Path) -> tuple[TestClient, list[tuple[str, str, str]]]:
    application = FastAPI()
    changes: list[tuple[str, str, str]] = []
    store = SQLitePortfolioStore(database_path)

    @application.middleware("http")
    async def attach_actor(request: Request, call_next):
        request.state.actor_id = request.headers.get("x-test-actor", "owner-a")
        request.state.request_id = "portfolio-test"
        return await call_next(request)

    def record_change(
        _request: Request,
        domain: str,
        action: str,
        entity_id: str,
    ) -> None:
        changes.append((domain, action, entity_id))

    application.include_router(
        create_portfolio_router(
            portfolio_store=store,
            record_change=record_change,
        )
    )
    return TestClient(application), changes


def test_portfolio_api_starts_truthfully_blank(tmp_path) -> None:
    client, _ = _client(tmp_path / "chief.db")

    state = client.get("/portfolio")

    assert state.status_code == 200
    assert state.json()["summary"] == {
        "owner_id": "owner-a",
        "businesses": 0,
        "agents": 0,
        "systems": 0,
        "financial_accounts": 0,
        "active_agents": 0,
        "execution_enabled_agents": 0,
        "external_write_enabled_systems": 0,
        "healthy_agents": 0,
        "is_blank": True,
    }
    onboarding = state.json()["onboarding"]
    assert onboarding["is_blank"] is True
    assert onboarding["ready_for_autonomy"] is False
    assert onboarding["next_step"] == "register_first_business"
    assert client.get("/portfolio/businesses").json() == []
    assert client.get("/portfolio/agents").json() == []


def test_registration_cannot_smuggle_activation_or_authority(tmp_path) -> None:
    client, changes = _client(tmp_path / "chief.db")

    unsafe_business = client.post(
        "/portfolio/businesses",
        json={
            "key": "unsafe",
            "name": "Unsafe",
            "execution_enabled": True,
        },
    )
    assert unsafe_business.status_code == 422

    created_business = client.post(
        "/portfolio/businesses",
        json={"key": "first-business", "name": "First Business", "mission": "Learn safely."},
    )
    assert created_business.status_code == 201
    business = created_business.json()
    assert business["owner_id"] == "owner-a"
    assert business["status"] == "draft"
    assert business["monitoring_enabled"] is False
    assert business["execution_enabled"] is False
    assert business["kill_switch_engaged"] is True
    assert business["authority_ceiling"]["enabled"] is False
    assert business["authority_ceiling"]["external_writes_enabled"] is False
    assert business["budget"]["monthly_compute_limit"] == "0"
    assert business["budget"]["max_single_transaction"] == "0"

    unsafe_agent = client.post(
        "/portfolio/agents",
        json={
            "business_id": business["id"],
            "role": "business_governor",
            "scope": "business",
            "name": "Unsafe governor",
            "mission": "Run everything.",
            "authority": {"enabled": True, "external_writes_enabled": True},
        },
    )
    assert unsafe_agent.status_code == 422

    created_agent = client.post(
        "/portfolio/agents",
        json={
            "business_id": business["id"],
            "role": "business_governor",
            "scope": "business",
            "name": "Governor One",
            "mission": "Observe and escalate.",
        },
    )
    assert created_agent.status_code == 201
    agent = created_agent.json()
    assert agent["status"] == "draft"
    assert agent["execution_enabled"] is False
    assert agent["kill_switch_engaged"] is True
    assert agent["authority"]["enabled"] is False
    assert agent["authority"]["allowed_tools"] == []
    assert agent["authority"]["write_scopes"] == []
    assert agent["budget"]["monthly_operating_limit"] == "0"
    assert changes[0][:2] == ("portfolio_business", "registered")
    assert changes[1][:2] == ("portfolio_agent", "registered")
    assert "First Business" not in str(changes)


def test_heartbeat_reports_health_without_activating_agent(tmp_path) -> None:
    client, _ = _client(tmp_path / "chief.db")
    business = client.post(
        "/portfolio/businesses",
        json={"key": "heartbeat-business", "name": "Heartbeat Business"},
    ).json()
    agent = client.post(
        "/portfolio/agents",
        json={
            "business_id": business["id"],
            "role": "business_governor",
            "scope": "business",
            "name": "Observer Governor",
            "mission": "Report evidence only.",
        },
    ).json()

    response = client.post(
        f"/portfolio/agents/{agent['id']}/heartbeats",
        json={
            "health": "healthy",
            "summary": "Read-only checks completed.",
            "evidence_digest": "a" * 64,
            "metrics": {"source_coverage": 1.0},
        },
    )

    assert response.status_code == 201
    assert response.json()["business_id"] == business["id"]
    unchanged = client.get(f"/portfolio/agents/{agent['id']}").json()
    assert unchanged["status"] == "draft"
    assert unchanged["execution_enabled"] is False
    assert unchanged["kill_switch_engaged"] is True
    assert client.get("/portfolio/summary").json()["healthy_agents"] == 1


def test_systems_and_accounts_accept_references_but_no_access_grants(tmp_path) -> None:
    client, _ = _client(tmp_path / "chief.db")
    business = client.post(
        "/portfolio/businesses",
        json={"key": "reference-business", "name": "Reference Business"},
    ).json()

    system = client.post(
        "/portfolio/systems",
        json={
            "business_id": business["id"],
            "scope": "business",
            "kind": "saas",
            "name": "CRM",
            "credential_reference": {"uri": "vault://chief/crm"},
        },
    )
    assert system.status_code == 201
    assert system.json()["read_enabled"] is False
    assert system.json()["write_enabled"] is False

    raw_secret = client.post(
        "/portfolio/systems",
        json={
            "business_id": business["id"],
            "scope": "business",
            "kind": "saas",
            "name": "Unsafe CRM",
            "credential_reference": {"uri": "plain-api-token"},
        },
    )
    assert raw_secret.status_code == 422

    account = client.post(
        "/portfolio/financial-accounts",
        json={
            "business_id": business["id"],
            "scope": "business",
            "kind": "bank",
            "account_alias": "Operating account",
            "institution": "Example institution",
            "provider_account_id_digest": "b" * 64,
            "credential_reference": {"uri": "keyring://chief/operating-account"},
        },
    )
    assert account.status_code == 201
    assert account.json()["status"] == "draft"
    assert account.json()["credential_reference"]["uri"] == ("keyring://chief/operating-account")
    assert "balance" not in account.json()
    assert "transaction_writes_enabled" not in account.json()

    smuggled_write = client.post(
        "/portfolio/financial-accounts",
        json={
            "scope": "portfolio",
            "kind": "bank",
            "account_alias": "Unsafe account",
            "institution": "Example institution",
            "transaction_writes_enabled": True,
        },
    )
    assert smuggled_write.status_code == 422


def test_portfolio_api_derives_owner_and_hides_cross_owner_records(tmp_path) -> None:
    client, _ = _client(tmp_path / "chief.db")
    created = client.post(
        "/portfolio/businesses",
        headers={"x-test-actor": "owner-a"},
        json={"key": f"isolated-{uuid4().hex}", "name": "Owner A business"},
    ).json()

    assert (
        client.get(
            f"/portfolio/businesses/{created['id']}",
            headers={"x-test-actor": "owner-b"},
        ).status_code
        == 404
    )
    assert client.get("/portfolio/businesses", headers={"x-test-actor": "owner-b"}).json() == []
    assert (
        client.get("/portfolio/summary", headers={"x-test-actor": "owner-b"}).json()["is_blank"]
        is True
    )
