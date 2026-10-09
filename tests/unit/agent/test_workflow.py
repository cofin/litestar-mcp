"""Unit tests for DynamicWorkflow and WorkflowEngine."""

from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest

from litestar_mcp.agent.models import MockModelClient, ModelDelta
from litestar_mcp.agent.runtime import AgentRuntime, TurnRequest
from litestar_mcp.agent.spec import Agent, AgentMessage
from litestar_mcp.agent.workflow import (
    DynamicWorkflow,
    WorkflowContext,
    WorkflowEngine,
    WorkflowNode,
)


@pytest.mark.anyio
async def test_dynamic_workflow_linear_and_branching() -> None:
    """DynamicWorkflow runs nodes in dependency order and aggregates state."""
    wf = DynamicWorkflow(name="pipeline")

    async def step_a(state: dict[str, Any]) -> int:
        val = state.get("input", 0)
        return int(val) + 10

    async def step_b1(state: dict[str, Any]) -> int:
        return int(state["step_a"]) * 2

    async def step_b2(state: dict[str, Any]) -> int:
        return int(state["step_a"]) + 5

    async def step_c(state: dict[str, Any]) -> int:
        return int(state["step_b1"]) + int(state["step_b2"])

    wf.add_node("step_a", handler=step_a)
    wf.add_node("step_b1", handler=step_b1, depends_on=["step_a"])
    wf.add_node("step_b2", handler=step_b2, depends_on=["step_a"])
    wf.add_node("step_c", handler=step_c, depends_on=["step_b1", "step_b2"])

    engine = WorkflowEngine()
    final_state = await engine.run(wf, initial_state={"input": 5})

    assert final_state["step_a"] == 15
    assert final_state["step_b1"] == 30
    assert final_state["step_b2"] == 20
    assert final_state["step_c"] == 50


@pytest.mark.anyio
async def test_workflow_passes_upstream_output_and_isolates_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Downstream agent receives joined upstream outputs and isolated session IDs."""
    captured_messages: list[str] = []
    captured_sessions: list[str] = []

    class CapturingModel(MockModelClient):
        """Capturing model for testing."""

        def __init__(self) -> None:
            super().__init__(responses=[])

        async def stream_turn(
            self,
            *,
            system_instruction: str | None = None,
            messages: Sequence[AgentMessage],
            tools: Sequence[Any] = (),
        ) -> AsyncIterator[ModelDelta]:
            """Yield response delta and record call parameters."""
            last_msg = messages[-1].content if messages else ""
            captured_messages.append(last_msg)
            yield ModelDelta(event_type="delta", text=f"Answer to: {last_msg}")

    original_run_turn = AgentRuntime.run_turn

    async def capturing_run_turn(runtime_self: AgentRuntime, turn: TurnRequest) -> Any:
        if turn.session_id:
            captured_sessions.append(turn.session_id)
        return await original_run_turn(runtime_self, turn)

    monkeypatch.setattr(AgentRuntime, "run_turn", capturing_run_turn)

    agent_a = Agent(name="agent_a", model=CapturingModel())
    agent_b = Agent(name="agent_b", model=CapturingModel())

    wf = DynamicWorkflow(name="handoff")
    wf.add_node("node_a", agent=agent_a)
    wf.add_node("node_b", agent=agent_b, depends_on=["node_a"])

    engine = WorkflowEngine()
    ctx = await engine.execute_run(wf, initial_state={"input": "Initial query"})

    assert "Answer to: Initial query" in str(ctx.node_outputs["node_a"])
    assert "Answer to: Initial query" in str(ctx.node_outputs["node_b"])
    assert "node_a" in str(captured_sessions[0])
    assert "node_b" in str(captured_sessions[1])


@pytest.mark.anyio
async def test_workflow_execute_run_skip_condition_and_cycle_error() -> None:
    """WorkflowEngine handles condition skips and detects circular dependencies."""
    wf = DynamicWorkflow(name="agent_dag")
    wf.add_node("root", handler=lambda _ctx: "root_data")
    wf.add_node(
        WorkflowNode(
            name="skipped_branch",
            handler=lambda _ctx: "should not run",
            depends_on=["root"],
            condition=lambda _ctx: False,
        )
    )

    engine = WorkflowEngine()
    ctx = await engine.execute_run(wf, initial_state={"input": "Summarize"})
    assert isinstance(ctx, WorkflowContext)
    assert ctx.node_outputs["root"] == "root_data"
    assert ctx.node_outputs["skipped_branch"] is None
    assert ctx.step_results["skipped_branch"].status == "skipped"

    cyclic = DynamicWorkflow(name="cyclic")
    cyclic.add_node("n1", handler=lambda _s: 1, depends_on=["n2"])
    cyclic.add_node("n2", handler=lambda _s: 2, depends_on=["n1"])
    with pytest.raises(RuntimeError, match="circular or unresolved"):
        await engine.execute_run(cyclic)
