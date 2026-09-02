from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from chief.models.base import ModelPrivacy
from chief.models.router import RouteAttempt


class ModelRouteReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    actor_id: str
    request_id: str
    selected_provider: str | None
    selected_model: str | None
    selected_privacy: ModelPrivacy | None
    latency_ms: float | None = Field(default=None, ge=0)
    max_cost_tier: int | None = Field(default=None, ge=0)
    cloud_authorized: bool
    succeeded: bool
    attempts: tuple[dict[str, object], ...]
    created_at: datetime


class SQLiteModelRouteStore:
    """Persist route metadata without prompts, system text, keys, or provider error bodies."""

    def __init__(self, database_path: str | Path = "data/chief.db") -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS model_route_receipts (
                    id TEXT PRIMARY KEY,
                    actor_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    selected_provider TEXT,
                    selected_model TEXT,
                    selected_privacy TEXT,
                    latency_ms REAL,
                    max_cost_tier INTEGER,
                    cloud_authorized INTEGER NOT NULL,
                    succeeded INTEGER NOT NULL,
                    attempts_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_model_route_receipts_actor_created
                ON model_route_receipts(actor_id, created_at DESC)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @staticmethod
    def _attempts(attempts: list[RouteAttempt]) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "provider": item.provider,
                "succeeded": item.succeeded,
                "skipped": item.skipped,
                "failure_recorded": item.error is not None,
            }
            for item in attempts
        )

    def record(
        self,
        *,
        actor_id: str,
        request_id: str,
        attempts: list[RouteAttempt],
        selected_provider: str | None,
        selected_model: str | None,
        selected_privacy: ModelPrivacy | None,
        latency_ms: float | None,
        max_cost_tier: int | None,
        cloud_authorized: bool,
        succeeded: bool,
        created_at: datetime | None = None,
    ) -> ModelRouteReceipt:
        receipt = ModelRouteReceipt(
            id=uuid4(),
            actor_id=actor_id,
            request_id=request_id,
            selected_provider=selected_provider,
            selected_model=selected_model,
            selected_privacy=selected_privacy,
            latency_ms=latency_ms,
            max_cost_tier=max_cost_tier,
            cloud_authorized=cloud_authorized,
            succeeded=succeeded,
            attempts=self._attempts(attempts),
            created_at=(created_at or datetime.now(UTC)).astimezone(UTC),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO model_route_receipts(
                    id, actor_id, request_id, selected_provider, selected_model,
                    selected_privacy, latency_ms, max_cost_tier, cloud_authorized,
                    succeeded, attempts_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(receipt.id),
                    receipt.actor_id,
                    receipt.request_id,
                    receipt.selected_provider,
                    receipt.selected_model,
                    receipt.selected_privacy.value if receipt.selected_privacy else None,
                    receipt.latency_ms,
                    receipt.max_cost_tier,
                    int(receipt.cloud_authorized),
                    int(receipt.succeeded),
                    json.dumps(list(receipt.attempts), sort_keys=True, separators=(",", ":")),
                    receipt.created_at.isoformat(),
                ),
            )
        return receipt

    def list(self, *, actor_id: str, limit: int = 100) -> list[ModelRouteReceipt]:
        if not 1 <= limit <= 1_000:
            raise ValueError("Model route receipt limit must be between 1 and 1000")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM model_route_receipts
                WHERE actor_id = ? ORDER BY created_at DESC LIMIT ?
                """,
                (actor_id, limit),
            ).fetchall()
        return [
            ModelRouteReceipt(
                id=row["id"],
                actor_id=row["actor_id"],
                request_id=row["request_id"],
                selected_provider=row["selected_provider"],
                selected_model=row["selected_model"],
                selected_privacy=row["selected_privacy"],
                latency_ms=row["latency_ms"],
                max_cost_tier=row["max_cost_tier"],
                cloud_authorized=bool(row["cloud_authorized"]),
                succeeded=bool(row["succeeded"]),
                attempts=tuple(json.loads(row["attempts_json"])),
                created_at=row["created_at"],
            )
            for row in rows
        ]
