from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chief.business import (
    BusinessGraphConflict,
    BusinessNodeKind,
    BusinessRelationship,
    Document,
    Organization,
    Provenance,
    ProvenanceType,
    RelationshipKind,
    SQLiteBusinessGraphStore,
    Sensitivity,
)
from chief.integrations.registry import ConnectorRegistry
from chief.integrations.schema import EvidenceRecord, EvidenceSensitivity, SyncCursor


@dataclass(frozen=True, slots=True)
class EvidenceSyncSummary:
    connector_id: str
    business_key: str
    scopes: tuple[str, ...]
    observed: int
    created: int
    unchanged: int
    next_cursors: dict[str, str]


class SQLiteEvidenceSyncStore:
    """Persist connector cursors without storing credentials or source content."""

    def __init__(self, database_path: str | Path = "data/chief.db") -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS integration_sync_cursors (
                    principal_id TEXT NOT NULL,
                    connector_id TEXT NOT NULL,
                    business_key TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    cursor_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (principal_id, connector_id, business_key, scope)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def get(
        self, *, principal_id: str, connector_id: str, business_key: str, scope: str
    ) -> SyncCursor | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT cursor_value, updated_at
                FROM integration_sync_cursors
                WHERE principal_id = ? AND connector_id = ? AND business_key = ? AND scope = ?
                """,
                (principal_id, connector_id, business_key, scope),
            ).fetchone()
        if row is None:
            return None
        return SyncCursor(
            connector_id=connector_id,
            scope=scope,
            value=str(row["cursor_value"]),
            updated_at=datetime.fromisoformat(str(row["updated_at"])).astimezone(UTC),
        )

    def put(self, *, principal_id: str, business_key: str, cursor: SyncCursor) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO integration_sync_cursors (
                    principal_id, connector_id, business_key, scope, cursor_value, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(principal_id, connector_id, business_key, scope)
                DO UPDATE SET cursor_value = excluded.cursor_value, updated_at = excluded.updated_at
                """,
                (
                    principal_id,
                    cursor.connector_id,
                    business_key,
                    cursor.scope,
                    cursor.value,
                    cursor.updated_at.astimezone(UTC).isoformat(),
                ),
            )


