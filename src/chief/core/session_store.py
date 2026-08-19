from uuid import UUID

from chief.core.session import ConversationSession


class SessionStore:
    """In-memory store for CHIEF conversation sessions."""

    def __init__(self) -> None:
        self._sessions: dict[UUID, ConversationSession] = {}

    def create(self) -> ConversationSession:
        """Create and register a new conversation session."""

        session = ConversationSession()
        self._sessions[session.id] = session

        return session

    def get(
        self,
        session_id: UUID,
    ) -> ConversationSession | None:
        """Retrieve a session by ID."""

        return self._sessions.get(session_id)

    def get_or_create(
        self,
        session_id: UUID | None = None,
    ) -> ConversationSession:
        """Retrieve an existing session or create a new one."""

        if session_id is None:
            return self.create()

        session = self.get(session_id)

        if session is None:
            raise KeyError(
                f"Conversation session {session_id} does not exist."
            )

        return session

    def delete(
        self,
        session_id: UUID,
    ) -> bool:
        """Delete a conversation session."""

        if session_id not in self._sessions:
            return False

        del self._sessions[session_id]
        return True

    def count(self) -> int:
        """Return the number of active sessions."""

        return len(self._sessions)
