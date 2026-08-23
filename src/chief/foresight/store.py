import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from chief.foresight.schema import KPI, Assumption, ForesightSignal, SignalStatus


class ForesightStore:
    """Persistent, evidence-linked operating register for signals and assumptions."""

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
                CREATE TABLE IF NOT EXISTS foresight_signals (
                    id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, status TEXT NOT NULL,
                    observed_at TEXT NOT NULL, expires_at TEXT, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_foresight_signals_open
                    ON foresight_signals(status, observed_at DESC);
                CREATE TABLE IF NOT EXISTS assumptions (
                    id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, status TEXT NOT NULL,
                    review_due_at TEXT, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_assumptions_review
                    ON assumptions(status, review_due_at);
                CREATE TABLE IF NOT EXISTS kpis (
                    id TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                """
            )

    def save_signal(self, signal: ForesightSignal) -> ForesightSignal:
        signal.updated_at = datetime.now(UTC)
        data = signal.model_dump(mode="json")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO foresight_signals VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json,
                    status=excluded.status, observed_at=excluded.observed_at,
                    expires_at=excluded.expires_at, updated_at=excluded.updated_at
                """,
                (
                    data["id"],
                    json.dumps(data, sort_keys=True),
                    data["status"],
                    data["observed_at"],
                    data["expires_at"],
                    data["updated_at"],
                ),
            )
        return signal

    def get_signal(self, signal_id: UUID) -> ForesightSignal | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM foresight_signals WHERE id=?", (str(signal_id),)
            ).fetchone()
        return ForesightSignal.model_validate_json(row["payload_json"]) if row else None

    def list_signals(
        self, *, include_closed: bool = False, limit: int = 200
    ) -> list[ForesightSignal]:
        if not 1 <= limit <= 1_000:
            raise ValueError("Signal limit must be between 1 and 1000.")
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        query = "SELECT payload_json FROM foresight_signals WHERE (expires_at IS NULL OR expires_at > ?)"
        parameters: list[object] = [now]
        if not include_closed:
            query += " AND status = ?"
            parameters.append(SignalStatus.OPEN.value)
        query += " ORDER BY observed_at DESC LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [ForesightSignal.model_validate_json(row["payload_json"]) for row in rows]

    def save_assumption(self, assumption: Assumption) -> Assumption:
        assumption.updated_at = datetime.now(UTC)
        data = assumption.model_dump(mode="json")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO assumptions VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json,
                    status=excluded.status, review_due_at=excluded.review_due_at,
                    updated_at=excluded.updated_at
                """,
                (
                    data["id"],
                    json.dumps(data, sort_keys=True),
                    data["status"],
                    data["review_due_at"],
                    data["updated_at"],
                ),
            )
        return assumption

    def list_assumptions_due(self, *, now: datetime | None = None) -> list[Assumption]:
        now_text = (now or datetime.now(UTC)).isoformat().replace("+00:00", "Z")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM assumptions
                WHERE review_due_at IS NOT NULL AND review_due_at <= ?
                ORDER BY review_due_at
                """,
                (now_text,),
            ).fetchall()
        return [Assumption.model_validate_json(row["payload_json"]) for row in rows]

    def list_assumptions(self) -> list[Assumption]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM assumptions ORDER BY updated_at DESC"
            ).fetchall()
        return [Assumption.model_validate_json(row["payload_json"]) for row in rows]

    def save_kpi(self, kpi: KPI) -> KPI:
        kpi.updated_at = datetime.now(UTC)
        data = kpi.model_dump(mode="json")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO kpis VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json,
                    observed_at=excluded.observed_at, updated_at=excluded.updated_at
                """,
                (
                    data["id"],
                    json.dumps(data, sort_keys=True),
                    data["observed_at"],
                    data["updated_at"],
                ),
            )
        return kpi

    def list_kpis(self) -> list[KPI]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM kpis ORDER BY observed_at DESC"
            ).fetchall()
        return [KPI.model_validate_json(row["payload_json"]) for row in rows]
