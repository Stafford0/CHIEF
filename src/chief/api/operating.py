from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from chief.business import (
    BusinessGraphConflict,
    BusinessGraphScopeError,
    BusinessNode,
    BusinessNodeKind,
    BusinessRelationship,
    RelationshipKind,
    SQLiteBusinessGraphStore,
    TraversalDirection,
)
from chief.decisions import (
    DecisionRecord,
    DecisionStatus,
    SQLiteDecisionStore,
    score_decision,
)
from chief.notifications import (
    AttentionPolicy,
    IdempotencyConflict,
    Notification,
    NotificationChannel,
    NotificationPriority,
    NotificationStore,
)


class DecisionScoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weight_overrides: dict[str, float] = Field(default_factory=dict)


class NotificationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=240)
    body: str = Field(min_length=1, max_length=20_000)
    priority: NotificationPriority = NotificationPriority.NORMAL
    channels: tuple[NotificationChannel, ...] = (NotificationChannel.IN_APP,)
    idempotency_key: str = Field(min_length=1, max_length=512)
    dedup_key: str = Field(min_length=1, max_length=512)
    expires_at: datetime | None = None
    acknowledgement_required: bool = False


def _actor(request: Request) -> str:
    actor_id = getattr(request.state, "actor_id", None)
    if not isinstance(actor_id, str) or not actor_id:
        raise HTTPException(status_code=401, detail="An authenticated CHIEF actor is required.")
    return actor_id


