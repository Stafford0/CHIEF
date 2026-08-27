from chief.core.tool_planner import DeterministicToolPlanner


def test_natural_system_status_command_uses_safe_runtime_tool() -> None:
    planner = DeterministicToolPlanner()

    planned = planner.plan("CHIEF, check system status.")

    assert planned is not None
    assert planned.tool_name == "system_status"
    assert planned.arguments == {}
