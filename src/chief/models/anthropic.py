import json
import time
from urllib import error, request

from chief.models.base import ModelCapabilities, ModelPrivacy, ModelProvider, ModelResponse

_API_VERSION = "2023-06-01"


class AnthropicProvider(ModelProvider):
    """Cloud provider backed by Anthropic's Messages API, favored for coding/execution tasks."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-opus-5",
        base_url: str = "https://api.anthropic.com",
        timeout: float = 120.0,
        max_response_bytes: int = 2_000_000,
        max_output_tokens: int = 4096,
    ) -> None:
        if not api_key:
            raise ValueError("Anthropic provider requires an API key.")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.max_output_tokens = max_output_tokens

    @property
    def name(self) -> str:
        return "anthropic"

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
            specialties=frozenset({"coding", "execution"}),
        )

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> ModelResponse:
        if not prompt.strip():
            raise ValueError("Model prompt cannot be empty.")

        payload_data: dict[str, object] = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            payload_data["system"] = system_prompt

        payload = json.dumps(payload_data).encode("utf-8")

        http_request = request.Request(
            f"{self.base_url}/v1/messages",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": _API_VERSION,
            },
            method="POST",
        )

        started = time.perf_counter()
        try:
            with request.urlopen(http_request, timeout=self.timeout) as response:
                raw = response.read(self.max_response_bytes + 1)
                if len(raw) > self.max_response_bytes:
                    raise RuntimeError("Anthropic response exceeded the configured size limit.")
                data = json.loads(raw.decode("utf-8"))

        except TimeoutError as exc:
            raise RuntimeError(
                f"Anthropic exceeded the {self.timeout:.0f}-second response timeout."
            ) from exc

        except error.HTTPError as exc:
            raise RuntimeError(f"Anthropic returned HTTP error {exc.code}: {exc.reason}") from exc

        except error.URLError as exc:
            raise RuntimeError("CHIEF could not connect to Anthropic.") from exc

        except json.JSONDecodeError as exc:
            raise RuntimeError("Anthropic returned a response CHIEF could not decode.") from exc

        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise TypeError("Anthropic returned an invalid response.")
        content = "".join(
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )

        return ModelResponse(
            content=content.strip(),
            provider=self.name,
            model=self.model,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
