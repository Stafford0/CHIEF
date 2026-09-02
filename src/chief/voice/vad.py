from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from chief.voice.schema import AudioEncoding, AudioFrame


@dataclass(frozen=True, slots=True)
class VoiceActivity:
    speech: bool
    level: float
    sequence: int


class LocalVoiceActivityDetector:
    """Small on-device PCM energy detector used for telemetry, never authorization."""

    def __init__(self, *, speech_threshold: float = 0.02) -> None:
        if not 0.0 < speech_threshold < 1.0:
            raise ValueError("speech_threshold must be between 0 and 1")
        self.speech_threshold = speech_threshold

    def inspect(self, frame: AudioFrame) -> VoiceActivity | None:
        if frame.encoding is not AudioEncoding.PCM_S16LE:
            return None
        if len(frame.data) % 2:
            raise ValueError("PCM S16LE audio frame must contain complete 16-bit samples")
        sample_count = len(frame.data) // 2
        if sample_count == 0:
            return VoiceActivity(speech=False, level=0.0, sequence=frame.sequence)
        square_sum = 0.0
        for (sample,) in struct.iter_unpack("<h", frame.data):
            normalized = sample / 32768.0
            square_sum += normalized * normalized
        rms = math.sqrt(square_sum / sample_count)
        level = min(1.0, rms)
        return VoiceActivity(
            speech=level >= self.speech_threshold,
            level=level,
            sequence=frame.sequence,
        )
