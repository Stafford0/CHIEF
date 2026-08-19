import pytest

from chief.tools.base import (
    Tool,
    ToolDefinition,
    ToolResult,
    ToolRisk,
)
from chief.tools.registry import ToolRegistry


class EchoTool(Tool):
    """Harmless test tool that echoes supplied text."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="echo",
            description="Echo supplied text.",
            risk=ToolRisk.SAFE,
            requires_approval=False,
        )

    def validate(self, arguments: dict) -> None:
        super().validate(arguments)

        if "text" not in arguments:
            raise ValueError("Missing required argument: text")

        if not isinstance(arguments["text"], str):
            raise TypeError("Argument 'text' must be a string.")

    def execute(self, arguments: dict) -> ToolResult:
        text = arguments["text"]

        return ToolResult(
            success=True,
            content=text,
            data={
                "text": text,
            },
        )


class ApprovalTool(Tool):
    """Test tool that must never run without approval."""

    def __init__(self) -> None:
        self.executed = False

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="approval_test",
            description="Tool requiring explicit approval.",
            risk=ToolRisk.SENSITIVE,
            requires_approval=True,
        )

    def execute(self, arguments: dict) -> ToolResult:
        self.executed = True

        return ToolResult(
            success=True,
            content="Executed.",
        )


def test_registry_registers_tool() -> None:
    registry = ToolRegistry()
    tool = EchoTool()

    registry.register(tool)

    assert registry.get("echo") is tool
    assert registry.count() == 1
    assert registry.names() == ["echo"]


def test_registry_rejects_duplicate_tool() -> None:
    registry = ToolRegistry()

    registry.register(EchoTool())

    with pytest.raises(
        ValueError,
        match="Tool 'echo' is already registered.",
    ):
        registry.register(EchoTool())


def test_registry_refuses_unknown_tool() -> None:
    registry = ToolRegistry()

    result = registry.execute(
        "not_registered",
        {},
    )

    assert result.success is False
    assert result.content == "Tool execution refused."
    assert result.error is not None
    assert "not registered" in result.error


def test_registered_tool_executes() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    result = registry.execute(
        "echo",
        {
            "text": "CHIEF TOOL ONLINE",
        },
    )

    assert result.success is True
    assert result.content == "CHIEF TOOL ONLINE"
    assert result.data["text"] == "CHIEF TOOL ONLINE"


def test_tool_validation_failure_is_safe() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    result = registry.execute(
        "echo",
        {},
    )

    assert result.success is False
    assert result.content == "Tool execution failed."
    assert result.error == "Missing required argument: text"


def test_tool_rejects_invalid_argument_type() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    result = registry.execute(
        "echo",
        {
            "text": 731,
        },
    )

    assert result.success is False
    assert result.error == (
        "Argument 'text' must be a string."
    )


def test_approval_tool_cannot_execute() -> None:
    registry = ToolRegistry()
    tool = ApprovalTool()

    registry.register(tool)

    result = registry.execute(
        "approval_test",
        {},
    )

    assert result.success is False
    assert result.content == (
        "Tool execution requires approval."
    )
    assert tool.executed is False


def test_registry_can_unregister_tool() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    assert registry.unregister("echo") is True
    assert registry.get("echo") is None
    assert registry.count() == 0

    assert registry.unregister("echo") is False


def test_registry_exposes_tool_definitions() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    definitions = registry.definitions()

    assert len(definitions) == 1
    assert definitions[0].name == "echo"
    assert definitions[0].risk == ToolRisk.SAFE
    assert definitions[0].requires_approval is False
