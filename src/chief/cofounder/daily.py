from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from chief.business import BusinessNodeKind, Document, Risk, SQLiteBusinessGraphStore
from chief.decisions import DecisionStatus, SQLiteDecisionStore
from chief.events.schema import EventStatus
from chief.events.store import EventStore
from chief.foresight.schema import KPIDirection
from chief.foresight.store import ForesightStore
from chief.notifications.store import NotificationStore
from chief.runs import RunStatus, SQLiteRunStore
from chief.work.briefing import build_briefing
from chief.work.store import WorkStore


class CanonicalBriefingItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    title: str
    why_now: str
    urgency: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    owner: str
    next_action: str
    freshness_seconds: int | None = Field(default=None, ge=0)
    source_refs: tuple[str, ...] = ()
    unverified: bool = False


class EvidenceConflict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_type: str
    title: str
    source_refs: tuple[str, ...]
    digests: tuple[str, ...]
    reason: str


class CanonicalBriefing(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    generated_at: datetime
    principal_id: str
    summary: str
    items: tuple[CanonicalBriefingItem, ...]
    counts: dict[str, int]
    conflicts: tuple[EvidenceConflict, ...]
    unverified: tuple[str, ...]


def _age_seconds(value: datetime | None, now: datetime) -> int | None:
    if value is None:
        return None
    normalized = value if value.tzinfo else value.replace(tzinfo=UTC)
    return max(0, int((now - normalized.astimezone(UTC)).total_seconds()))


def _kpi_off_target(kpi) -> tuple[bool, str]:
    if kpi.direction is KPIDirection.HIGHER_IS_BETTER:
        assert kpi.target_value is not None
        return kpi.current_value < kpi.target_value, f"target {kpi.target_value:g} {kpi.unit}"
    if kpi.direction is KPIDirection.LOWER_IS_BETTER:
        assert kpi.target_value is not None
        return kpi.current_value > kpi.target_value, f"target {kpi.target_value:g} {kpi.unit}"
    assert kpi.target_min is not None and kpi.target_max is not None
    outside = not kpi.target_min <= kpi.current_value <= kpi.target_max
    return outside, f"target range {kpi.target_min:g}-{kpi.target_max:g} {kpi.unit}"


def _document_source(document: Document) -> str:
    source_id = document.provenance.source_id or "unknown"
    return source_id.split(":", 1)[0] if ":" in source_id else source_id


def _evidence_conflicts(documents: list[Document]) -> list[EvidenceConflict]:
    groups: dict[tuple[str, str], list[Document]] = defaultdict(list)
    for document in documents:
        key = ((document.document_type or "unknown").casefold(), document.name.strip().casefold())
        groups[key].append(document)

    conflicts: list[EvidenceConflict] = []
    for (record_type, _), group in groups.items():
        connectors = {_document_source(item) for item in group}
        digests = {item.content_digest for item in group if item.content_digest}
        if len(connectors) < 2 or len(digests) < 2:
            continue
        conflicts.append(
            EvidenceConflict(
                record_type=record_type,
                title=group[0].name,
                source_refs=tuple(sorted(item.provenance.source_id or "unknown" for item in group)),
                digests=tuple(sorted(digests)),
                reason=(
                    "Multiple independent sources describe the same titled record with different "
                    "content digests. CHIEF must reconcile the values before making a high-confidence claim."
                ),
            )
        )
    return conflicts


def build_canonical_briefing(
    database_path: str | Path,
    *,
    principal_id: str,
    limit: int = 7,
    now: datetime | None = None,
) -> CanonicalBriefing:
    """Reconcile durable work, evidence, foresight, decisions, runs and attention into one brief."""

    if not isinstance(principal_id, str) or not principal_id.strip():
        raise ValueError("principal_id must be a non-empty string")
    if not 1 <= limit <= 20:
        raise ValueError("briefing limit must be between 1 and 20")
    principal_id = principal_id.strip()
    current = (now or datetime.now(UTC)).astimezone(UTC)

    work_store = WorkStore(database_path)
    foresight_store = ForesightStore(database_path)
    decision_store = SQLiteDecisionStore(database_path)
    business_store = SQLiteBusinessGraphStore(database_path)
    notification_store = NotificationStore(database_path)
    event_store = EventStore(database_path)
    run_store = SQLiteRunStore(database_path)

    candidates: list[CanonicalBriefingItem] = []
    unverified: list[str] = []

    work_brief = build_briefing(work_store, limit=20)
    task_by_id = {str(task.id): task for task in work_store.list_tasks(limit=1000)}
    for item in work_brief.items:
        task = task_by_id.get(str(item.task_id)) if item.task_id else None
        updated_at = task.updated_at if task is not None else None
        candidates.append(
            CanonicalBriefingItem(
                kind="task",
                title=item.title,
                why_now=item.reason,
                urgency=item.urgency,
                confidence=1.0,
                owner=principal_id,
                next_action="Advance the task or record the blocker/outcome.",
                freshness_seconds=_age_seconds(updated_at, current),
                source_refs=(f"task:{item.task_id}",) if item.task_id else (),
            )
        )

    signals = foresight_store.list_signals(limit=200)
    for signal in signals:
        urgency = min(100, 10 + signal.impact * 10 + signal.urgency * 8)
        candidates.append(
            CanonicalBriefingItem(
                kind=f"signal:{signal.kind.value}",
                title=signal.title,
                why_now=signal.summary,
                urgency=urgency,
                confidence=signal.confidence,
                owner=principal_id,
                next_action="Review the evidence and decide whether to acknowledge, act, or dismiss.",
                freshness_seconds=_age_seconds(signal.observed_at, current),
                source_refs=tuple(signal.evidence_refs),
                unverified=not bool(signal.evidence_refs),
            )
        )

    due_assumptions = foresight_store.list_assumptions_due(now=current)
    for assumption in due_assumptions:
        candidates.append(
            CanonicalBriefingItem(
                kind="assumption_review",
                title=assumption.statement[:240],
                why_now="Assumption review is due.",
                urgency=65 if assumption.confidence >= 0.5 else 75,
                confidence=assumption.confidence,
                owner=assumption.owner or principal_id,
                next_action="Validate, challenge, or invalidate the assumption with current evidence.",
                freshness_seconds=_age_seconds(assumption.updated_at, current),
                source_refs=tuple(assumption.evidence_refs),
                unverified=not bool(assumption.evidence_refs),
            )
        )

    kpis = foresight_store.list_kpis()
    for kpi in kpis:
        off_target, target_text = _kpi_off_target(kpi)
        if not off_target:
            continue
        candidates.append(
            CanonicalBriefingItem(
                kind="kpi",
                title=kpi.name,
                why_now=f"Current value {kpi.current_value:g} {kpi.unit} is outside {target_text}.",
                urgency=70,
                confidence=1.0,
                owner=kpi.owner or principal_id,
                next_action="Explain the variance and assign the next corrective action.",
                freshness_seconds=_age_seconds(kpi.observed_at, current),
                source_refs=(kpi.source_ref,),
            )
        )

    actionable_statuses = {DecisionStatus.DRAFT, DecisionStatus.IN_REVIEW, DecisionStatus.DEFERRED}
    decisions = decision_store.list(limit=200)
    for decision in decisions:
        if decision.status not in actionable_statuses:
            continue
        due = decision.due_at is not None and decision.due_at <= current
        review_due = decision.review_at is not None and decision.review_at <= current
        if not due and not review_due and decision.status is DecisionStatus.DRAFT:
            continue
        urgency = 85 if due else 70 if review_due else 55
        candidates.append(
            CanonicalBriefingItem(
                kind="decision",
                title=decision.title,
                why_now=(
                    "Decision due date has passed."
                    if due
                    else "Decision review is due."
                    if review_due
                    else f"Decision remains {decision.status.value}."
                ),
                urgency=urgency,
                confidence=decision.confidence,
                owner=decision.owner or principal_id,
                next_action="Review the evidence, assumptions and risks, then record the decision or defer it explicitly.",
                freshness_seconds=_age_seconds(decision.updated_at, current),
                source_refs=tuple(
                    ref
                    for evidence in decision.evidence
                    for ref in [evidence.provenance.source_uri or evidence.provenance.source_id]
                    if ref
                ),
                unverified=not bool(decision.evidence),
            )
        )

    risks = [
        node
        for node in business_store.list_nodes(
            owner_id=principal_id,
            kinds=[BusinessNodeKind.RISK],
            limit=1000,
        )
        if isinstance(node, Risk)
    ]
    severity_urgency = {"low": 35, "medium": 55, "high": 75, "critical": 95}
    for risk in risks:
        if risk.status and risk.status.casefold() in {"closed", "resolved", "mitigated"}:
            continue
        candidates.append(
            CanonicalBriefingItem(
                kind="risk",
                title=risk.name,
                why_now=risk.description or f"Open {risk.severity.value} business risk.",
                urgency=severity_urgency[risk.severity.value],
                confidence=risk.confidence,
                owner=principal_id,
                next_action=risk.mitigation or "Assign a mitigation owner and next verification step.",
                freshness_seconds=_age_seconds(risk.updated_at, current),
                source_refs=tuple(filter(None, [risk.provenance.source_uri, risk.provenance.source_id])),
                unverified=risk.provenance.evidence_digest is None,
            )
        )

    active_notifications = notification_store.active(
        recipient_id=principal_id,
        now=current,
        limit=200,
    )
    for notification in active_notifications:
        decision = notification_store.decision_for(notification.id)
        if decision is not None and decision.action.value not in {"interrupt", "digest"}:
            continue
        urgency = min(95, 40 + int(notification.priority) * 12)
        candidates.append(
            CanonicalBriefingItem(
                kind="notification",
                title=notification.title,
                why_now=f"Active notification from {notification.source}.",
                urgency=urgency,
                confidence=1.0,
                owner=principal_id,
                next_action="Acknowledge after reviewing the underlying event or evidence.",
                freshness_seconds=_age_seconds(notification.created_at, current),
                source_refs=(f"notification:{notification.id}",),
            )
        )

    failed_runs = run_store.list_runs(status=RunStatus.FAILED, limit=50)
    for run in failed_runs[:10]:
        candidates.append(
            CanonicalBriefingItem(
                kind="failed_run",
                title=f"Run failed: {run.correlation_id}",
                why_now=run.error_message or run.error_code or "Durable run failed without a detailed error.",
                urgency=88,
                confidence=1.0,
                owner=principal_id,
                next_action="Inspect the run trace, correct the cause, then retry with the same idempotency boundary.",
                freshness_seconds=_age_seconds(run.completed_at or run.updated_at, current),
                source_refs=(f"run:{run.id}",),
                unverified=True,
            )
        )

    dead_letters = [
        event for event in event_store.list_events(limit=1000) if event.status is EventStatus.DEAD_LETTER
    ]
    for event in dead_letters[:10]:
        candidates.append(
            CanonicalBriefingItem(
                kind="dead_letter",
                title=f"Dead-letter event: {event.event_type}",
                why_now=event.last_error or "Event exhausted its retry budget.",
                urgency=92,
                confidence=1.0,
                owner=principal_id,
                next_action="Review the error and explicitly retry or dismiss the event.",
                freshness_seconds=_age_seconds(event.updated_at, current),
                source_refs=(f"event:{event.id}",),
                unverified=True,
            )
        )

    documents = [
        node
        for node in business_store.list_nodes(
            owner_id=principal_id,
            kinds=[BusinessNodeKind.DOCUMENT],
            limit=1000,
        )
        if isinstance(node, Document) and "evidence" in node.tags
    ]
    conflicts = _evidence_conflicts(documents)
    for conflict in conflicts:
        candidates.append(
            CanonicalBriefingItem(
                kind="evidence_conflict",
                title=f"Evidence conflict: {conflict.title}",
                why_now=conflict.reason,
                urgency=82,
                confidence=0.0,
                owner=principal_id,
                next_action="Compare the conflicting sources and record which value is authoritative and why.",
                source_refs=conflict.source_refs,
                unverified=True,
            )
        )

    if not documents:
        unverified.append("No synchronized business evidence is available for this owner.")
    stale_documents = [
        item for item in documents if (_age_seconds(item.updated_at, current) or 0) > 604800
    ]
    if stale_documents:
        unverified.append(
            f"{len(stale_documents)} synchronized evidence record(s) are older than seven days."
        )
    if conflicts:
        unverified.append(f"{len(conflicts)} evidence conflict(s) require reconciliation.")

    candidates.sort(key=lambda item: (item.urgency, item.confidence), reverse=True)
    selected = tuple(candidates[:limit])
    counts = {
        "candidate_items": len(candidates),
        "open_tasks": work_brief.counts.get("open_tasks", 0),
        "blocked_tasks": work_brief.counts.get("blocked_tasks", 0),
        "signals": len(signals),
        "assumptions_due": len(due_assumptions),
        "kpis_off_target": sum(_kpi_off_target(kpi)[0] for kpi in kpis),
        "actionable_decisions": sum(decision.status in actionable_statuses for decision in decisions),
        "active_notifications": len(active_notifications),
        "failed_runs": len(failed_runs),
        "dead_letters": len(dead_letters),
        "evidence_records": len(documents),
        "evidence_conflicts": len(conflicts),
    }

    if not selected:
        summary = "No durable operating item currently requires founder attention."
    else:
        critical = sum(item.urgency >= 85 for item in selected)
        summary = (
            f"{len(selected)} prioritized item{'s' if len(selected) != 1 else ''}; "
            f"{critical} critical, {len(conflicts)} evidence conflict(s), "
            f"{len(unverified)} verification gap(s)."
        )

    return CanonicalBriefing(
        generated_at=current,
        principal_id=principal_id,
        summary=summary,
        items=selected,
        counts=counts,
        conflicts=tuple(conflicts),
        unverified=tuple(unverified),
    )
