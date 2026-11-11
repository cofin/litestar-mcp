---
title: OpenTelemetry Tracing & Tool Callbacks
---

# OpenTelemetry Tracing & Tool Callbacks

`litestar-mcp` provides complementary observability and diagnostic mechanisms across agent turns, MCP handlers, and background tasks:

1. **OpenTelemetry Tracing (`TelemetryConfig` & `SpanManager`)**: Emits vendor-neutral OpenTelemetry spans with standard GenAI semantic conventions (`gen_ai.operation.name`, `gen_ai.agent.name`, `gen_ai.tool.name`, token usage, and turn metadata) across `AgentRuntime` turns, tool executions, and custom instrumentation blocks.
2. **Tool-Call Lifecycle Hooks (`MCPConfig.before_tool_call` & `MCPConfig.after_tool_call`)**: Runs synchronous or asynchronous callbacks around every MCP `tools/call` dispatch for structured audit logging, latency histograms, and request-state tagging.
3. **Structured MCP Server Logging (`MCPConfig.logging`, `logging/setLevel`, & `ctx.log`)**: Streams threshold-filtered RFC 5424 `notifications/message` diagnostics to authorized client sessions and opted-in subscription streams without leaking logs across principals or unrelated streams.

---

## Installing OpenTelemetry Support

Install the optional `opentelemetry` extra (`opentelemetry-api>=1.20.0`) using your package manager:

=== "uv"

    ```bash
    uv add "litestar-mcp[opentelemetry]"
    ```

=== "pip"

    ```bash
    pip install "litestar-mcp[opentelemetry]"
    ```

=== "pdm"

    ```bash
    pdm add "litestar-mcp[opentelemetry]"
    ```

=== "poetry"

    ```bash
    poetry add "litestar-mcp[opentelemetry]"
    ```

!!! note "Zero-Overhead No-Op Fallback"
    `SpanManager` resolves `opentelemetry.trace` lazily only when `TelemetryConfig(enable_spans=True)` is set. When `enable_spans=False` (the default) or `opentelemetry-api` is not installed in the environment, all `SpanManager` methods degrade gracefully to zero-allocation no-ops without raising `ImportError`.

---

## Configuring TelemetryConfig and SpanManager

`TelemetryConfig` (`litestar_mcp.core.observability.TelemetryConfig`) controls span emission, custom `TracerProvider` resolution, resource attributes, and the instrumentation scope name:

| `TelemetryConfig` Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `enable_spans` | `bool` | `False` | Enables OpenTelemetry span creation for agent turns, tool calls, and `SpanManager.span(...)` blocks. |
| `provider_factory` | `Callable[[], Any] or None` | `None` | Optional factory returning a custom OpenTelemetry `TracerProvider`. When `None`, `SpanManager` uses the global `opentelemetry.trace.get_tracer(tracer_name)`. |
| `resource_attributes` | `dict[str, Any]` | `{}` | Key-value attributes merged onto every span started by `SpanManager` (for example, `{"service.name": "notes-mcp", "deployment.environment": "production"}`). |
| `tracer_name` | `str` | `"litestar_mcp"` | Instrumentation scope name passed to `get_tracer(tracer_name)`. |

Pass a configured `SpanManager(telemetry)` to `AgentRuntime(..., span_manager=...)` and use it inside `MCPConfig` tool callbacks or route dependencies:

```python title="docs/examples/snippets/observability_telemetry.py"
--8<-- "docs/examples/snippets/observability_telemetry.py:example"
```

---

## Emitted Span Hierarchy and GenAI Semantic Conventions

`SpanManager` (`litestar_mcp.core.observability.SpanManager`) and `litestar_mcp.core.observability.semantics` follow the OpenTelemetry Semantic Conventions for Generative AI systems (`SpanKind.INTERNAL`):

### Span Names

