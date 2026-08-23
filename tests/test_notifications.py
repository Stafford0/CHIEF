from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, time, timedelta

import pytest

from chief.notifications import (
    AttentionAction,
    AttentionPolicy,
    AttentionPolicyConfig,
    AttentionReason,
    DeliveryAttempt,
    DeliveryReceipt,
    DeliveryStatus,
    IdempotencyConflict,
    Notification,
    NotificationChannel,
    NotificationPriority,
    NotificationState,
    NotificationStore,
    QuietHours,
)

NOW = datetime(2026, 8, 23, 15, tzinfo=UTC)


def notification(
    index: int,
    *,
    priority: NotificationPriority = NotificationPriority.HIGH,
    dedup_key: str | None = None,
    created_at: datetime = NOW,
    expires_at: datetime | None = None,
    acknowledged_at: datetime | None = None,
) -> Notification:
    return Notification(
        recipient_id="founder-1",
        source="foresight",
        title=f"Signal {index}",
        body=f"Evidence-backed notification {index}",
        priority=priority,
        channels=(NotificationChannel.IN_APP, NotificationChannel.WEB_PUSH),
        idempotency_key=f"notification-{index}",
        dedup_key=dedup_key or f"signal-{index}",
        created_at=created_at,
        expires_at=expires_at,
        acknowledged_at=acknowledged_at,
    )


def test_priority_threshold_routes_low_priority_to_digest(tmp_path) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    policy = AttentionPolicy()

    result = policy.decide(notification(1, priority=NotificationPriority.NORMAL), store, now=NOW)

    assert result.action is AttentionAction.DIGEST
    assert result.reason is AttentionReason.BELOW_PRIORITY_THRESHOLD


def test_quiet_hours_use_local_timezone_and_critical_can_bypass(tmp_path) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    policy = AttentionPolicy(
        AttentionPolicyConfig(
            timezone="America/Chicago",
            quiet_hours=QuietHours(time(22), time(7), "America/Chicago"),
        )
    )
    local_11_pm = datetime(2026, 8, 24, 4, tzinfo=UTC)

    deferred = policy.decide(
        notification(1, created_at=local_11_pm - timedelta(minutes=1)),
        store,
        now=local_11_pm,
    )
    critical = policy.decide(
        notification(
            2,
            priority=NotificationPriority.CRITICAL,
            created_at=local_11_pm - timedelta(minutes=1),
        ),
        store,
        now=local_11_pm,
    )

    assert deferred.action is AttentionAction.DIGEST
    assert deferred.reason is AttentionReason.QUIET_HOURS
    assert deferred.next_eligible_at == datetime(2026, 8, 24, 12, tzinfo=UTC)
    assert critical.action is AttentionAction.INTERRUPT


def test_cooldown_deduplicates_interruptions_by_recipient_and_key(tmp_path) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    policy = AttentionPolicy(AttentionPolicyConfig(cooldown=timedelta(minutes=30)))
    first_time = NOW
    second_time = NOW + timedelta(minutes=5)

    first = policy.decide(
        notification(1, dedup_key="revenue-risk", created_at=first_time),
        store,
        now=first_time,
    )
    second = policy.decide(
        notification(2, dedup_key="revenue-risk", created_at=second_time),
        store,
        now=second_time,
    )

    assert first.action is AttentionAction.INTERRUPT
    assert second.action is AttentionAction.DIGEST
    assert second.reason is AttentionReason.COOLDOWN_ACTIVE
    assert second.next_eligible_at == first_time + timedelta(minutes=30)


def test_daily_budget_is_local_and_critical_can_bypass(tmp_path) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    policy = AttentionPolicy(
        AttentionPolicyConfig(timezone="America/Chicago", daily_interruption_budget=1)
    )

    first = policy.decide(notification(1), store, now=NOW)
    deferred = policy.decide(
        notification(2, created_at=NOW + timedelta(minutes=1)),
        store,
        now=NOW + timedelta(minutes=1),
    )
    critical = policy.decide(
        notification(
            3,
            priority=NotificationPriority.CRITICAL,
            created_at=NOW + timedelta(minutes=2),
        ),
        store,
        now=NOW + timedelta(minutes=2),
    )

    assert first.action is AttentionAction.INTERRUPT
    assert deferred.reason is AttentionReason.DAILY_BUDGET_EXHAUSTED
    assert deferred.action is AttentionAction.DIGEST
    assert critical.action is AttentionAction.INTERRUPT


