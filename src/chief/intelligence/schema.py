from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from chief.portfolio.schema import PortfolioSummary


def _now() -> datetime:
    return datetime.now(UTC)


def _normalize_required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Value cannot be blank.")
    return value


def _normalize_identifiers(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = raw.strip()
        if value and value not in seen:
            seen.add(value)
            normalized.append(value)
    return normalized


class NeuromapTool(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str
    risk: str
    requires_approval: bool
    side_effects: bool
    idempotent: bool
    timeout_seconds: int


class NeuromapModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    privacy: str
    structured_output: bool
    tool_calling: bool
    streaming: bool
    vision: bool
    audio: bool
    cost_tier: int
    consecutive_failures: int = 0
    circuit_open: bool = False


class NeuromapAgent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    name: str
    role: str
    scope: str
    business_id: UUID | None = None
    status: str
    execution_enabled: bool
    kill_switch_engaged: bool
    authority_enabled: bool
    authority_expires_at: datetime | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_system_ids: list[UUID] = Field(default_factory=list)
    can_delegate: bool = False
    monthly_token_limit: int = 0
    max_parallel_runs: int = 0
    memory_namespace: str


class NeuromapSystem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    name: str
    kind: str
    scope: str
    business_id: UUID | None = None
    status: str
    read_enabled: bool
    write_enabled: bool
    sensitivity: str


class NeuromapSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = "neuromap-v1"
    generated_at: datetime = Field(default_factory=_now)
    owner_id: str
    execution_enabled: bool
    portfolio: PortfolioSummary
    tools: list[NeuromapTool] = Field(default_factory=list)
    models: list[NeuromapModel] = Field(default_factory=list)
    agents: list[NeuromapAgent] = Field(default_factory=list)
    systems: list[NeuromapSystem] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class RoutingStatus(str, Enum):
    ROUTED = "routed"
    NO_ELIGIBLE_AGENT = "no_eligible_agent"
    AMBIGUOUS = "ambiguous"
    REQUESTED_AGENT_UNAVAILABLE = "requested_agent_unavailable"


class AgentRoutingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, max_length=10_000)
    business_id: UUID | None = None
    requested_agent_id: UUID | None = None
    required_tools: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("task")
    @classmethod
    def normalize_task(cls, value: str) -> str:
        return _normalize_required(value)

    @field_validator("required_tools")
    @classmethod
    def normalize_required_tools(cls, values: list[str]) -> list[str]:
        values = _normalize_identifiers(values)
        if any(len(value) > 200 for value in values):
            raise ValueError("Tool identifiers cannot exceed 200 characters.")
        return values


class RouteCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: UUID
    name: str
    score: int = Field(ge=0)
    matched_terms: list[str] = Field(default_factory=list)


class AgentRouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: RoutingStatus
    selected_agent_id: UUID | None = None
    selected_agent_name: str | None = None
    execution_ready: bool = False
    reason: str
    candidates: list[RouteCandidate] = Field(default_factory=list)


class AgentProposalStatus(str, Enum):
    PROPOSED = "proposed"
    MATERIALIZED = "materialized"
    REJECTED = "rejected"


class AgentProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_id: UUID
    parent_agent_id: UUID
    name: str = Field(min_length=1, max_length=300)
    mission: str = Field(min_length=1, max_length=5_000)
    requested_tools: list[str] = Field(default_factory=list, max_length=200)
    requested_system_ids: list[UUID] = Field(default_factory=list, max_length=200)
    rationale: str = Field(default="", max_length=5_000)

    @field_validator("name", "mission")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _normalize_required(value)

    @field_validator("rationale")
    @classmethod
    def normalize_rationale(cls, value: str) -> str:
        return value.strip()

    @field_validator("requested_tools")
    @classmethod
    def normalize_tools(cls, values: list[str]) -> list[str]:
        values = _normalize_identifiers(values)
        if any(len(value) > 200 for value in values):
            raise ValueError("Tool identifiers cannot exceed 200 characters.")
        return values

    @field_validator("requested_system_ids")
    @classmethod
    def unique_systems(cls, values: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(values))


class AgentProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID = Field(default_factory=uuid4)
    owner_id: str = Field(min_length=1, max_length=256)
    business_id: UUID
    parent_agent_id: UUID
    name: str
    mission: str
    requested_tools: list[str] = Field(default_factory=list)
    requested_system_ids: list[UUID] = Field(default_factory=list)
    rationale: str = ""
    status: AgentProposalStatus = AgentProposalStatus.PROPOSED
    materialized_agent_id: UUID | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
