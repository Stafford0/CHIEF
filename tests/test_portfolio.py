from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from chief.portfolio import (
    AgentRole,
    AuthorityPolicy,
    BusinessUnit,
    CredentialReference,
    FinancialAccountKind,
    FinancialAccountReference,
    HeartbeatHealth,
    HeartbeatReport,
    LifecycleState,
    ManagedAgent,
    PortfolioConflictError,
    PortfolioHierarchyError,
    PortfolioNotFoundError,
    PortfolioScope,
    PortfolioScopeError,
    PortfolioValidationError,
    SQLitePortfolioStore,
    SystemKind,
    SystemRegistration,
)


@pytest.fixture
def store(tmp_path) -> SQLitePortfolioStore:
    return SQLitePortfolioStore(tmp_path / "portfolio.db")


def business(owner_id: str = "owner-a", *, key: str = "alpha") -> BusinessUnit:
    return BusinessUnit(
        owner_id=owner_id,
        key=key,
        name=key.title(),
        mission="Create measurable customer value.",
    )


def governor(unit: BusinessUnit, *, name: str = "Governor") -> ManagedAgent:
    return ManagedAgent(
        owner_id=unit.owner_id,
        business_id=unit.id,
        role=AgentRole.BUSINESS_GOVERNOR,
        scope=PortfolioScope.BUSINESS,
        name=name,
        mission="Govern the business within approved limits.",
    )


def test_registry_starts_completely_blank_and_safe(store: SQLitePortfolioStore) -> None:
    summary = store.summary(owner_id="owner-a")
    onboarding = store.onboarding_state(owner_id="owner-a")

    assert summary.is_blank is True
    assert summary.businesses == 0
    assert summary.agents == 0
    assert summary.systems == 0
    assert summary.financial_accounts == 0
    assert summary.execution_enabled_agents == 0
    assert summary.external_write_enabled_systems == 0
    assert store.list_businesses(owner_id="owner-a") == []
    assert store.list_agents(owner_id="owner-a") == []
    assert onboarding.is_blank is True
    assert onboarding.ready_for_autonomy is False
    assert onboarding.next_step == "register_first_business"
    assert onboarding.steps[-1].requires_human is True
    assert store.health() is True


def test_new_businesses_and_agents_are_fail_closed(store: SQLitePortfolioStore) -> None:
    unit = store.create_business(business())
    agent = store.create_agent(governor(unit))

    assert unit.status is LifecycleState.DRAFT
    assert unit.monitoring_enabled is False
    assert unit.execution_enabled is False
    assert unit.kill_switch_engaged is True
    assert unit.authority_ceiling.enabled is False
    assert unit.budget.monthly_compute_limit == 0
    assert agent.status is LifecycleState.DRAFT
    assert agent.execution_enabled is False
    assert agent.kill_switch_engaged is True
    assert agent.authority.enabled is False
    assert agent.authority.allowed_tools == []
    assert agent.authority.allowed_system_ids == []
    assert agent.budget.max_parallel_runs == 0
    assert agent.memory_namespace != unit.memory_namespace


def test_owner_isolation_applies_to_reads_and_references(store: SQLitePortfolioStore) -> None:
    unit = store.create_business(business())

    assert store.get_business(unit.id, owner_id="owner-b") is None
    assert store.list_businesses(owner_id="owner-b") == []
    with pytest.raises(PortfolioScopeError):
        store.create_agent(
            ManagedAgent(
                owner_id="owner-b",
                business_id=unit.id,
                role=AgentRole.BUSINESS_GOVERNOR,
                scope=PortfolioScope.BUSINESS,
                name="Wrong owner",
                mission="This cross-owner reference must fail.",
            )
        )


def test_business_key_and_memory_namespaces_are_unique_per_owner(
    store: SQLitePortfolioStore,
) -> None:
    first = store.create_business(business())

    with pytest.raises(PortfolioConflictError):
        store.create_business(business())
    with pytest.raises(PortfolioConflictError):
        store.create_business(
            BusinessUnit(
                owner_id="owner-a",
                key="different-key",
                name="Different",
                memory_namespace=first.memory_namespace,
            )
        )
    store.create_business(business("owner-b"))


