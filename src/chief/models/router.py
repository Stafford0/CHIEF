from dataclasses import dataclass

from chief.models.base import ModelProvider, ModelResponse


@dataclass(frozen=True)
class RouteAttempt:
    provider: str
    succeeded: bool
    error: str | None = None


class ModelRouter:
    """Ordered, provider-independent router with bounded fallback."""

    def __init__(self, providers: list[ModelProvider]) -> None:
        if not providers:
            raise ValueError("At least one model provider is required.")
        self.providers = tuple(providers)
        self.last_attempts: list[RouteAttempt] = []

    def generate(self, prompt: str, system_prompt: str | None = None) -> ModelResponse:
        self.last_attempts = []
        errors: list[str] = []
        for provider in self.providers:
            provider_name = getattr(provider, "name", provider.__class__.__name__)
            try:
                result = provider.generate(prompt, system_prompt)
            except RuntimeError as exc:
                message = str(exc)
                errors.append(f"{provider_name}: {message}")
                self.last_attempts.append(RouteAttempt(provider_name, False, message))
                continue
            self.last_attempts.append(RouteAttempt(provider_name, True))
            return result
        if len(errors) == 1:
            raise RuntimeError(errors[0].split(": ", 1)[-1])
        raise RuntimeError("All model providers failed: " + "; ".join(errors))
