from __future__ import annotations

import re
from datetime import UTC, datetime

from chief.intelligence.schema import (
    AgentRouteDecision,
    AgentRoutingRequest,
    RouteCandidate,
    RoutingStatus,
)
from chief.portfolio.schema import AgentRole, LifecycleState, ManagedAgent, PortfolioScope
from chief.portfolio.store import SQLitePortfolioStore

_TOKEN = re.compile(r"[a-z0-9][a-z0-9_.-]{1,}")
_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "again",
        "also",
        "and",
        "are",
        "build",
        "can",
        "chief",
        "for",
        "from",
        "have",
        "into",
        "need",
        "please",
        "that",
        "the",
        "this",
        "with",
        "you",
    }
)
_SPECIALTY_HINTS: dict[str, frozenset[str]] = {
    "recon": frozenset(
        {
            "research",
            "investigate",
            "competitor",
            "competitive",
            "market",
            "evidence",
            "source",
            "sources",
            "intelligence",
            "compare",
            "web",
        }
    ),
    "forge": frozenset(
        {
            "code",
            "coding",
            "github",
            "repository",
            "repo",
            "test",
            "tests",
            "deploy",
            "deployment",
            "engineering",
            "software",
            "bug",
            "build",
        }
    ),
    "ops": frozenset(
        {
            "operations",
            "operate",
            "monitor",
            "monitoring",
            "schedule",
            "scheduled",
            "workflow",
            "incident",
            "status",
            "kpi",
            "notification",
            "task",
        }
    ),
}


def _terms(value: str) -> set[str]:
    return {
        token
        for token in _TOKEN.findall(value.casefold())
        if token not in _STOPWORDS and len(token) >= 3
    }


class SpecialistOrchestrator:
    """Route work to already-authorized specialists without granting new authority.

    Routing is deterministic and inspectable. It never activates an agent, expands its tool
    set, opens a kill switch, or executes the task. Execution remains in CHIEF's existing
    guarded run/tool plane.
    """

    def __init__(self, portfolio_store: SQLitePortfolioStore) -> None:
        self.portfolio_store = portfolio_store

    @staticmethod
    def _is_execution_ready(agent: ManagedAgent, *, now: datetime) -> bool:
        if agent.role not in {AgentRole.SPECIALIST, AgentRole.PORTFOLIO_OPERATIONS}:
            return False
        if agent.status is not LifecycleState.ACTIVE:
            return False
        if not agent.execution_enabled or agent.kill_switch_engaged:
            return False
        if not agent.authority.enabled:
            return False
        if agent.authority.expires_at is None or agent.authority.expires_at <= now:
            return False
        if agent.budget.max_parallel_runs < 1 or agent.budget.monthly_token_limit < 1:
            return False
        return True

    @staticmethod
    def _matches_scope(agent: ManagedAgent, request: AgentRoutingRequest) -> bool:
        if request.business_id is not None:
            return (
                agent.scope is PortfolioScope.BUSINESS
                and agent.business_id == request.business_id
            )
        return agent.scope is PortfolioScope.PORTFOLIO

    @staticmethod
    def _has_required_tools(agent: ManagedAgent, required_tools: set[str]) -> bool:
        if not required_tools:
            return True
        return required_tools.issubset(set(agent.authority.allowed_tools))

    @staticmethod
    def _score(agent: ManagedAgent, task_terms: set[str]) -> RouteCandidate:
        profile_terms = _terms(f"{agent.name} {agent.mission} {agent.role.value}")
        matched = task_terms & profile_terms
        score = len(matched) * 3

        name_terms = _terms(agent.name)
        direct_name_hits = task_terms & name_terms
        score += len(direct_name_hits) * 8

        normalized_name = agent.name.casefold()
        hinted: set[str] = set()
        for label, hints in _SPECIALTY_HINTS.items():
            if label in normalized_name:
                hint_hits = task_terms & hints
                hinted.update(hint_hits)
                score += len(hint_hits) * 2

        return RouteCandidate(
            agent_id=agent.id,
            name=agent.name,
            score=score,
            matched_terms=sorted(matched | direct_name_hits | hinted),
        )

    def route(self, *, owner_id: str, request: AgentRoutingRequest) -> AgentRouteDecision:
        now = datetime.now(UTC)
        required_tools = set(request.required_tools)
        agents = self.portfolio_store.list_agents(
            owner_id=owner_id,
            include_retired=False,
            limit=1_000,
        )

        if request.requested_agent_id is not None:
            requested = next(
                (agent for agent in agents if agent.id == request.requested_agent_id),
                None,
            )
            if requested is None:
                return AgentRouteDecision(
                    status=RoutingStatus.REQUESTED_AGENT_UNAVAILABLE,
                    reason="The requested agent is not registered for this owner.",
                )
            if not self._matches_scope(requested, request):
                return AgentRouteDecision(
                    status=RoutingStatus.REQUESTED_AGENT_UNAVAILABLE,
                    reason="The requested agent is outside the requested operating scope.",
                )
            if not self._is_execution_ready(requested, now=now):
                return AgentRouteDecision(
                    status=RoutingStatus.REQUESTED_AGENT_UNAVAILABLE,
                    reason=(
                        "The requested agent is not execution-ready under current authority, "
                        "kill-switch, lifecycle, or budget controls."
                    ),
                )
            if not self._has_required_tools(requested, required_tools):
                return AgentRouteDecision(
                    status=RoutingStatus.REQUESTED_AGENT_UNAVAILABLE,
                    reason="The requested agent lacks one or more required tools.",
                )
            candidate = self._score(requested, _terms(request.task))
            return AgentRouteDecision(
                status=RoutingStatus.ROUTED,
                selected_agent_id=requested.id,
                selected_agent_name=requested.name,
                execution_ready=True,
                reason="The explicitly requested agent is eligible under current authority.",
                candidates=[candidate],
            )

        eligible = [
            agent
            for agent in agents
            if self._matches_scope(agent, request)
            and self._is_execution_ready(agent, now=now)
            and self._has_required_tools(agent, required_tools)
        ]
        if not eligible:
            return AgentRouteDecision(
                status=RoutingStatus.NO_ELIGIBLE_AGENT,
                reason=(
                    "No registered specialist is active, within scope, execution-enabled, "
                    "inside its authority window, funded for model work, and equipped with "
                    "the required tools."
                ),
            )

        task_terms = _terms(request.task)
        candidates = sorted(
            (self._score(agent, task_terms) for agent in eligible),
            key=lambda item: (-item.score, item.name.casefold(), str(item.agent_id)),
        )
        top = candidates[0]
        if len(candidates) > 1 and top.score == candidates[1].score:
            return AgentRouteDecision(
                status=RoutingStatus.AMBIGUOUS,
                reason="Multiple eligible specialists have the same routing score.",
                candidates=candidates[:5],
            )
        if top.score == 0 and len(candidates) > 1:
            return AgentRouteDecision(
                status=RoutingStatus.AMBIGUOUS,
                reason="The task does not contain enough signal to choose among specialists.",
                candidates=candidates[:5],
            )

        selected = next(agent for agent in eligible if agent.id == top.agent_id)
        return AgentRouteDecision(
            status=RoutingStatus.ROUTED,
            selected_agent_id=selected.id,
            selected_agent_name=selected.name,
            execution_ready=True,
            reason=(
                "Selected the highest-scoring eligible specialist without changing its "
                "authority or executing the task."
            ),
            candidates=candidates[:5],
        )
