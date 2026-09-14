from __future__ import annotations

from collections.abc import Callable

from chief.intelligence.schema import (
    NeuromapAgent,
    NeuromapModel,
    NeuromapSnapshot,
    NeuromapSystem,
    NeuromapTool,
)
from chief.models.base import ModelCapabilities, ModelPrivacy
from chief.models.router import ModelRouter
from chief.portfolio.store import SQLitePortfolioStore
from chief.tools.registry import ToolRegistry


class NeuromapService:
    """Build a current, machine-readable map of CHIEF's real runtime capabilities.

    Neuromap reports registered capability and authority. It does not infer credentials,
    manufacture permissions, probe external services, or turn registration into execution.
    """

    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        model_router: ModelRouter,
        portfolio_store: SQLitePortfolioStore,
        execution_enabled: Callable[[], bool],
    ) -> None:
        self.tool_registry = tool_registry
        self.model_router = model_router
        self.portfolio_store = portfolio_store
        self.execution_enabled = execution_enabled

    @staticmethod
    def _capabilities(provider: object) -> ModelCapabilities:
        capabilities = getattr(provider, "capabilities", None)
        if isinstance(capabilities, ModelCapabilities):
            return capabilities
        # Legacy/test providers are conservatively represented as local and capability-poor.
        return ModelCapabilities(privacy=ModelPrivacy.LOCAL)

    def snapshot(self, *, owner_id: str) -> NeuromapSnapshot:
        portfolio = self.portfolio_store.summary(owner_id=owner_id)
        agents = self.portfolio_store.list_agents(
            owner_id=owner_id,
            include_retired=True,
            limit=1_000,
        )
        systems = self.portfolio_store.list_systems(
            owner_id=owner_id,
            include_retired=True,
            limit=1_000,
        )

        tools = [
            NeuromapTool(
                name=definition.name,
                description=definition.description,
                risk=definition.risk.value,
                requires_approval=definition.requires_approval,
                side_effects=definition.side_effects,
                idempotent=definition.idempotent,
                timeout_seconds=definition.timeout_seconds,
            )
            for definition in sorted(self.tool_registry.definitions(), key=lambda item: item.name)
        ]

        states = {
            str(state["provider"]): state
            for state in self.model_router.provider_states()
        }
        models: list[NeuromapModel] = []
        for provider in self.model_router.providers:
            provider_name = str(getattr(provider, "name", provider.__class__.__name__))
            capabilities = self._capabilities(provider)
            state = states.get(provider_name, {})
            models.append(
                NeuromapModel(
                    provider=provider_name,
                    privacy=capabilities.privacy.value,
                    structured_output=capabilities.structured_output,
                    tool_calling=capabilities.tool_calling,
                    streaming=capabilities.streaming,
                    vision=capabilities.vision,
                    audio=capabilities.audio,
                    cost_tier=capabilities.cost_tier,
                    consecutive_failures=int(state.get("consecutive_failures", 0)),
                    circuit_open=bool(state.get("circuit_open", False)),
                )
            )
        models.sort(key=lambda item: item.provider)

        mapped_agents = [
            NeuromapAgent(
                id=agent.id,
                name=agent.name,
                role=agent.role.value,
                scope=agent.scope.value,
                business_id=agent.business_id,
                status=agent.status.value,
                execution_enabled=agent.execution_enabled,
                kill_switch_engaged=agent.kill_switch_engaged,
                authority_enabled=agent.authority.enabled,
                authority_expires_at=agent.authority.expires_at,
                allowed_tools=list(agent.authority.allowed_tools),
                allowed_system_ids=list(agent.authority.allowed_system_ids),
                can_delegate=agent.authority.can_delegate,
                monthly_token_limit=agent.budget.monthly_token_limit,
                max_parallel_runs=agent.budget.max_parallel_runs,
                memory_namespace=agent.memory_namespace,
            )
            for agent in agents
        ]

        mapped_systems = [
            NeuromapSystem(
                id=system.id,
                name=system.name,
                kind=system.kind.value,
                scope=system.scope.value,
                business_id=system.business_id,
                status=system.status.value,
                read_enabled=system.read_enabled,
                write_enabled=system.write_enabled,
                sensitivity=system.sensitivity.value,
            )
            for system in systems
        ]

        execution_enabled = bool(self.execution_enabled())
        limitations: list[str] = []
        if not execution_enabled:
            limitations.append("Global execution is disabled.")
        if len(models) == 1 and models[0].privacy == ModelPrivacy.LOCAL.value:
            limitations.append("Only one local model provider is currently configured.")
        if not any(model.streaming for model in models):
            limitations.append("No configured model provider declares streaming capability.")
        if not any(model.audio for model in models):
            limitations.append("No configured model provider declares native audio capability.")
        if not mapped_agents:
            limitations.append("No managed agents are registered for this owner.")
        elif not any(
            agent.execution_enabled
            and agent.authority_enabled
            and not agent.kill_switch_engaged
            for agent in mapped_agents
        ):
            limitations.append("No managed agent is currently execution-ready.")
        if not mapped_systems:
            limitations.append("No portfolio systems are registered for this owner.")

        return NeuromapSnapshot(
            owner_id=owner_id,
            execution_enabled=execution_enabled,
            portfolio=portfolio,
            tools=tools,
            models=models,
            agents=mapped_agents,
            systems=mapped_systems,
            limitations=limitations,
        )
