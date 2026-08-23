import platform

from chief.tools.base import ToolRisk
from chief.tools.registry import ToolRegistry
from chief.tools.system_status import SystemStatusTool


def test_system_status_tool_definition() -> None:
    tool = SystemStatusTool()

    definition = tool.definition

    assert definition.name == "system_status"
    assert definition.risk == ToolRisk.SAFE
    assert definition.requires_approval is False


def test_system_status_registers() -> None:
    registry = ToolRegistry()

    registry.register(SystemStatusTool())

    assert registry.get("system_status") is not None
    assert "system_status" in registry.names()


def test_system_status_executes_through_registry() -> None:
    registry = ToolRegistry()
    registry.register(SystemStatusTool())

    result = registry.execute(
        "system_status",
        {},
    )

    assert result.success is True
    assert result.data["status"] == "online"
    assert result.data["system"] == "CHIEF"


def test_system_status_reports_runtime() -> None:
    registry = ToolRegistry()
    registry.register(SystemStatusTool())

    result = registry.execute(
        "system_status",
        {},
    )

    assert result.success is True
    assert result.data["os"] == platform.system()
    assert result.data["python_version"] == (platform.python_version())

    assert "CHIEF runtime is online." in result.content


def test_system_status_rejects_arguments() -> None:
    registry = ToolRegistry()
    registry.register(SystemStatusTool())

    result = registry.execute(
        "system_status",
        {
            "unexpected": "value",
        },
    )

    assert result.success is False
    assert result.error == ("system_status does not accept arguments.")