def test_credentials_are_references_only_and_raw_secret_fields_are_rejected() -> None:
    reference = CredentialReference(uri="vault://chief/agents/governor")
    assert reference.uri.startswith("vault://")

    with pytest.raises(ValidationError):
        CredentialReference(uri="sk-live-raw-secret")
    with pytest.raises(ValidationError):
        CredentialReference(uri="https://vault.example/secret")
    with pytest.raises(ValidationError):
        SystemRegistration(
            owner_id="owner-a",
            scope=PortfolioScope.PORTFOLIO,
            kind=SystemKind.SAAS,
            name="CRM",
            api_key="raw-secret",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        FinancialAccountReference(
            owner_id="owner-a",
            scope=PortfolioScope.PORTFOLIO,
            kind=FinancialAccountKind.BANK,
            account_alias="Operating",
            institution="Example Bank",
            balance=10_000,  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        FinancialAccountReference(
            owner_id="owner-a",
            scope=PortfolioScope.PORTFOLIO,
            kind=FinancialAccountKind.BANK,
            account_alias="Operating",
            institution="Example Bank",
            transaction_writes_enabled=True,  # type: ignore[call-arg]
        )


def test_financial_accounts_store_metadata_without_authority(
    store: SQLitePortfolioStore,
) -> None:
    account = FinancialAccountReference(
        owner_id="owner-a",
        scope=PortfolioScope.PORTFOLIO,
        kind=FinancialAccountKind.BANK,
        account_alias="Operating metadata",
        institution="Example Bank",
        provider_account_id_digest="a" * 64,
        credential_reference=CredentialReference(uri="env://CHIEF_BANK_CONNECTION"),
    )

    stored = store.create_financial_account(account)
    payload = stored.model_dump(mode="json")

    assert "balance" not in payload
    assert "transaction_writes_enabled" not in payload
    assert store.get_financial_account(account.id, owner_id="owner-a") == account
    assert store.get_financial_account(account.id, owner_id="owner-b") is None


def test_specialists_require_a_same_business_governor(store: SQLitePortfolioStore) -> None:
    first = store.create_business(business(key="alpha"))
    second = store.create_business(business(key="beta"))
    parent = store.create_agent(governor(first))

    with pytest.raises(ValidationError):
        ManagedAgent(
            owner_id=first.owner_id,
            business_id=first.id,
            role=AgentRole.SPECIALIST,
            scope=PortfolioScope.BUSINESS,
            name="Orphan specialist",
            mission="Should not exist without a governor.",
        )
    with pytest.raises(PortfolioHierarchyError):
        store.create_agent(
            ManagedAgent(
                owner_id=second.owner_id,
                business_id=second.id,
                parent_agent_id=parent.id,
                role=AgentRole.SPECIALIST,
                scope=PortfolioScope.BUSINESS,
                name="Cross-business specialist",
                mission="This hierarchy must fail.",
            )
        )


def test_agent_authority_cannot_cross_business_or_personal_system_boundaries(
    store: SQLitePortfolioStore,
) -> None:
    first = store.create_business(business(key="alpha"))
    second = store.create_business(business(key="beta"))
    second_system = store.create_system(
        SystemRegistration(
            owner_id="owner-a",
            business_id=second.id,
            scope=PortfolioScope.BUSINESS,
            kind=SystemKind.SAAS,
            name="Beta CRM",
        )
    )
    personal_system = store.create_system(
        SystemRegistration(
            owner_id="owner-a",
            scope=PortfolioScope.PERSONAL,
            kind=SystemKind.DATA_SOURCE,
            name="Personal records",
            contains_personal_data=True,
        )
    )

    with pytest.raises(PortfolioScopeError):
        store.create_agent(
            governor(first).model_copy(
                update={"authority": AuthorityPolicy(allowed_system_ids=[second_system.id])}
            )
        )
    with pytest.raises(PortfolioScopeError):
        store.create_agent(
            governor(first, name="Personal data attempt").model_copy(
                update={"authority": AuthorityPolicy(allowed_system_ids=[personal_system.id])}
            )
        )


def test_business_execution_gate_caps_business_agent_execution(
    store: SQLitePortfolioStore,
) -> None:
    unit = store.create_business(business())
    agent = governor(unit).model_copy(
        update={
            "status": LifecycleState.ACTIVE,
            "kill_switch_engaged": False,
            "execution_enabled": True,
            "authority": AuthorityPolicy(
                enabled=True,
                expires_at=datetime.now(UTC) + timedelta(days=30),
            ),
            "review_due_at": datetime.now(UTC) + timedelta(days=14),
        }
    )
    agent = ManagedAgent.model_validate(agent.model_dump(mode="python"))

    with pytest.raises(PortfolioValidationError):
        store.create_agent(agent)


def test_heartbeat_is_append_only_and_never_activates_agent(
    store: SQLitePortfolioStore,
) -> None:
    unit = store.create_business(business())
    agent = store.create_agent(governor(unit))

    heartbeat = store.record_heartbeat(
        owner_id="owner-a",
        agent_id=agent.id,
        report=HeartbeatReport(
            health=HeartbeatHealth.HEALTHY,
            summary="All read-only checks passed.",
            evidence_digest="b" * 64,
            metrics={"queue_depth": 0.0},
        ),
    )

    unchanged = store.get_agent(agent.id, owner_id="owner-a")
    assert heartbeat.business_id == unit.id
    assert heartbeat.owner_id == "owner-a"
    assert unchanged is not None
    assert unchanged.status is LifecycleState.DRAFT
    assert unchanged.execution_enabled is False
    assert unchanged.kill_switch_engaged is True
    assert store.list_heartbeats(owner_id="owner-a", agent_id=agent.id) == [heartbeat]
    assert store.summary(owner_id="owner-a").healthy_agents == 1


def test_heartbeat_rejects_wrong_owner_and_future_observations(
    store: SQLitePortfolioStore,
) -> None:
    unit = store.create_business(business())
    agent = store.create_agent(governor(unit))

    with pytest.raises(PortfolioNotFoundError):
        store.record_heartbeat(
            owner_id="owner-b",
            agent_id=agent.id,
            report=HeartbeatReport(),
        )
    with pytest.raises(PortfolioValidationError):
        store.record_heartbeat(
            owner_id="owner-a",
            agent_id=agent.id,
            report=HeartbeatReport(observed_at=datetime.now(UTC) + timedelta(minutes=6)),
        )


def test_pause_agent_fails_closed_without_erasing_read_configuration(
    store: SQLitePortfolioStore,
) -> None:
    active = ManagedAgent(
        owner_id="owner-a",
        role=AgentRole.PORTFOLIO_OPERATIONS,
        scope=PortfolioScope.PORTFOLIO,
        name="Portfolio monitor",
        mission="Monitor portfolio health.",
        status=LifecycleState.ACTIVE,
        execution_enabled=True,
        kill_switch_engaged=False,
        authority=AuthorityPolicy(
            enabled=True,
            allowed_tools=["portfolio.read"],
            read_scopes=["portfolio.health"],
            expires_at=datetime.now(UTC) + timedelta(days=30),
        ),
        review_due_at=datetime.now(UTC) + timedelta(days=14),
    )
    store.create_agent(active)

    paused = store.pause_agent(owner_id="owner-a", agent_id=active.id)

    assert paused.status is LifecycleState.PAUSED
    assert paused.execution_enabled is False
    assert paused.kill_switch_engaged is True
    assert paused.authority.enabled is False
    assert paused.authority.allowed_tools == ["portfolio.read"]
    assert paused.authority.read_scopes == ["portfolio.health"]


def test_onboarding_advances_but_requires_human_authority_review(
    store: SQLitePortfolioStore,
) -> None:
    unit = store.create_business(business())
    store.create_system(
        SystemRegistration(
            owner_id=unit.owner_id,
            business_id=unit.id,
            scope=PortfolioScope.BUSINESS,
            kind=SystemKind.SAAS,
            name="Read-only CRM registration",
        )
    )
    agent = store.create_agent(governor(unit))
    store.record_heartbeat(
        owner_id=unit.owner_id,
        agent_id=agent.id,
        report=HeartbeatReport(health=HeartbeatHealth.HEALTHY),
    )

    onboarding = store.onboarding_state(owner_id=unit.owner_id)

    assert onboarding.is_blank is False
    assert onboarding.next_step == "approve_bounded_authority"
    assert onboarding.ready_for_autonomy is False
    assert onboarding.steps[-1].requires_human is True


def test_agent_self_parent_is_rejected_before_persistence() -> None:
    agent_id = uuid4()
    with pytest.raises(ValidationError):
        ManagedAgent(
            id=agent_id,
            owner_id="owner-a",
            business_id=uuid4(),
            parent_agent_id=agent_id,
            role=AgentRole.SPECIALIST,
            scope=PortfolioScope.BUSINESS,
            name="Cycle",
            mission="Must be rejected.",
        )
