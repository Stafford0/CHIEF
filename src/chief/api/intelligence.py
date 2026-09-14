from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from chief.intelligence.evidence import SearchUnavailable
from chief.intelligence.execution import (
    SpecialistDispatchConflict,
    SpecialistRunError,
    SpecialistRunRoutingError,
    SpecialistRunService,
)
from chief.intelligence.factory import (
    AgentFactory,
    AgentFactoryError,
    AgentProposalNotFoundError,
    AgentProposalStateError,
)
from chief.intelligence.neuromap import NeuromapService
from chief.intelligence.orchestrator import SpecialistOrchestrator
from chief.intelligence.schema import (
    AgentProposal,
    AgentProposalCreate,
    AgentProposalStatus,
    AgentRouteDecision,
    AgentRoutingRequest,
    NeuromapSnapshot,
    SpecialistRunCreate,
    SpecialistRunDispatch,
)
from chief.intelligence.scout import (
    ReconScoutCreate,
    ReconScoutDispatch,
    ReconScoutDispatchConflict,
    ReconScoutError,
    ReconScoutRoutingError,
    ReconScoutScheduleCreate,
    ReconScoutScheduleView,
    ReconScoutService,
)


def _actor(request: Request) -> str:
    actor_id = getattr(request.state, "actor_id", None)
    if not isinstance(actor_id, str) or not actor_id:
        raise HTTPException(status_code=401, detail="An authenticated CHIEF actor is required.")
    return actor_id


