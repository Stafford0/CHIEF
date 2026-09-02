from __future__ import annotations

from datetime import UTC, datetime

import pytest

from chief.integrations.parcelsignals import ParcelSignalsReadOnlyConnector
from chief.integrations.schema import ConnectorCapability, IdempotencyMetadata

NOW = datetime(2026, 9, 1, 20, 0, tzinfo=UTC)


def test_parcelsignals_reads_only_approved_national_overview_rpc() -> None:
    seen: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes | None):
        seen.update({"url": url, "headers": headers, "body": body})
        return (
            200,
            {
                "market_label": "United States",
                "as_of": "2026-09-01T19:30:00Z",
                "geography_counts": {"state": 51, "county": 3235},
                "metric_observation_count": 72576,
                "sources": [{"source_key": "census.bps", "coverage_status": "active"}],
                "national_model_count": 0,
                "national_score_status": "not_published",
            },
            {},
            4.0,
        )

    connector = ParcelSignalsReadOnlyConnector(
        supabase_url="https://example.supabase.co",
        secret_provider=lambda: "server-secret",
        transport=transport,
        clock=lambda: NOW,
    )
    result = connector.read("national.overview.read")

    assert seen["url"] == (
        "https://example.supabase.co/rest/v1/rpc/parcelsignals_national_overview"
    )
    assert seen["body"] == b"{}"
    assert seen["headers"]["Authorization"] == "Bearer server-secret"
    assert seen["headers"]["apikey"] == "server-secret"
    assert len(result.evidence) == 1
    evidence = result.evidence[0]
    assert evidence.source.record_id == "national-overview"
    assert evidence.source.record_type == "business_intelligence_overview"
    assert evidence.observed_at == datetime(2026, 9, 1, 19, 30, tzinfo=UTC)
    assert evidence.verifies()
    assert "national_score_status" in evidence.content


def test_parcelsignals_connector_is_read_only_and_cursorless() -> None:
    connector = ParcelSignalsReadOnlyConnector(
        supabase_url="https://example.supabase.co",
        secret_provider=lambda: "server-secret",
    )
    assert connector.manifest.capabilities == frozenset({ConnectorCapability.READ})

    with pytest.raises(ValueError, match="does not use incremental cursors"):
        from chief.integrations.schema import SyncCursor

        connector.read(
            "national.overview.read",
            cursor=SyncCursor(
                connector_id="parcelsignals",
                scope="national.overview.read",
                value=NOW.isoformat(),
                updated_at=NOW,
            ),
        )

    with pytest.raises(PermissionError, match="does not permit writes"):
        connector.write(
            "national.overview.write",
            {},
            idempotency=IdempotencyMetadata(key="nope", created_at=NOW),
        )


def test_parcelsignals_health_fails_closed_when_secret_missing() -> None:
    connector = ParcelSignalsReadOnlyConnector(
        supabase_url="https://example.supabase.co",
        secret_provider=lambda: None,
        clock=lambda: NOW,
    )
    health = connector.health()
    assert health.status.value == "unavailable"
    assert "secret" in (health.message or "").casefold()
