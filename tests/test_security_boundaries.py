from __future__ import annotations

from pathlib import Path

import pytest

from chief.core.sqlite_session_store import SQLiteSessionStore
from chief.core.tool_planner import PlannedToolCall


def _call(label: str = "safe") -> PlannedToolCall:
    return PlannedToolCall(
        intent=f"intent-{label}",
        tool_name="powershell_command",
        arguments={"command": "pytest", "args": ["-q"]},
        description=f"proposal {label}",
    )


def test_cross_session_proposal_id_cannot_consume_other_session(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "chief.db")
    first = store.create(owner_id="owner-a")
    second = store.create(owner_id="owner-a")
    first_proposal = first.propose_tool(_call("first"))
    second_proposal = second.propose_tool(_call("second"))

    stolen = store.take_pending_tool(
        second.id,
        owner_id="owner-a",
        proposal_id=first_proposal.id,
    )

    assert stolen is None
    assert store.get(second.id).peek_pending_tool().id == second_proposal.id
    assert store.get(first.id).peek_pending_tool().id == first_proposal.id


def test_other_owner_cannot_consume_or_list_proposal(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "chief.db")
    session = store.create(owner_id="owner-a")
    proposal = session.propose_tool(_call())

    with pytest.raises(PermissionError, match="another operator"):
        store.take_pending_tool(
            session.id,
            owner_id="owner-b",
            proposal_id=proposal.id,
        )

    assert store.pending_tool_records("owner-b") == []
    assert [row["proposal_id"] for row in store.pending_tool_records("owner-a")] == [
        str(proposal.id)
    ]


def test_consumed_proposal_cannot_be_replayed(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "chief.db")
    session = store.create(owner_id="owner-a")
    proposal = session.propose_tool(_call())

    consumed = store.take_pending_tool(
        session.id,
        owner_id="owner-a",
        proposal_id=proposal.id,
    )
    replay = store.take_pending_tool(
        session.id,
        owner_id="owner-a",
        proposal_id=proposal.id,
    )

    assert consumed is not None
    assert replay is None
    decisions = store.proposal_decisions("owner-a")
    assert len(decisions) == 1
    assert decisions[0]["proposal_id"] == str(proposal.id)


def test_narrowed_proposal_invalidates_original_id(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "chief.db")
    session = store.create(owner_id="owner-a")
    original = session.propose_tool(
        PlannedToolCall(
            intent="draft",
            tool_name="connector_write",
            arguments={
                "connector_id": "gmail_drafts",
                "scope": "drafts.create",
                "payload": {
                    "to": "recipient@example.com",
                    "subject": "Subject",
                    "body": "Body",
                },
                "idempotency_key": "draft-1",
            },
            description="create draft",
        )
    )

    replacement = store.narrow_pending_tool(
        session.id,
        original.id,
        {
            "connector_id": "gmail_drafts",
            "scope": "drafts.create",
            "payload": {
                "to": "recipient@example.com",
                "subject": "Subject",
            },
            "idempotency_key": "draft-1",
        },
        owner_id="owner-a",
    )

    assert replacement is not None
    assert replacement.id != original.id
    assert store.take_pending_tool(
        session.id,
        owner_id="owner-a",
        proposal_id=original.id,
    ) is None
    assert store.take_pending_tool(
        session.id,
        owner_id="owner-a",
        proposal_id=replacement.id,
    ) is not None
