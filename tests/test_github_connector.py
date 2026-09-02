from __future__ import annotations

from datetime import UTC, datetime

import pytest

from chief.integrations.github import GitHubReadOnlyConnector
from chief.integrations.schema import (
    ConnectorCapability,
    ConnectorHealthStatus,
    EvidenceSensitivity,
    IdempotencyMetadata,
)

NOW = datetime(2026, 9, 1, 20, 0, tzinfo=UTC)


def test_manifest_is_read_only() -> None:
    connector = GitHubReadOnlyConnector(repositories=("Stafford0/CHIEF",))

    assert connector.manifest.connector_id == "github"
    assert connector.manifest.capabilities == frozenset({ConnectorCapability.READ})
    assert {scope.name for scope in connector.manifest.scopes} == {
        "repositories.read",
        "commits.read",
        "issues.read",
        "pulls.read",
    }
    assert all(not scope.is_write for scope in connector.manifest.scopes)


def test_repository_read_captures_provenance_digest_and_rate_limit() -> None:
    seen_headers: dict[str, str] = {}

    def transport(url: str, headers: dict[str, str]):
        seen_headers.update(headers)
        assert url == "https://api.github.com/repos/Stafford0/CHIEF"
        return (
            200,
            {
                "id": 123,
                "full_name": "Stafford0/CHIEF",
                "private": True,
                "updated_at": "2026-09-01T19:30:00Z",
                "html_url": "https://github.com/Stafford0/CHIEF",
            },
            {
                "X-RateLimit-Limit": "5000",
                "X-RateLimit-Remaining": "4999",
                "X-RateLimit-Reset": "1788310800",
            },
            12.0,
        )

    connector = GitHubReadOnlyConnector(
        repositories=("Stafford0/CHIEF",),
        token_provider=lambda: "test-token",
        transport=transport,
        clock=lambda: NOW,
    )

    result = connector.read("repositories.read", limit=1)

    assert seen_headers["Authorization"] == "Bearer test-token"
    assert len(result.evidence) == 1
    evidence = result.evidence[0]
    assert evidence.connector_id == "github"
    assert evidence.scope == "repositories.read"
    assert evidence.source.system == "github"
    assert evidence.source.record_id == "Stafford0/CHIEF:123"
    assert evidence.source.record_type == "repository"
    assert evidence.sensitivity is EvidenceSensitivity.INTERNAL
    assert evidence.verifies()
    assert result.rate_limit is not None
    assert result.rate_limit.limit == 5000
    assert result.rate_limit.remaining == 4999


def test_commit_read_uses_bounded_page_and_stable_source_id() -> None:
    def transport(url: str, headers: dict[str, str]):
        del headers
        assert "per_page=2" in url
        return (
            200,
            [
                {
                    "sha": "abc123",
                    "html_url": "https://github.com/Stafford0/CHIEF/commit/abc123",
                    "commit": {"committer": {"date": "2026-09-01T18:00:00Z"}},
                }
            ],
            {},
            5.0,
        )

    connector = GitHubReadOnlyConnector(
        repositories=("Stafford0/CHIEF",), transport=transport, clock=lambda: NOW
    )
    result = connector.read("commits.read", limit=2)

    assert result.evidence[0].source.record_id == "Stafford0/CHIEF:abc123"
    assert result.evidence[0].source.record_type == "commit"
    assert result.evidence[0].observed_at < result.evidence[0].retrieved_at


def test_issues_read_filters_pull_requests_from_combined_github_endpoint() -> None:
    def transport(url: str, headers: dict[str, str]):
        del url, headers
        return (
            200,
            [
                {
                    "id": 1,
                    "number": 1,
                    "updated_at": "2026-09-01T18:00:00Z",
                    "html_url": "https://github.com/Stafford0/CHIEF/issues/1",
                },
                {
                    "id": 2,
                    "number": 2,
                    "pull_request": {},
                    "updated_at": "2026-09-01T18:00:00Z",
                    "html_url": "https://github.com/Stafford0/CHIEF/pull/2",
                },
            ],
            {},
            5.0,
        )

    connector = GitHubReadOnlyConnector(
        repositories=("Stafford0/CHIEF",), transport=transport, clock=lambda: NOW
    )
    result = connector.read("issues.read")

    assert [item.source.record_id for item in result.evidence] == ["Stafford0/CHIEF:1"]


def test_health_reports_unavailable_without_raising() -> None:
    def transport(url: str, headers: dict[str, str]):
        del url, headers
        return 503, {"message": "down"}, {}, 2.0

    connector = GitHubReadOnlyConnector(
        repositories=("Stafford0/CHIEF",), transport=transport, clock=lambda: NOW
    )

    health = connector.health()
    assert health.status is ConnectorHealthStatus.UNAVAILABLE
    assert "503" in (health.message or "")


def test_write_always_fails_closed() -> None:
    connector = GitHubReadOnlyConnector(repositories=("Stafford0/CHIEF",))

    with pytest.raises(PermissionError, match="does not permit writes"):
        connector.write(
            "repositories.write",
            {"name": "not-allowed"},
            idempotency=IdempotencyMetadata(key="test", created_at=NOW),
        )


def test_incremental_cursor_is_rejected_until_implemented() -> None:
    from chief.integrations.schema import SyncCursor

    connector = GitHubReadOnlyConnector(repositories=("Stafford0/CHIEF",))
    cursor = SyncCursor(
        connector_id="github", scope="commits.read", value="opaque", updated_at=NOW
    )

    with pytest.raises(ValueError, match="does not yet support incremental cursors"):
        connector.read("commits.read", cursor=cursor)
