"""Read-only GitHub evidence connector.

The adapter deliberately exposes observation scopes only. Credentials are supplied lazily by a
callable and are never persisted by CHIEF. Incremental cursors are timestamp based and remain
provider-independent through the shared SyncCursor contract.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .schema import (
    ConnectorCapability,
    ConnectorHealth,
    ConnectorHealthStatus,
    ConnectorManifest,
    ConnectorReadResult,
    ConnectorScope,
    ConnectorWriteResult,
    EvidenceRecord,
    EvidenceSensitivity,
    EvidenceSource,
    IdempotencyMetadata,
    RateLimitMetadata,
    ScopeAccess,
    SyncCursor,
    utc_now,
)

JsonObject = Mapping[str, Any]
Transport = Callable[[str, Mapping[str, str]], tuple[int, Any, Mapping[str, str], float]]


def _parse_time(value: object, fallback: datetime) -> datetime:
    if not isinstance(value, str) or not value:
        return fallback
    return datetime.fromisoformat(value).astimezone(UTC)


def _cursor_time(cursor: SyncCursor | None) -> datetime | None:
    if cursor is None:
        return None
    try:
        value = datetime.fromisoformat(cursor.value).astimezone(UTC)
    except ValueError as exc:
        raise ValueError("GitHub sync cursor must contain an ISO-8601 timestamp") from exc
    return value


def _rate_limit(headers: Mapping[str, str]) -> RateLimitMetadata | None:
    lowered = {key.lower(): value for key, value in headers.items()}
    try:
        limit = int(lowered["x-ratelimit-limit"]) if "x-ratelimit-limit" in lowered else None
        remaining = (
            int(lowered["x-ratelimit-remaining"])
            if "x-ratelimit-remaining" in lowered
            else None
        )
        reset_at = (
            datetime.fromtimestamp(int(lowered["x-ratelimit-reset"]), tz=UTC)
            if "x-ratelimit-reset" in lowered
            else None
        )
    except (TypeError, ValueError):
        return None
    if limit is None and remaining is None and reset_at is None:
        return None
    return RateLimitMetadata(limit=limit, remaining=remaining, reset_at=reset_at)


def _default_transport(
    url: str, headers: Mapping[str, str]
) -> tuple[int, Any, Mapping[str, str], float]:
    request = Request(url, headers=dict(headers), method="GET")
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
            latency_ms = (time.perf_counter() - started) * 1000
            payload = json.loads(raw) if raw else None
            return response.status, payload, dict(response.headers.items()), latency_ms
    except HTTPError as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = {"message": raw}
        return exc.code, payload, dict(exc.headers.items()), latency_ms
    except URLError as exc:
        raise ConnectionError(f"GitHub request failed: {exc.reason}") from exc


class GitHubReadOnlyConnector:
    """Observe configured GitHub repositories without exposing mutation capability."""

    _manifest = ConnectorManifest(
        connector_id="github",
        display_name="GitHub",
        description="Read-only repository, commit, issue, and pull-request evidence.",
        capabilities=frozenset(
            {ConnectorCapability.READ, ConnectorCapability.INCREMENTAL_SYNC}
        ),
        scopes=(
            ConnectorScope("repositories.read", ScopeAccess.READ, "Read repository metadata."),
            ConnectorScope("commits.read", ScopeAccess.READ, "Read recent repository commits."),
            ConnectorScope("issues.read", ScopeAccess.READ, "Read recent repository issues."),
            ConnectorScope("pulls.read", ScopeAccess.READ, "Read recent pull requests."),
        ),
    )

    def __init__(
        self,
        *,
        repositories: tuple[str, ...],
        token_provider: Callable[[], str | None] = lambda: None,
        transport: Transport = _default_transport,
        clock: Callable[[], datetime] = utc_now,
        api_base: str = "https://api.github.com",
    ) -> None:
        if not repositories:
            raise ValueError("at least one GitHub repository must be configured")
        for repository in repositories:
            if repository.count("/") != 1 or not all(
                part.strip() for part in repository.split("/")
            ):
                raise ValueError("GitHub repositories must use owner/name form")
        if not api_base.startswith("https://"):
            raise ValueError("GitHub API base must use HTTPS")
        self._repositories = tuple(dict.fromkeys(repositories))
        self._token_provider = token_provider
        self._transport = transport
        self._clock = clock
        self._api_base = api_base.rstrip("/")

    @property
    def manifest(self) -> ConnectorManifest:
        return self._manifest

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "CHIEF/0.0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = self._token_provider()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _get(
        self, path: str, *, query: Mapping[str, object] | None = None
    ) -> tuple[Any, Mapping[str, str], float]:
        url = f"{self._api_base}{path}"
        if query:
            encoded = urlencode({key: value for key, value in query.items() if value is not None})
            url = f"{url}?{encoded}"
        status, payload, headers, latency_ms = self._transport(url, self._headers())
        if not 200 <= status < 300:
            message = payload.get("message") if isinstance(payload, Mapping) else None
            raise ConnectionError(
                f"GitHub API returned HTTP {status}: {message or 'request failed'}"
            )
        return payload, headers, latency_ms

    def health(self) -> ConnectorHealth:
        checked_at = self._clock()
        started = time.perf_counter()
        try:
            _, _, latency_ms = self._get("/rate_limit")
        except ConnectionError as exc:
            return ConnectorHealth(
                connector_id="github",
                status=ConnectorHealthStatus.UNAVAILABLE,
                checked_at=checked_at,
                message=str(exc),
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        return ConnectorHealth(
            connector_id="github",
            status=ConnectorHealthStatus.HEALTHY,
            checked_at=checked_at,
            message="GitHub API reachable.",
            latency_ms=latency_ms,
        )

    def read(
        self,
        scope: str,
        *,
        cursor: SyncCursor | None = None,
        limit: int = 100,
    ) -> ConnectorReadResult:
        if not 1 <= limit <= 1000:
            raise ValueError("read limit must be between 1 and 1000")
        if self.manifest.scope(scope) is None:
            raise PermissionError(f"undeclared GitHub scope: {scope}")
        if cursor is not None and (
            cursor.connector_id != "github" or cursor.scope != scope
        ):
            raise ValueError("GitHub sync cursor must match connector and scope")

        since = _cursor_time(cursor)
        retrieved_at = self._clock()
        if since is not None and since > retrieved_at:
            raise ValueError("GitHub sync cursor cannot be in the future")

        evidence: list[EvidenceRecord] = []
        latest_rate_limit: RateLimitMetadata | None = None
        remaining = limit

        for repository in self._repositories:
            if remaining <= 0:
                break
            records, headers = self._read_repository_scope(
                repository,
                scope,
                limit=remaining,
                retrieved_at=retrieved_at,
                since=since,
            )
            evidence.extend(records)
            remaining = limit - len(evidence)
            latest_rate_limit = _rate_limit(headers) or latest_rate_limit

        next_cursor = SyncCursor(
            connector_id="github",
            scope=scope,
            value=retrieved_at.isoformat(),
            updated_at=retrieved_at,
        )
        return ConnectorReadResult(
            evidence=tuple(evidence),
            next_cursor=next_cursor,
            rate_limit=latest_rate_limit,
        )

    def _read_repository_scope(
        self,
        repository: str,
        scope: str,
        *,
        limit: int,
        retrieved_at: datetime,
        since: datetime | None,
    ) -> tuple[list[EvidenceRecord], Mapping[str, str]]:
        per_page = min(limit, 100)
        if scope == "repositories.read":
            payload, headers, _ = self._get(f"/repos/{repository}")
            items = [payload]
            record_type = "repository"
        elif scope == "commits.read":
            payload, headers, _ = self._get(
                f"/repos/{repository}/commits",
                query={
                    "per_page": per_page,
                    "since": since.isoformat().replace("+00:00", "Z") if since else None,
                },
            )
            items = payload
            record_type = "commit"
        elif scope == "issues.read":
            payload, headers, _ = self._get(
                f"/repos/{repository}/issues",
                query={
                    "state": "all",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": per_page,
                    "since": since.isoformat().replace("+00:00", "Z") if since else None,
                },
            )
            if not isinstance(payload, list):
                raise ConnectionError("GitHub returned an unexpected response shape")
            items = [item for item in payload if "pull_request" not in item]
            record_type = "issue"
        elif scope == "pulls.read":
            payload, headers, _ = self._get(
                f"/repos/{repository}/pulls",
                query={
                    "state": "all",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": per_page,
                },
            )
            items = payload
            record_type = "pull_request"
        else:
            raise PermissionError(f"unsupported GitHub scope: {scope}")

        if not isinstance(items, list):
            raise ConnectionError("GitHub returned an unexpected response shape")
        records = [
            self._capture(repository, scope, record_type, item, retrieved_at)
            for item in items[:limit]
            if isinstance(item, Mapping)
        ]
        if since is not None:
            records = [item for item in records if item.observed_at > since]
        return records, headers

    def _capture(
        self,
        repository: str,
        scope: str,
        record_type: str,
        item: JsonObject,
        retrieved_at: datetime,
    ) -> EvidenceRecord:
        if record_type == "repository":
            record_id = str(item.get("id", repository))
            observed_at = _parse_time(
                item.get("updated_at") or item.get("pushed_at"), retrieved_at
            )
        elif record_type == "commit":
            record_id = str(item.get("sha", "unknown"))
            commit = item.get("commit") if isinstance(item.get("commit"), Mapping) else {}
            committer = (
                commit.get("committer") if isinstance(commit.get("committer"), Mapping) else {}
            )
            observed_at = _parse_time(committer.get("date"), retrieved_at)
        else:
            record_id = str(item.get("id") or item.get("number") or "unknown")
            observed_at = _parse_time(
                item.get("updated_at") or item.get("created_at"), retrieved_at
            )

        content = json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if record_type == "repository" and item.get("private") is False:
            sensitivity = EvidenceSensitivity.PUBLIC
        else:
            sensitivity = EvidenceSensitivity.INTERNAL
        deep_link = item.get("html_url") if isinstance(item.get("html_url"), str) else None
        return EvidenceRecord.capture(
            connector_id="github",
            scope=scope,
            source=EvidenceSource(
                system="github",
                record_id=f"{repository}:{record_id}",
                record_type=record_type,
            ),
            content=content,
            observed_at=min(observed_at, retrieved_at),
            retrieved_at=retrieved_at,
            confidence=1.0,
            deep_link=deep_link,
            sensitivity=sensitivity,
        )

    def write(
        self,
        scope: str,
        payload: Mapping[str, object],
        *,
        idempotency: IdempotencyMetadata,
    ) -> ConnectorWriteResult:
        del scope, payload, idempotency
        raise PermissionError("GitHubReadOnlyConnector does not permit writes")
