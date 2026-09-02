from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Callable, Mapping
from email.message import EmailMessage
from email.utils import parseaddr
from threading import RLock
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

HttpTransport = Callable[
    [str, str, Mapping[str, str], bytes | None],
    tuple[int, Any, Mapping[str, str], float],
]


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
) -> tuple[int, Any, Mapping[str, str], float]:
    request = Request(url, data=body, headers=dict(headers), method=method)
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else None
            return (
                int(response.status),
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
            int(exc.code),
            payload,
            dict(exc.headers.items()),
            (time.perf_counter() - started) * 1000,
        )
    except (URLError, TimeoutError, OSError) as exc:
        raise ConnectionError(f"Gmail draft request failed: {exc}") from exc


class GmailDraftConnector:
    """Create reversible Gmail drafts; this connector has no send capability."""

    _manifest = ConnectorManifest(
        connector_id="gmail_drafts",
        display_name="Gmail Drafts",
        description="Approval-gated creation of plain-text Gmail drafts without sending email.",
        capabilities=frozenset({ConnectorCapability.WRITE}),
        scopes=(
            ConnectorScope(
                "drafts.create",
                ScopeAccess.WRITE,
                "Create a plain-text Gmail draft. This scope does not expose send operations.",
            ),
        ),
    )

    def __init__(
        self,
        *,
        token_provider: Callable[[], str | None],
        transport: HttpTransport = _default_transport,
        clock=utc_now,
        api_base: str = "https://gmail.googleapis.com/gmail/v1",
    ) -> None:
        if not api_base.startswith("https://"):
            raise ValueError("Gmail API base must use HTTPS")
        self._token_provider = token_provider
        self._transport = transport
        self._clock = clock
        self._api_base = api_base.rstrip("/")
        self._write_lock = RLock()

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
            "Content-Type": "application/json",
            "User-Agent": "CHIEF/0.0.1",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, object] | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> tuple[Any, Mapping[str, str], float]:
        url = f"{self._api_base}{path}"
        if query:
            params = {key: value for key, value in query.items() if value is not None}
            url = f"{url}?{urlencode(params, doseq=True)}"
        body = None
        if payload is not None:
            body = json.dumps(
                payload,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        status, response, headers, latency_ms = self._transport(
            method,
            url,
            self._headers(),
            body,
        )
        if not 200 <= status < 300:
            detail = response.get("error") if isinstance(response, Mapping) else None
            raise ConnectionError(
                f"Gmail draft API returned HTTP {status}: {detail or 'request failed'}"
            )
        return response, headers, latency_ms

    def health(self) -> ConnectorHealth:
        checked_at = self._clock()
        started = time.perf_counter()
        try:
            self._request("GET", "/users/me/profile")
        except ConnectionError as exc:
            return ConnectorHealth(
                connector_id="gmail_drafts",
                status=ConnectorHealthStatus.UNAVAILABLE,
                checked_at=checked_at,
                message=str(exc),
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        return ConnectorHealth(
            connector_id="gmail_drafts",
            status=ConnectorHealthStatus.HEALTHY,
            checked_at=checked_at,
            message="Gmail API reachable for draft operations.",
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def read(
        self,
        scope: str,
        *,
        cursor: SyncCursor | None = None,
        limit: int = 100,
    ) -> ConnectorReadResult:
        del scope, cursor, limit
        raise PermissionError("GmailDraftConnector does not expose read scopes")

    @staticmethod
    def _validate_payload(payload: Mapping[str, object]) -> tuple[str, str, str]:
        allowed = {"to", "subject", "body"}
        extra = set(payload) - allowed
        if extra:
            names = ", ".join(sorted(extra))
            raise ValueError(f"Gmail draft payload contains unsupported fields: {names}")
        to = payload.get("to")
        subject = payload.get("subject")
        body = payload.get("body")
        if not isinstance(to, str) or not to.strip() or len(to) > 320:
            raise ValueError("Gmail draft 'to' must be a non-empty email address")
        _, address = parseaddr(to)
        if not address or "@" not in address or address != to.strip():
            raise ValueError("Gmail draft 'to' must contain exactly one plain email address")
        if not isinstance(subject, str) or not subject.strip() or len(subject) > 998:
            raise ValueError("Gmail draft subject must contain 1 to 998 characters")
        if not isinstance(body, str) or not body.strip() or len(body) > 200_000:
            raise ValueError("Gmail draft body must contain 1 to 200,000 characters")
        return address, subject.strip(), body

    @staticmethod
    def _message_id(idempotency: IdempotencyMetadata) -> str:
        digest = hashlib.sha256(idempotency.key.encode("utf-8")).hexdigest()[:40]
        return f"<chief-{digest}@local.chief>"

    @staticmethod
    def _raw_message(to: str, subject: str, body: str, message_id: str) -> str:
        message = EmailMessage()
        message["To"] = to
        message["Subject"] = subject
        message["Message-ID"] = message_id
        message["X-CHIEF-Draft"] = "approved"
        message.set_content(body)
        return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")

    def _existing_draft(self, message_id: str) -> tuple[str, str | None] | None:
        listing, _, _ = self._request(
            "GET",
            "/users/me/drafts",
            query={"maxResults": 10, "q": f"rfc822msgid:{message_id}"},
        )
        if not isinstance(listing, Mapping):
            raise ConnectionError("Gmail returned an unexpected draft-list response")
        drafts = listing.get("drafts", [])
        if not isinstance(drafts, list):
            raise ConnectionError("Gmail returned an unexpected drafts field")
        for draft in drafts:
            if not isinstance(draft, Mapping) or not isinstance(draft.get("id"), str):
                continue
            message = draft.get("message")
            message_external_id = (
                str(message.get("id"))
                if isinstance(message, Mapping) and isinstance(message.get("id"), str)
                else None
            )
            return str(draft["id"]), message_external_id
        return None

    def _evidence(
        self,
        *,
        draft_id: str,
        message_external_id: str | None,
        to: str,
        subject: str,
        idempotency: IdempotencyMetadata,
        recovered: bool,
    ) -> EvidenceRecord:
        captured_at = self._clock()
        content = json.dumps(
            {
                "draft_id": draft_id,
                "message_id": message_external_id,
                "to": to,
                "subject": subject,
                "recovered_existing": recovered,
                "idempotency_digest": hashlib.sha256(
                    idempotency.key.encode("utf-8")
                ).hexdigest(),
                "sent": False,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        deep_link_id = message_external_id or draft_id
        return EvidenceRecord.capture(
            connector_id="gmail_drafts",
            scope="drafts.create",
            source=EvidenceSource(
                system="gmail",
                record_id=draft_id,
                record_type="email_draft",
            ),
            content=content,
            observed_at=captured_at,
            retrieved_at=captured_at,
            confidence=1.0,
            deep_link=f"https://mail.google.com/mail/u/0/#drafts/{deep_link_id}",
            sensitivity=EvidenceSensitivity.CONFIDENTIAL,
        )

    def write(
        self,
        scope: str,
        payload: Mapping[str, object],
        *,
        idempotency: IdempotencyMetadata,
    ) -> ConnectorWriteResult:
        if scope != "drafts.create":
            raise PermissionError(f"unsupported Gmail draft scope: {scope}")
        to, subject, body = self._validate_payload(payload)
        message_id = self._message_id(idempotency)
        with self._write_lock:
            existing = self._existing_draft(message_id)
            if existing is not None:
                draft_id, external_message_id = existing
                evidence = self._evidence(
                    draft_id=draft_id,
                    message_external_id=external_message_id,
                    to=to,
                    subject=subject,
                    idempotency=idempotency,
                    recovered=True,
                )
                return ConnectorWriteResult(
                    success=True,
                    idempotency=idempotency,
                    evidence=(evidence,),
                    external_id=draft_id,
                )

            response, _, _ = self._request(
                "POST",
                "/users/me/drafts",
                payload={"message": {"raw": self._raw_message(to, subject, body, message_id)}},
            )
            if not isinstance(response, Mapping) or not isinstance(response.get("id"), str):
                raise ConnectionError("Gmail returned an unexpected draft-create response")
            draft_id = str(response["id"])
            message = response.get("message")
            external_message_id = (
                str(message.get("id"))
                if isinstance(message, Mapping) and isinstance(message.get("id"), str)
                else None
            )
            evidence = self._evidence(
                draft_id=draft_id,
                message_external_id=external_message_id,
                to=to,
                subject=subject,
                idempotency=idempotency,
                recovered=False,
            )
            return ConnectorWriteResult(
                success=True,
                idempotency=idempotency,
                evidence=(evidence,),
                external_id=draft_id,
            )
