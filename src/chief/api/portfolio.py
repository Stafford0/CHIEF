from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import NoReturn
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from chief.portfolio import (
    KPI,
    AgentHeartbeat,
    AgentRole,
    BusinessUnit,
    CredentialReference,
    DataSensitivity,
    FinancialAccountKind,
    FinancialAccountReference,
    HeartbeatReport,
    ManagedAgent,
    OnboardingState,
    PortfolioConflictError,
    PortfolioHierarchyError,
    PortfolioNotFoundError,
    PortfolioScope,
    PortfolioScopeError,
    PortfolioSummary,
    PortfolioValidationError,
    SQLitePortfolioStore,
    SystemKind,
    SystemRegistration,
)


class BusinessCreate(BaseModel):
    """Safe registration fields; activation and authority are intentionally absent."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=300)
    mission: str = Field(default="", max_length=5_000)
    graph_node_id: UUID | None = None
    kpis: list[KPI] = Field(default_factory=list, max_length=100)
    review_due_at: datetime | None = None


class AgentCreate(BaseModel):
    """Register an inert managed agent without accepting an execution grant."""

    model_config = ConfigDict(extra="forbid")

    business_id: UUID | None = None
    parent_agent_id: UUID | None = None
    role: AgentRole
    scope: PortfolioScope
    name: str = Field(min_length=1, max_length=300)
    mission: str = Field(min_length=1, max_length=5_000)
    kpis: list[KPI] = Field(default_factory=list, max_length=100)
    heartbeat_interval_seconds: int = Field(default=300, ge=30, le=86_400)
    review_due_at: datetime | None = None


class SystemCreate(BaseModel):
    """Register system metadata; read and write access remain disabled."""

    model_config = ConfigDict(extra="forbid")

    business_id: UUID | None = None
    scope: PortfolioScope
    kind: SystemKind
    name: str = Field(min_length=1, max_length=300)
    provider: str | None = Field(default=None, max_length=200)
    endpoint: str | None = Field(default=None, max_length=2_000)
    credential_reference: CredentialReference | None = None
    sensitivity: DataSensitivity = DataSensitivity.CONFIDENTIAL
    contains_personal_data: bool = False


class FinancialAccountCreate(BaseModel):
    """Register non-secret account metadata with all financial access disabled."""

    model_config = ConfigDict(extra="forbid")

    business_id: UUID | None = None
    scope: PortfolioScope
    kind: FinancialAccountKind
    account_alias: str = Field(min_length=1, max_length=200)
    institution: str = Field(min_length=1, max_length=300)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    provider_account_id_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    credential_reference: CredentialReference | None = None


def _actor(request: Request) -> str:
    actor_id = getattr(request.state, "actor_id", None)
    if not isinstance(actor_id, str) or not actor_id:
        raise HTTPException(status_code=401, detail="An authenticated CHIEF actor is required.")
    return actor_id


def _portfolio_error(exc: Exception) -> NoReturn:
    if isinstance(exc, PortfolioConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, PortfolioNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(
        exc,
        (PortfolioScopeError, PortfolioHierarchyError, PortfolioValidationError, ValueError),
    ):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def create_portfolio_router(
    *,
    portfolio_store: SQLitePortfolioStore,
    record_change: Callable[[Request, str, str, str], None] | None = None,
) -> APIRouter:
    """Expose the empty-by-default portfolio registry through a fail-closed API."""

    router = APIRouter(prefix="/portfolio", tags=["portfolio"])

    def changed(request: Request, domain: str, action: str, entity_id: UUID) -> None:
        if record_change is not None:
            record_change(request, domain, action, str(entity_id))

    @router.get("")
    def portfolio_state(request: Request) -> dict[str, PortfolioSummary | OnboardingState]:
        owner_id = _actor(request)
        return {
            "summary": portfolio_store.summary(owner_id=owner_id),
            "onboarding": portfolio_store.onboarding_state(owner_id=owner_id),
        }

    @router.get("/summary", response_model=PortfolioSummary)
    def portfolio_summary(request: Request) -> PortfolioSummary:
        return portfolio_store.summary(owner_id=_actor(request))

    @router.get("/onboarding", response_model=OnboardingState)
    def portfolio_onboarding(request: Request) -> OnboardingState:
        return portfolio_store.onboarding_state(owner_id=_actor(request))

    @router.get("/businesses", response_model=list[BusinessUnit])
    def list_businesses(
        request: Request,
        include_retired: bool = False,
        limit: int = 200,
    ) -> list[BusinessUnit]:
        try:
            return portfolio_store.list_businesses(
                owner_id=_actor(request),
                include_retired=include_retired,
                limit=limit,
            )
        except ValueError as exc:
            _portfolio_error(exc)

    @router.post("/businesses", response_model=BusinessUnit, status_code=201)
    def create_business(payload: BusinessCreate, request: Request) -> BusinessUnit:
        try:
            business = BusinessUnit(owner_id=_actor(request), **payload.model_dump())
            saved = portfolio_store.create_business(business)
        except (PortfolioConflictError, PortfolioScopeError, ValueError) as exc:
            _portfolio_error(exc)
        changed(request, "portfolio_business", "registered", saved.id)
        return saved

    @router.get("/businesses/{business_id}", response_model=BusinessUnit)
    def get_business(business_id: UUID, request: Request) -> BusinessUnit:
        business = portfolio_store.get_business(business_id, owner_id=_actor(request))
        if business is None:
            raise HTTPException(status_code=404, detail="Portfolio business not found.")
        return business

    @router.get("/agents", response_model=list[ManagedAgent])
    def list_agents(
        request: Request,
        business_id: UUID | None = None,
        scope: PortfolioScope | None = None,
        include_retired: bool = False,
        limit: int = 200,
    ) -> list[ManagedAgent]:
        try:
            return portfolio_store.list_agents(
                owner_id=_actor(request),
                business_id=business_id,
                scope=scope,
                include_retired=include_retired,
                limit=limit,
            )
        except ValueError as exc:
            _portfolio_error(exc)

    @router.post("/agents", response_model=ManagedAgent, status_code=201)
    def create_agent(payload: AgentCreate, request: Request) -> ManagedAgent:
        try:
            agent = ManagedAgent(owner_id=_actor(request), **payload.model_dump())
            saved = portfolio_store.create_agent(agent)
        except (
            PortfolioConflictError,
            PortfolioHierarchyError,
            PortfolioScopeError,
            ValueError,
        ) as exc:
            _portfolio_error(exc)
        changed(request, "portfolio_agent", "registered", saved.id)
        return saved

    @router.get("/agents/{agent_id}", response_model=ManagedAgent)
    def get_agent(agent_id: UUID, request: Request) -> ManagedAgent:
        agent = portfolio_store.get_agent(agent_id, owner_id=_actor(request))
        if agent is None:
            raise HTTPException(status_code=404, detail="Managed agent not found.")
        return agent

    @router.post("/agents/{agent_id}/pause", response_model=ManagedAgent)
    def pause_agent(agent_id: UUID, request: Request) -> ManagedAgent:
        try:
            agent = portfolio_store.pause_agent(owner_id=_actor(request), agent_id=agent_id)
        except (PortfolioNotFoundError, PortfolioValidationError) as exc:
            _portfolio_error(exc)
        changed(request, "portfolio_agent", "paused", agent.id)
        return agent

    @router.post(
        "/agents/{agent_id}/heartbeats",
        response_model=AgentHeartbeat,
        status_code=201,
    )
    def record_heartbeat(
        agent_id: UUID,
        report: HeartbeatReport,
        request: Request,
    ) -> AgentHeartbeat:
        try:
            heartbeat = portfolio_store.record_heartbeat(
                owner_id=_actor(request),
                agent_id=agent_id,
                report=report,
            )
        except (PortfolioNotFoundError, PortfolioValidationError, ValueError) as exc:
            _portfolio_error(exc)
        changed(request, "portfolio_heartbeat", "recorded", heartbeat.id)
        return heartbeat

    @router.get(
        "/agents/{agent_id}/heartbeats",
        response_model=list[AgentHeartbeat],
    )
    def list_heartbeats(
        agent_id: UUID,
        request: Request,
        limit: int = 100,
    ) -> list[AgentHeartbeat]:
        if portfolio_store.get_agent(agent_id, owner_id=_actor(request)) is None:
            raise HTTPException(status_code=404, detail="Managed agent not found.")
        try:
            return portfolio_store.list_heartbeats(
                owner_id=_actor(request),
                agent_id=agent_id,
                limit=limit,
            )
        except ValueError as exc:
            _portfolio_error(exc)

    @router.get("/systems", response_model=list[SystemRegistration])
    def list_systems(
        request: Request,
        business_id: UUID | None = None,
        scope: PortfolioScope | None = None,
        include_retired: bool = False,
        limit: int = 200,
    ) -> list[SystemRegistration]:
        try:
            return portfolio_store.list_systems(
                owner_id=_actor(request),
                business_id=business_id,
                scope=scope,
                include_retired=include_retired,
                limit=limit,
            )
        except ValueError as exc:
            _portfolio_error(exc)

    @router.post("/systems", response_model=SystemRegistration, status_code=201)
    def create_system(payload: SystemCreate, request: Request) -> SystemRegistration:
        try:
            system = SystemRegistration(owner_id=_actor(request), **payload.model_dump())
            saved = portfolio_store.create_system(system)
        except (PortfolioConflictError, PortfolioScopeError, ValueError) as exc:
            _portfolio_error(exc)
        changed(request, "portfolio_system", "registered", saved.id)
        return saved

    @router.get("/systems/{system_id}", response_model=SystemRegistration)
    def get_system(system_id: UUID, request: Request) -> SystemRegistration:
        system = portfolio_store.get_system(system_id, owner_id=_actor(request))
        if system is None:
            raise HTTPException(status_code=404, detail="Portfolio system not found.")
        return system

    @router.get("/financial-accounts", response_model=list[FinancialAccountReference])
    def list_financial_accounts(
        request: Request,
        business_id: UUID | None = None,
        scope: PortfolioScope | None = None,
        include_retired: bool = False,
        limit: int = 200,
    ) -> list[FinancialAccountReference]:
        try:
            return portfolio_store.list_financial_accounts(
                owner_id=_actor(request),
                business_id=business_id,
                scope=scope,
                include_retired=include_retired,
                limit=limit,
            )
        except ValueError as exc:
            _portfolio_error(exc)

    @router.post(
        "/financial-accounts",
        response_model=FinancialAccountReference,
        status_code=201,
    )
    def create_financial_account(
        payload: FinancialAccountCreate,
        request: Request,
    ) -> FinancialAccountReference:
        try:
            account = FinancialAccountReference(owner_id=_actor(request), **payload.model_dump())
            saved = portfolio_store.create_financial_account(account)
        except (PortfolioConflictError, PortfolioScopeError, ValueError) as exc:
            _portfolio_error(exc)
        changed(request, "portfolio_financial_account", "registered", saved.id)
        return saved

    @router.get(
        "/financial-accounts/{account_id}",
        response_model=FinancialAccountReference,
    )
    def get_financial_account(
        account_id: UUID,
        request: Request,
    ) -> FinancialAccountReference:
        account = portfolio_store.get_financial_account(account_id, owner_id=_actor(request))
        if account is None:
            raise HTTPException(status_code=404, detail="Financial account reference not found.")
        return account

    return router
