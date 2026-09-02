from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest

from chief.integrations.gmail import GmailReadOnlyConnector
from chief.integrations.google_calendar import GoogleCalendarReadOnlyConnector
from chief.integrations.schema import ConnectorCapability, IdempotencyMetadata, SyncCursor
from chief.integrations.stripe import StripeReadOnlyConnector

NOW = datetime(2026, 9, 1, 20, 0, tzinfo=UTC)


def test_gmail_reads_metadata_only_and_uses_incremental_after_query() -> None:
    seen: list[str] = []

    def transport(url: str, headers: dict[str, str]):
        assert headers["Authorization"] == "Bearer gmail-token"
        seen.append(url)
        if "/users/me/messages/msg-1" in url:
            query = parse_qs(urlparse(url).query)
            assert query["format"] == ["metadata"]
            assert "metadataHeaders" in query
            return (
                200,
                {
                    "id": "msg-1",
                    "threadId": "thread-1",
                    "internalDate": "1788291000000",
                    "labelIds": ["INBOX"],
                    "snippet": "Build finished",
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Parcel Signals deploy"},
                            {"name": "From", "value": "ops@example.com"},
                        ]
                    },
                },
                {},
                3.0,
            )
        query = parse_qs(urlparse(url).query)
        assert query["maxResults"] == ["10"]
        assert query["q"] == ["after:1788285600"]
        return 200, {"messages": [{"id": "msg-1"}]}, {}, 2.0

    connector = GmailReadOnlyConnector(
        token_provider=lambda: "gmail-token", transport=transport, clock=lambda: NOW
    )
    cursor = SyncCursor(
        connector_id="gmail",
        scope="messages.metadata.read",
        value="2026-09-01T18:00:00+00:00",
        updated_at=datetime(2026, 9, 1, 18, 0, tzinfo=UTC),
    )

    result = connector.read("messages.metadata.read", cursor=cursor, limit=10)

    assert len(result.evidence) == 1
    assert result.evidence[0].source.record_type == "email_metadata"
    assert "Parcel Signals deploy" in result.evidence[0].content
    assert "body" not in result.evidence[0].content.casefold()
    assert result.next_cursor is not None
    assert len(seen) == 2


def test_gmail_manifest_has_no_write_capability() -> None:
    connector = GmailReadOnlyConnector(token_provider=lambda: "token")
    assert ConnectorCapability.WRITE not in connector.manifest.capabilities
    with pytest.raises(PermissionError):
        connector.write(
            "messages.write",
            {},
            idempotency=IdempotencyMetadata(key="x", created_at=NOW),
        )


def test_calendar_uses_provider_sync_token_and_returns_next_token() -> None:
    def transport(url: str, headers: dict[str, str]):
        assert headers["Authorization"] == "Bearer calendar-token"
        query = parse_qs(urlparse(url).query)
        assert query["syncToken"] == ["old-token"]
        assert query["showDeleted"] == ["true"]
        return (
            200,
            {
                "items": [
                    {
                        "id": "event-1",
                        "summary": "Investor call",
                        "status": "confirmed",
                        "updated": "2026-09-01T19:00:00Z",
                        "htmlLink": "https://calendar.google.com/event?eid=1",
                    }
                ],
                "nextSyncToken": "new-token",
            },
            {},
            4.0,
        )

    connector = GoogleCalendarReadOnlyConnector(
        token_provider=lambda: "calendar-token",
        transport=transport,
        clock=lambda: NOW,
    )
    result = connector.read(
        "events.read",
        cursor=SyncCursor(
            connector_id="google-calendar",
            scope="events.read",
            value="old-token",
            updated_at=NOW,
        ),
    )

    assert result.evidence[0].source.record_type == "calendar_event"
    assert result.next_cursor is not None
    assert result.next_cursor.value == "new-token"


def test_calendar_expired_sync_token_fails_explicitly() -> None:
    def transport(url: str, headers: dict[str, str]):
        del url, headers
        return 410, {"error": "Gone"}, {}, 1.0

    connector = GoogleCalendarReadOnlyConnector(
        token_provider=lambda: "token", transport=transport, clock=lambda: NOW
    )
    with pytest.raises(ValueError, match="sync token expired"):
        connector.read(
            "events.read",
            cursor=SyncCursor(
                connector_id="google-calendar",
                scope="events.read",
                value="expired",
                updated_at=NOW,
            ),
        )


def test_stripe_incremental_charge_read_is_bounded_and_read_only() -> None:
    seen_query: dict[str, list[str]] = {}

    def transport(url: str, headers: dict[str, str]):
        assert headers["Authorization"] == "Bearer rk_test_readonly"
        parsed = urlparse(url)
        assert parsed.path.endswith("/charges")
        seen_query.update(parse_qs(parsed.query))
        return (
            200,
            {
                "data": [
                    {
                        "id": "ch_1",
                        "created": 1788291000,
                        "amount": 14900,
                        "currency": "usd",
                        "paid": True,
                    }
                ],
                "has_more": False,
            },
            {},
            3.0,
        )

    connector = StripeReadOnlyConnector(
        api_key_provider=lambda: "rk_test_readonly",
        transport=transport,
        clock=lambda: NOW,
    )
    result = connector.read(
        "charges.read",
        cursor=SyncCursor(
            connector_id="stripe",
            scope="charges.read",
            value="2026-09-01T18:00:00+00:00",
            updated_at=datetime(2026, 9, 1, 18, 0, tzinfo=UTC),
        ),
        limit=25,
    )

    assert seen_query["limit"] == ["25"]
    assert seen_query["created[gt]"] == ["1788285600"]
    assert result.evidence[0].source.record_type == "charge"
    assert result.evidence[0].verifies()
    assert ConnectorCapability.WRITE not in connector.manifest.capabilities
    with pytest.raises(PermissionError):
        connector.write(
            "charges.write",
            {},
            idempotency=IdempotencyMetadata(key="x", created_at=NOW),
        )


def test_stripe_subscription_scope_uses_subscription_collection() -> None:
    def transport(url: str, headers: dict[str, str]):
        del headers
        assert urlparse(url).path.endswith("/subscriptions")
        return (
            200,
            {"data": [{"id": "sub_1", "created": 1788291000, "status": "active"}], "has_more": False},
            {},
            2.0,
        )

    connector = StripeReadOnlyConnector(
        api_key_provider=lambda: "rk_test_readonly", transport=transport, clock=lambda: NOW
    )
    result = connector.read("subscriptions.read", limit=1)
    assert result.evidence[0].source.record_type == "subscription"
