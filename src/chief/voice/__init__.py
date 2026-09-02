"""Provider-independent, privacy-aware voice contracts for CHIEF."""

from chief.voice.providers import SpeechToTextProvider, TextToSpeechProvider
from chief.voice.schema import (
    AudioChunk,
    AudioEncoding,
    AudioFrame,
    SpeechSynthesisRequest,
    TranscriptEvent,
    TranscriptKind,
    VoicePrivacyPolicy,
    VoiceProcessingLocation,
    VoiceState,
)
from chief.voice.session import VoiceSessionCoordinator, VoiceSessionEvent
from chief.voice.state_machine import (
    LEGAL_TRANSITIONS,
    CancellationToken,
    InvalidVoiceTransition,
    VoiceCancelled,
    VoiceStateMachine,
    VoiceTransition,
)
from chief.voice.vad import LocalVoiceActivityDetector, VoiceActivity

__all__ = [
    "LEGAL_TRANSITIONS",
    "AudioChunk",
    "AudioEncoding",
    "AudioFrame",
    "CancellationToken",
    "InvalidVoiceTransition",
    "LocalVoiceActivityDetector",
    "SpeechSynthesisRequest",
    "SpeechToTextProvider",
    "TextToSpeechProvider",
    "TranscriptEvent",
    "TranscriptKind",
    "VoiceActivity",
    "VoiceCancelled",
    "VoicePrivacyPolicy",
    "VoiceProcessingLocation",
    "VoiceSessionCoordinator",
    "VoiceSessionEvent",
    "VoiceState",
    "VoiceStateMachine",
    "VoiceTransition",
]
