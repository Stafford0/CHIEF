from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator

import pytest

from chief.voice import (
    AudioChunk,
    AudioFrame,
    CancellationToken,
    SpeechSynthesisRequest,
    TranscriptEvent,
    TranscriptKind,
    VoiceCancelled,
    VoicePrivacyPolicy,
    VoiceProcessingLocation,
    VoiceSessionCoordinator,
    VoiceState,
)


class FakeSTT:
    name = "fake-stt"
    processing_location = VoiceProcessingLocation.LOCAL_HOST

    async def transcribe(
        self,
        frames: AsyncIterable[AudioFrame],
        *,
        cancellation: CancellationToken,
        language: str | None = None,
    ) -> AsyncIterator[TranscriptEvent]:
        async for frame in frames:
            cancellation.raise_if_cancelled()
            yield TranscriptEvent(
                text="Chief",
                kind=TranscriptKind.PARTIAL,
                sequence=frame.sequence,
                provider=self.name,
                processing_location=self.processing_location,
                language=language,
            )
        yield TranscriptEvent(
            text="Chief, status report.",
            kind=TranscriptKind.FINAL,
            sequence=1,
            provider=self.name,
            processing_location=self.processing_location,
            confidence=0.99,
            language=language,
        )


class FakeTTS:
    name = "fake-tts"
    processing_location = VoiceProcessingLocation.LOCAL_HOST

    async def synthesize(
        self,
        request: SpeechSynthesisRequest,
        *,
        cancellation: CancellationToken,
    ) -> AsyncIterator[AudioChunk]:
        cancellation.raise_if_cancelled()
        yield AudioChunk(
            data=b"chunk-1",
            sequence=0,
            provider=self.name,
            processing_location=self.processing_location,
            sample_rate_hz=24_000,
            duration_ms=100,
        )
        cancellation.raise_if_cancelled()
        yield AudioChunk(
            data=b"",
            sequence=1,
            provider=self.name,
            processing_location=self.processing_location,
            sample_rate_hz=24_000,
            duration_ms=0,
            is_final=True,
        )


class CloudSTT(FakeSTT):
    processing_location = VoiceProcessingLocation.CLOUD


async def frames() -> AsyncIterator[AudioFrame]:
    yield AudioFrame(data=b"\x00\x01", sequence=0, sample_rate_hz=16_000)


def test_voice_session_streams_transcript_response_audio_and_states() -> None:
    coordinator = VoiceSessionCoordinator(
        stt=FakeSTT(),
        tts=FakeTTS(),
        responder=lambda text: f"Received: {text}",
    )

    async def exercise():
        return [event async for event in coordinator.run(frames(), language="en-US")]

    events = asyncio.run(exercise())
    states = [event.state for event in events if event.kind == "state"]
    transcripts = [event.transcript for event in events if event.kind == "transcript"]
    responses = [event.response_text for event in events if event.kind == "response"]
    audio = [event.audio for event in events if event.kind == "audio"]

    assert states == [
        VoiceState.LISTENING,
        VoiceState.TRANSCRIBING,
        VoiceState.THINKING,
        VoiceState.SPEAKING,
        VoiceState.IDLE,
    ]
    assert [item.kind for item in transcripts if item is not None] == [
        TranscriptKind.PARTIAL,
        TranscriptKind.FINAL,
    ]
    assert responses == ["Received: Chief, status report."]
    assert len(audio) == 2
    assert audio[-1] is not None and audio[-1].is_final is True
    assert coordinator.machine.state is VoiceState.IDLE


def test_voice_session_rejects_cloud_provider_under_default_privacy() -> None:
    with pytest.raises(PermissionError, match="STT provider location cloud"):
        VoiceSessionCoordinator(
            stt=CloudSTT(),
            tts=FakeTTS(),
            responder=lambda text: text,
        )


def test_voice_session_can_explicitly_allow_cloud_processing() -> None:
    privacy = VoicePrivacyPolicy(
        allowed_processing_locations=frozenset(
            {VoiceProcessingLocation.LOCAL_HOST, VoiceProcessingLocation.CLOUD}
        )
    )
    coordinator = VoiceSessionCoordinator(
        stt=CloudSTT(),
        tts=FakeTTS(),
        responder=lambda text: text,
        privacy=privacy,
    )
    assert coordinator.privacy.permits(VoiceProcessingLocation.CLOUD)


def test_voice_session_cancellation_during_reasoning_is_propagated() -> None:
    coordinator: VoiceSessionCoordinator

    def responder(_text: str) -> str:
        coordinator.cancel("operator stop")
        return "should not speak"

    coordinator = VoiceSessionCoordinator(stt=FakeSTT(), tts=FakeTTS(), responder=responder)

    async def exercise() -> None:
        with pytest.raises(VoiceCancelled, match="operator stop"):
            _ = [event async for event in coordinator.run(frames())]

    asyncio.run(exercise())
    assert coordinator.machine.state is VoiceState.CANCELLED


def test_voice_session_requires_final_transcript() -> None:
    class PartialOnlySTT(FakeSTT):
        async def transcribe(
            self,
            frames: AsyncIterable[AudioFrame],
            *,
            cancellation: CancellationToken,
            language: str | None = None,
        ) -> AsyncIterator[TranscriptEvent]:
            async for frame in frames:
                yield TranscriptEvent(
                    text="partial",
                    kind=TranscriptKind.PARTIAL,
                    sequence=frame.sequence,
                    provider=self.name,
                    processing_location=self.processing_location,
                    language=language,
                )

    coordinator = VoiceSessionCoordinator(
        stt=PartialOnlySTT(),
        tts=FakeTTS(),
        responder=lambda text: text,
    )

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="final transcript"):
            _ = [event async for event in coordinator.run(frames())]

    asyncio.run(exercise())
    assert coordinator.machine.state is VoiceState.ERROR
