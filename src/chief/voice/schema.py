from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


class VoiceState(str, Enum):
    """Observable states in one voice interaction."""

    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    ERROR = "error"


class TranscriptKind(str, Enum):
    """Whether a transcript may still change or is ready for downstream use."""

    PARTIAL = "partial"
    FINAL = "final"


class VoiceProcessingLocation(str, Enum):
    """Where audio or speech data is processed."""

    ON_DEVICE = "on_device"
    LOCAL_HOST = "local_host"
    PRIVATE_NETWORK = "private_network"
    CLOUD = "cloud"


class AudioEncoding(str, Enum):
    """Provider-independent encodings accepted by the voice contracts."""

    PCM_S16LE = "pcm_s16le"
    PCM_F32LE = "pcm_f32le"
    OPUS = "opus"
    WAV = "wav"
    MP3 = "mp3"


@dataclass(frozen=True)
class VoicePrivacyPolicy:
    """Explicit privacy boundary for a voice session."""

    allowed_processing_locations: frozenset[VoiceProcessingLocation] = field(
        default_factory=lambda: frozenset(
            {VoiceProcessingLocation.ON_DEVICE, VoiceProcessingLocation.LOCAL_HOST}
        )
    )
    retain_raw_audio: bool = False

    def permits(self, location: VoiceProcessingLocation) -> bool:
        return location in self.allowed_processing_locations


@dataclass(frozen=True)
class AudioFrame:
    """An ephemeral input frame supplied to a speech-to-text provider."""

    data: bytes
    sequence: int
    sample_rate_hz: int
    channels: int = 1
    encoding: AudioEncoding = AudioEncoding.PCM_S16LE
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes) or not self.data:
            raise ValueError("Audio frame data must be non-empty bytes.")
        _validate_audio_metadata(self.sequence, self.sample_rate_hz, self.channels)


@dataclass(frozen=True)
class TranscriptEvent:
    """A partial or final transcript with source and timing metadata."""

    text: str
    kind: TranscriptKind
    sequence: int
    provider: str
    processing_location: VoiceProcessingLocation
    confidence: float | None = None
    language: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    emitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Transcript text cannot be empty.")
        if self.sequence < 0:
            raise ValueError("Transcript sequence cannot be negative.")
        if not self.provider.strip():
            raise ValueError("Transcript provider cannot be empty.")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Transcript confidence must be between 0 and 1.")
        _validate_time_range(self.start_ms, self.end_ms)

    @property
    def is_final(self) -> bool:
        return self.kind == TranscriptKind.FINAL


@dataclass(frozen=True)
class SpeechSynthesisRequest:
    """Text and presentation preferences sent to a text-to-speech provider."""

    text: str
    language: str | None = None
    voice: str | None = None
    rate: float = 1.0

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Speech synthesis text cannot be empty.")
        if not 0.25 <= self.rate <= 4.0:
            raise ValueError("Speech synthesis rate must be between 0.25 and 4.0.")


@dataclass(frozen=True)
class AudioChunk:
    """One ordered TTS output chunk with playback and provenance metadata."""

    data: bytes
    sequence: int
    provider: str
    processing_location: VoiceProcessingLocation
    sample_rate_hz: int
    channels: int = 1
    encoding: AudioEncoding = AudioEncoding.PCM_S16LE
    duration_ms: int | None = None
    is_final: bool = False
    emitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes):
            raise TypeError("Audio chunk data must be bytes.")
        if not self.data and not self.is_final:
            raise ValueError("A non-final audio chunk cannot be empty.")
        if not self.provider.strip():
            raise ValueError("Audio chunk provider cannot be empty.")
        _validate_audio_metadata(self.sequence, self.sample_rate_hz, self.channels)
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("Audio chunk duration cannot be negative.")


def _validate_audio_metadata(sequence: int, sample_rate_hz: int, channels: int) -> None:
    if sequence < 0:
        raise ValueError("Audio sequence cannot be negative.")
    if sample_rate_hz <= 0:
        raise ValueError("Audio sample rate must be positive.")
    if channels <= 0:
        raise ValueError("Audio channel count must be positive.")


def _validate_time_range(start_ms: int | None, end_ms: int | None) -> None:
    if start_ms is not None and start_ms < 0:
        raise ValueError("Transcript start time cannot be negative.")
    if end_ms is not None and end_ms < 0:
        raise ValueError("Transcript end time cannot be negative.")
    if start_ms is not None and end_ms is not None and end_ms < start_ms:
        raise ValueError("Transcript end time cannot precede start time.")
