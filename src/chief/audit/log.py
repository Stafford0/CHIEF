from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class AuditEvent:
    """Immutable record of a CHIEF tool execution attempt."""

    tool_name: str
    approved: bool
    decision: str
    success: bool
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AuditLog:
    """In-memory audit log used by the execution gateway.

    Persistence can be swapped in later without changing the registry API.
    """

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self._events.append(event)

    def events(self) -> list[AuditEvent]:
        return list(self._events)

    def latest(self) -> AuditEvent | None:
        if not self._events:
            return None
        return self._events[-1]

    def count(self) -> int:
        return len(self._events)
