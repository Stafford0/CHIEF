from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
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

Transport = Callable[
    [str, Mapping[str, str], bytes | None], tuple[int, Any, Mapping[str, str], float]
]


def _default_transport(
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
) -> tuple[int, Any, Mapping[str, str], float]:
    request = Request(url, headers=dict(headers), data=body, method="POST")
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
        return (
            exc.code,
            payload,
            dict(exc.headers.items()),
            (time.perf_counter() - started) * 1000,
        )
    except URLError as exc:
        raise ConnectionError(f"ParcelSignals data request failed: {exc.reason}") from exc


def _as_of(payload: Mapping[str, Any], fallback: datetime) -> datetime:
    value = payload.get("as_of")
    if not isinstance(value, str) or value == "Source timestamp unavailable":
        return fallback
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except ValueError:
        return fallback


class ParcelSignalsReadOnlyConnector:
    """Consume ParcelSignals' approved bounded server-side national overview RPC."""

    _manifest = ConnectorManifest(
        connector_id="parcelsignals",
        display_name="Parcel Signals Data",
        description="Read-only Parcel Signals national coverage and freshness overview.",
        capabilities=frozenset({ConnectorCapability.READ}),
        scopes=(
            ConnectorScope(
                "national.overview.read",
                ScopeAccess.READ,
                "Read the bounded national overview exposed by Parcel Signals' server RPC.",
            ),
        ),
    )

    def __init__(
        self,
        *,
        supabase_url: str,
        secret_provider: Callable[[], str | None],
        transport: Transport = _default_transport,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        normalized = supabase_url.strip().rstrip("/")
        if not normalized.startswith("https://"):
            raise ValueError("ParcelSignals Supabase URL must use HTTPS")
        self._rpc_url = (
            normalized + "/rest/v1/rpc/parcelsignals_national_overview"
        )
        self._secret_provider = secret_provider
        self._transport = transport
        self._clock = clock

    @property
    def manifest(self) -> ConnectorManifest:
        return self._manifest

    def _headers(self) -> dict[str, str]:
        secret = self._secret_provider()
        if not secret:
            raise ConnectionError("ParcelSignals Supabase server secret is not configured")
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {secret}",
            "apikey": secret,
            "User-Agent": "CHIEF/0.0.1",
        }

    def _overview(self) -> tuple[Mapping[str, Any], float]:
        status, payload, _, latency_ms = self._transport(
            self._rpc_url,
            self._headers(),
            b"{}",
        )
        if not 200 <= status < 300:
            detail = payload.get("message") if isinstance(payload, Mapping) else None
            raise ConnectionError(
                f"ParcelSignals data API returned HTTP {status}: {detail or 'request failed'}"
            )
        if not isinstance(payload, Mapping):
            raise ConnectionError("ParcelSignals national overview returned an unexpected response")
        return payload, latency_ms

    def health(self) -> ConnectorHealth:
        checked_at = self._clock()
        started = time.perf_counter()
        try:
            _, latency_ms = self._overview()
        except ConnectionError as exc:
            return ConnectorHealth(
                connector_id="parcelsignals",
                status=ConnectorHealthStatus.UNAVAILABLE,
                checked_at=checked_at,
                message=str(exc),
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        return ConnectorHealth(
            connector_id="parcelsignals",
            status=ConnectorHealthStatus.HEALTHY,
            checked_at=checked_at,
            message="ParcelSignals national overview reachable.",
            latency_ms=latency_ms,
        )

    def read(
        self,
        scope: str,
        *,
        cursor: SyncCursor | None = None,
        limit: int = 100,
    ) -> ConnectorReadResult:
        if scope != "national.overview.read":
            raise PermissionError(f"unsupported ParcelSignals scope: {scope}")
        if cursor is not None:
            raise ValueError("ParcelSignals national overview does not use incremental cursors")
        if not 1 <= limit <= 1000:
            raise ValueError("ParcelSignals read limit must be between 1 and 1000")

        retrieved_at = self._clock()
        payload, _ = self._overview()
        observed_at = min(_as_of(payload, retrieved_at), retrieved_at)
        content = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        evidence = EvidenceRecord.capture(
            connector_id="parcelsignals",
            scope=scope,
            source=EvidenceSource(
                system="parcelsignals",
                record_id="national-overview",
                record_type="business_intelligence_overview",
            ),
            content=content,
            observed_at=observed_at,
            retrieved_at=retrieved_at,
            confidence=1.0,
            deep_link=None,
            sensitivity=EvidenceSensitivity.INTERNAL,
        )
        return ConnectorReadResult(evidence=(evidence,))

    def write(
        self,
        scope: str,
        payload: Mapping[str, object],
        *,
        idempotency: IdempotencyMetadata,
    ) -> ConnectorWriteResult:
        del scope, payload, idempotency
        raise PermissionError("ParcelSignalsReadOnlyConnector does not permit writes")
