"""Deterministic and inspectable weighted decision scoring."""

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from chief.decisions.schema import DecisionRecord


class _ScoringModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CriterionContribution(_ScoringModel):
    criterion_id: UUID
    criterion_name: str
    raw_score: float = Field(ge=0.0, le=1.0)
    score_confidence: float = Field(ge=0.0, le=1.0)
    base_weight: float = Field(gt=0.0)
    effective_weight: float = Field(ge=0.0)
    normalized_weight: float = Field(ge=0.0, le=1.0)
    weighted_contribution: float = Field(ge=0.0, le=1.0)
    confidence_contribution: float = Field(ge=0.0, le=1.0)
    rationale: str
    evidence_ids: list[UUID]


class OptionScore(_ScoringModel):
    rank: int = Field(ge=1)
    option_id: UUID
    option_name: str
    total_score: float = Field(ge=0.0, le=1.0)
    aggregate_confidence: float = Field(ge=0.0, le=1.0)
    contributions: list[CriterionContribution]


class DecisionScorecard(_ScoringModel):
    decision_id: UUID
    total_effective_weight: float = Field(gt=0.0)
    applied_weight_overrides: dict[str, float]
    options: list[OptionScore]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def score_decision(
    decision: DecisionRecord,
    *,
    weight_overrides: Mapping[UUID | str, float] | None = None,
) -> DecisionScorecard:
    """Score every option while exposing every input and contribution.

    Scores are normalized desirability values between zero and one. Criterion
    weights are normalized before multiplication, so callers can vary weights
    freely for sensitivity analysis without changing the score scale.
    """

    criteria = {criterion.id: criterion for criterion in decision.criteria}
    overrides = _normalize_overrides(criteria, weight_overrides or {})
    effective_weights = {
        criterion_id: overrides.get(criterion_id, criterion.weight)
        for criterion_id, criterion in criteria.items()
    }
    total_weight = sum(effective_weights.values())
    if total_weight <= 0.0:
        raise ValueError("At least one criterion must have a positive effective weight.")

    unranked: list[OptionScore] = []
    for option in decision.options:
        assessments = {score.criterion_id: score for score in option.criterion_scores}
        missing = set(criteria) - set(assessments)
        if missing:
            names = ", ".join(sorted(criteria[item].name for item in missing))
            raise ValueError(f"Option '{option.name}' has no score for: {names}.")

        contributions: list[CriterionContribution] = []
        for criterion in decision.criteria:
            assessment = assessments[criterion.id]
            effective_weight = effective_weights[criterion.id]
            normalized_weight = effective_weight / total_weight
            contributions.append(
                CriterionContribution(
                    criterion_id=criterion.id,
                    criterion_name=criterion.name,
                    raw_score=assessment.score,
                    score_confidence=assessment.confidence,
                    base_weight=criterion.weight,
                    effective_weight=effective_weight,
                    normalized_weight=normalized_weight,
                    weighted_contribution=assessment.score * normalized_weight,
                    confidence_contribution=assessment.confidence * normalized_weight,
                    rationale=assessment.rationale,
                    evidence_ids=list(assessment.evidence_ids),
                )
            )

        unranked.append(
            OptionScore(
                rank=1,
                option_id=option.id,
                option_name=option.name,
                total_score=sum(item.weighted_contribution for item in contributions),
                aggregate_confidence=sum(item.confidence_contribution for item in contributions),
                contributions=contributions,
            )
        )

    ordered = sorted(
        unranked,
        key=lambda item: (-item.total_score, item.option_name.casefold(), str(item.option_id)),
    )
    ranked = [item.model_copy(update={"rank": rank}) for rank, item in enumerate(ordered, 1)]
    return DecisionScorecard(
        decision_id=decision.id,
        total_effective_weight=total_weight,
        applied_weight_overrides={str(key): value for key, value in overrides.items()},
        options=ranked,
    )


def _normalize_overrides(
    criteria: Mapping[UUID, object],
    overrides: Mapping[UUID | str, float],
) -> dict[UUID, float]:
    normalized: dict[UUID, float] = {}
    for raw_id, raw_weight in overrides.items():
        try:
            criterion_id = raw_id if isinstance(raw_id, UUID) else UUID(raw_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid criterion ID in weight overrides: {raw_id!r}.") from exc
        if criterion_id not in criteria:
            raise ValueError(f"Weight override references unknown criterion {criterion_id}.")
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, int | float):
            raise TypeError("Criterion weight overrides must be numeric.")
        weight = float(raw_weight)
        if not math.isfinite(weight) or weight < 0.0:
            raise ValueError("Criterion weight overrides must be finite and non-negative.")
        normalized[criterion_id] = weight
    return normalized
