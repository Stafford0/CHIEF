from __future__ import annotations

import smtplib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Protocol

from chief.notifications.schema import (
    AttentionAction,
    DeliveryAttempt,
    DeliveryReceipt,
    DeliveryStatus,
    Notification,
    NotificationChannel,
)
from chief.notifications.store import NotificationStore


@dataclass(frozen=True, slots=True)
class ProviderDelivery:
    provider_reference: str | None = None
    detail: str | None = None


class DeliveryProvider(Protocol):
    @property
    def channel(self) -> NotificationChannel: ...

    def send(self, notification: Notification) -> ProviderDelivery: ...


class SMTPEmailProvider:
    """Synchronous SMTP provider with STARTTLS/SSL and injected secrets."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        recipient: str,
        username: str | None = None,
        password_provider: Callable[[], str | None] = lambda: None,
        use_ssl: bool = False,
        starttls: bool = True,
        timeout_seconds: float = 20,
        smtp_factory=None,
    ) -> None:
        if not host.strip() or not sender.strip() or not recipient.strip():
            raise ValueError("SMTP host, sender, and recipient are required.")
        if not 1 <= port <= 65535:
            raise ValueError("SMTP port must be between 1 and 65535.")
        if use_ssl and starttls:
            raise ValueError("SMTP SSL and STARTTLS cannot both be enabled.")
        self.host = host.strip()
        self.port = port
        self.sender = sender.strip()
        self.recipient = recipient.strip()
        self.username = username.strip() if username else None
        self.password_provider = password_provider
        self.use_ssl = use_ssl
        self.starttls = starttls
        self.timeout_seconds = timeout_seconds
        self.smtp_factory = smtp_factory

    @property
    def channel(self) -> NotificationChannel:
        return NotificationChannel.EMAIL

    def _client(self):
        factory = self.smtp_factory or (smtplib.SMTP_SSL if self.use_ssl else smtplib.SMTP)
        return factory(self.host, self.port, timeout=self.timeout_seconds)

    def send(self, notification: Notification) -> ProviderDelivery:
        message = EmailMessage()
        message["Subject"] = notification.title
        message["From"] = self.sender
        message["To"] = self.recipient
        message["Message-ID"] = make_msgid(domain=self.sender.partition("@")[2] or None)
        message.set_content(notification.body)

        password = self.password_provider()
        if self.username and password is None:
            raise RuntimeError("SMTP username is configured but password is unavailable.")
        with self._client() as client:
            if self.starttls:
                client.starttls()
            if self.username:
                client.login(self.username, password)
            client.send_message(message)
        return ProviderDelivery(
            provider_reference=str(message["Message-ID"]),
            detail="SMTP server accepted the message for delivery.",
        )


class NotificationDispatcher:
    """Deliver approved interruptions and persist attempts/receipts."""

    def __init__(self, store: NotificationStore, providers: list[DeliveryProvider]) -> None:
        self.store = store
        self.providers = {provider.channel: provider for provider in providers}

    def deliver(self, notification_id, *, now: datetime | None = None) -> list[DeliveryReceipt]:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        notification = self.store.get(notification_id)
        if notification is None:
            raise KeyError(f"notification {notification_id} does not exist")
        decision = self.store.decision_for(notification.id)
        if decision is None or decision.action is not AttentionAction.INTERRUPT:
            raise PermissionError("Notification delivery requires an interrupt attention decision.")
        if notification.expires_at is not None and notification.expires_at <= now:
            raise PermissionError("Expired notification cannot be delivered.")

        receipts: list[DeliveryReceipt] = []
        existing_attempts = self.store.delivery_attempts(notification.id)
        for channel in notification.channels:
            provider = self.providers.get(channel)
            if provider is None:
                continue
            previous = [attempt for attempt in existing_attempts if attempt.channel is channel]
            for attempt in previous:
                if any(
                    receipt.status is DeliveryStatus.DELIVERED
                    for receipt in self.store.delivery_receipts(attempt.id)
                ):
                    continue
                break
            else:
                attempt_number = len(previous) + 1
                idempotency_key = f"notify:{notification.id}:{channel.value}:{attempt_number}"
                try:
                    result = provider.send(notification)
                except Exception as exc:
                    attempt = self.store.record_delivery_attempt(
                        DeliveryAttempt(
                            notification_id=notification.id,
                            channel=channel,
                            attempt_number=attempt_number,
                            idempotency_key=idempotency_key,
                            status=DeliveryStatus.FAILED,
                            attempted_at=now,
                            completed_at=now,
                            error=str(exc) or exc.__class__.__name__,
                        )
                    )
                    receipt = self.store.record_delivery_receipt(
                        DeliveryReceipt(
                            attempt_id=attempt.id,
                            idempotency_key=f"{idempotency_key}:receipt",
                            status=DeliveryStatus.FAILED,
                            received_at=now,
                            detail=attempt.error,
                        )
                    )
                    receipts.append(receipt)
                    continue
                attempt = self.store.record_delivery_attempt(
                    DeliveryAttempt(
                        notification_id=notification.id,
                        channel=channel,
                        attempt_number=attempt_number,
                        idempotency_key=idempotency_key,
                        status=DeliveryStatus.SENT,
                        attempted_at=now,
                        completed_at=now,
                    )
                )
                receipt = self.store.record_delivery_receipt(
                    DeliveryReceipt(
                        attempt_id=attempt.id,
                        idempotency_key=f"{idempotency_key}:receipt",
                        status=DeliveryStatus.DELIVERED,
                        received_at=now,
                        provider_reference=result.provider_reference,
                        detail=result.detail,
                    )
                )
                receipts.append(receipt)
        return receipts
