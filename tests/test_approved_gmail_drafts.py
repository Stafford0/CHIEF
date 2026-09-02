from __future__ import annotations

import base64
import email
import json
from datetime import UTC, datetime
from typing import Any

from chief.integrations.gmail_drafts import GmailDraftConnector
from chief.integrations.registry import ConnectorRegistry
from chief.integrations.schema import (
    ConsentGrant,
    IdempotencyMetadata,
)
from chief.tools.connector_write import ConnectorWriteTool
from chief.tools.registry import ToolRegistry

NOW = datetime(2026, 9, 2, 3, 0, tzinfo=UTC)


class GmailDraftTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []
        self.created: dict[str, dict[str, str]] = {}
        self.posts = 0

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[int, Any, dict[str, str], float]:
        self.calls.append((method, url, dict(headers), body))
        if url.endswith("/users/me/profile"):
            return 200, {"emailAddress": "owner@example.com"}, {}, 1.0
        if "/users/me/drafts?" in url:
            query = url.split("q=", 1)[-1]
            for message_id, record in self.created.items():
                encoded = message_id.replace("<", "%3C").replace(">", "%3E").replace("@", "%40")
                if encoded in query or message_id in query:
                    return (
                        200,
                        {"drafts": [{"id": record["draft_id"], "message": {"id": record["message_id"]}}]},
                        {},
                        1.0,
                    )
            return 200, {"drafts": []}, {}, 1.0
        if method == "POST" and url.endswith("/users/me/drafts"):
            self.posts += 1
            assert body is not None
            payload = json.loads(body.decode("utf-8"))
            raw = base64.urlsafe_b64decode(payload["message"]["raw"])
            message = email.message_from_bytes(raw)
            message_id = str(message["Message-ID"])
            record = {
                "draft_id": f"draft-{self.posts}",
                "message_id": f"message-{self.posts}",
                "to": str(message["To"]),
                "subject": str(message["Subject"]),
                "body": message.get_payload(decode=True).decode("utf-8"),
            }
            self.created[message_id] = record
            return 200, {"id": record["draft_id"], "message": {"id": record["message_id"]}}, {}, 2.0
        raise AssertionError(f"Unexpected transport request: {method} {url}")


def _connector(transport: GmailDraftTransport) -> GmailDraftConnector:
    return GmailDraftConnector(
        token_provider=lambda: "oauth-token",
        transport=transport,
        clock=lambda: NOW,
    )


def _idempotency(key: str = "draft-key") -> IdempotencyMetadata:
    return IdempotencyMetadata(key=key, created_at=NOW)


def test_gmail_draft_connector_is_write_only_and_exposes_no_send_scope() -> None:
    connector = _connector(GmailDraftTransport())
    manifest = connector.manifest

    assert {scope.name for scope in manifest.scopes} == {"drafts.create"}
    assert all(scope.is_write for scope in manifest.scopes)
    assert "send" not in " ".join(scope.name for scope in manifest.scopes)

    try:
        connector.read("drafts.create")
    except PermissionError as exc:
        assert "does not expose read scopes" in str(exc)
    else:
        raise AssertionError("draft connector unexpectedly permitted a read")


def test_gmail_draft_create_builds_plain_text_draft_and_never_sends() -> None:
    transport = GmailDraftTransport()
    connector = _connector(transport)

    result = connector.write(
        "drafts.create",
        {"to": "recipient@example.com", "subject": "Status", "body": "Draft body"},
        idempotency=_idempotency(),
    )

    assert result.success is True
    assert result.external_id == "draft-1"
    assert transport.posts == 1
    assert all("/send" not in call[1] for call in transport.calls)
    record = next(iter(transport.created.values()))
    assert record["to"] == "recipient@example.com"
    assert record["subject"] == "Status"
    assert "Draft body" in record["body"]
    evidence = json.loads(result.evidence[0].content)
    assert evidence["sent"] is False
    assert evidence["recovered_existing"] is False


