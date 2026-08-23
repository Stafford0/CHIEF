from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _now() -> datetime:
    return datetime.now(UTC)


def _normalize_required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Value cannot be blank.")
    return value


def _normalize_unique(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = raw.strip().casefold()
        if value and value not in seen:
            seen.add(value)
            normalized.append(value)
    return normalized


class LifecycleState(str, Enum):
    DRAFT = "draft"
    PAUSED = "paused"
    ACTIVE = "active"
    RETIRED = "retired"


class PortfolioScope(str, Enum):
    PORTFOLIO = "portfolio"
    BUSINESS = "business"
    PERSONAL = "personal"


class AgentRole(str, Enum):
    BUSINESS_GOVERNOR = "business_governor"
    SPECIALIST = "specialist"
    PORTFOLIO_OPERATIONS = "portfolio_operations"
    PERSONAL_CHIEF_OF_STAFF = "personal_chief_of_staff"


class SystemKind(str, Enum):
    SAAS = "saas"
    INTERNAL_SERVICE = "internal_service"
    DATA_SOURCE = "data_source"
    COMMUNICATION = "communication"
    INFRASTRUCTURE = "infrastructure"
    DEVICE = "device"
    OTHER = "other"


class FinancialAccountKind(str, Enum):
    BANK = "bank"
    PAYMENT_PROCESSOR = "payment_processor"
    CREDIT = "credit"
    BROKERAGE = "brokerage"
    CRYPTO = "crypto"
    OTHER = "other"


class FinancialActionMode(str, Enum):
    NONE = "none"
    PROPOSE = "propose"
    APPROVAL_REQUIRED = "approval_required"


class HeartbeatHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class DataSensitivity(str, Enum):
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class CredentialReference(BaseModel):
    """Opaque locator for a secret broker; secret material is never accepted here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    uri: str = Field(min_length=7, max_length=1_000)
    label: str | None = Field(default=None, max_length=200)

    @field_validator("uri")
    @classmethod
    def validate_reference_uri(cls, value: str) -> str:
        value = value.strip()
        schemes = (
            "vault",
            "env",
            "keyring",
            "secret",
            "secret-manager",
            "aws-secretsmanager",
            "gcp-secretmanager",
            "azure-keyvault",
            "onepassword",
        )
        scheme_pattern = "|".join(re.escape(scheme) for scheme in schemes)
        if not re.fullmatch(rf"(?:{scheme_pattern})://[^\s?#]+", value):
            raise ValueError(
                "Credential references must be opaque vault, environment, keyring, or secret-manager URIs."
            )
        return value

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class KPI(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=240)
    target: float | None = None
    unit: str | None = Field(default=None, max_length=60)
    source_system_id: UUID | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return _normalize_required(value)

    @field_validator("target")
    @classmethod
    def finite_target(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("KPI targets must be finite.")
        return value


class AuthorityPolicy(BaseModel):
    """Least-privilege authority. The default grants no execution capability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    allowed_tools: list[str] = Field(default_factory=list, max_length=200)
    allowed_system_ids: list[UUID] = Field(default_factory=list, max_length=200)
    read_scopes: list[str] = Field(default_factory=list, max_length=200)
    write_scopes: list[str] = Field(default_factory=list, max_length=200)
    external_writes_enabled: bool = False
    financial_actions: FinancialActionMode = FinancialActionMode.NONE
    can_delegate: bool = False
    human_approval_required: bool = True
    expires_at: datetime | None = None

    @field_validator("allowed_tools", "read_scopes", "write_scopes")
    @classmethod
    def normalize_scopes(cls, values: list[str]) -> list[str]:
        values = _normalize_unique(values)
        if any(len(value) > 200 for value in values):
            raise ValueError("Authority identifiers cannot exceed 200 characters.")
        return values

    @field_validator("allowed_system_ids")
    @classmethod
    def unique_systems(cls, values: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def validate_authority(self) -> Self:
        if self.enabled and self.expires_at is None:
            raise ValueError("Enabled authority requires an explicit expiry time.")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("Authority expiry times must include a timezone.")
        if not self.enabled and (
            self.external_writes_enabled
            or self.financial_actions is not FinancialActionMode.NONE
            or self.can_delegate
        ):
            raise ValueError("Execution authority cannot be enabled on a disabled policy.")
        if self.external_writes_enabled:
            if not self.write_scopes:
                raise ValueError("External writes require at least one explicit write scope.")
            if not self.human_approval_required:
                raise ValueError("External writes must preserve a human approval gate.")
        if self.financial_actions is FinancialActionMode.APPROVAL_REQUIRED and (
            not self.external_writes_enabled or not self.human_approval_required
        ):
            raise ValueError("Financial execution requires external writes and human approval.")
        if self.can_delegate and not self.human_approval_required:
            raise ValueError("Delegated authority must preserve a human approval gate.")
        return self


class BudgetEnvelope(BaseModel):
    """Hard resource ceilings; a zero-valued default means no resources are authorized."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    monthly_compute_limit: Decimal = Field(default=Decimal(0), ge=0, max_digits=18)
    monthly_operating_limit: Decimal = Field(default=Decimal(0), ge=0, max_digits=18)
    max_single_transaction: Decimal = Field(default=Decimal(0), ge=0, max_digits=18)
    monthly_token_limit: int = Field(default=0, ge=0)
    max_parallel_runs: int = Field(default=0, ge=0, le=1_000)
    hard_stop_at_limit: bool = True
    approval_required_for_changes: bool = True

    @model_validator(mode="after")
    def validate_limits(self) -> Self:
        if self.monthly_operating_limit == 0 and self.max_single_transaction != 0:
            raise ValueError("A transaction limit requires a non-zero monthly operating limit.")
        if self.max_single_transaction > self.monthly_operating_limit:
            raise ValueError("A single transaction cannot exceed the monthly operating limit.")
        if not self.hard_stop_at_limit:
            raise ValueError("Portfolio budgets must hard-stop at their configured limits.")
        if not self.approval_required_for_changes:
            raise ValueError("Budget changes must retain human approval.")
        return self


class OwnedPortfolioRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID = Field(default_factory=uuid4)
    owner_id: str = Field(min_length=1, max_length=256)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @field_validator("owner_id")
    @classmethod
    def normalize_owner(cls, value: str) -> str:
        return _normalize_required(value)

    @model_validator(mode="after")
    def validate_timestamps(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at.")
        return self


class BusinessUnit(OwnedPortfolioRecord):
    key: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=300)
    mission: str = Field(default="", max_length=5_000)
    status: LifecycleState = LifecycleState.DRAFT
    monitoring_enabled: bool = False
    execution_enabled: bool = False
    kill_switch_engaged: bool = True
    memory_namespace: str = Field(
        default_factory=lambda: f"memory://business/{uuid4()}",
        pattern=r"^memory://business/[0-9a-f-]{36}$",
    )
    credential_reference: CredentialReference | None = None
    graph_node_id: UUID | None = None
    authority_ceiling: AuthorityPolicy = Field(default_factory=AuthorityPolicy)
    budget: BudgetEnvelope = Field(default_factory=BudgetEnvelope)
    kpis: list[KPI] = Field(default_factory=list, max_length=100)
    review_due_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _normalize_required(value)

    @field_validator("mission")
    @classmethod
    def normalize_mission(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_activation(self) -> Self:
        if self.status is LifecycleState.RETIRED and (
            self.monitoring_enabled or self.execution_enabled
        ):
            raise ValueError("A retired business cannot be monitored or executed.")
        if self.execution_enabled:
            if self.status is not LifecycleState.ACTIVE:
                raise ValueError("Business execution requires active status.")
            if self.kill_switch_engaged or not self.authority_ceiling.enabled:
                raise ValueError("Business execution requires an open kill switch and authority.")
        return self


class ManagedAgent(OwnedPortfolioRecord):
    business_id: UUID | None = None
    parent_agent_id: UUID | None = None
    role: AgentRole
    scope: PortfolioScope
    name: str = Field(min_length=1, max_length=300)
    mission: str = Field(min_length=1, max_length=5_000)
    status: LifecycleState = LifecycleState.DRAFT
    execution_enabled: bool = False
    kill_switch_engaged: bool = True
    memory_namespace: str = Field(
        default_factory=lambda: f"memory://agent/{uuid4()}",
        pattern=r"^memory://agent/[0-9a-f-]{36}$",
    )
    credential_reference: CredentialReference | None = None
    authority: AuthorityPolicy = Field(default_factory=AuthorityPolicy)
    budget: BudgetEnvelope = Field(default_factory=BudgetEnvelope)
    kpis: list[KPI] = Field(default_factory=list, max_length=100)
    heartbeat_interval_seconds: int = Field(default=300, ge=30, le=86_400)
    review_due_at: datetime | None = None

    @field_validator("name", "mission")
    @classmethod
    def normalize_agent_text(cls, value: str) -> str:
        return _normalize_required(value)

    @model_validator(mode="after")
    def validate_scope_and_activation(self) -> Self:
        business_role = self.role in {AgentRole.BUSINESS_GOVERNOR, AgentRole.SPECIALIST}
        if business_role and (
            self.scope is not PortfolioScope.BUSINESS or self.business_id is None
        ):
            raise ValueError("Business agents require business scope and a business_id.")
        if not business_role and self.business_id is not None:
            raise ValueError("Portfolio and personal agents cannot be attached to a business.")
        if (
            self.role is AgentRole.PORTFOLIO_OPERATIONS
            and self.scope is not PortfolioScope.PORTFOLIO
        ):
            raise ValueError("Portfolio operations agents require portfolio scope.")
        if (
            self.role is AgentRole.PERSONAL_CHIEF_OF_STAFF
            and self.scope is not PortfolioScope.PERSONAL
        ):
            raise ValueError("Personal chief-of-staff agents require personal scope.")
        if self.role is AgentRole.BUSINESS_GOVERNOR and self.parent_agent_id is not None:
            raise ValueError(
                "Business governors report directly to CHIEF and cannot have an agent parent."
            )
        if self.role is AgentRole.SPECIALIST and self.parent_agent_id is None:
            raise ValueError("Specialist agents require a parent business governor.")
        if self.role is AgentRole.PERSONAL_CHIEF_OF_STAFF and self.parent_agent_id is not None:
            raise ValueError("The personal chief of staff reports directly to CHIEF.")
        if self.parent_agent_id == self.id:
            raise ValueError("An agent cannot be its own parent.")
        if self.execution_enabled:
            if self.status is not LifecycleState.ACTIVE:
                raise ValueError("Agent execution requires active status.")
            if self.kill_switch_engaged or not self.authority.enabled:
                raise ValueError("Agent execution requires an open kill switch and authority.")
        if self.status is LifecycleState.RETIRED and self.execution_enabled:
            raise ValueError("A retired agent cannot execute.")
        return self


class SystemRegistration(OwnedPortfolioRecord):
    business_id: UUID | None = None
    scope: PortfolioScope
    kind: SystemKind
    name: str = Field(min_length=1, max_length=300)
    provider: str | None = Field(default=None, max_length=200)
    endpoint: str | None = Field(default=None, max_length=2_000)
    status: LifecycleState = LifecycleState.DRAFT
    read_enabled: bool = False
    write_enabled: bool = False
    human_approval_required_for_writes: bool = True
    credential_reference: CredentialReference | None = None
    sensitivity: DataSensitivity = DataSensitivity.CONFIDENTIAL
    contains_personal_data: bool = False
    review_due_at: datetime | None = None
    permission_expires_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def normalize_system_name(cls, value: str) -> str:
        return _normalize_required(value)

    @field_validator("provider", "endpoint")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def validate_system_scope(self) -> Self:
        if self.scope is PortfolioScope.BUSINESS and self.business_id is None:
            raise ValueError("Business-scoped systems require a business_id.")
        if self.scope is not PortfolioScope.BUSINESS and self.business_id is not None:
            raise ValueError("Only business-scoped systems can have a business_id.")
        if self.contains_personal_data and self.scope is not PortfolioScope.PERSONAL:
            raise ValueError("Systems containing personal data must use the personal scope.")
        if self.write_enabled:
            if self.status is not LifecycleState.ACTIVE or not self.read_enabled:
                raise ValueError("System writes require active status and read access.")
            if not self.human_approval_required_for_writes:
                raise ValueError("System writes must retain human approval.")
        if self.status is LifecycleState.RETIRED and (self.read_enabled or self.write_enabled):
            raise ValueError("A retired system cannot be enabled.")
        return self


class FinancialAccountReference(OwnedPortfolioRecord):
    business_id: UUID | None = None
    scope: PortfolioScope
    kind: FinancialAccountKind
    account_alias: str = Field(min_length=1, max_length=200)
    institution: str = Field(min_length=1, max_length=300)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    provider_account_id_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: LifecycleState = LifecycleState.DRAFT
    credential_reference: CredentialReference | None = None
    review_due_at: datetime | None = None

    @field_validator("account_alias", "institution")
    @classmethod
    def normalize_account_text(cls, value: str) -> str:
        return _normalize_required(value)

    @model_validator(mode="after")
    def validate_financial_scope(self) -> Self:
        if self.scope is PortfolioScope.BUSINESS and self.business_id is None:
            raise ValueError("Business financial accounts require a business_id.")
        if self.scope is not PortfolioScope.BUSINESS and self.business_id is not None:
            raise ValueError("Only business financial accounts can have a business_id.")
        return self


class HeartbeatReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    health: HeartbeatHealth = HeartbeatHealth.UNKNOWN
    observed_at: datetime = Field(default_factory=_now)
    summary: str = Field(default="", max_length=2_000)
    evidence_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    metrics: dict[str, float] = Field(default_factory=dict, max_length=100)
    work_item_references: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("summary")
    @classmethod
    def normalize_summary(cls, value: str) -> str:
        return value.strip()

    @field_validator("metrics")
    @classmethod
    def validate_metrics(cls, values: dict[str, float]) -> dict[str, float]:
        normalized: dict[str, float] = {}
        for raw_key, value in values.items():
            key = raw_key.strip().casefold()
            if not key or len(key) > 120:
                raise ValueError("Heartbeat metric names must be 1 to 120 characters.")
            if not math.isfinite(value):
                raise ValueError("Heartbeat metrics must be finite.")
            normalized[key] = value
        return normalized

    @field_validator("work_item_references")
    @classmethod
    def normalize_references(cls, values: list[str]) -> list[str]:
        values = _normalize_unique(values)
        if any(len(value) > 500 for value in values):
            raise ValueError("Work item references cannot exceed 500 characters.")
        return values


class AgentHeartbeat(HeartbeatReport):
    id: UUID = Field(default_factory=uuid4)
    owner_id: str = Field(min_length=1, max_length=256)
    agent_id: UUID
    business_id: UUID | None = None
    received_at: datetime = Field(default_factory=_now)

    @field_validator("owner_id")
    @classmethod
    def normalize_heartbeat_owner(cls, value: str) -> str:
        return _normalize_required(value)


class PortfolioSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_id: str
    businesses: int = Field(default=0, ge=0)
    agents: int = Field(default=0, ge=0)
    systems: int = Field(default=0, ge=0)
    financial_accounts: int = Field(default=0, ge=0)
    active_agents: int = Field(default=0, ge=0)
    execution_enabled_agents: int = Field(default=0, ge=0)
    external_write_enabled_systems: int = Field(default=0, ge=0)
    healthy_agents: int = Field(default=0, ge=0)
    is_blank: bool = True


class OnboardingStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    title: str
    complete: bool = False
    requires_human: bool = False


class OnboardingState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_id: str
    is_blank: bool = True
    ready_for_autonomy: bool = False
    next_step: str | None = "register_first_business"
    steps: list[OnboardingStep] = Field(default_factory=list)
