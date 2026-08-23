from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from chief.core import app as app_module
from chief.work.briefing import build_briefing
from chief.work.schema import Goal, Task, WorkPriority, WorkStatus
from chief.work.store import WorkStore


def test_work_store_persists_goals_and_tasks(tmp_path) -> None:
    path = tmp_path / "work.db"
    store = WorkStore(path)
    goal = store.save_goal(Goal(title="Reach product-market fit"))
    task = store.save_task(
        Task(
            goal_id=goal.id,
            title="Interview five customers",
            priority=WorkPriority.HIGH,
        )
    )

    reopened = WorkStore(path)

    assert reopened.get_goal(goal.id) == goal
    assert reopened.get_task(task.id) == task


def test_work_store_orders_attention_and_tracks_completion(tmp_path) -> None:
    store = WorkStore(tmp_path / "work.db")
    low = store.save_task(Task(title="Polish notes", priority=WorkPriority.LOW))
    critical = store.save_task(Task(title="Restore checkout", priority=WorkPriority.CRITICAL))

    assert [task.id for task in store.list_tasks()] == [critical.id, low.id]

    critical.status = WorkStatus.DONE
    saved = store.save_task(critical)
    assert saved.completed_at is not None
    assert [task.id for task in store.list_tasks()] == [low.id]
    assert store.get_task(critical.id) is not None


def test_briefing_prioritizes_blockers_and_overdue_work(tmp_path) -> None:
    store = WorkStore(tmp_path / "work.db")
    store.save_task(
        Task(
            title="Resolve launch blocker",
            priority=WorkPriority.HIGH,
            status=WorkStatus.BLOCKED,
            blocked_reason="Waiting on vendor",
            due_at=datetime.now(UTC) - timedelta(hours=2),
        )
    )

    briefing = build_briefing(store)

    assert briefing.items[0].urgency == 95
    assert "blocked" in briefing.items[0].reason
    assert "overdue" in briefing.items[0].reason
    assert briefing.counts["blocked_tasks"] == 1
    assert briefing.counts["overdue_tasks"] == 1


def test_work_api_round_trip(tmp_path, monkeypatch) -> None:
    store = WorkStore(tmp_path / "api-work.db")
    monkeypatch.setattr(app_module, "work_store", store)
    client = TestClient(app_module.app)

    goal_response = client.post(
        "/goals",
        json={"title": "Build the most useful co-founder", "target_date": "2026-12-31"},
    )
    assert goal_response.status_code == 201
    goal_id = goal_response.json()["id"]

    task_response = client.post(
        "/tasks",
        json={
            "title": "Ship evidence-backed daily briefing",
            "goal_id": goal_id,
            "priority": "critical",
        },
    )
    assert task_response.status_code == 201
    task_id = task_response.json()["id"]

    updated = client.patch(
        f"/tasks/{task_id}",
        json={"status": "blocked", "blocked_reason": "Needs connected business data"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "blocked"

    briefing = client.get("/briefing").json()
    assert briefing["items"][0]["task_id"] == task_id
    assert briefing["counts"]["active_goals"] == 1


def test_task_api_rejects_unknown_goal(tmp_path, monkeypatch) -> None:
    store = WorkStore(tmp_path / "api-work.db")
    monkeypatch.setattr(app_module, "work_store", store)
    client = TestClient(app_module.app)

    response = client.post(
        "/tasks",
        json={
            "title": "Orphan task",
            "goal_id": "724b6304-4f95-4755-b938-a6ed0d58d39b",
        },
    )

    assert response.status_code == 422
    assert "goal does not exist" in response.json()["detail"]
