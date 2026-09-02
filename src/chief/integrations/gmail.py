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
            payload = {"message": raw}
        return exc.code, payload, dict(exc.headers.items()), (time.perf_counter() - started) * 1000
    except URLError as exc:
        raise ConnectionError(f"Gmail request failed: {exc.reason}") from exc


def _cursor_time(cursor: SyncCursor | None) -> datetime | None:
    if cursor is None:
        return None
    try:
        return datetime.fromisoformat(cursor.value).astimezone(UTC)
    except ValueError as exc:
        raise ValueError("Gmail sync cursor must contain an ISO-8601 timestamp") from exc


def _header_map(payload: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    headers = payload.get("headers")
    if not isinstance(headers, list):
        return result
    for item in headers:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        value = item.get("value")
        if isinstance(name, str) and isinstance(value, str):
            result[name.casefold()] = value
    return result


class GmailReadOnlyConnector:
    """Read Gmail message metadata without retrieving message bodies."""

    _manifest = ConnectorManifest(
        connector_id="gmail",
        display_name="Gmail",
        description="Read-only Gmail message metadata for business evidence.",
        capabilities=frozenset(
            {ConnectorCapability.READ, ConnectorCapability.INCREMENTAL_SYNC, ConnectorCapability.SEARCH}
        ),
        scopes=(
            ConnectorScope(
                "messages.metadata.read",
                ScopeAccess.READ,
                "Read recent message metadata, headers, labels, and snippets without message bodies.",
            ),
        ),
    )

    def __init__(
        self,
        *,
        token_provider: Callable[[], str | None],
        transport: Transport = _default_transport,
        clock: Callable[[], datetime] = utc_now,
        api_base: str = "https://gmail.googleapis.com/gmail/v1",
    ) -> None:
        if not api_base.startswith("https://"):
            raise ValueError("Gmail API base must use HTTPS")
        self._token_provider = token_provider
        self._transport = transport
        self._clock = clock
        self._api_base = api_base.rstrip("/")

    @property
    def manifest(self) -> ConnectorManifest:
        return self._manifest

    def _headers(self) -> dict[str, str]:
        token = self._token_provider()
        if not token:
            raise ConnectionError("Gmail OAuth access token is not configured")
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "CHIEF/0.0.1",
        }

    def _get(
        self, path: str, *, query: Mapping[str, object] | None = None
    ) -> tuple[Any, Mapping[str, str], float]:
        url = f"{self._api_base}{path}"
        if query:
            url = f"{url}?{urlencode({k: v for k, v in query.items() if v is not None}, doseq=True)}"
        status, payload, headers, latency_ms = self._transport(url, self._headers())
        if not 200 <= status < 300:
            detail = payload.get("error") if isinstance(payload, Mapping) else None
            raise ConnectionError(f"Gmail API returned HTTP {status}: {detail or 'request failed'}")
        return payload, headers, latency_ms

    def health(self) -> ConnectorHealth:
        checked_at = self._clock()
        started = time.perf_counter()
        try:
            self._get("/users/me/profile")
        except ConnectionError as exc:
            return ConnectorHealth(
                connector_id="gmail",
                status=ConnectorHealthStatus.UNAVAILABLE,
                checked_at=checked_at,
                message=str(exc),
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        return ConnectorHealth(
            connector_id="gmail",
            status=ConnectorHealthStatus.HEALTHY,
            checked_at=checked_at,
            message="Gmail API reachable.",
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def read(
        self,
        scope: str,
        *,
        cursor: SyncCursor | None = None,
        limit: int = 100,
    ) -> ConnectorReadResult:
        if scope != "messages.metadata.read":
            raise PermissionError(f"unsupported Gmail scope: {scope}")
        if not 1 <= limit <= 500:
            raise ValueError("Gmail read limit must be between 1 and 500")
        if cursor is not None and (cursor.connector_id != "gmail" or cursor.scope != scope):
            raise ValueError("Gmail sync cursor must match connector and scope")
        since = _cursor_time(cursor)
        retrieved_at = self._clock()
        if since is not None and since > retrieved_at:
            raise ValueError("Gmail sync cursor cannot be in the future")

        query = None
        if since is not None:
            query = f"after:{int(since.timestamp())}"
        listing, _, _ = self._get(
            "/users/me/messages",
            query={"maxResults": limit, "q": query},
        )
        if not isinstance(listing, Mapping):
            raise ConnectionError("Gmail returned an unexpected list response")
        messages = listing.get("messages", [])
        if not isinstance(messages, list):
            raise ConnectionError("Gmail returned an unexpected messages field")

        evidence: list[EvidenceRecord] = []
        for item in messages[:limit]:
            if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                continue
            message_id = str(item["id"])
            message, _, _ = self._get(
                f"/users/me/messages/{message_id}",
                query={
                    "format": "metadata",
                    "metadataHeaders": ["Subject", "From", "To", "Date", "Message-ID"],
                },
            )
            if not isinstance(message, Mapping):
                continue
            payload = message.get("payload")
            header_values = _header_map(payload) if isinstance(payload, Mapping) else {}
            internal_date = message.get("internalDate")
            try:
                observed_at = datetime.fromtimestamp(int(str(internal_date)) / 1000, tz=UTC)
            except (TypeError, ValueError):
                observed_at = retrieved_at
            content = json.dumps(
                {
                    "id": message_id,
                    "threadId": message.get("threadId"),
                    "labelIds": message.get("labelIds", []),
                    "snippet": message.get("snippet", ""),
                    "subject": header_values.get("subject"),
                    "from": header_values.get("from"),
                    "to": header_values.get("to"),
                    "date": header_values.get("date"),
                    "message_id": header_values.get("message-id"),
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            evidence.append(
                EvidenceRecord.capture(
                    connector_id="gmail",
                    scope=scope,
                    source=EvidenceSource(
                        system="gmail",
                        record_id=message_id,
                        record_type="email_metadata",
                    ),
                    content=content,
                    observed_at=min(observed_at, retrieved_at),
                    retrieved_at=retrieved_at,
                    confidence=1.0,
                    deep_link=f"https://mail.google.com/mail/u/0/#all/{message_id}",
                    sensitivity=EvidenceSensitivity.CONFIDENTIAL,
                )
            )

        return ConnectorReadResult(
            evidence=tuple(evidence),
            next_cursor=SyncCursor(
                connector_id="gmail",
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
        raise PermissionError("GmailReadOnlyConnector does not permit writes")
