"""Provider-independent schemas for connector access and sourced evidence."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Self
from urllib.parse import urlparse
from uuid import UUID, uuid4

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.:-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _require_identifier(value: str, field_name: str) -> None:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(
            f"{field_name} must start with a lowercase letter and contain only "
            "lowercase letters, digits, '.', '_', ':', or '-'"
        )


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class ScopeAccess(str, Enum):
    """Whether a connector scope permits observation or mutation."""

    READ = "read"
    WRITE = "write"


class ConnectorCapability(str, Enum):
    """Provider-independent features advertised by a connector."""

    READ = "read"
    WRITE = "write"
    INCREMENTAL_SYNC = "incremental_sync"
    SEARCH = "search"
    WEBHOOKS = "webhooks"


class ConnectorHealthStatus(str, Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


class EvidenceSensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


@dataclass(frozen=True, slots=True)
class ConnectorScope:
    """One exact permission exposed by a connector."""

    name: str
    access: ScopeAccess
    description: str

    def __post_init__(self) -> None:
        _require_identifier(self.name, "scope name")
        if not self.description.strip():
            raise ValueError("scope description must not be empty")

    @property
    def is_write(self) -> bool:
        return self.access is ScopeAccess.WRITE


@dataclass(frozen=True, slots=True)
class RateLimitMetadata:
    """A provider's rate-limit snapshot, without provider-specific coupling."""

    limit: int | None = None
    remaining: int | None = None
    reset_at: datetime | None = None
    retry_after_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.limit is not None and self.limit < 0:
            raise ValueError("rate limit must not be negative")
        if self.remaining is not None and self.remaining < 0:
            raise ValueError("rate-limit remaining must not be negative")
        if self.limit is not None and self.remaining is not None and self.remaining > self.limit:
            raise ValueError("rate-limit remaining must not exceed limit")
        if self.reset_at is not None:
            _require_aware(self.reset_at, "rate-limit reset_at")
        if self.retry_after_seconds is not None and self.retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must not be negative")


@dataclass(frozen=True, slots=True)
class ConnectorManifest:
    """Static, non-secret description of a connector's contract."""

    connector_id: str
    display_name: str
    description: str
    capabilities: frozenset[ConnectorCapability]
    scopes: tuple[ConnectorScope, ...]
    default_rate_limit: RateLimitMetadata | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.connector_id, "connector_id")
        if not self.display_name.strip():
            raise ValueError("display_name must not be empty")
        if not self.description.strip():
            raise ValueError("connector description must not be empty")
        if not self.scopes:
            raise ValueError("a connector must declare at least one scope")

        scope_names = [scope.name for scope in self.scopes]
        if len(scope_names) != len(set(scope_names)):
            raise ValueError("connector scope names must be unique")

        access_types = {scope.access for scope in self.scopes}
        if ScopeAccess.READ in access_types and ConnectorCapability.READ not in self.capabilities:
            raise ValueError("read scopes require the read capability")
        if ScopeAccess.WRITE in access_types and ConnectorCapability.WRITE not in self.capabilities:
            raise ValueError("write scopes require the write capability")

    def scope(self, name: str) -> ConnectorScope | None:
        return next((scope for scope in self.scopes if scope.name == name), None)


@dataclass(frozen=True, slots=True)
class ConnectorHealth:
    connector_id: str
    status: ConnectorHealthStatus
    checked_at: datetime
    message: str | None = None
    latency_ms: float | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.connector_id, "connector_id")
        _require_aware(self.checked_at, "health checked_at")
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("health latency_ms must not be negative")


