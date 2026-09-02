from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from chief.notifications.delivery import NotificationDispatcher
from chief.notifications.schema import DeliveryStatus, NotificationChannel
from chief.notifications.store import NotificationStore


@dataclass(frozen=True, slots=True)
class NotificationPumpResult:
    selected: int
    delivered: int
    failed: int
    skipped: int


class NotificationPump:
    """Bounded background delivery for policy-approved interrupt notifications."""

    def __init__(
        self,
        store: NotificationStore,
        dispatcher: NotificationDispatcher,
        *,
        max_notifications_per_tick: int = 10,
        failure_backoff_seconds: int = 300,
    ) -> None:
        if not 1 <= max_notifications_per_tick <= 1_000:
            raise ValueError("max_notifications_per_tick must be between 1 and 1,000")
        if not 1 <= failure_backoff_seconds <= 86_400:
            raise ValueError("failure_backoff_seconds must be between 1 and 86,400")
        self.store = store
        self.dispatcher = dispatcher
        self.max_notifications_per_tick = max_notifications_per_tick
        self.failure_backoff = timedelta(seconds=failure_backoff_seconds)

    def _pending_ids(self, now: datetime) -> list[UUID]:
        timestamp = now.astimezone(UTC).isoformat().replace("+00:00", "Z")
        connection = sqlite3.connect(self.store.database_path, timeout=5)
        try:
            rows = connection.execute(
                """
                SELECT n.id
                FROM notifications AS n
                JOIN notification_decisions AS d ON d.notification_id = n.id
                WHERE d.action = 'interrupt'
                  AND n.acknowledged_at IS NULL
                  AND (n.expires_at IS NULL OR n.expires_at > ?)
                ORDER BY n.priority DESC, d.decided_at ASC
                LIMIT ?
                """,
                (timestamp, self.max_notifications_per_tick),
            ).fetchall()
        finally:
            connection.close()
        return [UUID(str(row[0])) for row in rows]

    def _supported_pending_channels(self, notification_id: UUID, now: datetime) -> set[NotificationChannel]:
        notification = self.store.get(notification_id)
        if notification is None:
            return set()
        supported = set(self.dispatcher.providers).intersection(notification.channels)
        pending: set[NotificationChannel] = set()
        attempts = self.store.delivery_attempts(notification_id)
        for channel in supported:
            channel_attempts = [attempt for attempt in attempts if attempt.channel is channel]
            delivered = any(
                receipt.status is DeliveryStatus.DELIVERED
                for attempt in channel_attempts
                for receipt in self.store.delivery_receipts(attempt.id)
            )
            if delivered:
                continue
            if channel_attempts:
                latest = max(channel_attempts, key=lambda attempt: attempt.attempted_at)
                if (
                    latest.status is DeliveryStatus.FAILED
                    and now - latest.attempted_at < self.failure_backoff
                ):
                    continue
            pending.add(channel)
        return pending

    def pump_once(self, *, now: datetime | None = None) -> NotificationPumpResult:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        selected = delivered = failed = skipped = 0
        for notification_id in self._pending_ids(now):
            selected += 1
            if not self._supported_pending_channels(notification_id, now):
                skipped += 1
                continue
            receipts = self.dispatcher.deliver(notification_id, now=now)
            if not receipts:
                skipped += 1
                continue
            delivered += sum(receipt.status is DeliveryStatus.DELIVERED for receipt in receipts)
            failed += sum(receipt.status is DeliveryStatus.FAILED for receipt in receipts)
        return NotificationPumpResult(
            selected=selected,
            delivered=delivered,
            failed=failed,
            skipped=skipped,
        )
