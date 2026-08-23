import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from chief.business import (
    BusinessGraphConflict,
    BusinessGraphScopeError,
    BusinessNodeKind,
    BusinessRelationship,
    Competitor,
    Customer,
    Document,
    Opportunity,
    Organization,
    Person,
    Product,
    Project,
    Provenance,
    ProvenanceType,
    RelationshipKind,
    Risk,
    RiskSeverity,
    Sensitivity,
    SQLiteBusinessGraphStore,
    TraversalDirection,
)

AS_OF = datetime(2026, 8, 23, 12, tzinfo=UTC)


def provenance(source_id: str = "strategy-brief") -> Provenance:
    return Provenance(
        source_type=ProvenanceType.DOCUMENT,
        source_id=source_id,
        source_uri="drive://strategy/brief",
        captured_at=AS_OF,
        evidence_digest="a" * 64,
    )


def organization(
    key: str,
    *,
    owner_id: str = "tenant-a",
    name: str | None = None,
    valid_from: datetime = AS_OF - timedelta(days=1),
    valid_to: datetime | None = None,
) -> Organization:
    return Organization(
        key=key,
        name=name or key.title(),
        owner_id=owner_id,
        provenance=provenance(),
        valid_from=valid_from,
        valid_to=valid_to,
        created_at=valid_from,
        updated_at=valid_from,
    )


def relationship(
    source_id,
    target_id,
    kind: RelationshipKind,
    *,
    owner_id: str = "tenant-a",
    valid_from: datetime = AS_OF - timedelta(hours=1),
    valid_to: datetime | None = None,
) -> BusinessRelationship:
    return BusinessRelationship(
        source_id=source_id,
        target_id=target_id,
        kind=kind,
        owner_id=owner_id,
        provenance=provenance("crm-sync-42"),
        sensitivity=Sensitivity.CONFIDENTIAL,
        confidence=0.87,
        valid_from=valid_from,
        valid_to=valid_to,
        created_at=valid_from,
        updated_at=valid_from,
    )


def test_round_trips_all_typed_entities_and_metadata(tmp_path) -> None:
    path = tmp_path / "business.db"
    store = SQLiteBusinessGraphStore(path)
    common = {
        "owner_id": "tenant-a",
        "provenance": provenance(),
        "sensitivity": Sensitivity.RESTRICTED,
        "confidence": 0.91,
        "valid_from": AS_OF - timedelta(days=1),
        "created_at": AS_OF - timedelta(days=1),
        "updated_at": AS_OF,
    }
    nodes = [
        Organization(key="chief", name="Chief Labs", industry="AI", **common),
        Person(key="founder", name="Daria", role="Founder", **common),
        Product(key="cognitive-hub", name="Cognitive Hub", lifecycle_stage="beta", **common),
        Customer(key="acme", name="Acme", segment="enterprise", status="active", **common),
        Competitor(
            key="incumbent",
            name="Incumbent",
            strengths=["distribution"],
            weaknesses=["speed"],
            **common,
        ),
        Project(key="launch", name="Launch", status="green", target_date=AS_OF, **common),
        Opportunity(
            key="series-a",
            name="Series A",
            stage="diligence",
            projected_value=5_000_000,
            currency="USD",
            probability=0.65,
            **common,
        ),
        Risk(
            key="runway",
            name="Runway",
            severity=RiskSeverity.HIGH,
            likelihood=0.4,
            mitigation="Close the round.",
            **common,
        ),
        Document(
            key="board-pack",
            name="Board Pack",
            uri="drive://board/pack",
            document_type="application/pdf",
            content_digest="b" * 64,
            **common,
        ),
    ]

    for node in nodes:
        assert store.add_node(node) == node

    loaded = store.list_nodes(owner_id="tenant-a", as_of=AS_OF)
    assert {type(node) for node in loaded} == {type(node) for node in nodes}
    assert {node.kind for node in loaded} == set(BusinessNodeKind)
    product = store.find_node(
        owner_id="tenant-a",
        kind=BusinessNodeKind.PRODUCT,
        key="cognitive-hub",
        as_of=AS_OF,
    )
    assert isinstance(product, Product)
    assert product.provenance.evidence_digest == "a" * 64
    assert product.sensitivity == Sensitivity.RESTRICTED
    assert product.confidence == 0.91
    assert store.get_node(product.id, owner_id="other", as_of=AS_OF) is None
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].casefold() == "wal"
        migration = connection.execute(
            "SELECT version FROM chief_component_migrations WHERE component = 'business_graph'"
        ).fetchone()
        assert migration == (1,)


