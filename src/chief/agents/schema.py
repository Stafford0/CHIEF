from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlanStatus(str, Enum):
    READY = "ready"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BUDGET_EXCEEDED = "budget_exceeded"


class PlannedStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(min_length=1, max_length=2_000)
    expected_outcome: str = Field(min_length=1, max_length=2_000)
    depends_on: list[str] = Field(default_factory=list, max_length=20)


class ExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID = Field(default_factory=uuid4)
    objective: str = Field(min_length=1, max_length=10_000)
    steps: list[PlannedStep] = Field(min_length=1, max_length=20)
    max_duration_seconds: float = Field(default=120, gt=0, le=3_600)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_dependencies(self) -> "ExecutionPlan":
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("Plan step IDs must be unique.")
        seen: set[str] = set()
        for step in self.steps:
            unknown = set(step.depends_on) - seen
            if unknown:
                raise ValueError(
                    f"Step {step.id!r} depends on unavailable prior steps: {sorted(unknown)}"
                )
            seen.add(step.id)
        return self


class StepOutcome(BaseModel):
    step_id: str
    tool_name: str
    success: bool
    content: str
    error: str | None = None
    argument_digest: str
    duration_ms: float = Field(ge=0)


class PlanOutcome(BaseModel):
    plan_id: UUID
    status: PlanStatus
    steps: list[StepOutcome]
    pending_step_id: str | None = None
    pending_argument_digest: str | None = None
    error: str | None = None
