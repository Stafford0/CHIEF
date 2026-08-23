from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from chief.foresight.schema import (
    KPI,
    Assumption,
    ForesightSignal,
    KPIDirection,
    SignalKind,
    SignalStatus,
)
from chief.foresight.scoring import rank_signals, score_signal
from chief.foresight.store import ForesightStore


def signal(**overrides) -> ForesightSignal:
    values = {
        "kind": SignalKind.RISK,
        "title": "Checkout conversion dropped",
        "summary": "Conversion is below the seven-day baseline.",
        "impact": 5,
        "urgency": 4,
        "confidence": 0.9,
        "reversibility": 2,
        "evidence_refs": ["analytics://checkout/2026-08-23"],
    }
    values.update(overrides)
    return ForesightSignal(**values)


def test_signal_requires_evidence_for_high_confidence() -> None:
    with pytest.raises(ValidationError, match="evidence reference"):
        signal(evidence_refs=[])


def test_signal_score_is_transparent_and_status_aware() -> None:
    now = datetime.now(UTC)
    open_score = score_signal(signal(observed_at=now), now=now)
    acknowledged_score = score_signal(
        signal(observed_at=now, status=SignalStatus.ACKNOWLEDGED), now=now
    )

    assert open_score.score == 91.0
    assert sum(open_score.breakdown.values()) == open_score.score
    assert acknowledged_score.score == open_score.score * 0.25


def test_rank_signals_prioritizes_impact_and_urgency() -> None:
    high = signal()
    low = signal(title="Minor trend", impact=1, urgency=1, confidence=0.5, evidence_refs=[])

    assert rank_signals([low, high])[0][0].id == high.id


def test_store_persists_signals_assumptions_and_kpis(tmp_path) -> None:
    path = tmp_path / "foresight.db"
    store = ForesightStore(path)
    saved_signal = store.save_signal(signal())
    assumption = store.save_assumption(
        Assumption(
            statement="Customers will adopt annual billing.",
            review_due_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    kpi = store.save_kpi(
        KPI(
            name="Monthly recurring revenue",
            direction=KPIDirection.HIGHER_IS_BETTER,
            current_value=50_000,
            target_value=75_000,
            source_ref="billing://mrr",
        )
    )

    reopened = ForesightStore(path)
    assert reopened.get_signal(saved_signal.id) == saved_signal
    assert reopened.list_assumptions_due()[0].id == assumption.id
    assert reopened.list_kpis()[0].id == kpi.id


def test_expired_and_resolved_signals_are_not_open(tmp_path) -> None:
    store = ForesightStore(tmp_path / "foresight.db")
    store.save_signal(signal(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    store.save_signal(signal(title="Resolved", status=SignalStatus.RESOLVED))

    assert store.list_signals() == []
