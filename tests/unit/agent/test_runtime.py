import pytest

from litestar_mcp.agent.guards import BudgetExceededError, TurnBudget
from litestar_mcp.agent.models import MockModelClient, ModelDelta
from litestar_mcp.agent.runtime import AgentRuntime, TurnRequest
from litestar_mcp.agent.spec import Agent, AgentGroup, AgentMessage
from litestar_mcp.agent.tools import tool


@tool(description="Calculate square.")
def square(n: int) -> int:
    return n * n


@pytest.mark.asyncio
async def test_agent_runtime_tool_loop() -> None:
    mock_responses = [
        [
            ModelDelta(event_type="thought", thought="Need to call square"),
            ModelDelta(event_type="tool_call", tool_name="square", call_id="c1", arguments={"n": 5}),
        ],
        [
            ModelDelta(event_type="delta", text="The square of 5 is 25."),
        ],
    ]
    model = MockModelClient(responses=mock_responses)
    agent = Agent(name="math_bot", tools=[square], model=model)
    runtime = AgentRuntime(target=agent)

    req = TurnRequest(
        session_id="test_session",
        turn_id="turn_1",
        user_message="What is square of 5?",
    )

    frames = [frame async for frame in runtime.stream_turn(req)]

    frame_types = [f.event_type for f in frames]
    assert "thought" in frame_types
    assert "tool_call" in frame_types
    assert "tool_result" in frame_types
    assert "delta" in frame_types
    assert "complete" in frame_types

    res = await runtime.run_turn(
        TurnRequest(
            session_id="test_session",
            turn_id="turn_2",
            user_message="Hello again",
        )
    )
    assert res.session_id == "test_session"
    assert len(res.messages) >= 4


@pytest.mark.asyncio
async def test_agent_runtime_delegation() -> None:
    coordinator_responses = [
        [
            ModelDelta(
                event_type="tool_call",
                tool_name="transfer_to_agent",
                call_id="trans_1",
                arguments={"target_agent": "specialist", "reason": "Math query"},
            ),
        ],
        [
            ModelDelta(event_type="delta", text="Specialist handled the query."),
        ],
    ]
    model = MockModelClient(responses=coordinator_responses)

    coordinator = Agent(name="coordinator", model=model)
    specialist = Agent(name="specialist", model=model)
    group = AgentGroup(coordinator=coordinator, specialists=[specialist])
    runtime = AgentRuntime(target=group)

    req = TurnRequest(
        session_id="delegation_session",
        turn_id="turn_1",
        user_message="I need a specialist.",
    )

    frames = [frame async for frame in runtime.stream_turn(req)]

    frame_types = [f.event_type for f in frames]
    assert "agent_transfer" in frame_types
    transfer_frame = next(f for f in frames if f.event_type == "agent_transfer")
    assert transfer_frame.agent_name == "specialist"


@pytest.mark.asyncio
async def test_agent_runtime_turn_budget_and_compaction() -> None:
    compacted_calls: list[int] = []

    def compact(msgs: list[AgentMessage]) -> list[AgentMessage]:
        compacted_calls.append(len(msgs))
        return msgs

    infinite_tool_responses = [
        [ModelDelta(event_type="tool_call", tool_name="square", call_id=f"c{i}", arguments={"n": i})] for i in range(5)
    ]
    model = MockModelClient(responses=infinite_tool_responses)
    agent = Agent(name="loop_bot", tools=[square], model=model)
    runtime = AgentRuntime(
        target=agent,
        budget=TurnBudget(max_turns=5, max_tool_calls=2),
        compaction_hook=compact,
    )

    with pytest.raises(BudgetExceededError) as exc_info:
        await runtime.run_turn(TurnRequest(session_id="budget_sess", turn_id="t1", user_message="Loop"))

    assert "max_tool_calls" in str(exc_info.value)
    assert len(compacted_calls) >= 1
