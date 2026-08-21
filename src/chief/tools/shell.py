from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from chief.tools.base import Tool, ToolDefinition, ToolResult, ToolRisk


class ShellCommandTool(Tool):
    """Execute explicitly approved, allowlisted diagnostic commands."""

    DEFAULT_ALLOWED_COMMANDS = frozenset({"hostname", "ipconfig", "ping", "whoami"})
    _UNSAFE_ARGUMENT = re.compile(r"[;&|<>`\r\n]")

    def __init__(
        self,
        allowed_roots: Iterable[str | Path],
        *,
        allowed_commands: Iterable[str] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        timeout_seconds: int = 10,
        max_output_chars: int = 20_000,
    ) -> None:
        self.allowed_roots = tuple(Path(root).resolve() for root in allowed_roots)
        if not self.allowed_roots:
            raise ValueError("At least one allowed working-directory root is required.")
        self.allowed_commands = frozenset(allowed_commands or self.DEFAULT_ALLOWED_COMMANDS)
        self.runner = runner
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="shell_command",
            description="Run an approved command from a restricted diagnostic allowlist.",
            risk=ToolRisk.SENSITIVE,
            requires_approval=True,
        )

    def _working_directory(self, value: object) -> Path:
        if value is None:
            return self.allowed_roots[0]
        if not isinstance(value, str) or not value.strip():
            raise TypeError("Argument 'cwd' must be a non-empty string.")
        path = Path(value)
        if not path.is_absolute():
            path = self.allowed_roots[0] / path
        resolved = path.resolve()
        if not any(
            resolved == root or resolved.is_relative_to(root) for root in self.allowed_roots
        ):
            raise PermissionError("Working directory is outside the configured roots.")
        if not resolved.is_dir():
            raise NotADirectoryError(f"Not a directory: {resolved}")
        return resolved

    def validate(self, arguments: dict[str, Any]) -> None:
        super().validate(arguments)
        if set(arguments) - {"command", "args", "cwd"}:
            raise ValueError("shell_command accepts only command, args, and cwd.")
        command = arguments.get("command")
        if not isinstance(command, str) or command not in self.allowed_commands:
            raise PermissionError("Command is not in the diagnostic allowlist.")
        if Path(command).name != command:
            raise PermissionError("Executable paths are not permitted.")
        args = arguments.get("args", [])
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise TypeError("Argument 'args' must be a list of strings.")
        if any(self._UNSAFE_ARGUMENT.search(arg) for arg in args):
            raise PermissionError("Shell operators and redirection are not permitted.")
        self._working_directory(arguments.get("cwd"))

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        command = [arguments["command"], *arguments.get("args", [])]
        cwd = self._working_directory(arguments.get("cwd"))
        completed = self.runner(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            shell=False,
            timeout=self.timeout_seconds,
            check=False,
        )
        stdout = completed.stdout[: self.max_output_chars]
        stderr = completed.stderr[: self.max_output_chars]
        return ToolResult(
            success=completed.returncode == 0,
            content=stdout or stderr or f"Command exited with code {completed.returncode}.",
            data={
                "command": command,
                "cwd": str(cwd),
                "returncode": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "truncated": len(completed.stdout) > self.max_output_chars
                or len(completed.stderr) > self.max_output_chars,
            },
            error=None
            if completed.returncode == 0
            else (stderr or f"Command exited with code {completed.returncode}."),
        )
