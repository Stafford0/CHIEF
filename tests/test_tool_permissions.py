from chief.audit.log import AuditLog
from chief.guard.policy import PolicyDecision, ToolPolicy
from chief.tools.base import (
    Tool,
    ToolDefinition,
    ToolResult,
    ToolRisk,
)
from chief.tools.registry import ToolRegistry


class ControlledTool(Tool):
    def __init__(self) -> None:
        self.executed = False

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="controlled_test",
            description="Controlled test action.",
            risk=ToolRisk.CONTROLLED,
        )

    def execute(self, arguments: dict) -> ToolResult:
        self.executed = True
        return ToolResult(success=True, content="Controlled action executed.")


class SensitiveTool(Tool):
    def __init__(self) -> None:
        self.executed = False

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="sensitive_test",
            description="Sensitive test action.",
            risk=ToolRisk.SENSITIVE,
            requires_approval=True,
        )

    def execute(self, arguments: dict) -> ToolResult:
        self.executed = True
        return ToolResult(success=True, content="Sensitive action executed.")


def test_controlled_tool_requires_approval_by_default() -> None:
    tool = ControlledTool()
    registry = ToolRegistry()
    registry.register(tool)

    result = registry.execute("controlled_test", {})

    assert result.success is False
    assert result.content == "Tool execution requires approval."
    assert tool.executed is False


def test_controlled_tool_can_be_enabled_by_policy() -> None:
    tool = ControlledTool()
    registry = ToolRegistry(policy=ToolPolicy(allow_controlled=True))
    registry.register(tool)

    result = registry.execute("controlled_test", {})

    assert result.success is True
    assert tool.executed is True


def test_sensitive_tool_runs_only_after_explicit_approval() -> None:
    tool = SensitiveTool()
    registry = ToolRegistry()
    registry.register(tool)

    blocked = registry.execute("sensitive_test", {})
    approved = registry.execute(
        "sensitive_test",
        {},
        approved=True,
    )

    assert blocked.success is False
    assert approved.success is True
    assert tool.executed is True


def test_blocked_attempt_is_audited() -> None:
    audit_log = AuditLog()
    registry = ToolRegistry(audit_log=audit_log)
    registry.register(SensitiveTool())

    registry.execute("sensitive_test", {})

    event = audit_log.latest()
    assert event is not None
    assert event.tool_name == "sensitive_test"
    assert event.decision == PolicyDecision.REQUIRE_APPROVAL.value
    assert event.approved is False
    assert event.success is False


def test_approved_execution_is_audited() -> None:
    audit_log = AuditLog()
    registry = ToolRegistry(audit_log=audit_log)
    registry.register(SensitiveTool())

    registry.execute(
        "sensitive_test",
        {},
        approved=True,
        audit_context={
            "request_id": "request-1",
            "actor_id": "operator-1",
            "session_id": "session-1",
            "proposal_id": "proposal-1",
        },
    )

    event = audit_log.latest()
    assert event is not None
    assert event.decision == PolicyDecision.ALLOW.value
    assert event.approved is True
    assert event.success is True
    assert event.request_id == "request-1"
    assert event.actor_id == "operator-1"
    assert event.session_id == "session-1"
    assert event.proposal_id == "proposal-1"
    assert len(event.metadata["argument_digest"]) == 64
    assert len(event.metadata["result_digest"]) == 64


def test_unknown_tool_attempt_is_audited_as_denied() -> None:
    audit_log = AuditLog()
    registry = ToolRegistry(audit_log=audit_log)

    registry.execute("ghost_tool", {})

    event = audit_log.latest()
    assert event is not None
    assert event.tool_name == "ghost_tool"
    assert event.decision == PolicyDecision.DENY.value
    assert event.success is False
