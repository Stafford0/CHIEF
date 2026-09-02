import hashlib
import json
import time
from typing import Any

from chief.audit.log import AuditEvent, AuditLog
from chief.guard.policy import PolicyDecision, ToolPolicy
from chief.tools.base import Tool, ToolDefinition, ToolExecutionContext, ToolResult


class ToolRegistry:
    """Whitelist, permission gate, and execution gateway for CHIEF tools."""

    def __init__(
        self,
        *,
        policy: ToolPolicy | None = None,
        audit_log: AuditLog | None = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self.policy = policy or ToolPolicy()
        self.audit_log = audit_log or AuditLog()

    def register(self, tool: Tool) -> None:
        """Register a tool with CHIEF."""

        name = tool.definition.name.strip()

        if not name:
            raise ValueError("Tool name cannot be empty.")

        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered.")

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

        return [tool.definition for tool in self._tools.values()]

    def names(self) -> list[str]:
        """Return names of all registered tools."""

        return list(self._tools.keys())

    def execute(
        self,
        name: str,
        arguments: dict,
        *,
        approved: bool = False,
        audit_context: dict[str, str | None] | None = None,
    ) -> ToolResult:
        """Evaluate policy, execute a registered tool, and audit the attempt."""
        started = time.perf_counter()
        argument_digest = hashlib.sha256(
            json.dumps(arguments, sort_keys=True, default=str).encode()
        ).hexdigest()
        context = audit_context or {}

        def event(
            *,
            decision: str,
            success: bool,
            error: str | None,
            metadata: dict[str, Any] | None = None,
        ) -> AuditEvent:
            return AuditEvent(
                tool_name=name,
                approved=approved,
                decision=decision,
                success=success,
                error=error,
                metadata=metadata or {},
                request_id=context.get("request_id"),
                actor_id=context.get("actor_id"),
                session_id=context.get("session_id"),
                run_id=context.get("run_id"),
                step_id=context.get("step_id"),
                proposal_id=context.get("proposal_id"),
            )

        tool = self.get(name)

        if tool is None:
            result = ToolResult(
                success=False,
                content="Tool execution refused.",
                error=(f"Tool '{name}' is not registered."),
            )
            self.audit_log.record(
                event(
                    decision=PolicyDecision.DENY.value,
                    success=False,
                    error=result.error,
                    metadata={"argument_digest": argument_digest},
                )
            )
            return result

        policy_result = self.policy.evaluate(
            tool.definition,
            approved=approved,
        )

        if policy_result.decision == PolicyDecision.REQUIRE_APPROVAL:
            result = ToolResult(
                success=False,
                content="Tool execution requires approval.",
                error=policy_result.reason,
            )
            self.audit_log.record(
                event(
                    decision=policy_result.decision.value,
                    success=False,
                    error=result.error,
                    metadata={"argument_digest": argument_digest},
                )
            )
            return result

        if policy_result.decision == PolicyDecision.DENY:
            result = ToolResult(
                success=False,
                content="Tool execution refused.",
                error=policy_result.reason,
            )
            self.audit_log.record(
                event(
                    decision=policy_result.decision.value,
                    success=False,
                    error=result.error,
                    metadata={"argument_digest": argument_digest},
                )
            )
            return result

        execution_context = ToolExecutionContext(
            actor_id=context.get("actor_id"),
            request_id=context.get("request_id"),
            session_id=context.get("session_id"),
            run_id=context.get("run_id"),
            step_id=context.get("step_id"),
            proposal_id=context.get("proposal_id"),
        )
        result = tool.run(arguments, context=execution_context)
        result_digest = hashlib.sha256(
            json.dumps(
                {
                    "success": result.success,
                    "content": result.content,
                    "data": result.data,
                    "error": result.error,
                },
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        self.audit_log.record(
            event(
                decision=policy_result.decision.value,
                success=result.success,
                error=result.error,
                metadata={
                    "argument_digest": argument_digest,
                    "result_digest": result_digest,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
        )
        return result

    def count(self) -> int:
        """Return the number of registered tools."""

        return len(self._tools)


def create_read_only_registry(
    allowed_roots: list[str],
    *,
    policy: ToolPolicy | None = None,
    audit_log: AuditLog | None = None,
) -> ToolRegistry:
    """Build a registry containing CHIEF's standard read-only tools."""

    from chief.tools.filesystem import ListDirectoryTool, ReadFileTool, SearchFilesTool
    from chief.tools.process_status import ProcessStatusTool
    from chief.tools.system_status import SystemStatusTool

    registry = ToolRegistry(policy=policy, audit_log=audit_log)
    registry.register(ListDirectoryTool(allowed_roots))
    registry.register(ReadFileTool(allowed_roots))
    registry.register(SearchFilesTool(allowed_roots))
    registry.register(SystemStatusTool())
    registry.register(ProcessStatusTool())
    return registry


def create_standard_registry(
    allowed_roots: list[str],
    *,
    policy: ToolPolicy | None = None,
    audit_log: AuditLog | None = None,
) -> ToolRegistry:
    """Build CHIEF's standard registry, including guarded execution tools."""

    from chief.tools.powershell import PowerShellCommandTool, PowerShellReadTool
    from chief.tools.shell import ShellCommandTool

    registry = create_read_only_registry(
        allowed_roots,
        policy=policy,
        audit_log=audit_log,
    )
    registry.register(PowerShellReadTool(allowed_roots))
    registry.register(PowerShellCommandTool(allowed_roots))
    registry.register(ShellCommandTool(allowed_roots))
    return registry
