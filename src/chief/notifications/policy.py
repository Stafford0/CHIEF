"""Deterministic attention policy; this module never sends notifications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .schema import (
    AttentionAction,
    AttentionDecision,
    AttentionReason,
    Notification,
    NotificationPriority,
    NotificationState,
    QuietHours,
    require_aware,
)
from .store import NotificationStore


@dataclass(frozen=True, slots=True)
class AttentionPolicyConfig:
    timezone: str = "UTC"
    quiet_hours: QuietHours | None = None
    interrupt_threshold: NotificationPriority = NotificationPriority.HIGH
    quiet_hours_bypass_priority: NotificationPriority = NotificationPriority.CRITICAL
    budget_bypass_priority: NotificationPriority = NotificationPriority.CRITICAL
    daily_interruption_budget: int = 8
    cooldown: timedelta = timedelta(minutes=30)

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown attention-policy timezone: {self.timezone}") from exc
        if self.daily_interruption_budget < 0:
            raise ValueError("daily_interruption_budget must not be negative")
        if self.cooldown < timedelta(0):
            raise ValueError("notification cooldown must not be negative")


class AttentionPolicy:
    """Choose interruption, digest, or suppression under a finite attention budget."""

    def __init__(self, config: AttentionPolicyConfig | None = None) -> None:
        self.config = config or AttentionPolicyConfig()

    def decide(
        self,
        notification: Notification,
        store: NotificationStore,
        *,
        now: datetime | None = None,
    ) -> AttentionDecision:
        comparison_time = now or datetime.now(UTC)
        require_aware(comparison_time, "attention decision time")

        stored = store.save(notification)
        existing = store.decision_for(stored.id)
        if existing is not None:
            return existing

        state = stored.state_at(comparison_time)
        if state is NotificationState.ACKNOWLEDGED:
            return self._record(
                store,
                stored,
                AttentionAction.SUPPRESS,
                AttentionReason.ACKNOWLEDGED,
                comparison_time,
            )
        if state is NotificationState.EXPIRED:
            return self._record(
                store,
                stored,
                AttentionAction.SUPPRESS,
                AttentionReason.EXPIRED,
                comparison_time,
            )

        last_interrupt = store.latest_interrupt_at(
            recipient_id=stored.recipient_id,
            dedup_key=stored.dedup_key,
            before=comparison_time,
        )
        if last_interrupt is not None:
            next_eligible = last_interrupt + self.config.cooldown
            if comparison_time < next_eligible:
                return self._record(
                    store,
                    stored,
                    AttentionAction.DIGEST,
                    AttentionReason.COOLDOWN_ACTIVE,
                    comparison_time,
                    next_eligible_at=next_eligible,
                )

        if stored.priority < self.config.interrupt_threshold:
            return self._record(
                store,
                stored,
                AttentionAction.DIGEST,
                AttentionReason.BELOW_PRIORITY_THRESHOLD,
                comparison_time,
            )

        quiet_hours = self.config.quiet_hours
        if (
            quiet_hours is not None
            and quiet_hours.contains(comparison_time)
            and stored.priority < self.config.quiet_hours_bypass_priority
        ):
            return self._record(
                store,
                stored,
                AttentionAction.DIGEST,
                AttentionReason.QUIET_HOURS,
                comparison_time,
                next_eligible_at=quiet_hours.ends_after(comparison_time).astimezone(UTC),
            )

        day_start, day_end = self._local_day_bounds(comparison_time)
        interruptions = store.count_interruptions(
            recipient_id=stored.recipient_id,
            start=day_start,
            end=day_end,
        )
        if (
            interruptions >= self.config.daily_interruption_budget
            and stored.priority < self.config.budget_bypass_priority
        ):
            return self._record(
                store,
                stored,
                AttentionAction.DIGEST,
                AttentionReason.DAILY_BUDGET_EXHAUSTED,
                comparison_time,
                next_eligible_at=day_end,
            )

        return self._record(
            store,
            stored,
            AttentionAction.INTERRUPT,
            AttentionReason.INTERRUPT_ALLOWED,
            comparison_time,
        )

    def _local_day_bounds(self, moment: datetime) -> tuple[datetime, datetime]:
        zone = ZoneInfo(self.config.timezone)
        local = moment.astimezone(zone)
        start = datetime.combine(local.date(), time.min, tzinfo=zone)
        next_date = local.date().fromordinal(local.date().toordinal() + 1)
        end = datetime.combine(next_date, time.min, tzinfo=zone)
        return start.astimezone(UTC), end.astimezone(UTC)

    @staticmethod
    def _record(
        store: NotificationStore,
        notification: Notification,
        action: AttentionAction,
        reason: AttentionReason,
        decided_at: datetime,
        *,
        next_eligible_at: datetime | None = None,
    ) -> AttentionDecision:
        return store.record_decision(
            AttentionDecision(
                notification_id=notification.id,
                recipient_id=notification.recipient_id,
                action=action,
                reason=reason,
                decided_at=decided_at,
                next_eligible_at=next_eligible_at,
            )
        )
