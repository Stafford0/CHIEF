"""Typed, provider-independent notification and attention records."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, time
from enum import Enum, IntEnum
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def require_text(value: str, field_name: str, *, maximum: int) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    if len(value) > maximum:
        raise ValueError(f"{field_name} must not exceed {maximum} characters")


class NotificationChannel(str, Enum):
    IN_APP = "in_app"
    WEB_PUSH = "web_push"
    EMAIL = "email"
    SMS = "sms"
    VOICE = "voice"


class NotificationPriority(IntEnum):
    LOW = 10
    NORMAL = 20
    HIGH = 30
    CRITICAL = 40


class NotificationState(str, Enum):
    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    EXPIRED = "expired"


class AttentionAction(str, Enum):
    INTERRUPT = "interrupt"
    DIGEST = "digest"
    SUPPRESS = "suppress"


class AttentionReason(str, Enum):
    INTERRUPT_ALLOWED = "interrupt_allowed"
    BELOW_PRIORITY_THRESHOLD = "below_priority_threshold"
    QUIET_HOURS = "quiet_hours"
    COOLDOWN_ACTIVE = "cooldown_active"
    DAILY_BUDGET_EXHAUSTED = "daily_budget_exhausted"
    EXPIRED = "expired"
    ACKNOWLEDGED = "acknowledged"


class DeliveryStatus(str, Enum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Notification:
    recipient_id: str
    source: str
    title: str
    body: str
    priority: NotificationPriority
    channels: tuple[NotificationChannel, ...]
    idempotency_key: str
    dedup_key: str
    created_at: datetime
    expires_at: datetime | None = None
    acknowledgement_required: bool = False
    acknowledged_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        require_text(self.recipient_id, "recipient_id", maximum=256)
        require_text(self.source, "notification source", maximum=128)
        require_text(self.title, "notification title", maximum=240)
        require_text(self.body, "notification body", maximum=20_000)
        require_text(self.idempotency_key, "notification idempotency_key", maximum=512)
        require_text(self.dedup_key, "notification dedup_key", maximum=512)
        if not self.channels:
            raise ValueError("a notification must request at least one channel")
        if len(self.channels) != len(set(self.channels)):
            raise ValueError("notification channels must be unique")
        require_aware(self.created_at, "notification created_at")
        if self.expires_at is not None:
            require_aware(self.expires_at, "notification expires_at")
            if self.expires_at <= self.created_at:
                raise ValueError("notification expires_at must be after created_at")
        if self.acknowledged_at is not None:
            require_aware(self.acknowledged_at, "notification acknowledged_at")
            if self.acknowledged_at < self.created_at:
                raise ValueError("notification acknowledged_at must not precede created_at")

    def state_at(self, now: datetime) -> NotificationState:
        require_aware(now, "notification comparison time")
        if self.acknowledged_at is not None and self.acknowledged_at <= now:
            return NotificationState.ACKNOWLEDGED
        if self.expires_at is not None and self.expires_at <= now:
            return NotificationState.EXPIRED
        return NotificationState.ACTIVE

    def acknowledge(self, at: datetime) -> Notification:
        require_aware(at, "notification acknowledgement time")
        if self.acknowledged_at is not None:
            return self
        return replace(self, acknowledged_at=at)


@dataclass(frozen=True, slots=True)
class QuietHours:
    start: time
    end: time
    timezone: str

    def __post_init__(self) -> None:
        if self.start.tzinfo is not None or self.end.tzinfo is not None:
            raise ValueError("quiet-hour times must be naive local wall-clock times")
        if self.start == self.end:
            raise ValueError("quiet-hour start and end must differ")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown quiet-hours timezone: {self.timezone}") from exc

    def contains(self, moment: datetime) -> bool:
        require_aware(moment, "quiet-hours comparison time")
        local_time = moment.astimezone(ZoneInfo(self.timezone)).time().replace(tzinfo=None)
        if self.start < self.end:
            return self.start <= local_time < self.end
        return local_time >= self.start or local_time < self.end

    def ends_after(self, moment: datetime) -> datetime:
        """Return the end of the active quiet period containing ``moment``."""

        require_aware(moment, "quiet-hours comparison time")
        if not self.contains(moment):
            raise ValueError("moment is not within quiet hours")
        zone = ZoneInfo(self.timezone)
        local = moment.astimezone(zone)
        end_date = local.date()
        if self.start > self.end and local.time().replace(tzinfo=None) >= self.start:
            end_date = end_date.fromordinal(end_date.toordinal() + 1)
        return datetime.combine(end_date, self.end, tzinfo=zone)


@dataclass(frozen=True, slots=True)
class AttentionDecision:
    notification_id: UUID
    recipient_id: str
    action: AttentionAction
    reason: AttentionReason
    decided_at: datetime
    next_eligible_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        require_text(self.recipient_id, "decision recipient_id", maximum=256)
        require_aware(self.decided_at, "decision decided_at")
        if self.next_eligible_at is not None:
            require_aware(self.next_eligible_at, "decision next_eligible_at")
            if self.next_eligible_at <= self.decided_at:
                raise ValueError("decision next_eligible_at must be after decided_at")
        if self.action is AttentionAction.INTERRUPT and (
            self.reason is not AttentionReason.INTERRUPT_ALLOWED
            or self.next_eligible_at is not None
        ):
            raise ValueError("interrupt decisions must be immediately allowed")
        if self.action is AttentionAction.SUPPRESS and self.next_eligible_at is not None:
            raise ValueError("suppressed notifications cannot have a next eligible time")


@dataclass(frozen=True, slots=True)
class DeliveryAttempt:
    notification_id: UUID
    channel: NotificationChannel
    attempt_number: int
    idempotency_key: str
    status: DeliveryStatus
    attempted_at: datetime
    completed_at: datetime | None = None
    error: str | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if self.attempt_number < 1:
            raise ValueError("delivery attempt_number must be positive")
        require_text(self.idempotency_key, "delivery idempotency_key", maximum=512)
        require_aware(self.attempted_at, "delivery attempted_at")
        if self.completed_at is not None:
            require_aware(self.completed_at, "delivery completed_at")
            if self.completed_at < self.attempted_at:
                raise ValueError("delivery completed_at must not precede attempted_at")
        if self.status is DeliveryStatus.FAILED and not (self.error and self.error.strip()):
            raise ValueError("a failed delivery attempt must contain an error")
        if self.status is not DeliveryStatus.FAILED and self.error is not None:
            raise ValueError("only failed delivery attempts can contain an error")
        if self.error is not None and len(self.error) > 20_000:
            raise ValueError("delivery error must not exceed 20000 characters")


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    attempt_id: UUID
    idempotency_key: str
    status: DeliveryStatus
    received_at: datetime
    provider_reference: str | None = None
    detail: str | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        require_text(self.idempotency_key, "receipt idempotency_key", maximum=512)
        require_aware(self.received_at, "receipt received_at")
        if self.status not in {
            DeliveryStatus.DELIVERED,
            DeliveryStatus.FAILED,
            DeliveryStatus.CANCELLED,
        }:
            raise ValueError("a receipt status must be delivered, failed, or cancelled")
        if self.provider_reference is not None:
            require_text(self.provider_reference, "receipt provider_reference", maximum=512)
        if self.detail is not None and len(self.detail) > 20_000:
            raise ValueError("receipt detail must not exceed 20000 characters")
