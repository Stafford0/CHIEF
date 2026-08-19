from fastapi import FastAPI
from pydantic import BaseModel

from chief.core.identity import SYSTEM_IDENTITY
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

model_provider = OllamaProvider()
memory_store = SQLiteMemoryStore()
memory_manager = MemoryManager(memory_store)
memory_command_parser = MemoryCommandParser()


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str
    provider: str
    model: str


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

        return ChatResponse(
            response=f"Memory saved: {memory.content}",
            provider="chief-memory",
            model="deterministic",
        )

    if isinstance(memory_command, CorrectMemoryCommand):
        try:
            old_memory = memory_manager.resolve_exact(
                memory_command.old_content
            )
        except ValueError:
            return ChatResponse(
                response=(
                    "I found multiple active memories matching "
                    "that exact content, so I did not change anything."
                ),
                provider="chief-memory",
                model="deterministic",
            )

        if old_memory is None:
            return ChatResponse(
                response=(
                    "I could not find an active memory matching "
                    "that exact content, so I did not change anything."
                ),
                provider="chief-memory",
                model="deterministic",
            )

        new_memory = memory_manager.correct(
            old_memory,
            memory_command.new_content,
            source_type="user",
            source_description="Explicit user correction",
            confidence=1.0,
        )

        return ChatResponse(
            response=(
                f"Memory corrected: {new_memory.content}"
            ),
            provider="chief-memory",
            model="deterministic",
        )

    if isinstance(memory_command, ForgetMemoryCommand):
        try:
            memory = memory_manager.resolve_exact(
                memory_command.content
            )
        except ValueError:
            return ChatResponse(
                response=(
                    "I found multiple active memories matching "
                    "that exact content, so I did not forget anything."
                ),
                provider="chief-memory",
                model="deterministic",
            )

        if memory is None:
            return ChatResponse(
                response=(
                    "I could not find an active memory matching "
                    "that exact content, so I did not forget anything."
                ),
                provider="chief-memory",
                model="deterministic",
            )

        memory_manager.forget(memory)

        return ChatResponse(
            response=f"Memory forgotten: {memory.content}",
            provider="chief-memory",
            model="deterministic",
        )

    memory_context = memory_manager.build_context(
        chat_request.message
    )

    system_prompt = SYSTEM_IDENTITY

    if memory_context:
        system_prompt = (
            f"{SYSTEM_IDENTITY}\n\n"
            f"{memory_context}"
        )

    result = model_provider.generate(
        prompt=chat_request.message,
        system_prompt=system_prompt,
    )

    return ChatResponse(
        response=result.content,
        provider=result.provider,
        model=result.model,
    )