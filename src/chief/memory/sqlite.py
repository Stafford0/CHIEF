import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from chief.memory.schema import MemoryRecord
from chief.memory.store import MemoryStore


class SQLiteMemoryStore(MemoryStore):
    """SQLite-backed persistent memory store for CHIEF."""

    def __init__(self, database_path: str | Path = "data/chief.db") -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    importance REAL NOT NULL,
                    scope TEXT NOT NULL DEFAULT 'personal',
                    scope_id TEXT,
                    sensitivity TEXT NOT NULL DEFAULT 'internal',
                    valid_from TEXT,
                    valid_until TEXT,
                    expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    supersedes TEXT,
                    active INTEGER NOT NULL
                )
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(memories)")}
            additions = {
                "scope": "TEXT NOT NULL DEFAULT 'personal'",
                "scope_id": "TEXT",
                "sensitivity": "TEXT NOT NULL DEFAULT 'internal'",
                "valid_from": "TEXT",
                "valid_until": "TEXT",
                "expires_at": "TEXT",
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE memories ADD COLUMN {name} {definition}")
            try:
                connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(id UNINDEXED, content, tags)"
                )
                connection.execute("DELETE FROM memories_fts")
                connection.execute(
                    """
                    INSERT INTO memories_fts(id, content, tags)
                    SELECT id, content, tags_json FROM memories WHERE active = 1
                    """
                )
                self._fts_enabled = True
            except sqlite3.OperationalError:
                self._fts_enabled = False

    def health(self) -> bool:
        """Run a bounded integrity query against the persistent memory database."""
        try:
            with self._connect() as connection:
                result = connection.execute("PRAGMA quick_check(1)").fetchone()
            return result is not None and result[0] == "ok"
        except sqlite3.Error:
            return False

    def save(self, memory: MemoryRecord) -> MemoryRecord:
        data = memory.model_dump(mode="json")

        import json

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO memories (
                    id,
                    memory_type,
                    content,
                    source_json,
                    confidence,
                    importance,
                    scope,
                    scope_id,
                    sensitivity,
                    valid_from,
                    valid_until,
                    expires_at,
                    created_at,
                    updated_at,
                    tags_json,
                    supersedes,
                    active
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["id"],
                    data["memory_type"],
                    data["content"],
                    json.dumps(data["source"]),
                    data["confidence"],
                    data["importance"],
                    data["scope"],
                    data["scope_id"],
                    data["sensitivity"],
                    data["valid_from"],
                    data["valid_until"],
                    data["expires_at"],
                    data["created_at"],
                    data["updated_at"],
                    json.dumps(data["tags"]),
                    data["supersedes"],
                    int(data["active"]),
                ),
            )
            if self._fts_enabled:
                connection.execute("DELETE FROM memories_fts WHERE id = ?", (data["id"],))
                if data["active"]:
                    connection.execute(
                        "INSERT INTO memories_fts(id, content, tags) VALUES (?, ?, ?)",
                        (data["id"], data["content"], " ".join(data["tags"])),
                    )

        return memory

    def get(self, memory_id: UUID) -> MemoryRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE id = ?",
                (str(memory_id),),
            ).fetchone()

        if row is None:
            return None

        return self._row_to_memory(row)

    def list_active(
        self,
        limit: int = 100,
        *,
        scope: str | None = None,
        scope_id: str | None = None,
    ) -> list[MemoryRecord]:
        now = datetime.now(UTC).isoformat()
        where = [
            "active = 1",
            "(valid_from IS NULL OR valid_from <= ?)",
            "(valid_until IS NULL OR valid_until > ?)",
            "(expires_at IS NULL OR expires_at > ?)",
        ]
        parameters: list[object] = [now, now, now]
        if scope is not None:
            where.append("scope = ?")
            parameters.append(scope)
        if scope_id is not None:
            where.append("scope_id = ?")
            parameters.append(scope_id)
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM memories
                WHERE {" AND ".join(where)}
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()

        return [self._row_to_memory(row) for row in rows]

    def search_text(self, query: str, limit: int = 100) -> list[MemoryRecord]:
        """Use SQLite FTS5 when available, falling back to bounded active memory."""
        tokens = [token for token in re.findall(r"[a-zA-Z0-9]+", query) if len(token) > 2]
        if not tokens or not self._fts_enabled:
            return self.list_active(limit=limit)
        expression = " OR ".join(f'"{token}"' for token in tokens[:32])
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT memories.*
                FROM memories_fts
                JOIN memories ON memories.id = memories_fts.id
                WHERE memories_fts MATCH ? AND memories.active = 1
                  AND (memories.valid_from IS NULL OR memories.valid_from <= ?)
                  AND (memories.valid_until IS NULL OR memories.valid_until > ?)
                  AND (memories.expires_at IS NULL OR memories.expires_at > ?)
                ORDER BY bm25(memories_fts), memories.importance DESC
                LIMIT ?
                """,
                (expression, now, now, now, limit),
            ).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def deactivate(self, memory_id: UUID) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE memories
                SET active = 0
                WHERE id = ?
                """,
                (str(memory_id),),
            )
            if self._fts_enabled:
                connection.execute("DELETE FROM memories_fts WHERE id = ?", (str(memory_id),))

        return cursor.rowcount > 0

    def delete(self, memory_id: UUID) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM memories WHERE id = ?",
                (str(memory_id),),
            )
            if self._fts_enabled:
                connection.execute("DELETE FROM memories_fts WHERE id = ?", (str(memory_id),))

        return cursor.rowcount > 0

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> MemoryRecord:
        import json

        return MemoryRecord.model_validate(
            {
                "id": row["id"],
                "memory_type": row["memory_type"],
                "content": row["content"],
                "source": json.loads(row["source_json"]),
                "confidence": row["confidence"],
                "importance": row["importance"],
                "scope": row["scope"],
                "scope_id": row["scope_id"],
                "sensitivity": row["sensitivity"],
                "valid_from": row["valid_from"],
                "valid_until": row["valid_until"],
                "expires_at": row["expires_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "tags": json.loads(row["tags_json"]),
                "supersedes": row["supersedes"],
                "active": bool(row["active"]),
            }
        )
