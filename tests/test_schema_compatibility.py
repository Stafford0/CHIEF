from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from chief.events.store import EventStore
from chief.release import CURRENT_SCHEMA_VERSIONS, SchemaCompatibilityService
from chief.work.store import WorkStore


def test_schema_service_baselines_known_v1_tables(tmp_path: Path) -> None:
    database = tmp_path / "chief.db"
    WorkStore(database)
    EventStore(database)
    service = SchemaCompatibilityService(database)

    report = service.inspect()

    assert report.integrity_ok is True
    assert report.compatible is True
    assert report.applied_versions["work"] == 1
    assert report.applied_versions["events"] == 1
    assert "work" not in report.missing_components
    assert "events" not in report.missing_components


def test_schema_service_fails_closed_on_newer_component_version(tmp_path: Path) -> None:
    database = tmp_path / "chief.db"
    WorkStore(database)
    service = SchemaCompatibilityService(database)
    service.baseline_known_v1_components()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO chief_component_migrations(component, version, applied_at) VALUES (?, ?, ?)",
            ("work", CURRENT_SCHEMA_VERSIONS["work"] + 1, "2026-09-02T03:00:00+00:00"),
        )

    report = service.inspect(baseline=False)

    assert report.compatible is False
    assert report.newer_components == ("work",)
    with pytest.raises(RuntimeError, match="not compatible"):
        service.assert_upgrade_safe()


def test_schema_service_blocks_unsafe_rollback(tmp_path: Path) -> None:
    database = tmp_path / "chief.db"
    WorkStore(database)
    service = SchemaCompatibilityService(database)
    service.baseline_known_v1_components()

    with pytest.raises(RuntimeError, match="Rollback is unsafe"):
        service.assert_rollback_safe({"work": 0})

    service.assert_rollback_safe({"work": 1})
