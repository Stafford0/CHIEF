"""Durable local persistence for notifications, decisions, attempts, and receipts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from .schema import (
    AttentionAction,
    AttentionDecision,
    AttentionReason,
    DeliveryAttempt,
    DeliveryReceipt,
    DeliveryStatus,
    Notification,
    NotificationChannel,
    NotificationPriority,
    require_aware,
)


class NotificationStoreError(RuntimeError):
    """Base persistence error for local notification state."""


class IdempotencyConflict(NotificationStoreError):
    """Raised when an idempotency key is reused for different content."""


def _timestamp(value: datetime) -> str:
    require_aware(value, "stored timestamp")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _notification_fingerprint(notification: Notification) -> str:
    return _fingerprint(
        {
            "recipient_id": notification.recipient_id,
            "source": notification.source,
            "title": notification.title,
            "body": notification.body,
            "priority": int(notification.priority),
            "channels": [channel.value for channel in notification.channels],
            "idempotency_key": notification.idempotency_key,
            "dedup_key": notification.dedup_key,
            "created_at": _timestamp(notification.created_at),
            "expires_at": (
                _timestamp(notification.expires_at) if notification.expires_at is not None else None
            ),
            "acknowledgement_required": notification.acknowledgement_required,
        }
    )


def _attempt_fingerprint(attempt: DeliveryAttempt) -> str:
    return _fingerprint(
        {
            "notification_id": str(attempt.notification_id),
            "channel": attempt.channel.value,
            "attempt_number": attempt.attempt_number,
            "idempotency_key": attempt.idempotency_key,
            "status": attempt.status.value,
            "attempted_at": _timestamp(attempt.attempted_at),
            "completed_at": (
                _timestamp(attempt.completed_at) if attempt.completed_at is not None else None
            ),
            "error": attempt.error,
        }
    )


def _receipt_fingerprint(receipt: DeliveryReceipt) -> str:
    return _fingerprint(
        {
            "attempt_id": str(receipt.attempt_id),
            "idempotency_key": receipt.idempotency_key,
            "status": receipt.status.value,
            "received_at": _timestamp(receipt.received_at),
            "provider_reference": receipt.provider_reference,
            "detail": receipt.detail,
        }
    )


class NotificationStore:
    """SQLite-backed attention history; it never invokes a delivery provider."""

    def __init__(
        self,
        database_path: str | Path = "data/chief.db",
        *,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        if busy_timeout_ms < 1:
            raise ValueError("busy_timeout_ms must be positive")
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_ms = busy_timeout_ms
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_ms / 1_000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA synchronous = FULL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS notifications (
                    id TEXT PRIMARY KEY,
                    recipient_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    channels_json TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    dedup_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    acknowledgement_required INTEGER NOT NULL
                        CHECK (acknowledgement_required IN (0, 1)),
                    acknowledged_at TEXT,
                    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64)
                );
                CREATE INDEX IF NOT EXISTS ix_notifications_active
                    ON notifications(recipient_id, expires_at, acknowledged_at, created_at);
                CREATE INDEX IF NOT EXISTS ix_notifications_dedup
                    ON notifications(recipient_id, dedup_key, created_at);

                CREATE TABLE IF NOT EXISTS notification_decisions (
                    id TEXT PRIMARY KEY,
                    notification_id TEXT NOT NULL UNIQUE
                        REFERENCES notifications(id) ON DELETE RESTRICT,
                    recipient_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    decided_at TEXT NOT NULL,
                    next_eligible_at TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_notification_decisions_budget
                    ON notification_decisions(recipient_id, action, decided_at);

                CREATE TABLE IF NOT EXISTS notification_delivery_attempts (
                    id TEXT PRIMARY KEY,
                    notification_id TEXT NOT NULL
                        REFERENCES notifications(id) ON DELETE RESTRICT,
                    channel TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    attempted_at TEXT NOT NULL,
                    completed_at TEXT,
                    error TEXT,
                    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
                    UNIQUE(notification_id, channel, attempt_number)
                );
                CREATE INDEX IF NOT EXISTS ix_notification_attempts_notification
                    ON notification_delivery_attempts(notification_id, attempted_at);

                CREATE TABLE IF NOT EXISTS notification_delivery_receipts (
                    id TEXT PRIMARY KEY,
                    attempt_id TEXT NOT NULL
                        REFERENCES notification_delivery_attempts(id) ON DELETE RESTRICT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    provider_reference TEXT,
                    detail TEXT,
                    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64)
                );
                CREATE INDEX IF NOT EXISTS ix_notification_receipts_attempt
                    ON notification_delivery_receipts(attempt_id, received_at);
                """
            )

    def save(self, notification: Notification) -> Notification:
        if not isinstance(notification, Notification):
            raise TypeError("notification must be a Notification")
        fingerprint = _notification_fingerprint(notification)
        channels_json = json.dumps([channel.value for channel in notification.channels])
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO notifications (
                        id, recipient_id, source, title, body, priority, channels_json,
                        idempotency_key, dedup_key, created_at, expires_at,
                        acknowledgement_required, acknowledged_at, fingerprint
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(notification.id),
                        notification.recipient_id,
                        notification.source,
                        notification.title,
                        notification.body,
                        int(notification.priority),
                        channels_json,
                        notification.idempotency_key,
                        notification.dedup_key,
                        _timestamp(notification.created_at),
                        (
                            _timestamp(notification.expires_at)
                            if notification.expires_at is not None
                            else None
                        ),
                        int(notification.acknowledgement_required),
                        (
                            _timestamp(notification.acknowledged_at)
                            if notification.acknowledged_at is not None
                            else None
                        ),
                        fingerprint,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM notifications WHERE id = ?", (str(notification.id),)
                ).fetchone()
            except sqlite3.IntegrityError as exc:
                row = connection.execute(
                    "SELECT * FROM notifications WHERE idempotency_key = ?",
                    (notification.idempotency_key,),
                ).fetchone()
                if row is None:
                    raise IdempotencyConflict(
                        f"notification id {notification.id} is already in use"
                    ) from exc
                if row["fingerprint"] != fingerprint:
                    raise IdempotencyConflict(
                        "notification idempotency key was reused for different content"
                    ) from exc
        assert row is not None
        return self._notification(row)

    def get(self, notification_id: UUID) -> Notification | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM notifications WHERE id = ?", (str(notification_id),)
            ).fetchone()
        return self._notification(row) if row is not None else None

    def get_by_idempotency_key(self, idempotency_key: str) -> Notification | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM notifications WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
        return self._notification(row) if row is not None else None

    def acknowledge(self, notification_id: UUID, *, at: datetime) -> Notification:
        require_aware(at, "notification acknowledgement time")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM notifications WHERE id = ?", (str(notification_id),)
            ).fetchone()
            if row is None:
                raise KeyError(f"notification {notification_id} does not exist")
            current = self._notification(row)
            acknowledged = current.acknowledge(at)
            if current.acknowledged_at is None:
                connection.execute(
                    "UPDATE notifications SET acknowledged_at = ? WHERE id = ?",
                    (_timestamp(acknowledged.acknowledged_at), str(notification_id)),
                )
                row = connection.execute(
                    "SELECT * FROM notifications WHERE id = ?", (str(notification_id),)
                ).fetchone()
        assert row is not None
        return self._notification(row)

    def active(self, *, recipient_id: str, now: datetime, limit: int = 100) -> list[Notification]:
        require_aware(now, "active-notification comparison time")
        if not 1 <= limit <= 1_000:
            raise ValueError("notification limit must be between 1 and 1000")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM notifications
                WHERE recipient_id = ? AND acknowledged_at IS NULL
                    AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY priority DESC, created_at ASC LIMIT ?
                """,
                (recipient_id, _timestamp(now), limit),
            ).fetchall()
        return [self._notification(row) for row in rows]

    def record_decision(self, decision: AttentionDecision) -> AttentionDecision:
        if not isinstance(decision, AttentionDecision):
            raise TypeError("decision must be an AttentionDecision")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            notification = connection.execute(
                "SELECT recipient_id, created_at FROM notifications WHERE id = ?",
                (str(decision.notification_id),),
            ).fetchone()
            if notification is None:
                raise KeyError(f"notification {decision.notification_id} does not exist")
            if notification["recipient_id"] != decision.recipient_id:
                raise ValueError("decision recipient does not match notification recipient")
            created_at = _datetime(notification["created_at"])
            assert created_at is not None
            if decision.decided_at < created_at:
                raise ValueError("decision cannot precede notification creation")
            connection.execute(
                """
                INSERT INTO notification_decisions (
                    id, notification_id, recipient_id, action, reason,
                    decided_at, next_eligible_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(notification_id) DO NOTHING
                """,
                (
                    str(decision.id),
                    str(decision.notification_id),
                    decision.recipient_id,
                    decision.action.value,
                    decision.reason.value,
                    _timestamp(decision.decided_at),
                    (
                        _timestamp(decision.next_eligible_at)
                        if decision.next_eligible_at is not None
                        else None
                    ),
                ),
            )
            row = connection.execute(
                "SELECT * FROM notification_decisions WHERE notification_id = ?",
                (str(decision.notification_id),),
            ).fetchone()
        assert row is not None
        return self._decision(row)

    def decision_for(self, notification_id: UUID) -> AttentionDecision | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM notification_decisions WHERE notification_id = ?",
                (str(notification_id),),
            ).fetchone()
        return self._decision(row) if row is not None else None

    def latest_interrupt_at(
        self,
        *,
        recipient_id: str,
        dedup_key: str,
        before: datetime,
    ) -> datetime | None:
        require_aware(before, "interrupt lookup time")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT d.decided_at FROM notification_decisions AS d
                JOIN notifications AS n ON n.id = d.notification_id
                WHERE d.recipient_id = ? AND n.dedup_key = ? AND d.action = ?
                    AND d.decided_at <= ?
                ORDER BY d.decided_at DESC LIMIT 1
                """,
                (
                    recipient_id,
                    dedup_key,
                    AttentionAction.INTERRUPT.value,
                    _timestamp(before),
                ),
            ).fetchone()
        return _datetime(row["decided_at"]) if row is not None else None

    def count_interruptions(
        self,
        *,
        recipient_id: str,
        start: datetime,
        end: datetime,
    ) -> int:
        require_aware(start, "interruption-count start")
        require_aware(end, "interruption-count end")
        if end <= start:
            raise ValueError("interruption-count end must be after start")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count FROM notification_decisions
                WHERE recipient_id = ? AND action = ?
                    AND decided_at >= ? AND decided_at < ?
                """,
                (
                    recipient_id,
                    AttentionAction.INTERRUPT.value,
                    _timestamp(start),
                    _timestamp(end),
                ),
            ).fetchone()
        assert row is not None
        return int(row["count"])

    def record_delivery_attempt(self, attempt: DeliveryAttempt) -> DeliveryAttempt:
        if not isinstance(attempt, DeliveryAttempt):
            raise TypeError("attempt must be a DeliveryAttempt")
        fingerprint = _attempt_fingerprint(attempt)
        with self._connect() as connection:
            notification_row = connection.execute(
                "SELECT * FROM notifications WHERE id = ?", (str(attempt.notification_id),)
            ).fetchone()
            if notification_row is None:
                raise KeyError(f"notification {attempt.notification_id} does not exist")
            notification = self._notification(notification_row)
            if attempt.channel not in notification.channels:
                raise ValueError("delivery channel was not requested by the notification")
            if attempt.attempted_at < notification.created_at:
                raise ValueError("delivery attempt cannot precede notification creation")
            try:
                connection.execute(
                    """
                    INSERT INTO notification_delivery_attempts (
                        id, notification_id, channel, attempt_number, idempotency_key,
                        status, attempted_at, completed_at, error, fingerprint
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(attempt.id),
                        str(attempt.notification_id),
                        attempt.channel.value,
                        attempt.attempt_number,
                        attempt.idempotency_key,
                        attempt.status.value,
                        _timestamp(attempt.attempted_at),
                        (
                            _timestamp(attempt.completed_at)
                            if attempt.completed_at is not None
                            else None
                        ),
                        attempt.error,
                        fingerprint,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM notification_delivery_attempts WHERE id = ?",
                    (str(attempt.id),),
                ).fetchone()
            except sqlite3.IntegrityError as exc:
                row = connection.execute(
                    """
                    SELECT * FROM notification_delivery_attempts
                    WHERE idempotency_key = ?
                    """,
                    (attempt.idempotency_key,),
                ).fetchone()
                if row is None or row["fingerprint"] != fingerprint:
                    raise IdempotencyConflict(
                        "delivery attempt conflicts with an existing attempt"
                    ) from exc
        assert row is not None
        return self._attempt(row)

    def delivery_attempts(self, notification_id: UUID) -> list[DeliveryAttempt]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM notification_delivery_attempts
                WHERE notification_id = ? ORDER BY attempted_at, attempt_number
                """,
                (str(notification_id),),
            ).fetchall()
        return [self._attempt(row) for row in rows]

    def record_delivery_receipt(self, receipt: DeliveryReceipt) -> DeliveryReceipt:
        if not isinstance(receipt, DeliveryReceipt):
            raise TypeError("receipt must be a DeliveryReceipt")
        fingerprint = _receipt_fingerprint(receipt)
        with self._connect() as connection:
            attempt = connection.execute(
                "SELECT attempted_at FROM notification_delivery_attempts WHERE id = ?",
                (str(receipt.attempt_id),),
            ).fetchone()
            if attempt is None:
                raise KeyError(f"delivery attempt {receipt.attempt_id} does not exist")
            attempted_at = _datetime(attempt["attempted_at"])
            assert attempted_at is not None
            if receipt.received_at < attempted_at:
                raise ValueError("delivery receipt cannot precede its attempt")
            try:
                connection.execute(
                    """
                    INSERT INTO notification_delivery_receipts (
                        id, attempt_id, idempotency_key, status, received_at,
                        provider_reference, detail, fingerprint
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(receipt.id),
                        str(receipt.attempt_id),
                        receipt.idempotency_key,
                        receipt.status.value,
                        _timestamp(receipt.received_at),
                        receipt.provider_reference,
                        receipt.detail,
                        fingerprint,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM notification_delivery_receipts WHERE id = ?",
                    (str(receipt.id),),
                ).fetchone()
            except sqlite3.IntegrityError as exc:
                row = connection.execute(
                    """
                    SELECT * FROM notification_delivery_receipts
                    WHERE idempotency_key = ?
                    """,
                    (receipt.idempotency_key,),
                ).fetchone()
                if row is None or row["fingerprint"] != fingerprint:
                    raise IdempotencyConflict(
                        "delivery receipt idempotency key was reused for different content"
                    ) from exc
        assert row is not None
        return self._receipt(row)

    def delivery_receipts(self, attempt_id: UUID) -> list[DeliveryReceipt]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM notification_delivery_receipts
                WHERE attempt_id = ? ORDER BY received_at, id
                """,
                (str(attempt_id),),
            ).fetchall()
        return [self._receipt(row) for row in rows]

    @staticmethod
    def _notification(row: sqlite3.Row) -> Notification:
        created_at = _datetime(row["created_at"])
        assert created_at is not None
        return Notification(
            id=UUID(row["id"]),
            recipient_id=row["recipient_id"],
            source=row["source"],
            title=row["title"],
            body=row["body"],
            priority=NotificationPriority(int(row["priority"])),
            channels=tuple(NotificationChannel(item) for item in json.loads(row["channels_json"])),
            idempotency_key=row["idempotency_key"],
            dedup_key=row["dedup_key"],
            created_at=created_at,
            expires_at=_datetime(row["expires_at"]),
            acknowledgement_required=bool(row["acknowledgement_required"]),
            acknowledged_at=_datetime(row["acknowledged_at"]),
        )

    @staticmethod
    def _decision(row: sqlite3.Row) -> AttentionDecision:
        decided_at = _datetime(row["decided_at"])
        assert decided_at is not None
        return AttentionDecision(
            id=UUID(row["id"]),
            notification_id=UUID(row["notification_id"]),
            recipient_id=row["recipient_id"],
            action=AttentionAction(row["action"]),
            reason=AttentionReason(row["reason"]),
            decided_at=decided_at,
            next_eligible_at=_datetime(row["next_eligible_at"]),
        )

    @staticmethod
    def _attempt(row: sqlite3.Row) -> DeliveryAttempt:
        attempted_at = _datetime(row["attempted_at"])
        assert attempted_at is not None
        return DeliveryAttempt(
            id=UUID(row["id"]),
            notification_id=UUID(row["notification_id"]),
            channel=NotificationChannel(row["channel"]),
            attempt_number=int(row["attempt_number"]),
            idempotency_key=row["idempotency_key"],
            status=DeliveryStatus(row["status"]),
            attempted_at=attempted_at,
            completed_at=_datetime(row["completed_at"]),
            error=row["error"],
        )

    @staticmethod
    def _receipt(row: sqlite3.Row) -> DeliveryReceipt:
        received_at = _datetime(row["received_at"])
        assert received_at is not None
        return DeliveryReceipt(
            id=UUID(row["id"]),
            attempt_id=UUID(row["attempt_id"]),
            idempotency_key=row["idempotency_key"],
            status=DeliveryStatus(row["status"]),
            received_at=received_at,
            provider_reference=row["provider_reference"],
            detail=row["detail"],
        )
