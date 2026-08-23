from threading import RLock
from uuid import UUID

from chief.core.session import ConversationSession, PendingToolCall


class SessionStore:
    """In-memory store for CHIEF conversation sessions."""

    def __init__(self) -> None:
        self._sessions: dict[UUID, ConversationSession] = {}
        self._lock = RLock()

    def create(self, owner_id: str = "local") -> ConversationSession:
        """Create and register a new conversation session."""

        session = ConversationSession(owner_id=owner_id)
        with self._lock:
            self._sessions[session.id] = session

        return session

    def get(
        self,
        session_id: UUID,
    ) -> ConversationSession | None:
        """Retrieve a session by ID."""

        with self._lock:
            return self._sessions.get(session_id)

    def get_or_create(
        self,
        session_id: UUID | None = None,
        *,
        owner_id: str = "local",
    ) -> ConversationSession:
        """Retrieve an existing session or create a new one."""

        if session_id is None:
            return self.create(owner_id)

        session = self.get(session_id)

        if session is None:
            raise KeyError(f"Conversation session {session_id} does not exist.")
        if session.owner_id != owner_id:
            raise PermissionError("Conversation session belongs to another operator.")

        return session

    def delete(
        self,
        session_id: UUID,
    ) -> bool:
        """Delete a conversation session."""

        with self._lock:
            if session_id not in self._sessions:
                return False
            del self._sessions[session_id]
            return True

    def count(self, owner_id: str | None = None) -> int:
        """Return the number of active sessions."""

        with self._lock:
            if owner_id is None:
                return len(self._sessions)
            return sum(session.owner_id == owner_id for session in self._sessions.values())

    def pending_tool_calls(self, owner_id: str = "local") -> list[dict[str, str]]:
        """Return live approval-gated tool calls waiting in active sessions."""

        pending: list[dict[str, str]] = []
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            if session.owner_id != owner_id:
                continue
            call = session.peek_pending_tool()
            if call is None:
                continue
            pending.append(
                {
                    "name": call.call.description,
                    "tool": call.call.tool_name,
                    "status": "awaiting approval",
                    "expires_at": call.expires_at.isoformat(),
                }
            )
        return pending

    def take_pending_tool(
        self,
        session_id: UUID,
        *,
        owner_id: str = "local",
    ) -> PendingToolCall | None:
        session = self.get_or_create(session_id, owner_id=owner_id)
        return session.take_pending_tool()

    def summaries(self, owner_id: str = "local") -> list[dict[str, str | int]]:
        """Return lightweight live summaries for active sessions."""

        with self._lock:
            sessions = list(self._sessions.values())
        return [
            {
                "messages": len(session.messages),
                "status": (
                    "awaiting approval" if session.peek_pending_tool() is not None else "active"
                ),
                "updated_at": session.updated_at.isoformat(),
            }
            for session in sorted(
                (item for item in sessions if item.owner_id == owner_id),
                key=lambda item: item.updated_at,
                reverse=True,
            )
        ]
