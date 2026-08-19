import sqlite3
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
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    importance REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    supersedes TEXT,
                    active INTEGER NOT NULL
                )
                """
            )

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
                    created_at,
                    updated_at,
                    tags_json,
                    supersedes,
                    active
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["id"],
                    data["memory_type"],
                    data["content"],
                    json.dumps(data["source"]),
                    data["confidence"],
                    data["importance"],
                    data["created_at"],
                    data["updated_at"],
                    json.dumps(data["tags"]),
                    data["supersedes"],
                    int(data["active"]),
                ),
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

    def list_active(self, limit: int = 100) -> list[MemoryRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM memories
                WHERE active = 1
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
                """,
                (limit,),
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

        return cursor.rowcount > 0

    def delete(self, memory_id: UUID) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM memories WHERE id = ?",
                (str(memory_id),),
            )

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
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "tags": json.loads(row["tags_json"]),
                "supersedes": row["supersedes"],
                "active": bool(row["active"]),
            }
        )