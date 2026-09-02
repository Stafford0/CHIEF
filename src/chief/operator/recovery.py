from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from chief.core.execution_control import ExecutionControlStore
from chief.events.schema import Event, EventStatus
from chief.events.store import EventStore


class EventRecoveryAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    event_id: UUID
    actor_id: str
    action: str
    reason: str
    previous_status: EventStatus
    previous_attempts: int
    previous_error: str | None
    created_at: datetime


class OperatorStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    generated_at: datetime
    execution_enabled: bool
    execution_reason: str | None
    runtime_status: str
    runtime_reason: str | None
    runtime_last_tick_at: datetime | None
    event_counts: dict[str, int]
    dead_letters: int


class OperatorRecoveryService:
    """Explicit owner recovery surface for terminal event failures."""

    def __init__(self, database_path: str | Path = "data/chief.db") -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.events = EventStore(self.database_path)
        self.execution = ExecutionControlStore(self.database_path)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS event_recovery_actions (
                    id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    previous_status TEXT NOT NULL,
                    previous_attempts INTEGER NOT NULL,
                    previous_error TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_event_recovery_actions_event
                ON event_recovery_actions(event_id, created_at DESC)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @staticmethod
    def _validate_actor(actor_id: str) -> str:
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("actor_id must be a non-empty string")
        return actor_id.strip()

    @staticmethod
    def _validate_reason(reason: str) -> str:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("A recovery reason is required")
        reason = reason.strip()
        if len(reason) > 2_000:
            raise ValueError("Recovery reason cannot exceed 2,000 characters")
        return reason

    def status(self, *, now: datetime | None = None) -> OperatorStatus:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        execution = self.execution.get()
        runtime_status = "not_started"
        runtime_reason = None
        runtime_last_tick_at = None
        with self._connect() as connection:
            try:
                row = connection.execute(
                    "SELECT last_tick_at, last_status, last_reason FROM runtime_state WHERE singleton = 1"
                ).fetchone()
            except sqlite3.OperationalError:
                row = None
        if row is not None:
            runtime_status = str(row["last_status"] or "unknown")
            runtime_reason = str(row["last_reason"]) if row["last_reason"] else None
            if row["last_tick_at"]:
                runtime_last_tick_at = datetime.fromisoformat(str(row["last_tick_at"])).astimezone(UTC)
        counts = self.events.counts()
        return OperatorStatus(
            generated_at=current,
            execution_enabled=execution.enabled,
            execution_reason=execution.reason,
            runtime_status=runtime_status,
            runtime_reason=runtime_reason,
            runtime_last_tick_at=runtime_last_tick_at,
            event_counts=counts,
            dead_letters=counts.get(EventStatus.DEAD_LETTER.value, 0),
        )

    def list_dead_letters(self, *, limit: int = 100) -> list[Event]:
        if not 1 <= limit <= 1_000:
            raise ValueError("Dead-letter limit must be between 1 and 1000")
        return [
            event
            for event in self.events.list_events(limit=1_000)
            if event.status is EventStatus.DEAD_LETTER
        ][:limit]

    def _record_action(
        self,
        connection: sqlite3.Connection,
        *,
        event: Event,
        actor_id: str,
        action: str,
        reason: str,
        now: datetime,
    ) -> EventRecoveryAction:
        record = EventRecoveryAction(
            id=uuid4(),
            event_id=event.id,
            actor_id=actor_id,
            action=action,
            reason=reason,
            previous_status=event.status,
            previous_attempts=event.attempts,
            previous_error=event.last_error,
            created_at=now,
        )
        connection.execute(
            """
            INSERT INTO event_recovery_actions(
                id, event_id, actor_id, action, reason, previous_status,
                previous_attempts, previous_error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(record.id),
                str(record.event_id),
                record.actor_id,
                record.action,
                record.reason,
                record.previous_status.value,
                record.previous_attempts,
                record.previous_error,
                record.created_at.isoformat(),
            ),
        )
        return record

    def _dead_letter_row(self, connection: sqlite3.Connection, event_id: UUID):
        row = connection.execute(
            "SELECT * FROM chief_events WHERE id = ?",
            (str(event_id),),
        ).fetchone()
        if row is None:
            raise KeyError("Event does not exist")
        values = dict(row)
        values["payload"] = json.loads(values.pop("payload_json"))
        event = Event.model_validate(values)
        if event.status is not EventStatus.DEAD_LETTER:
            raise ValueError("Only dead-letter events can be recovered")
        return event

    def retry(
        self,
        event_id: UUID,
        *,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> tuple[Event, EventRecoveryAction]:
        actor_id = self._validate_actor(actor_id)
        reason = self._validate_reason(reason)
        current = (now or datetime.now(UTC)).astimezone(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            event = self._dead_letter_row(connection, event_id)
            action = self._record_action(
                connection,
                event=event,
                actor_id=actor_id,
                action="retry",
                reason=reason,
                now=current,
            )
            connection.execute(
                """
                UPDATE chief_events
                SET status=?, attempts=0, available_at=?, completed_at=NULL,
                    lease_owner=NULL, lease_until=NULL, last_error=NULL, updated_at=?
                WHERE id=?
                """,
                (
                    EventStatus.PENDING.value,
                    current.isoformat(),
                    current.isoformat(),
                    str(event_id),
                ),
            )
        retried = next(item for item in self.events.list_events(limit=1_000) if item.id == event_id)
        return retried, action

    def dismiss(
        self,
        event_id: UUID,
        *,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> tuple[Event, EventRecoveryAction]:
        actor_id = self._validate_actor(actor_id)
        reason = self._validate_reason(reason)
        current = (now or datetime.now(UTC)).astimezone(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            event = self._dead_letter_row(connection, event_id)
            action = self._record_action(
                connection,
                event=event,
                actor_id=actor_id,
                action="dismiss",
                reason=reason,
                now=current,
            )
            prior_error = event.last_error or "no previous error"
            error = f"Operator dismissed: {reason}; previous error: {prior_error}"[:20_000]
            connection.execute(
                """
                UPDATE chief_events
                SET status=?, completed_at=?, lease_owner=NULL, lease_until=NULL,
                    last_error=?, updated_at=? WHERE id=?
                """,
                (
                    EventStatus.FAILED.value,
                    current.isoformat(),
                    error,
                    current.isoformat(),
                    str(event_id),
                ),
            )
        dismissed = next(item for item in self.events.list_events(limit=1_000) if item.id == event_id)
        return dismissed, action

    def history(self, *, event_id: UUID | None = None, limit: int = 100) -> list[EventRecoveryAction]:
        if not 1 <= limit <= 1_000:
            raise ValueError("Recovery history limit must be between 1 and 1000")
        query = "SELECT * FROM event_recovery_actions"
        parameters: list[object] = []
        if event_id is not None:
            query += " WHERE event_id = ?"
            parameters.append(str(event_id))
        query += " ORDER BY created_at DESC LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            EventRecoveryAction(
                id=row["id"],
                event_id=row["event_id"],
                actor_id=row["actor_id"],
                action=row["action"],
                reason=row["reason"],
                previous_status=row["previous_status"],
                previous_attempts=row["previous_attempts"],
                previous_error=row["previous_error"],
                created_at=row["created_at"],
            )
            for row in rows
        ]
