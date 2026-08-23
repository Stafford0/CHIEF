"""SQLite persistence for decision records."""

import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from chief.decisions.schema import DecisionRecord, DecisionStatus


class DecisionStore(ABC):
    @abstractmethod
    def save(self, decision: DecisionRecord) -> DecisionRecord:
        """Create or replace a decision record."""

    @abstractmethod
    def get(self, decision_id: UUID) -> DecisionRecord | None:
        """Retrieve a decision by ID."""

    @abstractmethod
    def list(
        self,
        *,
        status: DecisionStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DecisionRecord]:
        """List decisions from most recently updated to least recent."""

    @abstractmethod
    def delete(self, decision_id: UUID) -> bool:
        """Permanently remove a decision."""


class SQLiteDecisionStore(DecisionStore):
    """Durable decision storage with indexed status and update time."""

    def __init__(
        self,
        database_path: str | Path = "data/chief.db",
        *,
        max_payload_bytes: int = 1_000_000,
    ) -> None:
        if max_payload_bytes < 1_024:
            raise ValueError("Decision payload limit must be at least 1024 bytes.")
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_payload_bytes = max_payload_bytes
        self._initialize_database()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = FULL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chief_component_migrations (
                    component TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    applied_at TEXT NOT NULL,
                    PRIMARY KEY (component, version)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO chief_component_migrations(component, version, applied_at)
                VALUES ('decisions', 1, ?)
                """,
                (datetime.now(UTC).isoformat(),),
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_decisions_status_updated
                ON decisions (status, updated_at DESC)
                """
            )

    def save(self, decision: DecisionRecord) -> DecisionRecord:
        if not isinstance(decision, DecisionRecord):
            raise TypeError("decision must be a DecisionRecord.")

        payload = decision.model_dump_json()
        if len(payload.encode("utf-8")) > self.max_payload_bytes:
            raise ValueError("Decision payload exceeds the configured size limit.")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO decisions (id, title, status, created_at, updated_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    status = excluded.status,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json
                """,
                (
                    str(decision.id),
                    decision.title,
                    decision.status.value,
                    decision.created_at.isoformat(),
                    decision.updated_at.isoformat(),
                    payload,
                ),
            )
        return decision

    def get(self, decision_id: UUID) -> DecisionRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM decisions WHERE id = ?",
                (str(decision_id),),
            ).fetchone()
        if row is None:
            return None
        return DecisionRecord.model_validate_json(row["payload_json"])

    def list(
        self,
        *,
        status: DecisionStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DecisionRecord]:
        if not 1 <= limit <= 1_000:
            raise ValueError("Decision list limit must be between 1 and 1000.")
        if offset < 0:
            raise ValueError("Decision list offset cannot be negative.")

        query = "SELECT payload_json FROM decisions"
        parameters: list[str | int] = []
        if status is not None:
            query += " WHERE status = ?"
            parameters.append(status.value)
        query += " ORDER BY updated_at DESC, created_at DESC, id ASC LIMIT ? OFFSET ?"
        parameters.extend((limit, offset))

        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [DecisionRecord.model_validate_json(row["payload_json"]) for row in rows]

    def delete(self, decision_id: UUID) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM decisions WHERE id = ?",
                (str(decision_id),),
            )
        return cursor.rowcount > 0