def test_semantic_uniqueness_and_owner_scoped_endpoints(tmp_path) -> None:
    store = SQLiteBusinessGraphStore(tmp_path / "business.db")
    chief = store.add_node(organization("chief"))
    product = store.add_node(organization("product"))

    with pytest.raises(BusinessGraphConflict):
        store.add_node(organization("chief", name="Duplicate semantic key"))

    edge = relationship(chief.id, product.id, RelationshipKind.PRODUCES)
    store.add_relationship(edge)
    with pytest.raises(BusinessGraphConflict):
        store.add_relationship(relationship(chief.id, product.id, RelationshipKind.PRODUCES))

    foreign_node = store.add_node(organization("foreign", owner_id="tenant-b"))
    with pytest.raises(BusinessGraphScopeError):
        store.add_relationship(relationship(chief.id, foreign_node.id, RelationshipKind.RELATED_TO))
    with pytest.raises(BusinessGraphScopeError):
        store.add_relationship(relationship(chief.id, uuid4(), RelationshipKind.RELATED_TO))


def test_temporal_filters_are_half_open_for_nodes_and_relationships(tmp_path) -> None:
    store = SQLiteBusinessGraphStore(tmp_path / "business.db")
    start = AS_OF - timedelta(days=10)
    boundary = AS_OF
    old = store.add_node(organization("old", valid_from=start, valid_to=boundary))
    current = store.add_node(
        organization(
            "current",
            valid_from=boundary,
            valid_to=boundary + timedelta(days=10),
        )
    )
    future = store.add_node(organization("future", valid_from=boundary + timedelta(days=1)))

    at_boundary = store.list_nodes(owner_id="tenant-a", as_of=boundary)
    assert {node.key for node in at_boundary} == {"current"}
    assert store.get_node(old.id, owner_id="tenant-a", as_of=boundary) is None
    assert store.get_node(current.id, owner_id="tenant-a", as_of=boundary) == current
    assert store.get_node(future.id, owner_id="tenant-a", as_of=boundary) is None

    old_edge = relationship(
        old.id,
        current.id,
        RelationshipKind.RELATED_TO,
        valid_from=start,
        valid_to=boundary,
    )
    # The edge can be persisted even though the endpoint windows do not overlap;
    # reads require the edge and both endpoints to be active at the same instant.
    store.add_relationship(old_edge)
    assert store.list_relationships(owner_id="tenant-a", as_of=boundary) == []
    before_boundary = store.list_relationships(
        owner_id="tenant-a", as_of=boundary - timedelta(microseconds=1)
    )
    assert before_boundary == []  # the target is not active yet


def test_relationship_round_trip_and_typed_direction_filters(tmp_path) -> None:
    store = SQLiteBusinessGraphStore(tmp_path / "business.db")
    parent = store.add_node(organization("parent"))
    child = store.add_node(organization("child"))
    edge = store.add_relationship(relationship(parent.id, child.id, RelationshipKind.OWNS))

    outbound = store.list_relationships(
        owner_id="tenant-a",
        node_id=parent.id,
        direction=TraversalDirection.OUTBOUND,
        kinds=[RelationshipKind.OWNS],
        as_of=AS_OF,
    )
    inbound = store.list_relationships(
        owner_id="tenant-a",
        node_id=child.id,
        direction=TraversalDirection.INBOUND,
        as_of=AS_OF,
    )
    assert outbound == [edge]
    assert inbound == [edge]
    assert inbound[0].provenance.source_id == "crm-sync-42"
    assert inbound[0].sensitivity == Sensitivity.CONFIDENTIAL
    assert inbound[0].confidence == 0.87
    with pytest.raises(ValueError):
        store.list_relationships(
            owner_id="tenant-a",
            direction=TraversalDirection.OUTBOUND,
            as_of=AS_OF,
        )


