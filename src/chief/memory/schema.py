from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class MemoryType(str, Enum):
    """The four primary categories of long-term CHIEF memory."""

    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    DECISION = "decision"
    PROCEDURAL = "procedural"


class MemoryScope(str, Enum):
    PERSONAL = "personal"
    ORGANIZATION = "organization"
    PROJECT = "project"
    SESSION = "session"


class MemorySensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class MemorySource(BaseModel):
    """Where a memory came from."""

    source_type: str
    source_id: str | None = None
    description: str | None = None
    uri: str | None = None
    observed_at: datetime | None = None
    retrieved_at: datetime | None = None
    content_digest: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")


class MemoryRecord(BaseModel):
    """Canonical representation of a CHIEF long-term memory."""

    id: UUID = Field(default_factory=uuid4)

    memory_type: MemoryType
    content: str = Field(min_length=1)

    source: MemorySource

    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    scope: MemoryScope = MemoryScope.PERSONAL
    scope_id: str | None = Field(default=None, max_length=256)
    sensitivity: MemorySensitivity = MemorySensitivity.INTERNAL
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    expires_at: datetime | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    tags: list[str] = Field(default_factory=list, max_length=32)

    supersedes: UUID | None = None
    active: bool = True

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        value = value.strip()
        if len(value) > 20_000:
            raise ValueError("Memory content is too large.")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            tag = value.strip().casefold()
            if tag and tag not in normalized:
                normalized.append(tag[:64])
        return normalized

    @model_validator(mode="after")
    def validate_temporal_window(self) -> "MemoryRecord":
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError("Memory valid_until must be after valid_from.")
        return self
