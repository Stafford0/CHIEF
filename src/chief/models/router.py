import time
from dataclasses import dataclass

from chief.models.base import (
    ModelCapabilities,
    ModelPrivacy,
    ModelProvider,
    ModelResponse,
    RouteRequirements,
)


@dataclass(frozen=True)
class RouteAttempt:
    provider: str
    succeeded: bool
    error: str | None = None
    skipped: bool = False


class ModelRouter:
    """Ordered, provider-independent router with bounded fallback."""

    def __init__(
        self,
        providers: list[ModelProvider],
        *,
        failure_threshold: int = 2,
        cooldown_seconds: float = 30,
        clock=time.monotonic,
    ) -> None:
        if not providers:
            raise ValueError("At least one model provider is required.")
        if failure_threshold < 1:
            raise ValueError("Model failure threshold must be positive.")
        if cooldown_seconds < 0:
            raise ValueError("Model cooldown cannot be negative.")
        self.providers = tuple(providers)
        self.last_attempts: list[RouteAttempt] = []
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._consecutive_failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    @staticmethod
    def _capabilities(provider: ModelProvider) -> ModelCapabilities:
        if isinstance(provider, ModelProvider):
            return provider.capabilities
        # Backward compatibility for code-owned/test-injected legacy providers that predate
        # ModelProvider. They are treated as local-only shims, never as cloud-capable adapters.
        return ModelCapabilities(privacy=ModelPrivacy.LOCAL)

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        *,
        requirements: RouteRequirements | None = None,
    ) -> ModelResponse:
        self.last_attempts = []
        errors: list[str] = []
        requirements = requirements or RouteRequirements()
        compatible = [
            provider
            for provider in self.providers
            if requirements.accepts(self._capabilities(provider))
        ]
        if not compatible:
            raise RuntimeError("No configured model provider satisfies the route requirements.")
        for provider in compatible:
            provider_name = getattr(provider, "name", provider.__class__.__name__)
            now = self._clock()
            if self._open_until.get(provider_name, 0) > now:
                message = "circuit breaker cooling down"
                errors.append(f"{provider_name}: {message}")
                self.last_attempts.append(RouteAttempt(provider_name, False, message, True))
                continue
            try:
                result = provider.generate(prompt, system_prompt)
            except RuntimeError as exc:
                message = str(exc)
                errors.append(f"{provider_name}: {message}")
                self.last_attempts.append(RouteAttempt(provider_name, False, message))
                failures = self._consecutive_failures.get(provider_name, 0) + 1
                self._consecutive_failures[provider_name] = failures
                if failures >= self.failure_threshold:
                    self._open_until[provider_name] = now + self.cooldown_seconds
                continue
            self._consecutive_failures[provider_name] = 0
            self._open_until.pop(provider_name, None)
            self.last_attempts.append(RouteAttempt(provider_name, True))
            return result
        if len(errors) == 1:
            raise RuntimeError(errors[0].split(": ", 1)[-1])
        raise RuntimeError("All model providers failed: " + "; ".join(errors))

    def provider_states(self) -> list[dict[str, object]]:
        now = self._clock()
        return [
            {
                "provider": provider.name,
                "privacy": self._capabilities(provider).privacy.value,
                "consecutive_failures": self._consecutive_failures.get(provider.name, 0),
                "circuit_open": self._open_until.get(provider.name, 0) > now,
            }
            for provider in self.providers
        ]
