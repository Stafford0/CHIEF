import json
import time
from urllib import error, request

from chief.models.base import ModelCapabilities, ModelPrivacy, ModelProvider, ModelResponse


class OpenAIProvider(ModelProvider):
    """Cloud provider backed by OpenAI's Chat Completions API, favored for voice/briefing tasks."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-5.1",
        base_url: str = "https://api.openai.com",
        timeout: float = 120.0,
        max_response_bytes: int = 2_000_000,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAI provider requires an API key.")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes

    @property
    def name(self) -> str:
        return "openai"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            privacy=ModelPrivacy.CLOUD,
            structured_output=True,
            tool_calling=True,
            streaming=False,
            vision=True,
            audio=True,
            cost_tier=2,
            specialties=frozenset({"voice", "briefing"}),
        )

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> ModelResponse:
        if not prompt.strip():
            raise ValueError("Model prompt cannot be empty.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = json.dumps({"model": self.model, "messages": messages}).encode("utf-8")

        http_request = request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        started = time.perf_counter()
        try:
            with request.urlopen(http_request, timeout=self.timeout) as response:
                raw = response.read(self.max_response_bytes + 1)
                if len(raw) > self.max_response_bytes:
                    raise RuntimeError("OpenAI response exceeded the configured size limit.")
                data = json.loads(raw.decode("utf-8"))

        except TimeoutError as exc:
            raise RuntimeError(
                f"OpenAI exceeded the {self.timeout:.0f}-second response timeout."
            ) from exc

        except error.HTTPError as exc:
            raise RuntimeError(f"OpenAI returned HTTP error {exc.code}: {exc.reason}") from exc

        except error.URLError as exc:
            raise RuntimeError("CHIEF could not connect to OpenAI.") from exc

        except json.JSONDecodeError as exc:
            raise RuntimeError("OpenAI returned a response CHIEF could not decode.") from exc

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise TypeError("OpenAI returned an invalid response.")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise TypeError("OpenAI returned an invalid response.")

        return ModelResponse(
            content=content.strip(),
            provider=self.name,
            model=self.model,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
