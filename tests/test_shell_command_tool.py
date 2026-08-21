import subprocess
from pathlib import Path

from chief.tools.base import ToolRisk
from chief.tools.registry import ToolRegistry, create_standard_registry
from chief.tools.shell import ShellCommandTool


def test_shell_tool_is_sensitive_and_requires_approval(tmp_path: Path) -> None:
    ran = False

    def runner(command, **kwargs):
        nonlocal ran
        ran = True
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    tool = ShellCommandTool([tmp_path], allowed_commands={"status"}, runner=runner)
    registry = ToolRegistry()
    registry.register(tool)

    result = registry.execute("shell_command", {"command": "status"})

    assert tool.definition.risk == ToolRisk.SENSITIVE
    assert tool.definition.requires_approval is True
    assert result.success is False
    assert result.content == "Tool execution requires approval."
    assert ran is False


def test_approved_allowlisted_command_uses_no_shell(tmp_path: Path) -> None:
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="healthy", stderr="")

    registry = ToolRegistry()
    registry.register(ShellCommandTool([tmp_path], allowed_commands={"status"}, runner=runner))

    result = registry.execute(
        "shell_command",
        {"command": "status", "args": ["--brief"]},
        approved=True,
    )

    assert result.success is True
    assert result.content == "healthy"
    assert calls[0][0] == ["status", "--brief"]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["cwd"] == tmp_path.resolve()


def test_shell_tool_denies_non_allowlisted_commands(tmp_path: Path) -> None:
    tool = ShellCommandTool([tmp_path], allowed_commands={"status"})

    result = tool.run({"command": "rm", "args": ["file"]})

    assert result.success is False
    assert "allowlist" in result.error


def test_shell_tool_denies_operators_and_redirection(tmp_path: Path) -> None:
    tool = ShellCommandTool([tmp_path], allowed_commands={"status"})

    for unsafe in ["; rm file", "&& next", "| other", "> output", "`command`"]:
        result = tool.run({"command": "status", "args": [unsafe]})
        assert result.success is False
        assert "not permitted" in result.error


def test_shell_tool_confines_working_directory(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    tool = ShellCommandTool([allowed], allowed_commands={"status"})

    result = tool.run({"command": "status", "cwd": ".."})

    assert result.success is False
    assert "outside" in result.error


def test_failed_command_is_reported_and_audited(tmp_path: Path) -> None:
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 2, stdout="", stderr="failed")

    registry = ToolRegistry()
    registry.register(ShellCommandTool([tmp_path], allowed_commands={"status"}, runner=runner))

    result = registry.execute("shell_command", {"command": "status"}, approved=True)

    assert result.success is False
    assert result.error == "failed"
    assert registry.audit_log.latest().success is False


def test_standard_registry_includes_approval_only_shell(tmp_path: Path) -> None:
    registry = create_standard_registry([str(tmp_path)])

    assert registry.names()[-1] == "shell_command"
    result = registry.execute("shell_command", {"command": "whoami"})
    assert result.success is False
    assert result.content == "Tool execution requires approval."
