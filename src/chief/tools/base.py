from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolRisk(str, Enum):
    """Risk level associated with executing a CHIEF tool."""

    SAFE = "safe"
    CONTROLLED = "controlled"
    SENSITIVE = "sensitive"


@dataclass(frozen=True)
class ToolDefinition:
    """Metadata describing a tool available to CHIEF."""

    name: str
    description: str
    risk: ToolRisk = ToolRisk.SAFE
    requires_approval: bool = False


@dataclass(frozen=True)
class ToolResult:
    """Standard result returned by every CHIEF tool."""

    success: bool
    content: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class Tool(ABC):
    """Base contract for every executable CHIEF tool."""

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        """Describe the tool and its execution requirements."""

    @abstractmethod
    def execute(
        self,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Execute the tool using validated arguments."""

    def validate(
        self,
        arguments: dict[str, Any],
    ) -> None:
        """Validate arguments before execution."""

        if not isinstance(arguments, dict):
            raise TypeError("Tool arguments must be a dictionary.")

    def run(
        self,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Validate and execute the tool."""

        try:
            self.validate(arguments)
            return self.execute(arguments)

        except Exception as exc:  # noqa: BLE001 - tool gateway must contain adapter failures
            return ToolResult(
                success=False,
                content="Tool execution failed.",
                error=str(exc),
            )
