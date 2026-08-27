from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar


@dataclass(frozen=True)
class PlannedToolCall:
    """A tool call selected from a fixed, code-owned intent map."""

    intent: str
    tool_name: str
    arguments: dict[str, Any]
    description: str


class PendingAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


class DeterministicToolPlanner:
    """Recognize a narrow set of explicit intents without involving the LLM."""

    _INTENTS: ClassVar[dict[str, PlannedToolCall]] = {
        "run the tests": PlannedToolCall(
            intent="run_tests",
            tool_name="powershell_command",
            arguments={"command": "pytest", "args": ["-q"]},
            description="run the test suite with pytest -q",
        ),
        "run tests": PlannedToolCall(
            intent="run_tests",
            tool_name="powershell_command",
            arguments={"command": "pytest", "args": ["-q"]},
            description="run the test suite with pytest -q",
        ),
        "show processes": PlannedToolCall(
            intent="show_processes",
            tool_name="powershell_read",
            arguments={"command": "Get-Process"},
            description="show running processes",
        ),
        "show running processes": PlannedToolCall(
            intent="show_processes",
            tool_name="powershell_read",
            arguments={"command": "Get-Process"},
            description="show running processes",
        ),
        "show system info": PlannedToolCall(
            intent="show_system_info",
            tool_name="powershell_read",
            arguments={"command": "Get-ComputerInfo"},
            description="show system information",
        ),
        "show system information": PlannedToolCall(
            intent="show_system_info",
            tool_name="powershell_read",
            arguments={"command": "Get-ComputerInfo"},
            description="show system information",
        ),
        "check system status": PlannedToolCall(
            intent="check_system_status",
            tool_name="system_status",
            arguments={},
            description="check CHIEF and host system status",
        ),
        "show system status": PlannedToolCall(
            intent="check_system_status",
            tool_name="system_status",
            arguments={},
            description="check CHIEF and host system status",
        ),
        "show the date": PlannedToolCall(
            intent="show_date",
            tool_name="powershell_read",
            arguments={"command": "Get-Date"},
            description="show the current date and time",
        ),
        "show current directory": PlannedToolCall(
            intent="show_location",
            tool_name="powershell_read",
            arguments={"command": "Get-Location"},
            description="show the current working directory",
        ),
    }
    _APPROVALS = frozenset({"approve", "approved", "yes approve", "yes, approve", "go ahead"})
    _REJECTIONS = frozenset(
        {"reject", "cancel", "cancel it", "do not run it", "don't run it", "no"}
    )

    @staticmethod
    def _normalize(message: str) -> str:
        normalized = re.sub(r"\s+", " ", message.strip().casefold())
        normalized = re.sub(r"^chief\s*,?\s+", "", normalized)
        return normalized.rstrip(".!?")

    def plan(self, message: str) -> PlannedToolCall | None:
        return self._INTENTS.get(self._normalize(message))

    def pending_action(self, message: str) -> PendingAction | None:
        normalized = self._normalize(message)
        if normalized in self._APPROVALS:
            return PendingAction.APPROVE
        if normalized in self._REJECTIONS:
            return PendingAction.REJECT
        return None
