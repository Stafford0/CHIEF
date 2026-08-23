from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from chief.tools.base import Tool, ToolDefinition, ToolResult, ToolRisk


_UNSAFE_TOKEN = re.compile(r"[;&|<>`\r\n]")


class _PowerShellBase(Tool):
    def __init__(
        self,
        allowed_roots: Iterable[str | Path],
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        timeout_seconds: int = 30,
        max_output_chars: int = 20_000,
        executable: str | None = None,
    ) -> None:
        self.allowed_roots = tuple(Path(root).resolve() for root in allowed_roots)
        if not self.allowed_roots:
            raise ValueError("At least one allowed working-directory root is required.")
        self.runner = runner
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars
        self.executable = executable or shutil.which("pwsh") or shutil.which("powershell")

    def _require_executable(self) -> str:
        if not self.executable:
            raise RuntimeError("PowerShell was not found on PATH.")
        return self.executable

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

    def _run(self, command: str, args: list[str], cwd: Path) -> ToolResult:
        executable = self._require_executable()
        completed = self.runner(
            [
                executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
                *args,
            ],
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
            content=stdout or stderr or f"PowerShell exited with code {completed.returncode}.",
            data={
                "command": command,
                "args": args,
                "cwd": str(cwd),
                "returncode": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "truncated": len(completed.stdout) > self.max_output_chars
                or len(completed.stderr) > self.max_output_chars,
            },
            error=None
            if completed.returncode == 0
            else (stderr or f"PowerShell exited with code {completed.returncode}."),
        )


class PowerShellReadTool(_PowerShellBase):
    """Run a small allowlist of read-only PowerShell commands automatically."""

    DEFAULT_ALLOWED_COMMANDS = frozenset(
        {
            "Get-Date",
            "Get-Location",
            "Get-Process",
            "Get-Service",
            "Get-ComputerInfo",
        }
    )

    def __init__(
        self,
        allowed_roots: Iterable[str | Path],
        *,
        allowed_commands: Iterable[str] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        timeout_seconds: int = 30,
        max_output_chars: int = 20_000,
        executable: str | None = None,
    ) -> None:
        super().__init__(
            allowed_roots,
            runner=runner,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
            executable=executable,
        )
        self.allowed_commands = frozenset(allowed_commands or self.DEFAULT_ALLOWED_COMMANDS)

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="powershell_read",
            description="Run allowlisted read-only PowerShell diagnostics.",
            risk=ToolRisk.SAFE,
            requires_approval=False,
        )

    def validate(self, arguments: dict[str, Any]) -> None:
        super().validate(arguments)
        if set(arguments) - {"command", "args", "cwd"}:
            raise ValueError("powershell_read accepts only command, args, and cwd.")
        command = arguments.get("command")
        if not isinstance(command, str) or command not in self.allowed_commands:
            raise PermissionError("PowerShell command is not in the read-only allowlist.")
        args = arguments.get("args", [])
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise TypeError("Argument 'args' must be a list of strings.")
        if any(_UNSAFE_TOKEN.search(arg) for arg in args):
            raise PermissionError("PowerShell operators and redirection are not permitted.")
        self._working_directory(arguments.get("cwd"))

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return self._run(
            arguments["command"],
            arguments.get("args", []),
            self._working_directory(arguments.get("cwd")),
        )


class PowerShellCommandTool(_PowerShellBase):
    """Run explicitly approved developer commands through PowerShell."""

    DEFAULT_ALLOWED_COMMANDS = frozenset(
        {
            "git",
            "python",
            "pytest",
            "pip",
            "docker",
            "docker-compose",
            "node",
            "npm",
            "ollama",
            "uvicorn",
        }
    )
    _BLOCKED_ARGUMENT_PATTERNS = (
        re.compile(r"(^|\s)--force($|\s)", re.IGNORECASE),
        re.compile(r"(^|\s)reset\s+--hard($|\s)", re.IGNORECASE),
        re.compile(r"(^|\s)clean\s+-[^\s]*f", re.IGNORECASE),
        re.compile(r"(^|\s)rm($|\s)", re.IGNORECASE),
        re.compile(r"(^|\s)remove($|\s)", re.IGNORECASE),
        re.compile(r"(^|\s)prune($|\s)", re.IGNORECASE),
        re.compile(r"(^|\s)system\s+prune($|\s)", re.IGNORECASE),
    )

    def __init__(
        self,
        allowed_roots: Iterable[str | Path],
        *,
        allowed_commands: Iterable[str] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        timeout_seconds: int = 120,
        max_output_chars: int = 40_000,
        executable: str | None = None,
    ) -> None:
        super().__init__(
            allowed_roots,
            runner=runner,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
            executable=executable,
        )
        self.allowed_commands = frozenset(allowed_commands or self.DEFAULT_ALLOWED_COMMANDS)

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="powershell_command",
            description="Run an explicitly approved developer command through PowerShell.",
            risk=ToolRisk.SENSITIVE,
            requires_approval=True,
        )

    def validate(self, arguments: dict[str, Any]) -> None:
        super().validate(arguments)
        if set(arguments) - {"command", "args", "cwd"}:
            raise ValueError("powershell_command accepts only command, args, and cwd.")
        command = arguments.get("command")
        if not isinstance(command, str) or command not in self.allowed_commands:
            raise PermissionError("Command is not in the approved developer allowlist.")
        if Path(command).name != command:
            raise PermissionError("Executable paths are not permitted.")
        args = arguments.get("args", [])
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise TypeError("Argument 'args' must be a list of strings.")
        if any(_UNSAFE_TOKEN.search(arg) for arg in args):
            raise PermissionError("PowerShell operators and redirection are not permitted.")
        joined = " ".join(args)
        if any(pattern.search(joined) for pattern in self._BLOCKED_ARGUMENT_PATTERNS):
            raise PermissionError("Command arguments match a destructive-operation block rule.")
        self._working_directory(arguments.get("cwd"))

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return self._run(
            arguments["command"],
            arguments.get("args", []),
            self._working_directory(arguments.get("cwd")),
        )
