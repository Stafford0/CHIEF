from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from chief.audit.log import AuditEvent
from chief.core.execution_control import ExecutionControlStore
from chief.core.sqlite_session_store import SQLiteSessionStore
from chief.tools.registry import ToolRegistry


class ApprovalNarrowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, object]


class ExecutionControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)


def _actor(request: Request) -> str:
    actor_id = getattr(request.state, "actor_id", None)
    if not isinstance(actor_id, str) or not actor_id:
        raise HTTPException(status_code=401, detail="An authenticated CHIEF actor is required.")
    return actor_id


def _audit_context(request: Request, *, session_id: str, proposal_id: str) -> dict[str, str]:
    return {
        "request_id": str(request.state.request_id),
        "actor_id": _actor(request),
        "session_id": session_id,
        "proposal_id": proposal_id,
    }


def _find_proposal(
    session_store: SQLiteSessionStore,
    *,
    actor_id: str,
    proposal_id: UUID,
) -> dict[str, object]:
    for record in session_store.pending_tool_records(actor_id):
        if record["proposal_id"] == str(proposal_id):
            return record
    raise HTTPException(status_code=404, detail="Approval proposal not found or no longer pending.")


def _preview(record: dict[str, object], tool_registry: ToolRegistry) -> dict[str, object]:
    tool_name = str(record["tool_name"])
    tool = tool_registry.get(tool_name)
    if tool is None:
        raise HTTPException(status_code=409, detail="The proposal references a tool that is no longer registered.")
    definition = tool.definition
    expires_at = datetime.fromisoformat(str(record["expires_at"])).astimezone(UTC)
    return {
        **record,
        "risk": definition.risk.value,
        "requires_approval": definition.requires_approval,
        "input_schema": definition.input_schema,
        "side_effects": definition.side_effects,
        "idempotent": definition.idempotent,
        "timeout_seconds": definition.timeout_seconds,
        "expired": datetime.now(UTC) >= expires_at,
        "data_sharing": (
            "Not declared by this tool contract. Inspect the exact arguments and command target."
        ),
        "expected_side_effects": (
            "This tool declares side effects and may change local or external state."
            if definition.side_effects
            else "This tool declares no side effects."
        ),
        "rollback_plan": (
            "No automatic rollback is declared. If the action changes state, rollback must be explicit."
            if definition.side_effects
            else "No rollback is expected for a non-mutating tool."
        ),
        "verification_plan": (
            "Require a successful tool receipt, then inspect the affected target before claiming completion."
            if definition.side_effects
            else "Require a successful tool receipt before using the result."
        ),
        "allowed_decisions": ["approve", "reject", "narrow"],
        "standing_permission_available": False,
    }


