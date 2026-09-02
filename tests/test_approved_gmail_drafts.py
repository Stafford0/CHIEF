from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser

import pytest

from chief.integrations import (
    ConsentGrant,
    ConnectorRegistry,
    IdempotencyMetadata,
)
from chief.integrations.gmail_drafts import GmailDraftConnector
from chief.tools.connector_write import ConnectorWriteTool
from chief.tools.registry import ToolRegistry

NOW = datetime(2026, 9, 2, 2, 30, tzinfo=UTC)


class GmailDraftTransport:
    def __init__(self) -> None:
        self.posts = 0
        self.last_raw: str | None = None
        self.existing: dict[str, tuple[str, str]] = {}

    def __call__(self, method, url, headers, body):
        assert headers["Authorization"] == "Bearer token"
        if url.endswith("/users/me/profile"):
            return 200, {"emailAddress": "owner@example.com"}, {}, 1.0
        if "/users/me/drafts?" in url:
            marker = url.split("rfc822msgid%3A", 1)[1] if "rfc822msgid%3A" in url else ""
            for message_id, (draft_id, message_external_id) in self.existing.items():
                encoded = message_id.replace("<", "%3C").replace(">", "%3E").replace("@", "%40")
                if marker == encoded:
                    return (
                        200,
                        {"drafts": [{"id": draft_id, "message": {"id": message_external_id}}]},
                        {},
                        1.0,
                    )
            return 200, {"drafts": []}, {}, 1.0
        if method == "POST" and url.endswith("/users/me/drafts"):
            self.posts += 1
            payload = json.loads((body or b"").decode("utf-8"))
            self.last_raw = str(payload["message"]["raw"])
            decoded = base64.urlsafe_b64decode(self.last_raw.encode("ascii"))
            message = BytesParser(policy=policy.default).parsebytes(decoded)
            message_id = str(message["Message-ID"])
            draft_id = f"draft-{self.posts}"
            message_external_id = f"message-{self.posts}"
            self.existing[message_id] = (draft_id, message_external_id)
            return (
                200,
                {"id": draft_id, "message": {"id": message_external_id}},
                {},
                1.0,
            )
        raise AssertionError(f"Unexpected transport call: {method} {url}")


def _registry_with_connector(transport: GmailDraftTransport) -> ConnectorRegistry:
    registry = ConnectorRegistry(clock=lambda: NOW)
    registry.register(
        GmailDraftConnector(
            token_provider=lambda: "token",
            transport=transport,
            clock=lambda: NOW,
        )
    )
    return registry


def _tool_arguments() -> dict[str, object]:
    return {
        "connector_id": "gmail_drafts",
        "scope": "drafts.create",
        "payload": {
            "to": "recipient@example.com",
            "subject": "Draft subject",
            "body": "Draft body",
        },
        "idempotency_key": "draft-approval-1",
    }


def test_gmail_draft_connector_manifest_exposes_create_only() -> None:
    connector = GmailDraftConnector(token_provider=lambda: "token")

    assert connector.manifest.connector_id == "gmail_drafts"
    assert [scope.name for scope in connector.manifest.scopes] == ["drafts.create"]
    assert connector.manifest.scope("drafts.create") is not None
    assert connector.manifest.scope("messages.send") is None


def test_gmail_draft_connector_creates_plain_text_draft_and_returns_evidence() -> None:
    transport = GmailDraftTransport()
    connector = GmailDraftConnector(
        token_provider=lambda: "token",
        transport=transport,
        clock=lambda: NOW,
    )
    idempotency = IdempotencyMetadata(
        key="draft-one",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )

    result = connector.write(
        "drafts.create",
        {"to": "recipient@example.com", "subject": "Hello", "body": "Draft only."},
        idempotency=idempotency,
    )

    assert result.success is True
    assert result.external_id == "draft-1"
    assert transport.posts == 1
    assert result.evidence[0].verifies() is True
    evidence = json.loads(result.evidence[0].content)
    assert evidence["sent"] is False
    assert evidence["recovered_existing"] is False
    assert transport.last_raw is not None
    decoded = base64.urlsafe_b64decode(transport.last_raw.encode("ascii"))
    message = BytesParser(policy=policy.default).parsebytes(decoded)
    assert message["To"] == "recipient@example.com"
    assert message["Subject"] == "Hello"
    assert message["X-CHIEF-Draft"] == "approved"
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == "Draft only."


