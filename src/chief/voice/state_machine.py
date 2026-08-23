from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Event, RLock

from chief.voice.schema import VoiceState


class InvalidVoiceTransition(ValueError):
    """Raised when a caller attempts an illegal voice-state transition."""


class VoiceCancelled(RuntimeError):
    """Raised when cancelled work checks its cancellation token."""


class CancellationToken:
    """Thread-safe cooperative cancellation shared across voice providers."""

    def __init__(self) -> None:
        self._event = Event()
        self._reason: str | None = None
        self._lock = RLock()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        with self._lock:
            return self._reason

    def cancel(self, reason: str = "Voice operation cancelled.") -> bool:
        """Cancel once, preserving the first reason for audit and diagnosis."""
        reason = reason.strip() or "Voice operation cancelled."
        with self._lock:
            if self._event.is_set():
                return False
            self._reason = reason
            self._event.set()
            return True

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise VoiceCancelled(self.reason or "Voice operation cancelled.")


@dataclass(frozen=True)
class VoiceTransition:
    previous: VoiceState
    current: VoiceState
    reason: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


LEGAL_TRANSITIONS: dict[VoiceState, frozenset[VoiceState]] = {
    VoiceState.IDLE: frozenset({VoiceState.LISTENING}),
    VoiceState.LISTENING: frozenset(
        {VoiceState.IDLE, VoiceState.TRANSCRIBING, VoiceState.CANCELLED, VoiceState.ERROR}
    ),
    VoiceState.TRANSCRIBING: frozenset(
        {
            VoiceState.IDLE,
            VoiceState.THINKING,
            VoiceState.INTERRUPTED,
            VoiceState.CANCELLED,
            VoiceState.ERROR,
        }
    ),
    VoiceState.THINKING: frozenset(
        {
            VoiceState.IDLE,
            VoiceState.SPEAKING,
            VoiceState.INTERRUPTED,
            VoiceState.CANCELLED,
            VoiceState.ERROR,
        }
    ),
    VoiceState.SPEAKING: frozenset(
        {VoiceState.IDLE, VoiceState.INTERRUPTED, VoiceState.CANCELLED, VoiceState.ERROR}
    ),
    VoiceState.INTERRUPTED: frozenset(
        {VoiceState.IDLE, VoiceState.LISTENING, VoiceState.CANCELLED, VoiceState.ERROR}
    ),
    VoiceState.CANCELLED: frozenset({VoiceState.IDLE}),
    VoiceState.ERROR: frozenset({VoiceState.IDLE}),
}


class VoiceStateMachine:
    """Small, strict lifecycle coordinator for one voice session."""

    def __init__(self) -> None:
        self._state = VoiceState.IDLE
        self._history: list[VoiceTransition] = []
        self._cancellation = CancellationToken()
        self._lock = RLock()

    @property
    def state(self) -> VoiceState:
        with self._lock:
            return self._state

    @property
    def history(self) -> tuple[VoiceTransition, ...]:
        with self._lock:
            return tuple(self._history)

    @property
    def cancellation(self) -> CancellationToken:
        with self._lock:
            return self._cancellation

    def can_transition(self, target: VoiceState) -> bool:
        with self._lock:
            return target in LEGAL_TRANSITIONS[self._state]

    def transition(self, target: VoiceState, *, reason: str | None = None) -> VoiceTransition:
        with self._lock:
            previous = self._state
            if target not in LEGAL_TRANSITIONS[previous]:
                raise InvalidVoiceTransition(
                    f"Cannot transition voice state from {previous.value} to {target.value}."
                )
            transition = VoiceTransition(previous=previous, current=target, reason=reason)
            self._state = target
            self._history.append(transition)
            return transition

    def cancel(self, reason: str = "Voice operation cancelled.") -> VoiceTransition:
        with self._lock:
            if self._state == VoiceState.CANCELLED:
                return self._history[-1]
            if VoiceState.CANCELLED not in LEGAL_TRANSITIONS[self._state]:
                raise InvalidVoiceTransition(
                    f"Cannot transition voice state from {self._state.value} to cancelled."
                )
            self._cancellation.cancel(reason)
            return self.transition(VoiceState.CANCELLED, reason=reason)

    def interrupt(self, reason: str = "Voice operation interrupted.") -> VoiceTransition:
        return self.transition(VoiceState.INTERRUPTED, reason=reason)

    def fail(self, reason: str) -> VoiceTransition:
        reason = reason.strip()
        if not reason:
            raise ValueError("Voice failure reason cannot be empty.")
        return self.transition(VoiceState.ERROR, reason=reason)

    def reset(self, *, reason: str | None = None) -> VoiceTransition:
        """Return a terminal/interrupted session to idle with a fresh token."""
        with self._lock:
            if self._state not in {
                VoiceState.CANCELLED,
                VoiceState.ERROR,
                VoiceState.INTERRUPTED,
            }:
                raise InvalidVoiceTransition(
                    f"Cannot reset voice state from {self._state.value}; finish or cancel it first."
                )
            transition = self.transition(VoiceState.IDLE, reason=reason)
            self._cancellation = CancellationToken()
            return transition
