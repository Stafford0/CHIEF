from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolRisk(str, Enum):
    """Risk level associated with executing a CHIEF tool."""

    SAFE = "safe"
    CONTROLLED = "controlled"
    SENSITIVE = "sensitive"


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """Authenticated execution metadata supplied by CHIEF, never by model arguments."""

    actor_id: str | None = None
    request_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    step_id: str | None = None
    proposal_id: str | None = None


@dataclass(frozen=True)
class ToolDefinition:
    """Metadata describing a tool available to CHIEF."""

    name: str
    description: str
    risk: ToolRisk = ToolRisk.SAFE
    requires_approval: bool = False
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}, "additionalProperties": False}
    )
    side_effects: bool = False
    idempotent: bool = True
    timeout_seconds: int = 30


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

    def execute_with_context(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Execute with authenticated context; ordinary tools ignore it by default."""

        del context
        return self.execute(arguments)

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
        *,
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        """Validate and execute the tool."""

        try:
            self.validate(arguments)
            if context is None:
                return self.execute(arguments)
            return self.execute_with_context(arguments, context)

        except Exception as exc:  # noqa: BLE001 - tool gateway must contain adapter failures
            return ToolResult(
                success=False,
                content="Tool execution failed.",
                error=str(exc),
            )
