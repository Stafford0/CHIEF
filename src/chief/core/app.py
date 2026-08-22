from uuid import UUID

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from chief.core.identity import SYSTEM_IDENTITY
from chief.core.session_store import SessionStore
from chief.memory.commands import (
    CorrectMemoryCommand,
    ForgetMemoryCommand,
    MemoryCommand,
    MemoryCommandParser,
)
from chief.memory.manager import MemoryManager
from chief.memory.sqlite import SQLiteMemoryStore
from chief.models.ollama import OllamaProvider


app = FastAPI(
    title="CHIEF",
    description="Cognitive Hub for Intelligence, Execution & Foresight",
    version="0.0.1",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_origin_regex=r"http://(?:192\.168|10\.\d|172\.(?:1[6-9]|2\d|3[01]))\.\d+\.\d+:5173",
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

model_provider = OllamaProvider()

memory_store = SQLiteMemoryStore()
memory_manager = MemoryManager(memory_store)
memory_command_parser = MemoryCommandParser()

session_store = SessionStore()


class ChatRequest(BaseModel):
    message: str
    session_id: UUID | None = None


class ChatResponse(BaseModel):
    response: str
    provider: str
    model: str
    session_id: UUID


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "online",
        "system": "CHIEF",
        "version": "0.0.1",
    }


@app.get("/system")
def system_info() -> dict[str, str]:
    return {
        "name": "CHIEF",
        "full_name": "Cognitive Hub for Intelligence, Execution & Foresight",
        "version": "0.0.1",
        "milestone": "CHIEF ZERO",
        "environment": "development",
    }


@app.post("/chat", response_model=ChatResponse)
def chat(chat_request: ChatRequest) -> ChatResponse:
    try:
        session = session_store.get_or_create(
            chat_request.session_id
        )
    except KeyError:
        session = session_store.create()

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

    result = model_provider.generate(
        prompt=chat_request.message,
        system_prompt=system_prompt,
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
