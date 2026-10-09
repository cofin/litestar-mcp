"""In-process concurrent DAG execution engine for multi-agent workflows."""

import inspect
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

import anyio

from litestar_mcp.agent.runtime import AgentRuntime, TurnRequest
from litestar_mcp.agent.spec import Agent


@dataclass(slots=True)
class StepResult:
    """Result record for a single executed workflow step."""

    node_name: str
    output: Any
    status: Literal["completed", "skipped", "failed"] = "completed"


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
    depends_on: tuple[str, ...] | Sequence[str] = ()
    prompt: Callable[[WorkflowContext], str] | None = None
    timeout_seconds: float = 300.0
    condition: Callable[[WorkflowContext], bool] | None = None

    def __post_init__(self) -> None:
        if isinstance(self.handler, Agent) and self.agent is None:
            object.__setattr__(self, "agent", self.handler)
            object.__setattr__(self, "handler", None)
        if not isinstance(self.depends_on, tuple):
            object.__setattr__(self, "depends_on", tuple(self.depends_on))

    @property
    def dependencies(self) -> list[str]:
        """Backward-compatible list representation of node dependencies."""
        return list(self.depends_on)


class DynamicWorkflow:
    """DAG-based workflow connecting agents and discrete task handlers."""

    def __init__(self, name: str = "workflow") -> None:
        """Initialize DynamicWorkflow with a name."""
        self.name = name
        self.nodes: dict[str, WorkflowNode] = {}

    def add_node(
        self,
        name_or_node: str | WorkflowNode,
        *,
        agent: Agent | None = None,
        handler: Callable[..., Any] | None = None,
        depends_on: tuple[str, ...] | list[str] | None = None,
        dependencies: tuple[str, ...] | list[str] | None = None,
        prompt: Callable[[WorkflowContext], str] | None = None,
        timeout_seconds: float = 300.0,
        condition: Callable[[WorkflowContext], bool] | None = None,
    ) -> "DynamicWorkflow":
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

        resolved_deps = tuple(depends_on if depends_on is not None else (dependencies or ()))
        node = WorkflowNode(
            name=name,
            agent=agent,
            handler=handler,
            depends_on=resolved_deps,
            prompt=prompt,
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
            if all(dep in completed for dep in node.depends_on):
                ready.append(name)
        return ready


class WorkflowEngine:
    """Concurrent in-process DAG execution engine for DynamicWorkflow pipelines."""

    def __init__(self, default_runtime: AgentRuntime | None = None) -> None:
        """Initialize WorkflowEngine with optional default AgentRuntime."""
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
            ctx.step_results[node_name] = StepResult(node_name=node_name, output=None, status="skipped")
            return None

        with anyio.fail_after(node.timeout_seconds):
            if node.handler is not None and callable(node.handler):
                res = node.handler(ctx)
                if inspect.isawaitable(res):
                    return await res
                return res

            if node.agent is not None:
                runtime = self.default_runtime or AgentRuntime(target=node.agent)
                if node.prompt is not None:
                    prompt_input = node.prompt(ctx)
                else:
                    upstream_parts = [
                        str(ctx.node_outputs[dep])
                        for dep in node.depends_on
                        if dep in ctx.node_outputs and ctx.node_outputs[dep] is not None
                    ]
                    prompt_input = "\n\n".join(upstream_parts) if upstream_parts else str(ctx.get("input") or "")

                req = TurnRequest(
                    session_id=f"{ctx.run_id}:{node.name}",
                    turn_id=node.name,
                    user_message=prompt_input,
                )
                turn_res = await runtime.run_turn(req)
                return turn_res.content

        msg = f"Node '{node_name}' has no executable target"
        raise RuntimeError(msg)

    @staticmethod
    def _validate_dependencies(workflow: DynamicWorkflow) -> None:
        """Validate that all node dependencies exist and graph is acyclic."""
        all_nodes = set(workflow.nodes.keys())
        for name, node in workflow.nodes.items():
            for dep in node.depends_on:
                if dep not in all_nodes:
                    msg = f"Node '{name}' depends on unknown node '{dep}'"
                    raise ValueError(msg)

        in_degrees: dict[str, int] = {name: len(node.depends_on) for name, node in workflow.nodes.items()}
        dependents: dict[str, list[str]] = defaultdict(list)
        for name, node in workflow.nodes.items():
            for dep in node.depends_on:
                dependents[dep].append(name)

        ready = [name for name, deg in in_degrees.items() if deg == 0]
        visited = 0
        while ready:
            curr = ready.pop()
            visited += 1
            for nxt in dependents[curr]:
                in_degrees[nxt] -= 1
                if in_degrees[nxt] == 0:
                    ready.append(nxt)

        if visited < len(workflow.nodes):
            unresolved = set(workflow.nodes.keys()) - {n for n, deg in in_degrees.items() if deg == 0}
            msg = f"Workflow stalled: circular or unresolved dependencies in {unresolved}"
            raise RuntimeError(msg)

    async def execute_run(
        self,
        workflow: DynamicWorkflow,
        initial_state: dict[str, Any] | None = None,
    ) -> WorkflowContext:
        """Execute workflow in DAG topological order via ready queue scheduling."""
        ctx = WorkflowContext(state=dict(initial_state or {}))
        self._validate_dependencies(workflow)

        in_degrees: dict[str, int] = {name: len(node.depends_on) for name, node in workflow.nodes.items()}
        dependents: dict[str, list[str]] = defaultdict(list)
        for name, node in workflow.nodes.items():
            for dep in node.depends_on:
                dependents[dep].append(name)

        send_channel, receive_channel = anyio.create_memory_object_stream[str](max_buffer_size=len(workflow.nodes) + 1)
        completed: set[str] = set()

        for name, deg in in_degrees.items():
            if deg == 0:
                await send_channel.send(name)

        if not workflow.nodes:
            return ctx

        async with anyio.create_task_group() as tg:

            async def _worker(node_name: str) -> None:
                try:
                    out = await self.execute_node(workflow, node_name, ctx)
                    if node_name not in ctx.step_results:
                        ctx.step_results[node_name] = StepResult(node_name=node_name, output=out, status="completed")
                    ctx.node_outputs[node_name] = out
                    ctx.state[node_name] = out
                    completed.add(node_name)

                    for dep in dependents[node_name]:
                        in_degrees[dep] -= 1
                        if in_degrees[dep] == 0:
                            await send_channel.send(dep)
                except Exception:
                    ctx.step_results[node_name] = StepResult(node_name=node_name, output=None, status="failed")
                    tg.cancel_scope.cancel()
                    raise

            for _ in range(len(workflow.nodes)):
                if len(completed) + 1 > len(workflow.nodes):
                    break
                try:
                    with anyio.fail_after(3600.0):
                        next_node = await receive_channel.receive()
                except TimeoutError as exc:
                    unresolved = set(workflow.nodes.keys()) - completed
                    msg = f"Workflow stalled: circular or unresolved dependencies in {unresolved}"
                    raise RuntimeError(msg) from exc
                tg.start_soon(_worker, next_node)

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
