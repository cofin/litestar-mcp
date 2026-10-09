"""Unit tests for AgentRuntime producer streaming, budgets, sessions, and cancellation."""

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import anyio
import pytest

from litestar_mcp.agent.guards import BudgetExceededError, TurnBudget
from litestar_mcp.agent.models import GoogleGenAIClient, MockModelClient, ModelDelta
from litestar_mcp.agent.runtime import AgentRuntime, TurnRequest
from litestar_mcp.agent.spec import Agent, AgentGroup, AgentMessage
from litestar_mcp.core.context import ToolContext
from litestar_mcp.core.tools import tool

if TYPE_CHECKING:
    from litestar_mcp.agent.streaming import AgentStreamFrame


@tool(description="Calculate square.")
def square(n: int) -> int:
    """Return square of integer."""
    return n * n


@pytest.mark.anyio
async def test_stream_deadline_never_cancels_consumer() -> None:
    """Consumer processing time never counts against the turn deadline."""
    mock_responses = [
        [
            ModelDelta(event_type="delta", text="part 1"),
            ModelDelta(event_type="delta", text="part 2"),
        ]
    ]
    model = MockModelClient(responses=mock_responses)
    agent = Agent(name="fast_bot", model=model)
    runtime = AgentRuntime(target=agent, budget=TurnBudget(timeout_seconds=0.2))

    received_frames: list[AgentStreamFrame] = []
    async with runtime.open_stream(TurnRequest(user_message="hi")) as stream:
        async for frame in stream:
            received_frames.append(frame)
            await anyio.sleep(0.15)

    event_types = [f.event_type for f in received_frames]
    assert "complete" in event_types
    assert "error" not in event_types


@pytest.mark.anyio
async def test_model_stall_times_out_with_error_frame() -> None:
    """Model awaiting past timeout triggers BudgetExceededError and error frame."""

    class StallingModelClient:
        """Simulate model hanging indefinitely."""

        async def stream_turn(self, **kwargs: Any) -> AsyncIterator[ModelDelta]:
            """Sleep indefinitely to trigger timeout."""
            await anyio.sleep(10.0)
            yield ModelDelta(event_type="delta", text="never reached")

    agent = Agent(name="stall_bot", model=StallingModelClient())
    runtime = AgentRuntime(target=agent, budget=TurnBudget(timeout_seconds=0.05))

    with pytest.raises(BudgetExceededError, match="Turn timed out"):
        await runtime.run_turn(TurnRequest(user_message="stall"))

    received_frames: list[AgentStreamFrame] = []
    async with runtime.open_stream(TurnRequest(user_message="stall")) as stream:
        received_frames.extend([frame async for frame in stream])

    error_frames = [f for f in received_frames if f.event_type == "error"]
    assert len(error_frames) == 1
    assert "Turn timed out" in str(error_frames[0].payload)


@pytest.mark.anyio
async def test_tool_loop_and_frames() -> None:
    """Multi-turn tool loop emits ordered frames with sequential sequence numbers."""
    mock_responses = [
        [
            ModelDelta(event_type="thought", text="Need square"),
            ModelDelta(event_type="tool_call", tool_name="square", call_id="c1", arguments={"n": 5}),
        ],
        [
            ModelDelta(event_type="delta", text="The square is 25."),
        ],
    ]
    model = MockModelClient(responses=mock_responses)
    agent = Agent(name="math_bot", tools=[square], model=model)
    runtime = AgentRuntime(target=agent)

    received_frames: list[AgentStreamFrame] = []
    async with runtime.open_stream(TurnRequest(turn_id="t1", user_message="What is square of 5?")) as stream:
        received_frames.extend([frame async for frame in stream])

    event_types = [f.event_type for f in received_frames]
    assert event_types == ["session", "thought", "tool_call", "tool_result", "delta", "complete"]
    seqs = [f.seq for f in received_frames]
    assert seqs == [1, 2, 3, 4, 5, 6]


