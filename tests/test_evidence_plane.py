from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from chief.business import BusinessNodeKind, RelationshipKind, SQLiteBusinessGraphStore
from chief.integrations.evidence_plane import BusinessEvidencePlane, SQLiteEvidenceSyncStore
from chief.integrations.registry import ConnectorRegistry, ConnectorScopeDenied
from chief.integrations.schema import (
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
    ScopeAccess,
    SyncCursor,
)

NOW = datetime(2026, 9, 1, 20, 0, tzinfo=UTC)


class FakeEvidenceConnector:
    manifest = ConnectorManifest(
        connector_id="fake",
        display_name="Fake",
        description="Test evidence connector.",
        capabilities=frozenset(
            {ConnectorCapability.READ, ConnectorCapability.INCREMENTAL_SYNC}
        ),
        scopes=(ConnectorScope("events.read", ScopeAccess.READ, "Read events."),),
    )

    def __init__(self) -> None:
        self.cursors: list[SyncCursor | None] = []

    def health(self) -> ConnectorHealth:
        return ConnectorHealth(
            connector_id="fake",
            status=ConnectorHealthStatus.HEALTHY,
            checked_at=NOW,
        )

    def read(
        self,
        scope: str,
        *,
        cursor: SyncCursor | None = None,
        limit: int = 100,
    ) -> ConnectorReadResult:
        del limit
        self.cursors.append(cursor)
        evidence = ()
        if cursor is None:
            evidence = (
                EvidenceRecord.capture(
                    connector_id="fake",
                    scope=scope,
                    source=EvidenceSource(
                        system="fake",
                        record_id="record-1",
                        record_type="event",
                    ),
                    content='{"title":"Deployment failed","state":"open"}',
                    observed_at=datetime(2026, 9, 1, 19, 30, tzinfo=UTC),
                    retrieved_at=NOW,
                    confidence=1.0,
                    deep_link="https://example.com/event/1",
                    sensitivity=EvidenceSensitivity.INTERNAL,
                ),
            )
        return ConnectorReadResult(
            evidence=evidence,
            next_cursor=SyncCursor(
                connector_id="fake",
                scope=scope,
                value=NOW.isoformat(),
                updated_at=NOW,
            ),
        )

    def write(
        self,
        scope: str,
        payload: dict[str, object],
        *,
        idempotency: IdempotencyMetadata,
    ) -> ConnectorWriteResult:
        del scope, payload, idempotency
        raise PermissionError


def _plane(tmp_path: Path):
    database = tmp_path / "chief.db"
    business_store = SQLiteBusinessGraphStore(database)
    connector = FakeEvidenceConnector()
    registry = ConnectorRegistry(clock=lambda: NOW)
    registry.register(connector)
    plane = BusinessEvidencePlane(
        registry=registry,
        business_store=business_store,
        sync_store=SQLiteEvidenceSyncStore(database),
    )
    return plane, registry, connector, business_store


def test_sync_requires_explicit_consent(tmp_path: Path) -> None:
    plane, _, _, _ = _plane(tmp_path)

    with pytest.raises(ConnectorScopeDenied):
        plane.sync(
            principal_id="owner",
            connector_id="fake",
            scopes=("events.read",),
            business_key="parcel-signals",
            business_name="Parcel Signals",
        )


def test_sync_persists_evidence_relationship_and_cursor(tmp_path: Path) -> None:
    plane, registry, connector, business_store = _plane(tmp_path)
    registry.grant_consent(
        ConsentGrant(
            connector_id="fake",
            principal_id="owner",
            scopes=frozenset({"events.read"}),
            purpose="Observe Parcel Signals operations",
            granted_at=NOW,
        )
    )

    first = plane.sync(
        principal_id="owner",
        connector_id="fake",
        scopes=("events.read",),
        business_key="Parcel Signals",
        business_name="Parcel Signals",
    )
    second = plane.sync(
        principal_id="owner",
        connector_id="fake",
        scopes=("events.read",),
        business_key="Parcel Signals",
        business_name="Parcel Signals",
    )

    assert first.observed == 1
    assert first.created == 1
    assert second.observed == 0
    assert connector.cursors[0] is None
    assert connector.cursors[1] is not None
    assert connector.cursors[1].value == NOW.isoformat()

    organizations = business_store.list_nodes(
        owner_id="owner", kinds=[BusinessNodeKind.ORGANIZATION]
    )
    documents = business_store.list_nodes(owner_id="owner", kinds=[BusinessNodeKind.DOCUMENT])
    relationships = business_store.list_relationships(
        owner_id="owner", kinds=[RelationshipKind.DOCUMENTS]
    )
    assert [item.name for item in organizations] == ["Parcel Signals"]
    assert [item.name for item in documents] == ["Deployment failed"]
    assert documents[0].content_digest is not None
    assert len(relationships) == 1
    assert relationships[0].source_id == organizations[0].id
    assert relationships[0].target_id == documents[0].id


def test_business_evidence_briefing_surfaces_verified_source(tmp_path: Path) -> None:
    plane, registry, _, _ = _plane(tmp_path)
    registry.grant_consent(
        ConsentGrant(
            connector_id="fake",
            principal_id="owner",
            scopes=frozenset({"events.read"}),
            purpose="Business briefing",
            granted_at=NOW,
        )
    )
    plane.sync(
        principal_id="owner",
        connector_id="fake",
        scopes=("events.read",),
        business_key="parcel-signals",
        business_name="Parcel Signals",
    )

    briefing = plane.briefing(principal_id="owner", business_key="parcel-signals")

    assert briefing["evidence_count"] == 1
    assert briefing["items"][0]["title"] == "Deployment failed"
    assert briefing["items"][0]["verified"] is True
    assert briefing["items"][0]["source_uri"] == "https://example.com/event/1"
    assert briefing["unverified"] == []
