from abc import ABC, abstractmethod
from uuid import UUID

from chief.memory.schema import MemoryRecord


class MemoryStore(ABC):
    """Storage contract for CHIEF long-term memory."""

    @abstractmethod
    def save(self, memory: MemoryRecord) -> MemoryRecord:
        """Persist a memory record."""

    @abstractmethod
    def get(self, memory_id: UUID) -> MemoryRecord | None:
        """Retrieve one memory by ID."""

    @abstractmethod
    def list_active(self, limit: int = 100) -> list[MemoryRecord]:
        """Return active memories."""

    @abstractmethod
    def deactivate(self, memory_id: UUID) -> bool:
        """Mark a memory inactive."""

    @abstractmethod
    def delete(self, memory_id: UUID) -> bool:
        """Permanently delete a memory."""