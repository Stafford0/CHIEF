from datetime import UTC, datetime, timedelta

import pytest

from chief.agents.loop import ApprovalGrant, ApprovalLedger, PlanExecutor, argument_digest
from chief.agents.schema import ExecutionPlan, PlannedStep, PlanStatus
from chief.tools.base import Tool, ToolDefinition, ToolResult, ToolRisk
from chief.tools.registry import ToolRegistry


class RecordingTool(Tool):
    def __init__(self, name: str, *, sensitive: bool = False) -> None:
        self._definition = ToolDefinition(
            name=name,
            description=name,
            risk=ToolRisk.SENSITIVE if sensitive else ToolRisk.SAFE,
            requires_approval=sensitive,
            side_effects=sensitive,
        )
        self.calls: list[dict] = []

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def validate(self, arguments: dict) -> None:
        super().validate(arguments)
        if set(arguments) != {"value"}:
            raise ValueError("value is required")

    def execute(self, arguments: dict) -> ToolResult:
        self.calls.append(arguments)
        return ToolResult(success=True, content="ok")


def plan(tool_name: str = "read") -> ExecutionPlan:
    return ExecutionPlan(
        objective="Inspect current state",
        steps=[
            PlannedStep(
                id="one",
                tool_name=tool_name,
                arguments={"value": 1},
                rationale="Collect current evidence.",
                expected_outcome="Current state is returned.",
            )
        ],
    )


def test_plan_executor_runs_registered_safe_tool() -> None:
    registry = ToolRegistry()
    tool = RecordingTool("read")
    registry.register(tool)

    result = PlanExecutor(registry).execute(plan(), actor_id="director")

    assert result.status == PlanStatus.SUCCEEDED
    assert tool.calls == [{"value": 1}]


def test_plan_validation_denies_unknown_tool_and_invalid_arguments() -> None:
    registry = ToolRegistry()
    registry.register(RecordingTool("read"))
    executor = PlanExecutor(registry)

    with pytest.raises(ValueError, match="unregistered"):
        executor.validate(plan("invented"))
    invalid = plan().model_copy(
        update={"steps": [plan().steps[0].model_copy(update={"arguments": {}})]}
    )
    with pytest.raises(ValueError, match="Invalid arguments"):
        executor.validate(invalid)


def test_sensitive_step_requires_exact_single_use_actor_grant() -> None:
    registry = ToolRegistry()
    tool = RecordingTool("write", sensitive=True)
    registry.register(tool)
    ledger = ApprovalLedger()
    executor = PlanExecutor(registry, approval_ledger=ledger)
    planned = plan("write")
    digest = argument_digest("write", {"value": 1})

    pending = executor.execute(planned, actor_id="director")
    assert pending.status == PlanStatus.AWAITING_APPROVAL
    assert tool.calls == []

    ledger.issue(ApprovalGrant(digest, "director"))
    assert executor.execute(planned, actor_id="other").status == PlanStatus.AWAITING_APPROVAL
    assert executor.execute(planned, actor_id="director").status == PlanStatus.SUCCEEDED
    assert executor.execute(planned, actor_id="director").status == PlanStatus.AWAITING_APPROVAL
    assert tool.calls == [{"value": 1}]


def test_expired_approval_cannot_execute() -> None:
    registry = ToolRegistry()
    registry.register(RecordingTool("write", sensitive=True))
    ledger = ApprovalLedger()
    digest = argument_digest("write", {"value": 1})
    ledger.issue(
        ApprovalGrant(
            digest,
            "director",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )

    assert (
        PlanExecutor(registry, approval_ledger=ledger)
        .execute(plan("write"), actor_id="director")
        .status
        == PlanStatus.AWAITING_APPROVAL
    )