def test_gmail_draft_retry_recovers_existing_draft_without_duplicate_post() -> None:
    transport = GmailDraftTransport()
    connector = GmailDraftConnector(
        token_provider=lambda: "token",
        transport=transport,
        clock=lambda: NOW,
    )
    idempotency = IdempotencyMetadata(key="stable-draft", created_at=NOW)
    payload = {"to": "recipient@example.com", "subject": "Stable", "body": "One draft."}

    first = connector.write("drafts.create", payload, idempotency=idempotency)
    second = connector.write("drafts.create", payload, idempotency=idempotency)

    assert first.external_id == second.external_id == "draft-1"
    assert transport.posts == 1
    recovered = json.loads(second.evidence[0].content)
    assert recovered["recovered_existing"] is True
    assert recovered["sent"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {
            "to": "recipient@example.com",
            "subject": "Bad",
            "body": "Body",
            "cc": "other@example.com",
        },
        {
            "to": "recipient@example.com",
            "subject": "Bad",
            "body": "Body",
            "attachments": ["file"],
        },
        {
            "to": "Recipient <recipient@example.com>",
            "subject": "Bad",
            "body": "Body",
        },
        {
            "to": "a@example.com,b@example.com",
            "subject": "Bad",
            "body": "Body",
        },
    ],
)
def test_gmail_draft_connector_rejects_unbounded_email_features(payload) -> None:
    transport = GmailDraftTransport()
    connector = GmailDraftConnector(
        token_provider=lambda: "token",
        transport=transport,
        clock=lambda: NOW,
    )

    with pytest.raises(ValueError):
        connector.write(
            "drafts.create",
            payload,
            idempotency=IdempotencyMetadata(key="reject", created_at=NOW),
        )

    assert transport.posts == 0


def test_gmail_draft_connector_rejects_send_scope() -> None:
    connector = GmailDraftConnector(token_provider=lambda: "token")

    with pytest.raises(PermissionError, match="unsupported Gmail draft scope"):
        connector.write(
            "messages.send",
            {"to": "recipient@example.com", "subject": "No", "body": "No"},
            idempotency=IdempotencyMetadata(key="no-send", created_at=NOW),
        )


def test_connector_write_requires_tool_approval_before_external_mutation() -> None:
    transport = GmailDraftTransport()
    registry = _registry_with_connector(transport)
    registry.grant_consent(
        ConsentGrant(
            connector_id="gmail_drafts",
            principal_id="local",
            scopes=frozenset({"drafts.create"}),
            purpose="Create drafts only after owner approval",
            granted_at=NOW,
        )
    )
    tools = ToolRegistry()
    tools.register(ConnectorWriteTool(registry, clock=lambda: NOW))

    result = tools.execute(
        "connector_write",
        _tool_arguments(),
        approved=False,
        audit_context={"actor_id": "local"},
    )

    assert result.success is False
    assert "approval" in (result.error or "").casefold()
    assert transport.posts == 0


def test_connector_write_requires_consent_for_authenticated_actor() -> None:
    transport = GmailDraftTransport()
    registry = _registry_with_connector(transport)
    registry.grant_consent(
        ConsentGrant(
            connector_id="gmail_drafts",
            principal_id="different-actor",
            scopes=frozenset({"drafts.create"}),
            purpose="Different actor",
            granted_at=NOW,
        )
    )
    tools = ToolRegistry()
    tools.register(ConnectorWriteTool(registry, clock=lambda: NOW))

    result = tools.execute(
        "connector_write",
        _tool_arguments(),
        approved=True,
        audit_context={"actor_id": "local"},
    )

    assert result.success is False
    assert "no active consent" in (result.error or "").casefold()
    assert transport.posts == 0


def test_model_cannot_spoof_connector_write_principal() -> None:
    transport = GmailDraftTransport()
    registry = _registry_with_connector(transport)
    registry.grant_consent(
        ConsentGrant(
            connector_id="gmail_drafts",
            principal_id="attacker",
            scopes=frozenset({"drafts.create"}),
            purpose="Attacker consent",
            granted_at=NOW,
        )
    )
    tools = ToolRegistry()
    tools.register(ConnectorWriteTool(registry, clock=lambda: NOW))
    arguments = _tool_arguments()
    arguments["principal_id"] = "attacker"

    result = tools.execute(
        "connector_write",
        arguments,
        approved=True,
        audit_context={"actor_id": "local"},
    )

    assert result.success is False
    assert "exactly" in (result.error or "").casefold()
    assert transport.posts == 0


def test_connector_write_requires_authenticated_tool_context() -> None:
    transport = GmailDraftTransport()
    registry = _registry_with_connector(transport)
    tools = ToolRegistry()
    tools.register(ConnectorWriteTool(registry, clock=lambda: NOW))

    result = tools.execute(
        "connector_write",
        _tool_arguments(),
        approved=True,
    )

    assert result.success is False
    assert "authenticated actor" in (result.error or "").casefold()
    assert transport.posts == 0


def test_approved_connector_write_creates_draft_for_consented_actor() -> None:
    transport = GmailDraftTransport()
    registry = _registry_with_connector(transport)
    registry.grant_consent(
        ConsentGrant(
            connector_id="gmail_drafts",
            principal_id="local",
            scopes=frozenset({"drafts.create"}),
            purpose="Approved reversible drafts",
            granted_at=NOW,
        )
    )
    tools = ToolRegistry()
    tools.register(ConnectorWriteTool(registry, clock=lambda: NOW))

    result = tools.execute(
        "connector_write",
        _tool_arguments(),
        approved=True,
        audit_context={"actor_id": "local"},
    )

    assert result.success is True
    assert result.data["external_id"] == "draft-1"
    assert result.data["connector_id"] == "gmail_drafts"
    assert result.data["scope"] == "drafts.create"
    assert transport.posts == 1
