from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from chief.cofounder import CanonicalBriefing, build_canonical_briefing
from chief.operator import EventRecoveryAction, OperatorRecoveryService, OperatorStatus
from chief.release import SchemaCompatibilityReport, SchemaCompatibilityService


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2_000)


def _actor(request: Request) -> str:
    actor_id = getattr(request.state, "actor_id", None)
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise HTTPException(status_code=401, detail="An authenticated CHIEF actor is required.")
    return actor_id.strip()


def create_cofounder_router(*, database_path: str | Path) -> APIRouter:
    router = APIRouter(tags=["cofounder"])
    recovery = OperatorRecoveryService(database_path)
    schema = SchemaCompatibilityService(database_path)

    @router.get("/cofounder/briefing", response_model=CanonicalBriefing)
    def canonical_briefing(request: Request, limit: int = 7) -> CanonicalBriefing:
        try:
            return build_canonical_briefing(
                database_path,
                principal_id=_actor(request),
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/operator/status", response_model=OperatorStatus)
    def operator_status(request: Request) -> OperatorStatus:
        _actor(request)
        return recovery.status()

    @router.get("/operator/schema-compatibility", response_model=SchemaCompatibilityReport)
    def schema_compatibility(request: Request) -> SchemaCompatibilityReport:
        _actor(request)
        return schema.inspect()

    @router.get("/operator/dead-letters")
    def dead_letters(request: Request, limit: int = 100):
        _actor(request)
        try:
            return recovery.list_dead_letters(limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/operator/dead-letters/{event_id}/retry")
    def retry_dead_letter(event_id: UUID, payload: RecoveryRequest, request: Request):
        try:
            event, action = recovery.retry(
                event_id,
                actor_id=_actor(request),
                reason=payload.reason,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"event": event, "recovery_action": action}

    @router.post("/operator/dead-letters/{event_id}/dismiss")
    def dismiss_dead_letter(event_id: UUID, payload: RecoveryRequest, request: Request):
        try:
            event, action = recovery.dismiss(
                event_id,
                actor_id=_actor(request),
                reason=payload.reason,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"event": event, "recovery_action": action}

    @router.get("/operator/recovery-history", response_model=list[EventRecoveryAction])
    def recovery_history(request: Request, event_id: UUID | None = None, limit: int = 100):
        _actor(request)
        try:
            return recovery.history(event_id=event_id, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router


__all__ = ["create_cofounder_router"]
