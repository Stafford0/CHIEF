from chief.tools.base import Tool, ToolDefinition, ToolResult


class ToolRegistry:
    """Whitelist and execution gateway for CHIEF tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool with CHIEF."""

        name = tool.definition.name.strip()

        if not name:
            raise ValueError(
                "Tool name cannot be empty."
            )

        if name in self._tools:
            raise ValueError(
                f"Tool '{name}' is already registered."
            )

        self._tools[name] = tool

    def unregister(self, name: str) -> bool:
        """Remove a tool from the registry."""

        if name not in self._tools:
            return False

        del self._tools[name]
        return True

    def get(self, name: str) -> Tool | None:
        """Retrieve a registered tool by name."""

        return self._tools.get(name)

    def definitions(self) -> list[ToolDefinition]:
        """Return definitions for all registered tools."""

        return [
            tool.definition
            for tool in self._tools.values()
        ]

    def names(self) -> list[str]:
        """Return names of all registered tools."""

        return list(self._tools.keys())

    def execute(
        self,
        name: str,
        arguments: dict,
    ) -> ToolResult:
        """Execute a registered tool."""

        tool = self.get(name)

        if tool is None:
            return ToolResult(
                success=False,
                content="Tool execution refused.",
                error=(
                    f"Tool '{name}' is not registered."
                ),
            )

        if tool.definition.requires_approval:
            return ToolResult(
                success=False,
                content="Tool execution requires approval.",
                error=(
                    f"Tool '{name}' requires user approval."
                ),
            )

        return tool.run(arguments)

    def count(self) -> int:
        """Return the number of registered tools."""

        return len(self._tools)