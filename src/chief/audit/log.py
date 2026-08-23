from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class AuditEvent:
    """Immutable record of a CHIEF tool execution attempt."""

    tool_name: str
    approved: bool
    decision: str
    success: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    request_id: str | None = None
    actor_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    step_id: str | None = None
    proposal_id: str | None = None
    sequence: int | None = None
    previous_hash: str | None = None
    event_hash: str | None = None


class AuditLog:
    """In-memory audit log used by the execution gateway.

    Persistence can be swapped in later without changing the registry API.
    """

    def __init__(self, max_events: int = 10_000) -> None:
        self._events: list[AuditEvent] = []
        self._max_events = max_events
        self._lock = Lock()

    def record(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)
            if len(self._events) > self._max_events:
                del self._events[: len(self._events) - self._max_events]

    def events(self) -> list[AuditEvent]:
        with self._lock:
            return list(self._events)

    def latest(self) -> AuditEvent | None:
        if not self._events:
            return None
        return self._events[-1]

    def count(self) -> int:
        return len(self._events)
