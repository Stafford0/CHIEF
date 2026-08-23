from uuid import uuid4

from fastapi.testclient import TestClient

from chief.core.app import app

client = TestClient(app)


def test_business_api_derives_owner_from_authenticated_actor() -> None:
    key = f"product-{uuid4()}"
    response = client.post(
        "/business/nodes",
        json={
            "kind": "product",
            "key": key,
            "name": "CHIEF",
            "owner_id": "client-supplied-owner",
            "provenance": {"source_type": "user"},
            "lifecycle_stage": "development",
        },
    )

    assert response.status_code == 201
    node = response.json()
    assert node["owner_id"] == "local"
    assert client.get(f"/business/nodes/{node['id']}").json()["key"] == key


def test_decision_api_persists_and_scores_transparent_inputs() -> None:
    criterion_id = str(uuid4())
    response = client.post(
        "/decisions",
        json={
            "title": f"Decision {uuid4()}",
            "question": "Which path should CHIEF take?",
            "criteria": [{"id": criterion_id, "name": "Strategic value", "weight": 2.0}],
            "options": [
                {
                    "name": "Build",
                    "criterion_scores": [
                        {
                            "criterion_id": criterion_id,
                            "score": 0.8,
                            "confidence": 0.7,
                            "rationale": "The evidence supports owning the capability.",
                        }
                    ],
                }
            ],
        },
    )

    assert response.status_code == 201
    scored = client.post(
        f"/decisions/{response.json()['id']}/score",
        json={"weight_overrides": {criterion_id: 3.0}},
    )
    assert scored.status_code == 200
    assert scored.json()["options"][0]["total_score"] == 0.8
    assert scored.json()["options"][0]["contributions"][0]["rationale"]


def test_notification_api_is_idempotent_and_attention_budgeted() -> None:
    key = f"notice-{uuid4()}"
    payload = {
        "source": "foresight",
        "title": "Review an evidence-backed risk",
        "body": "The signal crossed its configured threshold.",
        "priority": 20,
        "channels": ["in_app"],
        "idempotency_key": key,
        "dedup_key": key,
    }

    first = client.post("/notifications", json=payload)
    repeated = client.post("/notifications", json=payload)

    assert first.status_code == 201
    assert repeated.status_code == 201
    assert repeated.json()["notification"]["id"] == first.json()["notification"]["id"]
    assert first.json()["attention"]["action"] == "digest"

    conflict = client.post("/notifications", json={**payload, "title": "Different"})
    assert conflict.status_code == 409


def test_domain_mutations_are_audited_without_copying_content() -> None:
    title = f"Confidential goal {uuid4()}"
    created = client.post("/goals", json={"title": title})

    assert created.status_code == 201
    latest = client.get("/audit/events", params={"limit": 1}).json()[0]
    assert latest["tool_name"] == "domain.goal"
    assert latest["metadata"]["entity_id"] == created.json()["id"]
    assert title not in str(latest)
