from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from chief.intelligence.schema import (
    AgentProposal,
    AgentProposalCreate,
    AgentProposalStatus,
)
from chief.portfolio.schema import (
    AgentRole,
    AuthorityPolicy,
    LifecycleState,
    ManagedAgent,
    PortfolioScope,
)
from chief.portfolio.store import PortfolioConflictError, SQLitePortfolioStore
from chief.tools.registry import ToolRegistry


class AgentFactoryError(RuntimeError):
    """Base error for proposal-only specialist creation."""


class AgentProposalNotFoundError(AgentFactoryError):
    """A proposal does not exist for the current owner."""


class AgentProposalStateError(AgentFactoryError):
    """A proposal cannot transition from its current state."""


class SQLiteAgentProposalStore:
    """Durable proposal ledger kept separate from executable portfolio state."""

    def __init__(
        self,
        database_path: str | Path = "data/chief.db",
        *,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        if busy_timeout_ms < 1:
            raise ValueError("Agent proposal database busy timeout must be positive.")
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
        connection.execute("PRAGMA foreign_keys = ON")
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

                CREATE TABLE IF NOT EXISTS intelligence_agent_proposals (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    business_id TEXT NOT NULL,
                    parent_agent_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS ix_intelligence_agent_proposals_owner_status
                    ON intelligence_agent_proposals(owner_id, status, created_at);
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO chief_component_migrations(component, version, applied_at)
                VALUES ('intelligence_agent_factory', 1, ?)
                """,
                (datetime.now(UTC).isoformat(),),
            )

    @staticmethod
    def _owner(owner_id: str) -> str:
        owner_id = owner_id.strip()
        if not owner_id:
            raise ValueError("Agent proposal owner cannot be blank.")
        if len(owner_id) > 256:
            raise ValueError("Agent proposal owner cannot exceed 256 characters.")
        return owner_id

    @staticmethod
    def _parse(row: sqlite3.Row) -> AgentProposal:
        return AgentProposal.model_validate_json(row["payload_json"])

    def create(self, proposal: AgentProposal) -> AgentProposal:
        proposal = AgentProposal.model_validate(proposal.model_dump(mode="python"))
        owner_id = self._owner(proposal.owner_id)
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO intelligence_agent_proposals(
                        id, owner_id, status, business_id, parent_agent_id,
                        payload_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(proposal.id),
                        owner_id,
                        proposal.status.value,
                        str(proposal.business_id),
                        str(proposal.parent_agent_id),
                        proposal.model_dump_json(),
                        proposal.created_at.astimezone(UTC).isoformat(),
                        proposal.updated_at.astimezone(UTC).isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise AgentFactoryError("The agent proposal already exists.") from exc
        return proposal

    def get(self, proposal_id: UUID, *, owner_id: str) -> AgentProposal | None:
        owner_id = self._owner(owner_id)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM intelligence_agent_proposals
                WHERE id = ? AND owner_id = ?
                """,
                (str(proposal_id), owner_id),
            ).fetchone()
        return self._parse(row) if row is not None else None

    def list(
        self,
        *,
        owner_id: str,
        status: AgentProposalStatus | None = None,
        limit: int = 200,
    ) -> list[AgentProposal]:
        owner_id = self._owner(owner_id)
        if not 1 <= limit <= 1_000:
            raise ValueError("Agent proposal limit must be between 1 and 1000.")
        query = "SELECT payload_json FROM intelligence_agent_proposals WHERE owner_id = ?"
        parameters: list[object] = [owner_id]
        if status is not None:
            query += " AND status = ?"
            parameters.append(status.value)
        query += " ORDER BY created_at, id LIMIT ?"
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._parse(row) for row in rows]

    def update(self, proposal: AgentProposal) -> AgentProposal:
        proposal = AgentProposal.model_validate(proposal.model_dump(mode="python"))
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE intelligence_agent_proposals
                SET status = ?, payload_json = ?, updated_at = ?
                WHERE id = ? AND owner_id = ?
                """,
                (
                    proposal.status.value,
                    proposal.model_dump_json(),
                    proposal.updated_at.astimezone(UTC).isoformat(),
                    str(proposal.id),
                    proposal.owner_id,
                ),
            )
            if cursor.rowcount != 1:
                raise AgentProposalNotFoundError(
                    "The agent proposal does not exist for this owner."
                )
        return proposal


class AgentFactory:
    """Create reviewed specialist proposals and materialize them with zero authority."""

    def __init__(
        self,
        *,
        portfolio_store: SQLitePortfolioStore,
        proposal_store: SQLiteAgentProposalStore,
        tool_registry: ToolRegistry,
    ) -> None:
        self.portfolio_store = portfolio_store
        self.proposal_store = proposal_store
        self.tool_registry = tool_registry

    def propose(
        self,
        *,
        owner_id: str,
        request: AgentProposalCreate,
    ) -> AgentProposal:
        business = self.portfolio_store.get_business(request.business_id, owner_id=owner_id)
        if business is None:
            raise AgentFactoryError("The proposed specialist business does not exist.")

        parent = self.portfolio_store.get_agent(request.parent_agent_id, owner_id=owner_id)
        if parent is None:
            raise AgentFactoryError("The proposed specialist parent agent does not exist.")
        if parent.role is not AgentRole.BUSINESS_GOVERNOR:
            raise AgentFactoryError("Specialists must report to a business governor.")
        if parent.business_id != request.business_id:
            raise AgentFactoryError("The specialist parent belongs to a different business.")

        registered_tools = set(self.tool_registry.names())
        unknown_tools = sorted(set(request.requested_tools) - registered_tools)
        if unknown_tools:
            raise AgentFactoryError(
                "The proposal requests unregistered tools: " + ", ".join(unknown_tools)
            )

        for system_id in request.requested_system_ids:
            system = self.portfolio_store.get_system(system_id, owner_id=owner_id)
            if system is None:
                raise AgentFactoryError("The proposal references an unavailable system.")
            if system.scope is PortfolioScope.PERSONAL:
                raise AgentFactoryError("Business specialists cannot request personal systems.")
            if (
                system.scope is PortfolioScope.BUSINESS
                and system.business_id != request.business_id
            ):
                raise AgentFactoryError("Business specialists cannot request another business's system.")

        return self.proposal_store.create(
            AgentProposal(
                owner_id=owner_id,
                business_id=request.business_id,
                parent_agent_id=request.parent_agent_id,
                name=request.name,
                mission=request.mission,
                requested_tools=request.requested_tools,
                requested_system_ids=request.requested_system_ids,
                rationale=request.rationale,
            )
        )

    def approve(self, *, owner_id: str, proposal_id: UUID) -> AgentProposal:
        proposal = self.proposal_store.get(proposal_id, owner_id=owner_id)
        if proposal is None:
            raise AgentProposalNotFoundError(
                "The agent proposal does not exist for this owner."
            )
        if proposal.status is AgentProposalStatus.REJECTED:
            raise AgentProposalStateError("A rejected proposal cannot be approved.")
        if proposal.status is AgentProposalStatus.MATERIALIZED:
            return proposal

        now = datetime.now(UTC)
        existing = self.portfolio_store.get_agent(proposal.id, owner_id=owner_id)
        if existing is None:
            agent = ManagedAgent(
                id=proposal.id,
                owner_id=owner_id,
                business_id=proposal.business_id,
                parent_agent_id=proposal.parent_agent_id,
                role=AgentRole.SPECIALIST,
                scope=PortfolioScope.BUSINESS,
                name=proposal.name,
                mission=proposal.mission,
                status=LifecycleState.DRAFT,
                execution_enabled=False,
                kill_switch_engaged=True,
                authority=AuthorityPolicy(
                    enabled=False,
                    allowed_tools=proposal.requested_tools,
                    allowed_system_ids=proposal.requested_system_ids,
                ),
            )
            try:
                self.portfolio_store.create_agent(agent)
            except PortfolioConflictError:
                existing = self.portfolio_store.get_agent(proposal.id, owner_id=owner_id)
                if existing is None:
                    raise

        materialized = proposal.model_copy(
            update={
                "status": AgentProposalStatus.MATERIALIZED,
                "materialized_agent_id": proposal.id,
                "approved_at": now,
                "updated_at": now,
            }
        )
        return self.proposal_store.update(materialized)

    def reject(self, *, owner_id: str, proposal_id: UUID) -> AgentProposal:
        proposal = self.proposal_store.get(proposal_id, owner_id=owner_id)
        if proposal is None:
            raise AgentProposalNotFoundError(
                "The agent proposal does not exist for this owner."
            )
        if proposal.status is AgentProposalStatus.MATERIALIZED:
            raise AgentProposalStateError("A materialized proposal cannot be rejected.")
        if proposal.status is AgentProposalStatus.REJECTED:
            return proposal
        now = datetime.now(UTC)
        rejected = proposal.model_copy(
            update={
                "status": AgentProposalStatus.REJECTED,
                "rejected_at": now,
                "updated_at": now,
            }
        )
        return self.proposal_store.update(rejected)
