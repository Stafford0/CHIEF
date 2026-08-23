"""Typed, source-aware decision records."""

from datetime import UTC, datetime
from enum import Enum
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _DecisionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _HasID(Protocol):
    id: UUID


class DecisionStatus(str, Enum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    DECIDED = "decided"
    DEFERRED = "deferred"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class EvidenceStance(str, Enum):
    SUPPORTS = "supports"
    OPPOSES = "opposes"
    NEUTRAL = "neutral"


class AssumptionStatus(str, Enum):
    UNTESTED = "untested"
    SUPPORTED = "supported"
    REJECTED = "rejected"


class RiskStatus(str, Enum):
    OPEN = "open"
    MITIGATED = "mitigated"
    ACCEPTED = "accepted"
    CLOSED = "closed"


class Provenance(_DecisionModel):
    """Where an input came from and when CHIEF captured it."""

    source_type: str = Field(min_length=1, max_length=100)
    source_id: str | None = Field(default=None, max_length=500)
    source_uri: str | None = Field(default=None, max_length=2_000)
    description: str | None = Field(default=None, max_length=2_000)
    content_hash: str | None = Field(default=None, max_length=128)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DecisionCriterion(_DecisionModel):
    """One independently weighted dimension used to compare options."""

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)
    weight: float = Field(default=1.0, gt=0.0)
    provenance: list[Provenance] = Field(default_factory=list)


class OptionCriterionScore(_DecisionModel):
    """A normalized desirability score for one option and criterion."""

    criterion_id: UUID
    score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=4_000)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence_ids: list[UUID] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)


class DecisionOption(_DecisionModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4_000)
    criterion_scores: list[OptionCriterionScore] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)
    drawbacks: list[str] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)

    @model_validator(mode="after")
    def scores_are_unique(self) -> "DecisionOption":
        score_ids = [score.criterion_id for score in self.criterion_scores]
        if len(score_ids) != len(set(score_ids)):
            raise ValueError("An option cannot score the same criterion more than once.")
        return self


class DecisionEvidence(_DecisionModel):
    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=20_000)
    provenance: Provenance
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    stance: EvidenceStance = EvidenceStance.NEUTRAL
    related_option_ids: list[UUID] = Field(default_factory=list)
    related_criterion_ids: list[UUID] = Field(default_factory=list)


class DecisionAssumption(_DecisionModel):
    id: UUID = Field(default_factory=uuid4)
    statement: str = Field(min_length=1, max_length=4_000)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    status: AssumptionStatus = AssumptionStatus.UNTESTED
    validation_plan: str | None = Field(default=None, max_length=4_000)
    evidence_ids: list[UUID] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)


class DecisionRisk(_DecisionModel):
    id: UUID = Field(default_factory=uuid4)
    description: str = Field(min_length=1, max_length=4_000)
    probability: float = Field(ge=0.0, le=1.0)
    impact: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    status: RiskStatus = RiskStatus.OPEN
    mitigation: str | None = Field(default=None, max_length=4_000)
    option_id: UUID | None = None
    criterion_id: UUID | None = None
    evidence_ids: list[UUID] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)

    @property
    def exposure(self) -> float:
        """Return probability multiplied by impact without hiding uncertainty."""

        return self.probability * self.impact


class DecisionRecord(_DecisionModel):
    """A complete, inspectable business decision and its supporting inputs."""

    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=300)
    question: str = Field(min_length=1, max_length=4_000)
    context: str | None = Field(default=None, max_length=20_000)
    status: DecisionStatus = DecisionStatus.DRAFT
    owner: str | None = Field(default=None, max_length=300)
    constraints: list[str] = Field(default_factory=list)
    criteria: list[DecisionCriterion] = Field(min_length=1)
    options: list[DecisionOption] = Field(min_length=1)
    evidence: list[DecisionEvidence] = Field(default_factory=list)
    assumptions: list[DecisionAssumption] = Field(default_factory=list)
    risks: list[DecisionRisk] = Field(default_factory=list)
    recommended_option_id: UUID | None = None
    recommendation_rationale: str | None = Field(default=None, max_length=10_000)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    provenance: list[Provenance] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    due_at: datetime | None = None
    decided_at: datetime | None = None
    review_at: datetime | None = None

    @field_validator("constraints")
    @classmethod
    def normalize_constraints(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if item and item not in normalized:
                normalized.append(item)
        return normalized

    @model_validator(mode="after")
    def references_are_consistent(self) -> "DecisionRecord":
        criterion_ids = self._unique_ids("criterion", self.criteria)
        option_ids = self._unique_ids("option", self.options)
        evidence_ids = self._unique_ids("evidence", self.evidence)
        self._unique_ids("assumption", self.assumptions)
        self._unique_ids("risk", self.risks)

        criterion_names = [criterion.name.casefold() for criterion in self.criteria]
        if len(criterion_names) != len(set(criterion_names)):
            raise ValueError("Decision criterion names must be unique.")
        option_names = [option.name.casefold() for option in self.options]
        if len(option_names) != len(set(option_names)):
            raise ValueError("Decision option names must be unique.")

        if self.recommended_option_id is not None and self.recommended_option_id not in option_ids:
            raise ValueError("The recommended option must belong to the decision.")
        if self.status == DecisionStatus.DECIDED and self.recommended_option_id is None:
            raise ValueError("A decided record must identify its recommended option.")

        for option in self.options:
            for score in option.criterion_scores:
                if score.criterion_id not in criterion_ids:
                    raise ValueError("An option score references an unknown criterion.")
                if not set(score.evidence_ids).issubset(evidence_ids):
                    raise ValueError("An option score references unknown evidence.")

        for item in self.evidence:
            if not set(item.related_option_ids).issubset(option_ids):
                raise ValueError("Evidence references an unknown option.")
            if not set(item.related_criterion_ids).issubset(criterion_ids):
                raise ValueError("Evidence references an unknown criterion.")

        for assumption in self.assumptions:
            if not set(assumption.evidence_ids).issubset(evidence_ids):
                raise ValueError("An assumption references unknown evidence.")

        for risk in self.risks:
            if risk.option_id is not None and risk.option_id not in option_ids:
                raise ValueError("A risk references an unknown option.")
            if risk.criterion_id is not None and risk.criterion_id not in criterion_ids:
                raise ValueError("A risk references an unknown criterion.")
            if not set(risk.evidence_ids).issubset(evidence_ids):
                raise ValueError("A risk references unknown evidence.")

        return self

    @staticmethod
    def _unique_ids(label: str, records: list[_HasID]) -> set[UUID]:
        identifiers = [record.id for record in records]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError(f"Decision {label} IDs must be unique.")
        return set(identifiers)
