from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chief.audit.log import AuditEvent

_GENESIS_HASH = "0" * 64
_HASH_HEX_LENGTH = 64


@dataclass(frozen=True)
class AuditIntegrityResult:
    """Result of verifying the persisted audit sequence and hash chain."""

    valid: bool
    checked_events: int
    first_sequence: int | None = None
    last_sequence: int | None = None
    error_sequence: int | None = None
    reason: str | None = None


class SQLiteAuditLog:
    """Append-only SQLite audit storage with a SHA-256 hash chain.

    The chain detects accidental corruption and unsophisticated edits. It is not
    proof against an attacker who can modify the database and recompute every
    later hash. A future deployment can add an HMAC or externally anchored hash
    without changing the event payload or pagination contract.
    """

    def __init__(
        self,
        database_path: str | Path = "data/chief.db",
        *,
        max_page_size: int = 1_000,
        busy_timeout_ms: int = 5_000,
        max_metadata_bytes: int = 262_144,
    ) -> None:
        if max_page_size < 1:
            raise ValueError("Audit maximum page size must be positive.")
        if busy_timeout_ms < 1:
            raise ValueError("Audit busy timeout must be positive.")
        if max_metadata_bytes < 2:
            raise ValueError("Audit metadata limit is too small.")

        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_page_size = max_page_size
        self.busy_timeout_ms = busy_timeout_ms
        self.max_metadata_bytes = max_metadata_bytes
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_ms / 1_000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    timestamp TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    approved INTEGER NOT NULL CHECK (approved IN (0, 1)),
                    decision TEXT NOT NULL,
                    success INTEGER NOT NULL CHECK (success IN (0, 1)),
                    error TEXT,
                    metadata_json TEXT NOT NULL,
                    request_id TEXT,
                    actor_id TEXT,
                    session_id TEXT,
                    run_id TEXT,
                    step_id TEXT,
                    proposal_id TEXT,
                    previous_hash TEXT NOT NULL CHECK (length(previous_hash) = 64),
                    event_hash TEXT NOT NULL UNIQUE CHECK (length(event_hash) = 64)
                );

                CREATE INDEX IF NOT EXISTS ix_audit_events_timestamp
                    ON audit_events(timestamp);
                CREATE INDEX IF NOT EXISTS ix_audit_events_request
                    ON audit_events(request_id) WHERE request_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS ix_audit_events_actor
                    ON audit_events(actor_id) WHERE actor_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS ix_audit_events_session
                    ON audit_events(session_id) WHERE session_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS ix_audit_events_run_step
                    ON audit_events(run_id, step_id) WHERE run_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS ix_audit_events_proposal
                    ON audit_events(proposal_id) WHERE proposal_id IS NOT NULL;

                CREATE TRIGGER IF NOT EXISTS audit_events_no_update
                BEFORE UPDATE ON audit_events
                BEGIN
                    SELECT RAISE(ABORT, 'audit_events are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
                BEFORE DELETE ON audit_events
                BEGIN
                    SELECT RAISE(ABORT, 'audit_events are append-only');
                END;
                """
            )

    @staticmethod
    def _timestamp(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()

    @staticmethod
    def _validate_identifier(name: str, value: str | None) -> None:
        if value is None:
            return
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Audit {name} must be a non-empty string when supplied.")
        if len(value) > 256:
            raise ValueError(f"Audit {name} cannot exceed 256 characters.")

    def _metadata_json(self, metadata: dict[str, Any]) -> str:
        if not isinstance(metadata, dict):
            raise TypeError("Audit metadata must be a dictionary.")
        encoded = json.dumps(
            metadata,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
        if len(encoded.encode("utf-8")) > self.max_metadata_bytes:
            raise ValueError("Audit metadata exceeds the configured size limit.")
        return encoded

    @staticmethod
    def _payload(
        *,
        sequence: int,
        event_id: str,
        timestamp: str,
        tool_name: str,
        approved: bool,
        decision: str,
        success: bool,
        error: str | None,
        metadata_json: str,
        request_id: str | None,
        actor_id: str | None,
        session_id: str | None,
        run_id: str | None,
        step_id: str | None,
        proposal_id: str | None,
    ) -> bytes:
        payload = {
            "sequence": sequence,
            "event_id": event_id,
            "timestamp": timestamp,
            "tool_name": tool_name,
            "approved": approved,
            "decision": decision,
            "success": success,
            "error": error,
            "metadata_json": metadata_json,
            "request_id": request_id,
            "actor_id": actor_id,
            "session_id": session_id,
            "run_id": run_id,
            "step_id": step_id,
            "proposal_id": proposal_id,
        }
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    @classmethod
    def _event_hash(cls, previous_hash: str, payload: bytes) -> str:
        if len(previous_hash) != _HASH_HEX_LENGTH:
            raise ValueError("Previous audit hash has an invalid length.")
        try:
            previous_bytes = bytes.fromhex(previous_hash)
        except ValueError as exc:
            raise ValueError("Previous audit hash is not hexadecimal.") from exc
        return hashlib.sha256(previous_bytes + payload).hexdigest()

    def record(self, event: AuditEvent) -> None:
        """Persist one event after serially extending the chain."""

        for name in (
            "event_id",
            "request_id",
            "actor_id",
            "session_id",
            "run_id",
            "step_id",
            "proposal_id",
        ):
            self._validate_identifier(name, getattr(event, name))
        self._validate_identifier("tool_name", event.tool_name)
        self._validate_identifier("decision", event.decision)
        if event.error is not None and len(event.error) > 20_000:
            raise ValueError("Audit error cannot exceed 20,000 characters.")

        timestamp = self._timestamp(event.timestamp)
        metadata_json = self._metadata_json(event.metadata)

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT sequence, event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = int(previous["sequence"]) + 1 if previous is not None else 1
            previous_hash = str(previous["event_hash"]) if previous is not None else _GENESIS_HASH
            payload = self._payload(
                sequence=sequence,
                event_id=event.event_id,
                timestamp=timestamp,
                tool_name=event.tool_name,
                approved=event.approved,
                decision=event.decision,
                success=event.success,
                error=event.error,
                metadata_json=metadata_json,
                request_id=event.request_id,
                actor_id=event.actor_id,
                session_id=event.session_id,
                run_id=event.run_id,
                step_id=event.step_id,
                proposal_id=event.proposal_id,
            )
            event_hash = self._event_hash(previous_hash, payload)
            connection.execute(
                """
                INSERT INTO audit_events (
                    sequence, event_id, timestamp, tool_name, approved, decision,
                    success, error, metadata_json, request_id, actor_id, session_id,
                    run_id, step_id, proposal_id, previous_hash, event_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sequence,
                    event.event_id,
                    timestamp,
                    event.tool_name,
                    int(event.approved),
                    event.decision,
                    int(event.success),
                    event.error,
                    metadata_json,
                    event.request_id,
                    event.actor_id,
                    event.session_id,
                    event.run_id,
                    event.step_id,
                    event.proposal_id,
                    previous_hash,
                    event_hash,
                ),
            )

    def events(
        self,
        *,
        limit: int = 100,
        before_sequence: int | None = None,
        after_sequence: int | None = None,
    ) -> list[AuditEvent]:
        """Return a bounded chronological page, using exclusive sequence cursors."""

        if not 1 <= limit <= self.max_page_size:
            raise ValueError(f"Audit page limit must be between 1 and {self.max_page_size}.")
        if before_sequence is not None and after_sequence is not None:
            raise ValueError("Use either before_sequence or after_sequence, not both.")
        if before_sequence is not None and before_sequence < 1:
            raise ValueError("before_sequence must be positive.")
        if after_sequence is not None and after_sequence < 0:
            raise ValueError("after_sequence cannot be negative.")

        parameters: list[object] = []
        if after_sequence is not None:
            query = "SELECT * FROM audit_events WHERE sequence > ? ORDER BY sequence ASC LIMIT ?"
            parameters.extend((after_sequence, limit))
            reverse = False
        elif before_sequence is not None:
            query = "SELECT * FROM audit_events WHERE sequence < ? ORDER BY sequence DESC LIMIT ?"
            parameters.extend((before_sequence, limit))
            reverse = True
        else:
            query = "SELECT * FROM audit_events ORDER BY sequence DESC LIMIT ?"
            parameters.append(limit)
            reverse = True

        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        if reverse:
            rows.reverse()
        return [self._row_to_event(row) for row in rows]

    def latest(self) -> AuditEvent | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM audit_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
        return self._row_to_event(row) if row is not None else None

    def count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM audit_events").fetchone()
        assert row is not None
        return int(row["count"])

    def verify_integrity(self) -> AuditIntegrityResult:
        """Stream through the full log and verify sequence and hash continuity."""

        expected_sequence = 1
        expected_previous_hash = _GENESIS_HASH
        checked = 0
        first_sequence: int | None = None
        last_sequence: int | None = None

        with self._connect() as connection:
            cursor = connection.execute("SELECT * FROM audit_events ORDER BY sequence ASC")
            while True:
                rows = cursor.fetchmany(500)
                if not rows:
                    break
                for row in rows:
                    sequence = int(row["sequence"])
                    if first_sequence is None:
                        first_sequence = sequence
                    if sequence != expected_sequence:
                        return AuditIntegrityResult(
                            valid=False,
                            checked_events=checked,
                            first_sequence=first_sequence,
                            last_sequence=last_sequence,
                            error_sequence=sequence,
                            reason=(
                                f"Expected audit sequence {expected_sequence}, found {sequence}."
                            ),
                        )
                    if row["previous_hash"] != expected_previous_hash:
                        return AuditIntegrityResult(
                            valid=False,
                            checked_events=checked,
                            first_sequence=first_sequence,
                            last_sequence=last_sequence,
                            error_sequence=sequence,
                            reason="Audit previous-hash link does not match.",
                        )

                    payload = self._payload(
                        sequence=sequence,
                        event_id=row["event_id"],
                        timestamp=row["timestamp"],
                        tool_name=row["tool_name"],
                        approved=bool(row["approved"]),
                        decision=row["decision"],
                        success=bool(row["success"]),
                        error=row["error"],
                        metadata_json=row["metadata_json"],
                        request_id=row["request_id"],
                        actor_id=row["actor_id"],
                        session_id=row["session_id"],
                        run_id=row["run_id"],
                        step_id=row["step_id"],
                        proposal_id=row["proposal_id"],
                    )
                    calculated_hash = self._event_hash(expected_previous_hash, payload)
                    if row["event_hash"] != calculated_hash:
                        return AuditIntegrityResult(
                            valid=False,
                            checked_events=checked,
                            first_sequence=first_sequence,
                            last_sequence=last_sequence,
                            error_sequence=sequence,
                            reason="Audit event hash does not match its stored payload.",
                        )

                    checked += 1
                    last_sequence = sequence
                    expected_sequence += 1
                    expected_previous_hash = row["event_hash"]

        return AuditIntegrityResult(
            valid=True,
            checked_events=checked,
            first_sequence=first_sequence,
            last_sequence=last_sequence,
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> AuditEvent:
        return AuditEvent(
            tool_name=row["tool_name"],
            approved=bool(row["approved"]),
            decision=row["decision"],
            success=bool(row["success"]),
            timestamp=datetime.fromisoformat(row["timestamp"]),
            error=row["error"],
            metadata=json.loads(row["metadata_json"]),
            event_id=row["event_id"],
            request_id=row["request_id"],
            actor_id=row["actor_id"],
            session_id=row["session_id"],
            run_id=row["run_id"],
            step_id=row["step_id"],
            proposal_id=row["proposal_id"],
            sequence=int(row["sequence"]),
            previous_hash=row["previous_hash"],
            event_hash=row["event_hash"],
        )
