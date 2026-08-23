from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from chief.events.schema import Event, EventStatus, Schedule, ScheduleStatus


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class EventStore:
    """SQLite queue with durable deduplication and atomic leases."""

    def __init__(self, database_path: str | Path = "data/chief.db") -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schedules (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL, cadence TEXT NOT NULL, timezone TEXT NOT NULL,
                    run_at TEXT, interval_seconds INTEGER, daily_time TEXT, next_run_at TEXT,
                    status TEXT NOT NULL, last_run_at TEXT, last_success_at TEXT,
                    consecutive_failures INTEGER NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, lease_owner TEXT, lease_until TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_schedules_due
                    ON schedules(status, next_run_at, lease_until);
                CREATE TABLE IF NOT EXISTS chief_events (
                    id TEXT PRIMARY KEY, event_type TEXT NOT NULL, source TEXT NOT NULL,
                    payload_json TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
                    correlation_id TEXT, status TEXT NOT NULL, attempts INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL, available_at TEXT NOT NULL,
                    observed_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    completed_at TEXT, lease_owner TEXT, lease_until TEXT, last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_chief_events_claim
                    ON chief_events(status, available_at, lease_until);
                """
            )

    def save_schedule(self, schedule: Schedule) -> Schedule:
        schedule.updated_at = datetime.now(UTC)
        data = schedule.model_dump(mode="json")
        keys = (
            "id",
            "name",
            "event_type",
            "cadence",
            "timezone",
            "run_at",
            "interval_seconds",
            "daily_time",
            "next_run_at",
            "status",
            "last_run_at",
            "last_success_at",
            "consecutive_failures",
            "created_at",
            "updated_at",
            "lease_owner",
            "lease_until",
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO schedules (
                    id, name, event_type, payload_json, cadence, timezone, run_at,
                    interval_seconds, daily_time, next_run_at, status, last_run_at,
                    last_success_at, consecutive_failures, created_at, updated_at,
                    lease_owner, lease_until
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, event_type=excluded.event_type,
                    payload_json=excluded.payload_json, cadence=excluded.cadence,
                    timezone=excluded.timezone, run_at=excluded.run_at,
                    interval_seconds=excluded.interval_seconds, daily_time=excluded.daily_time,
                    next_run_at=excluded.next_run_at, status=excluded.status,
                    last_run_at=excluded.last_run_at, last_success_at=excluded.last_success_at,
                    consecutive_failures=excluded.consecutive_failures,
                    updated_at=excluded.updated_at, lease_owner=excluded.lease_owner,
                    lease_until=excluded.lease_until
                """,
                (
                    data["id"],
                    data["name"],
                    data["event_type"],
                    json.dumps(data["payload"], sort_keys=True),
                    *(data[key] for key in keys[3:]),
                ),
            )
        return schedule

    def get_schedule(self, schedule_id: UUID) -> Schedule | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM schedules WHERE id = ?", (str(schedule_id),)
            ).fetchone()
        return self._schedule(row) if row else None

    def list_schedules(self, *, include_inactive: bool = False) -> list[Schedule]:
        query = "SELECT * FROM schedules"
        parameters: tuple[object, ...] = ()
        if not include_inactive:
            query += " WHERE status = ?"
            parameters = (ScheduleStatus.ACTIVE.value,)
        query += " ORDER BY next_run_at IS NULL, next_run_at, name"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._schedule(row) for row in rows]

    def claim_due_schedule(
        self,
        worker_id: str,
        *,
        now: datetime | None = None,
        lease_seconds: int = 30,
    ) -> Schedule | None:
        now = now or datetime.now(UTC)
        lease_until = now + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM schedules
                WHERE status = ? AND next_run_at IS NOT NULL AND next_run_at <= ?
                  AND (lease_until IS NULL OR lease_until <= ?)
                ORDER BY next_run_at, created_at LIMIT 1
                """,
                (ScheduleStatus.ACTIVE.value, _timestamp(now), _timestamp(now)),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE schedules SET lease_owner=?, lease_until=?, updated_at=? WHERE id=?",
                (worker_id, _timestamp(lease_until), _timestamp(now), row["id"]),
            )
            row = connection.execute(
                "SELECT * FROM schedules WHERE id = ?", (row["id"],)
            ).fetchone()
        return self._schedule(row)

    def release_schedule(
        self,
        schedule: Schedule,
        worker_id: str,
        *,
        success: bool,
    ) -> Schedule:
        current = self.get_schedule(schedule.id)
        if current is None:
            raise KeyError("Schedule does not exist.")
        if current.lease_owner != worker_id:
            raise PermissionError("Schedule lease is not owned by this worker.")
        schedule.lease_owner = None
        schedule.lease_until = None
        schedule.last_run_at = datetime.now(UTC)
        if success:
            schedule.last_success_at = schedule.last_run_at
            schedule.consecutive_failures = 0
        else:
            schedule.consecutive_failures += 1
        return self.save_schedule(schedule)

    def enqueue(self, event: Event) -> Event:
        data = event.model_dump(mode="json")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO chief_events (
                    id, event_type, source, payload_json, idempotency_key, correlation_id,
                    status, attempts, max_attempts, available_at, observed_at, created_at,
                    updated_at, completed_at, lease_owner, lease_until, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO NOTHING
                """,
                (
                    data["id"],
                    data["event_type"],
                    data["source"],
                    json.dumps(data["payload"], sort_keys=True),
                    data["idempotency_key"],
                    data["correlation_id"],
                    data["status"],
                    data["attempts"],
                    data["max_attempts"],
                    data["available_at"],
                    data["observed_at"],
                    data["created_at"],
                    data["updated_at"],
                    data["completed_at"],
                    data["lease_owner"],
                    data["lease_until"],
                    data["last_error"],
                ),
            )
            row = connection.execute(
                "SELECT * FROM chief_events WHERE idempotency_key = ?",
                (event.idempotency_key,),
            ).fetchone()
        assert row is not None
        return self._event(row)

    def claim_event(
        self,
        worker_id: str,
        *,
        now: datetime | None = None,
        lease_seconds: int = 60,
    ) -> Event | None:
        now = now or datetime.now(UTC)
        lease_until = now + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM chief_events
                WHERE status IN (?, ?) AND available_at <= ?
                  AND (lease_until IS NULL OR lease_until <= ?)
                ORDER BY available_at, created_at LIMIT 1
                """,
                (
                    EventStatus.PENDING.value,
                    EventStatus.PROCESSING.value,
                    _timestamp(now),
                    _timestamp(now),
                ),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE chief_events SET status=?, attempts=attempts+1, lease_owner=?,
                    lease_until=?, updated_at=? WHERE id=?
                """,
                (
                    EventStatus.PROCESSING.value,
                    worker_id,
                    _timestamp(lease_until),
                    _timestamp(now),
                    row["id"],
                ),
            )
            row = connection.execute(
                "SELECT * FROM chief_events WHERE id = ?", (row["id"],)
            ).fetchone()
        return self._event(row)

    def complete_event(
        self,
        event_id: UUID,
        worker_id: str,
        *,
        success: bool,
        error: str | None = None,
        retry_delay_seconds: int = 30,
    ) -> Event:
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM chief_events WHERE id = ?", (str(event_id),)
            ).fetchone()
            if row is None:
                raise KeyError("Event does not exist.")
            if row["lease_owner"] != worker_id or row["status"] != EventStatus.PROCESSING.value:
                raise PermissionError("Event lease is not owned by this worker.")
            if success:
                status = EventStatus.COMPLETED.value
                available_at = row["available_at"]
                completed_at = _timestamp(now)
            elif int(row["attempts"]) >= int(row["max_attempts"]):
                status = EventStatus.DEAD_LETTER.value
                available_at = row["available_at"]
                completed_at = _timestamp(now)
            else:
                status = EventStatus.PENDING.value
                available_at = _timestamp(now + timedelta(seconds=retry_delay_seconds))
                completed_at = None
            connection.execute(
                """
                UPDATE chief_events SET status=?, available_at=?, completed_at=?,
                    lease_owner=NULL, lease_until=NULL, last_error=?, updated_at=? WHERE id=?
                """,
                (status, available_at, completed_at, error, _timestamp(now), str(event_id)),
            )
            row = connection.execute(
                "SELECT * FROM chief_events WHERE id = ?", (str(event_id),)
            ).fetchone()
        return self._event(row)

    def counts(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM chief_events GROUP BY status"
            ).fetchall()
        return {row["status"]: int(row["count"]) for row in rows}

    def list_events(self, *, limit: int = 100) -> list[Event]:
        if not 1 <= limit <= 1_000:
            raise ValueError("Event limit must be between 1 and 1000.")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM chief_events ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._event(row) for row in rows]

    @staticmethod
    def _schedule(row: sqlite3.Row) -> Schedule:
        values = dict(row)
        values["payload"] = json.loads(values.pop("payload_json"))
        return Schedule.model_validate(values)

    @staticmethod
    def _event(row: sqlite3.Row) -> Event:
        values = dict(row)
        values["payload"] = json.loads(values.pop("payload_json"))
        return Event.model_validate(values)
