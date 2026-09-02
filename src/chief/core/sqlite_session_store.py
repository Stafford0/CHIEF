from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import UUID

from chief.core.session import ConversationSession, PendingToolCall, SessionMessage
from chief.core.tool_planner import PlannedToolCall


def _is_narrower(original: object, proposed: object) -> bool:
    """Allow only removal of dictionary fields; existing values cannot change."""

    if isinstance(original, dict) and isinstance(proposed, dict):
        if not set(proposed).issubset(original):
            return False
        return all(_is_narrower(original[key], value) for key, value in proposed.items())
    return original == proposed


class SQLiteSessionStore:
    """Restart-safe sessions with owner-scoped, atomically consumed approvals."""

    def __init__(self, database_path: str | Path = "data/chief.db") -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[UUID, ConversationSession] = {}
        self._lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversation_sessions (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    max_messages INTEGER NOT NULL, max_message_chars INTEGER NOT NULL,
                    ultron_silenced INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS ix_conversation_sessions_owner
                    ON conversation_sessions(owner_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    session_id TEXT NOT NULL REFERENCES conversation_sessions(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(session_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS pending_tool_proposals (
                    session_id TEXT PRIMARY KEY REFERENCES conversation_sessions(id) ON DELETE CASCADE,
                    proposal_id TEXT NOT NULL,
                    intent TEXT NOT NULL, tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL, description TEXT NOT NULL,
                    created_at TEXT NOT NULL, expires_at TEXT NOT NULL, digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS consumed_tool_proposals (
                    proposal_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                    consumed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tool_proposal_decisions (
                    proposal_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    decided_at TEXT NOT NULL,
                    replacement_proposal_id TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_tool_proposal_decisions_owner
                    ON tool_proposal_decisions(owner_id, decided_at DESC);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(pending_tool_proposals)")
            }
            if "proposal_id" not in columns:
                connection.execute("ALTER TABLE pending_tool_proposals ADD COLUMN proposal_id TEXT")
                connection.execute(
                    """
                    UPDATE pending_tool_proposals SET proposal_id = lower(hex(randomblob(16)))
                    WHERE proposal_id IS NULL
                    """
                )
            session_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(conversation_sessions)")
            }
            if "ultron_silenced" not in session_columns:
                connection.execute(
                    "ALTER TABLE conversation_sessions "
                    "ADD COLUMN ultron_silenced INTEGER NOT NULL DEFAULT 0"
                )

    def save(self, session: ConversationSession) -> ConversationSession:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO conversation_sessions(
                    id, owner_id, created_at, updated_at, max_messages,
                    max_message_chars, ultron_silenced
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET owner_id=excluded.owner_id,
                    updated_at=excluded.updated_at, max_messages=excluded.max_messages,
                    max_message_chars=excluded.max_message_chars,
                    ultron_silenced=excluded.ultron_silenced
                """,
                (
                    str(session.id),
                    session.owner_id,
                    session.created_at.isoformat(),
                    session.updated_at.isoformat(),
                    session.max_messages,
                    session.max_message_chars,
                    int(session.ultron_silenced),
                ),
            )
            connection.execute(
                "DELETE FROM conversation_messages WHERE session_id = ?", (str(session.id),)
            )
            connection.executemany(
                """
                INSERT INTO conversation_messages(session_id, ordinal, role, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(session.id),
                        index,
                        message.role,
                        message.content,
                        message.created_at.isoformat(),
                    )
                    for index, message in enumerate(session.messages)
                ],
            )
            pending = session.peek_pending_tool()
            if pending is not None:
                consumed = connection.execute(
                    "SELECT 1 FROM consumed_tool_proposals WHERE proposal_id = ?",
                    (str(pending.id),),
                ).fetchone()
                if consumed is None:
                    connection.execute(
                        "DELETE FROM pending_tool_proposals WHERE session_id = ?",
                        (str(session.id),),
                    )
                    connection.execute(
                        """
                        INSERT INTO pending_tool_proposals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(session.id),
                            str(pending.id),
                            pending.call.intent,
                            pending.call.tool_name,
                            json.dumps(pending.call.arguments, sort_keys=True),
                            pending.call.description,
                            pending.created_at.isoformat(),
                            pending.expires_at.isoformat(),
                            pending.digest,
                        ),
                    )
        with self._lock:
            self._sessions[session.id] = session
        return session

    def create(self, owner_id: str = "local") -> ConversationSession:
        session = ConversationSession(owner_id=owner_id)
        session.set_change_callback(self.save)
        return self.save(session)

    def get(self, session_id: UUID) -> ConversationSession | None:
        with self._lock:
            cached = self._sessions.get(session_id)
        if cached is not None:
            return cached
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM conversation_sessions WHERE id = ?", (str(session_id),)
            ).fetchone()
            if row is None:
                return None
            message_rows = connection.execute(
                """
                SELECT role, content, created_at FROM conversation_messages
                WHERE session_id = ? ORDER BY ordinal
                """,
                (str(session_id),),
            ).fetchall()
            pending_row = connection.execute(
                "SELECT * FROM pending_tool_proposals WHERE session_id = ?", (str(session_id),)
            ).fetchone()
        session = ConversationSession(
            id=UUID(row["id"]),
            owner_id=row["owner_id"],
            messages=[
                SessionMessage(
                    role=item["role"],
                    content=item["content"],
                    created_at=datetime.fromisoformat(item["created_at"]),
                )
                for item in message_rows
            ],
            pending_tool_call=self._pending(pending_row) if pending_row else None,
            ultron_silenced=bool(row["ultron_silenced"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            max_messages=int(row["max_messages"]),
            max_message_chars=int(row["max_message_chars"]),
        )
        session.set_change_callback(self.save)
        with self._lock:
            self._sessions[session.id] = session
        return session

    def get_or_create(
        self,
        session_id: UUID | None = None,
        *,
        owner_id: str = "local",
    ) -> ConversationSession:
        if session_id is None:
            return self.create(owner_id)
        session = self.get(session_id)
        if session is None:
            raise KeyError(f"Conversation session {session_id} does not exist.")
        if session.owner_id != owner_id:
            raise PermissionError("Conversation session belongs to another operator.")
        return session

    def delete(self, session_id: UUID) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM conversation_sessions WHERE id = ?", (str(session_id),)
            )
        with self._lock:
            self._sessions.pop(session_id, None)
        return cursor.rowcount > 0

    def count(self, owner_id: str | None = None) -> int:
        query = "SELECT COUNT(*) AS count FROM conversation_sessions"
        parameters: tuple[object, ...] = ()
        if owner_id is not None:
            query += " WHERE owner_id = ?"
            parameters = (owner_id,)
        with self._connect() as connection:
            row = connection.execute(query, parameters).fetchone()
        return int(row["count"])

    @staticmethod
    def _record_decision(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        owner_id: str,
        decision: str,
        replacement_proposal_id: UUID | None = None,
    ) -> None:
        decided_at = datetime.now(UTC).isoformat()
        connection.execute(
            """
            INSERT OR IGNORE INTO consumed_tool_proposals(proposal_id, session_id, consumed_at)
            VALUES (?, ?, ?)
            """,
            (row["proposal_id"], row["session_id"], decided_at),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO tool_proposal_decisions(
                proposal_id, session_id, owner_id, decision, decided_at, replacement_proposal_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                row["proposal_id"],
                row["session_id"],
                owner_id,
                decision,
                decided_at,
                str(replacement_proposal_id) if replacement_proposal_id else None,
            ),
        )

    def take_pending_tool(
        self,
        session_id: UUID,
        *,
        owner_id: str = "local",
        proposal_id: UUID | None = None,
        decision: str = "approved",
    ) -> PendingToolCall | None:
        session = self.get_or_create(session_id, owner_id=owner_id)
        query = """
            SELECT proposal.* FROM pending_tool_proposals AS proposal
            JOIN conversation_sessions AS session ON session.id = proposal.session_id
            WHERE proposal.session_id = ? AND session.owner_id = ?
        """
        parameters: list[object] = [str(session_id), owner_id]
        if proposal_id is not None:
            query += " AND proposal.proposal_id = ?"
            parameters.append(str(proposal_id))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(query, parameters).fetchone()
            if row is None:
                return None
            self._record_decision(connection, row, owner_id=owner_id, decision=decision)
            connection.execute(
                "DELETE FROM pending_tool_proposals WHERE session_id = ? AND proposal_id = ?",
                (str(session_id), row["proposal_id"]),
            )
        pending = self._pending(row)
        with session._lock:
            if session.pending_tool_call is not None and session.pending_tool_call.id == pending.id:
                session.pending_tool_call = None
            session.updated_at = datetime.now(UTC)
        return pending

    def reject_pending_tool(
        self,
        session_id: UUID,
        proposal_id: UUID,
        *,
        owner_id: str = "local",
    ) -> PendingToolCall | None:
        return self.take_pending_tool(
            session_id,
            owner_id=owner_id,
            proposal_id=proposal_id,
            decision="rejected",
        )

    def narrow_pending_tool(
        self,
        session_id: UUID,
        proposal_id: UUID,
        arguments: dict[str, object],
        *,
        owner_id: str = "local",
    ) -> PendingToolCall | None:
        session = self.get_or_create(session_id, owner_id=owner_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT proposal.* FROM pending_tool_proposals AS proposal
                JOIN conversation_sessions AS session ON session.id = proposal.session_id
                WHERE proposal.session_id = ? AND proposal.proposal_id = ? AND session.owner_id = ?
                """,
                (str(session_id), str(proposal_id), owner_id),
            ).fetchone()
            if row is None:
                return None
            original_arguments = json.loads(row["arguments_json"])
            if arguments == original_arguments or not _is_narrower(original_arguments, arguments):
                raise ValueError(
                    "Narrowed arguments may only remove existing fields; values cannot be changed or added."
                )
            replacement = PendingToolCall(
                call=PlannedToolCall(
                    intent=row["intent"],
                    tool_name=row["tool_name"],
                    arguments=arguments,
                    description=row["description"],
                )
            )
            self._record_decision(
                connection,
                row,
                owner_id=owner_id,
                decision="narrowed",
                replacement_proposal_id=replacement.id,
            )
            connection.execute(
                "DELETE FROM pending_tool_proposals WHERE session_id = ? AND proposal_id = ?",
                (str(session_id), str(proposal_id)),
            )
            connection.execute(
                """
                INSERT INTO pending_tool_proposals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(session_id),
                    str(replacement.id),
                    replacement.call.intent,
                    replacement.call.tool_name,
                    json.dumps(replacement.call.arguments, sort_keys=True),
                    replacement.call.description,
                    replacement.created_at.isoformat(),
                    replacement.expires_at.isoformat(),
                    replacement.digest,
                ),
            )
        with session._lock:
            session.pending_tool_call = replacement
            session.updated_at = datetime.now(UTC)
        return replacement

    def pending_tool_records(self, owner_id: str = "local") -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT proposal.* FROM pending_tool_proposals AS proposal
                JOIN conversation_sessions AS session ON session.id = proposal.session_id
                WHERE session.owner_id = ? ORDER BY proposal.expires_at
                """,
                (owner_id,),
            ).fetchall()
        return [
            {
                "session_id": row["session_id"],
                "proposal_id": row["proposal_id"],
                "intent": row["intent"],
                "tool_name": row["tool_name"],
                "arguments": json.loads(row["arguments_json"]),
                "description": row["description"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "digest": row["digest"],
            }
            for row in rows
        ]

    def pending_tool_calls(self, owner_id: str = "local") -> list[dict[str, str]]:
        return [
            {
                "name": str(row["description"]),
                "tool": str(row["tool_name"]),
                "status": "awaiting approval",
                "expires_at": str(row["expires_at"]),
            }
            for row in self.pending_tool_records(owner_id)
        ]

    def proposal_decisions(self, owner_id: str = "local", *, limit: int = 100) -> list[dict[str, str | None]]:
        if not 1 <= limit <= 1000:
            raise ValueError("decision limit must be between 1 and 1000")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT proposal_id, session_id, decision, decided_at, replacement_proposal_id
                FROM tool_proposal_decisions
                WHERE owner_id = ? ORDER BY decided_at DESC LIMIT ?
                """,
                (owner_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def summaries(self, owner_id: str = "local") -> list[dict[str, str | int]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT session.id, session.updated_at, COUNT(message.ordinal) AS messages,
                    CASE WHEN proposal.session_id IS NULL THEN 'active' ELSE 'awaiting approval' END AS status
                FROM conversation_sessions AS session
                LEFT JOIN conversation_messages AS message ON message.session_id = session.id
                LEFT JOIN pending_tool_proposals AS proposal ON proposal.session_id = session.id
                WHERE session.owner_id = ? GROUP BY session.id ORDER BY session.updated_at DESC
                """,
                (owner_id,),
            ).fetchall()
        return [
            {
                "messages": int(row["messages"]),
                "status": row["status"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _pending(row: sqlite3.Row) -> PendingToolCall:
        return PendingToolCall(
            call=PlannedToolCall(
                intent=row["intent"],
                tool_name=row["tool_name"],
                arguments=json.loads(row["arguments_json"]),
                description=row["description"],
            ),
            id=UUID(row["proposal_id"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
        )
