from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EvaluationCheckKind(str, Enum):
    OBSERVATION_PRESENT = "observation_present"
    TOOL_CHOICE = "tool_choice"
    APPROVAL_REQUIRED = "approval_required"
    FORBIDDEN_ACTION = "forbidden_action"
    EVIDENCE_MARKERS = "evidence_markers"
    CITATION_MARKERS = "citation_markers"
    MEMORY_RECALL = "memory_recall"
    LATENCY = "latency"
    FORBIDDEN_RESPONSE_MARKERS = "forbidden_response_markers"
    ACTION_BUDGET = "action_budget"
    ATTENTION_BUDGET = "attention_budget"
    RESPONSE_LENGTH = "response_length"


class EvaluationExpectation(BaseModel):
    """Deterministic expectations for one recorded CHIEF interaction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_tool: str | None = Field(default=None, min_length=1, max_length=200)
    approval_required: bool | None = None
    forbidden_actions: list[str] = Field(default_factory=list, max_length=100)
    required_evidence_markers: list[str] = Field(default_factory=list, max_length=100)
    required_citation_markers: list[str] = Field(default_factory=list, max_length=100)
    required_memory_tokens: list[str] = Field(default_factory=list, max_length=100)
    forbidden_response_markers: list[str] = Field(default_factory=list, max_length=100)
    maximum_actions: int | None = Field(default=None, ge=0, le=1_000)
    maximum_attention_items: int | None = Field(default=None, ge=0, le=1_000)
    maximum_response_characters: int | None = Field(default=None, ge=1, le=1_000_000)
    latency_ceiling_ms: float | None = Field(default=None, gt=0, le=3_600_000)

    @field_validator(
        "expected_tool",
        mode="after",
    )
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Expected tool cannot be blank.")
        return value

    @field_validator(
        "forbidden_actions",
        "required_evidence_markers",
        "required_citation_markers",
        "required_memory_tokens",
        "forbidden_response_markers",
    )
    @classmethod
    def normalize_markers(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            marker = value.strip()
            if not marker:
                raise ValueError("Evaluation markers cannot be blank.")
            if len(marker) > 500:
                raise ValueError("Evaluation markers cannot exceed 500 characters.")
            folded = marker.casefold()
            if folded not in seen:
                seen.add(folded)
                normalized.append(marker)
        return normalized

    @model_validator(mode="after")
    def require_at_least_one_check(self) -> EvaluationExpectation:
        if not any(
            (
                self.expected_tool is not None,
                self.approval_required is not None,
                self.forbidden_actions,
                self.required_evidence_markers,
                self.required_citation_markers,
                self.required_memory_tokens,
                self.forbidden_response_markers,
                self.maximum_actions is not None,
                self.maximum_attention_items is not None,
                self.maximum_response_characters is not None,
                self.latency_ceiling_ms is not None,
            )
        ):
            raise ValueError("An evaluation case must configure at least one check.")
        return self


class EvaluationObservation(BaseModel):
    """Recorded behavior evaluated without invoking a model or external system."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    selected_tool: str | None = Field(default=None, max_length=200)
    approval_required: bool | None = None
    actions: list[str] = Field(default_factory=list, max_length=1_000)
    attention_items: list[str] = Field(default_factory=list, max_length=1_000)
    response_text: str = Field(default="", max_length=1_000_000)
    recalled_memory: list[str] = Field(default_factory=list, max_length=1_000)
    latency_ms: float | None = Field(default=None, ge=0, le=3_600_000)


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=300)
    prompt: str = Field(default="", max_length=100_000)
    expectation: EvaluationExpectation
    tags: list[str] = Field(default_factory=list, max_length=50)
    weight: float = Field(default=1.0, gt=0, le=100)
    critical: bool = False

    @field_validator("id", "name")
    @classmethod
    def strip_case_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Evaluation case identifiers cannot be blank.")
        return value


class ReleaseThresholds(BaseModel):
    """Aggregate requirements that decide whether a suite can release."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_case_pass_rate: float = Field(default=1.0, ge=0, le=1)
    minimum_weighted_score: float = Field(default=1.0, ge=0, le=1)
    maximum_failed_cases: int = Field(default=0, ge=0)
    maximum_critical_failures: int = Field(default=0, ge=0)
    minimum_check_pass_rates: dict[EvaluationCheckKind, float] = Field(default_factory=dict)
    blocking_checks: list[EvaluationCheckKind] = Field(
        default_factory=lambda: [
            EvaluationCheckKind.FORBIDDEN_ACTION,
            EvaluationCheckKind.APPROVAL_REQUIRED,
            EvaluationCheckKind.FORBIDDEN_RESPONSE_MARKERS,
            EvaluationCheckKind.ACTION_BUDGET,
            EvaluationCheckKind.ATTENTION_BUDGET,
        ]
    )

    @field_validator("minimum_check_pass_rates")
    @classmethod
    def validate_check_rates(
        cls, values: dict[EvaluationCheckKind, float]
    ) -> dict[EvaluationCheckKind, float]:
        for value in values.values():
            if not 0 <= value <= 1:
                raise ValueError("Per-check release thresholds must be between 0 and 1.")
        return values

    @field_validator("blocking_checks")
    @classmethod
    def unique_blocking_checks(cls, values: list[EvaluationCheckKind]) -> list[EvaluationCheckKind]:
        return list(dict.fromkeys(values))


class EvaluationSuite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=300)
    version: str = Field(default="1", min_length=1, max_length=100)
    cases: list[EvaluationCase] = Field(min_length=1, max_length=10_000)
    thresholds: ReleaseThresholds = Field(default_factory=ReleaseThresholds)

    @model_validator(mode="after")
    def unique_case_ids(self) -> EvaluationSuite:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("Evaluation case IDs must be unique within a suite.")
        return self


class EvaluationCheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: EvaluationCheckKind
    passed: bool
    message: str
    expected: Any = None
    observed: Any = None


class EvaluationCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    name: str
    critical: bool
    weight: float
    passed: bool
    score: float = Field(ge=0, le=1)
    checks: list[EvaluationCheckResult] = Field(min_length=1)


class EvaluationSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_cases: int
    passed_cases: int
    failed_cases: int
    critical_failures: int
    total_checks: int
    passed_checks: int
    case_pass_rate: float = Field(ge=0, le=1)
    weighted_score: float = Field(ge=0, le=1)
    check_pass_rates: dict[EvaluationCheckKind, float]


class EvaluationSuiteResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    suite_id: str
    suite_name: str
    suite_version: str
    generated_at: datetime
    release_gate_passed: bool
    gate_failures: list[str]
    summary: EvaluationSummary
    cases: list[EvaluationCaseResult]
