from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ExecutionState:
    enabled: bool
    reason: str | None
    updated_at: datetime
    updated_by: str


class ExecutionControlStore:
    """Durable operator kill switch shared by API and background workers."""

    def __init__(
        self,
        database_path: str | Path = "data/chief.db",
        *,
        initial_enabled: bool = True,
    ) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_control (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                    reason TEXT,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                )
                """
            )
            now = datetime.now(UTC).isoformat()
            connection.execute(
                """
                INSERT OR IGNORE INTO execution_control(
                    singleton, enabled, reason, updated_at, updated_by
                ) VALUES (1, ?, NULL, ?, 'configuration')
                """,
                (int(initial_enabled), now),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def get(self) -> ExecutionState:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT enabled, reason, updated_at, updated_by FROM execution_control WHERE singleton = 1"
            ).fetchone()
        if row is None:
            raise RuntimeError("execution control state is missing")
        return ExecutionState(
            enabled=bool(row["enabled"]),
            reason=str(row["reason"]) if row["reason"] is not None else None,
            updated_at=datetime.fromisoformat(str(row["updated_at"])).astimezone(UTC),
            updated_by=str(row["updated_by"]),
        )

    def set_enabled(
        self,
        enabled: bool,
        *,
        actor_id: str,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> ExecutionState:
        actor_id = actor_id.strip()
        if not actor_id:
            raise ValueError("actor_id must not be empty")
        if reason is not None:
            reason = reason.strip() or None
            if reason is not None and len(reason) > 2000:
                raise ValueError("execution-control reason cannot exceed 2000 characters")
        timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE execution_control
                SET enabled = ?, reason = ?, updated_at = ?, updated_by = ?
                WHERE singleton = 1
                """,
                (int(enabled), reason, timestamp, actor_id),
            )
        return self.get()
