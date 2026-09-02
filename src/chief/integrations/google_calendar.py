from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
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
        raise ConnectionError(f"Google Calendar request failed: {exc.reason}") from exc


class GoogleCalendarReadOnlyConnector:
    """Read primary-calendar event changes with provider-issued sync tokens."""

    _manifest = ConnectorManifest(
        connector_id="google-calendar",
        display_name="Google Calendar",
        description="Read-only Google Calendar event evidence.",
        capabilities=frozenset({ConnectorCapability.READ, ConnectorCapability.INCREMENTAL_SYNC}),
        scopes=(
            ConnectorScope("events.read", ScopeAccess.READ, "Read calendar event changes."),
        ),
    )

    def __init__(
        self,
        *,
        token_provider: Callable[[], str | None],
        calendar_id: str = "primary",
        transport: Transport = _default_transport,
        clock: Callable[[], datetime] = utc_now,
        api_base: str = "https://www.googleapis.com/calendar/v3",
    ) -> None:
        if not calendar_id.strip():
            raise ValueError("calendar_id must not be empty")
        if not api_base.startswith("https://"):
            raise ValueError("Google Calendar API base must use HTTPS")
        self._token_provider = token_provider
        self._calendar_id = calendar_id.strip()
        self._transport = transport
        self._clock = clock
        self._api_base = api_base.rstrip("/")

    @property
    def manifest(self) -> ConnectorManifest:
        return self._manifest

    def _headers(self) -> dict[str, str]:
        token = self._token_provider()
        if not token:
            raise ConnectionError("Google Calendar OAuth access token is not configured")
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
        if status == 410:
            raise ValueError("Google Calendar sync token expired; clear the cursor and perform a full sync")
        if not 200 <= status < 300:
            detail = payload.get("error") if isinstance(payload, Mapping) else None
            raise ConnectionError(
                f"Google Calendar API returned HTTP {status}: {detail or 'request failed'}"
            )
        return payload, headers, latency_ms

    def health(self) -> ConnectorHealth:
        checked_at = self._clock()
        started = time.perf_counter()
        try:
            self._get("/users/me/calendarList", query={"maxResults": 1})
        except (ConnectionError, ValueError) as exc:
            return ConnectorHealth(
                connector_id="google-calendar",
                status=ConnectorHealthStatus.UNAVAILABLE,
                checked_at=checked_at,
                message=str(exc),
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        return ConnectorHealth(
            connector_id="google-calendar",
            status=ConnectorHealthStatus.HEALTHY,
            checked_at=checked_at,
            message="Google Calendar API reachable.",
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    @staticmethod
    def _observed_at(event: Mapping[str, Any], fallback: datetime) -> datetime:
        value = event.get("updated")
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value).astimezone(UTC)
            except ValueError:
                pass
        return fallback

    def read(
        self,
        scope: str,
        *,
        cursor: SyncCursor | None = None,
        limit: int = 100,
    ) -> ConnectorReadResult:
        if scope != "events.read":
            raise PermissionError(f"unsupported Google Calendar scope: {scope}")
        if not 1 <= limit <= 1000:
            raise ValueError("Google Calendar read limit must be between 1 and 1000")
        if cursor is not None and (
            cursor.connector_id != "google-calendar" or cursor.scope != scope
        ):
            raise ValueError("Google Calendar sync cursor must match connector and scope")

        retrieved_at = self._clock()
        sync_token = cursor.value if cursor is not None else None
        page_token: str | None = None
        evidence: list[EvidenceRecord] = []
        next_sync_token: str | None = None

        while len(evidence) < limit:
            page_size = min(2500, limit - len(evidence))
            query: dict[str, object] = {
                "maxResults": page_size,
                "showDeleted": "true",
                "pageToken": page_token,
            }
            if sync_token is not None:
                query["syncToken"] = sync_token
            payload, _, _ = self._get(
                f"/calendars/{quote(self._calendar_id, safe='')}/events",
                query=query,
            )
            if not isinstance(payload, Mapping):
                raise ConnectionError("Google Calendar returned an unexpected response")
            items = payload.get("items", [])
            if not isinstance(items, list):
                raise ConnectionError("Google Calendar returned an unexpected items field")
            for event in items:
                if len(evidence) >= limit:
                    break
                if not isinstance(event, Mapping) or not isinstance(event.get("id"), str):
                    continue
                event_id = str(event["id"])
                observed_at = min(self._observed_at(event, retrieved_at), retrieved_at)
                selected = {
                    key: event.get(key)
                    for key in (
                        "id",
                        "status",
                        "summary",
                        "description",
                        "location",
                        "start",
                        "end",
                        "organizer",
                        "attendees",
                        "updated",
                        "htmlLink",
                    )
                    if key in event
                }
                content = json.dumps(
                    selected,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                link = event.get("htmlLink")
                evidence.append(
                    EvidenceRecord.capture(
                        connector_id="google-calendar",
                        scope=scope,
                        source=EvidenceSource(
                            system="google-calendar",
                            record_id=event_id,
                            record_type="calendar_event",
                        ),
                        content=content,
                        observed_at=observed_at,
                        retrieved_at=retrieved_at,
                        confidence=1.0,
                        deep_link=link if isinstance(link, str) else None,
                        sensitivity=EvidenceSensitivity.CONFIDENTIAL,
                    )
                )
            page = payload.get("nextPageToken")
            if isinstance(page, str) and page and len(evidence) < limit:
                page_token = page
                continue
            token = payload.get("nextSyncToken")
            if isinstance(token, str) and token:
                next_sync_token = token
            break

        next_cursor = None
        if next_sync_token is not None:
            next_cursor = SyncCursor(
                connector_id="google-calendar",
                scope=scope,
                value=next_sync_token,
                updated_at=retrieved_at,
            )
        return ConnectorReadResult(evidence=tuple(evidence), next_cursor=next_cursor)

    def write(
        self,
        scope: str,
        payload: Mapping[str, object],
        *,
        idempotency: IdempotencyMetadata,
    ) -> ConnectorWriteResult:
        del scope, payload, idempotency
        raise PermissionError("GoogleCalendarReadOnlyConnector does not permit writes")
