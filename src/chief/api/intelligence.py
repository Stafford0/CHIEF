from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

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


def create_intelligence_router(
    *,
    neuromap_service: NeuromapService,
    orchestrator: SpecialistOrchestrator,
    agent_factory: AgentFactory,
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
