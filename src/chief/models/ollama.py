import json
from urllib import error, request

from chief.models.base import ModelProvider, ModelResponse


class OllamaProvider(ModelProvider):
    """Local Ollama model provider for CHIEF."""

    def __init__(
        self,
        model: str = "llama3.1:8b",
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "ollama"

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> ModelResponse:
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

        try:
            with request.urlopen(
                http_request,
                timeout=self.timeout,
            ) as response:
                data = json.loads(response.read().decode("utf-8"))

        except TimeoutError as exc:
            raise RuntimeError(
                f"Ollama exceeded the {self.timeout:.0f}-second response timeout."
            ) from exc

        except error.HTTPError as exc:
            raise RuntimeError(
                f"Ollama returned HTTP error {exc.code}: {exc.reason}"
            ) from exc

        except error.URLError as exc:
            raise RuntimeError(
                "CHIEF could not connect to Ollama. "
                "Verify that the Ollama service is running."
            ) from exc

        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Ollama returned a response CHIEF could not decode."
            ) from exc

        content = data.get("response")

        if not isinstance(content, str):
            raise RuntimeError(
                "Ollama returned an invalid response."
            )

        return ModelResponse(
            content=content.strip(),
            provider=self.name,
            model=self.model,
        )