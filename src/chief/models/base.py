from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class ModelPrivacy(str, Enum):
    LOCAL = "local"
    PRIVATE_NETWORK = "private_network"
    CLOUD = "cloud"


@dataclass(frozen=True)
class ModelCapabilities:
    """Provider facts used for explicit, privacy-aware routing."""

    privacy: ModelPrivacy = ModelPrivacy.CLOUD
    structured_output: bool = False
    tool_calling: bool = False
    streaming: bool = False
    vision: bool = False
    audio: bool = False
    cost_tier: int = 1


@dataclass(frozen=True)
class RouteRequirements:
    allowed_privacy: frozenset[ModelPrivacy] = frozenset(ModelPrivacy)
    structured_output: bool = False
    tool_calling: bool = False
    streaming: bool = False
    vision: bool = False
    audio: bool = False
    max_cost_tier: int | None = None

    def accepts(self, capabilities: ModelCapabilities) -> bool:
        if capabilities.privacy not in self.allowed_privacy:
            return False
        for field_name in ("structured_output", "tool_calling", "streaming", "vision", "audio"):
            if getattr(self, field_name) and not getattr(capabilities, field_name):
                return False
        return self.max_cost_tier is None or capabilities.cost_tier <= self.max_cost_tier


@dataclass(frozen=True)
class ModelResponse:
    content: str
    provider: str
    model: str
    latency_ms: float | None = None


class ModelProvider(ABC):
    """Common interface for every intelligence provider CHIEF can use."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the provider's unique name."""

    @property
    def capabilities(self) -> ModelCapabilities:
        """Declare routing capabilities; adapters should override conservative defaults."""
        return ModelCapabilities()

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> ModelResponse:
        """Generate a response using an optional system prompt."""
