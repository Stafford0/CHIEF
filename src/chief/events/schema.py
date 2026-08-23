from datetime import UTC, datetime, time
from enum import Enum
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, model_validator


class ScheduleCadence(str, Enum):
    ONCE = "once"
    INTERVAL = "interval"
    DAILY = "daily"


class ScheduleStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class EventStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class Schedule(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=240)
    event_type: str = Field(min_length=1, max_length=128)
    payload: dict = Field(default_factory=dict)
    cadence: ScheduleCadence
    timezone: str = "UTC"
    run_at: datetime | None = None
    interval_seconds: int | None = Field(default=None, ge=1, le=31_536_000)
    daily_time: time | None = None
    next_run_at: datetime | None = None
    status: ScheduleStatus = ScheduleStatus.ACTIVE
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    lease_owner: str | None = None
    lease_until: datetime | None = None

    @model_validator(mode="after")
    def validate_cadence(self) -> "Schedule":
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown schedule timezone: {self.timezone}") from exc
        required = {
            ScheduleCadence.ONCE: self.run_at,
            ScheduleCadence.INTERVAL: self.interval_seconds,
            ScheduleCadence.DAILY: self.daily_time,
        }
        if required[self.cadence] is None:
            raise ValueError(f"{self.cadence.value} schedule is missing its cadence value.")
        return self


class Event(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    event_type: str = Field(min_length=1, max_length=128)
    source: str = Field(default="scheduler", min_length=1, max_length=128)
    payload: dict = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=512)
    correlation_id: str | None = Field(default=None, max_length=256)
    status: EventStatus = EventStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=5, ge=1, le=20)
    available_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    lease_owner: str | None = None
    lease_until: datetime | None = None
    last_error: str | None = Field(default=None, max_length=20_000)
