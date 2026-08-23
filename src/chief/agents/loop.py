from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from threading import Lock
from uuid import UUID, uuid4

from chief.agents.schema import ExecutionPlan, PlanOutcome, PlanStatus, StepOutcome
from chief.tools.registry import ToolRegistry


def argument_digest(tool_name: str, arguments: dict) -> str:
    payload = json.dumps(
        {"tool": tool_name, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class ApprovalGrant:
    argument_digest: str
    actor_id: str
    id: UUID
    expires_at: datetime
    consumed_at: datetime | None = None

    def __init__(
        self,
        argument_digest: str,
        actor_id: str,
        *,
        ttl_seconds: int = 300,
        id: UUID | None = None,
        expires_at: datetime | None = None,
        consumed_at: datetime | None = None,
    ) -> None:
        if len(argument_digest) != 64:
            raise ValueError("Approval argument digest must be a SHA-256 hex digest.")
        bytes.fromhex(argument_digest)
        if not actor_id.strip():
            raise ValueError("Approval actor cannot be empty.")
        if ttl_seconds <= 0:
            raise ValueError("Approval TTL must be positive.")
        object.__setattr__(self, "argument_digest", argument_digest)
        object.__setattr__(self, "actor_id", actor_id)
        object.__setattr__(self, "id", id or uuid4())
        object.__setattr__(
            self,
            "expires_at",
            expires_at or datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        )
        object.__setattr__(self, "consumed_at", consumed_at)


class ApprovalLedger:
    """Single-use, actor-bound grants; persistent deployments can swap the ledger."""

    def __init__(self) -> None:
        self._grants: dict[UUID, ApprovalGrant] = {}
        self._lock = Lock()

    def issue(self, grant: ApprovalGrant) -> ApprovalGrant:
        with self._lock:
            if grant.id in self._grants:
                raise ValueError("Approval grant already exists.")
            self._grants[grant.id] = grant
        return grant

    def consume(self, digest: str, actor_id: str, *, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        with self._lock:
            for grant_id, grant in self._grants.items():
                if (
                    grant.argument_digest == digest
                    and grant.actor_id == actor_id
                    and grant.consumed_at is None
                    and now < grant.expires_at
                ):
                    self._grants[grant_id] = replace(grant, consumed_at=now)
                    return True
        return False


class PlanExecutor:
    """Validate and execute a caller-bounded plan through the guarded registry."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        approval_ledger: ApprovalLedger | None = None,
        max_steps: int = 8,
        clock=time.monotonic,
    ) -> None:
        if not 1 <= max_steps <= 20:
            raise ValueError("Agent maximum steps must be between 1 and 20.")
        self.registry = registry
        self.approval_ledger = approval_ledger or ApprovalLedger()
        self.max_steps = max_steps
        self._clock = clock

    def validate(self, plan: ExecutionPlan) -> None:
        if len(plan.steps) > self.max_steps:
            raise ValueError(f"Plan exceeds the {self.max_steps}-step execution budget.")
        for step in plan.steps:
            tool = self.registry.get(step.tool_name)
            if tool is None:
                raise ValueError(f"Plan references unregistered tool {step.tool_name!r}.")
            try:
                tool.validate(step.arguments)
            except Exception as exc:
                raise ValueError(f"Invalid arguments for step {step.id!r}: {exc}") from exc

    def execute(
        self,
        plan: ExecutionPlan,
        *,
        actor_id: str,
        audit_context: dict[str, str | None] | None = None,
    ) -> PlanOutcome:
        self.validate(plan)
        started = self._clock()
        outcomes: list[StepOutcome] = []
        for step in plan.steps:
            if self._clock() - started > plan.max_duration_seconds:
                return PlanOutcome(
                    plan_id=plan.id,
                    status=PlanStatus.BUDGET_EXCEEDED,
                    steps=outcomes,
                    error="Plan duration budget exceeded.",
                )
            tool = self.registry.get(step.tool_name)
            assert tool is not None
            digest = argument_digest(step.tool_name, step.arguments)
            approved = False
            if tool.definition.requires_approval:
                approved = self.approval_ledger.consume(digest, actor_id)
                if not approved:
                    return PlanOutcome(
                        plan_id=plan.id,
                        status=PlanStatus.AWAITING_APPROVAL,
                        steps=outcomes,
                        pending_step_id=step.id,
                        pending_argument_digest=digest,
                    )
            step_started = self._clock()
            result = self.registry.execute(
                step.tool_name,
                step.arguments,
                approved=approved,
                audit_context=audit_context,
            )
            outcomes.append(
                StepOutcome(
                    step_id=step.id,
                    tool_name=step.tool_name,
                    success=result.success,
                    content=result.content,
                    error=result.error,
                    argument_digest=digest,
                    duration_ms=(self._clock() - step_started) * 1_000,
                )
            )
            if not result.success:
                return PlanOutcome(
                    plan_id=plan.id,
                    status=PlanStatus.FAILED,
                    steps=outcomes,
                    error=result.error,
                )
        return PlanOutcome(plan_id=plan.id, status=PlanStatus.SUCCEEDED, steps=outcomes)
