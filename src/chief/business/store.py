from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from chief.business.schema import (
    BUSINESS_NODE_ADAPTER,
    BusinessNode,
    BusinessNodeKind,
    BusinessRelationship,
    BusinessTraversal,
    RelationshipKind,
    TraversalDirection,
)


class BusinessGraphError(RuntimeError):
    """Base error for business graph persistence and traversal."""


class BusinessGraphConflict(BusinessGraphError):
    """A node or relationship violates a graph uniqueness constraint."""


class BusinessGraphScopeError(BusinessGraphError):
    """A relationship attempts to cross an ownership boundary."""


class SQLiteBusinessGraphStore:
    """Tenant-scoped, temporal business graph backed by SQLite.

    Public methods expose only typed filters; callers cannot provide SQL,
    column names, or sort expressions. Validity windows are half-open:
    ``valid_from <= as_of < valid_to`` (or indefinitely when ``valid_to`` is
    null). Traversal is breadth-first and constrained by both caller limits
    and hard store-level budgets.
    """

    def __init__(
        self,
        database_path: str | Path = "data/chief.db",
        *,
        busy_timeout_ms: int = 5_000,
        max_json_bytes: int = 1_000_000,
        max_page_size: int = 1_000,
        max_traversal_depth: int = 8,
        max_traversal_nodes: int = 1_000,
        max_traversal_edges: int = 5_000,
    ) -> None:
        if busy_timeout_ms < 1:
            raise ValueError("Business graph busy timeout must be positive.")
        if max_json_bytes < 2:
            raise ValueError("Business graph JSON limit is too small.")
        if max_page_size < 1:
            raise ValueError("Business graph maximum page size must be positive.")
        if max_traversal_depth < 1:
            raise ValueError("Business graph maximum traversal depth must be positive.")
        if max_traversal_nodes < 1:
            raise ValueError("Business graph maximum traversal node count must be positive.")
        if max_traversal_edges < 1:
            raise ValueError("Business graph maximum traversal edge count must be positive.")

        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_ms = busy_timeout_ms
        self.max_json_bytes = max_json_bytes
        self.max_page_size = max_page_size
        self.max_traversal_depth = max_traversal_depth
        self.max_traversal_nodes = max_traversal_nodes
        self.max_traversal_edges = max_traversal_edges
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_ms / 1_000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS chief_component_migrations (
                    component TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    applied_at TEXT NOT NULL,
                    PRIMARY KEY (component, version)
                );

                CREATE TABLE IF NOT EXISTS business_nodes (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    node_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    sensitivity TEXT NOT NULL,
                    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (owner_id, kind, node_key),
                    UNIQUE (id, owner_id),
                    CHECK (valid_to IS NULL OR valid_to > valid_from)
                );

                CREATE TABLE IF NOT EXISTS business_edges (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    label TEXT,
                    payload_json TEXT NOT NULL,
                    sensitivity TEXT NOT NULL,
                    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (owner_id, source_id, target_id, kind, valid_from),
                    FOREIGN KEY (source_id, owner_id)
                        REFERENCES business_nodes(id, owner_id) ON DELETE RESTRICT,
                    FOREIGN KEY (target_id, owner_id)
                        REFERENCES business_nodes(id, owner_id) ON DELETE RESTRICT,
                    CHECK (source_id <> target_id),
                    CHECK (valid_to IS NULL OR valid_to > valid_from)
                );

                CREATE INDEX IF NOT EXISTS ix_business_nodes_owner_kind_validity
                    ON business_nodes(owner_id, kind, valid_from, valid_to);
                CREATE INDEX IF NOT EXISTS ix_business_nodes_owner_name
                    ON business_nodes(owner_id, name, id);
                CREATE INDEX IF NOT EXISTS ix_business_edges_source_validity
                    ON business_edges(owner_id, source_id, valid_from, valid_to);
                CREATE INDEX IF NOT EXISTS ix_business_edges_target_validity
                    ON business_edges(owner_id, target_id, valid_from, valid_to);
                CREATE INDEX IF NOT EXISTS ix_business_edges_kind_validity
                    ON business_edges(owner_id, kind, valid_from, valid_to);
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO chief_component_migrations(component, version, applied_at)
                VALUES ('business_graph', 1, ?)
                """,
                (self._iso(self._now()),),
            )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @classmethod
    def _iso(cls, value: datetime) -> str:
        return cls._utc(value).isoformat()

    @staticmethod
    def _validate_owner(owner_id: str) -> str:
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("Business graph owner must be a non-empty string.")
        owner_id = owner_id.strip()
        if len(owner_id) > 256:
            raise ValueError("Business graph owner cannot exceed 256 characters.")
        return owner_id

    def _json(self, value: dict[str, Any]) -> str:
        try:
            encoded = json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Business graph data must contain only finite JSON values.") from exc
        if len(encoded.encode("utf-8")) > self.max_json_bytes:
            raise ValueError("Business graph data exceeds the configured JSON size limit.")
        return encoded

    @staticmethod
    def _validate_page_limit(limit: int, maximum: int) -> int:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= maximum:
            raise ValueError(f"Business graph page limit must be between 1 and {maximum}.")
        return limit

    @staticmethod
    def _node_kinds(kinds: Sequence[BusinessNodeKind] | None) -> list[str]:
        if kinds is None:
            return []
        if not kinds:
            raise ValueError("Node kind filters cannot be empty.")
        if any(not isinstance(kind, BusinessNodeKind) for kind in kinds):
            raise TypeError("Node kind filters must contain BusinessNodeKind values.")
        return list(dict.fromkeys(kind.value for kind in kinds))

    @staticmethod
    def _relationship_kinds(kinds: Sequence[RelationshipKind] | None) -> list[str]:
        if kinds is None:
            return []
        if not kinds:
            raise ValueError("Relationship kind filters cannot be empty.")
        if any(not isinstance(kind, RelationshipKind) for kind in kinds):
            raise TypeError("Relationship kind filters must contain RelationshipKind values.")
        return list(dict.fromkeys(kind.value for kind in kinds))

    @staticmethod
    def _direction(direction: TraversalDirection) -> TraversalDirection:
        if not isinstance(direction, TraversalDirection):
            raise TypeError("Traversal direction must be a TraversalDirection value.")
        return direction

    @staticmethod
    def _active_sql(alias: str) -> str:
        # ``alias`` is always one of the fixed internal aliases below; it never
        # originates from a caller.
        return f"{alias}.valid_from <= ? AND ({alias}.valid_to IS NULL OR {alias}.valid_to > ?)"

    @classmethod
    def _active_parameters(cls, as_of: datetime, *, aliases: int = 1) -> list[str]:
        timestamp = cls._iso(as_of)
        return [timestamp, timestamp] * aliases

    @staticmethod
    def _node(row: sqlite3.Row) -> BusinessNode:
        return BUSINESS_NODE_ADAPTER.validate_json(row["payload_json"])

    @staticmethod
    def _relationship(row: sqlite3.Row) -> BusinessRelationship:
        return BusinessRelationship.model_validate_json(row["payload_json"])

    def add_node(self, node: BusinessNode) -> BusinessNode:
        """Insert one typed node, enforcing owner/kind/key semantic uniqueness."""

        validated = BUSINESS_NODE_ADAPTER.validate_python(node)
        payload = self._json(validated.model_dump(mode="json"))
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO business_nodes (
                        id, owner_id, kind, node_key, name, payload_json,
                        sensitivity, confidence, valid_from, valid_to,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(validated.id),
                        validated.owner_id,
                        validated.kind.value,
                        validated.key,
                        validated.name,
                        payload,
                        validated.sensitivity.value,
                        validated.confidence,
                        self._iso(validated.valid_from),
                        self._iso(validated.valid_to) if validated.valid_to else None,
                        self._iso(validated.created_at),
                        self._iso(validated.updated_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise BusinessGraphConflict(
                "A business node with this ID or owner/kind/key already exists."
            ) from exc
        return validated

    def add_relationship(self, relationship: BusinessRelationship) -> BusinessRelationship:
        """Insert an owner-scoped edge after validating both endpoints atomically."""

        validated = BusinessRelationship.model_validate(relationship)
        payload = self._json(validated.model_dump(mode="json"))
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                endpoint_rows = connection.execute(
                    """
                    SELECT id, owner_id FROM business_nodes
                    WHERE id IN (?, ?)
                    """,
                    (str(validated.source_id), str(validated.target_id)),
                ).fetchall()
                endpoints = {str(row["id"]): str(row["owner_id"]) for row in endpoint_rows}
                expected_ids = {str(validated.source_id), str(validated.target_id)}
                if endpoints.keys() != expected_ids:
                    raise BusinessGraphScopeError(
                        "Both relationship endpoints must exist before the edge is added."
                    )
                if any(owner != validated.owner_id for owner in endpoints.values()):
                    raise BusinessGraphScopeError(
                        "Business relationships cannot cross owner boundaries."
                    )
                connection.execute(
                    """
                    INSERT INTO business_edges (
                        id, owner_id, source_id, target_id, kind, label,
                        payload_json, sensitivity, confidence, valid_from,
                        valid_to, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(validated.id),
                        validated.owner_id,
                        str(validated.source_id),
                        str(validated.target_id),
                        validated.kind.value,
                        validated.label,
                        payload,
                        validated.sensitivity.value,
                        validated.confidence,
                        self._iso(validated.valid_from),
                        self._iso(validated.valid_to) if validated.valid_to else None,
                        self._iso(validated.created_at),
                        self._iso(validated.updated_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise BusinessGraphConflict(
                "A business relationship with this ID or semantic identity already exists."
            ) from exc
        return validated

    def get_node(
        self,
        node_id: UUID,
        *,
        owner_id: str,
        as_of: datetime | None = None,
    ) -> BusinessNode | None:
        owner_id = self._validate_owner(owner_id)
        effective_at = as_of or self._now()
        query = f"""
            SELECT * FROM business_nodes n
            WHERE n.id = ? AND n.owner_id = ? AND {self._active_sql("n")}
        """
        parameters: list[object] = [str(node_id), owner_id]
        parameters.extend(self._active_parameters(effective_at))
        with self._connect() as connection:
            row = connection.execute(query, parameters).fetchone()
        return self._node(row) if row is not None else None

    def find_node(
        self,
        *,
        owner_id: str,
        kind: BusinessNodeKind,
        key: str,
        as_of: datetime | None = None,
    ) -> BusinessNode | None:
        owner_id = self._validate_owner(owner_id)
        if not isinstance(kind, BusinessNodeKind):
            raise TypeError("Node kind must be a BusinessNodeKind value.")
        if not isinstance(key, str) or not key.strip() or len(key.strip()) > 256:
            raise ValueError("Business node key must contain between 1 and 256 characters.")
        effective_at = as_of or self._now()
        query = f"""
            SELECT * FROM business_nodes n
            WHERE n.owner_id = ? AND n.kind = ? AND n.node_key = ?
              AND {self._active_sql("n")}
        """
        parameters: list[object] = [owner_id, kind.value, key.strip()]
        parameters.extend(self._active_parameters(effective_at))
        with self._connect() as connection:
            row = connection.execute(query, parameters).fetchone()
        return self._node(row) if row is not None else None

    def list_nodes(
        self,
        *,
        owner_id: str,
        kinds: Sequence[BusinessNodeKind] | None = None,
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> list[BusinessNode]:
        owner_id = self._validate_owner(owner_id)
        limit = self._validate_page_limit(limit, self.max_page_size)
        kind_values = self._node_kinds(kinds)
        effective_at = as_of or self._now()
        query = f"""
            SELECT * FROM business_nodes n
            WHERE n.owner_id = ? AND {self._active_sql("n")}
        """
        parameters: list[object] = [owner_id]
        parameters.extend(self._active_parameters(effective_at))
        if kind_values:
            placeholders = ",".join("?" for _ in kind_values)
            query += f" AND n.kind IN ({placeholders})"
            parameters.extend(kind_values)
        query += " ORDER BY n.kind, n.node_key, n.id LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._node(row) for row in rows]

    def list_relationships(
        self,
        *,
        owner_id: str,
        node_id: UUID | None = None,
        kinds: Sequence[RelationshipKind] | None = None,
        direction: TraversalDirection = TraversalDirection.BOTH,
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> list[BusinessRelationship]:
        owner_id = self._validate_owner(owner_id)
        direction = self._direction(direction)
        limit = self._validate_page_limit(limit, self.max_page_size)
        kind_values = self._relationship_kinds(kinds)
        if node_id is None and direction is not TraversalDirection.BOTH:
            raise ValueError("Relationship direction requires a node_id filter.")

        effective_at = as_of or self._now()
        query = f"""
            SELECT e.* FROM business_edges e
            JOIN business_nodes s ON s.id = e.source_id AND s.owner_id = e.owner_id
            JOIN business_nodes t ON t.id = e.target_id AND t.owner_id = e.owner_id
            WHERE e.owner_id = ?
              AND {self._active_sql("e")}
              AND {self._active_sql("s")}
              AND {self._active_sql("t")}
        """
        parameters: list[object] = [owner_id]
        parameters.extend(self._active_parameters(effective_at, aliases=3))
        if node_id is not None:
            node = str(node_id)
            if direction is TraversalDirection.OUTBOUND:
                query += " AND e.source_id = ?"
                parameters.append(node)
            elif direction is TraversalDirection.INBOUND:
                query += " AND e.target_id = ?"
                parameters.append(node)
            else:
                query += " AND (e.source_id = ? OR e.target_id = ?)"
                parameters.extend((node, node))
        if kind_values:
            placeholders = ",".join("?" for _ in kind_values)
            query += f" AND e.kind IN ({placeholders})"
            parameters.extend(kind_values)
        query += " ORDER BY e.created_at, e.id LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._relationship(row) for row in rows]

    def traverse(
        self,
        *,
        owner_id: str,
        start_id: UUID,
        direction: TraversalDirection = TraversalDirection.BOTH,
        relationship_kinds: Sequence[RelationshipKind] | None = None,
        as_of: datetime | None = None,
        max_depth: int | None = None,
        max_nodes: int | None = None,
        max_edges: int | None = None,
    ) -> BusinessTraversal:
        """Return a bounded breadth-first traversal from an active start node."""

        owner_id = self._validate_owner(owner_id)
        direction = self._direction(direction)
        kind_values = self._relationship_kinds(relationship_kinds)
        max_depth = min(3, self.max_traversal_depth) if max_depth is None else max_depth
        max_nodes = min(100, self.max_traversal_nodes) if max_nodes is None else max_nodes
        max_edges = min(500, self.max_traversal_edges) if max_edges is None else max_edges
        if (
            not isinstance(max_depth, int)
            or isinstance(max_depth, bool)
            or not 0 <= max_depth <= self.max_traversal_depth
        ):
            raise ValueError(f"Traversal depth must be between 0 and {self.max_traversal_depth}.")
        if (
            not isinstance(max_nodes, int)
            or isinstance(max_nodes, bool)
            or not 1 <= max_nodes <= self.max_traversal_nodes
        ):
            raise ValueError(
                f"Traversal node count must be between 1 and {self.max_traversal_nodes}."
            )
        if (
            not isinstance(max_edges, int)
            or isinstance(max_edges, bool)
            or not 1 <= max_edges <= self.max_traversal_edges
        ):
            raise ValueError(
                f"Traversal edge count must be between 1 and {self.max_traversal_edges}."
            )

        effective_at = self._utc(as_of or self._now())
        start = self.get_node(start_id, owner_id=owner_id, as_of=effective_at)
        if start is None:
            raise BusinessGraphScopeError(
                "The traversal start node does not exist, is inactive, or belongs to another owner."
            )

        nodes: dict[str, BusinessNode] = {str(start.id): start}
        relationships: dict[str, BusinessRelationship] = {}
        frontier = [str(start.id)]
        depth_reached = 0
        truncated = False

        with self._connect() as connection:
            while frontier and depth_reached < max_depth:
                remaining_edges = max_edges - len(relationships)
                if remaining_edges <= 0:
                    truncated = True
                    break
                rows = self._traversal_rows(
                    connection,
                    owner_id=owner_id,
                    frontier=frontier,
                    direction=direction,
                    kind_values=kind_values,
                    as_of=effective_at,
                    limit=remaining_edges + len(relationships) + 1,
                )
                next_frontier: list[str] = []
                frontier_set = set(frontier)
                unseen_rows = [row for row in rows if str(row["id"]) not in relationships]
                if len(unseen_rows) > remaining_edges:
                    truncated = True
                    unseen_rows = unseen_rows[:remaining_edges]

                for row in unseen_rows:
                    source_id = str(row["source_id"])
                    target_id = str(row["target_id"])
                    neighbor_ids: list[str] = []
                    if direction is not TraversalDirection.INBOUND and source_id in frontier_set:
                        neighbor_ids.append(target_id)
                    if direction is not TraversalDirection.OUTBOUND and target_id in frontier_set:
                        neighbor_ids.append(source_id)
                    if not neighbor_ids:
                        continue

                    additions: list[tuple[str, BusinessNode]] = []
                    for neighbor_id in dict.fromkeys(neighbor_ids):
                        if neighbor_id in nodes:
                            continue
                        payload_column = (
                            "source_payload_json"
                            if neighbor_id == source_id
                            else "target_payload_json"
                        )
                        neighbor = BUSINESS_NODE_ADAPTER.validate_json(row[payload_column])
                        additions.append((neighbor_id, neighbor))

                    if len(nodes) + len(additions) > max_nodes:
                        truncated = True
                        continue

                    relationship = self._relationship(row)
                    relationships[str(relationship.id)] = relationship
                    for neighbor_id, neighbor in additions:
                        nodes[neighbor_id] = neighbor
                        next_frontier.append(neighbor_id)

                if not next_frontier:
                    break
                depth_reached += 1
                frontier = list(dict.fromkeys(next_frontier))

        if frontier and depth_reached >= max_depth:
            truncated = True
        return BusinessTraversal(
            start_id=start.id,
            direction=direction,
            as_of=effective_at,
            nodes=list(nodes.values()),
            relationships=list(relationships.values()),
            depth_reached=depth_reached,
            truncated=truncated,
        )

    def _traversal_rows(
        self,
        connection: sqlite3.Connection,
        *,
        owner_id: str,
        frontier: list[str],
        direction: TraversalDirection,
        kind_values: list[str],
        as_of: datetime,
        limit: int,
    ) -> list[sqlite3.Row]:
        placeholders = ",".join("?" for _ in frontier)
        query = f"""
            SELECT e.*, s.payload_json AS source_payload_json,
                   t.payload_json AS target_payload_json
            FROM business_edges e
            JOIN business_nodes s ON s.id = e.source_id AND s.owner_id = e.owner_id
            JOIN business_nodes t ON t.id = e.target_id AND t.owner_id = e.owner_id
            WHERE e.owner_id = ?
              AND {self._active_sql("e")}
              AND {self._active_sql("s")}
              AND {self._active_sql("t")}
        """
        parameters: list[object] = [owner_id]
        parameters.extend(self._active_parameters(as_of, aliases=3))
        if direction is TraversalDirection.OUTBOUND:
            query += f" AND e.source_id IN ({placeholders})"
            parameters.extend(frontier)
        elif direction is TraversalDirection.INBOUND:
            query += f" AND e.target_id IN ({placeholders})"
            parameters.extend(frontier)
        else:
            query += f" AND (e.source_id IN ({placeholders}) OR e.target_id IN ({placeholders}))"
            parameters.extend(frontier)
            parameters.extend(frontier)
        if kind_values:
            kind_placeholders = ",".join("?" for _ in kind_values)
            query += f" AND e.kind IN ({kind_placeholders})"
            parameters.extend(kind_values)
        query += " ORDER BY e.created_at, e.id LIMIT ?"
        parameters.append(limit)
        return connection.execute(query, parameters).fetchall()
