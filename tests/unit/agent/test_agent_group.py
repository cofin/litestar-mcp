from __future__ import annotations

import pytest

from litestar_mcp.agent.models import MockModelClient, ModelDelta
from litestar_mcp.agent.runtime import AgentRuntime, TurnRequest
from litestar_mcp.agent.spec import Agent, AgentGroup, AgentMessage
from litestar_mcp.agent.tools import tool
from litestar_mcp.controllers.skill import SkillController


class AnalyticsSkill(SkillController):
    name = "analytics"
    instructions = "Always format numbers clearly."

    @tool(description="Sum two integers.")
    def add_numbers(self, a: int, b: int) -> int:
        return a + b


def test_agent_group_synthesis_and_execution() -> None:
    coordinator = Agent(name="coordinator", instructions="Route requests.")
    specialist_sql = Agent(
        name="sql_specialist",
        description="SQL expert",
        instructions="Write SQL.",
        skills=[AnalyticsSkill],
    )
    specialist_code = Agent(
        name="code_specialist",
        description="Python expert",
        instructions="Write Python.",
    )

    group = AgentGroup(coordinator=coordinator, specialists=[specialist_sql, specialist_code])

    assert group.get_agent("coordinator") is coordinator
    assert group.get_agent("sql_specialist") is specialist_sql
    assert group.get_agent("code_specialist") is specialist_code
    with pytest.raises(KeyError):
        group.get_agent("nonexistent")

    tools_by_name = {t.name: t for t in coordinator.get_all_tools()}
    assert "transfer_to_agent" in tools_by_name
    transfer_tool = tools_by_name["transfer_to_agent"]
    enum_vals = transfer_tool.input_schema["properties"]["target_agent"]["enum"]
    assert enum_vals == ["sql_specialist", "code_specialist"]

    ok_res = transfer_tool.fn(target_agent="sql_specialist", reason="Need SQL query")
    assert ok_res["status"] == "transferred"
    assert ok_res["target_agent"] == "sql_specialist"

    err_res = transfer_tool.fn(target_agent="invalid_agent", reason="Bad target")
    assert err_res["status"] == "error"


@pytest.mark.asyncio
async def test_agent_group_heterogeneous_multi_model_dispatch() -> None:
    coordinator_model = MockModelClient(
        responses=[
            [
                ModelDelta(
                    event_type="tool_call",
                    tool_name="transfer_to_agent",
                    call_id="call_transfer",
                    arguments={"target_agent": "math_specialist", "reason": "Requires addition"},
                )
            ]
        ]
    )
    specialist_model = MockModelClient(
        responses=[
            [
                ModelDelta(
                    event_type="tool_call",
                    tool_name="add_numbers",
                    call_id="call_add",
                    arguments={"a": 10, "b": 32},
                )
            ],
            [
                ModelDelta(event_type="delta", text="The sum is 42."),
            ],
        ]
    )

    coordinator = Agent(
        name="triage_coordinator",
        instructions="Triage user questions.",
        model=coordinator_model,
    )
    specialist = Agent(
        name="math_specialist",
        instructions="Compute math accurately.",
        skills=[AnalyticsSkill()],
        model=specialist_model,
    )
    group = AgentGroup(coordinator=coordinator, specialists=[specialist])
    runtime = AgentRuntime(target=group)

    result = await runtime.run_turn(
        TurnRequest(
            session_id="hetero_session",
            turn_id="turn_1",
            user_message="What is 10 + 32?",
        )
    )

    assert result.content == "The sum is 42."
    assert result.agent_name == "math_specialist"
    assert len(coordinator_model.call_history) == 1
    assert len(specialist_model.call_history) == 2
    assert "Always format numbers clearly." in specialist_model.call_history[0]["system_instruction"]
    assert any(isinstance(m, AgentMessage) and m.role == "user" for m in result.messages)