class BusinessEvidencePlane:
    """Move consent-authorized connector observations into durable business context."""

    def __init__(
        self,
        *,
        registry: ConnectorRegistry,
        business_store: SQLiteBusinessGraphStore,
        sync_store: SQLiteEvidenceSyncStore | None = None,
    ) -> None:
        self.registry = registry
        self.business_store = business_store
        self.sync_store = sync_store or SQLiteEvidenceSyncStore(business_store.database_path)

    @staticmethod
    def _sensitivity(value: EvidenceSensitivity) -> Sensitivity:
        return Sensitivity(value.value)

    @staticmethod
    def _business_key(value: str) -> str:
        normalized = value.strip().casefold().replace(" ", "-")
        if not normalized or len(normalized) > 200:
            raise ValueError("business_key must contain between 1 and 200 characters")
        return normalized

    def _organization(self, *, owner_id: str, business_key: str, business_name: str) -> Organization:
        key = f"business:{business_key}"
        existing = self.business_store.find_node(
            owner_id=owner_id,
            kind=BusinessNodeKind.ORGANIZATION,
            key=key,
        )
        if existing is not None:
            if not isinstance(existing, Organization):
                raise RuntimeError("business key is already occupied by a non-organization node")
            return existing
        organization = Organization(
            key=key,
            name=business_name.strip() or business_key,
            owner_id=owner_id,
            provenance=Provenance(source_type=ProvenanceType.SYSTEM, source_id="evidence-plane"),
            tags=["business", business_key],
        )
        try:
            return self.business_store.add_node(organization)
        except BusinessGraphConflict:
            concurrent = self.business_store.find_node(
                owner_id=owner_id,
                kind=BusinessNodeKind.ORGANIZATION,
                key=key,
            )
            if isinstance(concurrent, Organization):
                return concurrent
            raise

    @staticmethod
    def _document_key(evidence: EvidenceRecord) -> str:
        source = f"{evidence.connector_id}:{evidence.source.system}:{evidence.source.record_id}"
        source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]
        return f"evidence:{source_hash}:{evidence.content_digest[:24]}"

    @staticmethod
    def _document_name(evidence: EvidenceRecord) -> str:
        try:
            payload = json.loads(evidence.content)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            for key in ("title", "name", "full_name"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()[:500]
            commit = payload.get("commit")
            if isinstance(commit, dict):
                message = commit.get("message")
                if isinstance(message, str) and message.strip():
                    return message.strip().splitlines()[0][:500]
        return f"{evidence.source.record_type}: {evidence.source.record_id}"[:500]

    def _persist_evidence(
        self,
        *,
        owner_id: str,
        organization: Organization,
        business_key: str,
        evidence: EvidenceRecord,
    ) -> bool:
        key = self._document_key(evidence)
        existing = self.business_store.find_node(
            owner_id=owner_id,
            kind=BusinessNodeKind.DOCUMENT,
            key=key,
        )
        if existing is not None:
            return False

        document = Document(
            key=key,
            name=self._document_name(evidence),
            description=evidence.content[:20_000],
            owner_id=owner_id,
            sensitivity=self._sensitivity(evidence.sensitivity),
            confidence=evidence.confidence,
            valid_from=evidence.observed_at,
            created_at=evidence.retrieved_at,
            updated_at=evidence.retrieved_at,
            uri=evidence.deep_link,
            document_type=evidence.source.record_type,
            content_digest=evidence.content_digest,
            provenance=Provenance(
                source_type=ProvenanceType.INTEGRATION,
                source_id=f"{evidence.connector_id}:{evidence.source.record_id}",
                source_uri=evidence.deep_link,
                captured_at=evidence.retrieved_at,
                evidence_digest=evidence.content_digest,
            ),
            tags=["evidence", business_key, evidence.connector_id, evidence.source.record_type],
        )
        try:
            saved = self.business_store.add_node(document)
        except BusinessGraphConflict:
            return False
        self.business_store.add_relationship(
            BusinessRelationship(
                source_id=organization.id,
                target_id=saved.id,
                kind=RelationshipKind.DOCUMENTS,
                label=f"{evidence.connector_id}:{evidence.source.record_type}",
                owner_id=owner_id,
                sensitivity=saved.sensitivity,
                confidence=evidence.confidence,
                valid_from=evidence.observed_at,
                created_at=evidence.retrieved_at,
                updated_at=evidence.retrieved_at,
                provenance=saved.provenance,
            )
        )
        return True

    def sync(
        self,
        *,
        principal_id: str,
        connector_id: str,
        scopes: tuple[str, ...],
        business_key: str,
        business_name: str,
        limit_per_scope: int = 100,
    ) -> EvidenceSyncSummary:
        if not scopes:
            raise ValueError("at least one evidence scope is required")
        if not 1 <= limit_per_scope <= 1000:
            raise ValueError("limit_per_scope must be between 1 and 1000")
        business_key = self._business_key(business_key)
        organization = self._organization(
            owner_id=principal_id,
            business_key=business_key,
            business_name=business_name,
        )
        observed = 0
        created = 0
        next_cursors: dict[str, str] = {}

        for scope in dict.fromkeys(scopes):
            cursor = self.sync_store.get(
                principal_id=principal_id,
                connector_id=connector_id,
                business_key=business_key,
                scope=scope,
            )
            result = self.registry.read(
                connector_id,
                scope,
                principal_id=principal_id,
                cursor=cursor,
                limit=limit_per_scope,
            )
            observed += len(result.evidence)
            for evidence in result.evidence:
                if self._persist_evidence(
                    owner_id=principal_id,
                    organization=organization,
                    business_key=business_key,
                    evidence=evidence,
                ):
                    created += 1
            if result.next_cursor is not None:
                self.sync_store.put(
                    principal_id=principal_id,
                    business_key=business_key,
                    cursor=result.next_cursor,
                )
                next_cursors[scope] = result.next_cursor.value

        return EvidenceSyncSummary(
            connector_id=connector_id,
            business_key=business_key,
            scopes=tuple(dict.fromkeys(scopes)),
            observed=observed,
            created=created,
            unchanged=observed - created,
            next_cursors=next_cursors,
        )

    def briefing(
        self,
        *,
        principal_id: str,
        business_key: str,
        limit: int = 10,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ValueError("briefing limit must be between 1 and 100")
        business_key = self._business_key(business_key)
        documents = [
            node
            for node in self.business_store.list_nodes(
                owner_id=principal_id,
                kinds=[BusinessNodeKind.DOCUMENT],
                limit=1000,
            )
            if isinstance(node, Document)
            and "evidence" in node.tags
            and business_key in node.tags
        ]
        documents.sort(key=lambda item: item.updated_at, reverse=True)
        selected = documents[:limit]
        source_counts: dict[str, int] = {}
        for document in documents:
            source = next(
                (tag for tag in document.tags if tag not in {"evidence", business_key}),
                "unknown",
            )
            source_counts[source] = source_counts.get(source, 0) + 1
        return {
            "business_key": business_key,
            "evidence_count": len(documents),
            "source_counts": source_counts,
            "items": [
                {
                    "title": item.name,
                    "record_type": item.document_type,
                    "observed_at": item.valid_from.isoformat(),
                    "captured_at": item.updated_at.isoformat(),
                    "confidence": item.confidence,
                    "source_uri": item.uri,
                    "evidence_digest": item.content_digest,
                    "why_now": "Recently observed source evidence",
                    "verified": item.content_digest is not None,
                }
                for item in selected
            ],
            "unverified": [] if documents else ["No source evidence has been synchronized yet."],
        }
