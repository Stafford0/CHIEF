import subprocess

from chief.tools.process_status import ProcessStatusTool


def test_process_status_parses_windows_tasklist() -> None:
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='"python.exe","123","Console","1","10,000 K"\n',
            stderr="",
        )

    result = ProcessStatusTool(runner=runner, platform_name="nt").run({})

    assert result.success is True
    assert result.data["processes"] == [{"pid": 123, "name": "python.exe"}]
    assert calls[0][0] == ["tasklist", "/FO", "CSV", "/NH"]
    assert calls[0][1]["timeout"] == 10


def test_process_status_parses_posix_ps() -> None:
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="  12 python\n99 worker\n", stderr="")

    result = ProcessStatusTool(runner=runner, platform_name="posix").run({})

    assert result.data["processes"] == [
        {"pid": 12, "name": "python"},
        {"pid": 99, "name": "worker"},
    ]


def test_process_status_rejects_arguments() -> None:
    result = ProcessStatusTool().run({"pid": 1})

    assert result.success is False
    assert result.error == "process_status does not accept arguments."