def test_traversal_is_directional_temporal_and_bounded(tmp_path) -> None:
    store = SQLiteBusinessGraphStore(
        tmp_path / "business.db",
        max_traversal_depth=4,
        max_traversal_nodes=10,
        max_traversal_edges=20,
    )
    chief = store.add_node(organization("chief"))
    product = store.add_node(organization("product"))
    project = store.add_node(organization("project"))
    competitor = store.add_node(organization("competitor"))
    inactive = store.add_node(organization("inactive", valid_to=AS_OF - timedelta(seconds=1)))
    store.add_relationship(relationship(chief.id, product.id, RelationshipKind.PRODUCES))
    store.add_relationship(relationship(product.id, project.id, RelationshipKind.DEPENDS_ON))
    store.add_relationship(relationship(chief.id, competitor.id, RelationshipKind.COMPETES_WITH))
    store.add_relationship(relationship(chief.id, inactive.id, RelationshipKind.RELATED_TO))

    first_hop = store.traverse(
        owner_id="tenant-a",
        start_id=chief.id,
        direction=TraversalDirection.OUTBOUND,
        as_of=AS_OF,
        max_depth=1,
    )
    assert {node.key for node in first_hop.nodes} == {"chief", "product", "competitor"}
    assert {edge.kind for edge in first_hop.relationships} == {
        RelationshipKind.PRODUCES,
        RelationshipKind.COMPETES_WITH,
    }
    assert first_hop.depth_reached == 1
    assert first_hop.truncated is True

    full = store.traverse(
        owner_id="tenant-a",
        start_id=chief.id,
        direction=TraversalDirection.OUTBOUND,
        as_of=AS_OF,
        max_depth=4,
    )
    assert {node.key for node in full.nodes} == {"chief", "product", "project", "competitor"}
    assert full.depth_reached == 2
    assert full.truncated is False

    inbound = store.traverse(
        owner_id="tenant-a",
        start_id=project.id,
        direction=TraversalDirection.INBOUND,
        as_of=AS_OF,
        max_depth=4,
    )
    assert {node.key for node in inbound.nodes} == {"chief", "product", "project"}

    capped = store.traverse(
        owner_id="tenant-a",
        start_id=chief.id,
        direction=TraversalDirection.OUTBOUND,
        as_of=AS_OF,
        max_depth=4,
        max_nodes=2,
    )
    assert len(capped.nodes) == 2
    assert capped.truncated is True
    assert all(
        edge.source_id in {node.id for node in capped.nodes}
        and edge.target_id in {node.id for node in capped.nodes}
        for edge in capped.relationships
    )

    produces_only = store.traverse(
        owner_id="tenant-a",
        start_id=chief.id,
        relationship_kinds=[RelationshipKind.PRODUCES],
        direction=TraversalDirection.OUTBOUND,
        as_of=AS_OF,
        max_depth=4,
    )
    assert {node.key for node in produces_only.nodes} == {"chief", "product"}

    with pytest.raises(ValueError):
        store.traverse(owner_id="tenant-a", start_id=chief.id, max_depth=5)
    with pytest.raises(BusinessGraphScopeError):
        store.traverse(owner_id="tenant-b", start_id=chief.id, as_of=AS_OF)


def test_query_inputs_are_parameterized_and_filter_types_are_closed(tmp_path) -> None:
    store = SQLiteBusinessGraphStore(tmp_path / "business.db")
    malicious_owner = "tenant'; DROP TABLE business_edges;--"
    malicious_key = "roadmap'); DROP TABLE business_nodes;--"
    node = store.add_node(
        organization(malicious_key, owner_id=malicious_owner, name="Literal SQL-shaped text")
    )

    loaded = store.find_node(
        owner_id=malicious_owner,
        kind=BusinessNodeKind.ORGANIZATION,
        key=malicious_key,
        as_of=AS_OF,
    )
    assert loaded == node
    store.add_node(organization("still-present"))
    with pytest.raises(TypeError):
        store.list_nodes(owner_id="tenant-a", kinds=["organization"])  # type: ignore[list-item]
    with pytest.raises(TypeError):
        store.list_relationships(
            owner_id="tenant-a",
            kinds=["owns) OR 1=1 --"],  # type: ignore[list-item]
        )
    assert not hasattr(store, "execute_sql")


def test_schema_rejects_invalid_confidence_windows_and_self_loops() -> None:
    with pytest.raises(ValidationError):
        Organization(
            key="bad-confidence",
            name="Bad",
            owner_id="tenant-a",
            provenance=provenance(),
            confidence=1.1,
        )
    with pytest.raises(ValidationError):
        organization(
            "bad-window",
            valid_from=AS_OF,
            valid_to=AS_OF,
        )
    node_id = uuid4()
    with pytest.raises(ValidationError):
        relationship(node_id, node_id, RelationshipKind.RELATED_TO)
