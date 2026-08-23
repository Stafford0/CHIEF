from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from chief.core import app as app_module
from chief.events.scheduler import Scheduler
from chief.events.store import EventStore
from chief.foresight.store import ForesightStore
from chief.runs import RunEngine, SQLiteRunStore
from chief.work.store import WorkStore


def make_client(tmp_path, monkeypatch) -> TestClient:
    database = tmp_path / "cofounder.db"
    work_store = WorkStore(database)
    event_store = EventStore(database)
    foresight_store = ForesightStore(database)
    run_store = SQLiteRunStore(database)
    monkeypatch.setattr(app_module, "work_store", work_store)
    monkeypatch.setattr(app_module, "event_store", event_store)
    monkeypatch.setattr(app_module, "scheduler", Scheduler(event_store))
    monkeypatch.setattr(app_module, "foresight_store", foresight_store)
    monkeypatch.setattr(app_module, "run_store", run_store)
    monkeypatch.setattr(
        app_module,
        "run_engine",
        RunEngine(
            run_store,
            {
                "briefing.generate": app_module._generate_briefing_step,
                "foresight.snapshot": app_module._foresight_snapshot_step,
            },
        ),
    )
    return TestClient(app_module.app)


def test_schedule_queues_exactly_one_durable_event(tmp_path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    response = client.post(
        "/schedules",
        json={
            "name": "Brief now",
            "event_type": "briefing.daily",
            "cadence": "once",
            "run_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        },
    )
    assert response.status_code == 201

    assert client.post("/scheduler/tick").status_code == 200
    assert len(client.get("/events").json()) == 1
    assert client.post("/scheduler/tick").json() is None


def test_foresight_api_ranks_evidence_backed_signal(tmp_path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    response = client.post(
        "/signals",
        json={
            "kind": "opportunity",
            "title": "Expansion revenue increased",
            "summary": "Existing customers expanded usage above baseline.",
            "impact": 4,
            "urgency": 3,
            "confidence": 0.9,
            "evidence_refs": ["analytics://expansion/2026-08-23"],
        },
    )
    assert response.status_code == 201

    snapshot = client.get("/foresight").json()
    assert snapshot["signals"][0]["signal"]["title"] == "Expansion revenue increased"
    assert snapshot["signals"][0]["attention"]["score"] > 0


def test_durable_run_executes_only_registered_read_action(tmp_path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    created = client.post(
        "/runs",
        json={
            "idempotency_key": "briefing-test",
            "steps": [
                {
                    "action": "briefing.generate",
                    "idempotency_key": "briefing-step",
                    "verification_required": True,
                }
            ],
        },
    )
    assert created.status_code == 201
    run_id = created.json()["id"]

    outcome = client.post("/runs/worker/tick")
    assert outcome.status_code == 200
    assert outcome.json()["run_status"] == "succeeded"
    assert client.get(f"/runs/{run_id}").json()["status"] == "succeeded"
    assert client.get(f"/runs/{run_id}/steps").json()[0]["verification_status"] == "verified"
