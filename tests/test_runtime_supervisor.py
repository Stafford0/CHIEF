from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from chief.events.scheduler import Scheduler
from chief.events.schema import Event, EventStatus, Schedule, ScheduleCadence
from chief.events.store import EventStore
from chief.runs import ActionResult, RunEngine, RunStatus, SQLiteRunStore, VerificationStatus
from chief.runtime.supervisor import RuntimeStateStore, RuntimeSupervisor

NOW = datetime(2026, 9, 1, 20, 0, tzinfo=UTC)


def _supervisor(tmp_path: Path, handlers=None, *, min_free_disk_bytes: int = 0):
    database = tmp_path / "chief.db"
    event_store = EventStore(database)
    run_store = SQLiteRunStore(database)
    run_engine = RunEngine(run_store, handlers or {})
    supervisor = RuntimeSupervisor(
        event_store=event_store,
        scheduler=Scheduler(event_store),
        run_store=run_store,
        run_engine=run_engine,
        state_store=RuntimeStateStore(database),
        min_free_disk_bytes=min_free_disk_bytes,
    )
    return supervisor, event_store, run_store


def test_supervisor_turns_due_schedule_into_verified_durable_run(tmp_path: Path) -> None:
    def handler(_context, arguments):
        return ActionResult(
            result_data={"received": arguments["value"]},
            verification_status=VerificationStatus.VERIFIED,
        )

    supervisor, event_store, run_store = _supervisor(
        tmp_path, {"demo.action": handler}
    )
    supervisor.scheduler.add(
        Schedule(
            name="Demo",
            event_type="demo.action",
            payload={"value": 7},
            cadence=ScheduleCadence.ONCE,
            run_at=NOW,
        ),
        now=NOW,
    )

    tick = supervisor.tick_once(now=NOW)

    assert tick.status == "healthy"
    assert tick.scheduled_events == 1
    assert tick.dispatched_events == 1
    assert tick.run_steps == 1
    runs = run_store.list_runs()
    assert len(runs) == 1
    assert runs[0].status is RunStatus.SUCCEEDED
    events = event_store.list_events()
    assert events[0].status is EventStatus.COMPLETED


def test_unknown_event_fails_closed_into_dead_letter(tmp_path: Path) -> None:
    supervisor, event_store, _ = _supervisor(tmp_path)
    event_store.enqueue(
        Event(
            event_type="unknown.action",
            idempotency_key="unknown-1",
            max_attempts=1,
            available_at=NOW,
            observed_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )

    tick = supervisor.tick_once(now=NOW)

    assert tick.dispatched_events == 0
    assert tick.dead_letters == 1
    assert tick.status == "degraded"
    assert "dead-letter" in (tick.reason or "")
    assert event_store.list_events()[0].status is EventStatus.DEAD_LETTER


def test_low_disk_blocks_queue_execution(tmp_path: Path) -> None:
    supervisor, event_store, _ = _supervisor(tmp_path, min_free_disk_bytes=10)
    supervisor._free_disk_bytes = lambda: 1  # type: ignore[method-assign]
    event_store.enqueue(
        Event(
            event_type="unknown.action",
            idempotency_key="pending",
            available_at=NOW,
            observed_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )

    tick = supervisor.tick_once(now=NOW)

    assert tick.status == "degraded"
    assert tick.run_steps == 0
    assert tick.dispatched_events == 0
    assert "free disk" in (tick.reason or "")
    assert event_store.list_events()[0].status is EventStatus.PENDING


def test_backward_clock_is_detected_across_worker_restart(tmp_path: Path) -> None:
    supervisor, _, _ = _supervisor(tmp_path)
    supervisor.state_store.record(
        now=datetime(2026, 9, 1, 20, 5, tzinfo=UTC),
        status="healthy",
        reason=None,
    )

    restarted, _, _ = _supervisor(tmp_path)
    tick = restarted.tick_once(now=NOW)

    assert tick.status == "degraded"
    assert "clock moved backwards" in (tick.reason or "")
