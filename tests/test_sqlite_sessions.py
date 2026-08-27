from chief.core.sqlite_session_store import SQLiteSessionStore
from chief.core.tool_planner import PlannedToolCall


def test_session_messages_and_owner_persist_across_restart(tmp_path) -> None:
    path = tmp_path / "sessions.db"
    session = SQLiteSessionStore(path).create(owner_id="director")
    session.add_message("user", "Remember the launch date.")

    reopened = SQLiteSessionStore(path).get_or_create(session.id, owner_id="director")

    assert reopened.messages[0].content == "Remember the launch date."
    assert reopened.owner_id == "director"


def test_ultron_silence_preference_persists_across_restart(tmp_path) -> None:
    path = tmp_path / "sessions.db"
    session = SQLiteSessionStore(path).create(owner_id="director")
    session.set_ultron_silenced(True)

    reopened = SQLiteSessionStore(path).get_or_create(session.id, owner_id="director")

    assert reopened.ultron_silenced is True


def test_pending_approval_is_consumed_once_across_store_instances(tmp_path) -> None:
    path = tmp_path / "sessions.db"
    first_store = SQLiteSessionStore(path)
    session = first_store.create(owner_id="director")
    session.propose_tool(
        PlannedToolCall(
            intent="tests",
            tool_name="powershell_command",
            arguments={"command": "pytest", "args": ["-q"]},
            description="run tests",
        )
    )
    second_store = SQLiteSessionStore(path)

    consumed = second_store.take_pending_tool(session.id, owner_id="director")
    replay = first_store.take_pending_tool(session.id, owner_id="director")

    assert consumed is not None
    assert consumed.call.arguments == {"command": "pytest", "args": ["-q"]}
    assert replay is None
    assert SQLiteSessionStore(path).pending_tool_calls("director") == []
