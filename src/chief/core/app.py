from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from chief.core.identity import SYSTEM_IDENTITY
from chief.core.session_store import SessionStore
from chief.core.tool_planner import DeterministicToolPlanner, PendingAction
from chief.memory.commands import (
    CorrectMemoryCommand,
    ForgetMemoryCommand,
    MemoryCommand,
    MemoryCommandParser,
)
from chief.memory.manager import MemoryManager
from chief.memory.sqlite import SQLiteMemoryStore
from chief.models.ollama import OllamaProvider
from chief.tools.registry import ToolRegistry, create_standard_registry


app = FastAPI(
    title="CHIEF",
    description="Cognitive Hub for Intelligence, Execution & Foresight",
    version="0.0.1",
)

model_provider = OllamaProvider(model="qwen3:4b")

memory_store = SQLiteMemoryStore()
memory_manager = MemoryManager(memory_store)
memory_command_parser = MemoryCommandParser()

session_store = SessionStore()

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WEB_UI_PATH = Path(__file__).resolve().parents[1] / "web" / "index.html"
tool_registry: ToolRegistry = create_standard_registry([str(PROJECT_ROOT)])
tool_planner = DeterministicToolPlanner()


class ChatRequest(BaseModel):
    message: str
    session_id: UUID | None = None


class ChatResponse(BaseModel):
    response: str
    provider: str
    model: str
    session_id: UUID
    status: str = "completed"
    pending_action: str | None = None
    tool_description: str | None = None


class ToolDefinitionResponse(BaseModel):
    name: str
    description: str
    risk: str
    requires_approval: bool


class ToolExecuteRequest(BaseModel):
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    approved: bool = False


class ToolExecuteResponse(BaseModel):
    success: bool
    content: str
    data: dict[str, Any]
    error: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "online",
        "system": "CHIEF",
        "version": "0.0.1",
    }


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def chat_ui() -> HTMLResponse:
    """Serve CHIEF's lightweight local chat interface."""

    return HTMLResponse(WEB_UI_PATH.read_text(encoding="utf-8"))


@app.get("/system")
def system_info() -> dict[str, str]:
    return {
        "name": "CHIEF",
        "full_name": "Cognitive Hub for Intelligence, Execution & Foresight",
        "version": "0.0.1",
        "milestone": "CHIEF ZERO",
        "environment": "development",
    }


@app.get("/tools", response_model=list[ToolDefinitionResponse])
def list_tools() -> list[ToolDefinitionResponse]:
    """List tools currently available through CHIEF's guarded registry."""

    return [
        ToolDefinitionResponse(
            name=definition.name,
            description=definition.description,
            risk=definition.risk.value,
            requires_approval=definition.requires_approval,
        )
        for definition in tool_registry.definitions()
    ]


@app.post("/tools/execute", response_model=ToolExecuteResponse)
def execute_tool(tool_request: ToolExecuteRequest) -> ToolExecuteResponse:
    """Execute a tool through CHIEF's policy and audit gates."""

    result = tool_registry.execute(
        tool_request.name,
        tool_request.arguments,
        approved=tool_request.approved,
    )

    return ToolExecuteResponse(
        success=result.success,
        content=result.content,
        data=result.data,
        error=result.error,
    )


