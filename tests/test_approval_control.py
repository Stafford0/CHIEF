from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from chief.api.approvals import create_approvals_router
from chief.core.execution_control import ExecutionControlStore
from chief.core.sqlite_session_store import SQLiteSessionStore
from chief.core.tool_planner import PlannedToolCall
from chief.events.scheduler import Scheduler
from chief.events.schema import Event, Schedule, ScheduleCadence
from chief.events.store import EventStore
from chief.runs import ActionResult, RunEngine, SQLiteRunStore, VerificationStatus
from chief.runtime.supervisor import RuntimeSupervisor, RuntimeStateStore
from chief.tools.base import Tool, ToolDefinition, ToolResult, ToolRisk
from chief.tools.registry import ToolRegistry


class RecordingTool(Tool):
    def __init__(self) -> None:
        self.executions: list[dict[str, object]] = []

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="recording_write",
            description="Record an approved test mutation.",
            risk=ToolRisk.SENSITIVE,
            requires_approval=True,
            input_schema={
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["target"],
                "additionalProperties": False,
            },
            side_effects=True,
            idempotent=False,
        )

    def validate(self, arguments: dict[str, object]) -> None:
        super().validate(arguments)
        if set(arguments) - {"target", "note"}:
            raise ValueError("Unexpected argument")
        if not isinstance(arguments.get("target"), str) or not arguments["target"]:
            raise ValueError("target is required")
        if "note" in arguments and not isinstance(arguments["note"], str):
            raise ValueError("note must be text")

    def execute(self, arguments: dict[str, object]) -> ToolResult:
        self.executions.append(dict(arguments))
        return ToolResult(success=True, content="recorded", data=dict(arguments))


def _client(tmp_path: Path):
    database = tmp_path / "chief.db"
    sessions = SQLiteSessionStore(database)
    tool = RecordingTool()
    registry = ToolRegistry()
    registry.register(tool)
    control = ExecutionControlStore(database, initial_enabled=True)
    app = FastAPI()

    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.actor_id = "owner"
        request.state.request_id = "test-request"
        return await call_next(request)

    app.include_router(
        create_approvals_router(
            session_store=sessions,
            tool_registry=registry,
            execution_control=control,
            configured_execution_enabled=True,
        )
    )
    return TestClient(app), sessions, tool, control


def _proposal(sessions: SQLiteSessionStore, **arguments: object):
    session = sessions.create("owner")
    proposal = session.propose_tool(
        PlannedToolCall(
            intent="test_write",
            tool_name="recording_write",
            arguments=dict(arguments),
            description="record a test mutation",
        )
    )
    return session, proposal


def test_approval_preview_and_exact_id_execution(tmp_path: Path) -> None:
    client, sessions, tool, _control = _client(tmp_path)
    _session, proposal = _proposal(sessions, target="alpha", note="keep")

    preview = client.get(f"/approvals/{proposal.id}")
    assert preview.status_code == 200
    body = preview.json()
    assert body["arguments"] == {"target": "alpha", "note": "keep"}
    assert body["risk"] == "sensitive"
    assert body["side_effects"] is True
    assert body["standing_permission_available"] is False

    approved = client.post(f"/approvals/{proposal.id}/approve")
    assert approved.status_code == 200
    assert approved.json()["success"] is True
    assert tool.executions == [{"target": "alpha", "note": "keep"}]

    replay = client.post(f"/approvals/{proposal.id}/approve")
    assert replay.status_code == 404
    assert tool.executions == [{"target": "alpha", "note": "keep"}]


def test_narrow_consumes_old_proposal_and_never_executes_it(tmp_path: Path) -> None:
    client, sessions, tool, _control = _client(tmp_path)
    _session, proposal = _proposal(sessions, target="alpha", note="remove me")

    narrowed = client.post(
        f"/approvals/{proposal.id}/narrow",
        json={"arguments": {"target": "alpha"}},
    )
    assert narrowed.status_code == 200
    replacement_id = narrowed.json()["proposal_id"]
    assert replacement_id != str(proposal.id)
    assert narrowed.json()["arguments"] == {"target": "alpha"}

    stale = client.post(f"/approvals/{proposal.id}/approve")
    assert stale.status_code == 404
    assert tool.executions == []

    approved = client.post(f"/approvals/{replacement_id}/approve")
    assert approved.status_code == 200
    assert tool.executions == [{"target": "alpha"}]

    decisions = client.get("/approvals/history").json()
    assert any(item["decision"] == "narrowed" for item in decisions)
    assert any(item["decision"] == "approved" for item in decisions)


def test_narrow_cannot_change_existing_values_or_add_arguments(tmp_path: Path) -> None:
    client, sessions, _tool, _control = _client(tmp_path)
    _session, proposal = _proposal(sessions, target="alpha", note="fixed")

    changed = client.post(
        f"/approvals/{proposal.id}/narrow",
        json={"arguments": {"target": "beta"}},
    )
    assert changed.status_code == 422

    added = client.post(
        f"/approvals/{proposal.id}/narrow",
        json={"arguments": {"target": "alpha", "other": "x"}},
    )
    assert added.status_code == 422


def test_persisted_pause_blocks_approval_until_resumed(tmp_path: Path) -> None:
    client, sessions, tool, control = _client(tmp_path)
    _session, proposal = _proposal(sessions, target="alpha")

    paused = client.post("/control/execution/pause", json={"reason": "test stop"})
    assert paused.status_code == 200
    assert paused.json()["effective_enabled"] is False
    assert ExecutionControlStore(control.database_path).get().enabled is False

    blocked = client.post(f"/approvals/{proposal.id}/approve")
    assert blocked.status_code == 503
    assert tool.executions == []

    resumed = client.post("/control/execution/resume", json={"reason": "test resume"})
    assert resumed.status_code == 200
    assert resumed.json()["effective_enabled"] is True

    approved = client.post(f"/approvals/{proposal.id}/approve")
    assert approved.status_code == 200
    assert tool.executions == [{"target": "alpha"}]


def test_runtime_supervisor_honors_persisted_operator_pause(tmp_path: Path) -> None:
    database = tmp_path / "chief.db"
    events = EventStore(database)
    runs = SQLiteRunStore(database)
    engine = RunEngine(
        runs,
        {
            "verified.action": lambda _context, _args: ActionResult(
                result_data={"ok": True},
                verification_status=VerificationStatus.VERIFIED,
            )
        },
    )
    control = ExecutionControlStore(database, initial_enabled=True)
    supervisor = RuntimeSupervisor(
        event_store=events,
        scheduler=Scheduler(events),
        run_store=runs,
        run_engine=engine,
        state_store=RuntimeStateStore(database),
        execution_control=control,
        configured_execution_enabled=True,
        min_free_disk_bytes=0,
    )
    events.enqueue(
        Event(
            event_type="verified.action",
            idempotency_key="approval-control-runtime",
        )
    )

    control.set_enabled(False, actor_id="owner", reason="stop")
    paused = supervisor.tick_once()
    assert paused.status == "paused"
    assert paused.dispatched_events == 0
    assert events.counts().get("pending") == 1

    control.set_enabled(True, actor_id="owner", reason="resume")
    resumed = supervisor.tick_once()
    assert resumed.dispatched_events == 1
    assert resumed.run_steps == 1
    assert len(runs.list_runs()) == 1
