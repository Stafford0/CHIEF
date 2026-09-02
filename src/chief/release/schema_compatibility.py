from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

CURRENT_SCHEMA_VERSIONS: dict[str, int] = {
    "business_graph": 1,
    "decisions": 1,
    "events": 1,
    "evidence_plane": 1,
    "foresight": 1,
    "notifications": 1,
    "operator_recovery": 1,
    "portfolio_registry": 1,
    "runs": 1,
    "work": 1,
}

_COMPONENT_TABLES: dict[str, tuple[str, frozenset[str]]] = {
    "business_graph": ("business_nodes", frozenset({"id", "owner_id", "kind", "payload_json"})),
    "decisions": ("decisions", frozenset({"id", "status", "payload_json", "updated_at"})),
    "events": (
        "chief_events",
        frozenset({"id", "status", "attempts", "max_attempts", "idempotency_key"}),
    ),
    "evidence_plane": (
        "integration_sync_cursors",
        frozenset({"principal_id", "connector_id", "business_key", "scope", "cursor_value"}),
    ),
    "foresight": (
        "foresight_signals",
        frozenset({"id", "payload_json", "status", "observed_at"}),
    ),
    "notifications": (
        "notifications",
        frozenset({"id", "recipient_id", "idempotency_key", "created_at"}),
    ),
    "operator_recovery": (
        "event_recovery_actions",
        frozenset({"id", "event_id", "actor_id", "action", "created_at"}),
    ),
    "portfolio_registry": (
        "portfolio_businesses",
        frozenset({"id", "owner_id", "business_key", "payload_json"}),
    ),
    "runs": (
        "runs",
        frozenset({"id", "idempotency_key", "status", "plan_digest", "version"}),
    ),
    "work": ("tasks", frozenset({"id", "status", "priority", "updated_at"})),
}


class SchemaCompatibilityReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checked_at: datetime
    integrity_ok: bool
    compatible: bool
    applied_versions: dict[str, int]
    missing_components: tuple[str, ...]
    newer_components: tuple[str, ...]
    structural_mismatches: tuple[str, ...]


class SchemaCompatibilityService:
    """Baseline known v1 stores and fail closed on unknown-newer or malformed schemas."""

    def __init__(self, database_path: str | Path = "data/chief.db") -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
        return {str(row["name"]) for row in connection.execute(f'PRAGMA table_info("{table}")')}

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None

    def baseline_known_v1_components(self) -> dict[str, int]:
        """Register an existing known v1 table only after its required columns match."""

        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
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
            for component, (table, required_columns) in _COMPONENT_TABLES.items():
                if not self._table_exists(connection, table):
                    continue
                columns = self._table_columns(connection, table)
                if not required_columns.issubset(columns):
                    continue
                existing = connection.execute(
                    "SELECT MAX(version) AS version FROM chief_component_migrations WHERE component=?",
                    (component,),
                ).fetchone()
                if existing is None or existing["version"] is None:
                    connection.execute(
                        "INSERT INTO chief_component_migrations(component, version, applied_at) VALUES (?, 1, ?)",
                        (component, now),
                    )
        return self.applied_versions()

    def applied_versions(self) -> dict[str, int]:
        with self._connect() as connection:
            if not self._table_exists(connection, "chief_component_migrations"):
                return {}
            rows = connection.execute(
                """
                SELECT component, MAX(version) AS version
                FROM chief_component_migrations GROUP BY component
                """
            ).fetchall()
        return {str(row["component"]): int(row["version"]) for row in rows}

    def inspect(self, *, baseline: bool = True) -> SchemaCompatibilityReport:
        if baseline:
            self.baseline_known_v1_components()
        applied = self.applied_versions()
        mismatches: list[str] = []
        missing: list[str] = []
        newer: list[str] = []
        with self._connect() as connection:
            integrity_row = connection.execute("PRAGMA quick_check(1)").fetchone()
            integrity_ok = integrity_row is not None and str(integrity_row[0]) == "ok"
            for component, expected_version in CURRENT_SCHEMA_VERSIONS.items():
                table, required_columns = _COMPONENT_TABLES[component]
                if not self._table_exists(connection, table):
                    missing.append(component)
                    continue
                columns = self._table_columns(connection, table)
                absent_columns = sorted(required_columns - columns)
                if absent_columns:
                    mismatches.append(
                        f"{component}: table {table} is missing columns {', '.join(absent_columns)}"
                    )
                applied_version = applied.get(component)
                if applied_version is None:
                    mismatches.append(f"{component}: schema exists but has no migration version")
                elif applied_version > expected_version:
                    newer.append(component)
        compatible = integrity_ok and not mismatches and not newer
        return SchemaCompatibilityReport(
            checked_at=datetime.now(UTC),
            integrity_ok=integrity_ok,
            compatible=compatible,
            applied_versions=applied,
            missing_components=tuple(sorted(missing)),
            newer_components=tuple(sorted(newer)),
            structural_mismatches=tuple(sorted(mismatches)),
        )

    def assert_upgrade_safe(self) -> SchemaCompatibilityReport:
        report = self.inspect()
        if not report.compatible:
            detail = "; ".join((*report.newer_components, *report.structural_mismatches))
            raise RuntimeError(f"CHIEF schema is not compatible with this build: {detail or 'integrity failure'}")
        return report

    def assert_rollback_safe(self, target_versions: dict[str, int]) -> None:
        applied = self.applied_versions()
        unsafe: list[str] = []
        for component, version in applied.items():
            target = target_versions.get(component)
            if target is not None and version > target:
                unsafe.append(f"{component} v{version} > rollback target v{target}")
        if unsafe:
            raise RuntimeError("Rollback is unsafe: " + "; ".join(sorted(unsafe)))
