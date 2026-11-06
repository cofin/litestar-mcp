---
title: Durable DAG Workflows
---

# Durable DAG Workflows (`DynamicWorkflow` & `DurableWorkflowEngine`)

`litestar-mcp` provides a declarative Directed Acyclic Graph (DAG) workflow abstraction (`DynamicWorkflow` and `WorkflowNode`) that can run either in-process via `WorkflowEngine` or across distributed `litestar-queues` worker replicas via `DurableWorkflowEngine` and `WorkflowStore`.

--8<-- "tools/docs/includes/diagram-durable-workflows.html"

---

## Defining DAGs

A `DynamicWorkflow` connects discrete Python functions (`handler`) and `Agent` turns (`agent`) into an acyclic dependency graph. At registration time, `WorkflowEngine.validate_dependencies(workflow)` verifies that all `depends_on` references exist and that the graph contains no cycles.

```python title="docs/examples/snippets/durable_workflow.py"
--8<-- "docs/examples/snippets/durable_workflow.py:example"
```

### WorkflowNode Parameters

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `name_or_node` | `str or WorkflowNode` | *(required)* | Unique node identifier within the `DynamicWorkflow`, or a pre-constructed `WorkflowNode` instance. |
| `agent` | `Agent or None` | `None` | `Agent` executed via `AgentRuntime.run_turn()` when this node runs. |
| `handler` | `Callable[[WorkflowContext], Any] or Agent or None` | `None` | Sync or async callable accepting `WorkflowContext` and returning a JSON-serializable output (or an `Agent`, which is automatically normalized to `node.agent`). Either `agent` or `handler` must be supplied. |
| `depends_on` | `tuple[str, ...] or list[str] or None` | `()` | Upstream node names that must reach a terminal state before this node becomes ready. |
| `prompt` | `Callable[[WorkflowContext], str] or None` | `None` | Optional callable building the user prompt string for an `agent` node from `WorkflowContext`. When omitted, upstream `node_outputs` are joined with `"\n\n"` (falling back to `ctx.get("input", "")`). |
| `timeout_seconds` | `float` | `300.0` | Maximum execution time in seconds enforced via `anyio.fail_after(node.timeout_seconds)`. |
| `condition` | `Callable[[WorkflowContext], bool] or None` | `None` | Optional predicate evaluated against `WorkflowContext` before execution. When `condition(ctx)` returns `False`—or when any upstream dependency in `depends_on` did not complete with `status == "completed"` (`should_skip`)—the node is recorded with `status="skipped"`. |

---

## In-Process WorkflowEngine vs Distributed DurableWorkflowEngine

The exact same `DynamicWorkflow` instance can be executed in-process or distributed across `litestar-queues`:

| Feature | `WorkflowEngine` (`litestar_mcp.agent.workflow`) | `DurableWorkflowEngine` (`litestar_mcp.queues.workflow`) |
| :--- | :--- | :--- |
| **Execution Topology** | Single-process AnyIO `TaskGroup` + memory object stream | Distributed `litestar-queues` tasks (`advance_workflow`, `execute_workflow_node`, `watch_workflow`) |
| **State Persistence** | In-memory `WorkflowContext` returned directly by `await engine.execute_run(wf)` or `await engine.run(wf)` | Durable `WorkflowStore` backed by the `"litestar_mcp_workflows"` Litestar `Store` |
| **Litestar Integration** | Plain Python class instantiated on demand | `InitPluginProtocol` registering `workflow_engine` DI provider and attaching to `QueuePlugin` via `TaskDependencyChain` |
| **Crash Recovery** | Process-bound | Per-run delayed watchdog (`wf-watchdog:{run_id}:{tick}`) + forever-unique node reservations (`wf-node:{run_id}:{node}`) |

### Running In-Process with WorkflowEngine

