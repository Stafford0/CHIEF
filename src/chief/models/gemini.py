import json
import time
from urllib import error, request

from chief.models.base import ModelCapabilities, ModelPrivacy, ModelProvider, ModelResponse


class GeminiProvider(ModelProvider):
    """Cloud provider backed by Google's Gemini API, favored for deep-research tasks."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3-pro",
        base_url: str = "https://generativelanguage.googleapis.com",
        timeout: float = 120.0,
        max_response_bytes: int = 2_000_000,
    ) -> None:
        if not api_key:
            raise ValueError("Gemini provider requires an API key.")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            privacy=ModelPrivacy.CLOUD,
            structured_output=True,
            tool_calling=True,
            streaming=False,
            vision=True,
            audio=False,
            cost_tier=2,
            specialties=frozenset({"research"}),
        )

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> ModelResponse:
        if not prompt.strip():
            raise ValueError("Model prompt cannot be empty.")

        payload_data: dict[str, object] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        }
        if system_prompt:
            payload_data["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        payload = json.dumps(payload_data).encode("utf-8")

        http_request = request.Request(
            f"{self.base_url}/v1beta/models/{self.model}:generateContent?key={self.api_key}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        started = time.perf_counter()
        try:
            with request.urlopen(http_request, timeout=self.timeout) as response:
                raw = response.read(self.max_response_bytes + 1)
                if len(raw) > self.max_response_bytes:
                    raise RuntimeError("Gemini response exceeded the configured size limit.")
                data = json.loads(raw.decode("utf-8"))

        except TimeoutError as exc:
            raise RuntimeError(
                f"Gemini exceeded the {self.timeout:.0f}-second response timeout."
            ) from exc

        except error.HTTPError as exc:
            raise RuntimeError(f"Gemini returned HTTP error {exc.code}: {exc.reason}") from exc

        except error.URLError as exc:
            raise RuntimeError("CHIEF could not connect to Gemini.") from exc

        except json.JSONDecodeError as exc:
            raise RuntimeError("Gemini returned a response CHIEF could not decode.") from exc

        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise TypeError("Gemini returned an invalid response.")
        first = candidates[0]
        parts = first.get("content", {}).get("parts") if isinstance(first, dict) else None
        if not isinstance(parts, list):
            raise TypeError("Gemini returned an invalid response.")
        content = "".join(
            part.get("text", "") for part in parts if isinstance(part, dict)
        )

        return ModelResponse(
            content=content.strip(),
            provider=self.name,
            model=self.model,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
