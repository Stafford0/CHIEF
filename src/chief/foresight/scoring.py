from datetime import UTC, datetime

from chief.foresight.schema import ForesightSignal, SignalScore, SignalStatus


def score_signal(
    signal: ForesightSignal,
    *,
    now: datetime | None = None,
    freshness_horizon_days: int = 30,
) -> SignalScore:
    """Return a transparent attention score; it is ranking support, not ground truth."""
    now = now or datetime.now(UTC)
    observed = signal.observed_at
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    age_days = max(0.0, (now - observed).total_seconds() / 86_400)
    freshness = max(0.0, 1 - age_days / freshness_horizon_days)
    breakdown = {
        "impact": (signal.impact / 5) * 30,
        "urgency": (signal.urgency / 5) * 25,
        "confidence": signal.confidence * 20,
        "freshness": freshness * 15,
        "irreversibility": ((6 - signal.reversibility) / 5) * 10,
    }
    score = round(sum(breakdown.values()), 2)
    if signal.status != SignalStatus.OPEN:
        score = round(score * 0.25, 2)
    rationale = (
        f"impact {signal.impact}/5; urgency {signal.urgency}/5; "
        f"confidence {signal.confidence:.0%}; observed {age_days:.1f} days ago"
    )
    return SignalScore(
        signal_id=signal.id,
        score=score,
        breakdown={key: round(value, 2) for key, value in breakdown.items()},
        rationale=rationale,
    )


def rank_signals(
    signals: list[ForesightSignal],
    *,
    now: datetime | None = None,
) -> list[tuple[ForesightSignal, SignalScore]]:
    ranked = [(signal, score_signal(signal, now=now)) for signal in signals]
    return sorted(ranked, key=lambda item: item[1].score, reverse=True)