def test_expired_and_acknowledged_notifications_are_suppressed(tmp_path) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    policy = AttentionPolicy()
    expired = notification(
        1,
        created_at=NOW - timedelta(hours=2),
        expires_at=NOW - timedelta(hours=1),
    )
    acknowledged = notification(
        2,
        created_at=NOW - timedelta(hours=1),
        acknowledged_at=NOW - timedelta(minutes=5),
    )

    expired_result = policy.decide(expired, store, now=NOW)
    acknowledged_result = policy.decide(acknowledged, store, now=NOW)

    assert expired.state_at(NOW) is NotificationState.EXPIRED
    assert expired_result.reason is AttentionReason.EXPIRED
    assert expired_result.action is AttentionAction.SUPPRESS
    assert acknowledged.state_at(NOW) is NotificationState.ACKNOWLEDGED
    assert acknowledged_result.reason is AttentionReason.ACKNOWLEDGED


def test_sqlite_persists_and_idempotently_restores_notifications_and_decisions(tmp_path) -> None:
    path = tmp_path / "notifications.db"
    original = notification(1)
    first_store = NotificationStore(path)
    first = first_store.save(original)
    decision = AttentionPolicy().decide(original, first_store, now=NOW)

    reopened = NotificationStore(path)
    duplicate = reopened.save(original)
    restored = reopened.get(original.id)
    restored_decision = reopened.decision_for(original.id)

    assert duplicate.id == first.id
    assert restored == first
    assert restored_decision == decision
    assert AttentionPolicy().decide(original, reopened, now=NOW) == decision


def test_reused_notification_idempotency_key_with_different_content_is_rejected(
    tmp_path,
) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    original = notification(1)
    store.save(original)

    with pytest.raises(IdempotencyConflict, match="different content"):
        store.save(replace(original, body="Different content"))


def test_acknowledgement_is_persistent_and_idempotent(tmp_path) -> None:
    path = tmp_path / "notifications.db"
    store = NotificationStore(path)
    original = store.save(notification(1))

    acknowledged = store.acknowledge(original.id, at=NOW + timedelta(minutes=1))
    repeated = store.acknowledge(original.id, at=NOW + timedelta(minutes=2))

    assert acknowledged.acknowledged_at == NOW + timedelta(minutes=1)
    assert repeated.acknowledged_at == acknowledged.acknowledged_at
    assert NotificationStore(path).get(original.id) == acknowledged


def test_active_query_excludes_acknowledged_and_expired_records(tmp_path) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    active = store.save(notification(1, created_at=NOW - timedelta(minutes=1)))
    acknowledged = store.save(notification(2, created_at=NOW - timedelta(minutes=2)))
    store.acknowledge(acknowledged.id, at=NOW - timedelta(minutes=1))
    store.save(
        notification(
            3,
            created_at=NOW - timedelta(hours=2),
            expires_at=NOW - timedelta(hours=1),
        )
    )

    assert store.active(recipient_id="founder-1", now=NOW) == [active]


def test_delivery_attempts_and_receipts_are_durable_and_idempotent(tmp_path) -> None:
    path = tmp_path / "notifications.db"
    store = NotificationStore(path)
    notice = store.save(notification(1))
    attempt = DeliveryAttempt(
        notification_id=notice.id,
        channel=NotificationChannel.WEB_PUSH,
        attempt_number=1,
        idempotency_key="attempt-1",
        status=DeliveryStatus.SENT,
        attempted_at=NOW + timedelta(seconds=1),
        completed_at=NOW + timedelta(seconds=2),
    )
    receipt = DeliveryReceipt(
        attempt_id=attempt.id,
        idempotency_key="receipt-1",
        status=DeliveryStatus.DELIVERED,
        received_at=NOW + timedelta(seconds=3),
        provider_reference="local-receipt-1",
    )

    assert store.record_delivery_attempt(attempt) == attempt
    assert store.record_delivery_attempt(attempt) == attempt
    assert store.record_delivery_receipt(receipt) == receipt
    assert store.record_delivery_receipt(receipt) == receipt

    reopened = NotificationStore(path)
    assert reopened.delivery_attempts(notice.id) == [attempt]
    assert reopened.delivery_receipts(attempt.id) == [receipt]


def test_delivery_attempt_rejects_unrequested_channel(tmp_path) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    notice = store.save(notification(1))
    attempt = DeliveryAttempt(
        notification_id=notice.id,
        channel=NotificationChannel.EMAIL,
        attempt_number=1,
        idempotency_key="attempt-email",
        status=DeliveryStatus.QUEUED,
        attempted_at=NOW,
    )

    with pytest.raises(ValueError, match="not requested"):
        store.record_delivery_attempt(attempt)
