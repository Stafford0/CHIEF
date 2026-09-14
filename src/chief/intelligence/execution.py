from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from chief.intelligence.orchestrator import SpecialistOrchestrator, agent_is_execution_ready
from chief.intelligence.schema import (
    AgentRoutingRequest,
    RoutingStatus,
    SpecialistRunCreate,
    SpecialistRunDispatch,
)
from chief.models.base import ModelCapabilities, ModelPrivacy, RouteRequirements
from chief.models.route_audit import SQLiteModelRouteStore
from chief.models.router import ModelRouter
from chief.portfolio.schema import ManagedAgent
from chief.portfolio.store import SQLitePortfolioStore
from chief.runs import (
    ActionContext,
    ActionResult,
    IdempotencyConflict,
    PermanentActionError,
    RetryableActionError,
    RunEngine,
    SQLiteRunStore,
    StepSpec,
    VerificationStatus,
)

SPECIALIST_ANALYSIS_ACTION = "intelligence.specialist.analyze"


class SpecialistRunError(RuntimeError):
    """Base error for governed specialist run dispatch."""


class SpecialistRunRoutingError(SpecialistRunError):
    """No single execution-ready specialist can accept the task."""


class SpecialistDispatchConflict(SpecialistRunError):
    """A durable run is already bound to different specialist authority."""


class SpecialistDispatchRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    owner_id: str
    agent_id: UUID
    task_digest: str
    agent_digest: str
    created_at: datetime


