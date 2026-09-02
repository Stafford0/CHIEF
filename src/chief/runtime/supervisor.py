from __future__ import annotations

import shutil
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from chief.events.scheduler import Scheduler
from chief.events.store import EventStore
from chief.foresight.scoring import rank_signals
from chief.foresight.store import ForesightStore
from chief.runs import ActionResult, RunEngine, SQLiteRunStore, StepSpec, VerificationStatus
from chief.work.briefing import build_briefing
from chief.work.store import WorkStore


@dataclass(frozen=True, slots=True)
class RuntimeTick:
    status: str
    scheduled_events: int
    dispatched_events: int
    run_steps: int
    dead_letters: int
    free_disk_bytes: int
    reason: str | None = None


class RuntimeStateStore:
    """Persist worker heartbeat so backward clock jumps survive process restarts."""

    def __init__(self, database_path: str | Path = "data/chief.db") -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    last_tick_at TEXT,
                    last_status TEXT,
                    last_reason TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def last_tick_at(self) -> datetime | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT last_tick_at FROM runtime_state WHERE singleton = 1"
            ).fetchone()
        if row is None or row["last_tick_at"] is None:
            return None
        return datetime.fromisoformat(str(row["last_tick_at"])).astimezone(UTC)

    def record(self, *, now: datetime, status: str, reason: str | None) -> None:
        timestamp = now.astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO runtime_state(singleton, last_tick_at, last_status, last_reason, updated_at)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    last_tick_at=excluded.last_tick_at,
                    last_status=excluded.last_status,
                    last_reason=excluded.last_reason,
                    updated_at=excluded.updated_at
                """,
                (timestamp, status, reason, timestamp),
            )


class RuntimeSupervisor:
    """Continuously advance CHIEF's existing durable scheduler, event, and run queues."""

    def __init__(
        self,
        *,
        event_store: EventStore,
        scheduler: Scheduler,
        run_store: SQLiteRunStore,
        run_engine: RunEngine,
        state_store: RuntimeStateStore | None = None,
        worker_id: str = "chief-runtime",
        min_free_disk_bytes: int = 512 * 1024 * 1024,
        clock_skew_tolerance_seconds: int = 5,
        max_schedules_per_tick: int = 25,
        max_events_per_tick: int = 25,
        max_run_steps_per_tick: int = 50,
    ) -> None:
        if min_free_disk_bytes < 0:
            raise ValueError("min_free_disk_bytes cannot be negative")
        if not 0 <= clock_skew_tolerance_seconds <= 3600:
            raise ValueError("clock_skew_tolerance_seconds must be between 0 and 3600")
        for name, value in {
            "max_schedules_per_tick": max_schedules_per_tick,
            "max_events_per_tick": max_events_per_tick,
            "max_run_steps_per_tick": max_run_steps_per_tick,
        }.items():
            if not 1 <= value <= 10_000:
                raise ValueError(f"{name} must be between 1 and 10,000")
        self.event_store = event_store
        self.scheduler = scheduler
        self.run_store = run_store
        self.run_engine = run_engine
        self.state_store = state_store or RuntimeStateStore(event_store.database_path)
        self.worker_id = worker_id
        self.min_free_disk_bytes = min_free_disk_bytes
        self.clock_skew_tolerance = timedelta(seconds=clock_skew_tolerance_seconds)
        self.max_schedules_per_tick = max_schedules_per_tick
        self.max_events_per_tick = max_events_per_tick
        self.max_run_steps_per_tick = max_run_steps_per_tick

    def _free_disk_bytes(self) -> int:
        return shutil.disk_usage(self.event_store.database_path.parent).free

    def _preflight(self, now: datetime) -> tuple[int, str | None]:
        free_disk = self._free_disk_bytes()
        if free_disk < self.min_free_disk_bytes:
            return free_disk, (
                f"free disk {free_disk} is below required minimum {self.min_free_disk_bytes}"
            )
        previous = self.state_store.last_tick_at()
        if previous is not None and now + self.clock_skew_tolerance < previous:
            return free_disk, (
                f"clock moved backwards from {previous.isoformat()} to {now.isoformat()}"
            )
        return free_disk, None

    def _queue_due_schedules(self, *, now: datetime) -> int:
        queued = 0
        for _ in range(self.max_schedules_per_tick):
            event = self.scheduler.tick(self.worker_id, now=now)
            if event is None:
                break
            queued += 1
        return queued

    def _dispatch_event(self, event) -> bool:
        if event.event_type not in self.run_engine.handlers:
            self.event_store.complete_event(
                event.id,
                self.worker_id,
                success=False,
                error=f"No durable run handler is registered for event type '{event.event_type}'.",
            )
            return False
        self.run_store.create_run(
            idempotency_key=f"event:{event.idempotency_key}",
            correlation_id=event.correlation_id or str(event.id),
            input_data={"event_id": str(event.id), "source": event.source, **event.payload},
            steps=[
                StepSpec(
                    action=event.event_type,
                    idempotency_key=f"event:{event.id}:step:0",
                    input_data=dict(event.payload),
                    verification_required=True,
                )
            ],
        )
        self.event_store.complete_event(event.id, self.worker_id, success=True)
        return True

    def _dispatch_events(self, *, now: datetime) -> int:
        dispatched = 0
        for _ in range(self.max_events_per_tick):
            event = self.event_store.claim_event(self.worker_id, now=now)
            if event is None:
                break
            if self._dispatch_event(event):
                dispatched += 1
        return dispatched

    def tick_once(self, *, now: datetime | None = None) -> RuntimeTick:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        free_disk, reason = self._preflight(now)
        if reason is not None:
            self.state_store.record(now=now, status="degraded", reason=reason)
            return RuntimeTick(
                status="degraded",
                scheduled_events=0,
                dispatched_events=0,
                run_steps=0,
                dead_letters=self.event_store.counts().get("dead_letter", 0),
                free_disk_bytes=free_disk,
                reason=reason,
            )

        scheduled = self._queue_due_schedules(now=now)
        dispatched = self._dispatch_events(now=now)
        outcomes = self.run_engine.drain(
            worker_id=self.worker_id,
            max_steps=self.max_run_steps_per_tick,
        )
        dead_letters = self.event_store.counts().get("dead_letter", 0)
        status = "degraded" if dead_letters else "healthy"
        reason = f"{dead_letters} event(s) are in dead-letter state" if dead_letters else None
        self.state_store.record(now=now, status=status, reason=reason)
        return RuntimeTick(
            status=status,
            scheduled_events=scheduled,
            dispatched_events=dispatched,
            run_steps=len(outcomes),
            dead_letters=dead_letters,
            free_disk_bytes=free_disk,
            reason=reason,
        )

    def run_forever(
        self,
        *,
        stop_event: threading.Event | None = None,
        interval_seconds: float = 2.0,
    ) -> None:
        if not 0.1 <= interval_seconds <= 3600:
            raise ValueError("interval_seconds must be between 0.1 and 3600")
        stop_event = stop_event or threading.Event()
        while not stop_event.is_set():
            self.tick_once()
            stop_event.wait(interval_seconds)


