---
title: Tasks, Queues & Durable Workflows API
---

# Tasks, Queues & Durable Workflows API

API reference for the MCP Tasks protocol runtime (`MCPTaskExecutor`, `MCPTaskRuntime`, `MCPTaskStore`, `TaskRecord`, `ToolTaskCall`, `TaskLookupError`, `TaskStateError`) and the `litestar_mcp.queues` distributed execution layer (`QueueTaskExecutor`, `DurableWorkflowEngine`, `WorkflowStore`, `WorkflowRun`, `NodeRecord`, `TaskDependencyChain`, `QueuedAgentChatController`, `QueuedTurnManager`, `QueuedTurn`, `AgentRegistry`, `PrincipalSnapshot`).

For architectural guides, see [MCP Tasks & Human-in-the-Loop](../queues/mcp-tasks.md), [Distributed Task Queues](../queues/distributed-task-queues.md), [Durable DAG Workflows](../queues/durable-workflows.md), and [Queued Agent Turns](../queues/queued-agent-turns.md).

## MCP Tasks Store & Executor Protocol

::: litestar_mcp.mcp.tasks.MCPTaskExecutor

::: litestar_mcp.mcp.tasks.MCPTaskRuntime

::: litestar_mcp.mcp.tasks.MCPTaskStore

::: litestar_mcp.mcp.tasks.TaskRecord

::: litestar_mcp.mcp.tasks.ToolTaskCall

::: litestar_mcp.mcp.tasks.TaskLookupError

::: litestar_mcp.mcp.tasks.TaskStateError

## Distributed Queue Task Execution

::: litestar_mcp.queues.mcp_tasks.QueueTaskExecutor

::: litestar_mcp.queues.mcp_tasks.run_mcp_tool_task

## Durable DAG Workflow Engine & Persistence

::: litestar_mcp.queues.workflow.DurableWorkflowEngine

::: litestar_mcp.queues.workflow_store.WorkflowStore

::: litestar_mcp.queues.workflow_store.WorkflowRun

::: litestar_mcp.queues.workflow_store.NodeRecord

::: litestar_mcp.queues.dependencies.TaskDependencyChain

## Queued Agent Turns & Streaming Bridge

::: litestar_mcp.queues.bridge.QueuedAgentChatController

::: litestar_mcp.queues.bridge.emit_agent_frame

::: litestar_mcp.queues.bridge.agent_turn_channel

::: litestar_mcp.queues.tasks.QueuedTurnManager

::: litestar_mcp.queues.tasks.QueuedTurn

::: litestar_mcp.queues.tasks.AgentRegistry

::: litestar_mcp.queues.tasks.PrincipalSnapshot

::: litestar_mcp.queues.tasks.run_agent_turn
