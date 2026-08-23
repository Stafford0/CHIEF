from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AttemptStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABANDONED = "abandoned"
    CANCELLED = "cancelled"


class VerificationStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"


class RunEventType(str, Enum):
    RUN_CREATED = "run_created"
    RUN_STARTED = "run_started"
    RUN_SUCCEEDED = "run_succeeded"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"
    STEP_CLAIMED = "step_claimed"
    STEP_SUCCEEDED = "step_succeeded"
    STEP_FAILED = "step_failed"
    STEP_RETRY_SCHEDULED = "step_retry_scheduled"
    STEP_LEASE_EXPIRED = "step_lease_expired"


class StepSpec(BaseModel):
    """Immutable description of one sequential, idempotent run step."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=256)
    input_data: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = Field(default=3, ge=1, le=10)
    verification_required: bool = False

    @field_validator("action", "idempotency_key")
    @classmethod
    def strip_identifiers(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Step identifiers cannot be blank.")
        return value


class RunRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    idempotency_key: str
    correlation_id: str
    status: RunStatus
    input_data: dict[str, Any]
    input_digest: str
    plan_digest: str
    result_data: dict[str, Any] | None = None
    result_digest: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    version: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None


class StepRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    run_id: UUID
    ordinal: int
    action: str
    idempotency_key: str
    status: StepStatus
    input_data: dict[str, Any]
    input_digest: str
    result_data: dict[str, Any] | None = None
    result_digest: str | None = None
    verification_required: bool
    verification_status: VerificationStatus
    max_attempts: int
    attempt_count: int
    next_attempt_at: datetime | None = None
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AttemptRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    run_id: UUID
    step_id: UUID
    attempt_number: int
    worker_id: str
    lease_token: str
    status: AttemptStatus
    input_digest: str
    result_digest: str | None = None
    verification_status: VerificationStatus
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class RunEventRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int
    id: UUID
    run_id: UUID
    step_id: UUID | None = None
    attempt_id: UUID | None = None
    event_type: RunEventType
    correlation_id: str
    payload: dict[str, Any]
    created_at: datetime


class CheckpointRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int
    id: UUID
    run_id: UUID
    step_id: UUID
    attempt_id: UUID
    data: dict[str, Any]
    data_digest: str
    verification_status: VerificationStatus
    created_at: datetime


class StepLease(BaseModel):
    """A time-bounded, single-worker claim over an exact step attempt."""

    model_config = ConfigDict(frozen=True)

    run: RunRecord
    step: StepRecord
    attempt: AttemptRecord
    lease_token: str
    lease_expires_at: datetime
