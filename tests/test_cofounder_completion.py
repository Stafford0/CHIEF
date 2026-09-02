from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from chief.business import (
    Document,
    Provenance,
    ProvenanceType,
    SQLiteBusinessGraphStore,
)
from chief.cofounder import build_canonical_briefing
from chief.events.schema import Event, EventStatus
from chief.events.store import EventStore
from chief.operator import OperatorRecoveryService
from chief.work.schema import Task, WorkPriority, WorkStatus
from chief.work.store import WorkStore

NOW = datetime(2026, 9, 2, 3, 0, tzinfo=UTC)


def _dead_letter(database: Path):
    events = EventStore(database)
    event = events.enqueue(
        Event(
            event_type="missing.handler",
            idempotency_key="dead-letter-test",
            max_attempts=1,
            observed_at=NOW,
            available_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    claimed = events.claim_event("test-worker", now=NOW)
    assert claimed is not None
    return events.complete_event(
        event.id,
        "test-worker",
        success=False,
        error="No handler registered",
    )


def _conflicting_documents(database: Path) -> None:
    business = SQLiteBusinessGraphStore(database)
    for connector, digest, description in (
        ("source-a", "a" * 64, '{"title":"Market state","value":10}'),
        ("source-b", "b" * 64, '{"title":"Market state","value":20}'),
    ):
        business.add_node(
            Document(
                key=f"evidence:{connector}",
                name="Market state",
                description=description,
                owner_id="local",
                document_type="market_snapshot",
                content_digest=digest,
                confidence=0.9,
                valid_from=NOW,
                created_at=NOW,
                updated_at=NOW,
                tags=["evidence", "parcelsignals", connector],
                provenance=Provenance(
                    source_type=ProvenanceType.INTEGRATION,
                    source_id=f"{connector}:record-1",
                    captured_at=NOW,
                    evidence_digest=digest,
                ),
            )
        )


def test_canonical_briefing_reconciles_attention_and_conflicts(tmp_path: Path) -> None:
    database = tmp_path / "chief.db"
    WorkStore(database).save_task(
        Task(
            title="Resolve blocked launch task",
            status=WorkStatus.BLOCKED,
            priority=WorkPriority.CRITICAL,
            blocked_reason="provider acceptance missing",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    dead = _dead_letter(database)
    assert dead.status is EventStatus.DEAD_LETTER
    _conflicting_documents(database)

    briefing = build_canonical_briefing(
        database,
        principal_id="local",
        limit=7,
        now=NOW,
    )

    kinds = {item.kind for item in briefing.items}
    assert "task" in kinds
    assert "dead_letter" in kinds
    assert "evidence_conflict" in kinds
    assert briefing.counts["dead_letters"] == 1
    assert briefing.counts["evidence_conflicts"] == 1
    assert briefing.conflicts[0].title == "Market state"
    assert any("evidence conflict" in item.casefold() for item in briefing.unverified)
    assert all(item.owner == "local" for item in briefing.items)
    assert all(item.next_action for item in briefing.items)


def test_operator_recovery_retries_dead_letter_and_records_actor(tmp_path: Path) -> None:
    database = tmp_path / "chief.db"
    dead = _dead_letter(database)
    recovery = OperatorRecoveryService(database)

    retried, action = recovery.retry(
        dead.id,
        actor_id="local",
        reason="Handler was registered after the original attempts.",
        now=NOW,
    )

    assert retried.status is EventStatus.PENDING
    assert retried.attempts == 0
    assert action.actor_id == "local"
    assert action.action == "retry"
    assert action.previous_status is EventStatus.DEAD_LETTER
    assert recovery.status(now=NOW).dead_letters == 0
    history = recovery.history(event_id=dead.id)
    assert [item.action for item in history] == ["retry"]


def test_operator_can_explicitly_dismiss_dead_letter(tmp_path: Path) -> None:
    database = tmp_path / "chief.db"
    dead = _dead_letter(database)
    recovery = OperatorRecoveryService(database)

    dismissed, action = recovery.dismiss(
        dead.id,
        actor_id="local",
        reason="Obsolete scheduled request.",
        now=NOW,
    )

    assert dismissed.status is EventStatus.FAILED
    assert "Operator dismissed" in (dismissed.last_error or "")
    assert action.action == "dismiss"
    assert recovery.list_dead_letters() == []
    assert recovery.status(now=NOW).dead_letters == 0
