from datetime import UTC, datetime, time, timedelta

import pytest

from chief.events.scheduler import Scheduler
from chief.events.schema import Event, EventStatus, Schedule, ScheduleCadence, ScheduleStatus
from chief.events.store import EventStore


def test_once_schedule_survives_restart_and_enqueues_once(tmp_path) -> None:
    path = tmp_path / "events.db"
    now = datetime.now(UTC)
    store = EventStore(path)
    schedule = Scheduler(store).add(
        Schedule(
            name="Morning briefing",
            event_type="briefing.daily",
            cadence=ScheduleCadence.ONCE,
            run_at=now - timedelta(seconds=1),
        ),
        now=now - timedelta(seconds=2),
    )

    restarted = EventStore(path)
    event = Scheduler(restarted).tick("worker-1", now=now)

    assert event is not None
    assert event.event_type == "briefing.daily"
    assert restarted.get_schedule(schedule.id).status == ScheduleStatus.COMPLETED
    assert Scheduler(restarted).tick("worker-1", now=now) is None


def test_event_idempotency_returns_original(tmp_path) -> None:
    store = EventStore(tmp_path / "events.db")
    first = store.enqueue(Event(event_type="signal", idempotency_key="same"))
    second = store.enqueue(Event(event_type="signal", idempotency_key="same"))

    assert second.id == first.id
    assert store.counts() == {"pending": 1}


def test_event_claim_is_leased_and_completed(tmp_path) -> None:
    store = EventStore(tmp_path / "events.db")
    original = store.enqueue(Event(event_type="signal", idempotency_key="claim"))
    claimed = store.claim_event("worker-1")

    assert claimed is not None
    assert claimed.id == original.id
    assert claimed.status == EventStatus.PROCESSING
    assert claimed.attempts == 1
    assert store.claim_event("worker-2") is None

    completed = store.complete_event(claimed.id, "worker-1", success=True)
    assert completed.status == EventStatus.COMPLETED
    with pytest.raises(PermissionError):
        store.complete_event(claimed.id, "worker-2", success=True)


def test_failed_event_retries_then_dead_letters(tmp_path) -> None:
    store = EventStore(tmp_path / "events.db")
    event = store.enqueue(Event(event_type="watch", idempotency_key="retry", max_attempts=2))

    first = store.claim_event("worker", now=event.available_at)
    assert first is not None
    pending = store.complete_event(
        first.id, "worker", success=False, error="offline", retry_delay_seconds=0
    )
    assert pending.status == EventStatus.PENDING

    second = store.claim_event("worker", now=datetime.now(UTC) + timedelta(seconds=1))
    assert second is not None
    dead = store.complete_event(second.id, "worker", success=False, error="still offline")
    assert dead.status == EventStatus.DEAD_LETTER


def test_daily_schedule_respects_timezone(tmp_path) -> None:
    store = EventStore(tmp_path / "events.db")
    now = datetime(2026, 8, 23, 15, 0, tzinfo=UTC)
    schedule = Scheduler(store).add(
        Schedule(
            name="Chicago briefing",
            event_type="briefing.daily",
            cadence=ScheduleCadence.DAILY,
            timezone="America/Chicago",
            daily_time=time(9, 0),
        ),
        now=now,
    )

    assert schedule.next_run_at == datetime(2026, 8, 24, 14, 0, tzinfo=UTC)
