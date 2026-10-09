from __future__ import annotations

from typing import Any

import pytest

from litestar_mcp.agent.models import MockModelClient, ModelDelta
from litestar_mcp.agent.spec import Agent
from litestar_mcp.agent.workflow import (
    DynamicWorkflow,
    WorkflowContext,
    WorkflowEngine,
    WorkflowNode,
)


@pytest.mark.asyncio
async def test_dynamic_workflow_linear_and_branching() -> None:
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


@pytest.mark.asyncio
async def test_workflow_execute_run_agent_node_and_cycle_detection() -> None:
    model = MockModelClient(responses=[[ModelDelta(event_type="delta", text="Analyzed output")]])
    analyst = Agent(name="analyst", model=model)

    wf = DynamicWorkflow(name="agent_dag")
    wf.add_node(WorkflowNode(name="analyze", handler=analyst))
    wf.add_node(
        WorkflowNode(
            name="skipped_branch",
            handler=lambda _ctx: "should not run",
            depends_on=["analyze"],
            condition=lambda _ctx: False,
        )
    )

    engine = WorkflowEngine()
    ctx = await engine.execute_run(wf, initial_state={"input": "Summarize report"})
    assert isinstance(ctx, WorkflowContext)
    assert ctx.node_outputs["analyze"] == "Analyzed output"
    assert ctx.node_outputs["skipped_branch"] is None
    assert ctx.step_results["analyze"].status == "completed"

    cyclic = DynamicWorkflow(name="cyclic")
    cyclic.add_node("n1", handler=lambda _s: 1, depends_on=["n2"])
    cyclic.add_node("n2", handler=lambda _s: 2, depends_on=["n1"])
    with pytest.raises(RuntimeError, match="circular or unresolved"):
        await engine.execute_run(cyclic)
