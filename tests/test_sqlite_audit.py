import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from chief.audit.log import AuditEvent
from chief.audit.sqlite import SQLiteAuditLog


def event(index: int = 1) -> AuditEvent:
    return AuditEvent(
        tool_name="test_tool",
        approved=index % 2 == 0,
        decision="allow",
        success=True,
        metadata={
            "argument_digest": f"arg-{index}",
            "result_digest": f"result-{index}",
        },
        request_id=f"request-{index}",
        actor_id="director",
        session_id="session-1",
        run_id="run-1",
        step_id=f"step-{index}",
        proposal_id=f"proposal-{index}",
    )


def test_sqlite_audit_persists_identity_context_digests_and_chain(tmp_path) -> None:
    path = tmp_path / "audit.db"
    log = SQLiteAuditLog(path)
    original = event()

    log.record(original)
    stored = SQLiteAuditLog(path).latest()

    assert stored is not None
    assert stored.sequence == 1
    assert stored.event_id == original.event_id
    assert stored.request_id == "request-1"
    assert stored.actor_id == "director"
    assert stored.session_id == "session-1"
    assert stored.run_id == "run-1"
    assert stored.step_id == "step-1"
    assert stored.proposal_id == "proposal-1"
    assert stored.metadata["argument_digest"] == "arg-1"
    assert stored.metadata["result_digest"] == "result-1"
    assert stored.previous_hash == "0" * 64
    assert stored.event_hash is not None and len(stored.event_hash) == 64
    assert SQLiteAuditLog(path).verify_integrity().valid is True


def test_sqlite_audit_links_events_and_supports_bounded_cursor_pages(tmp_path) -> None:
    log = SQLiteAuditLog(tmp_path / "audit.db", max_page_size=3)
    for index in range(1, 6):
        log.record(event(index))

    latest_page = log.events(limit=3)
    older_page = log.events(limit=2, before_sequence=3)
    forward_page = log.events(limit=2, after_sequence=2)

    assert [item.sequence for item in latest_page] == [3, 4, 5]
    assert [item.sequence for item in older_page] == [1, 2]
    assert [item.sequence for item in forward_page] == [3, 4]
    assert latest_page[1].previous_hash == latest_page[0].event_hash
    with pytest.raises(ValueError, match="between 1 and 3"):
        log.events(limit=4)
    with pytest.raises(ValueError, match="either"):
        log.events(limit=1, before_sequence=2, after_sequence=1)


def test_sqlite_audit_uses_wal_and_busy_timeout(tmp_path) -> None:
    path = tmp_path / "audit.db"
    log = SQLiteAuditLog(path, busy_timeout_ms=7_500)
    log.record(event())

    with sqlite3.connect(path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    with log._connect() as connection:
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]

    assert journal_mode.casefold() == "wal"
    assert busy_timeout == 7_500


def test_sqlite_audit_database_rejects_update_and_delete(tmp_path) -> None:
    path = tmp_path / "audit.db"
    log = SQLiteAuditLog(path)
    log.record(event())

    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE audit_events SET decision = 'deny' WHERE sequence = 1")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM audit_events WHERE sequence = 1")


def test_sqlite_audit_detects_payload_tampering(tmp_path) -> None:
    path = tmp_path / "audit.db"
    log = SQLiteAuditLog(path)
    log.record(event(1))
    log.record(event(2))

    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER audit_events_no_update")
        connection.execute("UPDATE audit_events SET decision = 'deny' WHERE sequence = 1")

    result = log.verify_integrity()
    assert result.valid is False
    assert result.error_sequence == 1
    assert result.reason == "Audit event hash does not match its stored payload."


def test_sqlite_audit_serializes_concurrent_writers(tmp_path) -> None:
    path = tmp_path / "audit.db"
    log = SQLiteAuditLog(path)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda index: log.record(event(index)), range(1, 33)))

    stored = log.events(limit=100)
    assert log.count() == 32
    assert [item.sequence for item in stored] == list(range(1, 33))
    assert log.verify_integrity().valid is True