| Operation | `SpanManager` Method | Span Name Format | `gen_ai.operation.name` |
| :--- | :--- | :--- | :--- |
| **Agent Turn Execution** | `start_agent_span(agent_name, action="invoke_agent", ...)` | `invoke_agent <agent_name>` | `"invoke_agent"` |
| **Tool Execution** | `start_tool_span(tool_name, call_id=..., ...)` | `execute_tool <tool_name>` | `"execute_tool"` |
| **Custom Instrumentation Block** | `span(name, attributes=...)` / `start_span(name, attributes=...)` | `<name>` | *(caller-supplied)* |

### Span Attributes Reference

| Constant | Attribute Key | Emitted On | Value / Description |
| :--- | :--- | :--- | :--- |
| `GEN_AI_OPERATION_NAME` | `"gen_ai.operation.name"` | Agent & Tool spans | `"invoke_agent"` for agent turns; `"execute_tool"` for tool invocations. |
| `GEN_AI_AGENT_NAME` | `"gen_ai.agent.name"` | Agent spans | Name of the active `Agent` (`agent.name`). |
| `GEN_AI_CONVERSATION_ID` | `"gen_ai.conversation.id"` | Agent spans | Conversation `session_id` when present on the `TurnRequest`. |
| `GEN_AI_REQUEST_MODEL` | `"gen_ai.request.model"` | Agent spans | Requested model identifier (for example, `"gemini-3.8-flash"`). |
| `GEN_AI_PROVIDER_NAME` | `"gen_ai.provider.name"` | Agent spans | Model provider identifier (for example, `"google_genai"`). |
| `GEN_AI_USAGE_INPUT_TOKENS` | `"gen_ai.usage.input_tokens"` | Agent spans (`set_usage`) | Total prompt/input tokens consumed during the turn. |
| `GEN_AI_USAGE_OUTPUT_TOKENS` | `"gen_ai.usage.output_tokens"` | Agent spans (`set_usage`) | Total completion/output tokens produced during the turn. |
| `GEN_AI_TOOL_NAME` | `"gen_ai.tool.name"` | Tool spans | Registered name of the executed tool. |
| `GEN_AI_TOOL_CALL_ID` | `"gen_ai.tool.call.id"` | Tool spans | Model-assigned `call_id` correlating the tool invocation to the assistant turn. |
| `LITESTAR_MCP_AGENT_GROUP` | `"litestar_mcp.agent.group"` | Agent spans | Name of the owning `AgentGroup` when running a coordinator/specialist tree. |
| `LITESTAR_MCP_TURN_ID` | `"litestar_mcp.turn.id"` | Agent spans | Unique `turn_id` for the current turn execution. |
| `ERROR_TYPE` | `"error.type"` | Any failed span (`end_span`) | Exception class name (`type(error).__name__`); `end_span` also calls `span.record_exception(error)` and sets `Status(StatusCode.ERROR, str(error))`. |

---

## MCP Tool-Call Lifecycle Hooks

For MCP server observability independent of `AgentRuntime`, `MCPConfig` exposes `before_tool_call` (`BeforeToolCallHook`) and `after_tool_call` (`AfterToolCallHook`):

```python title="docs/examples/snippets/configuration_tool_callbacks.py"
--8<-- "docs/examples/snippets/configuration_tool_callbacks.py:example"
```

### Callback Signatures and Execution Order

1. **`before_tool_call(tool_name: str, arguments: dict[str, Any], request: Request[Any, Any, Any], /) -> Awaitable[None] | None`**:
   - Runs once before every `tools/call` execution, immediately after the synthesized internal HTTP `Request` is created and **before** route guards or dependencies run.
   - Receives a shallow copy of `arguments` so mutations inside the hook do not alter the arguments passed to the handler.
   - Useful for binding correlation IDs or audit metadata onto `request.state`.
2. **`after_tool_call(tool_name: str, arguments: dict[str, Any], request: Request[Any, Any, Any], /, *, result: Any, exception: Exception | None, duration: float) -> Awaitable[None] | None`**:
   - Runs inside a `finally` block after every `tools/call` attempt—whether the tool succeeded (`exception is None`), failed argument validation, was rejected by a `Guard` (`PermissionDeniedException` / `NotAuthorizedException`), or raised an unhandled exception.
   - `duration` is the monotonic elapsed wall-clock time in seconds (`float`) covering `before_tool_call`, guard evaluation, dependency resolution, and handler execution.
