from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from chief.core.execution_control import ExecutionControlStore
from chief.events.scheduler import Scheduler
from chief.events.store import EventStore
from chief.notifications.delivery import NotificationDispatcher, ProviderDelivery
from chief.notifications.pump import NotificationPump
from chief.notifications.schema import (
    AttentionAction,
    AttentionDecision,
    AttentionReason,
    Notification,
    NotificationChannel,
    NotificationPriority,
)
from chief.notifications.store import NotificationStore
from chief.runs import RunEngine, SQLiteRunStore
from chief.runtime.supervisor import RuntimeStateStore, RuntimeSupervisor

NOW = datetime(2026, 9, 2, 2, 30, tzinfo=UTC)


class RecordingProvider:
    channel = NotificationChannel.EMAIL

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[str] = []

    def send(self, notification: Notification) -> ProviderDelivery:
        self.sent.append(str(notification.id))
        if self.fail:
            raise RuntimeError("smtp unavailable")
        return ProviderDelivery(provider_reference=f"message:{notification.id}")


def interrupt_notification(store: NotificationStore, *, key: str = "notice") -> Notification:
    notification = store.save(
        Notification(
            recipient_id="owner",
            source="runtime-test",
            title="Needs attention",
            body="A verified condition needs attention.",
            priority=NotificationPriority.HIGH,
            channels=(NotificationChannel.EMAIL,),
            idempotency_key=key,
            dedup_key=key,
            created_at=NOW,
            expires_at=NOW + timedelta(hours=1),
        )
    )
    store.record_decision(
        AttentionDecision(
            notification_id=notification.id,
            recipient_id="owner",
            action=AttentionAction.INTERRUPT,
            reason=AttentionReason.INTERRUPT_ALLOWED,
            decided_at=NOW,
        )
    )
    return notification


def test_pump_delivers_interrupt_once_and_never_redelivers(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "chief.db")
    notification = interrupt_notification(store)
    provider = RecordingProvider()
    pump = NotificationPump(store, NotificationDispatcher(store, [provider]))

    first = pump.pump_once(now=NOW)
    second = pump.pump_once(now=NOW + timedelta(seconds=1))

    assert first.delivered == 1
    assert first.failed == 0
    assert second.delivered == 0
    assert second.skipped == 1
    assert provider.sent == [str(notification.id)]


def test_pump_backs_off_failed_delivery_before_retry(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "chief.db")
    notification = interrupt_notification(store)
    provider = RecordingProvider(fail=True)
    pump = NotificationPump(
        store,
        NotificationDispatcher(store, [provider]),
        failure_backoff_seconds=300,
    )

    failed = pump.pump_once(now=NOW)
    backed_off = pump.pump_once(now=NOW + timedelta(seconds=299))
    retried = pump.pump_once(now=NOW + timedelta(seconds=301))

    assert failed.failed == 1
    assert backed_off.skipped == 1
    assert retried.failed == 1
    assert provider.sent == [str(notification.id), str(notification.id)]


def test_pump_skips_digest_decisions_and_unsupported_channels(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "chief.db")
    digest = store.save(
        Notification(
            recipient_id="owner",
            source="runtime-test",
            title="Digest",
            body="Later",
            priority=NotificationPriority.NORMAL,
            channels=(NotificationChannel.EMAIL,),
            idempotency_key="digest",
            dedup_key="digest",
            created_at=NOW,
        )
    )
    store.record_decision(
        AttentionDecision(
            notification_id=digest.id,
            recipient_id="owner",
            action=AttentionAction.DIGEST,
            reason=AttentionReason.BELOW_PRIORITY_THRESHOLD,
            decided_at=NOW,
        )
    )
    interrupt_notification(store, key="interrupt-no-provider")
    pump = NotificationPump(store, NotificationDispatcher(store, []))

    result = pump.pump_once(now=NOW)

    assert result.selected == 1
    assert result.delivered == 0
    assert result.skipped == 1


def test_runtime_pause_stops_notification_pump(tmp_path: Path) -> None:
    database = tmp_path / "chief.db"
    event_store = EventStore(database)
    run_store = SQLiteRunStore(database)
    notification_store = NotificationStore(database)
    interrupt_notification(notification_store)
    provider = RecordingProvider()
    execution_control = ExecutionControlStore(database)
    execution_control.set_enabled(False, actor_id="owner", reason="emergency stop", now=NOW)
    supervisor = RuntimeSupervisor(
        event_store=event_store,
        scheduler=Scheduler(event_store),
        run_store=run_store,
        run_engine=RunEngine(run_store, {}),
        state_store=RuntimeStateStore(database),
        execution_control=execution_control,
        notification_pump=NotificationPump(
            notification_store,
            NotificationDispatcher(notification_store, [provider]),
        ),
        min_free_disk_bytes=0,
    )

    tick = supervisor.tick_once(now=NOW)

    assert tick.status == "paused"
    assert tick.notification_deliveries == 0
    assert provider.sent == []


def test_runtime_reports_notification_failure_as_degraded(tmp_path: Path) -> None:
    database = tmp_path / "chief.db"
    event_store = EventStore(database)
    run_store = SQLiteRunStore(database)
    notification_store = NotificationStore(database)
    interrupt_notification(notification_store)
    provider = RecordingProvider(fail=True)
    supervisor = RuntimeSupervisor(
        event_store=event_store,
        scheduler=Scheduler(event_store),
        run_store=run_store,
        run_engine=RunEngine(run_store, {}),
        state_store=RuntimeStateStore(database),
        notification_pump=NotificationPump(
            notification_store,
            NotificationDispatcher(notification_store, [provider]),
        ),
        min_free_disk_bytes=0,
    )

    tick = supervisor.tick_once(now=NOW)

    assert tick.status == "degraded"
    assert tick.notification_failures == 1
    assert "notification delivery" in (tick.reason or "")
