"""Unit tests for agent specifications, groups, and message structures."""

from typing import Any

import msgspec
import pytest
from litestar.exceptions import ImproperlyConfiguredException

from litestar_mcp.agent.models import MockModelClient, ModelDelta
from litestar_mcp.agent.runtime import AgentRuntime, TurnRequest
from litestar_mcp.agent.spec import TRANSFER_TOOL_NAME, Agent, AgentGroup, AgentMessage
from litestar_mcp.core.tools import ToolArgumentError, ToolCall, ToolResult, tool
from litestar_mcp.mcp.skill_controller import SkillController


class CountingSkill(SkillController):
    """Skill that counts how many times it was instantiated."""

    init_count = 0
    name = "counting"
    instructions = "Counting instructions."

    def __init__(self, owner: Any = None) -> None:
        """Increment class instance counter on construction."""
        super().__init__(owner)
        CountingSkill.init_count += 1

    @tool(description="Count action.")
    def do_count(self) -> int:
        """Return the current instance count."""
        return CountingSkill.init_count


class AnalyticsSkill(SkillController):
    """Analytics skill for math operations."""

    name = "analytics"
    instructions = "Always format numbers clearly."

    @tool(description="Sum two integers.")
    def add_numbers(self, a: int, b: int) -> int:
        """Add two integers together."""
        return a + b


def test_agent_message_round_trip() -> None:
    """AgentMessage converts cleanly to builtins and back."""
    call = ToolCall(name="calc", arguments={"expr": "1+1"}, call_id="c1")
    res = ToolResult(name="calc", content={"value": 2}, call_id="c1")
    msg = AgentMessage(
        role="assistant",
        content="Computed",
        tool_calls=[call],
        tool_results=[res],
        agent_name="calculator",
    )
    raw = msgspec.to_builtins(msg)
    reconstructed = msgspec.convert(raw, AgentMessage)
    assert reconstructed == msg


def test_stateful_skill_instance_reused() -> None:
    """Agent instantiates skill classes once during initialization."""
    CountingSkill.init_count = 0
    agent = Agent(
        name="worker",
        instructions="Work hard.",
        skills=[CountingSkill],
    )
    assert CountingSkill.init_count == 1
    assert len(agent.tool_set) == 1
    assert agent.tool_set[0].name == "do_count"
    _ = agent.tool_set
    _ = agent.combined_instructions
    assert CountingSkill.init_count == 1
    assert "Counting instructions." in agent.combined_instructions


def test_duplicate_tool_names_rejected() -> None:
    """Agent rejects duplicate tool names across tools and skills."""

    @tool(name="duplicate_tool")
    def tool_one() -> str:
        """First implementation."""
        return "one"

    @tool(name="duplicate_tool")
    def tool_two() -> str:
        """Second implementation."""
        return "two"

    with pytest.raises(ImproperlyConfiguredException, match="duplicate tool names"):
        Agent(name="broken", tools=[tool_one, tool_two])


def test_transfer_tool_name_reserved() -> None:
    """Agent rejects tool named transfer_to_agent."""

    @tool(name=TRANSFER_TOOL_NAME)
    def custom_transfer() -> str:
        """Reserved tool implementation."""
        return "transferred"

    with pytest.raises(ImproperlyConfiguredException, match="reserved"):
        Agent(name="reserved_worker", tools=[custom_transfer])


def test_agent_group_does_not_mutate_shared_coordinator() -> None:
    """Shared coordinator agent tools are never mutated by AgentGroup."""
    coordinator = Agent(name="coordinator", instructions="Route requests.")
    specialist_a = Agent(name="spec_a", instructions="Spec A.")
    specialist_b = Agent(name="spec_b", instructions="Spec B.")

    group_1 = AgentGroup(coordinator=coordinator, specialists=[specialist_a])
    group_2 = AgentGroup(coordinator=coordinator, specialists=[specialist_b])

    assert len(coordinator.tools) == 0
    assert len(coordinator.tool_set) == 0

    g1_tools = group_1.tools_for(coordinator)
    g2_tools = group_2.tools_for(coordinator)
    assert len(g1_tools) == 1
    assert len(g2_tools) == 1
    assert g1_tools[0].name == TRANSFER_TOOL_NAME
    assert g2_tools[0].name == TRANSFER_TOOL_NAME

    g1_schema = g1_tools[0].input_schema
    g2_schema = g2_tools[0].input_schema
    assert g1_schema["properties"]["target_agent"]["enum"] == ["spec_a"]
    assert g2_schema["properties"]["target_agent"]["enum"] == ["spec_b"]


@pytest.mark.anyio
async def test_transfer_tool_rejects_unknown_target() -> None:
    """Group transfer tool validates target agent parameter via schema."""
    coordinator = Agent(name="coordinator")
    specialist = Agent(name="specialist")
    group = AgentGroup(coordinator=coordinator, specialists=[specialist])

    transfer_tool = group.tools_for(coordinator)[0]
    with pytest.raises(ToolArgumentError):
        await transfer_tool.execute({"target_agent": "unknown_target"})


def test_resolve_transfer_returns_specialist() -> None:
    """Group resolve_transfer returns target specialist agent or None."""
    coordinator = Agent(name="coordinator")
    specialist = Agent(name="specialist")
    group = AgentGroup(coordinator=coordinator, specialists=[specialist])

    valid_call = ToolCall(
        name=TRANSFER_TOOL_NAME,
        arguments={"target_agent": "specialist"},
        call_id="c1",
    )
    assert group.resolve_transfer(valid_call) is specialist

    unknown_call = ToolCall(
        name=TRANSFER_TOOL_NAME,
        arguments={"target_agent": "missing"},
        call_id="c2",
    )
    assert group.resolve_transfer(unknown_call) is None

    other_call = ToolCall(name="other_tool", arguments={}, call_id="c3")
    assert group.resolve_transfer(other_call) is None


def test_group_rejects_duplicate_agent_names() -> None:
    """AgentGroup rejects non-unique agent names or coordinator in specialists."""
    coordinator = Agent(name="worker")
    specialist = Agent(name="worker")
    with pytest.raises(ImproperlyConfiguredException):
        AgentGroup(coordinator=coordinator, specialists=[specialist])

    coord_unique = Agent(name="coord")
    spec_a = Agent(name="helper")
    spec_b = Agent(name="helper")
    with pytest.raises(ImproperlyConfiguredException):
        AgentGroup(coordinator=coord_unique, specialists=[spec_a, spec_b])


@pytest.mark.anyio
async def test_agent_group_heterogeneous_multi_model_dispatch() -> None:
    """AgentGroup dispatches to specialist with distinct model client."""
    coordinator_model = MockModelClient(
        responses=[
            [
                ModelDelta(
                    event_type="tool_call",
                    tool_name="transfer_to_agent",
                    call_id="call_transfer",
                    arguments={"target_agent": "math_specialist", "reason": "Requires addition"},
                ),
            ],
        ],
    )
    specialist_model = MockModelClient(
        responses=[
            [
                ModelDelta(
                    event_type="tool_call",
                    tool_name="add_numbers",
                    call_id="call_add",
                    arguments={"a": 10, "b": 32},
                ),
            ],
            [
                ModelDelta(event_type="delta", text="The sum is 42."),
            ],
        ],
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
        ),
    )

    assert result.content == "The sum is 42."
    assert result.agent_name == "math_specialist"
    assert len(coordinator_model.call_history) == 1
    assert len(specialist_model.call_history) == 2
    assert "Always format numbers clearly." in specialist_model.call_history[0]["system_instruction"]
    assert any(isinstance(m, AgentMessage) and m.role == "user" for m in result.messages)
