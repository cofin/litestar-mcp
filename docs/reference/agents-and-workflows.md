---
title: Agents, Sessions & Workflows API
---

# Agents, Sessions & Workflows API

API reference for `litestar_mcp.agent`: declarative `Agent` and `AgentGroup` specs, `AgentRuntime` and `TurnBudget`, `AgentChatController`, pluggable `SessionStore` implementations (`ANONYMOUS_OWNER`, `MemorySessionStore`, `LitestarStoreSessionStore`), model adapters (`ModelClient`, `GoogleGenAIClient`, `MockModelClient`), SSE `AgentStreamFrame` streaming, and in-process `DynamicWorkflow` / `WorkflowEngine` DAG execution.

For guides and patterns, see [Agent Runtime & Turn Budgets](../agents/runtime-and-budgets.md), [Agent Groups & Handoffs](../agents/groups-and-handoffs.md), [Sessions & SSE Streaming](../agents/sessions-and-streaming.md), and [Durable DAG Workflows](../queues/durable-workflows.md).

## Agent & Group Specifications

::: litestar_mcp.agent.spec.Agent

::: litestar_mcp.agent.spec.AgentGroup

::: litestar_mcp.agent.spec.AgentMessage

## Agent Runtime & Turn Budgets

::: litestar_mcp.agent.runtime.AgentRuntime

::: litestar_mcp.agent.runtime.TurnRequest

::: litestar_mcp.agent.runtime.TurnResponse

::: litestar_mcp.agent.guards.TurnBudget

::: litestar_mcp.agent.guards.BudgetExceededError

## HTTP & SSE Chat Controller

::: litestar_mcp.agent.controller.AgentChatController

::: litestar_mcp.agent.controller.ChatRequest

::: litestar_mcp.agent.controller.TurnResult

::: litestar_mcp.agent.controller.SessionHistory

::: litestar_mcp.agent.controller.SessionNotFoundError

## Session Stores

::: litestar_mcp.agent.sessions.ANONYMOUS_OWNER

::: litestar_mcp.agent.sessions.SessionStore

::: litestar_mcp.agent.sessions.MemorySessionStore

::: litestar_mcp.agent.sessions.LitestarStoreSessionStore

## Model Clients & Streaming Frames

::: litestar_mcp.agent.models.ModelClient

::: litestar_mcp.agent.models.GoogleGenAIClient

::: litestar_mcp.agent.models.MockModelClient

::: litestar_mcp.agent.models.ModelDelta

::: litestar_mcp.agent.streaming.AgentStreamFrame

::: litestar_mcp.agent.streaming.iter_agent_stream

## DAG Workflows

::: litestar_mcp.agent.workflow.DynamicWorkflow

::: litestar_mcp.agent.workflow.WorkflowNode

::: litestar_mcp.agent.workflow.WorkflowEngine

::: litestar_mcp.agent.workflow.WorkflowContext

::: litestar_mcp.agent.workflow.StepResult
