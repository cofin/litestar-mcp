---
title: Tasks, Queues & Workflows
---

# Asynchronous Execution, Queues & Durable Workflows

`litestar-mcp` supports two complementary execution models for background MCP Tasks (`io.modelcontextprotocol/tasks`), multi-step DAG workflows (`DynamicWorkflow`), and streaming agent turns (`AgentRuntime`):

1. **Single-Process In-Memory Execution**: Built into the base `litestar-mcp` package with zero external infrastructure dependencies. Ideal for local development, `litestar mcp stdio` servers, and single-replica deployments.
2. **Distributed Queue Execution (`litestar-mcp[queues]`)**: Powered by `litestar-queues`, Litestar's `StoreRegistry`, and `ChannelsPlugin`. Scales tool tasks, DAG nodes, and long-running agent turns across horizontally autoscaled worker replicas.

---

## Single-Process vs Distributed Execution Matrix

| Capability | Single-Process (Base Package) | Distributed (`litestar-mcp[queues]`) | Shared State & Coordination |
| :--- | :--- | :--- | :--- |
| **MCP Tasks (`io.modelcontextprotocol/tasks`)** | `InProcessTaskExecutor` (process-local `asyncio.Task` runners in `MCPTaskStore`) | `QueueTaskExecutor` (auto-wired when `QueuePlugin` is registered; dispatches `litestar_mcp.mcp.run_tool_task`) | `MCPTaskStore` backed by the `"litestar_mcp_tasks"` Litestar `Store` + `mcp-task:{id}:job` tracking |
| **Principal & DI Context** | Clones the active HTTP `Request` scope in-memory (stripping request-scoped Dishka containers for fresh resolution) | Captures `PrincipalSnapshot` (`user`, `auth`, `litestar-security` `SecurityContext`) and rehydrates `Provide` + Dishka `Scope.REQUEST` in workers | `ToolTaskCall` persisted in `TaskRecord.dispatch` (`dispatchInternal`) |
| **Cooperative Cancellation & `input_required` Resume** | Local `asyncio.Event` / `asyncio.Queue` signalling within the originating process | Cross-replica `QueueService.cancel_task(include_running=True)` and stateless `executor.resume(call)` re-enqueueing on `tasks/update` | Shared `Store` record + `litestar-queues` durable cancellation signal |
| **DAG Workflows (`DynamicWorkflow`)** | `WorkflowEngine` (topological wave execution via in-process AnyIO `TaskGroup` and memory streams) | `DurableWorkflowEngine` (plugin injecting `workflow_engine`, scheduling `advance_workflow`, `execute_workflow_node`, and `watch_workflow`) | `WorkflowStore` backed by `"litestar_mcp_workflows"` with single-writer run and write-once node keys |
| **Agent Turn Execution & SSE Streaming** | `AgentChatController` (`POST /agent/chat` and `POST /agent/turns` running inline inside the HTTP request) | `QueuedAgentChatController` + `QueuedTurnManager` (`POST /agent/turns` returns `202 Accepted`; workers run `litestar_mcp.agent.run_turn`) | `LitestarStoreSessionStore` + `ChannelsPlugin` history replay and live `AgentStreamFrame` fan-out |

---

## Architecture Overview

The interactive diagram below illustrates how task-augmented `tools/call` requests, principal snapshots, cooperative cancellation, and multi-round `MCPInputRequiredResult` resumptions flow through `QueueTaskExecutor` and `InProcessTaskExecutor`:

--8<-- "tools/docs/includes/diagram-mcp-tasks-and-queues.html"

---

## Installing `litestar-mcp[queues]`

Install the `queues` extra to unlock `litestar_mcp.queues` (`QueueTaskExecutor`, `DurableWorkflowEngine`, `WorkflowStore`, `TaskDependencyChain`, `QueuedAgentChatController`, `QueuedTurnManager`, `AgentRegistry`, and `PrincipalSnapshot`):

=== "uv"

    ```bash
    uv add "litestar-mcp[queues]"
    ```

=== "pip"

    ```bash
    pip install "litestar-mcp[queues]"
    ```

=== "pdm"

    ```bash
    pdm add "litestar-mcp[queues]"
    ```

=== "poetry"

    ```bash
    poetry add "litestar-mcp[queues]"
    ```

---

## Guides in This Section

<div class="lm-grid">
  <div class="lm-card">
    <div>
      <h3><a href="mcp-tasks.md">MCP Tasks Extension</a></h3>
      <p>Configure <code>MCPTaskConfig</code>, declare task-capable tools, drive the <code>working</code> / <code>input_required</code> state machine with <code>MCPInputRequiredResult</code>, and handle SEP-2663 tool errors.</p>
    </div>
    <a href="mcp-tasks.md">MCP Tasks Guide &rarr;</a>
  </div>

  <div class="lm-card">
    <div>
      <h3><a href="distributed-task-queues.md">Distributed Task Queues</a></h3>
      <p>Auto-wire <code>QueueTaskExecutor</code> with <code>QueuePlugin</code>, override queues per controller or tool, rehydrate <code>SecurityContext</code> and Dishka dependencies in workers, and resume across replicas.</p>
    </div>
    <a href="distributed-task-queues.md">Distributed Queues &rarr;</a>
  </div>

  <div class="lm-card">
    <div>
      <h3><a href="durable-workflows.md">Durable DAG Workflows</a></h3>
      <p>Build <code>DynamicWorkflow</code> and <code>WorkflowNode</code> pipelines, compare <code>WorkflowEngine</code> with <code>DurableWorkflowEngine</code>, and inspect single-writer <code>WorkflowStore</code> keys and watchdog recovery.</p>
    </div>
    <a href="durable-workflows.md">Durable Workflows &rarr;</a>
  </div>

  <div class="lm-card">
    <div>
      <h3><a href="queued-agent-turns.md">Queued Agent Turns</a></h3>
      <p>Offload multi-step <code>AgentRuntime</code> turns to background workers via <code>QueuedAgentChatController</code> and <code>QueuedTurnManager</code> with deterministic SSE frame replay over <code>ChannelsPlugin</code>.</p>
    </div>
    <a href="queued-agent-turns.md">Queued Agent Turns &rarr;</a>
  </div>

  <div class="lm-card">
    <div>
      <h3><a href="deployment.md">Multi-Replica Deployment</a></h3>
      <p>Production checklist for horizontal scaling: stateless Streamable HTTP routing, shared <code>StoreRegistry</code>, cross-worker <code>ChannelsPlugin</code>, external worker placement, and conformance testing.</p>
    </div>
    <a href="deployment.md">Deployment Checklist &rarr;</a>
  </div>
</div>
