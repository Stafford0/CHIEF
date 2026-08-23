from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest

from chief.integrations import (
    Connector,
    ConnectorCapability,
    ConnectorHealth,
    ConnectorHealthStatus,
    ConnectorManifest,
    ConnectorNotRegistered,
    ConnectorReadResult,
    ConnectorRegistry,
    ConnectorScope,
    ConnectorScopeDenied,
    ConnectorWriteResult,
    ConsentGrant,
    ConsentGrantError,
    EvidenceRecord,
    EvidenceSensitivity,
    EvidenceSource,
    IdempotencyMetadata,
    RateLimitMetadata,
    ScopeAccess,
    SyncCursor,
)

NOW = datetime(2026, 8, 23, 12, tzinfo=UTC)


class FakeConnector:
    def __init__(self) -> None:
        self.manifest = ConnectorManifest(
            connector_id="example",
            display_name="Example",
            description="A deterministic test connector.",
            capabilities=frozenset(
                {
                    ConnectorCapability.READ,
                    ConnectorCapability.WRITE,
                    ConnectorCapability.INCREMENTAL_SYNC,
                }
            ),
            scopes=(
                ConnectorScope("metrics.read", ScopeAccess.READ, "Read company metrics."),
                ConnectorScope("invoice.create", ScopeAccess.WRITE, "Create an invoice."),
            ),
            default_rate_limit=RateLimitMetadata(limit=100, remaining=100),
        )
        self.read_calls = 0
        self.write_calls = 0

    def health(self) -> ConnectorHealth:
        return ConnectorHealth(
            connector_id="example",
            status=ConnectorHealthStatus.HEALTHY,
            checked_at=NOW,
            latency_ms=4.2,
        )

    def read(
        self,
        scope: str,
        *,
        cursor: SyncCursor | None = None,
        limit: int = 100,
    ) -> ConnectorReadResult:
        self.read_calls += 1
        evidence = EvidenceRecord.capture(
            connector_id="example",
            scope=scope,
            source=EvidenceSource("example", "metric-42", "metric"),
            content=f"revenue=42;limit={limit}",
            observed_at=NOW,
            retrieved_at=NOW + timedelta(seconds=1),
            confidence=0.98,
            deep_link="https://example.test/metrics/42",
            sensitivity=EvidenceSensitivity.CONFIDENTIAL,
        )
        next_cursor = SyncCursor("example", scope, "opaque-next", NOW)
        return ConnectorReadResult(
            evidence=(evidence,),
            next_cursor=next_cursor,
            rate_limit=RateLimitMetadata(limit=100, remaining=99, reset_at=NOW),
        )

    def write(
        self,
        scope: str,
        payload: Mapping[str, object],
        *,
        idempotency: IdempotencyMetadata,
    ) -> ConnectorWriteResult:
        self.write_calls += 1
        return ConnectorWriteResult(
            success=True,
            external_id=str(payload["invoice_id"]),
            idempotency=idempotency,
        )


def grant(*scopes: str, expires_at: datetime | None = None) -> ConsentGrant:
    return ConsentGrant(
        connector_id="example",
        principal_id="founder-1",
        scopes=frozenset(scopes),
        purpose="Operate the company with founder approval.",
        granted_at=NOW - timedelta(minutes=1),
        expires_at=expires_at,
    )


def registry_with(connector: FakeConnector) -> ConnectorRegistry:
    registry = ConnectorRegistry(clock=lambda: NOW)
    registry.register(connector)
    return registry


def test_connector_protocol_and_manifest_enforce_typed_access() -> None:
    connector = FakeConnector()
    assert isinstance(connector, Connector)
    assert connector.manifest.scope("metrics.read").access is ScopeAccess.READ
    assert connector.manifest.scope("invoice.create").is_write

    with pytest.raises(ValueError, match="write scopes require"):
        ConnectorManifest(
            connector_id="invalid",
            display_name="Invalid",
            description="Missing a capability.",
            capabilities=frozenset({ConnectorCapability.READ}),
            scopes=(ConnectorScope("record.write", ScopeAccess.WRITE, "Write a record."),),
        )


def test_evidence_captures_provenance_digest_and_sensitivity() -> None:
    record = EvidenceRecord.capture(
        connector_id="example",
        scope="metrics.read",
        source=EvidenceSource("example", "row-1", "metric"),
        content="arr=1000000",
        observed_at=NOW,
        retrieved_at=NOW + timedelta(seconds=2),
        confidence=0.91,
        deep_link="https://example.test/rows/1",
        sensitivity=EvidenceSensitivity.RESTRICTED,
    )

    assert record.verifies()
    assert len(record.content_digest) == 64
    assert record.source.record_id == "row-1"
    assert record.retrieved_at > record.observed_at
    assert record.sensitivity is EvidenceSensitivity.RESTRICTED

    with pytest.raises(ValueError, match="confidence"):
        EvidenceRecord.capture(
            connector_id="example",
            scope="metrics.read",
            source=record.source,
            content="invalid",
            observed_at=NOW,
            retrieved_at=NOW,
            confidence=1.1,
            deep_link=None,
            sensitivity=EvidenceSensitivity.INTERNAL,
        )


