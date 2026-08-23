import asyncio
from collections.abc import AsyncIterable, AsyncIterator

import pytest

from chief.voice import (
    AudioChunk,
    AudioEncoding,
    AudioFrame,
    CancellationToken,
    InvalidVoiceTransition,
    SpeechSynthesisRequest,
    SpeechToTextProvider,
    TextToSpeechProvider,
    TranscriptEvent,
    TranscriptKind,
    VoiceCancelled,
    VoicePrivacyPolicy,
    VoiceProcessingLocation,
    VoiceState,
    VoiceStateMachine,
)


class FakeSpeechToText:
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
            confidence=0.98,
            language=language,
            start_ms=0,
            end_ms=900,
        )


class FakeTextToSpeech:
    name = "fake-tts"
    processing_location = VoiceProcessingLocation.ON_DEVICE

    async def synthesize(
        self,
        request: SpeechSynthesisRequest,
        *,
        cancellation: CancellationToken,
    ) -> AsyncIterator[AudioChunk]:
        cancellation.raise_if_cancelled()
        yield AudioChunk(
            data=request.text.encode(),
            sequence=0,
            provider=self.name,
            processing_location=self.processing_location,
            sample_rate_hz=24_000,
            duration_ms=200,
            is_final=True,
        )


async def one_audio_frame() -> AsyncIterator[AudioFrame]:
    yield AudioFrame(data=b"\x00\x01", sequence=0, sample_rate_hz=16_000)


def test_voice_states_are_explicit() -> None:
    assert {state.value for state in VoiceState} == {
        "idle",
        "listening",
        "transcribing",
        "thinking",
        "speaking",
        "interrupted",
        "cancelled",
        "error",
    }


def test_happy_path_transitions_are_recorded() -> None:
    machine = VoiceStateMachine()

    for state in (
        VoiceState.LISTENING,
        VoiceState.TRANSCRIBING,
        VoiceState.THINKING,
        VoiceState.SPEAKING,
        VoiceState.IDLE,
    ):
        machine.transition(state)

    assert machine.state == VoiceState.IDLE
    assert [transition.current for transition in machine.history] == [
        VoiceState.LISTENING,
        VoiceState.TRANSCRIBING,
        VoiceState.THINKING,
        VoiceState.SPEAKING,
        VoiceState.IDLE,
    ]


def test_illegal_transition_is_rejected_without_mutation() -> None:
    machine = VoiceStateMachine()

    with pytest.raises(InvalidVoiceTransition, match="idle to speaking"):
        machine.transition(VoiceState.SPEAKING)

    assert machine.state == VoiceState.IDLE
    assert machine.history == ()


def test_interruption_can_return_to_listening() -> None:
    machine = VoiceStateMachine()
    machine.transition(VoiceState.LISTENING)
    machine.transition(VoiceState.TRANSCRIBING)
    machine.transition(VoiceState.THINKING)
    machine.transition(VoiceState.SPEAKING)

    machine.interrupt("User started speaking.")
    machine.transition(VoiceState.LISTENING)

    assert machine.state == VoiceState.LISTENING
    assert machine.history[-2].reason == "User started speaking."


def test_cancellation_is_cooperative_idempotent_and_resettable() -> None:
    machine = VoiceStateMachine()
    machine.transition(VoiceState.LISTENING)
    token = machine.cancellation

    transition = machine.cancel("Operator stopped voice.")

    assert transition.current == VoiceState.CANCELLED
    assert token.cancelled is True
    assert token.reason == "Operator stopped voice."
    assert token.cancel("Different reason") is False
    with pytest.raises(VoiceCancelled, match="Operator stopped voice"):
        token.raise_if_cancelled()

    machine.reset()
    assert machine.state == VoiceState.IDLE
    assert machine.cancellation is not token
    assert machine.cancellation.cancelled is False


def test_illegal_cancellation_does_not_poison_idle_token() -> None:
    machine = VoiceStateMachine()

    with pytest.raises(InvalidVoiceTransition, match="idle to cancelled"):
        machine.cancel()

    assert machine.state == VoiceState.IDLE
    assert machine.cancellation.cancelled is False


def test_raw_audio_retention_is_off_and_local_processing_is_default() -> None:
    policy = VoicePrivacyPolicy()

    assert policy.retain_raw_audio is False
    assert policy.permits(VoiceProcessingLocation.ON_DEVICE)
    assert policy.permits(VoiceProcessingLocation.LOCAL_HOST)
    assert not policy.permits(VoiceProcessingLocation.CLOUD)


def test_transcript_and_audio_metadata_are_validated() -> None:
    partial = TranscriptEvent(
        text="status",
        kind=TranscriptKind.PARTIAL,
        sequence=0,
        provider="test",
        processing_location=VoiceProcessingLocation.ON_DEVICE,
        confidence=0.5,
    )
    final_chunk = AudioChunk(
        data=b"",
        sequence=1,
        provider="test",
        processing_location=VoiceProcessingLocation.ON_DEVICE,
        sample_rate_hz=24_000,
        encoding=AudioEncoding.OPUS,
        is_final=True,
    )

    assert partial.is_final is False
    assert final_chunk.is_final is True
    with pytest.raises(ValueError, match="confidence"):
        TranscriptEvent(
            text="invalid",
            kind=TranscriptKind.FINAL,
            sequence=1,
            provider="test",
            processing_location=VoiceProcessingLocation.CLOUD,
            confidence=1.1,
        )
    with pytest.raises(ValueError, match="non-final"):
        AudioChunk(
            data=b"",
            sequence=0,
            provider="test",
            processing_location=VoiceProcessingLocation.LOCAL_HOST,
            sample_rate_hz=16_000,
        )


def test_provider_protocols_support_streaming_without_vendor_dependencies() -> None:
    stt = FakeSpeechToText()
    tts = FakeTextToSpeech()

    assert isinstance(stt, SpeechToTextProvider)
    assert isinstance(tts, TextToSpeechProvider)

    async def exercise() -> None:
        transcripts = [
            event
            async for event in stt.transcribe(
                one_audio_frame(), cancellation=CancellationToken(), language="en-US"
            )
        ]
        chunks = [
            chunk
            async for chunk in tts.synthesize(
                SpeechSynthesisRequest("Systems nominal."), cancellation=CancellationToken()
            )
        ]

        assert [event.kind for event in transcripts] == [
            TranscriptKind.PARTIAL,
            TranscriptKind.FINAL,
        ]
        assert chunks[0].provider == "fake-tts"
        assert chunks[0].duration_ms == 200

    asyncio.run(exercise())
