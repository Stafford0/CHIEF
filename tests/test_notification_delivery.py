from __future__ import annotations

from datetime import UTC, datetime

import pytest

from chief.notifications.delivery import NotificationDispatcher, ProviderDelivery, SMTPEmailProvider
from chief.notifications.schema import (
    AttentionAction,
    AttentionDecision,
    AttentionReason,
    DeliveryStatus,
    Notification,
    NotificationChannel,
    NotificationPriority,
)
from chief.notifications.store import NotificationStore

NOW = datetime(2026, 9, 2, 2, 0, tzinfo=UTC)


class FakeEmailProvider:
    channel = NotificationChannel.EMAIL

    def __init__(self) -> None:
        self.sent = 0

    def send(self, notification: Notification) -> ProviderDelivery:
        self.sent += 1
        return ProviderDelivery(provider_reference="fake-message-1", detail="accepted")


def _notification() -> Notification:
    return Notification(
        recipient_id="owner",
        source="test",
        title="Needs attention",
        body="Review Parcel Signals ingestion health.",
        priority=NotificationPriority.HIGH,
        channels=(NotificationChannel.EMAIL,),
        idempotency_key="delivery-notice-1",
        dedup_key="delivery-notice",
        created_at=NOW,
    )


def _allow_interrupt(store: NotificationStore, notification: Notification) -> None:
    store.record_decision(
        AttentionDecision(
            notification_id=notification.id,
            recipient_id=notification.recipient_id,
            action=AttentionAction.INTERRUPT,
            reason=AttentionReason.INTERRUPT_ALLOWED,
            decided_at=NOW,
        )
    )


def test_dispatcher_requires_interrupt_decision(tmp_path) -> None:
    store = NotificationStore(tmp_path / "chief.db")
    notification = store.save(_notification())
    provider = FakeEmailProvider()

    with pytest.raises(PermissionError, match="interrupt"):
        NotificationDispatcher(store, [provider]).deliver(notification.id, now=NOW)
    assert provider.sent == 0


def test_dispatcher_records_receipt_and_does_not_duplicate_delivery(tmp_path) -> None:
    store = NotificationStore(tmp_path / "chief.db")
    notification = store.save(_notification())
    _allow_interrupt(store, notification)
    provider = FakeEmailProvider()
    dispatcher = NotificationDispatcher(store, [provider])

    first = dispatcher.deliver(notification.id, now=NOW)
    second = dispatcher.deliver(notification.id, now=NOW)

    assert provider.sent == 1
    assert len(first) == 1
    assert first[0].status is DeliveryStatus.DELIVERED
    assert first[0].provider_reference == "fake-message-1"
    assert second == []
    attempts = store.delivery_attempts(notification.id)
    assert len(attempts) == 1
    assert attempts[0].status is DeliveryStatus.SENT
    assert store.delivery_receipts(attempts[0].id) == first


def test_dispatcher_persists_failed_delivery_for_retry_visibility(tmp_path) -> None:
    store = NotificationStore(tmp_path / "chief.db")
    notification = store.save(_notification())
    _allow_interrupt(store, notification)

    class FailingProvider:
        channel = NotificationChannel.EMAIL

        def send(self, notification):
            raise RuntimeError("smtp unavailable")

    receipts = NotificationDispatcher(store, [FailingProvider()]).deliver(notification.id, now=NOW)
    assert receipts[0].status is DeliveryStatus.FAILED
    assert "smtp unavailable" in (receipts[0].detail or "")
    attempts = store.delivery_attempts(notification.id)
    assert attempts[0].status is DeliveryStatus.FAILED


def test_smtp_provider_uses_starttls_login_and_never_returns_password() -> None:
    events: list[object] = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            events.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            events.append(("close",))

        def starttls(self):
            events.append(("starttls",))

        def login(self, username, password):
            events.append(("login", username, password))

        def send_message(self, message):
            events.append(("send", message["To"], message["Subject"], message.get_content()))

    provider = SMTPEmailProvider(
        host="smtp.example.test",
        port=587,
        sender="chief@example.test",
        recipient="owner@example.test",
        username="chief-user",
        password_provider=lambda: "super-secret-password",
        starttls=True,
        use_ssl=False,
        smtp_factory=FakeSMTP,
    )
    result = provider.send(_notification())

    assert events[0] == ("connect", "smtp.example.test", 587, 20)
    assert ("starttls",) in events
    assert ("login", "chief-user", "super-secret-password") in events
    assert any(event[0] == "send" for event in events)
    assert "super-secret-password" not in repr(result)
