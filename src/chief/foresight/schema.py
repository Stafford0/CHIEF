from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class SignalKind(str, Enum):
    RISK = "risk"
    OPPORTUNITY = "opportunity"
    ANOMALY = "anomaly"
    TREND = "trend"


class SignalStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class AssumptionStatus(str, Enum):
    UNTESTED = "untested"
    VALIDATED = "validated"
    CHALLENGED = "challenged"
    INVALIDATED = "invalidated"


class KPIDirection(str, Enum):
    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"
    TARGET_RANGE = "target_range"


class ForesightSignal(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    kind: SignalKind
    title: str = Field(min_length=1, max_length=240)
    summary: str = Field(min_length=1, max_length=10_000)
    impact: int = Field(ge=1, le=5)
    urgency: int = Field(ge=1, le=5)
    confidence: float = Field(ge=0, le=1)
    reversibility: int = Field(
        default=3,
        ge=1,
        le=5,
        description="1 is hard to reverse; 5 is easily reversible.",
    )
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    linked_goal_id: UUID | None = None
    status: SignalStatus = SignalStatus.OPEN
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def require_evidence_for_confidence(self) -> "ForesightSignal":
        if self.confidence >= 0.75 and not self.evidence_refs:
            raise ValueError("High-confidence signals require at least one evidence reference.")
        return self


class SignalScore(BaseModel):
    signal_id: UUID
    score: float = Field(ge=0, le=100)
    breakdown: dict[str, float]
    rationale: str


class Assumption(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    statement: str = Field(min_length=1, max_length=10_000)
    category: str = Field(default="general", min_length=1, max_length=128)
    confidence: float = Field(default=0.5, ge=0, le=1)
    status: AssumptionStatus = AssumptionStatus.UNTESTED
    owner: str | None = Field(default=None, max_length=256)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    review_due_at: datetime | None = None
    last_validated_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class KPI(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=240)
    unit: str = Field(default="count", min_length=1, max_length=64)
    direction: KPIDirection
    current_value: float
    target_value: float | None = None
    target_min: float | None = None
    target_max: float | None = None
    baseline_value: float | None = None
    owner: str | None = Field(default=None, max_length=256)
    source_ref: str = Field(min_length=1, max_length=2_000)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_target(self) -> "KPI":
        if self.direction == KPIDirection.TARGET_RANGE:
            if self.target_min is None or self.target_max is None:
                raise ValueError("Range KPI requires target_min and target_max.")
            if self.target_min > self.target_max:
                raise ValueError("KPI target_min cannot exceed target_max.")
        elif self.target_value is None:
            raise ValueError("Directional KPI requires target_value.")
        return self