def create_operating_router(
    *,
    decision_store: SQLiteDecisionStore,
    business_store: SQLiteBusinessGraphStore,
    notification_store: NotificationStore,
    attention_policy: AttentionPolicy,
    record_change: Callable[[Request, str, str, str], None] | None = None,
) -> APIRouter:
    """Expose the co-founder operating domains through a thin HTTP adapter."""

    router = APIRouter(tags=["operating-system"])

    def changed(request: Request, domain: str, action: str, entity_id: UUID) -> None:
        if record_change is not None:
            record_change(request, domain, action, str(entity_id))

    @router.get("/decisions", response_model=list[DecisionRecord])
    def list_decisions(
        status: DecisionStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DecisionRecord]:
        try:
            return decision_store.list(status=status, limit=limit, offset=offset)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/decisions", response_model=DecisionRecord, status_code=201)
    def save_decision(decision: DecisionRecord, request: Request) -> DecisionRecord:
        saved = decision_store.save(decision)
        changed(request, "decision", "saved", saved.id)
        return saved

    @router.get("/decisions/{decision_id}", response_model=DecisionRecord)
    def get_decision(decision_id: UUID) -> DecisionRecord:
        decision = decision_store.get(decision_id)
        if decision is None:
            raise HTTPException(status_code=404, detail="Decision not found.")
        return decision

    @router.post("/decisions/{decision_id}/score")
    def score_saved_decision(
        decision_id: UUID,
        request_body: DecisionScoreRequest,
    ) -> dict[str, Any]:
        decision = decision_store.get(decision_id)
        if decision is None:
            raise HTTPException(status_code=404, detail="Decision not found.")
        try:
            scorecard = score_decision(
                decision,
                weight_overrides=request_body.weight_overrides,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return scorecard.model_dump(mode="json")

    @router.get("/business/nodes")
    def list_business_nodes(
        request: Request,
        kind: BusinessNodeKind | None = None,
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> list[BusinessNode]:
        try:
            return business_store.list_nodes(
                owner_id=_actor(request),
                kinds=[kind] if kind is not None else None,
                as_of=as_of,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/business/nodes", status_code=201)
    def add_business_node(node: BusinessNode, request: Request) -> BusinessNode:
        # Ownership is always server-derived; a client cannot write into another scope.
        owned_node = node.model_copy(update={"owner_id": _actor(request)})
        try:
            saved = business_store.add_node(owned_node)
        except BusinessGraphConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        changed(request, "business_node", "created", saved.id)
        return saved

    @router.get("/business/nodes/{node_id}")
    def get_business_node(
        node_id: UUID,
        request: Request,
        as_of: datetime | None = None,
    ) -> BusinessNode:
        node = business_store.get_node(node_id, owner_id=_actor(request), as_of=as_of)
        if node is None:
            raise HTTPException(status_code=404, detail="Business node not found.")
        return node

    @router.post("/business/relationships", status_code=201)
    def add_business_relationship(
        relationship: BusinessRelationship,
        request: Request,
    ) -> BusinessRelationship:
        owned_relationship = relationship.model_copy(update={"owner_id": _actor(request)})
        try:
            saved = business_store.add_relationship(owned_relationship)
        except BusinessGraphConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BusinessGraphScopeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        changed(request, "business_relationship", "created", saved.id)
        return saved

    @router.get("/business/relationships")
    def list_business_relationships(
        request: Request,
        node_id: UUID | None = None,
        kind: RelationshipKind | None = None,
        direction: TraversalDirection = TraversalDirection.BOTH,
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> list[BusinessRelationship]:
        try:
            return business_store.list_relationships(
                owner_id=_actor(request),
                node_id=node_id,
                kinds=[kind] if kind is not None else None,
                direction=direction,
                as_of=as_of,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/business/traverse/{start_id}")
    def traverse_business_graph(
        start_id: UUID,
        request: Request,
        direction: TraversalDirection = TraversalDirection.BOTH,
        relationship_kinds: Annotated[list[RelationshipKind] | None, Query()] = None,
        as_of: datetime | None = None,
        max_depth: int | None = None,
        max_nodes: int | None = None,
        max_edges: int | None = None,
    ):
        try:
            return business_store.traverse(
                owner_id=_actor(request),
                start_id=start_id,
                direction=direction,
                relationship_kinds=relationship_kinds,
                as_of=as_of,
                max_depth=max_depth,
                max_nodes=max_nodes,
                max_edges=max_edges,
            )
        except BusinessGraphScopeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/notifications")
    def list_notifications(request: Request, limit: int = 100):
        try:
            return notification_store.active(
                recipient_id=_actor(request),
                now=datetime.now(UTC),
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/notifications", status_code=201)
    def create_notification(payload: NotificationCreate, request: Request):
        recipient_id = _actor(request)
        existing = notification_store.get_by_idempotency_key(payload.idempotency_key)
        if existing is not None:
            expected = {
                "recipient_id": recipient_id,
                "source": payload.source,
                "title": payload.title,
                "body": payload.body,
                "priority": payload.priority,
                "channels": payload.channels,
                "dedup_key": payload.dedup_key,
                "expires_at": payload.expires_at,
                "acknowledgement_required": payload.acknowledgement_required,
            }
            if any(getattr(existing, key) != value for key, value in expected.items()):
                raise HTTPException(
                    status_code=409,
                    detail="Notification idempotency key was reused for different content.",
                )
            decision = attention_policy.decide(existing, notification_store)
            return {"notification": existing, "attention": decision}
        try:
            notification = Notification(
                recipient_id=recipient_id,
                created_at=datetime.now(UTC),
                **payload.model_dump(),
            )
            decision = attention_policy.decide(notification, notification_store)
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        stored = notification_store.get_by_idempotency_key(payload.idempotency_key)
        assert stored is not None
        changed(request, "notification", "created", stored.id)
        return {"notification": stored, "attention": decision}

    @router.post("/notifications/{notification_id}/acknowledge")
    def acknowledge_notification(notification_id: UUID, request: Request):
        existing = notification_store.get(notification_id)
        if existing is None or existing.recipient_id != _actor(request):
            raise HTTPException(status_code=404, detail="Notification not found.")
        try:
            acknowledged = notification_store.acknowledge(notification_id, at=datetime.now(UTC))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        changed(request, "notification", "acknowledged", acknowledged.id)
        return acknowledged

    return router
