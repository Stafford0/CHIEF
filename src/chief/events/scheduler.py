from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from chief.events.schema import Event, Schedule, ScheduleCadence, ScheduleStatus
from chief.events.store import EventStore


def next_occurrence(schedule: Schedule, *, after: datetime) -> datetime | None:
    """Calculate the next UTC occurrence, skipping a burst of missed intervals."""
    if schedule.cadence == ScheduleCadence.ONCE:
        return schedule.run_at if schedule.last_run_at is None else None
    if schedule.cadence == ScheduleCadence.INTERVAL:
        assert schedule.interval_seconds is not None
        candidate = schedule.next_run_at or after
        while candidate <= after:
            candidate += timedelta(seconds=schedule.interval_seconds)
        return candidate
    assert schedule.daily_time is not None
    zone = ZoneInfo(schedule.timezone)
    local_after = after.astimezone(zone)
    candidate = datetime.combine(local_after.date(), schedule.daily_time, tzinfo=zone)
    if candidate <= local_after:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


class Scheduler:
    """Turns due schedules into exactly-once queued events under a worker lease."""

    def __init__(self, store: EventStore) -> None:
        self.store = store

    def add(self, schedule: Schedule, *, now: datetime | None = None) -> Schedule:
        now = now or datetime.now(UTC)
        schedule.next_run_at = schedule.next_run_at or next_occurrence(schedule, after=now)
        return self.store.save_schedule(schedule)

    def tick(self, worker_id: str, *, now: datetime | None = None) -> Event | None:
        now = now or datetime.now(UTC)
        schedule = self.store.claim_due_schedule(worker_id, now=now)
        if schedule is None or schedule.next_run_at is None:
            return None
        occurrence = schedule.next_run_at
        event = self.store.enqueue(
            Event(
                event_type=schedule.event_type,
                source="schedule",
                payload={**schedule.payload, "schedule_id": str(schedule.id)},
                idempotency_key=f"schedule:{schedule.id}:{occurrence.isoformat()}",
                correlation_id=str(schedule.id),
                observed_at=occurrence,
                available_at=now,
            )
        )
        schedule.last_run_at = now
        schedule.next_run_at = next_occurrence(schedule, after=now)
        if schedule.next_run_at is None:
            schedule.status = ScheduleStatus.COMPLETED
        self.store.release_schedule(schedule, worker_id, success=True)
        return event
