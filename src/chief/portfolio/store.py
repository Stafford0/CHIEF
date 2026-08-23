from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from chief.portfolio.schema import (
    AgentHeartbeat,
    AgentRole,
    AuthorityPolicy,
    BusinessUnit,
    FinancialAccountReference,
    FinancialActionMode,
    HeartbeatHealth,
    HeartbeatReport,
    LifecycleState,
    ManagedAgent,
    OnboardingState,
    OnboardingStep,
    PortfolioScope,
    PortfolioSummary,
    SystemRegistration,
)


class PortfolioError(RuntimeError):
    """Base error for governed portfolio operations."""


class PortfolioConflictError(PortfolioError):
    """A portfolio identifier, key, or isolated namespace is already registered."""


class PortfolioNotFoundError(PortfolioError):
    """A requested owner-scoped portfolio record does not exist."""


class PortfolioScopeError(PortfolioError):
    """An operation attempts to cross an owner, business, or personal-data boundary."""


class PortfolioHierarchyError(PortfolioScopeError):
    """An agent hierarchy is cyclic or crosses a governance boundary."""


class PortfolioValidationError(PortfolioError):
    """An operation is structurally valid but violates a portfolio safety invariant."""


class SQLitePortfolioStore:
    """Durable owner-isolated registry for CHIEF's governed operating portfolio.

    The store starts empty and never seeds businesses, agents, systems, accounts,
    authority, or credentials. Agent heartbeats are append-only observations and
    cannot modify activation or authority state.
    """

    def __init__(
        self,
        database_path: str | Path = "data/chief.db",
        *,
        busy_timeout_ms: int = 5_000,
        max_page_size: int = 1_000,
        max_json_bytes: int = 1_000_000,
    ) -> None:
        if busy_timeout_ms < 1:
            raise ValueError("Portfolio database busy timeout must be positive.")
        if max_page_size < 1:
            raise ValueError("Portfolio maximum page size must be positive.")
        if max_json_bytes < 2:
            raise ValueError("Portfolio JSON size limit is too small.")
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_ms = busy_timeout_ms
        self.max_page_size = max_page_size
        self.max_json_bytes = max_json_bytes
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_ms / 1_000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
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

                CREATE TABLE IF NOT EXISTS portfolio_businesses (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    business_key TEXT NOT NULL,
                    memory_namespace TEXT NOT NULL,
                    credential_reference_uri TEXT,
                    status TEXT NOT NULL,
                    monitoring_enabled INTEGER NOT NULL CHECK (monitoring_enabled IN (0, 1)),
                    execution_enabled INTEGER NOT NULL CHECK (execution_enabled IN (0, 1)),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (id, owner_id),
                    UNIQUE (owner_id, business_key),
                    UNIQUE (owner_id, memory_namespace)
                );

                CREATE TABLE IF NOT EXISTS portfolio_agents (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    business_id TEXT,
                    parent_agent_id TEXT,
                    scope TEXT NOT NULL,
                    role TEXT NOT NULL,
                    memory_namespace TEXT NOT NULL,
                    credential_reference_uri TEXT,
                    status TEXT NOT NULL,
                    execution_enabled INTEGER NOT NULL CHECK (execution_enabled IN (0, 1)),
                    kill_switch_engaged INTEGER NOT NULL CHECK (kill_switch_engaged IN (0, 1)),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (id, owner_id),
                    UNIQUE (owner_id, memory_namespace),
                    UNIQUE (owner_id, credential_reference_uri),
                    FOREIGN KEY (business_id, owner_id)
                        REFERENCES portfolio_businesses(id, owner_id) ON DELETE RESTRICT,
                    FOREIGN KEY (parent_agent_id, owner_id)
                        REFERENCES portfolio_agents(id, owner_id) ON DELETE RESTRICT
                );

                CREATE TABLE IF NOT EXISTS portfolio_systems (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    business_id TEXT,
                    scope TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    name_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    read_enabled INTEGER NOT NULL CHECK (read_enabled IN (0, 1)),
                    write_enabled INTEGER NOT NULL CHECK (write_enabled IN (0, 1)),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (id, owner_id),
                    UNIQUE (owner_id, scope_key, name_key),
                    FOREIGN KEY (business_id, owner_id)
                        REFERENCES portfolio_businesses(id, owner_id) ON DELETE RESTRICT
                );

                CREATE TABLE IF NOT EXISTS portfolio_financial_accounts (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    business_id TEXT,
                    scope TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    alias_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (id, owner_id),
                    UNIQUE (owner_id, scope_key, alias_key),
                    FOREIGN KEY (business_id, owner_id)
                        REFERENCES portfolio_businesses(id, owner_id) ON DELETE RESTRICT
                );

                CREATE TABLE IF NOT EXISTS portfolio_agent_heartbeats (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    business_id TEXT,
                    health TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE (id, owner_id),
                    FOREIGN KEY (agent_id, owner_id)
                        REFERENCES portfolio_agents(id, owner_id) ON DELETE CASCADE,
                    FOREIGN KEY (business_id, owner_id)
                        REFERENCES portfolio_businesses(id, owner_id) ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS ix_portfolio_businesses_owner_status
                    ON portfolio_businesses(owner_id, status, updated_at);
                CREATE INDEX IF NOT EXISTS ix_portfolio_agents_owner_business
                    ON portfolio_agents(owner_id, business_id, status, updated_at);
                CREATE INDEX IF NOT EXISTS ix_portfolio_agents_owner_parent
                    ON portfolio_agents(owner_id, parent_agent_id);
                CREATE INDEX IF NOT EXISTS ix_portfolio_systems_owner_business
                    ON portfolio_systems(owner_id, business_id, status, updated_at);
                CREATE INDEX IF NOT EXISTS ix_portfolio_accounts_owner_business
                    ON portfolio_financial_accounts(owner_id, business_id, status, updated_at);
                CREATE INDEX IF NOT EXISTS ix_portfolio_heartbeats_agent_received
                    ON portfolio_agent_heartbeats(owner_id, agent_id, received_at DESC);
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO chief_component_migrations(component, version, applied_at)
                VALUES ('portfolio_registry', 1, ?)
                """,
                (self._iso(self._now()),),
            )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _iso(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()

    @staticmethod
    def _validate_owner(owner_id: str) -> str:
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("Portfolio owner must be a non-empty string.")
        owner_id = owner_id.strip()
        if len(owner_id) > 256:
            raise ValueError("Portfolio owner cannot exceed 256 characters.")
        return owner_id

    def _validate_limit(self, limit: int) -> int:
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= self.max_page_size
        ):
            raise ValueError(f"Portfolio page limit must be between 1 and {self.max_page_size}.")
        return limit

    def _payload(self, record: object) -> str:
        payload = record.model_dump_json()  # type: ignore[attr-defined]
        if len(payload.encode("utf-8")) > self.max_json_bytes:
            raise PortfolioValidationError(
                "Portfolio record exceeds the configured JSON size limit."
            )
        return payload

    @staticmethod
    def _scope_key(scope: PortfolioScope, business_id: UUID | None) -> str:
        return f"business:{business_id}" if business_id is not None else scope.value

    @staticmethod
    def _business(row: sqlite3.Row) -> BusinessUnit:
        return BusinessUnit.model_validate_json(row["payload_json"])

    @staticmethod
    def _agent(row: sqlite3.Row) -> ManagedAgent:
        return ManagedAgent.model_validate_json(row["payload_json"])

    @staticmethod
    def _system(row: sqlite3.Row) -> SystemRegistration:
        return SystemRegistration.model_validate_json(row["payload_json"])

    @staticmethod
    def _account(row: sqlite3.Row) -> FinancialAccountReference:
        return FinancialAccountReference.model_validate_json(row["payload_json"])

    @staticmethod
    def _heartbeat(row: sqlite3.Row) -> AgentHeartbeat:
        return AgentHeartbeat.model_validate_json(row["payload_json"])

    @staticmethod
    def _translate_integrity(exc: sqlite3.IntegrityError, entity: str) -> PortfolioError:
        message = str(exc).casefold()
        if "foreign key" in message:
            return PortfolioScopeError(
                f"The {entity} references a missing or differently owned portfolio record."
            )
        return PortfolioConflictError(f"The {entity} conflicts with an existing portfolio record.")

    def create_business(self, business: BusinessUnit) -> BusinessUnit:
        business = BusinessUnit.model_validate(business.model_dump(mode="python"))
        owner_id = self._validate_owner(business.owner_id)
        if owner_id != business.owner_id:
            raise PortfolioScopeError("Business owner_id must be normalized before storage.")
        credential_uri = (
            business.credential_reference.uri if business.credential_reference is not None else None
        )
        if business.execution_enabled:
            self._validate_execution_review(
                review_due_at=business.review_due_at,
                authority_expires_at=business.authority_ceiling.expires_at,
                entity="Business",
            )
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO portfolio_businesses(
                        id, owner_id, business_key, memory_namespace,
                        credential_reference_uri, status, monitoring_enabled,
                        execution_enabled, payload_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(business.id),
                        owner_id,
                        business.key,
                        business.memory_namespace,
                        credential_uri,
                        business.status.value,
                        int(business.monitoring_enabled),
                        int(business.execution_enabled),
                        self._payload(business),
                        self._iso(business.created_at),
                        self._iso(business.updated_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise self._translate_integrity(exc, "business") from exc
        return business

    def get_business(self, business_id: UUID, *, owner_id: str) -> BusinessUnit | None:
        owner_id = self._validate_owner(owner_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM portfolio_businesses WHERE id = ? AND owner_id = ?",
                (str(business_id), owner_id),
            ).fetchone()
        return self._business(row) if row is not None else None

    def list_businesses(
        self,
        *,
        owner_id: str,
        include_retired: bool = False,
        limit: int = 200,
    ) -> list[BusinessUnit]:
        owner_id = self._validate_owner(owner_id)
        limit = self._validate_limit(limit)
        query = "SELECT payload_json FROM portfolio_businesses WHERE owner_id = ?"
        parameters: list[object] = [owner_id]
        if not include_retired:
            query += " AND status <> ?"
            parameters.append(LifecycleState.RETIRED.value)
        query += " ORDER BY created_at, id LIMIT ?"
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._business(row) for row in rows]

    def create_agent(self, agent: ManagedAgent) -> ManagedAgent:
        agent = ManagedAgent.model_validate(agent.model_dump(mode="python"))
        owner_id = self._validate_owner(agent.owner_id)
        if owner_id != agent.owner_id:
            raise PortfolioScopeError("Agent owner_id must be normalized before storage.")
        if agent.execution_enabled:
            self._validate_execution_review(
                review_due_at=agent.review_due_at,
                authority_expires_at=agent.authority.expires_at,
                entity="Agent",
            )
        with self._connection() as connection:
            self._validate_agent_references(connection, agent)
            try:
                connection.execute(
                    """
                    INSERT INTO portfolio_agents(
                        id, owner_id, business_id, parent_agent_id, scope, role,
                        memory_namespace, credential_reference_uri, status,
                        execution_enabled, kill_switch_engaged, payload_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(agent.id),
                        owner_id,
                        str(agent.business_id) if agent.business_id is not None else None,
                        str(agent.parent_agent_id) if agent.parent_agent_id is not None else None,
                        agent.scope.value,
                        agent.role.value,
                        agent.memory_namespace,
                        agent.credential_reference.uri
                        if agent.credential_reference is not None
                        else None,
                        agent.status.value,
                        int(agent.execution_enabled),
                        int(agent.kill_switch_engaged),
                        self._payload(agent),
                        self._iso(agent.created_at),
                        self._iso(agent.updated_at),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise self._translate_integrity(exc, "agent") from exc
        return agent

    def _validate_agent_references(
        self,
        connection: sqlite3.Connection,
        agent: ManagedAgent,
    ) -> None:
        if agent.business_id is not None:
            business_row = connection.execute(
                "SELECT payload_json FROM portfolio_businesses WHERE id = ? AND owner_id = ?",
                (str(agent.business_id), agent.owner_id),
            ).fetchone()
            if business_row is None:
                raise PortfolioScopeError(
                    "The agent business does not exist or belongs to another owner."
                )
            business = self._business(business_row)
            if business.status is LifecycleState.RETIRED:
                raise PortfolioValidationError("Agents cannot be added to a retired business.")
            if agent.execution_enabled and not business.execution_enabled:
                raise PortfolioValidationError(
                    "A business agent cannot execute while its business execution gate is closed."
                )

        if agent.parent_agent_id is not None:
            parent_row = connection.execute(
                "SELECT payload_json FROM portfolio_agents WHERE id = ? AND owner_id = ?",
                (str(agent.parent_agent_id), agent.owner_id),
            ).fetchone()
            if parent_row is None:
                raise PortfolioHierarchyError(
                    "The parent agent does not exist or belongs to another owner."
                )
            parent = self._agent(parent_row)
            if parent.scope is not agent.scope:
                raise PortfolioHierarchyError("Agent parents cannot cross portfolio scopes.")
            if parent.business_id != agent.business_id:
                raise PortfolioHierarchyError("Agent parents cannot cross business boundaries.")
            if (
                agent.role is AgentRole.SPECIALIST
                and parent.role is not AgentRole.BUSINESS_GOVERNOR
            ):
                raise PortfolioHierarchyError(
                    "Specialist agents must report to a business governor."
                )
            self._assert_acyclic(connection, agent)

        referenced_systems = set(agent.authority.allowed_system_ids)
        referenced_systems.update(
            kpi.source_system_id for kpi in agent.kpis if kpi.source_system_id is not None
        )
        for system_id in referenced_systems:
            row = connection.execute(
                """
                SELECT scope, business_id FROM portfolio_systems
                WHERE id = ? AND owner_id = ?
                """,
                (str(system_id), agent.owner_id),
            ).fetchone()
            if row is None:
                raise PortfolioScopeError(
                    "Agent authority references a missing or differently owned system."
                )
            system_scope = PortfolioScope(row["scope"])
            system_business_id = UUID(row["business_id"]) if row["business_id"] else None
            if (
                system_scope is PortfolioScope.PERSONAL
                and agent.scope is not PortfolioScope.PERSONAL
            ):
                raise PortfolioScopeError(
                    "Personal systems cannot be assigned to non-personal agents."
                )
            if (
                agent.scope is PortfolioScope.PERSONAL
                and system_scope is not PortfolioScope.PERSONAL
            ):
                raise PortfolioScopeError("Personal agents cannot use non-personal systems.")
            if system_scope is PortfolioScope.BUSINESS and system_business_id != agent.business_id:
                raise PortfolioScopeError("Agents cannot use another business's systems.")

    def _assert_acyclic(self, connection: sqlite3.Connection, agent: ManagedAgent) -> None:
        cursor = agent.parent_agent_id
        visited = {agent.id}
        while cursor is not None:
            if cursor in visited:
                raise PortfolioHierarchyError("Agent hierarchy cannot contain a cycle.")
            visited.add(cursor)
            row = connection.execute(
                """
                SELECT parent_agent_id FROM portfolio_agents
                WHERE id = ? AND owner_id = ?
                """,
                (str(cursor), agent.owner_id),
            ).fetchone()
            if row is None:
                raise PortfolioHierarchyError("Agent hierarchy contains an inaccessible parent.")
            cursor = UUID(row["parent_agent_id"]) if row["parent_agent_id"] else None

    def get_agent(self, agent_id: UUID, *, owner_id: str) -> ManagedAgent | None:
        owner_id = self._validate_owner(owner_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM portfolio_agents WHERE id = ? AND owner_id = ?",
                (str(agent_id), owner_id),
            ).fetchone()
        return self._agent(row) if row is not None else None

    def list_agents(
        self,
        *,
        owner_id: str,
        business_id: UUID | None = None,
        scope: PortfolioScope | None = None,
        include_retired: bool = False,
        limit: int = 200,
    ) -> list[ManagedAgent]:
        owner_id = self._validate_owner(owner_id)
        limit = self._validate_limit(limit)
        query = "SELECT payload_json FROM portfolio_agents WHERE owner_id = ?"
        parameters: list[object] = [owner_id]
        if business_id is not None:
            query += " AND business_id = ?"
            parameters.append(str(business_id))
        if scope is not None:
            if not isinstance(scope, PortfolioScope):
                raise TypeError("Agent scope filter must be a PortfolioScope value.")
            query += " AND scope = ?"
            parameters.append(scope.value)
        if not include_retired:
            query += " AND status <> ?"
            parameters.append(LifecycleState.RETIRED.value)
        query += " ORDER BY created_at, id LIMIT ?"
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._agent(row) for row in rows]

    def pause_agent(self, *, owner_id: str, agent_id: UUID) -> ManagedAgent:
        """Fail closed by pausing an agent, disabling authority, and engaging its kill switch."""

        owner_id = self._validate_owner(owner_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM portfolio_agents WHERE id = ? AND owner_id = ?",
                (str(agent_id), owner_id),
            ).fetchone()
            if row is None:
                raise PortfolioNotFoundError("The agent does not exist for this owner.")
            agent = self._agent(row)
            if agent.status is LifecycleState.RETIRED:
                raise PortfolioValidationError("A retired agent cannot be paused.")
            disabled_authority = AuthorityPolicy(
                allowed_tools=agent.authority.allowed_tools,
                allowed_system_ids=agent.authority.allowed_system_ids,
                read_scopes=agent.authority.read_scopes,
                write_scopes=agent.authority.write_scopes,
                financial_actions=FinancialActionMode.NONE,
                human_approval_required=True,
                expires_at=agent.authority.expires_at,
            )
            payload = agent.model_dump(mode="python")
            payload.update(
                status=LifecycleState.PAUSED,
                execution_enabled=False,
                kill_switch_engaged=True,
                authority=disabled_authority,
                updated_at=self._now(),
            )
            paused = ManagedAgent.model_validate(payload)
            connection.execute(
                """
                UPDATE portfolio_agents
                SET status = ?, execution_enabled = 0, kill_switch_engaged = 1,
                    payload_json = ?, updated_at = ?
                WHERE id = ? AND owner_id = ?
                """,
                (
                    paused.status.value,
                    self._payload(paused),
                    self._iso(paused.updated_at),
                    str(agent_id),
                    owner_id,
                ),
            )
        return paused

    def create_system(self, system: SystemRegistration) -> SystemRegistration:
        system = SystemRegistration.model_validate(system.model_dump(mode="python"))
        owner_id = self._validate_owner(system.owner_id)
        if owner_id != system.owner_id:
            raise PortfolioScopeError("System owner_id must be normalized before storage.")
        self._require_business_if_scoped(
            owner_id=owner_id,
            business_id=system.business_id,
            entity="system",
        )
        if system.write_enabled:
            self._validate_execution_review(
                review_due_at=system.review_due_at,
                authority_expires_at=system.permission_expires_at,
                entity="System write access",
            )
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO portfolio_systems(
                        id, owner_id, business_id, scope, scope_key, name_key,
                        status, read_enabled, write_enabled, payload_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(system.id),
                        owner_id,
                        str(system.business_id) if system.business_id is not None else None,
                        system.scope.value,
                        self._scope_key(system.scope, system.business_id),
                        system.name.casefold(),
                        system.status.value,
                        int(system.read_enabled),
                        int(system.write_enabled),
                        self._payload(system),
                        self._iso(system.created_at),
                        self._iso(system.updated_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise self._translate_integrity(exc, "system") from exc
        return system

    def get_system(self, system_id: UUID, *, owner_id: str) -> SystemRegistration | None:
        owner_id = self._validate_owner(owner_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM portfolio_systems WHERE id = ? AND owner_id = ?",
                (str(system_id), owner_id),
            ).fetchone()
        return self._system(row) if row is not None else None

    def list_systems(
        self,
        *,
        owner_id: str,
        business_id: UUID | None = None,
        scope: PortfolioScope | None = None,
        include_retired: bool = False,
        limit: int = 200,
    ) -> list[SystemRegistration]:
        return self._list_scoped_records(
            table="portfolio_systems",
            parser=self._system,
            owner_id=owner_id,
            business_id=business_id,
            scope=scope,
            include_retired=include_retired,
            limit=limit,
        )

    def create_financial_account(
        self,
        account: FinancialAccountReference,
    ) -> FinancialAccountReference:
        account = FinancialAccountReference.model_validate(account.model_dump(mode="python"))
        owner_id = self._validate_owner(account.owner_id)
        if owner_id != account.owner_id:
            raise PortfolioScopeError("Account owner_id must be normalized before storage.")
        self._require_business_if_scoped(
            owner_id=owner_id,
            business_id=account.business_id,
            entity="financial account",
        )
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO portfolio_financial_accounts(
                        id, owner_id, business_id, scope, scope_key, alias_key,
                        status, payload_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(account.id),
                        owner_id,
                        str(account.business_id) if account.business_id is not None else None,
                        account.scope.value,
                        self._scope_key(account.scope, account.business_id),
                        account.account_alias.casefold(),
                        account.status.value,
                        self._payload(account),
                        self._iso(account.created_at),
                        self._iso(account.updated_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise self._translate_integrity(exc, "financial account") from exc
        return account

    def get_financial_account(
        self,
        account_id: UUID,
        *,
        owner_id: str,
    ) -> FinancialAccountReference | None:
        owner_id = self._validate_owner(owner_id)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM portfolio_financial_accounts
                WHERE id = ? AND owner_id = ?
                """,
                (str(account_id), owner_id),
            ).fetchone()
        return self._account(row) if row is not None else None

    def list_financial_accounts(
        self,
        *,
        owner_id: str,
        business_id: UUID | None = None,
        scope: PortfolioScope | None = None,
        include_retired: bool = False,
        limit: int = 200,
    ) -> list[FinancialAccountReference]:
        return self._list_scoped_records(
            table="portfolio_financial_accounts",
            parser=self._account,
            owner_id=owner_id,
            business_id=business_id,
            scope=scope,
            include_retired=include_retired,
            limit=limit,
        )

    def _require_business_if_scoped(
        self,
        *,
        owner_id: str,
        business_id: UUID | None,
        entity: str,
    ) -> None:
        if business_id is None:
            return
        with self._connection() as connection:
            row = connection.execute(
                "SELECT id FROM portfolio_businesses WHERE id = ? AND owner_id = ?",
                (str(business_id), owner_id),
            ).fetchone()
        if row is None:
            raise PortfolioScopeError(
                f"The {entity} business does not exist or belongs to another owner."
            )

    def _list_scoped_records(
        self,
        *,
        table: str,
        parser: object,
        owner_id: str,
        business_id: UUID | None,
        scope: PortfolioScope | None,
        include_retired: bool,
        limit: int,
    ) -> list[object]:
        owner_id = self._validate_owner(owner_id)
        limit = self._validate_limit(limit)
        if table not in {"portfolio_systems", "portfolio_financial_accounts"}:
            raise ValueError("Unsupported portfolio record table.")
        query = f"SELECT payload_json FROM {table} WHERE owner_id = ?"
        parameters: list[object] = [owner_id]
        if business_id is not None:
            query += " AND business_id = ?"
            parameters.append(str(business_id))
        if scope is not None:
            if not isinstance(scope, PortfolioScope):
                raise TypeError("Portfolio scope filter must be a PortfolioScope value.")
            query += " AND scope = ?"
            parameters.append(scope.value)
        if not include_retired:
            query += " AND status <> ?"
            parameters.append(LifecycleState.RETIRED.value)
        query += " ORDER BY created_at, id LIMIT ?"
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [parser(row) for row in rows]  # type: ignore[operator]

    def _validate_execution_review(
        self,
        *,
        review_due_at: datetime | None,
        authority_expires_at: datetime | None,
        entity: str,
    ) -> None:
        now = self._now()
        if review_due_at is None or authority_expires_at is None:
            raise PortfolioValidationError(
                f"{entity} execution requires explicit review and authority expiry times."
            )
        if review_due_at.tzinfo is None or authority_expires_at.tzinfo is None:
            raise PortfolioValidationError(
                f"{entity} review and expiry times must include a timezone."
            )
        if review_due_at.astimezone(UTC) <= now or authority_expires_at.astimezone(UTC) <= now:
            raise PortfolioValidationError(
                f"{entity} review and expiry times must be in the future."
            )

    def record_heartbeat(
        self,
        *,
        owner_id: str,
        agent_id: UUID,
        report: HeartbeatReport,
    ) -> AgentHeartbeat:
        owner_id = self._validate_owner(owner_id)
        report = HeartbeatReport.model_validate(report.model_dump(mode="python"))
        now = self._now()
        observed_at = report.observed_at
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        else:
            observed_at = observed_at.astimezone(UTC)
        if observed_at > now + timedelta(minutes=5):
            raise PortfolioValidationError(
                "A heartbeat cannot be more than five minutes in the future."
            )
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM portfolio_agents WHERE id = ? AND owner_id = ?",
                (str(agent_id), owner_id),
            ).fetchone()
            if row is None:
                raise PortfolioNotFoundError("The heartbeat agent does not exist for this owner.")
            agent = self._agent(row)
            if agent.status is LifecycleState.RETIRED:
                raise PortfolioValidationError("Retired agents cannot submit heartbeats.")
            heartbeat = AgentHeartbeat(
                owner_id=owner_id,
                agent_id=agent.id,
                business_id=agent.business_id,
                health=report.health,
                observed_at=observed_at,
                summary=report.summary,
                evidence_digest=report.evidence_digest,
                metrics=report.metrics,
                work_item_references=report.work_item_references,
                received_at=now,
            )
            try:
                connection.execute(
                    """
                    INSERT INTO portfolio_agent_heartbeats(
                        id, owner_id, agent_id, business_id, health,
                        observed_at, received_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(heartbeat.id),
                        owner_id,
                        str(agent.id),
                        str(agent.business_id) if agent.business_id is not None else None,
                        heartbeat.health.value,
                        self._iso(heartbeat.observed_at),
                        self._iso(heartbeat.received_at),
                        self._payload(heartbeat),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise self._translate_integrity(exc, "heartbeat") from exc
        return heartbeat

    def list_heartbeats(
        self,
        *,
        owner_id: str,
        agent_id: UUID,
        limit: int = 100,
    ) -> list[AgentHeartbeat]:
        owner_id = self._validate_owner(owner_id)
        limit = self._validate_limit(limit)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM portfolio_agent_heartbeats
                WHERE owner_id = ? AND agent_id = ?
                ORDER BY received_at DESC, id DESC LIMIT ?
                """,
                (owner_id, str(agent_id), limit),
            ).fetchall()
        return [self._heartbeat(row) for row in rows]

    def summary(self, *, owner_id: str) -> PortfolioSummary:
        owner_id = self._validate_owner(owner_id)
        with self._connection() as connection:
            businesses = self._count(connection, "portfolio_businesses", owner_id)
            agents = self._count(connection, "portfolio_agents", owner_id)
            systems = self._count(connection, "portfolio_systems", owner_id)
            accounts = self._count(connection, "portfolio_financial_accounts", owner_id)
            active_agents = connection.execute(
                "SELECT COUNT(*) FROM portfolio_agents WHERE owner_id = ? AND status = ?",
                (owner_id, LifecycleState.ACTIVE.value),
            ).fetchone()[0]
            execution_enabled_agents = connection.execute(
                """
                SELECT COUNT(*) FROM portfolio_agents
                WHERE owner_id = ? AND execution_enabled = 1
                """,
                (owner_id,),
            ).fetchone()[0]
            external_write_systems = connection.execute(
                """
                SELECT COUNT(*) FROM portfolio_systems
                WHERE owner_id = ? AND write_enabled = 1
                """,
                (owner_id,),
            ).fetchone()[0]
            healthy_agents = connection.execute(
                """
                WITH latest AS (
                    SELECT agent_id, MAX(received_at) AS received_at
                    FROM portfolio_agent_heartbeats
                    WHERE owner_id = ? GROUP BY agent_id
                )
                SELECT COUNT(DISTINCT h.agent_id)
                FROM latest
                JOIN portfolio_agent_heartbeats h
                  ON h.owner_id = ? AND h.agent_id = latest.agent_id
                 AND h.received_at = latest.received_at
                JOIN portfolio_agents a
                  ON a.owner_id = h.owner_id AND a.id = h.agent_id
                WHERE h.health = ? AND a.status <> ?
                """,
                (
                    owner_id,
                    owner_id,
                    HeartbeatHealth.HEALTHY.value,
                    LifecycleState.RETIRED.value,
                ),
            ).fetchone()[0]
        is_blank = businesses + agents + systems + accounts == 0
        return PortfolioSummary(
            owner_id=owner_id,
            businesses=businesses,
            agents=agents,
            systems=systems,
            financial_accounts=accounts,
            active_agents=active_agents,
            execution_enabled_agents=execution_enabled_agents,
            external_write_enabled_systems=external_write_systems,
            healthy_agents=healthy_agents,
            is_blank=is_blank,
        )

    @staticmethod
    def _count(connection: sqlite3.Connection, table: str, owner_id: str) -> int:
        if table not in {
            "portfolio_businesses",
            "portfolio_agents",
            "portfolio_systems",
            "portfolio_financial_accounts",
        }:
            raise ValueError("Unsupported portfolio count table.")
        return connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE owner_id = ?",
            (owner_id,),
        ).fetchone()[0]

    def onboarding_state(self, *, owner_id: str) -> OnboardingState:
        owner_id = self._validate_owner(owner_id)
        summary = self.summary(owner_id=owner_id)
        with self._connection() as connection:
            mission_count = connection.execute(
                """
                SELECT COUNT(*) FROM portfolio_businesses
                WHERE owner_id = ? AND TRIM(json_extract(payload_json, '$.mission')) <> ''
                """,
                (owner_id,),
            ).fetchone()[0]
            heartbeat_count = connection.execute(
                "SELECT COUNT(*) FROM portfolio_agent_heartbeats WHERE owner_id = ?",
                (owner_id,),
            ).fetchone()[0]
        steps = [
            OnboardingStep(
                key="register_first_business",
                title="Register the first business",
                complete=summary.businesses > 0,
            ),
            OnboardingStep(
                key="define_business_mission",
                title="Define the business mission and success measures",
                complete=mission_count > 0,
            ),
            OnboardingStep(
                key="register_systems",
                title="Register systems without granting access",
                complete=summary.systems > 0,
            ),
            OnboardingStep(
                key="register_agents",
                title="Register paused agents with isolated memory",
                complete=summary.agents > 0,
            ),
            OnboardingStep(
                key="verify_heartbeat",
                title="Verify an agent heartbeat and evidence path",
                complete=heartbeat_count > 0,
            ),
            OnboardingStep(
                key="approve_bounded_authority",
                title="Human-review bounded authority before activation",
                complete=summary.execution_enabled_agents > 0,
                requires_human=True,
            ),
        ]
        next_step = next((step.key for step in steps if not step.complete), None)
        return OnboardingState(
            owner_id=owner_id,
            is_blank=summary.is_blank,
            ready_for_autonomy=bool(steps) and all(step.complete for step in steps),
            next_step=next_step,
            steps=steps,
        )

    def health(self) -> bool:
        try:
            with self._connection() as connection:
                result = connection.execute("PRAGMA quick_check(1)").fetchone()
            return result is not None and result[0] == "ok"
        except sqlite3.Error:
            return False
