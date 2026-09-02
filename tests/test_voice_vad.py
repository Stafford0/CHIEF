from __future__ import annotations

import struct

from chief.voice import AudioEncoding, AudioFrame, LocalVoiceActivityDetector


def _frame(samples: list[int], *, sequence: int = 0) -> AudioFrame:
    return AudioFrame(
        data=b"".join(struct.pack("<h", sample) for sample in samples),
        sequence=sequence,
        sample_rate_hz=16_000,
        encoding=AudioEncoding.PCM_S16LE,
    )


def test_local_vad_marks_silence_and_speech() -> None:
    vad = LocalVoiceActivityDetector(speech_threshold=0.02)

    silence = vad.inspect(_frame([0] * 160, sequence=1))
    speech = vad.inspect(_frame([8_000] * 160, sequence=2))

    assert silence is not None and silence.speech is False
    assert speech is not None and speech.speech is True
    assert speech.level > silence.level
    assert speech.sequence == 2


def test_local_vad_ignores_non_pcm_without_decoding_it() -> None:
    vad = LocalVoiceActivityDetector()
    frame = AudioFrame(
        data=b"opaque-opus",
        sequence=0,
        sample_rate_hz=48_000,
        encoding=AudioEncoding.OPUS,
    )

    assert vad.inspect(frame) is None
