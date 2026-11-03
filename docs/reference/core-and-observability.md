---
title: Core Primitives & Observability API
---

# Core Primitives & Observability API

API reference for protocol-agnostic core primitives (`Tool`, `ToolCall`, `ToolResult`, `ToolArgumentError`, `@tool`, `ToolContext`), OpenTelemetry GenAI observability (`TelemetryConfig`, `SpanManager`), JSON-RPC 2.0 wire types (`JSONRPCRequest`, `JSONRPCError`, `JSONRPCErrorException`), SSE `SubscriptionManager`, the `litestar-mcp` exception hierarchy, and internal ASGI scope executors (`execute_tool`, `execute_handler_response`, `blank_http_scope`).

For guides, see [Core Architectural Concepts](../getting-started/concepts.md) and [OpenTelemetry & Observability](../security/observability.md).

---

## Core Tool & Context Primitives (`litestar_mcp.core`)

::: litestar_mcp.core.tools.Tool

::: litestar_mcp.core.tools.ToolCall

::: litestar_mcp.core.tools.ToolResult

::: litestar_mcp.core.tools.ToolArgumentError

::: litestar_mcp.core.tools.tool

::: litestar_mcp.core.context.ToolContext

---

## OpenTelemetry GenAI Observability

::: litestar_mcp.core.observability.config.TelemetryConfig

::: litestar_mcp.core.observability._spans.SpanManager

---

## JSON-RPC 2.0 & Subscription Streams

::: litestar_mcp.core.jsonrpc.JSONRPCRequest

::: litestar_mcp.core.jsonrpc.JSONRPCError

::: litestar_mcp.core.jsonrpc.JSONRPCErrorException

::: litestar_mcp.core.sse.SubscriptionManager

---

## Core Exceptions

::: litestar_mcp.core.exceptions.LitestarMCPError

::: litestar_mcp.core.exceptions.BridgeConnectionError

::: litestar_mcp.core.exceptions.BridgeMessageTooLargeError

::: litestar_mcp.core.exceptions.MissingDependencyError

---

## Internal ASGI Scope Execution (`litestar_mcp.mcp.executor`)

::: litestar_mcp.mcp.executor.execute_tool

::: litestar_mcp.mcp.executor.execute_handler_response

::: litestar_mcp.mcp.executor.blank_http_scope
