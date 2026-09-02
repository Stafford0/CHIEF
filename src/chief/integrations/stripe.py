from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from chief.integrations.schema import (
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
    ScopeAccess,
    SyncCursor,
    utc_now,
)

Transport = Callable[[str, Mapping[str, str]], tuple[int, Any, Mapping[str, str], float]]


def _default_transport(
    url: str, headers: Mapping[str, str]
) -> tuple[int, Any, Mapping[str, str], float]:
    request = Request(url, headers=dict(headers), method="GET")
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else None
            return (
                response.status,
                payload,
                dict(response.headers.items()),
                (time.perf_counter() - started) * 1000,
            )
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = {"error": {"message": raw}}
        return exc.code, payload, dict(exc.headers.items()), (time.perf_counter() - started) * 1000
    except URLError as exc:
        raise ConnectionError(f"Stripe request failed: {exc.reason}") from exc


def _cursor_time(cursor: SyncCursor | None) -> datetime | None:
    if cursor is None:
        return None
    try:
        return datetime.fromisoformat(cursor.value).astimezone(UTC)
    except ValueError as exc:
        raise ValueError("Stripe sync cursor must contain an ISO-8601 timestamp") from exc


class StripeReadOnlyConnector:
    """Read revenue and subscription state without exposing Stripe mutation capability."""

    _manifest = ConnectorManifest(
        connector_id="stripe",
        display_name="Stripe",
        description="Read-only Stripe charge and subscription evidence.",
        capabilities=frozenset({ConnectorCapability.READ, ConnectorCapability.INCREMENTAL_SYNC}),
        scopes=(
            ConnectorScope("charges.read", ScopeAccess.READ, "Read recent charges."),
            ConnectorScope("subscriptions.read", ScopeAccess.READ, "Read subscription state."),
        ),
    )

    def __init__(
        self,
        *,
        api_key_provider: Callable[[], str | None],
        transport: Transport = _default_transport,
        clock: Callable[[], datetime] = utc_now,
        api_base: str = "https://api.stripe.com/v1",
    ) -> None:
        if not api_base.startswith("https://"):
            raise ValueError("Stripe API base must use HTTPS")
        self._api_key_provider = api_key_provider
        self._transport = transport
        self._clock = clock
        self._api_base = api_base.rstrip("/")

    @property
    def manifest(self) -> ConnectorManifest:
        return self._manifest

    def _headers(self) -> dict[str, str]:
        api_key = self._api_key_provider()
        if not api_key:
            raise ConnectionError("Stripe restricted API key is not configured")
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "CHIEF/0.0.1",
        }

    def _get(
        self, path: str, *, query: Mapping[str, object] | None = None
    ) -> tuple[Any, Mapping[str, str], float]:
        url = f"{self._api_base}{path}"
        if query:
            url = f"{url}?{urlencode({k: v for k, v in query.items() if v is not None})}"
        status, payload, headers, latency_ms = self._transport(url, self._headers())
        if not 200 <= status < 300:
            detail = None
            if isinstance(payload, Mapping):
                error = payload.get("error")
                if isinstance(error, Mapping):
                    message = error.get("message")
                    if isinstance(message, str):
                        detail = message
            raise ConnectionError(f"Stripe API returned HTTP {status}: {detail or 'request failed'}")
        return payload, headers, latency_ms

    def health(self) -> ConnectorHealth:
        checked_at = self._clock()
        started = time.perf_counter()
        try:
            self._get("/balance")
        except ConnectionError as exc:
            return ConnectorHealth(
                connector_id="stripe",
                status=ConnectorHealthStatus.UNAVAILABLE,
                checked_at=checked_at,
                message=str(exc),
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        return ConnectorHealth(
            connector_id="stripe",
            status=ConnectorHealthStatus.HEALTHY,
            checked_at=checked_at,
            message="Stripe API reachable.",
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    @staticmethod
    def _created_at(item: Mapping[str, Any], fallback: datetime) -> datetime:
        value = item.get("created")
        try:
            return datetime.fromtimestamp(int(str(value)), tz=UTC)
        except (TypeError, ValueError, OSError):
            return fallback

    def _read_collection(
        self,
        *,
        path: str,
        scope: str,
        record_type: str,
        since: datetime | None,
        limit: int,
        retrieved_at: datetime,
    ) -> tuple[EvidenceRecord, ...]:
        evidence: list[EvidenceRecord] = []
        starting_after: str | None = None
        while len(evidence) < limit:
            page_limit = min(100, limit - len(evidence))
            query: dict[str, object] = {
                "limit": page_limit,
                "starting_after": starting_after,
            }
            if since is not None:
                query["created[gt]"] = int(since.timestamp())
            payload, _, _ = self._get(path, query=query)
            if not isinstance(payload, Mapping):
                raise ConnectionError("Stripe returned an unexpected collection response")
            items = payload.get("data", [])
            if not isinstance(items, list):
                raise ConnectionError("Stripe returned an unexpected data field")
            for item in items:
                if len(evidence) >= limit:
                    break
                if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                    continue
                item_id = str(item["id"])
                observed_at = min(self._created_at(item, retrieved_at), retrieved_at)
                content = json.dumps(
                    item,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                evidence.append(
                    EvidenceRecord.capture(
                        connector_id="stripe",
                        scope=scope,
                        source=EvidenceSource(
                            system="stripe",
                            record_id=item_id,
                            record_type=record_type,
                        ),
                        content=content,
                        observed_at=observed_at,
                        retrieved_at=retrieved_at,
                        confidence=1.0,
                        deep_link=f"https://dashboard.stripe.com/{record_type}s/{item_id}",
                        sensitivity=EvidenceSensitivity.CONFIDENTIAL,
                    )
                )
            has_more = payload.get("has_more") is True
            if not has_more or not items:
                break
            last = items[-1]
            starting_after = str(last.get("id")) if isinstance(last, Mapping) else None
            if not starting_after:
                break
        return tuple(evidence)

    def read(
        self,
        scope: str,
        *,
        cursor: SyncCursor | None = None,
        limit: int = 100,
    ) -> ConnectorReadResult:
        if scope not in {"charges.read", "subscriptions.read"}:
            raise PermissionError(f"unsupported Stripe scope: {scope}")
        if not 1 <= limit <= 1000:
            raise ValueError("Stripe read limit must be between 1 and 1000")
        if cursor is not None and (cursor.connector_id != "stripe" or cursor.scope != scope):
            raise ValueError("Stripe sync cursor must match connector and scope")
        since = _cursor_time(cursor)
        retrieved_at = self._clock()
        if since is not None and since > retrieved_at:
            raise ValueError("Stripe sync cursor cannot be in the future")

        if scope == "charges.read":
            evidence = self._read_collection(
                path="/charges",
                scope=scope,
                record_type="charge",
                since=since,
                limit=limit,
                retrieved_at=retrieved_at,
            )
        else:
            evidence = self._read_collection(
                path="/subscriptions",
                scope=scope,
                record_type="subscription",
                since=since,
                limit=limit,
                retrieved_at=retrieved_at,
            )

        return ConnectorReadResult(
            evidence=evidence,
            next_cursor=SyncCursor(
                connector_id="stripe",
                scope=scope,
                value=retrieved_at.isoformat(),
                updated_at=retrieved_at,
            ),
        )

    def write(
        self,
        scope: str,
        payload: Mapping[str, object],
        *,
        idempotency: IdempotencyMetadata,
    ) -> ConnectorWriteResult:
        del scope, payload, idempotency
        raise PermissionError("StripeReadOnlyConnector does not permit writes")
