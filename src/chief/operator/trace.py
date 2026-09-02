from __future__ import annotations

from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from chief.audit.sqlite import SQLiteAuditLog
from chief.core.sqlite_session_store import SQLiteSessionStore
from chief.runs import SQLiteRunStore


class TraceAuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    timestamp: str
    tool_name: str
    decision: str
    success: bool
    request_id: str | None
    actor_id: str | None
    session_id: str | None
    run_id: str | None
    step_id: str | None
    proposal_id: str | None
    event_type: str | None
    entity_id: str | None


class OperatorTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    actor_id: str
    request_id: str | None
    session_id: str | None
    proposal_id: str | None
    run_id: str | None
    approval_decisions: tuple[dict[str, str | None], ...]
    run: dict[str, object] | None
    steps: tuple[dict[str, object], ...]
    audit_events: tuple[TraceAuditEvent, ...]
    correlation_gaps: tuple[str, ...]


class OperatorTraceService:
    """Join CHIEF's durable correlation identifiers without copying sensitive payloads."""

    def __init__(self, database_path: str | Path = "data/chief.db") -> None:
        self.sessions = SQLiteSessionStore(database_path)
        self.runs = SQLiteRunStore(database_path)
        self.audit = SQLiteAuditLog(database_path)

    @staticmethod
    def _audit_view(event) -> TraceAuditEvent:
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        return TraceAuditEvent(
            event_id=event.event_id,
            timestamp=event.timestamp.isoformat(),
            tool_name=event.tool_name,
            decision=event.decision,
            success=event.success,
            request_id=event.request_id,
            actor_id=event.actor_id,
            session_id=event.session_id,
            run_id=event.run_id,
            step_id=event.step_id,
            proposal_id=event.proposal_id,
            event_type=str(metadata.get("event_type")) if metadata.get("event_type") else None,
            entity_id=str(metadata.get("entity_id")) if metadata.get("entity_id") else None,
        )

    def build(
        self,
        *,
        actor_id: str,
        request_id: str | None = None,
        session_id: UUID | None = None,
        proposal_id: UUID | None = None,
        run_id: UUID | None = None,
        audit_limit: int = 1_000,
    ) -> OperatorTrace:
        if not actor_id.strip():
            raise ValueError("actor_id must be non-empty")
        if not 1 <= audit_limit <= 1_000:
            raise ValueError("audit_limit must be between 1 and 1000")
        session_text = str(session_id) if session_id else None
        proposal_text = str(proposal_id) if proposal_id else None
        run_text = str(run_id) if run_id else None

        audit_events = []
        for event in self.audit.events(limit=audit_limit):
            if event.actor_id not in {None, actor_id}:
                continue
            selectors = [
                request_id is not None and event.request_id == request_id,
                session_text is not None and event.session_id == session_text,
                proposal_text is not None and event.proposal_id == proposal_text,
                run_text is not None and event.run_id == run_text,
            ]
            if any(selectors) or not any(
                value is not None for value in (request_id, session_text, proposal_text, run_text)
            ):
                audit_events.append(self._audit_view(event))

        decisions = self.sessions.proposal_decisions(actor_id, limit=1_000)
        if session_text is not None:
            decisions = [item for item in decisions if item["session_id"] == session_text]
        if proposal_text is not None:
            decisions = [item for item in decisions if item["proposal_id"] == proposal_text]

        run_payload = None
        step_payloads: list[dict[str, object]] = []
        if run_id is not None:
            run = self.runs.get_run(run_id)
            if run is not None:
                run_payload = run.model_dump(mode="json")
                for step in self.runs.list_steps(run_id):
                    attempts = self.runs.list_attempts(step.id)
                    step_payloads.append(
                        {
                            "step": step.model_dump(mode="json"),
                            "attempts": [attempt.model_dump(mode="json") for attempt in attempts],
                        }
                    )

        gaps: list[str] = []
        if proposal_id is not None and not decisions:
            gaps.append("No owner-scoped approval decision is linked to this proposal ID.")
        if run_id is not None and run_payload is None:
            gaps.append("No durable run exists for this run ID.")
        if not audit_events:
            gaps.append("No audit events match the supplied correlation identifiers.")
        if run_id is not None and not any(item.run_id == run_text for item in audit_events):
            gaps.append("The durable run exists but no audit event is correlated to its run ID.")

        return OperatorTrace(
            actor_id=actor_id,
            request_id=request_id,
            session_id=session_text,
            proposal_id=proposal_text,
            run_id=run_text,
            approval_decisions=tuple(decisions),
            run=run_payload,
            steps=tuple(step_payloads),
            audit_events=tuple(audit_events),
            correlation_gaps=tuple(gaps),
        )
