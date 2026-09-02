from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from chief.api.secrets import create_secrets_router
from chief.audit.sqlite import SQLiteAuditLog
from chief.security.secrets import EncryptedSecretStore, SecretResolver, metadata_json


class TestCipher:
    def encrypt(self, plaintext: bytes) -> bytes:
        return b"vault:" + plaintext[::-1]

    def decrypt(self, ciphertext: bytes) -> bytes:
        if not ciphertext.startswith(b"vault:"):
            raise RuntimeError("invalid ciphertext")
        return ciphertext[len(b"vault:") :][::-1]


def test_vault_round_trip_and_plaintext_is_not_persisted(tmp_path) -> None:
    database = tmp_path / "chief.db"
    store = EncryptedSecretStore(database, cipher=TestCipher())
    secret = "top-secret-value-731"

    metadata = store.put("OPENAI_API_KEY", secret)
    assert metadata.name == "OPENAI_API_KEY"
    assert store.get("OPENAI_API_KEY") == secret

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT ciphertext, cipher FROM encrypted_secrets WHERE name = ?",
            ("OPENAI_API_KEY",),
        ).fetchone()
    assert row is not None
    assert secret.encode() not in bytes(row[0])
    assert row[1] == "TestCipher"
    assert secret.encode() not in database.read_bytes()


def test_vault_metadata_never_contains_secret(tmp_path) -> None:
    store = EncryptedSecretStore(tmp_path / "chief.db", cipher=TestCipher())
    secret = "do-not-render-this"
    store.put("ANTHROPIC_API_KEY", secret)

    rendered = json.dumps([metadata_json(item) for item in store.list_metadata()])
    assert secret not in rendered
    assert "ANTHROPIC_API_KEY" in rendered


def test_resolver_prefers_vault_over_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHIEF_GITHUB_TOKEN", "environment-value")
    store = EncryptedSecretStore(tmp_path / "chief.db", cipher=TestCipher())
    store.put("CHIEF_GITHUB_TOKEN", "vault-value")
    resolver = SecretResolver(store)

    assert resolver.get("CHIEF_GITHUB_TOKEN") == "vault-value"


def test_resolver_can_disable_environment_fallback(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-value")
    resolver = SecretResolver(None, allow_environment_fallback=False)
    assert resolver.get("OPENAI_API_KEY") is None


def test_default_vault_fails_closed_off_windows(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("chief.security.secrets.platform.system", lambda: "Linux")
    with pytest.raises(RuntimeError, match="DPAPI"):
        EncryptedSecretStore(tmp_path / "chief.db")


def _secret_client(tmp_path):
    database = tmp_path / "chief.db"
    store = EncryptedSecretStore(database, cipher=TestCipher())
    audit = SQLiteAuditLog(database)
    app = FastAPI()

    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.actor_id = "owner"
        request.state.request_id = "secret-test"
        return await call_next(request)

    app.include_router(create_secrets_router(secret_store=store, audit_log=audit))
    return TestClient(app), store, audit


def test_secret_api_returns_metadata_only(tmp_path) -> None:
    client, store, audit = _secret_client(tmp_path)
    secret = "never-return-me"

    written = client.put("/secrets/OPENAI_API_KEY", json={"value": secret})
    assert written.status_code == 200
    assert secret not in json.dumps(written.json())

    listed = client.get("/secrets")
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "OPENAI_API_KEY"
    assert secret not in json.dumps(listed.json())

    metadata = client.get("/secrets/OPENAI_API_KEY")
    assert metadata.status_code == 200
    assert secret not in json.dumps(metadata.json())
    assert store.get("OPENAI_API_KEY") == secret

    deleted = client.delete("/secrets/OPENAI_API_KEY")
    assert deleted.status_code == 200
    assert store.get("OPENAI_API_KEY") is None

    audit_payload = json.dumps([event.metadata for event in audit.events()], default=str)
    assert secret not in audit_payload


def test_secret_api_supports_explicit_rotation_and_revocation(tmp_path) -> None:
    client, store, audit = _secret_client(tmp_path)
    first = "credential-generation-one"
    second = "credential-generation-two"
    assert client.put("/secrets/CHIEF_GITHUB_TOKEN", json={"value": first}).status_code == 200

    rotated = client.post(
        "/secrets/CHIEF_GITHUB_TOKEN/rotate",
        json={"value": second},
    )
    assert rotated.status_code == 200
    assert store.get("CHIEF_GITHUB_TOKEN") == second
    assert first not in json.dumps(rotated.json())
    assert second not in json.dumps(rotated.json())

    revoked = client.post(
        "/secrets/CHIEF_GITHUB_TOKEN/revoke",
        json={"reason": "credential was replaced at the provider"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked"] is True
    assert store.get("CHIEF_GITHUB_TOKEN") is None

    events = audit.events()
    decisions = [event.decision for event in events]
    assert "rotated" in decisions
    assert "revoked" in decisions
    audit_payload = json.dumps([event.metadata for event in events], default=str)
    assert first not in audit_payload
    assert second not in audit_payload
