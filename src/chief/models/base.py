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
def generate(
    self,
    prompt: str,
    system_prompt: str | None = None,
) -> ModelResponse:
    """Generate a response using an optional system prompt."""