def _factory_error(exc: Exception) -> NoReturn:
    if isinstance(exc, AgentProposalNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, AgentProposalStateError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (AgentFactoryError, ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def _specialist_run_error(exc: Exception) -> NoReturn:
    if isinstance(exc, (SpecialistRunRoutingError, SpecialistDispatchConflict)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (SpecialistRunError, ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def _scout_error(exc: Exception) -> NoReturn:
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(
        exc,
        (ReconScoutRoutingError, ReconScoutDispatchConflict, SearchUnavailable),
    ):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (ReconScoutError, ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def create_intelligence_router(
    *,
    neuromap_service: NeuromapService,
    orchestrator: SpecialistOrchestrator,
    agent_factory: AgentFactory,
    specialist_runs: SpecialistRunService | None = None,
    recon_scouts: ReconScoutService | None = None,
    record_change: Callable[[Request, str, str, str], None] | None = None,
) -> APIRouter:
    """Expose self-knowledge and governed specialist orchestration."""

    router = APIRouter(prefix="/intelligence", tags=["intelligence"])

    def changed(request: Request, action: str, entity_id: UUID) -> None:
        if record_change is not None:
            record_change(request, "intelligence", action, str(entity_id))

    @router.get("/neuromap", response_model=NeuromapSnapshot)
    def neuromap(request: Request) -> NeuromapSnapshot:
        return neuromap_service.snapshot(owner_id=_actor(request))

    @router.post("/route", response_model=AgentRouteDecision)
    def route_work(payload: AgentRoutingRequest, request: Request) -> AgentRouteDecision:
        return orchestrator.route(owner_id=_actor(request), request=payload)

    if specialist_runs is not None:

        @router.post("/specialist-runs", response_model=SpecialistRunDispatch, status_code=201)
        def create_specialist_run(
            payload: SpecialistRunCreate,
            request: Request,
        ) -> SpecialistRunDispatch:
            try:
                dispatch = specialist_runs.enqueue(owner_id=_actor(request), request=payload)
            except (SpecialistRunError, ValueError) as exc:
                _specialist_run_error(exc)
            changed(request, "specialist_run_queued", dispatch.run_id)
            return dispatch

    if recon_scouts is not None:

        @router.post("/recon/scouts", response_model=ReconScoutDispatch, status_code=201)
        def create_recon_scout(
            payload: ReconScoutCreate,
            request: Request,
        ) -> ReconScoutDispatch:
            try:
                dispatch = recon_scouts.enqueue(owner_id=_actor(request), request=payload)
            except (ReconScoutError, SearchUnavailable, ValueError) as exc:
                _scout_error(exc)
            changed(request, "recon_scout_queued", dispatch.run_id)
            return dispatch

        @router.get("/recon/scout-schedules", response_model=list[ReconScoutScheduleView])
        def list_recon_scout_schedules(request: Request) -> list[ReconScoutScheduleView]:
            return recon_scouts.list_schedules(owner_id=_actor(request))

        @router.post(
            "/recon/scout-schedules",
            response_model=ReconScoutScheduleView,
            status_code=201,
        )
        def create_recon_scout_schedule(
            payload: ReconScoutScheduleCreate,
            request: Request,
        ) -> ReconScoutScheduleView:
            try:
                schedule = recon_scouts.create_schedule(
                    owner_id=_actor(request),
                    request=payload,
                )
            except (ReconScoutError, SearchUnavailable, ValueError) as exc:
                _scout_error(exc)
            changed(request, "recon_scout_schedule_created", schedule.id)
            return schedule

        @router.post(
            "/recon/scout-schedules/{schedule_id}/pause",
            response_model=ReconScoutScheduleView,
        )
        def pause_recon_scout_schedule(
            schedule_id: UUID,
            request: Request,
        ) -> ReconScoutScheduleView:
            try:
                schedule = recon_scouts.set_schedule_status(
                    owner_id=_actor(request),
                    schedule_id=schedule_id,
                    active=False,
                )
            except (KeyError, ReconScoutError, ValueError) as exc:
                _scout_error(exc)
            changed(request, "recon_scout_schedule_paused", schedule.id)
            return schedule

        @router.post(
            "/recon/scout-schedules/{schedule_id}/resume",
            response_model=ReconScoutScheduleView,
        )
        def resume_recon_scout_schedule(
            schedule_id: UUID,
            request: Request,
        ) -> ReconScoutScheduleView:
            try:
                schedule = recon_scouts.set_schedule_status(
                    owner_id=_actor(request),
                    schedule_id=schedule_id,
                    active=True,
                )
            except (KeyError, ReconScoutError, ValueError) as exc:
                _scout_error(exc)
            changed(request, "recon_scout_schedule_resumed", schedule.id)
            return schedule

    @router.get("/agent-proposals", response_model=list[AgentProposal])
    def list_agent_proposals(
        request: Request,
        status: AgentProposalStatus | None = None,
        limit: int = 200,
    ) -> list[AgentProposal]:
        try:
            return agent_factory.proposal_store.list(
                owner_id=_actor(request),
                status=status,
                limit=limit,
            )
        except ValueError as exc:
            _factory_error(exc)

    @router.post("/agent-proposals", response_model=AgentProposal, status_code=201)
    def propose_agent(payload: AgentProposalCreate, request: Request) -> AgentProposal:
        try:
            proposal = agent_factory.propose(owner_id=_actor(request), request=payload)
        except (AgentFactoryError, ValueError) as exc:
            _factory_error(exc)
        changed(request, "agent_proposed", proposal.id)
        return proposal

    @router.post("/agent-proposals/{proposal_id}/approve", response_model=AgentProposal)
    def approve_agent(proposal_id: UUID, request: Request) -> AgentProposal:
        try:
            proposal = agent_factory.approve(owner_id=_actor(request), proposal_id=proposal_id)
        except (AgentFactoryError, ValueError) as exc:
            _factory_error(exc)
        changed(request, "agent_materialized", proposal.id)
        return proposal

    @router.post("/agent-proposals/{proposal_id}/reject", response_model=AgentProposal)
    def reject_agent(proposal_id: UUID, request: Request) -> AgentProposal:
        try:
            proposal = agent_factory.reject(owner_id=_actor(request), proposal_id=proposal_id)
        except (AgentFactoryError, ValueError) as exc:
            _factory_error(exc)
        changed(request, "agent_proposal_rejected", proposal.id)
        return proposal

    return router