class SQLiteSpecialistDispatchStore:
    """Server-owned authorization receipts for specialist durable runs.

    A generic caller can create a run step with the same action name, but it cannot create this
    receipt through the run API. The action handler refuses to operate without a matching receipt.
    """

    def __init__(
        self,
        database_path: str | Path = "data/chief.db",
        *,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        if busy_timeout_ms < 1:
            raise ValueError("Specialist dispatch database busy timeout must be positive.")
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_ms = busy_timeout_ms
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_ms / 1_000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS chief_component_migrations (
                    component TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    applied_at TEXT NOT NULL,
                    PRIMARY KEY (component, version)
                );

                CREATE TABLE IF NOT EXISTS intelligence_specialist_dispatches (
                    run_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    task_digest TEXT NOT NULL,
                    agent_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS ix_specialist_dispatch_owner_created
                    ON intelligence_specialist_dispatches(owner_id, created_at DESC);
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO chief_component_migrations(component, version, applied_at)
                VALUES ('intelligence_specialist_dispatch', 1, ?)
                """,
                (datetime.now(UTC).isoformat(),),
            )

    @staticmethod
    def _parse(row: sqlite3.Row) -> SpecialistDispatchRecord:
        return SpecialistDispatchRecord(
            run_id=row["run_id"],
            owner_id=row["owner_id"],
            agent_id=row["agent_id"],
            task_digest=row["task_digest"],
            agent_digest=row["agent_digest"],
            created_at=row["created_at"],
        )

    def bind(self, record: SpecialistDispatchRecord) -> SpecialistDispatchRecord:
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM intelligence_specialist_dispatches WHERE run_id = ?",
                (str(record.run_id),),
            ).fetchone()
            if existing is not None:
                parsed = self._parse(existing)
                if parsed != record:
                    raise SpecialistDispatchConflict(
                        "The durable run is already bound to a different specialist dispatch."
                    )
                return parsed
            connection.execute(
                """
                INSERT INTO intelligence_specialist_dispatches(
                    run_id, owner_id, agent_id, task_digest, agent_digest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record.run_id),
                    record.owner_id,
                    str(record.agent_id),
                    record.task_digest,
                    record.agent_digest,
                    record.created_at.astimezone(UTC).isoformat(),
                ),
            )
        return record

    def get(self, run_id: UUID) -> SpecialistDispatchRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM intelligence_specialist_dispatches WHERE run_id = ?",
                (str(run_id),),
            ).fetchone()
        return self._parse(row) if row is not None else None


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _agent_digest(agent: ManagedAgent) -> str:
    """Bind execution to the exact routed identity, mission, authority, and budget envelope."""

    payload = {
        "id": str(agent.id),
        "business_id": str(agent.business_id) if agent.business_id is not None else None,
        "parent_agent_id": (
            str(agent.parent_agent_id) if agent.parent_agent_id is not None else None
        ),
        "role": agent.role.value,
        "scope": agent.scope.value,
        "name": agent.name,
        "mission": agent.mission,
        "status": agent.status.value,
        "execution_enabled": agent.execution_enabled,
        "kill_switch_engaged": agent.kill_switch_engaged,
        "authority": agent.authority.model_dump(mode="json"),
        "budget": agent.budget.model_dump(mode="json"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return _digest_text(encoded)


def _specialist_system_prompt(agent: ManagedAgent) -> str:
    specialty = agent.name.casefold()
    if "recon" in specialty:
        specialty_rules = (
            "Focus on evidence quality, competing explanations, gaps, and what should be "
            "researched next. Never imply that you searched the web in this run."
        )
    elif "forge" in specialty:
        specialty_rules = (
            "Focus on engineering correctness, architecture, failure modes, tests, and the "
            "smallest safe implementation path. Never imply that code was changed in this run."
        )
    elif "ops" in specialty:
        specialty_rules = (
            "Focus on operational priority, dependencies, monitoring, handoffs, and recovery. "
            "Never imply that a system or schedule was changed in this run."
        )
    else:
        specialty_rules = (
            "Stay inside the named mission and clearly separate supplied facts from inference."
        )
    return (
        "You are a governed CHIEF specialist operating inside an analysis-only durable run.\n"
        f"Name: {agent.name}\n"
        f"Mission: {agent.mission}\n"
        f"Specialty rules: {specialty_rules}\n"
        "This run has no live tools, browser, shell, external writes, or private-memory access. "
        "Registered capabilities elsewhere in CHIEF are not available here. Do not claim to "
        "have performed actions or gathered evidence that was not supplied in the task.\n"
        "Return: Findings, Risks/Unknowns, and Recommended Next Action. Be concise and explicit "
        "about uncertainty."
    )


class SpecialistRunService:
    """Queue and execute analysis-only specialist work through CHIEF's durable run engine."""

    def __init__(
        self,
        *,
        portfolio_store: SQLitePortfolioStore,
        orchestrator: SpecialistOrchestrator,
        run_store: SQLiteRunStore,
        model_router: ModelRouter,
        dispatch_store: SQLiteSpecialistDispatchStore,
        route_store: SQLiteModelRouteStore | None = None,
    ) -> None:
        self.portfolio_store = portfolio_store
        self.orchestrator = orchestrator
        self.run_store = run_store
        self.model_router = model_router
        self.dispatch_store = dispatch_store
        self.route_store = route_store

    @staticmethod
    def _run_key(owner_id: str, idempotency_key: str) -> str:
        owner_digest = _digest_text(owner_id)[:12]
        return f"specialist:{owner_digest}:{idempotency_key}"

    def enqueue(self, *, owner_id: str, request: SpecialistRunCreate) -> SpecialistRunDispatch:
        decision = self.orchestrator.route(
            owner_id=owner_id,
            request=AgentRoutingRequest(
                task=request.task,
                business_id=request.business_id,
                requested_agent_id=request.requested_agent_id,
                required_tools=[],
            ),
        )
        if decision.status is not RoutingStatus.ROUTED or decision.selected_agent_id is None:
            raise SpecialistRunRoutingError(decision.reason)

        agent = self.portfolio_store.get_agent(
            decision.selected_agent_id,
            owner_id=owner_id,
        )
        if agent is None:
            raise SpecialistRunRoutingError("The routed specialist disappeared before dispatch.")

        task_digest = _digest_text(request.task)
        agent_digest = _agent_digest(agent)
        step = StepSpec(
            action=SPECIALIST_ANALYSIS_ACTION,
            idempotency_key=f"analyze:{agent.id}:{task_digest[:16]}",
            input_data={
                "agent_id": str(agent.id),
                "task": request.task,
                "mode": "analysis_only",
            },
            max_attempts=3,
            verification_required=True,
        )
        try:
            run = self.run_store.create_run(
                idempotency_key=self._run_key(owner_id, request.idempotency_key),
                input_data={
                    "agent_id": str(agent.id),
                    "mode": "analysis_only",
                },
                steps=[step],
            )
        except IdempotencyConflict as exc:
            raise SpecialistDispatchConflict(str(exc)) from exc

        self.dispatch_store.bind(
            SpecialistDispatchRecord(
                run_id=run.id,
                owner_id=owner_id,
                agent_id=agent.id,
                task_digest=task_digest,
                agent_digest=agent_digest,
                created_at=run.created_at,
            )
        )
        return SpecialistRunDispatch(
            run_id=run.id,
            agent_id=agent.id,
            agent_name=agent.name,
            correlation_id=run.correlation_id,
            status=run.status.value,
        )

    def register_handler(self, run_engine: RunEngine) -> None:
        if SPECIALIST_ANALYSIS_ACTION not in run_engine.handlers:
            run_engine.register_handler(SPECIALIST_ANALYSIS_ACTION, self.handle_analysis)

    @staticmethod
    def _selected_privacy(router: ModelRouter, provider_name: str) -> ModelPrivacy | None:
        provider = next(
            (item for item in router.providers if getattr(item, "name", None) == provider_name),
            None,
        )
        if provider is None:
            return None
        capabilities = getattr(provider, "capabilities", None)
        if not isinstance(capabilities, ModelCapabilities):
            return ModelPrivacy.LOCAL
        return capabilities.privacy

    def _record_route(
        self,
        *,
        owner_id: str,
        request_id: str,
        selected_provider: str | None,
        selected_model: str | None,
        latency_ms: float | None,
        succeeded: bool,
    ) -> None:
        if self.route_store is None:
            return
        selected_privacy = (
            self._selected_privacy(self.model_router, selected_provider)
            if selected_provider is not None
            else None
        )
        self.route_store.record(
            actor_id=owner_id,
            request_id=request_id,
            attempts=self.model_router.last_attempts,
            selected_provider=selected_provider,
            selected_model=selected_model,
            selected_privacy=selected_privacy,
            latency_ms=latency_ms,
            max_cost_tier=0,
            cloud_authorized=False,
            succeeded=succeeded,
        )

    def handle_analysis(self, context: ActionContext, payload: dict[str, object]) -> ActionResult:
        dispatch = self.dispatch_store.get(context.lease.run.id)
        if dispatch is None:
            raise RetryableActionError(
                "No server-owned specialist dispatch receipt exists for this run.",
                code="specialist_dispatch_missing",
            )

        agent = self.portfolio_store.get_agent(
            dispatch.agent_id,
            owner_id=dispatch.owner_id,
        )
        if agent is None:
            raise PermanentActionError(
                "The dispatched specialist no longer exists.",
                code="specialist_agent_missing",
            )
        if not agent_is_execution_ready(agent, now=datetime.now(UTC)):
            raise PermanentActionError(
                "The specialist is no longer execution-ready.",
                code="specialist_authority_closed",
            )
        if _agent_digest(agent) != dispatch.agent_digest:
            raise PermanentActionError(
                "The specialist authority, budget, identity, or mission changed after routing. "
                "Create a new routed run under the current state.",
                code="specialist_authority_changed",
            )

        task = payload.get("task")
        agent_id = payload.get("agent_id")
        mode = payload.get("mode")
        if not isinstance(task, str) or _digest_text(task) != dispatch.task_digest:
            raise PermanentActionError(
                "The specialist task does not match its server-owned dispatch receipt.",
                code="specialist_task_mismatch",
            )
        if agent_id != str(dispatch.agent_id) or mode != "analysis_only":
            raise PermanentActionError(
                "The specialist run payload does not match its dispatch receipt.",
                code="specialist_payload_mismatch",
            )

        requirements = RouteRequirements(
            allowed_privacy=frozenset({ModelPrivacy.LOCAL}),
            max_cost_tier=0,
            cloud_authorized=False,
        )
        try:
            response = self.model_router.generate(
                task,
                _specialist_system_prompt(agent),
                requirements=requirements,
            )
        except RuntimeError as exc:
            self._record_route(
                owner_id=dispatch.owner_id,
                request_id=context.correlation_id,
                selected_provider=None,
                selected_model=None,
                latency_ms=None,
                succeeded=False,
            )
            raise RetryableActionError(
                "The local specialist model route failed.",
                code="specialist_model_unavailable",
            ) from exc

        self._record_route(
            owner_id=dispatch.owner_id,
            request_id=context.correlation_id,
            selected_provider=response.provider,
            selected_model=response.model,
            latency_ms=response.latency_ms,
            succeeded=True,
        )
        return ActionResult(
            result_data={
                "mode": "analysis_only",
                "agent_id": str(agent.id),
                "agent_name": agent.name,
                "content": response.content,
                "provider": response.provider,
                "model": response.model,
                "latency_ms": response.latency_ms,
                "tools_executed": [],
            },
            verification_status=VerificationStatus.VERIFIED,
        )
