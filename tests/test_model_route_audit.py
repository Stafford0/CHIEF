from __future__ import annotations

import sqlite3
from pathlib import Path

from chief.models.base import ModelPrivacy
from chief.models.route_audit import SQLiteModelRouteStore
from chief.models.router import RouteAttempt
from chief.release import SchemaCompatibilityService


def test_model_route_receipt_omits_prompt_and_error_content(tmp_path: Path) -> None:
    database = tmp_path / "chief.db"
    store = SQLiteModelRouteStore(database)

    receipt = store.record(
        actor_id="local",
        request_id="request-1",
        attempts=[
            RouteAttempt("openai", False, "SECRET provider error body"),
            RouteAttempt("anthropic", True),
        ],
        selected_provider="anthropic",
        selected_model="configured-model",
        selected_privacy=ModelPrivacy.CLOUD,
        latency_ms=123.0,
        max_cost_tier=2,
        cloud_authorized=True,
        succeeded=True,
    )

    assert receipt.attempts[0]["failure_recorded"] is True
    assert "SECRET" not in str(receipt.model_dump())
    with sqlite3.connect(database) as connection:
        stored = " ".join(
            str(value)
            for row in connection.execute("SELECT * FROM model_route_receipts")
            for value in row
            if value is not None
        )
    assert "SECRET" not in stored
    assert "prompt" not in stored.casefold()


def test_model_route_receipts_are_actor_scoped(tmp_path: Path) -> None:
    store = SQLiteModelRouteStore(tmp_path / "chief.db")
    for actor in ("owner-a", "owner-b"):
        store.record(
            actor_id=actor,
            request_id=f"request-{actor}",
            attempts=[RouteAttempt("provider", True)],
            selected_provider="provider",
            selected_model="model",
            selected_privacy=ModelPrivacy.CLOUD,
            latency_ms=5.0,
            max_cost_tier=1,
            cloud_authorized=True,
            succeeded=True,
        )

    owner_a = store.list(actor_id="owner-a")
    assert len(owner_a) == 1
    assert owner_a[0].actor_id == "owner-a"


def test_model_route_table_is_in_schema_compatibility_gate(tmp_path: Path) -> None:
    database = tmp_path / "chief.db"
    SQLiteModelRouteStore(database)

    report = SchemaCompatibilityService(database).inspect()

    assert report.compatible is True
    assert report.applied_versions["model_routes"] == 1
    assert "model_routes" not in report.missing_components
