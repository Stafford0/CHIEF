import platform
import sys
from typing import Any

from chief.tools.base import (
    Tool,
    ToolDefinition,
    ToolResult,
    ToolRisk,
)


class SystemStatusTool(Tool):
    """Report basic information about CHIEF's runtime environment."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="system_status",
            description=(
                "Report CHIEF runtime status and basic "
                "environment information."
            ),
            risk=ToolRisk.SAFE,
            requires_approval=False,
        )

    def validate(
        self,
        arguments: dict[str, Any],
    ) -> None:
        super().validate(arguments)

        if arguments:
            raise ValueError(
                "system_status does not accept arguments."
            )

    def execute(
        self,
        arguments: dict[str, Any],
    ) -> ToolResult:
        system = platform.system()
        release = platform.release()
        python_version = platform.python_version()

        return ToolResult(
            success=True,
            content=(
                "CHIEF runtime is online. "
                f"Operating system: {system} {release}. "
                f"Python: {python_version}."
            ),
            data={
                "status": "online",
                "system": "CHIEF",
                "os": system,
                "os_release": release,
                "python_version": python_version,
                "python_executable": sys.executable,
            },
        )