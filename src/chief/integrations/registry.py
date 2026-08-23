"""Deny-by-default connector registration and consent enforcement."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from threading import RLock
from uuid import UUID

from .base import Connector
from .schema import (
    ConnectorHealth,
    ConnectorManifest,
    ConnectorReadResult,
    ConnectorWriteResult,
    ConsentGrant,
    EvidenceRecord,
    IdempotencyMetadata,
    ScopeAccess,
    SyncCursor,
    utc_now,
)


class ConnectorRegistryError(RuntimeError):
    """Base error for connector policy or registration failures."""


class ConnectorNotRegistered(ConnectorRegistryError):
    """Raised when code attempts to use an unknown connector."""


class ConnectorAlreadyRegistered(ConnectorRegistryError):
    """Raised when registration would replace an existing connector implicitly."""


class ConsentGrantError(ConnectorRegistryError):
    """Raised when a grant is invalid for the registered manifest."""


class ConnectorScopeDenied(PermissionError, ConnectorRegistryError):
    """Raised when registration, declaration, access type, or consent is missing."""


class ConnectorResponseError(ConnectorRegistryError):
    """Raised when a connector returns data outside its authorized boundary."""


class ConnectorRegistry:
    """In-memory policy boundary around provider connector objects.

    Registration never grants access. An operation requires an exact declared scope,
    a matching access type, and an active consent grant for the calling principal.
    """

    def __init__(self, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._clock = clock
        self._connectors: dict[str, Connector] = {}
        self._manifests: dict[str, ConnectorManifest] = {}
        self._grants: dict[UUID, ConsentGrant] = {}
        self._lock = RLock()

    def register(self, connector: Connector) -> ConnectorManifest:
        manifest = connector.manifest
        with self._lock:
            if manifest.connector_id in self._connectors:
                raise ConnectorAlreadyRegistered(
                    f"connector {manifest.connector_id!r} is already registered"
                )
            self._connectors[manifest.connector_id] = connector
            self._manifests[manifest.connector_id] = manifest
        return manifest

    def unregister(self, connector_id: str) -> None:
        with self._lock:
            if connector_id not in self._connectors:
                raise ConnectorNotRegistered(f"connector {connector_id!r} is not registered")
            del self._connectors[connector_id]
            del self._manifests[connector_id]
            revoked_at = self._clock()
            for grant_id, grant in tuple(self._grants.items()):
                if grant.connector_id == connector_id and grant.revoked_at is None:
                    effective_revocation = max(revoked_at, grant.granted_at)
                    self._grants[grant_id] = replace(grant, revoked_at=effective_revocation)

    def manifests(self) -> tuple[ConnectorManifest, ...]:
        with self._lock:
            return tuple(sorted(self._manifests.values(), key=lambda item: item.connector_id))

    def grant_consent(self, grant: ConsentGrant) -> ConsentGrant:
        """Record an explicit grant after validating every exact scope."""

        with self._lock:
            self._registered(grant.connector_id)
            declared = {scope.name for scope in self._manifests[grant.connector_id].scopes}
            unknown = grant.scopes - declared
            if unknown:
                names = ", ".join(sorted(unknown))
                raise ConsentGrantError(f"grant contains undeclared scopes: {names}")
            if grant.id in self._grants:
                raise ConsentGrantError(f"consent grant {grant.id} already exists")
            self._grants[grant.id] = grant
        return grant

    def revoke_consent(
        self,
        grant_id: UUID,
        *,
        revoked_at: datetime | None = None,
    ) -> ConsentGrant:
        """Replace an active grant with an immutable revocation record."""

        with self._lock:
            try:
                grant = self._grants[grant_id]
            except KeyError as exc:
                raise ConsentGrantError(f"consent grant {grant_id} does not exist") from exc
            if grant.revoked_at is not None:
                return grant
            revoked = replace(grant, revoked_at=revoked_at or self._clock())
            self._grants[grant_id] = revoked
            return revoked

    def consent_grants(
        self,
        *,
        connector_id: str | None = None,
        principal_id: str | None = None,
    ) -> tuple[ConsentGrant, ...]:
        """Return grant records, including expired and revoked records for audit."""

        with self._lock:
            grants = tuple(self._grants.values())
        if connector_id is not None:
            grants = tuple(item for item in grants if item.connector_id == connector_id)
        if principal_id is not None:
            grants = tuple(item for item in grants if item.principal_id == principal_id)
        return tuple(sorted(grants, key=lambda item: (item.granted_at, str(item.id))))

    def health(self, connector_id: str) -> ConnectorHealth:
        with self._lock:
            connector = self._registered(connector_id)
        health = connector.health()
        if health.connector_id != connector_id:
            raise ConnectorResponseError("health result does not match the requested connector")
        return health

    def read(
        self,
        connector_id: str,
        scope: str,
        *,
        principal_id: str,
        cursor: SyncCursor | None = None,
        limit: int = 100,
    ) -> ConnectorReadResult:
        if not 1 <= limit <= 1000:
            raise ValueError("read limit must be between 1 and 1000")
        connector, _ = self._authorize(connector_id, scope, principal_id, ScopeAccess.READ)
        if cursor is not None and (cursor.connector_id != connector_id or cursor.scope != scope):
            raise ValueError("sync cursor must match the requested connector and scope")
        result = connector.read(scope, cursor=cursor, limit=limit)
        self._validate_evidence(connector_id, scope, result.evidence)
        if result.next_cursor is not None and (
            result.next_cursor.connector_id != connector_id or result.next_cursor.scope != scope
        ):
            raise ConnectorResponseError(
                "connector returned a sync cursor outside the authorized connector and scope"
            )
        return result

    def write(
        self,
        connector_id: str,
        scope: str,
        payload: Mapping[str, object],
        *,
        principal_id: str,
        idempotency: IdempotencyMetadata,
    ) -> ConnectorWriteResult:
        if idempotency is None:
            raise ValueError("write operations require idempotency metadata")
        now = self._clock()
        if not idempotency.is_valid_at(now):
            raise ValueError("idempotency metadata has expired")
        connector, _ = self._authorize(
            connector_id,
            scope,
            principal_id,
            ScopeAccess.WRITE,
            now=now,
        )
        result = connector.write(scope, payload, idempotency=idempotency)
        if result.idempotency != idempotency:
            raise ConnectorResponseError("connector returned mismatched idempotency metadata")
        self._validate_evidence(connector_id, scope, result.evidence)
        return result

    def _registered(self, connector_id: str) -> Connector:
        try:
            return self._connectors[connector_id]
        except KeyError as exc:
            raise ConnectorNotRegistered(f"connector {connector_id!r} is not registered") from exc

    def _authorize(
        self,
        connector_id: str,
        scope_name: str,
        principal_id: str,
        access: ScopeAccess,
        *,
        now: datetime | None = None,
    ) -> tuple[Connector, ConsentGrant]:
        comparison_time = now or self._clock()
        with self._lock:
            connector = self._registered(connector_id)
            scope = self._manifests[connector_id].scope(scope_name)
            if scope is None:
                raise ConnectorScopeDenied(
                    f"scope {scope_name!r} is not declared by connector {connector_id!r}"
                )
            if scope.access is not access:
                raise ConnectorScopeDenied(
                    f"scope {scope_name!r} grants {scope.access.value}, not {access.value} access"
                )

            grant = next(
                (
                    item
                    for item in self._grants.values()
                    if item.connector_id == connector_id
                    and item.principal_id == principal_id
                    and item.allows(scope_name, comparison_time)
                ),
                None,
            )
            if grant is None:
                raise ConnectorScopeDenied(
                    f"principal {principal_id!r} has no active consent for "
                    f"{connector_id!r}:{scope_name!r}"
                )
            return connector, grant

    @staticmethod
    def _validate_evidence(
        connector_id: str,
        scope: str,
        evidence: tuple[EvidenceRecord, ...],
    ) -> None:
        for item in evidence:
            if item.connector_id != connector_id or item.scope != scope:
                raise ConnectorResponseError(
                    "connector returned evidence outside the authorized connector and scope"
                )
            if not item.verifies():
                raise ConnectorResponseError("connector returned evidence with an invalid digest")
