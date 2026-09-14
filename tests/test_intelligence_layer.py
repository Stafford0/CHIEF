from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from chief.intelligence import (
    AgentFactory,
    AgentFactoryError,
    AgentProposalCreate,
    AgentProposalStatus,
    AgentRoutingRequest,
    NeuromapService,
    RoutingStatus,
    SQLiteAgentProposalStore,
    SpecialistOrchestrator,
)
from chief.models.base import (
    ModelCapabilities,
    ModelPrivacy,
    ModelProvider,
    ModelResponse,
)
from chief.models.router import ModelRouter
from chief.portfolio import (
    AgentRole,
    AuthorityPolicy,
    BudgetEnvelope,
    BusinessUnit,
    LifecycleState,
    ManagedAgent,
    PortfolioScope,
    SQLitePortfolioStore,
)
from chief.tools.base import Tool, ToolDefinition, ToolResult, ToolRisk
from chief.tools.registry import ToolRegistry


class NamedTool(Tool):
    def __init__(self, name: str, *, sensitive: bool = False) -> None:
        self._definition = ToolDefinition(
            name=name,
            description=f"Test capability for {name}.",
            risk=ToolRisk.SENSITIVE if sensitive else ToolRisk.SAFE,
            requires_approval=sensitive,
            side_effects=sensitive,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def execute(self, arguments: dict) -> ToolResult:
        return ToolResult(success=True, content="ok", data={"arguments": arguments})


class LocalTestProvider(ModelProvider):
    @property
    def name(self) -> str:
        return "local-test"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            privacy=ModelPrivacy.LOCAL,
            structured_output=True,
            cost_tier=0,
        )

    def generate(self, prompt: str, system_prompt: str | None = None) -> ModelResponse:
        del system_prompt
        return ModelResponse(content=prompt, provider=self.name, model="test")


@pytest.fixture
def store(tmp_path) -> SQLitePortfolioStore:
    return SQLitePortfolioStore(tmp_path / "chief.db")


@pytest.fixture
def registry() -> ToolRegistry:
    tool_registry = ToolRegistry()
    tool_registry.register(NamedTool("web.research"))
    tool_registry.register(NamedTool("github.read"))
    tool_registry.register(NamedTool("shell.command", sensitive=True))
    return tool_registry


def _future() -> datetime:
    return datetime.now(UTC) + timedelta(days=30)


def _business_and_governor(
    store: SQLitePortfolioStore,
    *,
    executable: bool = False,
) -> tuple[BusinessUnit, ManagedAgent]:
    future = _future()
    if executable:
        business = BusinessUnit(
            owner_id="owner-a",
            key="alpha",
            name="Alpha",
            mission="Operate Alpha safely.",
            status=LifecycleState.ACTIVE,
            execution_enabled=True,
            kill_switch_engaged=False,
            authority_ceiling=AuthorityPolicy(enabled=True, expires_at=future),
            budget=BudgetEnvelope(monthly_token_limit=50_000, max_parallel_runs=4),
            review_due_at=future,
        )
    else:
        business = BusinessUnit(
            owner_id="owner-a",
            key="alpha",
            name="Alpha",
            mission="Operate Alpha safely.",
        )
    business = store.create_business(business)
    governor = store.create_agent(
        ManagedAgent(
            owner_id="owner-a",
            business_id=business.id,
            role=AgentRole.BUSINESS_GOVERNOR,
            scope=PortfolioScope.BUSINESS,
            name="Alpha Governor",
            mission="Govern Alpha under human authority.",
        )
    )
    return business, governor


def _specialist(
    store: SQLitePortfolioStore,
    business: BusinessUnit,
    governor: ManagedAgent,
    *,
    name: str,
    mission: str,
    tools: list[str],
    funded: bool = True,
) -> ManagedAgent:
    future = _future()
    return store.create_agent(
        ManagedAgent(
            owner_id="owner-a",
            business_id=business.id,
            parent_agent_id=governor.id,
            role=AgentRole.SPECIALIST,
            scope=PortfolioScope.BUSINESS,
            name=name,
            mission=mission,
            status=LifecycleState.ACTIVE,
            execution_enabled=True,
            kill_switch_engaged=False,
            authority=AuthorityPolicy(
                enabled=True,
                allowed_tools=tools,
                expires_at=future,
            ),
            budget=BudgetEnvelope(
                monthly_token_limit=10_000 if funded else 0,
                max_parallel_runs=1 if funded else 0,
            ),
            review_due_at=future,
        )
    )


