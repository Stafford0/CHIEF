from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelResponse:
    content: str
    provider: str
    model: str


class ModelProvider(ABC):
    """Common interface for every intelligence provider CHIEF can use."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the provider's unique name."""

    @abstractmethod
    def generate(self, prompt: str) -> ModelResponse:
        """Generate a response for the supplied prompt."""