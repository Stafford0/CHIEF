from fastapi import FastAPI
from pydantic import BaseModel

from chief.models.ollama import OllamaProvider
from chief.core.identity import SYSTEM_IDENTITY
from chief.memory.manager import MemoryManager
from chief.memory.sqlite import SQLiteMemoryStore

app = FastAPI(
    title="CHIEF",
    description="Cognitive Hub for Intelligence, Execution & Foresight",
    version="0.0.1",
)

model_provider = OllamaProvider()
memory_store = SQLiteMemoryStore()
memory_manager = MemoryManager(memory_store)


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