def test_neuromap_reports_actual_registered_capability(
    store: SQLitePortfolioStore,
    registry: ToolRegistry,
) -> None:
    service = NeuromapService(
        tool_registry=registry,
        model_router=ModelRouter([LocalTestProvider()]),
        portfolio_store=store,
        execution_enabled=lambda: False,
    )

    snapshot = service.snapshot(owner_id="owner-a")

    assert snapshot.version == "neuromap-v1"
    assert snapshot.execution_enabled is False
    assert snapshot.portfolio.is_blank is True
    assert [tool.name for tool in snapshot.tools] == [
        "github.read",
        "shell.command",
        "web.research",
    ]
    shell = next(tool for tool in snapshot.tools if tool.name == "shell.command")
    assert shell.requires_approval is True
    assert shell.side_effects is True
    assert snapshot.models[0].provider == "local-test"
    assert snapshot.models[0].privacy == "local"
    assert "Global execution is disabled." in snapshot.limitations
    assert "No managed agents are registered for this owner." in snapshot.limitations


def test_agent_factory_materializes_an_inert_zero_authority_specialist(
    store: SQLitePortfolioStore,
    registry: ToolRegistry,
) -> None:
    business, governor = _business_and_governor(store)
    proposal_store = SQLiteAgentProposalStore(store.database_path)
    factory = AgentFactory(
        portfolio_store=store,
        proposal_store=proposal_store,
        tool_registry=registry,
    )

    proposal = factory.propose(
        owner_id="owner-a",
        request=AgentProposalCreate(
            business_id=business.id,
            parent_agent_id=governor.id,
            name="RECON",
            mission="Research markets, competitors, and external evidence.",
            requested_tools=["web.research"],
            rationale="Isolated research context improves evidence gathering.",
        ),
    )
    materialized = factory.approve(owner_id="owner-a", proposal_id=proposal.id)
    agent = store.get_agent(proposal.id, owner_id="owner-a")

    assert materialized.status is AgentProposalStatus.MATERIALIZED
    assert materialized.materialized_agent_id == proposal.id
    assert agent is not None
    assert agent.role is AgentRole.SPECIALIST
    assert agent.status is LifecycleState.DRAFT
    assert agent.execution_enabled is False
    assert agent.kill_switch_engaged is True
    assert agent.authority.enabled is False
    assert agent.authority.allowed_tools == ["web.research"]
    assert agent.budget.monthly_token_limit == 0
    assert agent.budget.max_parallel_runs == 0

    # Approval is idempotent and cannot create a second agent.
    assert factory.approve(owner_id="owner-a", proposal_id=proposal.id) == materialized
    assert len(store.list_agents(owner_id="owner-a")) == 2


def test_agent_factory_rejects_unregistered_tool_requests(
    store: SQLitePortfolioStore,
    registry: ToolRegistry,
) -> None:
    business, governor = _business_and_governor(store)
    factory = AgentFactory(
        portfolio_store=store,
        proposal_store=SQLiteAgentProposalStore(store.database_path),
        tool_registry=registry,
    )

    with pytest.raises(AgentFactoryError, match="unregistered tools"):
        factory.propose(
            owner_id="owner-a",
            request=AgentProposalCreate(
                business_id=business.id,
                parent_agent_id=governor.id,
                name="Unsafe Specialist",
                mission="Request a capability CHIEF does not actually have.",
                requested_tools=["imaginary.root.access"],
            ),
        )


def test_orchestrator_routes_by_specialty_without_expanding_authority(
    store: SQLitePortfolioStore,
) -> None:
    business, governor = _business_and_governor(store, executable=True)
    recon = _specialist(
        store,
        business,
        governor,
        name="RECON",
        mission="Research competitors, markets, sources, and evidence.",
        tools=["web.research"],
    )
    _specialist(
        store,
        business,
        governor,
        name="FORGE",
        mission="Engineer code, repositories, tests, and deployments.",
        tools=["github.read"],
    )
    unfunded = _specialist(
        store,
        business,
        governor,
        name="OPS",
        mission="Monitor operations, schedules, incidents, and workflows.",
        tools=["web.research"],
        funded=False,
    )

    decision = SpecialistOrchestrator(store).route(
        owner_id="owner-a",
        request=AgentRoutingRequest(
            task="Research competitor market evidence and sources.",
            business_id=business.id,
            required_tools=["web.research"],
        ),
    )

    assert decision.status is RoutingStatus.ROUTED
    assert decision.selected_agent_id == recon.id
    assert decision.selected_agent_name == "RECON"
    assert decision.execution_ready is True
    assert unfunded.id not in {candidate.agent_id for candidate in decision.candidates}
    assert store.get_agent(recon.id, owner_id="owner-a") == recon
