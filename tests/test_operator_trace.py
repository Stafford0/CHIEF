from __future__ import annotations

from pathlib import Path

from chief.audit.log import AuditEvent
from chief.audit.sqlite import SQLiteAuditLog
from chief.core.sqlite_session_store import SQLiteSessionStore
from chief.core.tool_planner import PlannedToolCall
from chief.operator import OperatorTraceService


def test_operator_trace_is_actor_scoped_and_reports_correlation_gaps(tmp_path: Path) -> None:
    database = tmp_path / "chief.db"
    sessions = SQLiteSessionStore(database)
    session = sessions.create(owner_id="owner-a")
    proposal = session.propose_tool(
        PlannedToolCall(
            intent="status",
            tool_name="system_status",
            arguments={},
            description="check status",
        )
    )
    sessions.take_pending_tool(
        session.id,
        owner_id="owner-a",
        proposal_id=proposal.id,
    )
    audit = SQLiteAuditLog(database)
    audit.record(
        AuditEvent(
            tool_name="system_status",
            approved=True,
            decision="executed",
            success=True,
            request_id="request-a",
            actor_id="owner-a",
            session_id=str(session.id),
            proposal_id=str(proposal.id),
            metadata={"event_type": "tool.executed", "entity_id": "status-1"},
        )
    )
    audit.record(
        AuditEvent(
            tool_name="other",
            approved=True,
            decision="executed",
            success=True,
            request_id="request-b",
            actor_id="owner-b",
        )
    )

    trace = OperatorTraceService(database).build(
        actor_id="owner-a",
        request_id="request-a",
        session_id=session.id,
        proposal_id=proposal.id,
    )

    assert len(trace.approval_decisions) == 1
    assert len(trace.audit_events) == 1
    assert trace.audit_events[0].actor_id == "owner-a"
    assert trace.audit_events[0].entity_id == "status-1"
    assert trace.correlation_gaps == ()
