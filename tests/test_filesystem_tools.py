from pathlib import Path

from chief.tools.base import ToolRisk
from chief.tools.filesystem import ListDirectoryTool, ReadFileTool, SearchFilesTool
from chief.tools.registry import ToolRegistry, create_read_only_registry


def test_list_directory_returns_sorted_entries(tmp_path: Path) -> None:
    (tmp_path / "folder").mkdir()
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    tool = ListDirectoryTool([tmp_path])

    result = tool.run({"path": "."})

    assert result.success is True
    assert [entry["name"] for entry in result.data["entries"]] == ["folder", "note.txt"]
    assert tool.definition.risk == ToolRisk.SAFE


def test_list_directory_cannot_escape_allowed_root(tmp_path: Path) -> None:
    tool = ListDirectoryTool([tmp_path / "allowed"])
    (tmp_path / "allowed").mkdir()

    result = tool.run({"path": ".."})

    assert result.success is False
    assert "outside" in result.error


def test_read_file_returns_utf8_text(tmp_path: Path) -> None:
    path = tmp_path / "note.txt"
    path.write_text("CHIEF online", encoding="utf-8")

    result = ReadFileTool([tmp_path]).run({"path": "note.txt"})

    assert result.success is True
    assert result.content == "CHIEF online"
    assert result.data["path"] == str(path.resolve())


def test_read_file_enforces_size_limit(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_text("12345", encoding="utf-8")

    result = ReadFileTool([tmp_path], max_bytes=4).run({"path": "large.txt"})

    assert result.success is False
    assert "read limit" in result.error


def test_search_files_returns_line_matches(tmp_path: Path) -> None:
    (tmp_path / "one.txt").write_text("first\nCHIEF here\n", encoding="utf-8")
    (tmp_path / "two.log").write_text("CHIEF ignored", encoding="utf-8")

    result = SearchFilesTool([tmp_path]).run({"path": ".", "query": "CHIEF", "pattern": "*.txt"})

    assert result.success is True
    assert len(result.data["matches"]) == 1
    assert result.data["matches"][0]["line"] == 2


def test_read_only_tools_execute_and_audit_through_registry(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("safe", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(ReadFileTool([tmp_path]))

    result = registry.execute("read_file", {"path": "note.txt"})

    assert result.success is True
    assert registry.audit_log.latest() is not None
    assert registry.audit_log.latest().decision == "allow"


def test_read_only_registry_contains_standard_tools(tmp_path: Path) -> None:
    registry = create_read_only_registry([str(tmp_path)])

    assert registry.names() == [
        "list_directory",
        "read_file",
        "search_files",
        "system_status",
        "process_status",
    ]
