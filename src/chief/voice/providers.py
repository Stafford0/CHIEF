from collections.abc import AsyncIterable, AsyncIterator
from typing import Protocol, runtime_checkable

from chief.voice.schema import (
    AudioChunk,
    AudioFrame,
    SpeechSynthesisRequest,
    TranscriptEvent,
    VoiceProcessingLocation,
)
from chief.voice.state_machine import CancellationToken


@runtime_checkable
class SpeechToTextProvider(Protocol):
    """Streaming speech-to-text adapter boundary."""

    @property
    def name(self) -> str:
        """Stable provider identifier."""
        ...

    @property
    def processing_location(self) -> VoiceProcessingLocation:
        """Where this provider processes audio."""
        ...

    def transcribe(
        self,
        frames: AsyncIterable[AudioFrame],
        *,
        cancellation: CancellationToken,
        language: str | None = None,
    ) -> AsyncIterator[TranscriptEvent]:
        """Yield partial and final transcript events for ordered audio frames."""
        ...


@runtime_checkable
class TextToSpeechProvider(Protocol):
    """Streaming text-to-speech adapter boundary."""

    @property
    def name(self) -> str:
        """Stable provider identifier."""
        ...

    @property
    def processing_location(self) -> VoiceProcessingLocation:
        """Where this provider processes text and audio."""
        ...

    def synthesize(
        self,
        request: SpeechSynthesisRequest,
        *,
        cancellation: CancellationToken,
    ) -> AsyncIterator[AudioChunk]:
        """Yield ordered audio chunks suitable for immediate playback."""
        ...