```python
from litestar_mcp.agent.workflow import DynamicWorkflow, WorkflowContext, WorkflowEngine


async def fetch_items(ctx: WorkflowContext) -> list[str]:
    """Return initial items from workflow state."""
    return list(ctx.state.get("items", []))


async def count_items(ctx: WorkflowContext) -> int:
    """Count items produced by fetch_items."""
    return len(ctx.node_outputs["fetch"])


pipeline = DynamicWorkflow(name="local_pipeline")
pipeline.add_node("fetch", handler=fetch_items)
pipeline.add_node("count", handler=count_items, depends_on=["fetch"])


async def run_local() -> int:
    """Execute the DAG in-process and return the count output."""
    engine = WorkflowEngine()
    ctx = await engine.execute_run(pipeline, initial_state={"items": ["a", "b", "c"]})
    return int(ctx.node_outputs["count"])
```

---

## DurableWorkflowEngine Plugin Architecture

Register `DurableWorkflowEngine(workflows, ...)` in `Litestar(plugins=[QueuePlugin(...), DurableWorkflowEngine([...])])`:

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `workflows` | `Iterable[DynamicWorkflow]` | *(required)* | `DynamicWorkflow` definitions validated and registered in `WorkflowRegistry` across all web and worker processes. |
| `store` | `Store or None` | `None` | Optional Litestar `Store` for `WorkflowStore`. Defaults to the `"litestar_mcp_workflows"` store resolved from `Litestar(stores=...)`. |
| `runtime_factory` | `Callable[[Agent], AgentRuntime] or None` | `None` | Optional factory mapping an `Agent` node target to an `AgentRuntime` (defaults to `AgentRuntime(target=node.agent)`). |
| `queue` | `str` | `"workflow"` | Target `litestar-queues` queue lane for workflow advance, node execution, and watchdog tasks. |
| `watchdog_interval` | `float or None` | `30.0` | Delay in seconds (`run_after`) between self-rearming `watch_workflow` ticks. Pass `None` to disable the watchdog. |
| `run_timeout_seconds` | `float` | `86_400.0` | Maximum wall-clock lifetime in seconds (24 hours) for a workflow run before the watchdog marks the run `"failed"` with `error="workflow timed out"`. |

### Automatic DI & TaskDependencyChain Wiring

During `on_app_init`, `DurableWorkflowEngine` performs three setup steps automatically:

1. **`TaskDependencyChain.attach`**: Appends `DurableWorkflowEngine.resolve_task_dependencies` to `QueuePlugin.config.task_dependency_resolver` so the `workflow_engine` argument is injected into `advance_workflow` (`litestar_mcp.workflow.advance`), `execute_workflow_node` (`litestar_mcp.workflow.execute_node`), and `watch_workflow` (`litestar_mcp.workflow.watchdog`). Because `TaskDependencyChain` merges resolvers in order, `QueueTaskExecutor` and `DurableWorkflowEngine` coexist on the same `QueuePlugin` without manual resolver wiring.
2. **Default Store Resolution**: Resolves `"litestar_mcp_workflows"` from `app_config.stores` (`StoreRegistry`) when `store=None`.
3. **Route Handler Dependency (`workflow_engine`)**: Registers `"workflow_engine": Provide(self._provide_engine, sync_to_thread=False)` so any Litestar route handler can declare `workflow_engine: DurableWorkflowEngine` to call `await workflow_engine.start(...)` or `await workflow_engine.snapshot(run_id)`.

---

## WorkflowStore Single-Writer Key Separation

When parallel sibling nodes in a DAG wave finish simultaneously on different worker replicas, updating a single shared run document would cause read-modify-write races and lost node outputs. `WorkflowStore` eliminates sibling write contention by separating run metadata from write-once node records:

