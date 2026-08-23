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
            raise KeyError(f"Conversation session {session_id} does not exist.")

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

    def pending_tool_calls(self) -> list[dict[str, str]]:
        """Return live approval-gated tool calls waiting in active sessions."""

        pending: list[dict[str, str]] = []
        for session in self._sessions.values():
            call = session.pending_tool_call
            if call is None:
                continue
            pending.append(
                {
                    "name": call.call.description,
                    "tool": call.call.tool_name,
                    "status": "awaiting approval",
                    "session_id": str(session.id),
                    "approval_digest": call.digest[:12],
                    "expires_at": call.expires_at.isoformat(),
                }
            )
        return pending

    def summaries(self) -> list[dict[str, str | int]]:
        """Return lightweight live summaries for active sessions."""

        return [
            {
                "session_id": str(session.id),
                "messages": len(session.messages),
                "status": (
                    "awaiting approval" if session.pending_tool_call is not None else "active"
                ),
                "updated_at": session.updated_at.isoformat(),
            }
            for session in sorted(
                self._sessions.values(),
                key=lambda item: item.updated_at,
                reverse=True,
            )
        ]
