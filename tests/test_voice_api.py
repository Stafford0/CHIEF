from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chief.api.voice import create_voice_router
from chief.voice import (
    AudioChunk,
    AudioFrame,
    CancellationToken,
    SpeechSynthesisRequest,
    TranscriptEvent,
    TranscriptKind,
    VoiceProcessingLocation,
    VoiceSessionCoordinator,
)


class StreamSTT:
    name = "stream-stt"
    processing_location = VoiceProcessingLocation.LOCAL_HOST

    async def transcribe(
        self,
        frames: AsyncIterable[AudioFrame],
        *,
        cancellation: CancellationToken,
        language: str | None = None,
    ) -> AsyncIterator[TranscriptEvent]:
        count = 0
        async for frame in frames:
            cancellation.raise_if_cancelled()
            count += 1
            yield TranscriptEvent(
                text=f"partial-{count}",
                kind=TranscriptKind.PARTIAL,
                sequence=frame.sequence,
                provider=self.name,
                processing_location=self.processing_location,
                language=language,
            )
        yield TranscriptEvent(
            text="final request",
            kind=TranscriptKind.FINAL,
            sequence=count,
            provider=self.name,
            processing_location=self.processing_location,
            language=language,
        )


class StreamTTS:
    name = "stream-tts"
    processing_location = VoiceProcessingLocation.LOCAL_HOST

    async def synthesize(
        self,
        request: SpeechSynthesisRequest,
        *,
        cancellation: CancellationToken,
    ) -> AsyncIterator[AudioChunk]:
        cancellation.raise_if_cancelled()
        yield AudioChunk(
            data=request.text.encode("utf-8"),
            sequence=0,
            provider=self.name,
            processing_location=self.processing_location,
            sample_rate_hz=24_000,
            is_final=True,
        )


def coordinator_factory() -> VoiceSessionCoordinator:
    return VoiceSessionCoordinator(
        stt=StreamSTT(),
        tts=StreamTTS(),
        responder=lambda text: f"answer:{text}",
    )


def test_voice_capabilities_report_real_configuration_state() -> None:
    app = FastAPI()
    app.include_router(create_voice_router())
    client = TestClient(app)

    capabilities = client.get("/voice/capabilities").json()
    assert capabilities["configured"] is False
    assert capabilities["transport"] == "websocket"
    assert capabilities["raw_audio_retained"] is False
    assert capabilities["wake_word"] is False
    assert capabilities["full_duplex_barge_in"] is False


def test_voice_websocket_streams_session_events() -> None:
    app = FastAPI()
    app.include_router(create_voice_router(coordinator_factory=coordinator_factory))
    client = TestClient(app)

    with client.websocket_connect("/voice/stream") as websocket:
        websocket.send_json(
            {
                "type": "start",
                "sample_rate_hz": 16_000,
                "channels": 1,
                "encoding": "pcm_s16le",
                "language": "en-US",
            }
        )
        websocket.send_bytes(b"\x00\x01")
        websocket.send_bytes(b"\x02\x03")
        websocket.send_json({"type": "end"})

        messages: list[dict[str, object]] = []
        while True:
            message = websocket.receive_json()
            messages.append(message)
            if message.get("type") == "state" and message.get("state") == "idle":
                break

    states = [message["state"] for message in messages if message.get("type") == "state"]
    transcripts = [message for message in messages if message.get("type") == "transcript"]
    responses = [message for message in messages if message.get("type") == "response"]
    audio = [message for message in messages if message.get("type") == "audio"]

    assert states == ["listening", "transcribing", "thinking", "speaking", "idle"]
    assert [item["kind"] for item in transcripts] == ["partial", "partial", "final"]
    assert responses == [{"type": "response", "text": "answer:final request"}]
    assert audio[0]["provider"] == "stream-tts"
    assert audio[0]["data_base64"]
    assert audio[0]["is_final"] is True


def test_voice_websocket_rejects_bad_start_metadata() -> None:
    app = FastAPI()
    app.include_router(create_voice_router(coordinator_factory=coordinator_factory))
    client = TestClient(app)

    with client.websocket_connect("/voice/stream") as websocket:
        websocket.send_json({"type": "start", "sample_rate_hz": 0})
        message = websocket.receive_json()
        assert message["type"] == "error"
        assert "Invalid voice start metadata" in str(message["detail"])
