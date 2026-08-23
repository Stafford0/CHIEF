import subprocess

from chief.tools.powershell import PowerShellCommandTool, PowerShellReadTool
from chief.tools.registry import ToolRegistry, create_standard_registry


def completed(stdout: str = "OK\n", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_read_tool_is_safe_and_does_not_require_approval(tmp_path) -> None:
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return completed("Friday\n")

    tool = PowerShellReadTool(
        [tmp_path],
        runner=runner,
        executable="pwsh",
    )
    registry = ToolRegistry()
    registry.register(tool)

    result = registry.execute(
        "powershell_read",
        {"command": "Get-Date"},
    )

    assert result.success is True
    assert result.content == "Friday\n"
    assert calls[0][0][:5] == [
        "pwsh",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
    ]
    assert calls[0][0][5] == "Get-Date"
    assert calls[0][1]["shell"] is False


def test_read_tool_rejects_non_allowlisted_command(tmp_path) -> None:
    tool = PowerShellReadTool([tmp_path], executable="pwsh")

    result = tool.run({"command": "Remove-Item", "args": ["thing.txt"]})

    assert result.success is False
    assert "read-only allowlist" in (result.error or "")


def test_read_tool_rejects_shell_operators(tmp_path) -> None:
    tool = PowerShellReadTool([tmp_path], executable="pwsh")

    result = tool.run({"command": "Get-Process", "args": [";", "Remove-Item"]})

    assert result.success is False
    assert "operators" in (result.error or "")


def test_command_tool_requires_registry_approval(tmp_path) -> None:
    executed = False

    def runner(command, **kwargs):
        nonlocal executed
        executed = True
        return completed()

    tool = PowerShellCommandTool(
        [tmp_path],
        runner=runner,
        executable="pwsh",
    )
    registry = ToolRegistry()
    registry.register(tool)

    result = registry.execute(
        "powershell_command",
        {"command": "pytest", "args": ["-q"]},
    )

    assert result.success is False
    assert result.content == "Tool execution requires approval."
    assert executed is False


def test_command_tool_executes_after_explicit_approval(tmp_path) -> None:
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return completed("82 passed\n")

    tool = PowerShellCommandTool(
        [tmp_path],
        runner=runner,
        executable="pwsh",
    )
    registry = ToolRegistry()
    registry.register(tool)

    result = registry.execute(
        "powershell_command",
        {"command": "pytest", "args": ["-q"]},
        approved=True,
    )

    assert result.success is True
    assert "82 passed" in result.content
    assert calls[0][-2:] == ["pytest", "-q"]


def test_command_tool_blocks_destructive_git_reset_even_with_approval(tmp_path) -> None:
    tool = PowerShellCommandTool([tmp_path], executable="pwsh")
    registry = ToolRegistry()
    registry.register(tool)

    result = registry.execute(
        "powershell_command",
        {"command": "git", "args": ["reset", "--hard", "HEAD~1"]},
        approved=True,
    )

    assert result.success is False
    assert "destructive-operation" in (result.error or "")


def test_command_tool_restricts_working_directory(tmp_path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    tool = PowerShellCommandTool([allowed], executable="pwsh")

    result = tool.run(
        {
            "command": "pytest",
            "args": ["-q"],
            "cwd": str(outside),
        }
    )

    assert result.success is False
    assert "outside the configured roots" in (result.error or "")


def test_standard_registry_contains_both_powershell_tools(tmp_path) -> None:
    registry = create_standard_registry([str(tmp_path)])

    assert "powershell_read" in registry.names()
    assert "powershell_command" in registry.names()
