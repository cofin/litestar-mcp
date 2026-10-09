"""Two-phase DynamicWorkflow DAG execution engine for multi-agent pipelines."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import anyio

from litestar_mcp.agent.runtime import AgentRuntime, TurnRequest
from litestar_mcp.agent.spec import Agent

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(slots=True)
class StepResult:
    """Result record for a single executed workflow step."""

    node_name: str
    output: Any
    status: str = "completed"


@dataclass(slots=True)
class WorkflowContext:
    """Execution context tracking state and step outputs across a workflow run."""

    run_id: str = field(default_factory=lambda: uuid4().hex)
    state: dict[str, Any] = field(default_factory=dict)
    node_outputs: dict[str, Any] = field(default_factory=dict)
    step_results: dict[str, StepResult] = field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        if key in self.state:
            return self.state[key]
        return self.node_outputs[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.state[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a value from workflow state or node outputs."""
        if key in self.state:
            return self.state[key]
        return self.node_outputs.get(key, default)


@dataclass(slots=True)
class WorkflowNode:
    """A single execution node within a DynamicWorkflow DAG."""

    name: str
    agent: Agent | None = None
    handler: Callable[..., Any] | Agent | None = None
    dependencies: list[str] = field(default_factory=list)
    depends_on: list[str] | None = None
    timeout_seconds: float = 300.0
    condition: Callable[[WorkflowContext], bool] | None = None

    def __post_init__(self) -> None:
        if isinstance(self.handler, Agent) and self.agent is None:
            self.agent = self.handler
            self.handler = None
        if self.depends_on is not None:
            merged = list(self.dependencies)
            for dep in self.depends_on:
                if dep not in merged:
                    merged.append(dep)
            self.dependencies = merged


class DynamicWorkflow:
    """DAG-based workflow connecting agents and discrete task handlers."""

    def __init__(self, name: str = "workflow") -> None:
        self.name = name
        self.nodes: dict[str, WorkflowNode] = {}

    def add_node(
        self,
        name_or_node: str | WorkflowNode,
        *,
        agent: Agent | None = None,
        handler: Callable[..., Any] | Agent | None = None,
        depends_on: list[str] | None = None,
        timeout_seconds: float = 300.0,
        condition: Callable[[WorkflowContext], bool] | None = None,
    ) -> DynamicWorkflow:
        """Add a step to the workflow with explicit dependencies."""
        if isinstance(name_or_node, WorkflowNode):
            node = name_or_node
            if node.name in self.nodes:
                msg = f"Node '{node.name}' already exists in workflow '{self.name}'"
                raise ValueError(msg)
            self.nodes[node.name] = node
            return self

        name = name_or_node
        if name in self.nodes:
            msg = f"Node '{name}' already exists in workflow '{self.name}'"
            raise ValueError(msg)
        if agent is None and handler is None:
            msg = f"Node '{name}' requires either an agent or a handler"
            raise ValueError(msg)

        node = WorkflowNode(
            name=name,
            agent=agent,
            handler=handler,
            dependencies=list(depends_on or []),
            timeout_seconds=timeout_seconds,
            condition=condition,
        )
        self.nodes[name] = node
        return self

    def get_ready_nodes(self, completed: set[str]) -> list[str]:
        """Return names of all nodes whose dependencies are satisfied and not yet completed."""
        ready: list[str] = []
        for name, node in self.nodes.items():
            if name in completed:
                continue
            if all(dep in completed for dep in node.dependencies):
                ready.append(name)
        return ready


class WorkflowEngine:
    """Two-phase execution engine for DynamicWorkflow DAG pipelines."""

    def __init__(self, default_runtime: AgentRuntime | None = None) -> None:
        self.default_runtime = default_runtime

    async def execute_node(
        self,
        workflow: DynamicWorkflow,
        node_name: str,
        context_or_state: WorkflowContext | dict[str, Any],
    ) -> Any:
        """Execute a single workflow node against current workflow context or state."""
        node = workflow.nodes[node_name]
        ctx = (
            context_or_state
            if isinstance(context_or_state, WorkflowContext)
            else WorkflowContext(state=context_or_state, node_outputs=context_or_state)
        )

        if node.condition is not None and not node.condition(ctx):
            return None

        with anyio.fail_after(node.timeout_seconds):
            if node.handler is not None and callable(node.handler):
                res = node.handler(ctx)
                if inspect.isawaitable(res):
                    return await res
                return res

            if node.agent is not None:
                runtime = self.default_runtime or AgentRuntime(target=node.agent)
                prompt_input = str(ctx.get("input") or ctx.get(node_name, ""))
                req = TurnRequest(
                    session_id=workflow.name,
                    turn_id=node_name,
                    user_message=prompt_input,
                )
                turn_res = await runtime.run_turn(req, agent=node.agent)
                return turn_res.output

        msg = f"Node '{node_name}' has no executable target"
        raise RuntimeError(msg)

    async def execute_run(
        self,
        workflow: DynamicWorkflow,
        initial_state: dict[str, Any] | None = None,
    ) -> WorkflowContext:
        """Execute workflow in DAG topological order, fanning out concurrent ready nodes via anyio."""
        ctx = WorkflowContext(state=dict(initial_state or {}))
        completed: set[str] = set()

        while len(completed) < len(workflow.nodes):
            ready = workflow.get_ready_nodes(completed)
            if not ready:
                unresolved = set(workflow.nodes.keys()) - completed
                msg = f"Workflow stalled: circular or unresolved dependencies in {unresolved}"
                raise RuntimeError(msg)

            step_outputs: dict[str, Any] = {}

            async def _run_step(step_name: str, target_dict: dict[str, Any] = step_outputs) -> None:
                target_dict[step_name] = await self.execute_node(workflow, step_name, ctx)

            async with anyio.create_task_group() as tg:
                for name in ready:
                    tg.start_soon(_run_step, name)

            for name in ready:
                out = step_outputs[name]
                completed.add(name)
                ctx.state[name] = out
                ctx.node_outputs[name] = out
                ctx.step_results[name] = StepResult(node_name=name, output=out)

        return ctx

    async def run(
        self,
        workflow: DynamicWorkflow,
        initial_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute the workflow to completion and return the final state dictionary."""
        ctx = await self.execute_run(workflow, initial_state=initial_state)
        return ctx.state


__all__ = (
    "DynamicWorkflow",
    "StepResult",
    "WorkflowContext",
    "WorkflowEngine",
    "WorkflowNode",
)
