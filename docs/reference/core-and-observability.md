---
title: Core Primitives & Observability API
---

# Core Primitives & Observability API

API reference for protocol-agnostic core primitives (`Tool`, `ToolCall`, `ToolResult`, `ToolArgumentError`, `@tool`, `ToolContext`), OpenTelemetry GenAI observability (`TelemetryConfig`, `SpanManager`), JSON serialization helpers (`encode_json`, `decode_json`, `to_json`, `from_json`), JSON-RPC 2.0 wire types (`JSONRPCRequest`, `JSONRPCError`, `JSONRPCErrorException`), SSE `SubscriptionManager` and RFC 5424 log-level helpers (`validate_log_level`, `log_level_allows`), the `litestar-mcp` exception hierarchy, schema generation and validation helpers (`validate_tool_descriptions`, `typed_dict_to_json_schema`, `unwrap_root_object_schema`, `type_to_output_schema`, `generate_output_schema_for_handler`), and internal ASGI scope executors (`execute_tool`, `execute_handler_response`, `blank_http_scope`).

For guides, see [Core Architectural Concepts](../getting-started/concepts.md) and [OpenTelemetry & Observability](../security/observability.md).

## Core Tool & Context Primitives

::: litestar_mcp.core.tools.Tool

::: litestar_mcp.core.tools.ToolCall

::: litestar_mcp.core.tools.ToolResult

::: litestar_mcp.core.tools.ToolArgumentError

::: litestar_mcp.core.tools.tool

::: litestar_mcp.core.context.ToolContext

## OpenTelemetry GenAI Observability

::: litestar_mcp.core.observability.config.TelemetryConfig

::: litestar_mcp.core.observability._spans.SpanManager

## JSON Serialization Helpers

::: litestar_mcp.core.serialization.encode_json

::: litestar_mcp.core.serialization.decode_json

::: litestar_mcp.core.serialization.to_json

::: litestar_mcp.core.serialization.from_json

## JSON-RPC 2.0 & Subscription Streams

::: litestar_mcp.core.jsonrpc.JSONRPCRequest

::: litestar_mcp.core.jsonrpc.JSONRPCError

::: litestar_mcp.core.jsonrpc.JSONRPCErrorException

::: litestar_mcp.core.sse.SubscriptionManager

::: litestar_mcp.core.sse.validate_log_level

::: litestar_mcp.core.sse.log_level_allows

## Core Exceptions & Schema Validation

::: litestar_mcp.core.exceptions.LitestarMCPError

::: litestar_mcp.core.exceptions.BridgeConnectionError

::: litestar_mcp.core.exceptions.BridgeMessageTooLargeError

::: litestar_mcp.core.exceptions.MissingDependencyError

::: litestar_mcp.utils.validate_tool_descriptions

::: litestar_mcp.core.schema_builder.typed_dict_to_json_schema

::: litestar_mcp.core.schema_builder.unwrap_root_object_schema

::: litestar_mcp.core.schema_builder.type_to_output_schema

::: litestar_mcp.core.schema_builder.generate_output_schema_for_handler

## Internal ASGI Scope Execution

::: litestar_mcp.mcp.executor.execute_tool

::: litestar_mcp.mcp.executor.execute_handler_response

::: litestar_mcp.mcp.executor.blank_http_scope
