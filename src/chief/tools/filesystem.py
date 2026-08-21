from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from chief.tools.base import Tool, ToolDefinition, ToolResult, ToolRisk


class ScopedFilesystemTool(Tool):
    """Base class for read-only tools confined to configured roots."""

    def __init__(self, allowed_roots: Iterable[str | Path]) -> None:
        roots = tuple(Path(root).resolve() for root in allowed_roots)
        if not roots:
            raise ValueError("At least one allowed filesystem root is required.")
        self.allowed_roots = roots

    def _resolve_path(self, value: object) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise TypeError("Argument 'path' must be a non-empty string.")

        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = self.allowed_roots[0] / candidate
        resolved = candidate.resolve()

        if not any(
            resolved == root or resolved.is_relative_to(root) for root in self.allowed_roots
        ):
            raise PermissionError("Path is outside the configured filesystem roots.")
        return resolved


class ListDirectoryTool(ScopedFilesystemTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="list_directory",
            description="List entries in a directory inside an allowed root.",
            risk=ToolRisk.SAFE,
        )

    def validate(self, arguments: dict[str, Any]) -> None:
        super().validate(arguments)
        unknown = set(arguments) - {"path"}
        if unknown:
            raise ValueError(f"Unexpected arguments: {', '.join(sorted(unknown))}")
        self._resolve_path(arguments.get("path", "."))

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        directory = self._resolve_path(arguments.get("path", "."))
        if not directory.is_dir():
            raise NotADirectoryError(f"Not a directory: {directory}")

        entries = [
            {
                "name": item.name,
                "path": str(item),
                "type": "directory" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None,
            }
            for item in sorted(
                directory.iterdir(), key=lambda path: (not path.is_dir(), path.name.lower())
            )
        ]
        return ToolResult(
            success=True,
            content=f"Listed {len(entries)} entries in {directory}.",
            data={"path": str(directory), "entries": entries},
        )


class ReadFileTool(ScopedFilesystemTool):
    def __init__(self, allowed_roots: Iterable[str | Path], *, max_bytes: int = 1_000_000) -> None:
        super().__init__(allowed_roots)
        self.max_bytes = max_bytes

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read_file",
            description="Read a UTF-8 text file inside an allowed root.",
            risk=ToolRisk.SAFE,
        )

    def validate(self, arguments: dict[str, Any]) -> None:
        super().validate(arguments)
        if set(arguments) != {"path"}:
            raise ValueError("read_file requires exactly one argument: path.")
        path = self._resolve_path(arguments["path"])
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        if path.stat().st_size > self.max_bytes:
            raise ValueError(f"File exceeds the {self.max_bytes}-byte read limit.")

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        path = self._resolve_path(arguments["path"])
        content = path.read_text(encoding="utf-8")
        return ToolResult(
            success=True,
            content=content,
            data={"path": str(path), "size": path.stat().st_size},
        )


class SearchFilesTool(ScopedFilesystemTool):
    def __init__(
        self,
        allowed_roots: Iterable[str | Path],
        *,
        max_matches: int = 100,
        max_file_bytes: int = 1_000_000,
    ) -> None:
        super().__init__(allowed_roots)
        self.max_matches = max_matches
        self.max_file_bytes = max_file_bytes

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="search_files",
            description="Search text files inside an allowed root.",
            risk=ToolRisk.SAFE,
        )

    def validate(self, arguments: dict[str, Any]) -> None:
        super().validate(arguments)
        unknown = set(arguments) - {"path", "query", "pattern"}
        if unknown:
            raise ValueError(f"Unexpected arguments: {', '.join(sorted(unknown))}")
        self._resolve_path(arguments.get("path", "."))
        if not isinstance(arguments.get("query"), str) or not arguments["query"]:
            raise TypeError("Argument 'query' must be a non-empty string.")
        pattern = arguments.get("pattern", "*")
        if not isinstance(pattern, str) or not pattern:
            raise TypeError("Argument 'pattern' must be a non-empty string.")

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        root = self._resolve_path(arguments.get("path", "."))
        if not root.is_dir():
            raise NotADirectoryError(f"Not a directory: {root}")

        query = arguments["query"]
        pattern = arguments.get("pattern", "*")
        matches: list[dict[str, Any]] = []
        truncated = False
        for path in root.rglob("*"):
            if not path.is_file() or not fnmatch.fnmatch(path.name, pattern):
                continue
            if path.stat().st_size > self.max_file_bytes:
                continue
            try:
                with path.open(encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        if query in line:
                            matches.append(
                                {
                                    "path": str(path),
                                    "line": line_number,
                                    "text": line.rstrip("\r\n"),
                                }
                            )
                            if len(matches) == self.max_matches:
                                truncated = True
                                break
            except (OSError, UnicodeError):
                continue
            if truncated:
                break

        return ToolResult(
            success=True,
            content=f"Found {len(matches)} matches for {query!r}.",
            data={"root": str(root), "matches": matches, "truncated": truncated},
        )