def create_approvals_router(
    *,
    session_store: SQLiteSessionStore,
    tool_registry: ToolRegistry,
    execution_control: ExecutionControlStore,
    configured_execution_enabled: bool,
) -> APIRouter:
    router = APIRouter(tags=["approvals"])

    def execution_allowed() -> bool:
        return configured_execution_enabled and execution_control.get().enabled

    @router.get("/approvals")
    def list_approvals(request: Request) -> list[dict[str, object]]:
        actor_id = _actor(request)
        return [_preview(item, tool_registry) for item in session_store.pending_tool_records(actor_id)]

    @router.get("/approvals/history")
    def approval_history(request: Request, limit: int = 100):
        try:
            return session_store.proposal_decisions(_actor(request), limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/approvals/{proposal_id}")
    def get_approval(proposal_id: UUID, request: Request) -> dict[str, object]:
        return _preview(
            _find_proposal(session_store, actor_id=_actor(request), proposal_id=proposal_id),
            tool_registry,
        )

    @router.post("/approvals/{proposal_id}/approve")
    def approve(proposal_id: UUID, request: Request) -> dict[str, object]:
        actor_id = _actor(request)
        if not execution_allowed():
            raise HTTPException(
                status_code=503,
                detail="CHIEF execution is paused by the operator kill switch.",
            )
        record = _find_proposal(session_store, actor_id=actor_id, proposal_id=proposal_id)
        expires_at = datetime.fromisoformat(str(record["expires_at"])).astimezone(UTC)
        session_id = UUID(str(record["session_id"]))
        if datetime.now(UTC) >= expires_at:
            session_store.take_pending_tool(
                session_id,
                owner_id=actor_id,
                proposal_id=proposal_id,
                decision="expired",
            )
            raise HTTPException(status_code=410, detail="Approval proposal expired without execution.")
        pending = session_store.take_pending_tool(
            session_id,
            owner_id=actor_id,
            proposal_id=proposal_id,
            decision="approved",
        )
        if pending is None:
            raise HTTPException(status_code=409, detail="Approval proposal was already consumed or replaced.")
        result = tool_registry.execute(
            pending.call.tool_name,
            pending.call.arguments,
            approved=True,
            audit_context=_audit_context(
                request,
                session_id=str(session_id),
                proposal_id=str(proposal_id),
            ),
        )
        return {
            "proposal_id": str(proposal_id),
            "decision": "approved",
            "success": result.success,
            "content": result.content,
            "data": result.data,
            "error": result.error,
        }

    @router.post("/approvals/{proposal_id}/reject")
    def reject(proposal_id: UUID, request: Request) -> dict[str, object]:
        actor_id = _actor(request)
        record = _find_proposal(session_store, actor_id=actor_id, proposal_id=proposal_id)
        session_id = UUID(str(record["session_id"]))
        rejected = session_store.reject_pending_tool(
            session_id,
            proposal_id,
            owner_id=actor_id,
        )
        if rejected is None:
            raise HTTPException(status_code=409, detail="Approval proposal was already consumed or replaced.")
        tool_registry.audit_log.record(
            AuditEvent(
                tool_name=rejected.call.tool_name,
                approved=False,
                decision="rejected",
                success=False,
                metadata={"event_type": "tool.rejected"},
                **_audit_context(
                    request,
                    session_id=str(session_id),
                    proposal_id=str(proposal_id),
                ),
            )
        )
        return {"proposal_id": str(proposal_id), "decision": "rejected", "executed": False}

    @router.post("/approvals/{proposal_id}/narrow")
    def narrow(
        proposal_id: UUID,
        payload: ApprovalNarrowRequest,
        request: Request,
    ) -> dict[str, object]:
        actor_id = _actor(request)
        record = _find_proposal(session_store, actor_id=actor_id, proposal_id=proposal_id)
        session_id = UUID(str(record["session_id"]))
        tool = tool_registry.get(str(record["tool_name"]))
        if tool is None:
            raise HTTPException(status_code=409, detail="The proposal tool is no longer registered.")
        try:
            tool.validate(payload.arguments)
            replacement = session_store.narrow_pending_tool(
                session_id,
                proposal_id,
                payload.arguments,
                owner_id=actor_id,
            )
        except (TypeError, ValueError, PermissionError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if replacement is None:
            raise HTTPException(status_code=409, detail="Approval proposal was already consumed or replaced.")
        tool_registry.audit_log.record(
            AuditEvent(
                tool_name=replacement.call.tool_name,
                approved=False,
                decision="narrowed",
                success=False,
                metadata={
                    "event_type": "tool.narrowed",
                    "replacement_proposal_id": str(replacement.id),
                },
                **_audit_context(
                    request,
                    session_id=str(session_id),
                    proposal_id=str(proposal_id),
                ),
            )
        )
        replacement_record = _find_proposal(
            session_store,
            actor_id=actor_id,
            proposal_id=replacement.id,
        )
        return _preview(replacement_record, tool_registry)

    @router.get("/control/execution")
    def execution_state() -> dict[str, object]:
        state = execution_control.get()
        return {
            "configured_enabled": configured_execution_enabled,
            "operator_enabled": state.enabled,
            "effective_enabled": configured_execution_enabled and state.enabled,
            "reason": state.reason,
            "updated_at": state.updated_at.isoformat(),
            "updated_by": state.updated_by,
        }

    @router.post("/control/execution/pause")
    def pause_execution(payload: ExecutionControlRequest, request: Request) -> dict[str, object]:
        state = execution_control.set_enabled(
            False,
            actor_id=_actor(request),
            reason=payload.reason or "Operator emergency stop",
        )
        tool_registry.audit_log.record(
            AuditEvent(
                tool_name="control.execution",
                approved=True,
                decision="paused",
                success=True,
                request_id=str(request.state.request_id),
                actor_id=_actor(request),
                metadata={"event_type": "execution.paused"},
            )
        )
        return {
            "effective_enabled": False,
            "reason": state.reason,
            "updated_at": state.updated_at.isoformat(),
        }

    @router.post("/control/execution/resume")
    def resume_execution(payload: ExecutionControlRequest, request: Request) -> dict[str, object]:
        if not configured_execution_enabled:
            raise HTTPException(
                status_code=409,
                detail="CHIEF_EXECUTION_ENABLED=false prevents runtime resume until configuration changes.",
            )
        state = execution_control.set_enabled(
            True,
            actor_id=_actor(request),
            reason=payload.reason or "Operator resumed execution",
        )
        tool_registry.audit_log.record(
            AuditEvent(
                tool_name="control.execution",
                approved=True,
                decision="resumed",
                success=True,
                request_id=str(request.state.request_id),
                actor_id=_actor(request),
                metadata={"event_type": "execution.resumed"},
            )
        )
        return {
            "effective_enabled": True,
            "reason": state.reason,
            "updated_at": state.updated_at.isoformat(),
        }

    return router
