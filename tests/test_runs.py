import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from chief.runs import (
    ActionResult,
    AttemptStatus,
    IdempotencyConflict,
    LeaseLost,
    RetryableActionError,
    RunEngine,
    RunEventType,
    RunStatus,
    SQLiteRunStore,
    StepSpec,
    StepStatus,
    VerificationStatus,
)


def one_step(*, max_attempts: int = 3, verification_required: bool = False) -> list[StepSpec]:
    return [
        StepSpec(
            action="analyze",
            idempotency_key="analyze-v1",
            input_data={"subject": "parcel-signals"},
            max_attempts=max_attempts,
            verification_required=verification_required,
        )
    ]


def test_create_run_is_idempotent_and_binds_key_to_exact_plan(tmp_path) -> None:
    path = tmp_path / "runs.db"
    store = SQLiteRunStore(path)
    first = store.create_run(
        idempotency_key="morning-brief-2026-08-23",
        correlation_id="corr-1",
        input_data={"date": "2026-08-23"},
        steps=one_step(),
    )
    replay = store.create_run(
        idempotency_key="morning-brief-2026-08-23",
        correlation_id="ignored-on-replay",
        input_data={"date": "2026-08-23"},
        steps=one_step(),
    )

    assert replay.id == first.id
    assert first.status == RunStatus.QUEUED
    assert len(first.input_digest) == 64
    assert len(first.plan_digest) == 64
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].casefold() == "wal"
    with pytest.raises(IdempotencyConflict):
        store.create_run(
            idempotency_key="morning-brief-2026-08-23",
            input_data={"date": "different"},
            steps=one_step(),
        )


def test_step_claim_is_atomic_across_concurrent_store_instances(tmp_path) -> None:
    path = tmp_path / "runs.db"
    creator = SQLiteRunStore(path)
    creator.create_run(idempotency_key="atomic-claim", steps=one_step())
    stores = [SQLiteRunStore(path) for _ in range(8)]
    now = datetime(2026, 8, 23, 12, tzinfo=UTC)

    def claim(index: int):
        return stores[index].claim_next_step(worker_id=f"worker-{index}", lease_seconds=30, now=now)

    with ThreadPoolExecutor(max_workers=8) as executor:
        claims = list(executor.map(claim, range(8)))

    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    assert winners[0].step.status == StepStatus.RUNNING
    assert winners[0].attempt.attempt_number == 1


def test_expired_lease_recovers_after_restart_and_rejects_stale_worker(tmp_path) -> None:
    path = tmp_path / "runs.db"
    started = datetime(2026, 8, 23, 12, tzinfo=UTC)
    original_store = SQLiteRunStore(path)
    run = original_store.create_run(
        idempotency_key="restart-recovery", steps=one_step(max_attempts=2), now=started
    )
    first = original_store.claim_next_step(
        worker_id="worker-before-restart", lease_seconds=5, now=started
    )
    assert first is not None

    restarted_store = SQLiteRunStore(path)
    recovered = restarted_store.claim_next_step(
        worker_id="worker-after-restart",
        lease_seconds=30,
        now=started + timedelta(seconds=6),
    )

    assert recovered is not None
    assert recovered.step.id == first.step.id
    assert recovered.attempt.attempt_number == 2
    attempts = restarted_store.list_attempts(first.step.id)
    assert [attempt.status for attempt in attempts] == [
        AttemptStatus.ABANDONED,
        AttemptStatus.RUNNING,
    ]
    with pytest.raises(LeaseLost):
        original_store.complete_step(
            first.lease_token,
            result_data={"late": True},
            now=started + timedelta(seconds=6),
        )
    assert restarted_store.get_run(run.id).status == RunStatus.RUNNING


def test_committed_checkpoint_resumes_at_next_step_after_restart(tmp_path) -> None:
    path = tmp_path / "runs.db"
    started = datetime(2026, 8, 23, 12, tzinfo=UTC)
    first_store = SQLiteRunStore(path)
    run = first_store.create_run(
        idempotency_key="checkpoint-restart",
        steps=[
            StepSpec(action="collect", idempotency_key="collect-v1"),
            StepSpec(action="summarize", idempotency_key="summarize-v1"),
        ],
        now=started,
    )
    first = first_store.claim_next_step(worker_id="worker-1", now=started)
    assert first is not None
    first_store.complete_step(
        first.lease_token,
        result_data={"facts": 3},
        now=started + timedelta(seconds=1),
    )

    restarted_store = SQLiteRunStore(path)
    resumed = restarted_store.claim_next_step(
        worker_id="worker-2", now=started + timedelta(seconds=2)
    )

    assert resumed is not None
    assert resumed.step.action == "summarize"
    assert resumed.step.ordinal == 1
    checkpoints = restarted_store.list_checkpoints(run.id)
    assert len(checkpoints) == 1
    assert checkpoints[0].data == {"facts": 3}


