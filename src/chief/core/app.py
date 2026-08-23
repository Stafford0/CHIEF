import logging
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from starlette.requests import Request

from chief.core.config import Settings
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
from chief.models.router import ModelRouter
from chief.tools.registry import ToolRegistry, create_standard_registry

settings = Settings.from_env()
app = FastAPI(
    title="CHIEF",
    description="Cognitive Hub for Intelligence, Execution & Foresight",
    version="0.0.1",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_origin_regex=(
        r"http://(?:192\.168|10\.\d|172\.(?:1[6-9]|2\d|3[01]))\.\d+\.\d+:5173"
        if settings.allow_private_lan_ui
        else None
    ),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

logger = logging.getLogger("chief.api")


@app.middleware("http")
async def operational_headers(request: Request, call_next):
    """Attach correlation, timing, and browser hardening headers."""
    request_id = request.headers.get("x-request-id", str(uuid4()))[:128]
    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store"
    logger.info(
        "request_complete",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": round(duration_ms, 3),
        },
    )
    return response


model_provider = OllamaProvider(
    model=settings.ollama_model,
    base_url=settings.ollama_url,
    timeout=settings.model_timeout_seconds,
    max_response_bytes=settings.max_model_response_bytes,
)
model_router = ModelRouter([model_provider])


def generate_model(prompt: str, system_prompt: str):
    """Route providers while preserving runtime/test provider replacement."""
    router = model_router
    if model_provider not in router.providers:
        router = ModelRouter([model_provider])
    return router.generate(prompt, system_prompt)


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


@app.get("/ready")
def readiness() -> dict[str, str]:
    """Report whether required local components can accept work."""
    return {"status": "ready", "memory": "online", "tool_registry": "online"}


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


@app.get("/dashboard")
def dashboard_info() -> dict[str, Any]:
    """Return live host/runtime telemetry used by the CHIEF command center UI."""
    from chief.core.dashboard import collect_dashboard_snapshot

    snapshot = collect_dashboard_snapshot(PROJECT_ROOT)
    definitions = list(tool_registry.definitions())
    audit_events = tool_registry.audit_log.events()[-8:]
    snapshot["runtime"] = {
        "api_status": "online",
        "active_model": model_provider.model,
        "model_provider": model_provider.name,
        "sessions": session_store.count(),
        "session_details": session_store.summaries(),
        "tools": [
            {
                "name": definition.name,
                "description": definition.description,
                "risk": definition.risk.value,
                "requires_approval": definition.requires_approval,
            }
            for definition in definitions
        ],
        "permissions": {
            "approval_gated": sum(1 for item in definitions if item.requires_approval),
            "automatic": sum(1 for item in definitions if not item.requires_approval),
        },
        "agents": [
            {"name": "CHIEF Core", "status": "operational", "kind": "orchestrator"},
            {"name": "Memory", "status": "operational", "kind": "service"},
            {"name": "Tool Planner", "status": "operational", "kind": "agent"},
            {
                "name": "Ollama",
                "status": "operational" if snapshot["ollama"]["online"] else "offline",
                "kind": "model service",
            },
        ],
        "queued_tasks": session_store.pending_tool_calls(),
        "recent_executions": [
            {
                "name": event.tool_name,
                "status": "success" if event.success else "failed",
                "decision": event.decision,
                "approved": event.approved,
                "timestamp": event.timestamp.isoformat(),
                "error": event.error,
            }
            for event in reversed(audit_events)
        ],
        "projects": snapshot.get("projects", []),
        "objectives": [
            {"name": "CHIEF command center reference parity", "status": "active"},
            {"name": "Reliable local AI runtime", "status": "active"},
            {"name": "Phone-accessible control", "status": "active"},
        ],
    }
    return snapshot


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
        session = session_store.get_or_create(chat_request.session_id)
    except KeyError:
        session = session_store.create()

    pending_action = tool_planner.pending_action(chat_request.message)
    if session.pending_tool_call is not None and pending_action is not None:
        pending = session.take_pending_tool()
        assert pending is not None
        pending_call = pending.call

        if pending.expired():
            response_text = f"Approval expired: I did not {pending_call.description}."
            response_status = "expired"
        elif pending_action == PendingAction.REJECT:
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

    memory_command = memory_command_parser.parse(chat_request.message)

    if isinstance(memory_command, MemoryCommand):
        memory = memory_manager.remember(
            memory_command.content,
            source_type="user",
            source_description="Explicit remember command",
            confidence=1.0,
            importance=0.8,
        )
        response_text = f"Memory saved: {memory.content}"
        session.add_message("user", chat_request.message)
        session.add_message("assistant", response_text)
        return ChatResponse(
            response=response_text,
            provider="chief-memory",
            model="deterministic",
            session_id=session.id,
        )

    if isinstance(memory_command, CorrectMemoryCommand):
        try:
            old_memory = memory_manager.resolve_exact(memory_command.old_content)
        except ValueError:
            response_text = (
                "I found multiple active memories matching that exact content, "
                "so I did not change anything."
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
                "I could not find an active memory matching that exact content, "
                "so I did not change anything."
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
        response_text = f"Memory corrected: {new_memory.content}"
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
            memory = memory_manager.resolve_exact(memory_command.content)
        except ValueError:
            response_text = (
                "I found multiple active memories matching that exact content, "
                "so I did not forget anything."
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
                "I could not find an active memory matching that exact content, "
                "so I did not forget anything."
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
        response_text = f"Memory forgotten: {memory.content}"
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
        result = tool_registry.execute(planned_call.tool_name, planned_call.arguments)

        if result.content == "Tool execution requires approval.":
            proposal = session.propose_tool(planned_call)
            response_text = (
                f"Approval required to {planned_call.description}. "
                f"Review code {proposal.digest[:12]}. Reply 'approve' within five minutes "
                "to run this exact action or 'cancel' to discard it."
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
            pending_action=("approve_or_cancel" if response_status == "pending_approval" else None),
            tool_description=planned_call.description,
        )

    memory_context = memory_manager.build_context(chat_request.message)
    conversation_context = session.build_context()
    context_parts = [SYSTEM_IDENTITY]
    if memory_context:
        context_parts.append(memory_context)
    if conversation_context:
        context_parts.append(conversation_context)
    system_prompt = "\n\n".join(context_parts)

    try:
        result = generate_model(
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

    session.add_message("user", chat_request.message)
    session.add_message("assistant", result.content)
    return ChatResponse(
        response=result.content,
        provider=result.provider,
        model=result.model,
        session_id=session.id,
    )