def test_registry_denies_unregistered_connector_without_calling_it() -> None:
    connector = FakeConnector()
    registry = ConnectorRegistry(clock=lambda: NOW)

    with pytest.raises(ConnectorNotRegistered):
        registry.read("example", "metrics.read", principal_id="founder-1")
    assert connector.read_calls == 0


def test_registry_denies_registered_but_ungranted_scope() -> None:
    connector = FakeConnector()
    registry = registry_with(connector)

    with pytest.raises(ConnectorScopeDenied, match="no active consent"):
        registry.read("example", "metrics.read", principal_id="founder-1")
    assert connector.read_calls == 0


def test_read_consent_does_not_imply_write_consent() -> None:
    connector = FakeConnector()
    registry = registry_with(connector)
    registry.grant_consent(grant("metrics.read"))
    idempotency = IdempotencyMetadata("invoice-42", NOW)

    result = registry.read("example", "metrics.read", principal_id="founder-1", limit=25)
    assert result.evidence[0].verifies()
    assert result.next_cursor is not None

    with pytest.raises(ConnectorScopeDenied, match="no active consent"):
        registry.write(
            "example",
            "invoice.create",
            {"invoice_id": "inv-42"},
            principal_id="founder-1",
            idempotency=idempotency,
        )
    assert connector.write_calls == 0


def test_access_type_cannot_be_reinterpreted() -> None:
    connector = FakeConnector()
    registry = registry_with(connector)
    registry.grant_consent(grant("metrics.read"))

    with pytest.raises(ConnectorScopeDenied, match="grants read, not write"):
        registry.write(
            "example",
            "metrics.read",
            {},
            principal_id="founder-1",
            idempotency=IdempotencyMetadata("mutation-1", NOW),
        )


def test_explicit_write_consent_and_idempotency_allow_write() -> None:
    connector = FakeConnector()
    registry = registry_with(connector)
    registry.grant_consent(grant("invoice.create"))
    idempotency = IdempotencyMetadata(
        "invoice-42",
        NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=5),
    )

    result = registry.write(
        "example",
        "invoice.create",
        {"invoice_id": "inv-42"},
        principal_id="founder-1",
        idempotency=idempotency,
    )

    assert result.success
    assert result.external_id == "inv-42"
    assert result.idempotency.key == "invoice-42"
    assert connector.write_calls == 1


def test_expired_idempotency_metadata_denies_write_before_connector_call() -> None:
    connector = FakeConnector()
    registry = registry_with(connector)
    registry.grant_consent(grant("invoice.create"))

    with pytest.raises(ValueError, match="idempotency metadata has expired"):
        registry.write(
            "example",
            "invoice.create",
            {"invoice_id": "inv-expired"},
            principal_id="founder-1",
            idempotency=IdempotencyMetadata(
                "invoice-expired",
                NOW - timedelta(minutes=2),
                expires_at=NOW - timedelta(minutes=1),
            ),
        )
    assert connector.write_calls == 0


def test_expired_and_revoked_grants_are_retained_but_denied() -> None:
    connector = FakeConnector()
    registry = registry_with(connector)
    expired = grant("metrics.read", expires_at=NOW - timedelta(seconds=1))
    registry.grant_consent(expired)

    with pytest.raises(ConnectorScopeDenied):
        registry.read("example", "metrics.read", principal_id="founder-1")

    active = grant("metrics.read", expires_at=NOW + timedelta(hours=1))
    registry.grant_consent(active)
    revoked = registry.revoke_consent(active.id, revoked_at=NOW)
    assert revoked.revoked_at == NOW

    with pytest.raises(ConnectorScopeDenied):
        registry.read("example", "metrics.read", principal_id="founder-1")
    assert len(registry.consent_grants(principal_id="founder-1")) == 2


def test_unregister_revokes_grants_so_reregistration_does_not_reuse_consent() -> None:
    connector = FakeConnector()
    registry = registry_with(connector)
    active = registry.grant_consent(grant("metrics.read"))

    registry.unregister("example")
    assert registry.consent_grants()[0].id == active.id
    assert registry.consent_grants()[0].revoked_at == NOW

    replacement = FakeConnector()
    registry.register(replacement)
    with pytest.raises(ConnectorScopeDenied):
        registry.read("example", "metrics.read", principal_id="founder-1")


def test_undeclared_grant_and_mismatched_cursor_are_rejected() -> None:
    connector = FakeConnector()
    registry = registry_with(connector)

    with pytest.raises(ConsentGrantError, match="undeclared scopes"):
        registry.grant_consent(grant("admin.everything"))

    registry.grant_consent(grant("metrics.read"))
    cursor = SyncCursor("example", "different.read", "opaque", NOW)
    with pytest.raises(ValueError, match="cursor must match"):
        registry.read(
            "example",
            "metrics.read",
            principal_id="founder-1",
            cursor=cursor,
        )


def test_health_and_rate_limit_metadata_are_provider_independent() -> None:
    connector = FakeConnector()
    registry = registry_with(connector)

    health = registry.health("example")
    assert health.status is ConnectorHealthStatus.HEALTHY
    assert health.latency_ms == 4.2
    assert connector.manifest.default_rate_limit == RateLimitMetadata(limit=100, remaining=100)