def test_gmail_draft_retry_recovers_existing_draft_without_second_post() -> None:
    transport = GmailDraftTransport()
    connector = _connector(transport)
    payload = {"to": "recipient@example.com", "subject": "Status", "body": "Draft body"}

    first = connector.write("drafts.create", payload, idempotency=_idempotency("same-key"))
    second = connector.write("drafts.create", payload, idempotency=_idempotency("same-key"))

    assert first.external_id == second.external_id == "draft-1"
    assert transport.posts == 1
    assert json.loads(second.evidence[0].content)["recovered_existing"] is True


def test_gmail_draft_rejects_send_like_or_broad_payloads() -> None:
    connector = _connector(GmailDraftTransport())

    for payload in (
        {
            "to": "recipient@example.com",
            "subject": "Status",
            "body": "Draft body",
            "send": True,
        },
        {
            "to": "recipient@example.com",
            "subject": "Status",
            "body": "Draft body",
            "bcc": "hidden@example.com",
        },
    ):
        try:
            connector.write("drafts.create", payload, idempotency=_idempotency())
        except ValueError as exc:
            assert "unsupported fields" in str(exc)
        else:
            raise AssertionError("draft connector accepted unsupported mutation fields")

    try:
        connector.write(
            "messages.send",
            {"to": "recipient@example.com", "subject": "Status", "body": "Draft body"},
            idempotency=_idempotency(),
        )
    except PermissionError as exc:
        assert "unsupported Gmail draft scope" in str(exc)
    else:
        raise AssertionError("draft connector unexpectedly exposed a send scope")


def _registry_with_connector(transport: GmailDraftTransport) -> ConnectorRegistry:
    registry = ConnectorRegistry(clock=lambda: NOW)
    registry.register(_connector(transport))
    return registry


def _tool_arguments() -> dict[str, object]:
    return {
        "connector_id": "gmail_drafts",
        "scope": "drafts.create",
        "payload": {
            "to": "recipient@example.com",
            "subject": "Approved draft",
            "body": "Review before sending.",
        },
        "idempotency_key": "approval-bound-draft",
    }


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
    assert "requires approval" in (result.error or "").casefold()
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
    assert "no active consent" in (result.error or "")
    assert transport.posts == 0


def test_connector_write_uses_authenticated_context_not_model_principal() -> None:
    transport = GmailDraftTransport()
    registry = _registry_with_connector(transport)
    registry.grant_consent(
        ConsentGrant(
            connector_id="gmail_drafts",
            principal_id="local",
            scopes=frozenset({"drafts.create"}),
            purpose="Approved Gmail draft creation",
            granted_at=NOW,
        )
    )
    tools = ToolRegistry()
    tools.register(ConnectorWriteTool(registry, clock=lambda: NOW))

    bad_arguments = {**_tool_arguments(), "principal_id": "attacker"}
    rejected = tools.execute(
        "connector_write",
        bad_arguments,
        approved=True,
        audit_context={"actor_id": "local"},
    )
    assert rejected.success is False
    assert "requires exactly" in (rejected.error or "")
    assert transport.posts == 0

    approved = tools.execute(
        "connector_write",
        _tool_arguments(),
        approved=True,
        audit_context={"actor_id": "local", "proposal_id": "proposal-1"},
    )
    assert approved.success is True
    assert approved.data["external_id"] == "draft-1"
    assert approved.data["scope"] == "drafts.create"
    assert transport.posts == 1


def test_connector_write_refuses_missing_authenticated_context() -> None:
    transport = GmailDraftTransport()
    registry = _registry_with_connector(transport)
    tools = ToolRegistry()
    tools.register(ConnectorWriteTool(registry, clock=lambda: NOW))

    result = tools.execute("connector_write", _tool_arguments(), approved=True)

    assert result.success is False
    assert "authenticated CHIEF actor" in (result.error or "")
    assert transport.posts == 0