@pytest.mark.anyio
async def test_delegation_emits_agent_transfer() -> None:
    """AgentGroup delegation emits agent_transfer frame."""
    coordinator_responses = [
        [
            ModelDelta(
                event_type="tool_call",
                tool_name="transfer_to_agent",
                call_id="trans_1",
                arguments={"target_agent": "specialist", "reason": "Needs expert"},
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

    received_frames: list[AgentStreamFrame] = []
    async with runtime.open_stream(TurnRequest(turn_id="t1", user_message="Help")) as stream:
        received_frames.extend([frame async for frame in stream])

    event_types = [f.event_type for f in received_frames]
    assert "agent_transfer" in event_types
    transfer_frame = next(f for f in received_frames if f.event_type == "agent_transfer")
    assert transfer_frame.agent_name == "specialist"


@pytest.mark.anyio
async def test_max_tool_calls_budget() -> None:
    """Exceeding max_tool_calls budget raises BudgetExceededError."""
    infinite_tool_responses = [
        [ModelDelta(event_type="tool_call", tool_name="square", call_id=f"c{i}", arguments={"n": i})] for i in range(5)
    ]
    model = MockModelClient(responses=infinite_tool_responses)
    agent = Agent(name="loop_bot", tools=[square], model=model)
    runtime = AgentRuntime(target=agent, budget=TurnBudget(max_turns=5, max_tool_calls=2))

    with pytest.raises(BudgetExceededError, match="max_tool_calls"):
        await runtime.run_turn(TurnRequest(user_message="Loop"))


@pytest.mark.anyio
async def test_compaction_hook_invoked() -> None:
    """Compaction hook is invoked prior to model turns."""
    compacted_calls: list[int] = []

    def compact(msgs: list[AgentMessage]) -> list[AgentMessage]:
        compacted_calls.append(len(msgs))
        return msgs

    model = MockModelClient(responses=[[ModelDelta(event_type="delta", text="done")]])
    agent = Agent(name="comp_bot", model=model)
    runtime = AgentRuntime(target=agent, compaction_hook=compact)

    await runtime.run_turn(TurnRequest(user_message="Hello"))
    assert len(compacted_calls) == 1


@pytest.mark.anyio
async def test_concurrent_turns_same_session_serialize() -> None:
    """Concurrent turns for the same session serialize via lock without lost messages."""
    model = MockModelClient(
        responses=[
            [ModelDelta(event_type="delta", text="reply 1")],
            [ModelDelta(event_type="delta", text="reply 2")],
        ]
    )
    agent = Agent(name="serial_bot", model=model)
    runtime = AgentRuntime(target=agent)

    ctx = ToolContext(state={"user": "u1", "tenant": "t1"})

    req1 = TurnRequest(session_id="shared_sess", user_message="msg1", context=ctx)
    req2 = TurnRequest(session_id="shared_sess", user_message="msg2", context=ctx)

    _res1, _res2 = await asyncio.gather(
        runtime.run_turn(req1),
        runtime.run_turn(req2),
    )

    history = await runtime.get_history(ctx, "shared_sess")
    assert history is not None
    user_contents = [m.content for m in history if m.role == "user"]
    assistant_contents = [m.content for m in history if m.role == "assistant"]
    assert "msg1" in user_contents
    assert "msg2" in user_contents
    assert "reply 1" in assistant_contents
    assert "reply 2" in assistant_contents


@pytest.mark.anyio
async def test_anonymous_session_id_must_exist() -> None:
    """Anonymous callers cannot pick arbitrary new session IDs."""
    model = MockModelClient(responses=[[ModelDelta(event_type="delta", text="ok")]])
    agent = Agent(name="bot", model=model)
    runtime = AgentRuntime(target=agent)

    req_guess = TurnRequest(session_id="client_guess", user_message="hello")
    res1 = await runtime.run_turn(req_guess)
    assert res1.session_id != "client_guess"

    req_continue = TurnRequest(session_id=res1.session_id, user_message="continuation")
    res2 = await runtime.run_turn(req_continue)
    assert res2.session_id == res1.session_id


@pytest.mark.anyio
async def test_get_history_owner_scoped() -> None:
    """get_history returns messages only when requested with matching owner key."""
    model = MockModelClient(responses=[[ModelDelta(event_type="delta", text="secret answer")]])
    agent = Agent(name="bot", model=model)
    runtime = AgentRuntime(target=agent)

    ctx_a = ToolContext(state={"user": "alice", "tenant": "corp"})
    ctx_b = ToolContext(state={"user": "bob", "tenant": "corp"})

    await runtime.run_turn(TurnRequest(session_id="alice_sess", user_message="secret", context=ctx_a))

    history_a = await runtime.get_history(ctx_a, "alice_sess")
    history_b = await runtime.get_history(ctx_b, "alice_sess")
    history_anon = await runtime.get_history(None, "alice_sess")

    assert history_a is not None
    assert len(history_a) == 2
    assert history_b is None
    assert history_anon is None


@pytest.mark.anyio
async def test_open_stream_exit_cancels_producer() -> None:
    """Exiting open_stream context early cancels producer task group."""
    aclose_called = False

    class TrackingModelClient:
        """Model client tracking aclose invocation on early cancellation."""

        async def stream_turn(self, **kwargs: Any) -> AsyncIterator[ModelDelta]:
            """Yield one delta and then sleep indefinitely."""
            try:
                yield ModelDelta(event_type="delta", text="first chunk")
                await anyio.sleep(10.0)
            finally:
                nonlocal aclose_called
                aclose_called = True

    agent = Agent(name="cancel_bot", model=TrackingModelClient())
    runtime = AgentRuntime(target=agent)

    async with runtime.open_stream(TurnRequest(user_message="start")) as stream:
        async for frame in stream:
            if frame.event_type == "delta":
                break

    await anyio.sleep(0.05)
    assert aclose_called is True


def test_string_model_clients_cached() -> None:
    """Agent with string model caches GoogleGenAIClient instance."""
    agent = Agent(name="gemini_bot", model="gemini-3.8-flash")
    runtime = AgentRuntime(target=agent)

    client1 = runtime._resolve_model(agent)
    client2 = runtime._resolve_model(agent)
    assert client1 is client2
    assert isinstance(client1, GoogleGenAIClient)
    assert client1.model == "gemini-3.8-flash"
