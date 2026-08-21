from __future__ import annotations

import csv
import io
import os
import subprocess
from collections.abc import Callable
from typing import Any

from chief.tools.base import Tool, ToolDefinition, ToolResult, ToolRisk


class ProcessStatusTool(Tool):
    """Return a bounded snapshot of running processes without extra packages."""

    def __init__(
        self,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        platform_name: str | None = None,
        max_processes: int = 200,
    ) -> None:
        self.runner = runner
        self.platform_name = platform_name or os.name
        self.max_processes = max_processes

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="process_status",
            description="Report a read-only snapshot of running processes.",
            risk=ToolRisk.SAFE,
        )

    def validate(self, arguments: dict[str, Any]) -> None:
        super().validate(arguments)
        if arguments:
            raise ValueError("process_status does not accept arguments.")

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        command = (
            ["tasklist", "/FO", "CSV", "/NH"]
            if self.platform_name == "nt"
            else ["ps", "-eo", "pid=,comm="]
        )
        completed = self.runner(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "Unable to read process status.")

        if self.platform_name == "nt":
            rows = csv.reader(io.StringIO(completed.stdout))
            processes = [
                {"pid": int(row[1]), "name": row[0]}
                for row in rows
                if len(row) >= 2 and row[1].isdigit()
            ]
        else:
            processes = []
            for line in completed.stdout.splitlines():
                pid, separator, name = line.strip().partition(" ")
                if separator and pid.isdigit():
                    processes.append({"pid": int(pid), "name": name.strip()})

        processes = processes[: self.max_processes]
        return ToolResult(
            success=True,
            content=f"Found {len(processes)} running processes.",
            data={"processes": processes, "truncated": len(processes) == self.max_processes},
        )