@dataclass(frozen=True, slots=True)
class SyncCursor:
    """Opaque incremental-sync position scoped to one connector permission."""

    connector_id: str
    scope: str
    value: str
    updated_at: datetime

    def __post_init__(self) -> None:
        _require_identifier(self.connector_id, "connector_id")
        _require_identifier(self.scope, "cursor scope")
        if not self.value or len(self.value) > 4096:
            raise ValueError("cursor value must contain 1 to 4096 characters")
        _require_aware(self.updated_at, "cursor updated_at")


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    """Stable upstream identity for a piece of evidence."""

    system: str
    record_id: str
    record_type: str

    def __post_init__(self) -> None:
        _require_identifier(self.system, "evidence source system")
        if not self.record_id.strip():
            raise ValueError("evidence source record_id must not be empty")
        if not self.record_type.strip():
            raise ValueError("evidence source record_type must not be empty")


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """Content plus the provenance needed to verify a connector observation."""

    connector_id: str
    scope: str
    source: EvidenceSource
    content: str
    observed_at: datetime
    retrieved_at: datetime
    confidence: float
    deep_link: str | None
    content_digest: str
    sensitivity: EvidenceSensitivity
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        _require_identifier(self.connector_id, "connector_id")
        _require_identifier(self.scope, "evidence scope")
        if not self.content:
            raise ValueError("evidence content must not be empty")
        _require_aware(self.observed_at, "evidence observed_at")
        _require_aware(self.retrieved_at, "evidence retrieved_at")
        if self.retrieved_at < self.observed_at:
            raise ValueError("evidence retrieved_at must not precede observed_at")
        if not 0 <= self.confidence <= 1:
            raise ValueError("evidence confidence must be between 0 and 1")
        if not _SHA256.fullmatch(self.content_digest):
            raise ValueError("content_digest must be a lowercase SHA-256 hex digest")
        if self.deep_link is not None:
            parsed = urlparse(self.deep_link)
            if parsed.scheme not in {"https", "http", "local"} or not parsed.netloc:
                raise ValueError("deep_link must be an absolute http, https, or local URL")

    @classmethod
    def capture(
        cls,
        *,
        connector_id: str,
        scope: str,
        source: EvidenceSource,
        content: str,
        observed_at: datetime,
        retrieved_at: datetime,
        confidence: float,
        deep_link: str | None,
        sensitivity: EvidenceSensitivity,
    ) -> Self:
        """Create an evidence record with a deterministic digest of its content."""

        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return cls(
            connector_id=connector_id,
            scope=scope,
            source=source,
            content=content,
            observed_at=observed_at,
            retrieved_at=retrieved_at,
            confidence=confidence,
            deep_link=deep_link,
            content_digest=digest,
            sensitivity=sensitivity,
        )

    def verifies(self) -> bool:
        """Return whether the stored content still matches its capture digest."""

        return hashlib.sha256(self.content.encode("utf-8")).hexdigest() == self.content_digest


@dataclass(frozen=True, slots=True)
class IdempotencyMetadata:
    """Caller-supplied replay guard for a mutating connector operation."""

    key: str
    created_at: datetime
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.key.strip() or len(self.key) > 256:
            raise ValueError("idempotency key must contain 1 to 256 characters")
        _require_aware(self.created_at, "idempotency created_at")
        if self.expires_at is not None:
            _require_aware(self.expires_at, "idempotency expires_at")
            if self.expires_at <= self.created_at:
                raise ValueError("idempotency expires_at must be after created_at")

    def is_valid_at(self, now: datetime) -> bool:
        _require_aware(now, "idempotency comparison time")
        return self.expires_at is None or now < self.expires_at


@dataclass(frozen=True, slots=True)
class ConsentGrant:
    """Auditable, exact-scope permission granted by one principal."""

    connector_id: str
    principal_id: str
    scopes: frozenset[str]
    purpose: str
    granted_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        _require_identifier(self.connector_id, "connector_id")
        if not self.principal_id.strip():
            raise ValueError("principal_id must not be empty")
        if not self.scopes:
            raise ValueError("a consent grant must contain at least one scope")
        for scope in self.scopes:
            _require_identifier(scope, "consent scope")
        if not self.purpose.strip():
            raise ValueError("consent purpose must not be empty")
        _require_aware(self.granted_at, "consent granted_at")
        if self.expires_at is not None:
            _require_aware(self.expires_at, "consent expires_at")
            if self.expires_at <= self.granted_at:
                raise ValueError("consent expires_at must be after granted_at")
        if self.revoked_at is not None:
            _require_aware(self.revoked_at, "consent revoked_at")
            if self.revoked_at < self.granted_at:
                raise ValueError("consent revoked_at must not precede granted_at")

    def is_active_at(self, now: datetime) -> bool:
        _require_aware(now, "consent comparison time")
        if now < self.granted_at:
            return False
        if self.revoked_at is not None and now >= self.revoked_at:
            return False
        return self.expires_at is None or now < self.expires_at

    def allows(self, scope: str, now: datetime) -> bool:
        return scope in self.scopes and self.is_active_at(now)


@dataclass(frozen=True, slots=True)
class ConnectorReadResult:
    evidence: tuple[EvidenceRecord, ...]
    next_cursor: SyncCursor | None = None
    rate_limit: RateLimitMetadata | None = None


@dataclass(frozen=True, slots=True)
class ConnectorWriteResult:
    success: bool
    idempotency: IdempotencyMetadata
    evidence: tuple[EvidenceRecord, ...] = ()
    external_id: str | None = None
    rate_limit: RateLimitMetadata | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.success and self.error is not None:
            raise ValueError("a successful write result cannot contain an error")
        if not self.success and not (self.error and self.error.strip()):
            raise ValueError("a failed write result must contain an error")


def utc_now() -> datetime:
    """Timezone-aware clock helper used by policy code."""

    return datetime.now(UTC)
