from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MemoryType(str, Enum):
    """The four primary categories of long-term CHIEF memory."""

    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    DECISION = "decision"
    PROCEDURAL = "procedural"


class MemorySource(BaseModel):
    """Where a memory came from."""

    source_type: str
    source_id: str | None = None
    description: str | None = None


class MemoryRecord(BaseModel):
    """Canonical representation of a CHIEF long-term memory."""

    id: UUID = Field(default_factory=uuid4)

    memory_type: MemoryType
    content: str = Field(min_length=1)

    source: MemorySource

    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    tags: list[str] = Field(default_factory=list)

    supersedes: UUID | None = None
    active: bool = True