from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator


class BusinessNodeKind(str, Enum):
    ORGANIZATION = "organization"
    PERSON = "person"
    PRODUCT = "product"
    CUSTOMER = "customer"
    COMPETITOR = "competitor"
    PROJECT = "project"
    OPPORTUNITY = "opportunity"
    RISK = "risk"
    DOCUMENT = "document"


class Sensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class ProvenanceType(str, Enum):
    USER = "user"
    DOCUMENT = "document"
    INTEGRATION = "integration"
    SYSTEM = "system"
    INFERENCE = "inference"


class RelationshipKind(str, Enum):
    OWNS = "owns"
    EMPLOYS = "employs"
    LEADS = "leads"
    WORKS_ON = "works_on"
    PRODUCES = "produces"
    SERVES = "serves"
    CUSTOMER_OF = "customer_of"
    COMPETES_WITH = "competes_with"
    DEPENDS_ON = "depends_on"
    BLOCKS = "blocks"
    RELATED_TO = "related_to"
    DOCUMENTS = "documents"
    HAS_RISK = "has_risk"
    PURSUES = "pursues"


class TraversalDirection(str, Enum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"
    BOTH = "both"


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: ProvenanceType
    source_id: str | None = Field(default=None, max_length=500)
    source_uri: str | None = Field(default=None, max_length=2_000)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    evidence_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    notes: str | None = Field(default=None, max_length=5_000)


class BusinessEntity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID = Field(default_factory=uuid4)
    kind: BusinessNodeKind
    key: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=20_000)
    provenance: Provenance
    owner_id: str = Field(min_length=1, max_length=256)
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    confidence: float = Field(default=1.0, ge=0, le=1)
    valid_from: datetime = Field(default_factory=lambda: datetime.now(UTC))
    valid_to: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tags: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("key", "name", "owner_id")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Business entity identifiers cannot be blank.")
        return value

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str) -> str:
        return value.strip()

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            tag = value.strip().casefold()
            if tag and tag not in seen:
                if len(tag) > 100:
                    raise ValueError("Business entity tags cannot exceed 100 characters.")
                seen.add(tag)
                normalized.append(tag)
        return normalized

    @model_validator(mode="after")
    def validate_temporal_window(self) -> BusinessEntity:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be later than valid_from.")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at.")
        return self


class Organization(BusinessEntity):
    kind: Literal[BusinessNodeKind.ORGANIZATION] = BusinessNodeKind.ORGANIZATION
    industry: str | None = Field(default=None, max_length=300)
    website: str | None = Field(default=None, max_length=2_000)


class Person(BusinessEntity):
    kind: Literal[BusinessNodeKind.PERSON] = BusinessNodeKind.PERSON
    role: str | None = Field(default=None, max_length=300)
    email: str | None = Field(default=None, max_length=500)


class Product(BusinessEntity):
    kind: Literal[BusinessNodeKind.PRODUCT] = BusinessNodeKind.PRODUCT
    lifecycle_stage: str | None = Field(default=None, max_length=200)


class Customer(BusinessEntity):
    kind: Literal[BusinessNodeKind.CUSTOMER] = BusinessNodeKind.CUSTOMER
    segment: str | None = Field(default=None, max_length=200)
    status: str | None = Field(default=None, max_length=200)


class Competitor(BusinessEntity):
    kind: Literal[BusinessNodeKind.COMPETITOR] = BusinessNodeKind.COMPETITOR
    strengths: list[str] = Field(default_factory=list, max_length=100)
    weaknesses: list[str] = Field(default_factory=list, max_length=100)


class Project(BusinessEntity):
    kind: Literal[BusinessNodeKind.PROJECT] = BusinessNodeKind.PROJECT
    status: str | None = Field(default=None, max_length=200)
    target_date: datetime | None = None


class Opportunity(BusinessEntity):
    kind: Literal[BusinessNodeKind.OPPORTUNITY] = BusinessNodeKind.OPPORTUNITY
    stage: str | None = Field(default=None, max_length=200)
    projected_value: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    probability: float | None = Field(default=None, ge=0, le=1)


class RiskSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Risk(BusinessEntity):
    kind: Literal[BusinessNodeKind.RISK] = BusinessNodeKind.RISK
    severity: RiskSeverity = RiskSeverity.MEDIUM
    likelihood: float | None = Field(default=None, ge=0, le=1)
    mitigation: str | None = Field(default=None, max_length=20_000)
    status: str | None = Field(default=None, max_length=200)


class Document(BusinessEntity):
    kind: Literal[BusinessNodeKind.DOCUMENT] = BusinessNodeKind.DOCUMENT
    uri: str | None = Field(default=None, max_length=2_000)
    document_type: str | None = Field(default=None, max_length=200)
    content_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


BusinessNode = Annotated[
    Organization
    | Person
    | Product
    | Customer
    | Competitor
    | Project
    | Opportunity
    | Risk
    | Document,
    Field(discriminator="kind"),
]
BUSINESS_NODE_ADAPTER = TypeAdapter(BusinessNode)


class BusinessRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID = Field(default_factory=uuid4)
    source_id: UUID
    target_id: UUID
    kind: RelationshipKind
    label: str | None = Field(default=None, max_length=500)
    provenance: Provenance
    owner_id: str = Field(min_length=1, max_length=256)
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    confidence: float = Field(default=1.0, ge=0, le=1)
    valid_from: datetime = Field(default_factory=lambda: datetime.now(UTC))
    valid_to: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("owner_id")
    @classmethod
    def strip_owner(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Relationship owner cannot be blank.")
        return value

    @model_validator(mode="after")
    def validate_relationship(self) -> BusinessRelationship:
        if self.source_id == self.target_id:
            raise ValueError("Business relationships must connect two distinct nodes.")
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be later than valid_from.")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at.")
        return self


class BusinessTraversal(BaseModel):
    model_config = ConfigDict(frozen=True)

    start_id: UUID
    direction: TraversalDirection
    as_of: datetime
    nodes: list[BusinessNode]
    relationships: list[BusinessRelationship]
    depth_reached: int
    truncated: bool
