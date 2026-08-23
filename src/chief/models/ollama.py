import json
import time
from urllib import error, request

from chief.models.base import ModelCapabilities, ModelPrivacy, ModelProvider, ModelResponse


class OllamaProvider(ModelProvider):
    """Local Ollama model provider for CHIEF."""

    def __init__(
        self,
        model: str = "llama3.1:8b",
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 120.0,
        max_response_bytes: int = 2_000_000,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            privacy=ModelPrivacy.LOCAL,
            structured_output=False,
            tool_calling=False,
            streaming=False,
            vision=False,
            audio=False,
            cost_tier=0,
        )

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> ModelResponse:
        if not prompt.strip():
            raise ValueError("Model prompt cannot be empty.")
        payload_data = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }

        if system_prompt:
            payload_data["system"] = system_prompt

        payload = json.dumps(payload_data).encode("utf-8")

        http_request = request.Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        started = time.perf_counter()
        try:
            with request.urlopen(
                http_request,
                timeout=self.timeout,
            ) as response:
                raw = response.read(self.max_response_bytes + 1)
                if len(raw) > self.max_response_bytes:
                    raise RuntimeError("Ollama response exceeded the configured size limit.")
                data = json.loads(raw.decode("utf-8"))

        except TimeoutError as exc:
            raise RuntimeError(
                f"Ollama exceeded the {self.timeout:.0f}-second response timeout."
            ) from exc

        except error.HTTPError as exc:
            raise RuntimeError(f"Ollama returned HTTP error {exc.code}: {exc.reason}") from exc

        except error.URLError as exc:
            raise RuntimeError(
                "CHIEF could not connect to Ollama. Verify that the Ollama service is running."
            ) from exc

        except json.JSONDecodeError as exc:
            raise RuntimeError("Ollama returned a response CHIEF could not decode.") from exc

        content = data.get("response")

        if not isinstance(content, str):
            raise TypeError("Ollama returned an invalid response.")

        return ModelResponse(
            content=content.strip(),
            provider=self.name,
            model=self.model,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
