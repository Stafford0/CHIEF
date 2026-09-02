from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from chief.core import app as app_module
from chief.core.app import app
from chief.core.rate_limit import SlidingWindowRateLimiter
from chief.core.session import ConversationSession
from chief.memory.schema import MemoryRecord, MemorySource, MemoryType


def test_operational_headers_and_request_id():
    response = TestClient(app).get("/health", headers={"x-request-id": "audit-123"})
    assert response.headers["x-request-id"] == "audit-123"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_readiness_is_separate_from_liveness(monkeypatch):
    monkeypatch.setattr(
        app_module.model_provider,
        "available_models",
        lambda: {app_module.settings.ollama_model, app_module.settings.ultron_ollama_model},
    )
    assert TestClient(app).get("/ready").json()["status"] == "ready"


def test_readiness_reports_degraded_when_only_ultron_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        app_module.model_provider,
        "available_models",
        lambda: {app_module.settings.ollama_model},
    )

    response = TestClient(app).get("/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["agents"] == {"chief": "ready", "ultron": "unavailable"}


def test_configured_api_token_protects_non_health_routes(monkeypatch):
    token = "a" * 32
    monkeypatch.setattr(app_module, "settings", replace(app_module.settings, api_token=token))
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 401
    assert client.get("/system").status_code == 401
    assert client.get("/system", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_remote_client_is_denied_when_lan_mode_is_off(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, allow_private_lan_ui=False, api_token=None),
    )
    client = TestClient(app, client=("192.168.1.50", 50_000))

    assert client.get("/health").status_code == 200
    assert client.get("/dashboard").status_code == 403


def test_remote_authenticated_clients_are_rate_limited(monkeypatch):
    token = "b" * 32
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(
            app_module.settings,
            allow_private_lan_ui=True,
            api_token=token,
        ),
    )
    monkeypatch.setattr(
        app_module,
        "_remote_rate_limiter",
        SlidingWindowRateLimiter(1, clock=lambda: 100.0),
    )
    client = TestClient(app, client=("192.168.1.51", 50_001))
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/system", headers=headers).status_code == 200
    denied = client.get("/system", headers=headers)
    assert denied.status_code == 429
    assert denied.headers["retry-after"] == "60"


def test_tailscale_proxy_identity_is_remote_rate_limited(monkeypatch):
    token = "t" * 32
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(
            app_module.settings,
            allow_private_lan_ui=True,
            api_token=token,
            tailscale_allowed_logins=("stafford0@github",),
        ),
    )
    monkeypatch.setattr(
        app_module,
        "_remote_rate_limiter",
        SlidingWindowRateLimiter(1, clock=lambda: 100.0),
    )
    client = TestClient(app, client=("127.0.0.1", 50_003))
    headers = {
        "Authorization": f"Bearer {token}",
        "Tailscale-User-Login": "Stafford0@GitHub",
    }

    assert client.get("/system", headers=headers).status_code == 200
    denied = client.get("/system", headers=headers)
    assert denied.status_code == 429
    assert denied.headers["retry-after"] == "60"


def test_unenrolled_tailscale_identity_is_denied(monkeypatch):
    token = "t" * 32
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(
            app_module.settings,
            allow_private_lan_ui=True,
            api_token=token,
            tailscale_allowed_logins=("stafford0@github",),
        ),
    )
    client = TestClient(app, client=("127.0.0.1", 50_004))

    response = client.get(
        "/system",
        headers={
            "Authorization": f"Bearer {token}",
            "Tailscale-User-Login": "intruder@example.com",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "This Tailscale identity is not enrolled for CHIEF."


def test_tailscale_cgnat_client_is_permitted_in_protected_lan_mode(monkeypatch):
    token = "t" * 32
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(
            app_module.settings,
            allow_private_lan_ui=True,
            api_token=token,
            tailscale_allowed_logins=("stafford0@github",),
        ),
    )
    client = TestClient(app, client=("100.67.211.82", 50_005))

    response = client.get(
        "/system",
        headers={
            "Authorization": f"Bearer {token}",
            "Tailscale-User-Login": "stafford0@github",
        },
    )

    assert response.status_code == 200


def test_unidentified_cgnat_client_is_denied(monkeypatch):
    token = "t" * 32
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, allow_private_lan_ui=True, api_token=token),
    )
    client = TestClient(app, client=("100.67.211.83", 50_006))

    response = client.get("/system", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Only private-network clients are permitted in LAN mode."


def test_lan_mode_still_denies_public_network_clients(monkeypatch):
    token = "c" * 32
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, allow_private_lan_ui=True, api_token=token),
    )
    client = TestClient(app, client=("8.8.8.8", 50_002))

    response = client.get("/system", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert "private-network" in response.json()["detail"]


def test_private_lan_origin_validation_covers_full_rfc1918_ranges(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, allow_private_lan_ui=True),
    )
    assert app_module._origin_allowed("http://10.42.0.1:5173") is True
    assert app_module._origin_allowed("http://172.31.255.255:5173") is True
    assert app_module._origin_allowed("http://192.168.1.9:5173") is True
    assert app_module._origin_allowed("http://192.168.999.9:5173") is False


def test_readiness_fails_closed_when_a_store_check_raises(monkeypatch):
    def unavailable():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(app_module.memory_store, "health", unavailable)
    monkeypatch.setattr(
        app_module.model_provider,
        "available_models",
        lambda: {app_module.settings.ollama_model, app_module.settings.ultron_ollama_model},
    )
    response = TestClient(app).get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["memory"] is False


def test_execution_kill_switch_blocks_actions(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, execution_enabled=False),
    )
    response = TestClient(app).post(
        "/tools/execute",
        json={"name": "system_status", "arguments": {}},
    )

    assert response.status_code == 503
    assert "kill switch" in response.json()["detail"]


def test_audit_integrity_and_pagination_endpoints():
    client = TestClient(app)
    assert client.get("/audit/integrity").json()["valid"] is True
    assert client.get("/audit/events", params={"limit": 0}).status_code == 422


def test_oversized_request_bodies_are_rejected_before_parsing():
    response = TestClient(app).post(
        "/chat",
        content=b"x" * (app_module.settings.max_request_bytes + 1),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert "configured CHIEF limit" in response.json()["detail"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_session_rejects_empty_and_large_messages():
    session = ConversationSession(max_message_chars=3)
    with pytest.raises(ValueError):
        session.add_message("user", "   ")
    with pytest.raises(ValueError):
        session.add_message("user", "four")


def test_session_bounds_history():
    session = ConversationSession(max_messages=2)
    for value in ("one", "two", "three"):
        session.add_message("user", value)
    assert [message.content for message in session.messages] == ["two", "three"]


def test_memory_normalizes_content_and_tags():
    memory = MemoryRecord(
        memory_type=MemoryType.SEMANTIC,
        content=" fact ",
        source=MemorySource(source_type="user"),
        tags=[" Work ", "work"],
    )
    assert memory.content == "fact"
    assert memory.tags == ["work"]


def test_memory_rejects_oversized_content():
    with pytest.raises(ValidationError):
        MemoryRecord(
            memory_type=MemoryType.SEMANTIC,
            content="x" * 20_001,
            source=MemorySource(source_type="user"),
        )
