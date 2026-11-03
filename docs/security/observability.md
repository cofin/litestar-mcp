---
title: OpenTelemetry Tracing & Tool Callbacks
---

# OpenTelemetry Tracing & Tool Callbacks

`litestar-mcp` provides two complementary observability mechanisms:

1. **OpenTelemetry Tracing (`TelemetryConfig` & `SpanManager`)**: Emits vendor-neutral OpenTelemetry spans with standard GenAI semantic conventions (`gen_ai.operation.name`, `gen_ai.agent.name`, `gen_ai.tool.name`, token usage, and turn metadata) across `AgentRuntime` turns, tool executions, and custom instrumentation blocks.
2. **Tool-Call Lifecycle Hooks (`MCPConfig.before_tool_call` & `MCPConfig.after_tool_call`)**: Runs synchronous or asynchronous callbacks around every MCP `tools/call` dispatch for structured audit logging, latency histograms, and request-state tagging.

---

## 1. Installing OpenTelemetry Support

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

## 2. Configuring `TelemetryConfig` and `SpanManager`

`TelemetryConfig` (`litestar_mcp.core.observability.TelemetryConfig`) controls span emission, custom `TracerProvider` resolution, resource attributes, and the instrumentation scope name:

| `TelemetryConfig` Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `enable_spans` | `bool` | `False` | Enables OpenTelemetry span creation for agent turns, tool calls, and `SpanManager.span(...)` blocks. |
| `provider_factory` | `Callable[[], Any] \| None` | `None` | Optional factory returning a custom OpenTelemetry `TracerProvider`. When `None`, `SpanManager` uses the global `opentelemetry.trace.get_tracer(tracer_name)`. |
| `resource_attributes` | `dict[str, Any]` | `{}` | Key-value attributes merged onto every span started by `SpanManager` (for example, `{"service.name": "notes-mcp", "deployment.environment": "production"}`). |
| `tracer_name` | `str` | `"litestar_mcp"` | Instrumentation scope name passed to `get_tracer(tracer_name)`. |

Pass a configured `SpanManager(telemetry)` to `AgentRuntime(..., span_manager=...)` and use it inside `MCPConfig` tool callbacks or route dependencies:

```python title="docs/examples/snippets/observability_telemetry.py"
--8<-- "docs/examples/snippets/observability_telemetry.py"
```

---

## 3. Emitted Span Hierarchy & GenAI Semantic Conventions

`SpanManager` (`litestar_mcp.core.observability.SpanManager`) and `litestar_mcp.core.observability.semantics` follow the OpenTelemetry Semantic Conventions for Generative AI systems (`SpanKind.INTERNAL`):

### Span Names

| Operation | `SpanManager` Method | Span Name Format | `gen_ai.operation.name` |
| :--- | :--- | :--- | :--- |
| **Agent Turn Execution** | `start_agent_span(agent_name, action="invoke_agent", ...)` | `invoke_agent <agent_name>` | `"invoke_agent"` |
| **Tool Execution** | `start_tool_span(tool_name, call_id=..., ...)` | `execute_tool <tool_name>` | `"execute_tool"` |
| **Custom Instrumentation Block** | `span(name, attributes=...)` / `start_span(name, attributes=...)` | `<name>` | *(caller-supplied)* |

### Span Attributes Reference (`litestar_mcp.core.observability.semantics`)

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

## 4. MCP Tool-Call Lifecycle Hooks (`before_tool_call` & `after_tool_call`)

For MCP server observability independent of `AgentRuntime`, `MCPConfig` exposes `before_tool_call` (`BeforeToolCallHook`) and `after_tool_call` (`AfterToolCallHook`):

```python title="docs/examples/snippets/configuration_tool_callbacks.py"
--8<-- "docs/examples/snippets/configuration_tool_callbacks.py"
```

### Callback Signatures & Execution Order

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