3. **Fault Isolation**:
   - Both hooks accept either synchronous functions or `async def` coroutines.
   - Any exception raised inside `before_tool_call` or `after_tool_call` is caught and logged by `litestar-mcp` so a failing metrics sink or audit logger never aborts a valid tool execution.

---

## Structured MCP Server Logging

When `MCPConfig.logging` is `True` (default, paired with `MCPConfig.default_log_level = "info"`), `LitestarMCP` advertises `capabilities.logging = {}` in `server/discover` and `initialize`, accepts RFC 5424 severity threshold updates, and streams structured `notifications/message` frames emitted via `await ctx.log(level, data, logger=None)`. Setting `MCPConfig(logging=False)` omits `capabilities.logging`, rejects `logging/setLevel` with `METHOD_NOT_FOUND` (`-32601`), and turns `ctx.log(...)` into a no-op.

### Severity Hierarchy and Client Threshold Control

`MCPLogLevel` follows the ascending RFC 5424 severity ordering from `"debug"` through `"emergency"`:

| Severity Rank | `MCPLogLevel` Literal | Typical Diagnostic Use |
| :--- | :--- | :--- |
| `0` | `"debug"` | Verbose step-by-step execution traces and query plans |
| `1` | `"info"` | Standard operational milestones (`default_log_level` default) |
| `2` | `"notice"` | Normal but notable configuration or catalog transitions |
| `3` | `"warning"` | Recoverable retries, rate-limit throttling, or fallback paths |
| `4` | `"error"` | Tool-level or upstream failures impacting the active request |
| `5` | `"critical"` | Subsystem outages or corrupted external dependencies |
| `6` | `"alert"` | Conditions requiring immediate operator intervention |
| `7` | `"emergency"` | Unrecoverable service-wide failure state |

Clients control their active log threshold according to their negotiated Streamable HTTP mode:

- **Session-Aware `2025-11-25`, `2025-06-18`, and `2025-03-26` (`logging/setLevel`)**: Clients send JSON-RPC `logging/setLevel` with `{"level": "warning"}`—or call `await client.set_logging_level("warning")` on `MCPStreamableHTTPClient`—to persist the new threshold on their `MCPSession` (unknown level names fail with `INVALID_PARAMS` `-32602`). Matching `notifications/message` frames stream over the session's `GET /mcp` SSE connection, scoped strictly to `owner_id` and `session_id`.
- **Stateless `2026-07-28` (`io.modelcontextprotocol/logLevel` & `subscriptions/listen`)**: Stateless requests supply per-request `params._meta["io.modelcontextprotocol/logLevel"]`, and `POST /mcp` `subscriptions/listen` streams opt into log notifications explicitly with `"notifications": {"logLevel": "info"}`. Subscription streams that omit `"logLevel"` receive zero `notifications/message` frames.

### Emitting Server Logs in Handlers and Background Tasks

Inside any MCP tool, resource, prompt, or promoted background task (both in-process and distributed `QueueTaskExecutor` workers), call `await ctx.log(level, data, logger=None)` on `get_mcp_request_context()`:

```python
from litestar import post

from litestar_mcp import LitestarMCP, MCPConfig, get_mcp_request_context


@post("/deployments", mcp_tool="rollout_service")
async def rollout_service(service: str) -> dict[str, str]:
    """Emit structured RFC 5424 diagnostics to the calling MCP session."""
    ctx = get_mcp_request_context()
    await ctx.log(
        "info",
        {"event": "rollout_started", "service": service},
        logger="deployments",
    )
    return {"service": service, "status": "deployed"}


config = MCPConfig(logging=True, default_log_level="info")
plugin = LitestarMCP(config)
```
