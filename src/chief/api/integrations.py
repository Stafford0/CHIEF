from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from chief.integrations.evidence_plane import BusinessEvidencePlane
from chief.integrations.registry import (
    ConnectorNotRegistered,
    ConnectorRegistry,
    ConnectorScopeDenied,
    ConsentGrantError,
)
from chief.integrations.schema import ConsentGrant


class ConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scopes: tuple[str, ...] = Field(min_length=1)
    purpose: str = Field(min_length=1, max_length=1000)


class EvidenceSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scopes: tuple[str, ...] = Field(min_length=1)
    business_key: str = Field(min_length=1, max_length=200)
    business_name: str = Field(min_length=1, max_length=500)
    limit_per_scope: int = Field(default=100, ge=1, le=1000)


def _actor(request: Request) -> str:
    actor_id = getattr(request.state, "actor_id", None)
    if not isinstance(actor_id, str) or not actor_id:
        raise HTTPException(status_code=401, detail="An authenticated CHIEF actor is required.")
    return actor_id


def create_integrations_router(
    *,
    registry: ConnectorRegistry,
    evidence_plane: BusinessEvidencePlane,
) -> APIRouter:
    router = APIRouter(prefix="/integrations", tags=["integrations"])

    @router.get("")
    def list_integrations() -> list[dict[str, object]]:
        return [
            {
                "connector_id": manifest.connector_id,
                "display_name": manifest.display_name,
                "description": manifest.description,
                "capabilities": sorted(item.value for item in manifest.capabilities),
                "scopes": [
                    {
                        "name": scope.name,
                        "access": scope.access.value,
                        "description": scope.description,
                    }
                    for scope in manifest.scopes
                ],
            }
            for manifest in registry.manifests()
        ]

    @router.get("/{connector_id}/health")
    def connector_health(connector_id: str):
        try:
            return registry.health(connector_id)
        except ConnectorNotRegistered as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/{connector_id}/consent")
    def list_consent(connector_id: str, request: Request):
        return registry.consent_grants(
            connector_id=connector_id,
            principal_id=_actor(request),
        )

    @router.post("/{connector_id}/consent", status_code=201)
    def grant_consent(connector_id: str, payload: ConsentRequest, request: Request):
        grant = ConsentGrant(
            connector_id=connector_id,
            principal_id=_actor(request),
            scopes=frozenset(payload.scopes),
            purpose=payload.purpose,
            granted_at=datetime.now(UTC),
        )
        try:
            return registry.grant_consent(grant)
        except ConnectorNotRegistered as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConsentGrantError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/{connector_id}/sync")
    def sync_evidence(connector_id: str, payload: EvidenceSyncRequest, request: Request):
        try:
            return evidence_plane.sync(
                principal_id=_actor(request),
                connector_id=connector_id,
                scopes=payload.scopes,
                business_key=payload.business_key,
                business_name=payload.business_name,
                limit_per_scope=payload.limit_per_scope,
            )
        except ConnectorNotRegistered as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConnectorScopeDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/briefing/{business_key}")
    def evidence_briefing(business_key: str, request: Request, limit: int = 10):
        try:
            return evidence_plane.briefing(
                principal_id=_actor(request),
                business_key=business_key,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router
