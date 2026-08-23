from datetime import UTC, date, datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class WorkStatus(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class WorkPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Goal(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=10_000)
    status: WorkStatus = WorkStatus.TODO
    target_date: date | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("title", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class Task(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    goal_id: UUID | None = None
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=10_000)
    status: WorkStatus = WorkStatus.TODO
    priority: WorkPriority = WorkPriority.MEDIUM
    due_at: datetime | None = None
    blocked_reason: str | None = Field(default=None, max_length=2_000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    @field_validator("title", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class BriefingItem(BaseModel):
    kind: str
    title: str
    reason: str
    urgency: int = Field(ge=0, le=100)
    task_id: UUID | None = None
    goal_id: UUID | None = None


class ExecutiveBriefing(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    summary: str
    items: list[BriefingItem]
    counts: dict[str, int]
