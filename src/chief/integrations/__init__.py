"""Consent-gated, provider-independent connector contracts."""

from .base import Connector
from .github import GitHubReadOnlyConnector
from .registry import (
    ConnectorAlreadyRegistered,
    ConnectorNotRegistered,
    ConnectorRegistry,
    ConnectorRegistryError,
    ConnectorResponseError,
    ConnectorScopeDenied,
    ConsentGrantError,
)
from .schema import (
    ConnectorCapability,
    ConnectorHealth,
    ConnectorHealthStatus,
    ConnectorManifest,
    ConnectorReadResult,
    ConnectorScope,
    ConnectorWriteResult,
    ConsentGrant,
    EvidenceRecord,
    EvidenceSensitivity,
    EvidenceSource,
    IdempotencyMetadata,
    RateLimitMetadata,
    ScopeAccess,
    SyncCursor,
    utc_now,
)

__all__ = [
    "Connector",
    "ConnectorAlreadyRegistered",
    "ConnectorCapability",
    "ConnectorHealth",
    "ConnectorHealthStatus",
    "ConnectorManifest",
    "ConnectorNotRegistered",
    "ConnectorReadResult",
    "ConnectorRegistry",
    "ConnectorRegistryError",
    "ConnectorResponseError",
    "ConnectorScope",
    "ConnectorScopeDenied",
    "ConnectorWriteResult",
    "ConsentGrant",
    "ConsentGrantError",
    "EvidenceRecord",
    "EvidenceSensitivity",
    "EvidenceSource",
    "GitHubReadOnlyConnector",
    "IdempotencyMetadata",
    "RateLimitMetadata",
    "ScopeAccess",
    "SyncCursor",
    "utc_now",
]
