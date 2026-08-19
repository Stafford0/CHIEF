from fastapi import FastAPI
from pydantic import BaseModel

from chief.models.ollama import OllamaProvider
from chief.core.identity import SYSTEM_IDENTITY

app = FastAPI(
    title="CHIEF",
    description="Cognitive Hub for Intelligence, Execution & Foresight",
    version="0.0.1",
)

model_provider = OllamaProvider()


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
    result = model_provider.generate(
    prompt=chat_request.message,
    system_prompt=SYSTEM_IDENTITY,
)

    return ChatResponse(
        response=result.content,
        provider=result.provider,
        model=result.model,
    )