from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from chief.integrations.registry import ConnectorRegistry
from chief.integrations.schema import IdempotencyMetadata, utc_now
from chief.tools.base import Tool, ToolDefinition, ToolExecutionContext, ToolResult, ToolRisk


class ConnectorWriteTool(Tool):
    """Approval-gated bridge from CHIEF tools into exact consented connector writes."""

    def __init__(
        self,
        registry: ConnectorRegistry,
        *,
        clock: Callable = utc_now,
        idempotency_ttl_minutes: int = 15,
    ) -> None:
        if not 1 <= idempotency_ttl_minutes <= 1_440:
            raise ValueError("idempotency_ttl_minutes must be between 1 and 1,440")
        self.registry = registry
        self.clock = clock
        self.idempotency_ttl = timedelta(minutes=idempotency_ttl_minutes)

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="connector_write",
            description=(
                "Perform one exact consented external connector mutation. The authenticated "
                "principal comes from CHIEF's approval context, never from model arguments."
            ),
            risk=ToolRisk.SENSITIVE,
            requires_approval=True,
            side_effects=True,
            idempotent=True,
            timeout_seconds=30,
            input_schema={
                "type": "object",
                "properties": {
                    "connector_id": {"type": "string", "minLength": 1, "maxLength": 128},
                    "scope": {"type": "string", "minLength": 1, "maxLength": 128},
                    "payload": {"type": "object"},
                    "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 256},
                },
                "required": ["connector_id", "scope", "payload", "idempotency_key"],
                "additionalProperties": False,
            },
        )

    def validate(self, arguments: dict[str, Any]) -> None:
        super().validate(arguments)
        expected = {"connector_id", "scope", "payload", "idempotency_key"}
        if set(arguments) != expected:
            raise ValueError(
                "connector_write requires exactly connector_id, scope, payload, and idempotency_key"
            )
        for name in ("connector_id", "scope", "idempotency_key"):
            value = arguments[name]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"connector_write {name} must be a non-empty string")
        if len(arguments["idempotency_key"]) > 256:
            raise ValueError("connector_write idempotency_key cannot exceed 256 characters")
        if not isinstance(arguments["payload"], dict):
            raise TypeError("connector_write payload must be an object")

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        del arguments
        raise PermissionError("connector_write requires authenticated CHIEF execution context")

    def execute_with_context(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        actor_id = context.actor_id
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise PermissionError("connector_write requires an authenticated CHIEF actor")
        now = self.clock()
        idempotency = IdempotencyMetadata(
            key=str(arguments["idempotency_key"]).strip(),
            created_at=now,
            expires_at=now + self.idempotency_ttl,
        )
        result = self.registry.write(
            str(arguments["connector_id"]).strip(),
            str(arguments["scope"]).strip(),
            arguments["payload"],
            principal_id=actor_id,
            idempotency=idempotency,
        )
        evidence = [
            {
                "id": str(item.id),
                "connector_id": item.connector_id,
                "scope": item.scope,
                "source_record_id": item.source.record_id,
                "source_record_type": item.source.record_type,
                "content_digest": item.content_digest,
                "sensitivity": item.sensitivity.value,
                "deep_link": item.deep_link,
            }
            for item in result.evidence
        ]
        if not result.success:
            return ToolResult(
                success=False,
                content="Connector write failed.",
                data={
                    "connector_id": arguments["connector_id"],
                    "scope": arguments["scope"],
                    "external_id": result.external_id,
                    "evidence": evidence,
                },
                error=result.error,
            )
        return ToolResult(
            success=True,
            content="Approved connector write completed and returned a verification receipt.",
            data={
                "connector_id": arguments["connector_id"],
                "scope": arguments["scope"],
                "external_id": result.external_id,
                "evidence": evidence,
            },
        )