@app.post("/chat", response_model=ChatResponse)
def chat(chat_request: ChatRequest) -> ChatResponse:
    try:
        session = session_store.get_or_create(
            chat_request.session_id
        )
    except KeyError:
        session = session_store.create()

    pending_action = tool_planner.pending_action(chat_request.message)
    if session.pending_tool_call is not None and pending_action is not None:
        pending_call = session.pending_tool_call
        session.pending_tool_call = None

        if pending_action == PendingAction.REJECT:
            response_text = f"Cancelled: I did not {pending_call.description}."
            response_status = "cancelled"
        else:
            result = tool_registry.execute(
                pending_call.tool_name,
                pending_call.arguments,
                approved=True,
            )
            response_text = result.content
            if result.error and not result.success:
                response_text = f"{result.content} {result.error}"
            response_status = "completed" if result.success else "failed"

        session.add_message("user", chat_request.message)
        session.add_message("assistant", response_text)
        return ChatResponse(
            response=response_text,
            provider="chief-tools",
            model="deterministic",
            session_id=session.id,
            status=response_status,
            tool_description=pending_call.description,
        )

    memory_command = memory_command_parser.parse(
        chat_request.message
    )

    if isinstance(memory_command, MemoryCommand):
        memory = memory_manager.remember(
            memory_command.content,
            source_type="user",
            source_description="Explicit remember command",
            confidence=1.0,
            importance=0.8,
        )

        response_text = f"Memory saved: {memory.content}"

        session.add_message(
            "user",
            chat_request.message,
        )
        session.add_message(
            "assistant",
            response_text,
        )

        return ChatResponse(
            response=response_text,
            provider="chief-memory",
            model="deterministic",
            session_id=session.id,
        )

    if isinstance(memory_command, CorrectMemoryCommand):
        try:
            old_memory = memory_manager.resolve_exact(
                memory_command.old_content
            )
        except ValueError:
            response_text = (
                "I found multiple active memories matching "
                "that exact content, so I did not change anything."
            )

            session.add_message("user", chat_request.message)
            session.add_message("assistant", response_text)

            return ChatResponse(
                response=response_text,
                provider="chief-memory",
                model="deterministic",
                session_id=session.id,
            )

        if old_memory is None:
            response_text = (
                "I could not find an active memory matching "
                "that exact content, so I did not change anything."
            )

            session.add_message("user", chat_request.message)
            session.add_message("assistant", response_text)

            return ChatResponse(
                response=response_text,
                provider="chief-memory",
                model="deterministic",
                session_id=session.id,
            )

        new_memory = memory_manager.correct(
            old_memory,
            memory_command.new_content,
            source_type="user",
            source_description="Explicit user correction",
            confidence=1.0,
        )

        response_text = (
            f"Memory corrected: {new_memory.content}"
        )

        session.add_message("user", chat_request.message)
        session.add_message("assistant", response_text)

        return ChatResponse(
            response=response_text,
            provider="chief-memory",
            model="deterministic",
            session_id=session.id,
        )

    if isinstance(memory_command, ForgetMemoryCommand):
        try:
            memory = memory_manager.resolve_exact(
                memory_command.content
            )
        except ValueError:
            response_text = (
                "I found multiple active memories matching "
                "that exact content, so I did not forget anything."
            )

            session.add_message("user", chat_request.message)
            session.add_message("assistant", response_text)

            return ChatResponse(
                response=response_text,
                provider="chief-memory",
                model="deterministic",
                session_id=session.id,
            )

        if memory is None:
            response_text = (
                "I could not find an active memory matching "
                "that exact content, so I did not forget anything."
            )

            session.add_message("user", chat_request.message)
            session.add_message("assistant", response_text)

            return ChatResponse(
                response=response_text,
                provider="chief-memory",
                model="deterministic",
                session_id=session.id,
            )

        memory_manager.forget(memory)

        response_text = (
            f"Memory forgotten: {memory.content}"
        )

        session.add_message("user", chat_request.message)
        session.add_message("assistant", response_text)

        return ChatResponse(
            response=response_text,
            provider="chief-memory",
            model="deterministic",
            session_id=session.id,
        )

    planned_call = tool_planner.plan(chat_request.message)
    if planned_call is not None:
        result = tool_registry.execute(
            planned_call.tool_name,
            planned_call.arguments,
        )

        if result.content == "Tool execution requires approval.":
            session.pending_tool_call = planned_call
            response_text = (
                f"Approval required to {planned_call.description}. "
                "Reply 'approve' to run it or 'cancel' to discard it."
            )
            response_status = "pending_approval"
        else:
            response_text = result.content
            if result.error and not result.success:
                response_text = f"{result.content} {result.error}"
            response_status = "completed" if result.success else "failed"

        session.add_message("user", chat_request.message)
        session.add_message("assistant", response_text)
        return ChatResponse(
            response=response_text,
            provider="chief-tools",
            model="deterministic",
            session_id=session.id,
            status=response_status,
            pending_action=(
                "approve_or_cancel"
                if response_status == "pending_approval"
                else None
            ),
            tool_description=planned_call.description,
        )

    memory_context = memory_manager.build_context(
        chat_request.message
    )

    conversation_context = session.build_context()

    context_parts = [SYSTEM_IDENTITY]

    if memory_context:
        context_parts.append(memory_context)

    if conversation_context:
        context_parts.append(conversation_context)

    system_prompt = "\n\n".join(context_parts)

    try:
        result = model_provider.generate(
            prompt=chat_request.message,
            system_prompt=system_prompt,
        )
    except RuntimeError as exc:
        response_text = str(exc)
        session.add_message("user", chat_request.message)
        session.add_message("assistant", response_text)
        return ChatResponse(
            response=response_text,
            provider="chief-system",
            model="unavailable",
            session_id=session.id,
            status="unavailable",
        )

    session.add_message(
        "user",
        chat_request.message,
    )

    session.add_message(
        "assistant",
        result.content,
    )

    return ChatResponse(
        response=result.content,
        provider=result.provider,
        model=result.model,
        session_id=session.id,
    )
