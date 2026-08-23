import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from chief.decisions import (
    DecisionAssumption,
    DecisionCriterion,
    DecisionEvidence,
    DecisionOption,
    DecisionRecord,
    DecisionRisk,
    DecisionStatus,
    EvidenceStance,
    OptionCriterionScore,
    Provenance,
    SQLiteDecisionStore,
    score_decision,
)


def make_decision(*, status: DecisionStatus = DecisionStatus.DRAFT) -> DecisionRecord:
    value = DecisionCriterion(name="Customer value", weight=3.0)
    speed = DecisionCriterion(name="Speed to market", weight=1.0)
    source = Provenance(
        source_type="customer_interview",
        source_id="interview-42",
        source_uri="local://research/interview-42",
        description="Founder interview notes",
        content_hash="sha256:example",
    )
    evidence = DecisionEvidence(
        title="Customers need the workflow",
        content="Eight of ten interviewees ranked the workflow as urgent.",
        provenance=source,
        confidence=0.9,
        stance=EvidenceStance.SUPPORTS,
        related_criterion_ids=[value.id],
    )
    build = DecisionOption(
        name="Build internally",
        description="Own the complete product surface.",
        criterion_scores=[
            OptionCriterionScore(
                criterion_id=value.id,
                score=0.8,
                confidence=0.9,
                rationale="Best fit with the observed customer workflow.",
                evidence_ids=[evidence.id],
                provenance=[source],
            ),
            OptionCriterionScore(
                criterion_id=speed.id,
                score=0.4,
                confidence=0.8,
                rationale="Requires a longer implementation cycle.",
            ),
        ],
        benefits=["Full control"],
        drawbacks=["Slower launch"],
    )
    partner = DecisionOption(
        name="Partner",
        description="Integrate an existing platform.",
        criterion_scores=[
            OptionCriterionScore(
                criterion_id=value.id,
                score=0.6,
                confidence=0.7,
                rationale="Covers the core workflow with constraints.",
                evidence_ids=[evidence.id],
            ),
            OptionCriterionScore(
                criterion_id=speed.id,
                score=0.9,
                confidence=0.95,
                rationale="The integration can launch quickly.",
            ),
        ],
    )
    assumption = DecisionAssumption(
        statement="The integration API remains available.",
        confidence=0.6,
        validation_plan="Confirm the vendor roadmap before signing.",
        provenance=[source],
    )
    risk = DecisionRisk(
        description="A vendor change could delay the launch.",
        probability=0.3,
        impact=0.8,
        confidence=0.7,
        mitigation="Add an exit clause and data export path.",
        option_id=partner.id,
        evidence_ids=[evidence.id],
        provenance=[source],
    )
    recommended = build.id if status == DecisionStatus.DECIDED else None
    return DecisionRecord(
        title="Choose the first product delivery strategy",
        question="Should we build internally or partner?",
        context="The company needs a credible launch path this quarter.",
        status=status,
        owner="Founder",
        constraints=["Launch this quarter", "Launch this quarter", "  "],
        criteria=[value, speed],
        options=[build, partner],
        evidence=[evidence],
        assumptions=[assumption],
        risks=[risk],
        recommended_option_id=recommended,
        recommendation_rationale="The value advantage justifies the slower launch."
        if recommended
        else None,
        confidence=0.75,
        provenance=[source],
        decided_at=datetime.now(UTC) if recommended else None,
    )


def test_sqlite_round_trip_preserves_typed_inputs_and_provenance(tmp_path) -> None:
    store = SQLiteDecisionStore(tmp_path / "decisions.db")
    decision = make_decision(status=DecisionStatus.DECIDED)

    store.save(decision)

    loaded = store.get(decision.id)
    assert loaded == decision
    assert loaded is not None
    assert loaded.evidence[0].provenance.source_id == "interview-42"
    assert loaded.options[0].criterion_scores[0].confidence == 0.9
    assert loaded.risks[0].exposure == pytest.approx(0.24)
    assert loaded.constraints == ["Launch this quarter"]


def test_sqlite_list_filters_status_orders_and_deletes(tmp_path) -> None:
    store = SQLiteDecisionStore(tmp_path / "decisions.db")
    older = make_decision()
    newer = make_decision(status=DecisionStatus.DECIDED).model_copy(
        update={"updated_at": older.updated_at + timedelta(minutes=1)}
    )
    store.save(older)
    store.save(newer)

    assert [item.id for item in store.list()] == [newer.id, older.id]
    assert [item.id for item in store.list(status=DecisionStatus.DECIDED)] == [newer.id]
    assert store.delete(older.id) is True
    assert store.delete(older.id) is False
    assert store.get(older.id) is None


def test_weighted_scoring_exposes_every_contribution() -> None:
    decision = make_decision()

    result = score_decision(decision)

    assert [item.option_name for item in result.options] == ["Build internally", "Partner"]
    assert result.options[0].rank == 1
    assert result.options[0].total_score == pytest.approx(0.7)
    assert result.options[1].total_score == pytest.approx(0.675)
    value = result.options[0].contributions[0]
    assert value.base_weight == 3.0
    assert value.effective_weight == 3.0
    assert value.normalized_weight == pytest.approx(0.75)
    assert value.weighted_contribution == pytest.approx(0.6)
    assert value.rationale
    assert value.evidence_ids == [decision.evidence[0].id]


def test_weight_overrides_support_sensitivity_analysis() -> None:
    decision = make_decision()
    value, speed = decision.criteria

    result = score_decision(
        decision,
        weight_overrides={value.id: 1.0, str(speed.id): 4.0},
    )

    assert result.options[0].option_name == "Partner"
    assert result.options[0].total_score == pytest.approx(0.84)
    assert result.applied_weight_overrides == {str(value.id): 1.0, str(speed.id): 4.0}
    speed_contribution = result.options[0].contributions[1]
    assert speed_contribution.effective_weight == 4.0
    assert speed_contribution.normalized_weight == pytest.approx(0.8)


def test_scoring_rejects_incomplete_options() -> None:
    decision = make_decision()
    incomplete = decision.options[0].model_copy(
        update={"criterion_scores": decision.options[0].criterion_scores[:1]}
    )
    decision = decision.model_copy(update={"options": [incomplete, decision.options[1]]})

    with pytest.raises(ValueError, match="has no score for: Speed to market"):
        score_decision(decision)


def test_schema_rejects_dangling_evidence_references() -> None:
    decision = make_decision()
    invalid_score = (
        decision.options[0].criterion_scores[0].model_copy(update={"evidence_ids": [uuid4()]})
    )
    invalid_option = decision.options[0].model_copy(
        update={"criterion_scores": [invalid_score, decision.options[0].criterion_scores[1]]}
    )

    with pytest.raises(ValidationError, match="unknown evidence"):
        DecisionRecord.model_validate(
            decision.model_copy(update={"options": [invalid_option, decision.options[1]]})
        )


def test_store_uses_wal_migration_marker_and_payload_budget(tmp_path) -> None:
    path = tmp_path / "decisions.db"
    store = SQLiteDecisionStore(path, max_payload_bytes=1_024)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].casefold() == "wal"
        assert connection.execute(
            "SELECT version FROM chief_component_migrations WHERE component = 'decisions'"
        ).fetchone() == (1,)

    with pytest.raises(ValueError, match="configured size limit"):
        store.save(make_decision().model_copy(update={"context": "x" * 2_000}))
