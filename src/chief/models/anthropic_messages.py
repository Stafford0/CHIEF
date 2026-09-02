from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from chief.models.base import ModelCapabilities, ModelPrivacy, ModelProvider, ModelResponse
from chief.models.cloud_http import JsonTransport, post_json


class AnthropicMessagesProvider(ModelProvider):
    """Bounded Anthropic Messages API adapter with no tools or retained conversation state."""

    def __init__(
        self,
        *,
        model: str,
        api_key_provider: Callable[[], str | None],
        max_tokens: int = 4096,
        base_url: str = "https://api.anthropic.com/v1",
        timeout_seconds: float = 120,
        max_response_bytes: int = 2_000_000,
        transport: JsonTransport = post_json,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if not model.strip():
            raise ValueError("Anthropic model cannot be empty.")
        if not 1 <= max_tokens <= 200_000:
            raise ValueError("Anthropic max_tokens must be between 1 and 200,000.")
        if timeout_seconds <= 0:
            raise ValueError("Anthropic timeout must be positive.")
        if max_response_bytes < 1024:
            raise ValueError("Anthropic response limit is too small.")
        self.model = model.strip()
        self.api_key_provider = api_key_provider
        self.max_tokens = max_tokens
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.transport = transport
        self.clock = clock

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            privacy=ModelPrivacy.CLOUD,
            structured_output=False,
            tool_calling=False,
            streaming=False,
            vision=False,
            audio=False,
            cost_tier=2,
        )

    @staticmethod
    def _text(payload: dict[str, Any]) -> str:
        content = payload.get("content")
        if not isinstance(content, list):
            raise TypeError("Anthropic response did not contain content blocks.")
        parts = [
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
            and block["text"]
        ]
        if not parts:
            raise RuntimeError("Anthropic response contained no text output.")
        return "\n".join(parts)

    def generate(self, prompt: str, system_prompt: str | None = None) -> ModelResponse:
        api_key = self.api_key_provider()
        if api_key is None:
            raise RuntimeError("Anthropic API key is not configured.")
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            payload["system"] = system_prompt
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        started = self.clock()
        status, response = self.transport(
            f"{self.base_url}/messages",
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            body,
            self.timeout_seconds,
            self.max_response_bytes,
        )
        if status < 200 or status >= 300:
            error = response.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            raise RuntimeError(
                f"Anthropic request failed with HTTP {status}: {message or 'provider error'}"
            )
        return ModelResponse(
            content=self._text(response),
            provider=self.name,
            model=str(response.get("model") or self.model),
            latency_ms=round((self.clock() - started) * 1000, 3),
        )