def build_runtime_supervisor(
    *,
    database_path: str | Path = "data/chief.db",
    worker_id: str = "chief-runtime",
    min_free_disk_bytes: int = 512 * 1024 * 1024,
) -> RuntimeSupervisor:
    database_path = Path(database_path)
    work_store = WorkStore(database_path)
    foresight_store = ForesightStore(database_path)
    event_store = EventStore(database_path)
    run_store = SQLiteRunStore(database_path)

    def briefing_handler(_context, _arguments: dict[str, Any]) -> ActionResult:
        briefing = build_briefing(work_store, limit=10)
        return ActionResult(
            result_data=briefing.model_dump(mode="json"),
            verification_status=VerificationStatus.VERIFIED,
        )

    def foresight_handler(_context, _arguments: dict[str, Any]) -> ActionResult:
        ranked = rank_signals(foresight_store.list_signals(limit=100))[:10]
        return ActionResult(
            result_data={
                "signals": [
                    {
                        "signal": signal.model_dump(mode="json"),
                        "attention": score.model_dump(mode="json"),
                    }
                    for signal, score in ranked
                ]
            },
            verification_status=VerificationStatus.VERIFIED,
        )

    run_engine = RunEngine(
        run_store,
        {
            "briefing.generate": briefing_handler,
            "foresight.snapshot": foresight_handler,
        },
    )
    return RuntimeSupervisor(
        event_store=event_store,
        scheduler=Scheduler(event_store),
        run_store=run_store,
        run_engine=run_engine,
        state_store=RuntimeStateStore(database_path),
        worker_id=worker_id,
        min_free_disk_bytes=min_free_disk_bytes,
    )
