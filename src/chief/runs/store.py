from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from chief.runs.schema import (
    AttemptRecord,
    AttemptStatus,
    CheckpointRecord,
    RunEventRecord,
    RunEventType,
    RunRecord,
    RunStatus,
    StepLease,
    StepRecord,
    StepSpec,
    StepStatus,
    VerificationStatus,
)


class RunStoreError(RuntimeError):
    """Base error for durable run state transitions."""


class IdempotencyConflict(RunStoreError):
    """The same idempotency key was reused for a different run plan."""


class LeaseLost(RunStoreError):
    """A worker attempted to mutate a step after losing its lease."""


class InvalidRunTransition(RunStoreError):
    """The requested run or step transition is not valid."""


class SQLiteRunStore:
    """Durable sequential run/checkpoint state backed by SQLite.

    Claims and terminal transitions use ``BEGIN IMMEDIATE`` so only one process
    can own a step attempt. Handlers must still pass the step idempotency key to
    external systems: SQLite cannot make an unrelated side effect atomic with
    its local checkpoint.
    """

    def __init__(
        self,
        database_path: str | Path = "data/chief.db",
        *,
        busy_timeout_ms: int = 5_000,
        max_json_bytes: int = 1_000_000,
        max_page_size: int = 1_000,
    ) -> None:
        if busy_timeout_ms < 1:
            raise ValueError("Run-store busy timeout must be positive.")
        if max_json_bytes < 2:
            raise ValueError("Run-store JSON limit is too small.")
        if max_page_size < 1:
            raise ValueError("Run-store maximum page size must be positive.")
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_ms = busy_timeout_ms
        self.max_json_bytes = max_json_bytes
        self.max_page_size = max_page_size
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_ms / 1_000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        return connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS chief_component_migrations (
                    component TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    applied_at TEXT NOT NULL,
                    PRIMARY KEY (component, version)
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    correlation_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    input_digest TEXT NOT NULL,
                    plan_digest TEXT NOT NULL,
                    result_json TEXT,
                    result_digest TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    version INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    cancelled_at TEXT
                );

                CREATE TABLE IF NOT EXISTS run_steps (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    input_digest TEXT NOT NULL,
                    result_json TEXT,
                    result_digest TEXT,
                    verification_required INTEGER NOT NULL,
                    verification_status TEXT NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT,
                    lease_owner TEXT,
                    lease_token TEXT UNIQUE,
                    lease_expires_at TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    UNIQUE (run_id, ordinal),
                    UNIQUE (run_id, idempotency_key),
                    CHECK (max_attempts BETWEEN 1 AND 10),
                    CHECK (attempt_count BETWEEN 0 AND max_attempts)
                );

                CREATE TABLE IF NOT EXISTS run_attempts (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    step_id TEXT NOT NULL REFERENCES run_steps(id) ON DELETE CASCADE,
                    attempt_number INTEGER NOT NULL,
                    worker_id TEXT NOT NULL,
                    lease_token TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    input_digest TEXT NOT NULL,
                    result_digest TEXT,
                    verification_status TEXT NOT NULL,
                    error_code TEXT,
                    error_message TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    UNIQUE (step_id, attempt_number)
                );

                CREATE TABLE IF NOT EXISTS run_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    step_id TEXT REFERENCES run_steps(id) ON DELETE CASCADE,
                    attempt_id TEXT REFERENCES run_attempts(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS run_checkpoints (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    step_id TEXT NOT NULL UNIQUE REFERENCES run_steps(id) ON DELETE CASCADE,
                    attempt_id TEXT NOT NULL REFERENCES run_attempts(id) ON DELETE CASCADE,
                    data_json TEXT NOT NULL,
                    data_digest TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS ix_run_steps_claim
                    ON run_steps(status, next_attempt_at, lease_expires_at, run_id, ordinal);
                CREATE INDEX IF NOT EXISTS ix_run_attempts_step
                    ON run_attempts(step_id, attempt_number);
                CREATE INDEX IF NOT EXISTS ix_run_events_run_sequence
                    ON run_events(run_id, sequence);
                CREATE INDEX IF NOT EXISTS ix_run_checkpoints_run_sequence
                    ON run_checkpoints(run_id, sequence);
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO chief_component_migrations(component, version, applied_at)
                VALUES ('runs', 1, ?)
                """,
                (self._iso(self._now()),),
            )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @classmethod
    def _iso(cls, value: datetime) -> str:
        return cls._utc(value).isoformat()

    @staticmethod
    def _parsed(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    @staticmethod
    def _validate_text(name: str, value: str, *, maximum: int = 256) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string.")
        value = value.strip()
        if len(value) > maximum:
            raise ValueError(f"{name} cannot exceed {maximum} characters.")
        return value

    def _json(self, value: dict[str, Any]) -> tuple[str, str]:
        if not isinstance(value, dict):
            raise TypeError("Run data must be a dictionary.")
        try:
            encoded = json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Run data must contain only finite JSON values.") from exc
        if len(encoded.encode("utf-8")) > self.max_json_bytes:
            raise ValueError("Run data exceeds the configured JSON size limit.")
        return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _plan_json(self, input_data: dict[str, Any], steps: list[StepSpec]) -> tuple[str, str]:
        plan = {
            "input": input_data,
            "steps": [step.model_dump(mode="json") for step in steps],
        }
        return self._json(plan)

    def create_run(
        self,
        *,
        idempotency_key: str,
        steps: list[StepSpec],
        input_data: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        run_id: UUID | None = None,
        now: datetime | None = None,
    ) -> RunRecord:
        idempotency_key = self._validate_text("Run idempotency key", idempotency_key)
        correlation_id = self._validate_text("Correlation ID", correlation_id or str(uuid4()))
        if not 1 <= len(steps) <= 1_000:
            raise ValueError("A run must contain between 1 and 1,000 steps.")
        if len({step.idempotency_key for step in steps}) != len(steps):
            raise ValueError("Step idempotency keys must be unique within a run.")

        input_data = input_data or {}
        input_json, input_digest = self._json(input_data)
        _, plan_digest = self._plan_json(input_data, steps)
        timestamp = self._iso(now or self._now())
        new_run_id = str(run_id or uuid4())

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM runs WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                if existing["plan_digest"] != plan_digest:
                    raise IdempotencyConflict(
                        "Run idempotency key is already bound to a different plan."
                    )
                return self._run(existing)

            connection.execute(
                """
                INSERT INTO runs (
                    id, idempotency_key, correlation_id, status, input_json,
                    input_digest, plan_digest, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    new_run_id,
                    idempotency_key,
                    correlation_id,
                    RunStatus.QUEUED.value,
                    input_json,
                    input_digest,
                    plan_digest,
                    timestamp,
                    timestamp,
                ),
            )
            for ordinal, spec in enumerate(steps):
                step_input_json, step_input_digest = self._json(spec.input_data)
                connection.execute(
                    """
                    INSERT INTO run_steps (
                        id, run_id, ordinal, action, idempotency_key, status,
                        input_json, input_digest, verification_required,
                        verification_status, max_attempts, attempt_count,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        new_run_id,
                        ordinal,
                        spec.action,
                        spec.idempotency_key,
                        StepStatus.PENDING.value,
                        step_input_json,
                        step_input_digest,
                        int(spec.verification_required),
                        (
                            VerificationStatus.PENDING.value
                            if spec.verification_required
                            else VerificationStatus.NOT_REQUIRED.value
                        ),
                        spec.max_attempts,
                        timestamp,
                        timestamp,
                    ),
                )
            self._event(
                connection,
                run_id=new_run_id,
                correlation_id=correlation_id,
                event_type=RunEventType.RUN_CREATED,
                payload={"plan_digest": plan_digest, "step_count": len(steps)},
                created_at=timestamp,
            )
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (new_run_id,)).fetchone()
            assert row is not None
            return self._run(row)

    def get_run(self, run_id: UUID) -> RunRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (str(run_id),)).fetchone()
        return self._run(row) if row is not None else None

    def list_runs(
        self,
        *,
        status: RunStatus | None = None,
        limit: int = 100,
    ) -> list[RunRecord]:
        if not 1 <= limit <= 1_000:
            raise ValueError("Run limit must be between 1 and 1000.")
        query = "SELECT * FROM runs"
        parameters: list[object] = []
        if status is not None:
            query += " WHERE status = ?"
            parameters.append(status.value)
        query += " ORDER BY created_at DESC LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._run(row) for row in rows]

    def get_step(self, step_id: UUID) -> StepRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM run_steps WHERE id = ?", (str(step_id),)
            ).fetchone()
        return self._step(row) if row is not None else None

    def list_steps(self, run_id: UUID) -> list[StepRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM run_steps WHERE run_id = ? ORDER BY ordinal", (str(run_id),)
            ).fetchall()
        return [self._step(row) for row in rows]

    def list_attempts(self, step_id: UUID) -> list[AttemptRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM run_attempts
                WHERE step_id = ? ORDER BY attempt_number
                """,
                (str(step_id),),
            ).fetchall()
        return [self._attempt(row) for row in rows]

    def claim_next_step(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 30,
        now: datetime | None = None,
    ) -> StepLease | None:
        worker_id = self._validate_text("Worker ID", worker_id)
        if not 1 <= lease_seconds <= 3_600:
            raise ValueError("Step lease must be between 1 and 3,600 seconds.")
        current = self._utc(now or self._now())
        current_iso = self._iso(current)
        lease_expires_at = current + timedelta(seconds=lease_seconds)
        lease_expires_iso = self._iso(lease_expires_at)

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_expired(connection, current)
            row = connection.execute(
                """
                SELECT s.*, r.status AS run_status, r.correlation_id
                FROM run_steps AS s
                JOIN runs AS r ON r.id = s.run_id
                WHERE r.status IN (?, ?)
                  AND s.status IN (?, ?)
                  AND (s.next_attempt_at IS NULL OR s.next_attempt_at <= ?)
                  AND NOT EXISTS (
                      SELECT 1 FROM run_steps AS prior
                      WHERE prior.run_id = s.run_id
                        AND prior.ordinal < s.ordinal
                        AND prior.status != ?
                  )
                ORDER BY r.created_at, s.ordinal
                LIMIT 1
                """,
                (
                    RunStatus.QUEUED.value,
                    RunStatus.RUNNING.value,
                    StepStatus.PENDING.value,
                    StepStatus.RETRY_WAIT.value,
                    current_iso,
                    StepStatus.SUCCEEDED.value,
                ),
            ).fetchone()
            if row is None:
                return None

            step_id = row["id"]
            run_id = row["run_id"]
            attempt_number = int(row["attempt_count"]) + 1
            lease_token = str(uuid4())
            attempt_id = str(uuid4())
            connection.execute(
                """
                UPDATE run_steps
                SET status = ?, attempt_count = ?, lease_owner = ?, lease_token = ?,
                    lease_expires_at = ?, next_attempt_at = NULL,
                    started_at = COALESCE(started_at, ?), updated_at = ?,
                    error_code = NULL, error_message = NULL
                WHERE id = ? AND status IN (?, ?)
                """,
                (
                    StepStatus.RUNNING.value,
                    attempt_number,
                    worker_id,
                    lease_token,
                    lease_expires_iso,
                    current_iso,
                    current_iso,
                    step_id,
                    StepStatus.PENDING.value,
                    StepStatus.RETRY_WAIT.value,
                ),
            )
            if row["run_status"] == RunStatus.QUEUED.value:
                connection.execute(
                    """
                    UPDATE runs SET status = ?, started_at = ?, updated_at = ?, version = version + 1
                    WHERE id = ? AND status = ?
                    """,
                    (
                        RunStatus.RUNNING.value,
                        current_iso,
                        current_iso,
                        run_id,
                        RunStatus.QUEUED.value,
                    ),
                )
                self._event(
                    connection,
                    run_id=run_id,
                    correlation_id=row["correlation_id"],
                    event_type=RunEventType.RUN_STARTED,
                    payload={"worker_id": worker_id},
                    created_at=current_iso,
                )
            connection.execute(
                """
                INSERT INTO run_attempts (
                    id, run_id, step_id, attempt_number, worker_id, lease_token,
                    status, input_digest, verification_status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    run_id,
                    step_id,
                    attempt_number,
                    worker_id,
                    lease_token,
                    AttemptStatus.RUNNING.value,
                    row["input_digest"],
                    row["verification_status"],
                    current_iso,
                ),
            )
            self._event(
                connection,
                run_id=run_id,
                step_id=step_id,
                attempt_id=attempt_id,
                correlation_id=row["correlation_id"],
                event_type=RunEventType.STEP_CLAIMED,
                payload={
                    "attempt_number": attempt_number,
                    "lease_expires_at": lease_expires_iso,
                    "worker_id": worker_id,
                },
                created_at=current_iso,
            )
            return self._lease(connection, lease_token)

    def renew_lease(
        self,
        lease_token: str,
        *,
        lease_seconds: int = 30,
        now: datetime | None = None,
    ) -> StepLease:
        if not 1 <= lease_seconds <= 3_600:
            raise ValueError("Step lease must be between 1 and 3,600 seconds.")
        current = self._utc(now or self._now())
        expires = current + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._active_claim(connection, lease_token, current)
            connection.execute(
                """
                UPDATE run_steps SET lease_expires_at = ?, updated_at = ?
                WHERE lease_token = ? AND status = ?
                """,
                (
                    self._iso(expires),
                    self._iso(current),
                    lease_token,
                    StepStatus.RUNNING.value,
                ),
            )
            return self._lease(connection, lease_token)

    def complete_step(
        self,
        lease_token: str,
        *,
        result_data: dict[str, Any],
        verification_status: VerificationStatus = VerificationStatus.NOT_REQUIRED,
        now: datetime | None = None,
    ) -> StepRecord:
        result_json, result_digest = self._json(result_data)
        current = self._utc(now or self._now())
        current_iso = self._iso(current)

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._active_claim(connection, lease_token, current)
            if bool(row["verification_required"]) and verification_status != (
                VerificationStatus.VERIFIED
            ):
                raise InvalidRunTransition("A verification-required step must be verified.")
            if verification_status in {VerificationStatus.PENDING, VerificationStatus.FAILED}:
                raise InvalidRunTransition("An incomplete or failed verification cannot succeed.")

            connection.execute(
                """
                UPDATE run_attempts
                SET status = ?, result_digest = ?, verification_status = ?, finished_at = ?
                WHERE lease_token = ? AND status = ?
                """,
                (
                    AttemptStatus.SUCCEEDED.value,
                    result_digest,
                    verification_status.value,
                    current_iso,
                    lease_token,
                    AttemptStatus.RUNNING.value,
                ),
            )
            connection.execute(
                """
                UPDATE run_steps
                SET status = ?, result_json = ?, result_digest = ?,
                    verification_status = ?, lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL, completed_at = ?, updated_at = ?
                WHERE id = ? AND lease_token = ? AND status = ?
                """,
                (
                    StepStatus.SUCCEEDED.value,
                    result_json,
                    result_digest,
                    verification_status.value,
                    current_iso,
                    current_iso,
                    row["step_id"],
                    lease_token,
                    StepStatus.RUNNING.value,
                ),
            )
            connection.execute(
                """
                INSERT INTO run_checkpoints (
                    id, run_id, step_id, attempt_id, data_json, data_digest,
                    verification_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    row["run_id"],
                    row["step_id"],
                    row["attempt_id"],
                    result_json,
                    result_digest,
                    verification_status.value,
                    current_iso,
                ),
            )
            self._event(
                connection,
                run_id=row["run_id"],
                step_id=row["step_id"],
                attempt_id=row["attempt_id"],
                correlation_id=row["correlation_id"],
                event_type=RunEventType.STEP_SUCCEEDED,
                payload={
                    "result_digest": result_digest,
                    "verification_status": verification_status.value,
                },
                created_at=current_iso,
            )

            remaining = connection.execute(
                """
                SELECT COUNT(*) AS count FROM run_steps
                WHERE run_id = ? AND status != ?
                """,
                (row["run_id"], StepStatus.SUCCEEDED.value),
            ).fetchone()
            assert remaining is not None
            if int(remaining["count"]) == 0:
                connection.execute(
                    """
                    UPDATE runs
                    SET status = ?, result_json = ?, result_digest = ?,
                        completed_at = ?, updated_at = ?, version = version + 1
                    WHERE id = ? AND status = ?
                    """,
                    (
                        RunStatus.SUCCEEDED.value,
                        result_json,
                        result_digest,
                        current_iso,
                        current_iso,
                        row["run_id"],
                        RunStatus.RUNNING.value,
                    ),
                )
                self._event(
                    connection,
                    run_id=row["run_id"],
                    correlation_id=row["correlation_id"],
                    event_type=RunEventType.RUN_SUCCEEDED,
                    payload={"result_digest": result_digest},
                    created_at=current_iso,
                )
            else:
                connection.execute(
                    "UPDATE runs SET updated_at = ?, version = version + 1 WHERE id = ?",
                    (current_iso, row["run_id"]),
                )
            step_row = connection.execute(
                "SELECT * FROM run_steps WHERE id = ?", (row["step_id"],)
            ).fetchone()
            assert step_row is not None
            return self._step(step_row)

    def fail_step(
        self,
        lease_token: str,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
        retry_delay_seconds: int = 0,
        result_data: dict[str, Any] | None = None,
        verification_status: VerificationStatus = VerificationStatus.FAILED,
        now: datetime | None = None,
    ) -> StepRecord:
        error_code = self._validate_text("Step error code", error_code, maximum=100)
        if len(error_message) > 20_000:
            raise ValueError("Step error message cannot exceed 20,000 characters.")
        if not 0 <= retry_delay_seconds <= 86_400:
            raise ValueError("Retry delay must be between 0 and 86,400 seconds.")
        result_json: str | None = None
        result_digest: str | None = None
        if result_data is not None:
            result_json, result_digest = self._json(result_data)
        current = self._utc(now or self._now())
        current_iso = self._iso(current)

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._active_claim(connection, lease_token, current)
            connection.execute(
                """
                UPDATE run_attempts
                SET status = ?, result_digest = ?, verification_status = ?,
                    error_code = ?, error_message = ?, finished_at = ?
                WHERE lease_token = ? AND status = ?
                """,
                (
                    AttemptStatus.FAILED.value,
                    result_digest,
                    verification_status.value,
                    error_code,
                    error_message,
                    current_iso,
                    lease_token,
                    AttemptStatus.RUNNING.value,
                ),
            )

            can_retry = retryable and int(row["attempt_count"]) < int(row["max_attempts"])
            if can_retry:
                next_attempt = self._iso(current + timedelta(seconds=retry_delay_seconds))
                connection.execute(
                    """
                    UPDATE run_steps
                    SET status = ?, result_json = ?, result_digest = ?,
                        verification_status = ?, next_attempt_at = ?,
                        lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                        error_code = ?, error_message = ?, updated_at = ?
                    WHERE id = ? AND lease_token = ? AND status = ?
                    """,
                    (
                        StepStatus.RETRY_WAIT.value,
                        result_json,
                        result_digest,
                        verification_status.value,
                        next_attempt,
                        error_code,
                        error_message,
                        current_iso,
                        row["step_id"],
                        lease_token,
                        StepStatus.RUNNING.value,
                    ),
                )
                event_type = RunEventType.STEP_RETRY_SCHEDULED
                event_payload = {
                    "attempt_number": int(row["attempt_count"]),
                    "error_code": error_code,
                    "next_attempt_at": next_attempt,
                }
                connection.execute(
                    "UPDATE runs SET updated_at = ?, version = version + 1 WHERE id = ?",
                    (current_iso, row["run_id"]),
                )
            else:
                connection.execute(
                    """
                    UPDATE run_steps
                    SET status = ?, result_json = ?, result_digest = ?,
                        verification_status = ?, lease_owner = NULL, lease_token = NULL,
                        lease_expires_at = NULL, error_code = ?, error_message = ?,
                        completed_at = ?, updated_at = ?
                    WHERE id = ? AND lease_token = ? AND status = ?
                    """,
                    (
                        StepStatus.FAILED.value,
                        result_json,
                        result_digest,
                        verification_status.value,
                        error_code,
                        error_message,
                        current_iso,
                        current_iso,
                        row["step_id"],
                        lease_token,
                        StepStatus.RUNNING.value,
                    ),
                )
                connection.execute(
                    """
                    UPDATE run_steps SET status = ?, error_code = ?, error_message = ?,
                        completed_at = ?, updated_at = ?
                    WHERE run_id = ? AND ordinal > ? AND status IN (?, ?)
                    """,
                    (
                        StepStatus.CANCELLED.value,
                        "upstream_failed",
                        "A prior step failed.",
                        current_iso,
                        current_iso,
                        row["run_id"],
                        row["ordinal"],
                        StepStatus.PENDING.value,
                        StepStatus.RETRY_WAIT.value,
                    ),
                )
                connection.execute(
                    """
                    UPDATE runs
                    SET status = ?, error_code = ?, error_message = ?, completed_at = ?,
                        updated_at = ?, version = version + 1
                    WHERE id = ? AND status = ?
                    """,
                    (
                        RunStatus.FAILED.value,
                        error_code,
                        error_message,
                        current_iso,
                        current_iso,
                        row["run_id"],
                        RunStatus.RUNNING.value,
                    ),
                )
                event_type = RunEventType.STEP_FAILED
                event_payload = {
                    "attempt_number": int(row["attempt_count"]),
                    "error_code": error_code,
                    "retryable": retryable,
                }
            self._event(
                connection,
                run_id=row["run_id"],
                step_id=row["step_id"],
                attempt_id=row["attempt_id"],
                correlation_id=row["correlation_id"],
                event_type=event_type,
                payload=event_payload,
                created_at=current_iso,
            )
            if not can_retry:
                self._event(
                    connection,
                    run_id=row["run_id"],
                    step_id=row["step_id"],
                    attempt_id=row["attempt_id"],
                    correlation_id=row["correlation_id"],
                    event_type=RunEventType.RUN_FAILED,
                    payload={"error_code": error_code},
                    created_at=current_iso,
                )
            step_row = connection.execute(
                "SELECT * FROM run_steps WHERE id = ?", (row["step_id"],)
            ).fetchone()
            assert step_row is not None
            return self._step(step_row)

    def cancel_run(
        self,
        run_id: UUID,
        *,
        reason: str = "Cancelled by request.",
        now: datetime | None = None,
    ) -> RunRecord:
        if len(reason) > 2_000:
            raise ValueError("Cancellation reason cannot exceed 2,000 characters.")
        current_iso = self._iso(self._utc(now or self._now()))
        run_id_text = str(run_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id_text,)).fetchone()
            if row is None:
                raise KeyError(f"Run {run_id} does not exist.")
            if row["status"] in {
                RunStatus.SUCCEEDED.value,
                RunStatus.FAILED.value,
                RunStatus.CANCELLED.value,
            }:
                return self._run(row)

            connection.execute(
                """
                UPDATE run_attempts
                SET status = ?, error_code = ?, error_message = ?, finished_at = ?
                WHERE run_id = ? AND status = ?
                """,
                (
                    AttemptStatus.CANCELLED.value,
                    "run_cancelled",
                    reason,
                    current_iso,
                    run_id_text,
                    AttemptStatus.RUNNING.value,
                ),
            )
            connection.execute(
                """
                UPDATE run_steps
                SET status = ?, lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL, next_attempt_at = NULL,
                    error_code = ?, error_message = ?, completed_at = ?, updated_at = ?
                WHERE run_id = ? AND status IN (?, ?, ?)
                """,
                (
                    StepStatus.CANCELLED.value,
                    "run_cancelled",
                    reason,
                    current_iso,
                    current_iso,
                    run_id_text,
                    StepStatus.PENDING.value,
                    StepStatus.RUNNING.value,
                    StepStatus.RETRY_WAIT.value,
                ),
            )
            connection.execute(
                """
                UPDATE runs
                SET status = ?, error_code = ?, error_message = ?, cancelled_at = ?,
                    completed_at = ?, updated_at = ?, version = version + 1
                WHERE id = ?
                """,
                (
                    RunStatus.CANCELLED.value,
                    "run_cancelled",
                    reason,
                    current_iso,
                    current_iso,
                    current_iso,
                    run_id_text,
                ),
            )
            self._event(
                connection,
                run_id=run_id_text,
                correlation_id=row["correlation_id"],
                event_type=RunEventType.RUN_CANCELLED,
                payload={"reason": reason},
                created_at=current_iso,
            )
            updated = connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id_text,)
            ).fetchone()
            assert updated is not None
            return self._run(updated)

    def recover_expired_leases(self, *, now: datetime | None = None) -> int:
        current = self._utc(now or self._now())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._recover_expired(connection, current)

    def _recover_expired(self, connection: sqlite3.Connection, now: datetime) -> int:
        current_iso = self._iso(now)
        rows = connection.execute(
            """
            SELECT s.*, r.correlation_id
            FROM run_steps AS s
            JOIN runs AS r ON r.id = s.run_id
            WHERE s.status = ? AND s.lease_expires_at <= ? AND r.status = ?
            ORDER BY s.lease_expires_at
            """,
            (StepStatus.RUNNING.value, current_iso, RunStatus.RUNNING.value),
        ).fetchall()
        for row in rows:
            attempt = connection.execute(
                """
                SELECT * FROM run_attempts
                WHERE lease_token = ? AND status = ?
                """,
                (row["lease_token"], AttemptStatus.RUNNING.value),
            ).fetchone()
            if attempt is None:
                continue
            connection.execute(
                """
                UPDATE run_attempts
                SET status = ?, error_code = ?, error_message = ?, finished_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    AttemptStatus.ABANDONED.value,
                    "lease_expired",
                    "Worker lease expired before checkpointing.",
                    current_iso,
                    attempt["id"],
                    AttemptStatus.RUNNING.value,
                ),
            )
            self._event(
                connection,
                run_id=row["run_id"],
                step_id=row["id"],
                attempt_id=attempt["id"],
                correlation_id=row["correlation_id"],
                event_type=RunEventType.STEP_LEASE_EXPIRED,
                payload={"attempt_number": int(row["attempt_count"])},
                created_at=current_iso,
            )
            if int(row["attempt_count"]) < int(row["max_attempts"]):
                connection.execute(
                    """
                    UPDATE run_steps
                    SET status = ?, next_attempt_at = ?, lease_owner = NULL,
                        lease_token = NULL, lease_expires_at = NULL,
                        error_code = ?, error_message = ?, updated_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (
                        StepStatus.RETRY_WAIT.value,
                        current_iso,
                        "lease_expired",
                        "Worker lease expired before checkpointing.",
                        current_iso,
                        row["id"],
                        StepStatus.RUNNING.value,
                    ),
                )
                self._event(
                    connection,
                    run_id=row["run_id"],
                    step_id=row["id"],
                    attempt_id=attempt["id"],
                    correlation_id=row["correlation_id"],
                    event_type=RunEventType.STEP_RETRY_SCHEDULED,
                    payload={"reason": "lease_expired", "next_attempt_at": current_iso},
                    created_at=current_iso,
                )
                connection.execute(
                    "UPDATE runs SET updated_at = ?, version = version + 1 WHERE id = ?",
                    (current_iso, row["run_id"]),
                )
            else:
                self._fail_expired_step(connection, row, attempt, current_iso)
        return len(rows)

    def _fail_expired_step(
        self,
        connection: sqlite3.Connection,
        step: sqlite3.Row,
        attempt: sqlite3.Row,
        current_iso: str,
    ) -> None:
        error_message = "Step exhausted its attempts after a worker lease expired."
        connection.execute(
            """
            UPDATE run_steps
            SET status = ?, lease_owner = NULL, lease_token = NULL,
                lease_expires_at = NULL, error_code = ?, error_message = ?,
                completed_at = ?, updated_at = ?
            WHERE id = ? AND status = ?
            """,
            (
                StepStatus.FAILED.value,
                "lease_expired",
                error_message,
                current_iso,
                current_iso,
                step["id"],
                StepStatus.RUNNING.value,
            ),
        )
        connection.execute(
            """
            UPDATE run_steps SET status = ?, error_code = ?, error_message = ?,
                completed_at = ?, updated_at = ?
            WHERE run_id = ? AND ordinal > ? AND status IN (?, ?)
            """,
            (
                StepStatus.CANCELLED.value,
                "upstream_failed",
                "A prior step failed.",
                current_iso,
                current_iso,
                step["run_id"],
                step["ordinal"],
                StepStatus.PENDING.value,
                StepStatus.RETRY_WAIT.value,
            ),
        )
        connection.execute(
            """
            UPDATE runs SET status = ?, error_code = ?, error_message = ?,
                completed_at = ?, updated_at = ?, version = version + 1
            WHERE id = ? AND status = ?
            """,
            (
                RunStatus.FAILED.value,
                "lease_expired",
                error_message,
                current_iso,
                current_iso,
                step["run_id"],
                RunStatus.RUNNING.value,
            ),
        )
        self._event(
            connection,
            run_id=step["run_id"],
            step_id=step["id"],
            attempt_id=attempt["id"],
            correlation_id=step["correlation_id"],
            event_type=RunEventType.STEP_FAILED,
            payload={"error_code": "lease_expired", "attempts_exhausted": True},
            created_at=current_iso,
        )
        self._event(
            connection,
            run_id=step["run_id"],
            step_id=step["id"],
            attempt_id=attempt["id"],
            correlation_id=step["correlation_id"],
            event_type=RunEventType.RUN_FAILED,
            payload={"error_code": "lease_expired"},
            created_at=current_iso,
        )

    def _active_claim(
        self, connection: sqlite3.Connection, lease_token: str, now: datetime
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT s.id AS step_id, s.run_id, s.ordinal, s.attempt_count,
                   s.max_attempts, s.verification_required, s.lease_expires_at,
                   a.id AS attempt_id, r.correlation_id
            FROM run_steps AS s
            JOIN run_attempts AS a ON a.lease_token = s.lease_token
            JOIN runs AS r ON r.id = s.run_id
            WHERE s.lease_token = ? AND s.status = ? AND a.status = ?
              AND r.status = ?
            """,
            (
                lease_token,
                StepStatus.RUNNING.value,
                AttemptStatus.RUNNING.value,
                RunStatus.RUNNING.value,
            ),
        ).fetchone()
        if row is None or self._parsed(row["lease_expires_at"]) <= now:
            raise LeaseLost("Step lease is missing, expired, cancelled, or already consumed.")
        return row

    def _lease(self, connection: sqlite3.Connection, lease_token: str) -> StepLease:
        step_row = connection.execute(
            "SELECT * FROM run_steps WHERE lease_token = ?", (lease_token,)
        ).fetchone()
        attempt_row = connection.execute(
            "SELECT * FROM run_attempts WHERE lease_token = ?", (lease_token,)
        ).fetchone()
        if step_row is None or attempt_row is None:
            raise LeaseLost("Step lease no longer exists.")
        run_row = connection.execute(
            "SELECT * FROM runs WHERE id = ?", (step_row["run_id"],)
        ).fetchone()
        assert run_row is not None
        expires = self._parsed(step_row["lease_expires_at"])
        assert expires is not None
        return StepLease(
            run=self._run(run_row),
            step=self._step(step_row),
            attempt=self._attempt(attempt_row),
            lease_token=lease_token,
            lease_expires_at=expires,
        )

    def list_events(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[RunEventRecord]:
        if not 1 <= limit <= self.max_page_size:
            raise ValueError(f"Run-event page limit must be between 1 and {self.max_page_size}.")
        if after_sequence < 0:
            raise ValueError("Run-event cursor cannot be negative.")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM run_events
                WHERE run_id = ? AND sequence > ?
                ORDER BY sequence ASC LIMIT ?
                """,
                (str(run_id), after_sequence, limit),
            ).fetchall()
        return [self._run_event(row) for row in rows]

    def list_checkpoints(
        self,
        run_id: UUID,
        *,
        limit: int = 100,
    ) -> list[CheckpointRecord]:
        if not 1 <= limit <= self.max_page_size:
            raise ValueError(f"Checkpoint page limit must be between 1 and {self.max_page_size}.")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM run_checkpoints
                WHERE run_id = ? ORDER BY sequence ASC LIMIT ?
                """,
                (str(run_id), limit),
            ).fetchall()
        return [self._checkpoint(row) for row in rows]

    def _event(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        correlation_id: str,
        event_type: RunEventType,
        payload: dict[str, Any],
        created_at: str,
        step_id: str | None = None,
        attempt_id: str | None = None,
    ) -> None:
        payload_json, _ = self._json(payload)
        connection.execute(
            """
            INSERT INTO run_events (
                id, run_id, step_id, attempt_id, event_type,
                correlation_id, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                run_id,
                step_id,
                attempt_id,
                event_type.value,
                correlation_id,
                payload_json,
                created_at,
            ),
        )

    @staticmethod
    def _run(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            id=row["id"],
            idempotency_key=row["idempotency_key"],
            correlation_id=row["correlation_id"],
            status=row["status"],
            input_data=json.loads(row["input_json"]),
            input_digest=row["input_digest"],
            plan_digest=row["plan_digest"],
            result_data=json.loads(row["result_json"]) if row["result_json"] else None,
            result_digest=row["result_digest"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            cancelled_at=row["cancelled_at"],
        )

    @staticmethod
    def _step(row: sqlite3.Row) -> StepRecord:
        return StepRecord(
            id=row["id"],
            run_id=row["run_id"],
            ordinal=row["ordinal"],
            action=row["action"],
            idempotency_key=row["idempotency_key"],
            status=row["status"],
            input_data=json.loads(row["input_json"]),
            input_digest=row["input_digest"],
            result_data=json.loads(row["result_json"]) if row["result_json"] else None,
            result_digest=row["result_digest"],
            verification_required=bool(row["verification_required"]),
            verification_status=row["verification_status"],
            max_attempts=row["max_attempts"],
            attempt_count=row["attempt_count"],
            next_attempt_at=row["next_attempt_at"],
            lease_owner=row["lease_owner"],
            lease_token=row["lease_token"],
            lease_expires_at=row["lease_expires_at"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    @staticmethod
    def _attempt(row: sqlite3.Row) -> AttemptRecord:
        return AttemptRecord(
            id=row["id"],
            run_id=row["run_id"],
            step_id=row["step_id"],
            attempt_number=row["attempt_number"],
            worker_id=row["worker_id"],
            lease_token=row["lease_token"],
            status=row["status"],
            input_digest=row["input_digest"],
            result_digest=row["result_digest"],
            verification_status=row["verification_status"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    @staticmethod
    def _run_event(row: sqlite3.Row) -> RunEventRecord:
        return RunEventRecord(
            sequence=row["sequence"],
            id=row["id"],
            run_id=row["run_id"],
            step_id=row["step_id"],
            attempt_id=row["attempt_id"],
            event_type=row["event_type"],
            correlation_id=row["correlation_id"],
            payload=json.loads(row["payload_json"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _checkpoint(row: sqlite3.Row) -> CheckpointRecord:
        return CheckpointRecord(
            sequence=row["sequence"],
            id=row["id"],
            run_id=row["run_id"],
            step_id=row["step_id"],
            attempt_id=row["attempt_id"],
            data=json.loads(row["data_json"]),
            data_digest=row["data_digest"],
            verification_status=row["verification_status"],
            created_at=row["created_at"],
        )
