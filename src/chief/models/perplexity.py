import json
import time
from urllib import error, request

from chief.models.base import ModelCapabilities, ModelPrivacy, ModelProvider, ModelResponse


class PerplexityProvider(ModelProvider):
    """Cloud provider backed by Perplexity's Sonar API, favored for real-time signal lookups."""

    def __init__(
        self,
        api_key: str,
        model: str = "sonar-pro",
        base_url: str = "https://api.perplexity.ai",
        timeout: float = 120.0,
        max_response_bytes: int = 2_000_000,
    ) -> None:
        if not api_key:
            raise ValueError("Perplexity provider requires an API key.")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes

    @property
    def name(self) -> str:
        return "perplexity"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            privacy=ModelPrivacy.CLOUD,
            structured_output=False,
            tool_calling=False,
            streaming=False,
            vision=False,
            audio=False,
            cost_tier=1,
            specialties=frozenset({"signals"}),
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
            f"{self.base_url}/chat/completions",
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
                    raise RuntimeError("Perplexity response exceeded the configured size limit.")
                data = json.loads(raw.decode("utf-8"))

        except TimeoutError as exc:
            raise RuntimeError(
                f"Perplexity exceeded the {self.timeout:.0f}-second response timeout."
            ) from exc

        except error.HTTPError as exc:
            raise RuntimeError(f"Perplexity returned HTTP error {exc.code}: {exc.reason}") from exc

        except error.URLError as exc:
            raise RuntimeError("CHIEF could not connect to Perplexity.") from exc

        except json.JSONDecodeError as exc:
            raise RuntimeError("Perplexity returned a response CHIEF could not decode.") from exc

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise TypeError("Perplexity returned an invalid response.")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise TypeError("Perplexity returned an invalid response.")

        return ModelResponse(
            content=content.strip(),
            provider=self.name,
            model=self.model,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
