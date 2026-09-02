from __future__ import annotations

import inspect
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from chief.voice.providers import SpeechToTextProvider, TextToSpeechProvider
from chief.voice.schema import (
    AudioChunk,
    AudioFrame,
    SpeechSynthesisRequest,
    TranscriptEvent,
    VoicePrivacyPolicy,
    VoiceState,
)
from chief.voice.state_machine import VoiceCancelled, VoiceStateMachine, VoiceTransition

Responder = Callable[[str], str | Awaitable[str]]


@dataclass(frozen=True, slots=True)
class VoiceSessionEvent:
    kind: Literal["state", "transcript", "response", "audio"]
    state: VoiceState | None = None
    transition: VoiceTransition | None = None
    transcript: TranscriptEvent | None = None
    response_text: str | None = None
    audio: AudioChunk | None = None

    @classmethod
    def state_event(cls, transition: VoiceTransition) -> VoiceSessionEvent:
        return cls(kind="state", state=transition.current, transition=transition)


class VoiceSessionCoordinator:
    """One cancellable privacy-gated STT -> reasoning -> TTS session."""

    def __init__(
        self,
        *,
        stt: SpeechToTextProvider,
        tts: TextToSpeechProvider,
        responder: Responder,
        privacy: VoicePrivacyPolicy | None = None,
    ) -> None:
        self.stt = stt
        self.tts = tts
        self.responder = responder
        self.privacy = privacy or VoicePrivacyPolicy()
        self.machine = VoiceStateMachine()
        self._validate_provider_locations()

    def _validate_provider_locations(self) -> None:
        if not self.privacy.permits(self.stt.processing_location):
            raise PermissionError(
                f"Voice privacy policy does not permit STT provider location "
                f"{self.stt.processing_location.value}."
            )
        if not self.privacy.permits(self.tts.processing_location):
            raise PermissionError(
                f"Voice privacy policy does not permit TTS provider location "
                f"{self.tts.processing_location.value}."
            )

    def cancel(self, reason: str = "Operator cancelled voice session.") -> VoiceTransition:
        return self.machine.cancel(reason)

    def interrupt(self, reason: str = "Operator interrupted voice session.") -> VoiceTransition:
        return self.machine.interrupt(reason)

    async def _respond(self, transcript: str) -> str:
        result = self.responder(transcript)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError("Voice responder returned no text.")
        return result.strip()

    async def run(
        self,
        frames: AsyncIterable[AudioFrame],
        *,
        language: str | None = None,
        voice: str | None = None,
        rate: float = 1.0,
    ) -> AsyncIterator[VoiceSessionEvent]:
        if self.machine.state is not VoiceState.IDLE:
            raise RuntimeError("Voice session is already active.")
        self._validate_provider_locations()

        try:
            transition = self.machine.transition(VoiceState.LISTENING)
            yield VoiceSessionEvent.state_event(transition)
            transition = self.machine.transition(VoiceState.TRANSCRIBING)
            yield VoiceSessionEvent.state_event(transition)

            final_text: str | None = None
            async for transcript in self.stt.transcribe(
                frames,
                cancellation=self.machine.cancellation,
                language=language,
            ):
                self.machine.cancellation.raise_if_cancelled()
                if not self.privacy.permits(transcript.processing_location):
                    raise PermissionError(
                        "Speech-to-text emitted an event outside the permitted processing locations."
                    )
                yield VoiceSessionEvent(kind="transcript", transcript=transcript)
                if transcript.is_final:
                    final_text = transcript.text.strip()

            if not final_text:
                raise RuntimeError("Voice session ended without a final transcript.")

            transition = self.machine.transition(VoiceState.THINKING)
            yield VoiceSessionEvent.state_event(transition)
            response_text = await self._respond(final_text)
            self.machine.cancellation.raise_if_cancelled()
            yield VoiceSessionEvent(kind="response", response_text=response_text)

            transition = self.machine.transition(VoiceState.SPEAKING)
            yield VoiceSessionEvent.state_event(transition)
            async for chunk in self.tts.synthesize(
                SpeechSynthesisRequest(
                    text=response_text,
                    language=language,
                    voice=voice,
                    rate=rate,
                ),
                cancellation=self.machine.cancellation,
            ):
                self.machine.cancellation.raise_if_cancelled()
                if not self.privacy.permits(chunk.processing_location):
                    raise PermissionError(
                        "Text-to-speech emitted audio outside the permitted processing locations."
                    )
                yield VoiceSessionEvent(kind="audio", audio=chunk)

            transition = self.machine.transition(VoiceState.IDLE)
            yield VoiceSessionEvent.state_event(transition)
        except VoiceCancelled:
            if self.machine.state is not VoiceState.CANCELLED:
                transition = self.machine.cancel()
                yield VoiceSessionEvent.state_event(transition)
            raise
        except Exception as exc:
            if self.machine.state not in {VoiceState.ERROR, VoiceState.CANCELLED}:
                transition = self.machine.fail(str(exc) or exc.__class__.__name__)
                yield VoiceSessionEvent.state_event(transition)
            raise
