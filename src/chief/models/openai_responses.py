from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from chief.models.base import ModelCapabilities, ModelPrivacy, ModelProvider, ModelResponse
from chief.models.cloud_http import JsonTransport, post_json


class OpenAIResponsesProvider(ModelProvider):
    """Bounded OpenAI Responses API adapter with no tools or retained conversation state."""

    def __init__(
        self,
        *,
        model: str,
        api_key_provider: Callable[[], str | None],
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 120,
        max_response_bytes: int = 2_000_000,
        transport: JsonTransport = post_json,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if not model.strip():
            raise ValueError("OpenAI model cannot be empty.")
        if timeout_seconds <= 0:
            raise ValueError("OpenAI timeout must be positive.")
        if max_response_bytes < 1024:
            raise ValueError("OpenAI response limit is too small.")
        self.model = model.strip()
        self.api_key_provider = api_key_provider
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.transport = transport
        self.clock = clock

    @property
    def name(self) -> str:
        return "openai"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            privacy=ModelPrivacy.CLOUD,
            structured_output=True,
            tool_calling=False,
            streaming=False,
            vision=False,
            audio=False,
            cost_tier=2,
        )

    @staticmethod
    def _text(payload: dict[str, Any]) -> str:
        parts: list[str] = []
        output = payload.get("output")
        if not isinstance(output, list):
            raise RuntimeError("OpenAI response did not contain an output array.")
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "output_text":
                    text = block.get("text")
                    if isinstance(text, str) and text:
                        parts.append(text)
        if not parts:
            raise RuntimeError("OpenAI response contained no text output.")
        return "\n".join(parts)

    def generate(self, prompt: str, system_prompt: str | None = None) -> ModelResponse:
        api_key = self.api_key_provider()
        if api_key is None:
            raise RuntimeError("OpenAI API key is not configured.")
        input_items: list[dict[str, str]] = []
        if system_prompt:
            input_items.append({"role": "system", "content": system_prompt})
        input_items.append({"role": "user", "content": prompt})
        body = json.dumps(
            {
                "model": self.model,
                "input": input_items,
                "store": False,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        started = self.clock()
        status, payload = self.transport(
            f"{self.base_url}/responses",
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            body,
            self.timeout_seconds,
            self.max_response_bytes,
        )
        if status < 200 or status >= 300:
            error = payload.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            raise RuntimeError(
                f"OpenAI request failed with HTTP {status}: {message or 'provider error'}"
            )
        return ModelResponse(
            content=self._text(payload),
            provider=self.name,
            model=str(payload.get("model") or self.model),
            latency_ms=round((self.clock() - started) * 1000, 3),
        )