def test_engine_checkpoints_sequential_handlers_and_completes_run(tmp_path) -> None:
    store = SQLiteRunStore(tmp_path / "runs.db")
    run = store.create_run(
        idempotency_key="executive-brief",
        correlation_id="corr-executive-brief",
        steps=[
            StepSpec(
                action="collect",
                idempotency_key="collect-v1",
                input_data={"source": "local"},
            ),
            StepSpec(
                action="verify",
                idempotency_key="verify-v1",
                input_data={"minimum_sources": 1},
                verification_required=True,
            ),
        ],
    )
    seen_keys: list[str] = []

    def collect(context, arguments):
        seen_keys.append(context.idempotency_key)
        return {"facts": [arguments["source"]]}

    def verify(context, arguments):
        seen_keys.append(context.idempotency_key)
        return ActionResult(
            result_data={"verified_sources": arguments["minimum_sources"]},
            verification_status=VerificationStatus.VERIFIED,
        )

    outcomes = RunEngine(store, {"collect": collect, "verify": verify}).drain(worker_id="worker-1")

    finished = store.get_run(run.id)
    steps = store.list_steps(run.id)
    checkpoints = store.list_checkpoints(run.id)
    events = store.list_events(run.id)
    assert len(outcomes) == 2
    assert finished is not None and finished.status == RunStatus.SUCCEEDED
    assert [step.status for step in steps] == [StepStatus.SUCCEEDED, StepStatus.SUCCEEDED]
    assert len(checkpoints) == 2
    assert all(len(checkpoint.data_digest) == 64 for checkpoint in checkpoints)
    assert checkpoints[-1].verification_status == VerificationStatus.VERIFIED
    assert seen_keys == ["collect-v1", "verify-v1"]
    assert {event.correlation_id for event in events} == {"corr-executive-brief"}
    assert events[-1].event_type == RunEventType.RUN_SUCCEEDED


def test_engine_bounds_retries_and_fails_run_after_last_attempt(tmp_path) -> None:
    store = SQLiteRunStore(tmp_path / "runs.db")
    run = store.create_run(idempotency_key="bounded-retry", steps=one_step(max_attempts=2))

    def unavailable(_context, _arguments):
        raise RetryableActionError("temporary dependency failure", code="dependency_offline")

    engine = RunEngine(store, {"analyze": unavailable})
    first = engine.execute_once(worker_id="worker-1")
    second = engine.execute_once(worker_id="worker-1")

    step = store.list_steps(run.id)[0]
    attempts = store.list_attempts(step.id)
    finished = store.get_run(run.id)
    assert first is not None and first.step_status == StepStatus.RETRY_WAIT
    assert second is not None and second.step_status == StepStatus.FAILED
    assert finished is not None and finished.status == RunStatus.FAILED
    assert step.attempt_count == 2
    assert [attempt.status for attempt in attempts] == [
        AttemptStatus.FAILED,
        AttemptStatus.FAILED,
    ]
    assert finished.error_code == "dependency_offline"


def test_cancellation_invalidates_active_lease_and_is_idempotent(tmp_path) -> None:
    store = SQLiteRunStore(tmp_path / "runs.db")
    run = store.create_run(idempotency_key="cancel-me", steps=one_step())
    lease = store.claim_next_step(worker_id="worker-1")
    assert lease is not None

    cancelled = store.cancel_run(run.id, reason="Operator stopped the run.")
    replay = store.cancel_run(run.id, reason="Duplicate cancellation.")

    step = store.get_step(lease.step.id)
    attempts = store.list_attempts(lease.step.id)
    assert cancelled.status == RunStatus.CANCELLED
    assert replay.status == RunStatus.CANCELLED
    assert step is not None and step.status == StepStatus.CANCELLED
    assert attempts[0].status == AttemptStatus.CANCELLED
    assert store.claim_next_step(worker_id="worker-2") is None
    with pytest.raises(LeaseLost):
        store.complete_step(lease.lease_token, result_data={"too_late": True})


def test_verification_required_step_cannot_claim_success_without_evidence(tmp_path) -> None:
    store = SQLiteRunStore(tmp_path / "runs.db")
    run = store.create_run(
        idempotency_key="verification-gate",
        steps=one_step(max_attempts=1, verification_required=True),
    )
    engine = RunEngine(store, {"analyze": lambda _context, _arguments: {"answer": 42}})

    outcome = engine.execute_once(worker_id="worker-1")
    step = store.list_steps(run.id)[0]
    attempt = store.list_attempts(step.id)[0]

    assert outcome is not None and outcome.run_status == RunStatus.FAILED
    assert step.status == StepStatus.FAILED
    assert step.result_digest is not None and len(step.result_digest) == 64
    assert attempt.result_digest == step.result_digest
    assert step.error_code == "verification_incomplete"


def test_events_are_cursor_paginated_and_correlated(tmp_path) -> None:
    store = SQLiteRunStore(tmp_path / "runs.db", max_page_size=2)
    run = store.create_run(idempotency_key="events", correlation_id="corr-events", steps=one_step())
    RunEngine(store, {"analyze": lambda _context, _arguments: {"ok": True}}).execute_once(
        worker_id="worker-1"
    )

    first_page = store.list_events(run.id, limit=2)
    second_page = store.list_events(run.id, after_sequence=first_page[-1].sequence, limit=2)

    assert len(first_page) == 2
    assert second_page
    assert first_page[-1].sequence < second_page[0].sequence
    assert all(event.correlation_id == "corr-events" for event in first_page + second_page)
    with pytest.raises(ValueError, match="between 1 and 2"):
        store.list_events(run.id, limit=3)
