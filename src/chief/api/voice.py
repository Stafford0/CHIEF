from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from chief.voice import (
    AudioEncoding,
    AudioFrame,
    VoiceCancelled,
    VoiceSessionCoordinator,
    VoiceSessionEvent,
    VoiceState,
)

VoiceCoordinatorFactory = Callable[[], VoiceSessionCoordinator]


def _event_payload(event: VoiceSessionEvent) -> dict[str, object]:
    if event.kind == "state" and event.state is not None:
        return {
            "type": "state",
            "state": event.state.value,
            "reason": event.transition.reason if event.transition is not None else None,
        }
    if event.kind == "transcript" and event.transcript is not None:
        transcript = event.transcript
        return {
            "type": "transcript",
            "text": transcript.text,
            "kind": transcript.kind.value,
            "sequence": transcript.sequence,
            "provider": transcript.provider,
            "processing_location": transcript.processing_location.value,
            "confidence": transcript.confidence,
            "language": transcript.language,
        }
    if event.kind == "response" and event.response_text is not None:
        return {"type": "response", "text": event.response_text}
    if event.kind == "audio" and event.audio is not None:
        audio = event.audio
        return {
            "type": "audio",
            "sequence": audio.sequence,
            "provider": audio.provider,
            "processing_location": audio.processing_location.value,
            "sample_rate_hz": audio.sample_rate_hz,
            "channels": audio.channels,
            "encoding": audio.encoding.value,
            "duration_ms": audio.duration_ms,
            "is_final": audio.is_final,
            "data_base64": base64.b64encode(audio.data).decode("ascii"),
        }
    raise RuntimeError(f"Unsupported voice session event kind: {event.kind}")


def create_voice_router(
    *,
    coordinator_factory: VoiceCoordinatorFactory | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/voice", tags=["voice"])

    @router.get("/capabilities")
    def capabilities() -> dict[str, object]:
        return {
            "configured": coordinator_factory is not None,
            "transport": "websocket",
            "binary_audio_input": True,
            "streaming_transcripts": True,
            "streaming_audio_output": True,
            "cancellation": True,
            "raw_audio_retained": False,
            "wake_word": False,
            "full_duplex_barge_in": False,
        }

    @router.websocket("/stream")
    async def stream(websocket: WebSocket) -> None:
        await websocket.accept()
        if coordinator_factory is None:
            await websocket.send_json(
                {"type": "error", "detail": "Voice providers are not configured."}
            )
            await websocket.close(code=1013)
            return

        try:
            start = await websocket.receive_json()
        except (WebSocketDisconnect, json.JSONDecodeError, ValueError):
            await websocket.close(code=1003)
            return
        if start.get("type") != "start":
            await websocket.send_json({"type": "error", "detail": "First message must be start."})
            await websocket.close(code=1003)
            return

        try:
            sample_rate_hz = int(start.get("sample_rate_hz", 16_000))
            channels = int(start.get("channels", 1))
            encoding = AudioEncoding(str(start.get("encoding", AudioEncoding.PCM_S16LE.value)))
            rate = float(start.get("rate", 1.0))
        except (TypeError, ValueError):
            await websocket.send_json({"type": "error", "detail": "Invalid voice start metadata."})
            await websocket.close(code=1003)
            return
        if sample_rate_hz <= 0 or channels <= 0 or not 0.25 <= rate <= 4.0:
            await websocket.send_json({"type": "error", "detail": "Invalid voice start metadata."})
            await websocket.close(code=1003)
            return

        language = start.get("language")
        voice = start.get("voice")
        language = str(language) if language is not None else None
        voice = str(voice) if voice is not None else None

        coordinator = coordinator_factory()
        queue: asyncio.Queue[AudioFrame | None] = asyncio.Queue(maxsize=64)
        sequence = 0

        async def frames() -> AsyncIterator[AudioFrame]:
            while True:
                frame = await queue.get()
                if frame is None:
                    return
                yield frame

        async def emit() -> None:
            try:
                async for event in coordinator.run(
                    frames(),
                    language=language,
                    voice=voice,
                    rate=rate,
                ):
                    await websocket.send_json(_event_payload(event))
            except VoiceCancelled as exc:
                await websocket.send_json({"type": "cancelled", "detail": str(exc)})
            except Exception as exc:
                await websocket.send_json(
                    {"type": "error", "detail": str(exc) or exc.__class__.__name__}
                )

        emitter = asyncio.create_task(emit())
        input_closed = False
        try:
            while not emitter.done():
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect(int(message.get("code") or 1000))
                data = message.get("bytes")
                if data is not None:
                    await queue.put(
                        AudioFrame(
                            data=data,
                            sequence=sequence,
                            sample_rate_hz=sample_rate_hz,
                            channels=channels,
                            encoding=encoding,
                        )
                    )
                    sequence += 1
                    continue
                text = message.get("text")
                if text is None:
                    continue
                try:
                    control = json.loads(text)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "detail": "Invalid control JSON."})
                    continue
                control_type = control.get("type")
                if control_type == "end":
                    if not input_closed:
                        input_closed = True
                        await queue.put(None)
                    await emitter
                    return
                if control_type == "cancel":
                    if coordinator.machine.state not in {
                        VoiceState.IDLE,
                        VoiceState.CANCELLED,
                        VoiceState.ERROR,
                    }:
                        coordinator.cancel(str(control.get("reason") or "Operator cancelled voice."))
                    if not input_closed:
                        input_closed = True
                        await queue.put(None)
                    await emitter
                    return
                await websocket.send_json({"type": "error", "detail": "Unknown voice control message."})
        except WebSocketDisconnect:
            if coordinator.machine.state not in {
                VoiceState.IDLE,
                VoiceState.CANCELLED,
                VoiceState.ERROR,
            }:
                coordinator.cancel("Voice client disconnected.")
            if not input_closed:
                await queue.put(None)
        finally:
            if not emitter.done():
                await emitter

    return router