| Key Pattern | Record Type | Writer Semantics | Contents |
| :--- | :--- | :--- | :--- |
| `{prefix}:{run_id}:run` (default prefix `litestar_mcp:workflow`) | `WorkflowRun` | Created once at `start()`; updated monotonically when transitioning from `"running"` to `"completed"` or `"failed"` (`set_run_status`) | `run_id`, `workflow`, `state` (initial state dict), `status` (`"running"`, `"completed"`, `"failed"`), `error`, `created_at` |
| `{prefix}:{run_id}:node:{name}` | `NodeRecord` | Write-once per node (`put_node` checks `store.exists(node_key)` and never overwrites an existing outcome) | `node`, `status` (`"completed"`, `"skipped"`, `"failed"`), `output` (JSON-serializable result), `error` |

To inspect a running or completed workflow, call `await workflow_engine.snapshot(run_id)`, which loads the `WorkflowRun` and all persisted `NodeRecord` entries in parallel (`store.get_nodes`) and reconstructs a `WorkflowContext` via `store.to_context(run, nodes)`.

!!! note
    Node outputs must be JSON-serializable. If a node handler returns an unserializable object (`TypeError`, `msgspec.EncodeError`, or `SerializationException`), `DurableWorkflowEngine.execute_node` catches the encoding failure and persists a `NodeRecord(node=node, status="failed", output=None, error="TypeError: node output is not JSON serializable")` so the workflow terminates deterministically instead of stalling.

---

## Forever-Unique Node Scheduling

Multiple completed upstream nodes can trigger `advance_workflow(run_id)` concurrently. To guarantee each node in a run executes at most once:

1. `execute_workflow_node` is decorated with `@task(EXECUTE_NODE_TASK, queue="workflow", retries=0, unique_until="forever", unique_by="arguments")`.
2. `DurableWorkflowEngine.advance(run_id)` enqueues ready nodes with `key=f"wf-node:{run_id}:{name}"`. The queue backend deduplicates concurrent enqueue calls for the same `wf-node:{run_id}:{name}` key.
3. Inside `execute_node(run_id, node)`, `WorkflowStore.get_node(run_id, node)` performs an additional idempotency check before executing the node and enqueuing the next `advance_workflow`.
4. When all nodes in `wf.nodes` have a persisted `NodeRecord` (or when the run times out in `watch()`), `DurableWorkflowEngine` finalizes `WorkflowRun.status` (`"completed"` or `"failed"`) and calls `await service.reset_task_identity(f"wf-node:{run_id}:{name}")` for every node in the workflow to release the unique key reservations cleanly.

---

## Per-Run Watchdog Recovery

If a transient broker or worker crash drops an `advance_workflow` enqueue after a node writes its `NodeRecord`, the per-run watchdog heals the run automatically:

1. **Initial Watchdog Scheduling**: When `watchdog_interval` is not `None` (default `30.0` seconds), `workflow_engine.start()` enqueues `watch_workflow` with `tick=1`, `key=f"wf-watchdog:{run_id}:1"`, and `run_after=self.watchdog_interval`.
2. **Backend Validation**: Delayed `run_after` tasks require an asynchronous queue execution backend (`"local"`, `"redis"`, `"sqlspec"`, etc.). If `QueuePlugin` is configured with `execution_backend="immediate"` while `watchdog_interval` is enabled, `workflow_engine.start()` raises `ImproperlyConfiguredException`.
3. **Timeout & Re-Advance Loop**: On each tick, `DurableWorkflowEngine.watch(run_id, tick)`:
   - Returns immediately if the run is already `"completed"` or `"failed"`.
   - Checks whether `time.time() - run.created_at >= self.run_timeout_seconds`. If exceeded, marks the run `"failed"` with `error="workflow timed out"` and resets all `wf-node:{run_id}:{name}` identities.
   - Calls `await self.advance(run_id)` to schedule any unscheduled ready nodes or finalize the run.
   - If the run is still `"running"`, schedules tick `tick + 1` under `key=f"wf-watchdog:{run_id}:{tick + 1}"` with `run_after=self.watchdog_interval`.
